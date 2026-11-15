"""The repair rules and the effects of a push (lot M5b of docs/adr/0079).

- The repair vectors (`tests/fixtures/sync_repairs.json`): the rules of mobile ADR 0003 across
  rows, and the effects the web runs once per new state, applied in every order of the pushes,
  give the same rows, the same stock and the same audit entries. The file is published for the
  mobile repository to pin, as it pins `sync_merge.json`.
- The results of a push say `repaired` for an operation whose row a repair changed.
- What is derived (`visible_from`, the months of a season, the effect of a movement), the
  authors of the effects, and the rules of the data no repair covers (400).
"""

import datetime
import itertools
import json
import pathlib
import uuid
from decimal import Decimal
from typing import Any

import pytest
from django.db import transaction

from housecairn.accounts.models import User
from housecairn.audit.models import AuditEvent
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Membership, Role
from housecairn.stock import services as stock_services
from housecairn.stock.models import Movement
from housecairn.sync import changes, clock, ids, pushing, reading
from housecairn.sync.models import Change
from housecairn.tasks import services as task_services
from housecairn.tasks.models import MaterialDraw, Occurrence, Status, Task

pytestmark = pytest.mark.django_db

VECTORS = pathlib.Path(__file__).parent / "fixtures" / "sync_repairs.json"


def _vectors() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(VECTORS.read_text(encoding="utf-8"))
    return data


def _names(home: Home, case: dict[str, Any]) -> dict[str, str]:
    """Every `$name` of a case: the home, the deterministic ids, a random UUID for the others."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str) and value.startswith("$"):
            found.add(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(key)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk([case["before"], case["pushes"], case["expect"]])
    names = {"$home": str(home.pk)}
    for name in sorted(found - set(case["ids"]) - {"$home"}):
        names[name] = str(uuid.uuid4())
    makers = {"next": ids.successor, "material": ids.material, "draw": ids.draw}
    for name, (kind, *parts) in case["ids"].items():
        names[name] = str(makers[kind](home.pk, *(names[part] for part in parts)))
    return names


def _substitute(value: Any, names: dict[str, str]) -> Any:
    if isinstance(value, str) and value in names:
        return names[value]
    if isinstance(value, dict):
        return {k: _substitute(v, names) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, names) for v in value]
    return value


def _lines(home: Home) -> dict[str, dict[str, Any]]:
    return {
        line["id"]: {name: entry[0] for name, entry in line["f"].items()}
        for line in reading.snapshot_lines(home)
        if "id" in line
    }


def _check(home: Home, case: dict[str, Any], names: dict[str, str], order: Any) -> Any:
    expect = case["expect"]
    lines = _lines(home)
    for row, fields in expect.get("rows", {}).items():
        state = lines.get(names[row], {})
        for name, value in fields.items():
            assert state.get(name) == value, (case["name"], order, row, name, state.get(name))
    for row in expect.get("deleted", []):
        assert lines[names[row]].get(changes.DELETED_AT) is not None, (case["name"], order, row)
    for article, quantity in expect.get("stock", {}).items():
        total = sum(
            (m.delta for m in Movement.objects.filter(article_id=names[article])), Decimal(0)
        )
        assert total == Decimal(quantity), (case["name"], order, article, total)
    for action, count in expect.get("audit", {}).items():
        found = AuditEvent.objects.filter(home_id=home.pk, action=action).count()
        assert found == count, (case["name"], order, action, found)
    rows = {row: lines.get(names[row]) for row in expect.get("rows", {})}
    stock = {
        article: sorted(
            (str(m.kind), str(m.quantity), str(m.delta))
            for m in Movement.objects.filter(article_id=names[article])
        )
        for article in expect.get("stock", {})
    }
    return rows, stock


@pytest.fixture
def owner_and_member(alice: User, alice_home: Home, bob: User) -> Home:
    Membership.objects.create(home=alice_home, user=bob, role=Role.MEMBER)
    return alice_home


def test_the_vectors_name_their_rules() -> None:
    data = _vectors()
    assert data["version"] == 1
    assert {case["rule"] for case in data["cases"]} >= {
        "two open occurrences: the one that follows stays open",
        "two open occurrences: the one made last stays open",
        "two open occurrences: closed as done if marked done",
        "an undo whose successor was edited is reverted",
        "an undo whose successor was not edited stands",
        "the successor's date is the one recurrence gives",
        "stock follows done",
        "the effect of an inventory is the count minus the stock before it",
    }


@pytest.mark.parametrize("case", _vectors()["cases"], ids=lambda case: case["name"])
def test_a_repair_vector_gives_its_rows_in_every_order(
    alice: User, owner_and_member: Home, bob: User, case: dict[str, Any]
) -> None:
    home = owner_and_member
    data = _vectors()
    devices = {name: clock.device_from_b64(text) for name, text in data["devices"].items()}
    users = {"a": alice, "b": bob}
    names = _names(home, case)
    for push in case["before"]:
        device = push[0]["device"]
        pusher = pushing.Pusher(users[device], Role.OWNER, devices[device])
        answer = pushing.apply(home, pusher, [_substitute(e["op"], names) for e in push])
        assert all(r["status"] != "rejected" for r in answer["results"]), (case["name"], answer)
    seen = []
    for order in itertools.permutations(range(len(case["pushes"]))):
        sid = transaction.savepoint()
        for number in order:
            push = case["pushes"][number]
            device = push[0]["device"]
            pusher = pushing.Pusher(
                users[device], Role.OWNER if device == "a" else Role.MEMBER, devices[device]
            )
            answer = pushing.apply(home, pusher, [_substitute(e["op"], names) for e in push])
            assert all(r["status"] != "rejected" for r in answer["results"]), (order, answer)
        seen.append(_check(home, case, names, order))
        transaction.savepoint_rollback(sid)
    assert all(state == seen[0] for state in seen), case["name"]


# ---------- the results, and the rules on rows of the web ----------


def stamp(offset_ms: int = 0) -> str:
    return clock.format_clock(clock.make_clock(clock.wall_ms() + offset_ms))


def put(home: Home, entity: str, row: Any, fields: dict[str, Any], at: str) -> dict[str, Any]:
    return {"t": entity, "h": str(home.pk), "id": str(row), "k": "put", "c": at, "f": fields}


def phone_of(user: User, role: str = Role.OWNER) -> pushing.Pusher:
    return pushing.Pusher(user, role, bytes(range(16)))


def monthly(alice: User, home: Home, **extra: Any) -> Task:
    return task_services.create_task(
        alice,
        home,
        title="Clean the filter",
        repeat_mode="after_last",
        repeat_unit="month",
        repeat_interval=1,
        due_on=datetime.date(2026, 1, 10),
        **extra,
    )


class TestResults:
    def test_a_phone_that_postpones_what_the_web_closed_gets_repaired(
        self, alice: User, alice_home: Home
    ) -> None:
        """Mutant: without the rule of two open occurrences, the push is a 400."""
        task = monthly(alice, alice_home)
        first = task.occurrences.get()
        closing = task_services.complete(alice, first, on=datetime.date(2026, 1, 12))
        assert closing.next is not None
        answer = pushing.apply(
            alice_home,
            phone_of(alice),
            [
                put(
                    alice_home,
                    "tasks.occurrence",
                    first.pk,
                    {"status": "postponed", "due_on": "2026-12-20"},
                    stamp(),
                )
            ],
        )
        assert answer["results"][0]["status"] == "repaired"
        first.refresh_from_db()
        assert (first.status, first.due_on) == (Status.DONE, datetime.date(2026, 12, 20))
        assert Occurrence.objects.get(pk=closing.next.pk).status == Status.PLANNED
        repair = Change.objects.filter(row_id=first.pk).order_by("-pos").first()
        assert repair is not None
        assert repair.fields == {"status": "done"}
        assert bytes(repair.device) == changes.server_device()

    def test_the_web_undo_after_a_phone_edit_is_reverted_by_the_edit(
        self, alice: User, alice_home: Home
    ) -> None:
        """The web took back a closing; the phone had postponed the successor offline. The
        successor comes back, found by its deterministic id although the web let go of it."""
        task = monthly(alice, alice_home)
        first = task.occurrences.get()
        closing = task_services.complete(
            alice, first, on=datetime.date(2026, 1, 12), note="Done well"
        )
        assert closing.next is not None
        task_services.undo(alice, first)
        answer = pushing.apply(
            alice_home,
            phone_of(alice),
            [
                put(
                    alice_home,
                    "tasks.occurrence",
                    closing.next.pk,
                    {"status": "postponed", "postponed_from": "2026-02-12", "due_on": "2026-02-28"},
                    stamp(1_000),
                )
            ],
        )
        assert answer["results"][0]["status"] == "repaired"
        first.refresh_from_db()
        successor = Occurrence.objects.get(pk=closing.next.pk)
        assert (first.status, first.completed_on, first.completion_note) == (
            Status.DONE,
            datetime.date(2026, 1, 12),
            "Done well",
        )
        assert (successor.status, successor.follows_id) == (Status.POSTPONED, first.pk)

    def test_a_put_that_only_names_the_successor_is_not_checked(
        self, alice: User, alice_home: Home
    ) -> None:
        """The date of an open occurrence may be moved: only a closing's put is checked."""
        task = monthly(alice, alice_home)
        first = task.occurrences.get()
        closing = task_services.complete(alice, first, on=datetime.date(2026, 1, 12))
        assert closing.next is not None
        answer = pushing.apply(
            alice_home,
            phone_of(alice),
            [
                put(
                    alice_home,
                    "tasks.occurrence",
                    closing.next.pk,
                    {"due_on": "2026-05-05"},
                    stamp(),
                )
            ],
        )
        assert answer["results"][0]["status"] == "applied"
        assert Occurrence.objects.get(pk=closing.next.pk).due_on == datetime.date(2026, 5, 5)


class TestEffects:
    def test_the_audit_names_the_account_of_the_phone_once(
        self, alice: User, alice_home: Home, bob: User
    ) -> None:
        """Mutant: an entry per push, or the account named by the operation, fails here."""
        Membership.objects.create(home=alice_home, user=bob, role=Role.MEMBER)
        task = monthly(alice, alice_home)
        first = task.occurrences.get()
        closing = [
            put(
                alice_home,
                "tasks.occurrence",
                first.pk,
                {"status": "done", "completed_on": "2026-01-12", "completed_by_id": str(alice.pk)},
                stamp(),
            )
        ]
        pushing.apply(alice_home, phone_of(bob, Role.MEMBER), closing)
        pushing.apply(alice_home, phone_of(bob, Role.MEMBER), closing)
        entries = AuditEvent.objects.filter(home_id=alice_home.pk, action="occurrence.done")
        assert [(e.actor_id, e.detail["title"]) for e in entries] == [(bob.pk, "Clean the filter")]
        first.refresh_from_db()
        assert first.completed_by_id == bob.pk
        assert str(entries[0].label) == "Task marked done from a phone: Clean the filter"

    def test_an_undo_pushed_writes_nothing_more(self, alice: User, alice_home: Home) -> None:
        """An undo is data: no entry, no movement of the server."""
        task = monthly(alice, alice_home)
        article = stock_services.create_article(alice, alice_home, name="Cartridge")
        stock_services.record_movement(alice, article, kind="entry", quantity=Decimal(5))
        from housecairn.tasks import materials

        materials.add_material(alice, task, article, Decimal(2))
        first = task.occurrences.get()
        closing = task_services.complete(alice, first, on=datetime.date(2026, 1, 12))
        assert closing.next is not None
        movements = Movement.objects.count()
        answer = pushing.apply(
            alice_home,
            phone_of(alice),
            [
                {
                    "t": "tasks.occurrence",
                    "h": str(alice_home.pk),
                    "id": str(closing.next.pk),
                    "k": "delete",
                    "c": stamp(),
                },
                put(
                    alice_home,
                    "tasks.occurrence",
                    first.pk,
                    {"status": "planned", "completed_on": None},
                    stamp(1),
                ),
            ],
        )
        assert [r["status"] for r in answer["results"]] == ["applied", "applied"]
        assert Movement.objects.count() == movements
        assert MaterialDraw.objects.filter(occurrence=first).count() == 1
        assert not AuditEvent.objects.filter(action__startswith="occurrence.").exists()

    def test_taking_again_for_one_undo_writes_one_exit(self, alice: User, alice_home: Home) -> None:
        """The exit of a reverted undo has the id of the undo: a second call takes nothing."""
        task = monthly(alice, alice_home)
        article = stock_services.create_article(alice, alice_home, name="Cartridge")
        first = task.occurrences.get()
        movement = uuid.uuid4()
        for _time in range(2):
            stock_services.take_again_for_task(
                alice, article, Decimal(2), occurrence=first, movement_id=movement
            )
        assert list(Movement.objects.filter(article=article).values_list("pk", "delta")) == [
            (movement, Decimal("-2.00"))
        ]


class TestDerived:
    def test_visible_from_is_the_web_s(self, alice: User, alice_home: Home) -> None:
        """Mutant: a push that leaves `visible_from` as it was fails here."""
        task = monthly(alice, alice_home, hide_days_before=5)
        row = task.occurrences.get().pk
        pushing.apply(
            alice_home,
            phone_of(alice),
            [put(alice_home, "tasks.occurrence", row, {"due_on": "2026-03-10"}, stamp())],
        )
        assert Occurrence.objects.get(pk=row).visible_from == datetime.date(2026, 3, 5)
        pushing.apply(
            alice_home,
            phone_of(alice),
            [put(alice_home, "tasks.task", task.pk, {"hide_days_before": 1}, stamp(1))],
        )
        assert Occurrence.objects.get(pk=row).visible_from == datetime.date(2026, 3, 9)

    def test_a_hemisphere_pushed_moves_the_seasons(self, alice: User, alice_home: Home) -> None:
        task = task_services.create_task(alice, alice_home, title="Mow", season="summer")
        window = (task.season_start_month, task.season_end_month)
        pushing.apply(
            alice_home,
            phone_of(alice),
            [put(alice_home, "homes.home", alice_home.pk, {"hemisphere": "south"}, stamp())],
        )
        task.refresh_from_db()
        assert (task.season_start_month, task.season_end_month) != window

    def test_a_movement_pushed_has_its_effect(self, alice: User, alice_home: Home) -> None:
        """M5a could not keep a pushed movement: its effect is a required column."""
        article = stock_services.create_article(alice, alice_home, name="Salt")
        row = uuid.uuid4()
        answer = pushing.apply(
            alice_home,
            phone_of(alice),
            [
                put(
                    alice_home,
                    "stock.movement",
                    row,
                    {"article_id": str(article.pk), "kind": "exit", "quantity": "1.50"},
                    stamp(),
                )
            ],
        )
        assert answer["results"][0]["status"] == "applied"
        assert Movement.objects.get(pk=row).delta == Decimal("-1.50")
        assert stock_services.quantity_of(article) == Decimal("-1.50")


class TestRulesNoRepairCovers:
    def test_two_successors_of_one_occurrence_are_400(self, alice: User, alice_home: Home) -> None:
        """Kept from M5a (an écart of ADR 0077 need 6, written in ADR 0079): no rule repairs it."""
        task = task_services.create_task(alice, alice_home, title="Paint the hall")
        first = task.occurrences.get()
        task_services.skip(alice, first)  # done once: no successor
        fields = {"task_id": str(task.pk), "status": "done", "follows_id": str(first.pk)}
        with pytest.raises(pushing.MalformedError) as refused:
            pushing.apply(
                alice_home,
                phone_of(alice),
                [
                    put(alice_home, "tasks.occurrence", uuid.uuid4(), fields, stamp()),
                    put(alice_home, "tasks.occurrence", uuid.uuid4(), fields, stamp(1)),
                ],
            )
        assert refused.value.index == 1


def test_a_home_of_another_owner_is_untouched(alice: User, alice_home: Home, bob: User) -> None:
    """The rules run on the home of the push only."""
    other = home_services.create_home(bob, name="Flat")
    theirs = monthly(bob, other)
    open_before = list(theirs.occurrences.values_list("pk", "status"))
    task = monthly(alice, alice_home)
    pushing.apply(
        alice_home,
        phone_of(alice),
        [
            put(
                alice_home,
                "tasks.occurrence",
                uuid.uuid4(),
                {"task_id": str(task.pk), "status": "planned"},
                stamp(),
            )
        ],
    )
    assert list(theirs.occurrences.values_list("pk", "status")) == open_before
