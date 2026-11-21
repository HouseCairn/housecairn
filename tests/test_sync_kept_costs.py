"""The phone reads the real cost the server keeps, and never writes it (docs/adr/0092).

The unit price a draw was taken at (`tasks.materialdraw.unit_price`, ADR 0090) and the plan an
occurrence kept when it was marked done (`tasks.occurrence.planned_labour` and
`planned_material`, ADR 0091) are fields of the feed and of the snapshot. The server alone
sets them: a push that writes one is `forbidden`, and every value the server sets (a closing
on the web, a closing or an undo pushed, a draw brought back, the migration that sends the
values of before) is a change of the feed, so that a phone past it reads it.
"""

import importlib
import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.core.cache import cache
from django.test import Client

from housecairn.accounts.models import User
from housecairn.homes.models import Home
from housecairn.stock import services as stock_services
from housecairn.stock.models import Article
from housecairn.sync import changes, clock, ids, reading
from housecairn.sync.models import Change, RowClocks
from housecairn.tasks import materials, services
from housecairn.tasks.models import MaterialDraw, Occurrence, Status, Task
from tests.oauth_support import Connected, connect
from tests.test_sync_push_api import op, result_of, send, stamp

pytest_plugins = ("tests.tasks_fixtures", "tests.stock_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root", "frozen_today")]

D = Decimal
DRAW = "tasks.materialdraw"
OCCURRENCE = "tasks.occurrence"


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


def priced(alice: User, home: Home, name: str, price: str) -> Article:
    article = stock_services.create_article(
        alice, home, name=name, unit="piece", unit_price=D(price)
    )
    stock_services.record_movement(alice, article, kind="entry", quantity=D("10"))
    return article


@pytest.fixture
def paint(alice: User, alice_home: Home) -> Article:
    return priced(alice, alice_home, "Paint", "45.50")


@pytest.fixture
def filler(alice: User, alice_home: Home) -> Article:
    return priced(alice, alice_home, "Filler", "60.00")


@pytest.fixture
def planned_task(alice: User, owner_task: Task, paint: Article, filler: Article) -> Task:
    """2 x 45.50 of paint and 1 x 60.00 of filler, 80.00 of labour planned."""
    materials.add_material(alice, owner_task, paint, D(2))
    materials.add_material(alice, owner_task, filler, D(1))
    return services.update_task(alice, owner_task, labour_budget=D("80"))


@pytest.fixture
def phone(alice: User, alice_home: Home) -> Connected:
    return connect(alice, [alice_home])


def head_of(home: Home) -> int:
    return reading.head_of(home.pk).pos


def pull(phone: Connected, home: Home, after: int) -> list[dict[str, Any]]:
    """The changes a phone reads after `after`, through the API, every page."""
    found: list[dict[str, Any]] = []
    while True:
        answer = Client().get(
            f"/api/v1/sync/homes/{home.pk}/changes",
            {"after": after, "limit": 1000},
            secure=True,
            HTTP_AUTHORIZATION=f"Bearer {phone.access}",
        )
        assert answer.status_code == 200, answer.content
        body = answer.json()
        found.extend(body["changes"])
        if not body["more"]:
            return found
        after = body["changes"][-1]["pos"]


def snapshot(phone: Connected, home: Home) -> list[dict[str, Any]]:
    answer = Client().get(
        f"/api/v1/sync/homes/{home.pk}/snapshot",
        secure=True,
        HTTP_AUTHORIZATION=f"Bearer {phone.access}",
    )
    assert answer.status_code == 200
    return [json.loads(line) for line in answer.getvalue().decode().splitlines()]


def last_value(found: list[dict[str, Any]], entity: str, row_id: Any, name: str) -> Any:
    """The value of a field after applying the changes in order, as the phone does."""
    values = [
        change["op"]["f"][name]
        for change in found
        if change["op"]["t"] == entity
        and change["op"]["id"] == str(row_id)
        and name in change["op"]["f"]
    ]
    assert values, f"{entity} {row_id}: no change of {name}"
    return values[-1]


def server_device() -> str:
    return clock.device_to_b64(changes.server_device())


def draw_id(home: Home, occurrence: Occurrence, article: Article) -> Any:
    return ids.draw(home.pk, occurrence.pk, article.pk)


class TestRead:
    def test_a_closing_on_the_web_sends_the_prices_and_the_plan(
        self,
        phone: Connected,
        alice: User,
        alice_home: Home,
        planned_task: Task,
        owner_occurrence: Occurrence,
        paint: Article,
        filler: Article,
    ) -> None:
        after = head_of(alice_home)
        services.complete(alice, owner_occurrence)
        found = pull(phone, alice_home, after)
        paint_draw = draw_id(alice_home, owner_occurrence, paint)
        filler_draw = draw_id(alice_home, owner_occurrence, filler)
        assert last_value(found, DRAW, paint_draw, "unit_price") == "45.50"
        assert last_value(found, DRAW, filler_draw, "unit_price") == "60.00"
        assert last_value(found, OCCURRENCE, owner_occurrence.pk, "planned_labour") == "80.00"
        # The stock works the materials out: no material budget is kept over it (ADR 0091),
        # and a field that stays empty is not sent (the phone reads it as null).
        assert not any(
            "planned_material" in change["op"]["f"]
            for change in found
            if change["op"]["id"] == str(owner_occurrence.pk)
        )

    def test_the_snapshot_holds_them_with_their_clock(
        self,
        phone: Connected,
        alice: User,
        alice_home: Home,
        planned_task: Task,
        owner_occurrence: Occurrence,
        paint: Article,
    ) -> None:
        services.complete(alice, owner_occurrence)
        lines = {line.get("id"): line for line in snapshot(phone, alice_home)}
        draw = lines[str(draw_id(alice_home, owner_occurrence, paint))]["f"]
        value, made, device = draw["unit_price"]
        assert (value, device) == ("45.50", server_device())
        assert clock.parse_clock(made) > 0
        occurrence = lines[str(owner_occurrence.pk)]["f"]
        assert occurrence["planned_labour"][0] == "80.00"
        assert occurrence["planned_material"][0] is None

    def test_an_undo_on_the_web_sends_the_plan_emptied(
        self,
        phone: Connected,
        alice: User,
        alice_home: Home,
        planned_task: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        services.complete(alice, owner_occurrence)
        after = head_of(alice_home)
        services.undo(alice, Occurrence.objects.get(pk=owner_occurrence.pk))
        found = pull(phone, alice_home, after)
        assert last_value(found, OCCURRENCE, owner_occurrence.pk, "planned_labour") is None


class TestPush:
    @pytest.mark.parametrize(
        ("entity", "name"),
        [(DRAW, "unit_price"), (OCCURRENCE, "planned_labour"), (OCCURRENCE, "planned_material")],
    )
    def test_a_phone_may_not_write_them(
        self,
        phone: Connected,
        alice: User,
        alice_home: Home,
        planned_task: Task,
        owner_occurrence: Occurrence,
        paint: Article,
        entity: str,
        name: str,
    ) -> None:
        services.complete(alice, owner_occurrence)
        row = (
            draw_id(alice_home, owner_occurrence, paint) if entity == DRAW else owner_occurrence.pk
        )
        before = Change.objects.filter(home_id=alice_home.pk).count()
        answer = send(phone.access, alice_home.pk, [op(alice_home.pk, entity, row, {name: "0.01"})])
        assert (result_of(answer)["status"], result_of(answer)["code"]) == ("rejected", "forbidden")
        assert Change.objects.filter(home_id=alice_home.pk).count() == before
        price = MaterialDraw.objects.get(pk=draw_id(alice_home, owner_occurrence, paint)).unit_price
        assert price == D("45.50")
        assert Occurrence.objects.get(pk=owner_occurrence.pk).planned_labour == D("80.00")

    def test_a_closing_pushed_comes_back_with_the_price_and_the_plan(
        self,
        phone: Connected,
        alice_home: Home,
        planned_task: Task,
        owner_occurrence: Occurrence,
        paint: Article,
        filler: Article,
    ) -> None:
        after = head_of(alice_home)
        paint_draw = draw_id(alice_home, owner_occurrence, paint)
        made = stamp(1_000)
        ops = [
            op(
                alice_home.pk,
                OCCURRENCE,
                owner_occurrence.pk,
                {"status": "done", "completed_on": "2026-06-15"},
                c=made,
            ),
            op(
                alice_home.pk,
                DRAW,
                paint_draw,
                {
                    "occurrence_id": str(owner_occurrence.pk),
                    "article_id": str(paint.pk),
                    "wanted": "2.00",
                    "drawn": "2.00",
                },
                c=made,
            ),
        ]
        answer = send(phone.access, alice_home.pk, ops)
        assert answer.status_code == 200, answer.content
        found = pull(phone, alice_home, after)
        # The draw the phone pushed: its price, set by the server after the phone's write.
        priced_by = [
            change
            for change in found
            if change["op"]["id"] == str(paint_draw) and "unit_price" in change["op"]["f"]
        ]
        assert [c["op"]["f"]["unit_price"] for c in priced_by] == ["45.50"]
        assert priced_by[0]["device"] == server_device()
        assert priced_by[0]["op"]["c"] > made
        # The draw the server made for the material the push did not carry.
        filler_draw = draw_id(alice_home, owner_occurrence, filler)
        assert last_value(found, DRAW, filler_draw, "unit_price") == "60.00"
        assert last_value(found, OCCURRENCE, owner_occurrence.pk, "planned_labour") == "80.00"
        # What the phone holds after the pull is what the server holds.
        assert MaterialDraw.objects.get(pk=paint_draw).unit_price == D("45.50")
        assert Occurrence.objects.get(pk=owner_occurrence.pk).planned_labour == D("80.00")

    def test_an_undo_pushed_comes_back_with_the_plan_emptied(
        self,
        phone: Connected,
        alice: User,
        alice_home: Home,
        planned_task: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        services.complete(alice, owner_occurrence)
        after = head_of(alice_home)
        undone = {"status": "planned", "completed_on": None}
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, OCCURRENCE, owner_occurrence.pk, undone, c=stamp(1_000))],
        )
        assert result_of(answer)["status"] in ("applied", "repaired"), answer.content
        found = pull(phone, alice_home, after)
        emptied = [
            change
            for change in found
            if change["op"]["id"] == str(owner_occurrence.pk)
            and "planned_labour" in change["op"]["f"]
        ]
        assert [c["op"]["f"] for c in emptied] == [
            {"planned_labour": None},
        ]
        assert emptied[0]["device"] == server_device()

    def test_a_draw_brought_back_comes_with_the_price_of_now(
        self,
        phone: Connected,
        alice: User,
        alice_home: Home,
        planned_task: Task,
        owner_occurrence: Occurrence,
        paint: Article,
    ) -> None:
        services.complete(alice, owner_occurrence)
        services.undo(alice, Occurrence.objects.get(pk=owner_occurrence.pk))
        stock_services.update_article(alice, paint, unit_price=D("50.00"))
        paint_draw = draw_id(alice_home, owner_occurrence, paint)
        after = head_of(alice_home)
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, DRAW, paint_draw, k="restore", c=stamp(1_000))],
        )
        assert result_of(answer)["status"] == "applied", answer.content
        found = pull(phone, alice_home, after)
        assert last_value(found, DRAW, paint_draw, "unit_price") == "50.00"

    def test_an_older_phone_that_does_not_know_them_writes_as_before(
        self,
        phone: Connected,
        alice: User,
        alice_home: Home,
        planned_task: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        """An older app keeps the new fields in `extra` and never pushes them (mobile ADR
        0003): its writes of the row carry its own fields, and a field it does not know, as
        any older app's, goes to `extra` without touching the plan."""
        services.complete(alice, owner_occurrence)
        after = head_of(alice_home)
        fields = {"completion_note": "Two coats", "field_of_a_newer_app": "x"}
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, OCCURRENCE, owner_occurrence.pk, fields, c=stamp(1_000))],
        )
        assert result_of(answer)["status"] == "applied", answer.content
        row = Occurrence.objects.get(pk=owner_occurrence.pk)
        assert (row.completion_note, row.planned_labour) == ("Two coats", D("80.00"))
        found = pull(phone, alice_home, after)
        assert [change["op"]["f"] for change in found] == [fields]
        rc = RowClocks.objects.get(home_id=alice_home.pk, entity=OCCURRENCE, row_id=row.pk)
        assert set(rc.extra) == {"field_of_a_newer_app"}


class TestOtherHome:
    def test_the_rows_of_another_home_are_never_sent(
        self,
        phone: Connected,
        alice: User,
        bob: User,
        alice_home: Home,
        bob_home: Home,
        planned_task: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        flat = services.create_task(bob, bob_home, title="Sweep", labour_budget=D("12"))
        theirs = services.open_occurrence_of(flat)
        assert theirs is not None
        services.complete(bob, theirs)
        services.complete(alice, owner_occurrence)
        migration = importlib.import_module("housecairn.sync.migrations.0006_send_the_kept_costs")
        migration.send_the_kept_costs(django_apps, None)
        sent = {change["op"]["id"] for change in pull(phone, alice_home, 0)}
        sent |= {line.get("id") for line in snapshot(phone, alice_home)}
        assert str(theirs.pk) not in sent
        assert str(owner_occurrence.pk) in sent
        assert all(change["op"]["h"] == str(alice_home.pk) for change in pull(phone, alice_home, 0))
        answer = Client().get(
            f"/api/v1/sync/homes/{bob_home.pk}/changes",
            {"after": 0},
            secure=True,
            HTTP_AUTHORIZATION=f"Bearer {phone.access}",
        )
        assert answer.status_code == 404
        # The change of the migration for the row of bob's home is in bob's home.
        assert Change.objects.filter(home_id=bob_home.pk, row_id=theirs.pk).exists()
        assert not Change.objects.filter(home_id=alice_home.pk, row_id=theirs.pk).exists()


class TestValuesOfBefore:
    def test_the_migration_sends_each_value_once_after_the_head(
        self,
        phone: Connected,
        alice: User,
        alice_home: Home,
        planned_task: Task,
        owner_occurrence: Occurrence,
        paint: Article,
        filler: Article,
    ) -> None:
        """A phone synced before the update reads the values of the times done before."""
        services.complete(alice, owner_occurrence)
        after = head_of(alice_home)  # the cursor of a phone synced before the update
        migration = importlib.import_module("housecairn.sync.migrations.0006_send_the_kept_costs")
        migration.send_the_kept_costs(django_apps, None)
        found = pull(phone, alice_home, after)
        assert sorted((c["op"]["t"], c["op"]["id"]) for c in found) == sorted(
            [
                (DRAW, str(draw_id(alice_home, owner_occurrence, paint))),
                (DRAW, str(draw_id(alice_home, owner_occurrence, filler))),
                (OCCURRENCE, str(owner_occurrence.pk)),
            ]
        )
        assert all(c["device"] == server_device() and c["op"]["k"] == "put" for c in found)
        assert [c["pos"] for c in found] == list(range(after + 1, after + 4))
        by_row = {c["op"]["id"]: c["op"]["f"] for c in found}
        assert by_row[str(draw_id(alice_home, owner_occurrence, paint))] == {"unit_price": "45.50"}
        assert by_row[str(owner_occurrence.pk)] == {
            "planned_labour": "80.00",
            "planned_material": None,
        }
        # The clocks of the fields are those of the changes: the snapshot agrees with the feed.
        lines = {line.get("id"): line for line in snapshot(phone, alice_home)}
        for change in found:
            line = lines[change["op"]["id"]]["f"]
            for name, value in change["op"]["f"].items():
                assert line[name] == [value, change["op"]["c"], server_device()]
        # The head moved on: a write of the web after it is after the migration's.
        services.update_occurrence(
            alice, Occurrence.objects.get(pk=owner_occurrence.pk), completion_note="Later"
        )
        later = pull(phone, alice_home, found[-1]["pos"])
        assert later
        assert later[0]["op"]["c"] > found[-1]["op"]["c"]

    def test_nothing_for_a_row_without_a_value_or_of_the_example_home(
        self,
        alice: User,
        bob: User,
        alice_home: Home,
        bob_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        services.complete(alice, owner_occurrence)  # nothing planned, no stock
        flat = services.create_task(bob, bob_home, title="Sweep", labour_budget=D("12"))
        theirs = services.open_occurrence_of(flat)
        assert theirs is not None
        services.complete(bob, theirs)
        Home.all_objects.filter(pk=bob_home.pk).update(is_example=True)
        before = Change.objects.count()
        migration = importlib.import_module("housecairn.sync.migrations.0006_send_the_kept_costs")
        migration.send_the_kept_costs(django_apps, None)
        assert Change.objects.count() == before
        assert Occurrence.objects.get(pk=owner_occurrence.pk).status == Status.DONE
