"""The real cost of a done task counts the stock it used, at the price of when (ADR 0090).

The maintainer's example: a task needs two stock items, each with a unit price, 151.00 in
all. Marked done, its real cost is 151.00 without typing anything; 40.00 typed makes 191.00;
an undo brings it back to nothing; a price changed afterwards changes nothing.
"""

import csv
import datetime
import importlib
import io
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.apitokens import services as token_services
from housecairn.budget import services as budget
from housecairn.homes import csv_export
from housecairn.homes.models import Home
from housecairn.projects.models import BudgetNature
from housecairn.stock import services as stock_services
from housecairn.stock.models import Article
from housecairn.sync import ids
from housecairn.tasks import agenda, costs, materials, services
from housecairn.tasks.models import MaterialDraw, Occurrence, Task
from tests.oauth_support import Connected, connect
from tests.test_sync_push_api import op, result_of, send, stamp

pytest_plugins = ("tests.tasks_fixtures", "tests.stock_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root", "frozen_today")]

D = Decimal


def priced(alice: User, home: Home, name: str, price: str | None, stock: str = "10") -> Article:
    article = stock_services.create_article(
        alice, home, name=name, unit="piece", unit_price=None if price is None else D(price)
    )
    stock_services.record_movement(alice, article, kind="entry", quantity=D(stock))
    return article


@pytest.fixture
def paint(alice: User, alice_home: Home) -> Article:
    return priced(alice, alice_home, "Paint", "45.50")


@pytest.fixture
def filler(alice: User, alice_home: Home) -> Article:
    return priced(alice, alice_home, "Filler", "60.00")


@pytest.fixture
def needs_both(alice: User, owner_task: Task, paint: Article, filler: Article) -> Task:
    """2 x 45.50 of paint and 1 x 60.00 of filler: 151.00."""
    materials.add_material(alice, owner_task, paint, D("2"))
    materials.add_material(alice, owner_task, filler, D("1"))
    return owner_task


def real(occurrence: Occurrence) -> Decimal | None:
    occurrence.refresh_from_db()
    return costs.cost_of(occurrence).total


def done_row(home: Home, task: Task) -> budget.TaskRow:
    return next(r for r in budget.load(home).tasks if r.task.id == task.id)


class TestTheRule:
    def test_done_without_typing_anything_costs_the_stock(
        self, alice: User, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        cost = costs.cost_of(Occurrence.objects.get(pk=owner_occurrence.pk))
        assert cost.stock.amount == D("151.00")
        assert cost.typed is None
        assert cost.total == D("151.00")

    def test_the_amount_typed_is_added_to_the_stock(
        self, alice: User, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence, actual_cost=D("40"))
        assert real(owner_occurrence) == D("191.00")

    def test_the_stock_is_counted_once(
        self, alice: User, alice_home: Home, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence, actual_cost=D("40"))
        row = done_row(alice_home, needs_both)
        figures = row.figures(budget.anytime)
        assert (figures.paid, figures.stock, figures.typed) == (D("191.00"), D("151.00"), D("40"))
        assert agenda.cost_of(Occurrence.objects.filter(pk=owner_occurrence.pk)).total == D(
            "191.00"
        )

    def test_undo_takes_the_value_out(
        self, alice: User, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        services.undo(alice, Occurrence.objects.get(pk=owner_occurrence.pk))
        assert costs.stock_used_by(Occurrence.objects.get(pk=owner_occurrence.pk)).amount == 0
        assert real(owner_occurrence) is None
        assert MaterialDraw.all_objects.filter(occurrence=owner_occurrence).count() == 2

    def test_an_undo_leaves_nothing_behind_for_the_next_closing(
        self,
        alice: User,
        needs_both: Task,
        owner_occurrence: Occurrence,
        filler: Article,
    ) -> None:
        services.complete(alice, owner_occurrence)
        services.undo(alice, Occurrence.objects.get(pk=owner_occurrence.pk))
        materials.remove_material(alice, needs_both.materials.get(article=filler))
        services.complete(alice, Occurrence.objects.get(pk=owner_occurrence.pk))
        assert real(owner_occurrence) == D("91.00")  # the paint only: the filler went back

    def test_a_price_changed_afterwards_changes_nothing(
        self, alice: User, needs_both: Task, owner_occurrence: Occurrence, paint: Article
    ) -> None:
        services.complete(alice, owner_occurrence)
        stock_services.update_article(alice, paint, unit_price=D("99.00"))
        assert Article.objects.get(pk=paint.pk).unit_price == D("99.00")
        assert real(owner_occurrence) == D("151.00")

    def test_closing_again_after_an_undo_takes_the_price_of_that_day(
        self, alice: User, needs_both: Task, owner_occurrence: Occurrence, paint: Article
    ) -> None:
        services.complete(alice, owner_occurrence)
        services.undo(alice, Occurrence.objects.get(pk=owner_occurrence.pk))
        stock_services.update_article(alice, paint, unit_price=D("50.00"))
        services.complete(alice, Occurrence.objects.get(pk=owner_occurrence.pk))
        assert real(owner_occurrence) == D("160.00")  # 2 x 50.00 + 60.00

    def test_the_quantity_used_keeps_the_price_of_the_closing(
        self, alice: User, needs_both: Task, owner_occurrence: Occurrence, paint: Article
    ) -> None:
        services.complete(alice, owner_occurrence)
        stock_services.update_article(alice, paint, unit_price=D("99.00"))
        materials.adjust_draws(
            alice,
            Occurrence.objects.get(pk=owner_occurrence.pk),
            {paint.pk: D("1")},
            on=datetime.date(2026, 6, 15),
        )
        assert real(owner_occurrence) == D("105.50")  # 1 x 45.50 + 60.00

    def test_an_item_without_a_price_counts_for_nothing_and_is_said(
        self, alice: User, alice_home: Home, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        brush = priced(alice, alice_home, "Brush", None)
        materials.add_material(alice, owner_task, brush, D("3"))
        services.complete(alice, owner_occurrence)
        used = costs.stock_used_by(Occurrence.objects.get(pk=owner_occurrence.pk))
        assert (used.amount, used.unpriced) == (D("0.00"), 1)
        assert real(owner_occurrence) is None  # done without a cost, as before
        assert costs.unpriced_draws_of(owner_task.pk) == 1

    def test_rounded_once_to_the_cent(
        self, alice: User, alice_home: Home, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        for name in ("A", "B", "C"):
            materials.add_material(
                alice, owner_task, priced(alice, alice_home, name, "0.01"), D("0.50")
            )
        services.complete(alice, owner_occurrence)
        # 3 x 0.005 = 0.015, rounded once: 0.02 (each rounded first would make 0.03).
        assert costs.stock_used_by(Occurrence.objects.get(pk=owner_occurrence.pk)).amount == D(
            "0.02"
        )


class TestBudget:
    def test_by_nature_the_stock_is_materials(
        self, alice: User, alice_home: Home, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        ledger = budget.load(alice_home)
        natures = {line.label: line.amounts for line in budget.by_nature(ledger, 2026)}
        assert natures[BudgetNature.MATERIAL].paid == D("151.00")
        assert natures[BudgetNature.LABOUR].paid == 0
        assert natures[BudgetNature.OTHER].paid == 0

    def test_the_amount_typed_follows_the_share_of_the_estimate(
        self, alice: User, alice_home: Home, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.update_task(alice, needs_both, labour_budget=D("151.00"))
        services.complete(alice, owner_occurrence, actual_cost=D("40"))
        natures = {
            line.label: line.amounts for line in budget.by_nature(budget.load(alice_home), 2026)
        }
        # Estimate: 151.00 of materials from the stock, 151.00 of labour: 40 shared half-half.
        assert natures[BudgetNature.MATERIAL].paid == D("171.00")
        assert natures[BudgetNature.LABOUR].paid == D("20.00")
        assert natures[BudgetNature.OTHER].paid == 0

    def test_without_an_estimate_only_the_amount_typed_is_other(self) -> None:
        figures = budget.TaskFigures(1, D("0"), D("0"), D("191.00"), 0, 0, D("151.00"))
        assert budget.real_cost_split(figures, D("0"), D("0")) == (D("0"), D("0"))
        assert figures.typed == D("40.00")

    def test_by_task_and_the_task_page(
        self,
        alice: User,
        alice_home: Home,
        needs_both: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        services.complete(alice, owner_occurrence)
        (line,) = budget.by_task(budget.load(alice_home), 2026).lines
        assert line.amounts.paid == D("151.00")
        assert line.extra["total_paid"] == D("151.00")
        assert line.extra["total_stock_used"] == D("151.00")
        assert line.extra["missing_costs"] == 0
        client = Client()
        client.force_login(alice)
        page = client.get(
            reverse("tasks:detail", kwargs={"home_id": alice_home.id, "task_id": needs_both.id})
        )
        body = page.content.decode()
        assert "data-stock-used" in body
        assert "151.00" in body

    def test_the_budget_api_gives_the_stock_used(
        self, alice: User, alice_home: Home, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        client = APIClient()
        client.force_authenticate(alice)
        answer = client.get(f"/api/v1/homes/{alice_home.id}/budget/tasks/?year=2026")
        assert answer.status_code == 200, answer.content
        (line,) = answer.json()["lines"]
        assert (line["total_paid"], line["total_stock_used"]) == ("151.00", "151.00")


class TestScreens:
    def test_the_form_of_what_was_done_shows_the_stock_used(
        self, alice: User, alice_home: Home, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        client = Client()
        client.force_login(alice)
        page = client.get(
            reverse(
                "tasks:occurrence-edit",
                kwargs={
                    "home_id": alice_home.id,
                    "task_id": needs_both.id,
                    "occurrence_id": owner_occurrence.id,
                },
            )
        )
        body = page.content.decode()
        assert "Stock used: €151.00" in body
        assert "Other costs (labour, purchases outside the stock)" in body

    def test_the_form_says_an_item_taken_without_a_price(
        self, alice: User, alice_home: Home, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        materials.add_material(alice, owner_task, priced(alice, alice_home, "Brush", None), D("1"))
        services.complete(alice, owner_occurrence)
        client = Client()
        client.force_login(alice)
        page = client.get(
            reverse(
                "tasks:occurrence-edit",
                kwargs={
                    "home_id": alice_home.id,
                    "task_id": owner_task.id,
                    "occurrence_id": owner_occurrence.id,
                },
            )
        )
        assert "data-stock-unpriced" in page.content.decode()

    def test_a_task_without_stock_keeps_its_label(
        self, alice: User, alice_home: Home, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        client = Client()
        client.force_login(alice)
        page = client.get(
            reverse(
                "tasks:occurrence-edit",
                kwargs={
                    "home_id": alice_home.id,
                    "task_id": owner_task.id,
                    "occurrence_id": owner_occurrence.id,
                },
            )
        )
        body = page.content.decode()
        assert "Actual cost" in body
        assert "Stock used" not in body

    def test_the_history_shows_and_adds_the_real_cost(
        self, alice: User, alice_home: Home, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence, actual_cost=D("40"))
        client = Client()
        client.force_login(alice)
        page = client.get(reverse("tasks:list", kwargs={"home_id": alice_home.id}) + "?status=done")
        assert "191.00" in page.content.decode()
        cost = agenda.cost_of(agenda.history(alice_home, agenda.HistoryFilter()))
        assert (cost.total, cost.with_cost, cost.count) == (D("191.00"), 1, 1)


class TestApiAndFiles:
    def test_the_occurrence_gives_the_stock_and_the_total(
        self, alice: User, alice_home: Home, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence, actual_cost=D("40"))
        client = APIClient()
        client.force_authenticate(alice)
        url = f"/api/v1/homes/{alice_home.id}/occurrences/{owner_occurrence.id}/"
        body = client.get(url).json()
        assert (body["actual_cost"], body["stock_cost"], body["real_cost"]) == (
            "40.00",
            "151.00",
            "191.00",
        )
        assert sorted((line["unit_price"], line["value"]) for line in body["stock"]) == [
            ("45.50", "91.00"),
            ("60.00", "60.00"),
        ]
        listed = client.get(f"/api/v1/homes/{alice_home.id}/occurrences/?status=done").json()
        assert [r["real_cost"] for r in listed["results"]] == ["191.00"]

    def test_patch_sets_the_amount_typed_only(
        self, alice: User, alice_home: Home, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        client = APIClient()
        client.force_authenticate(alice)
        url = f"/api/v1/homes/{alice_home.id}/occurrences/{owner_occurrence.id}/"
        body = client.patch(url, {"actual_cost": "40.00", "real_cost": "1.00"}, format="json")
        assert body.status_code == 200, body.content
        assert (body.json()["actual_cost"], body.json()["real_cost"]) == ("40.00", "191.00")
        assert Occurrence.objects.get(pk=owner_occurrence.pk).actual_cost == D("40.00")

    def test_the_history_file_has_the_two_columns(
        self, alice: User, alice_home: Home, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence, actual_cost=D("40"))
        data, _name = csv_export.build_csv(alice_home, csv_export.ENTITIES["history"])
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        done = [r for r in rows if r["status"] == "done"]
        assert [(r["actual_cost"], r["stock_cost"], r["real_cost"]) for r in done] == [
            ("40.00", "151.00", "191.00")
        ]
        assert rows[0]["currency"] == alice_home.currency

    def test_the_budget_file_has_the_stock_used(
        self, alice: User, alice_home: Home, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        client = Client()
        client.force_login(alice)
        answer = client.get(
            reverse("budget:tasks", kwargs={"home_id": alice_home.id}) + "?year=2026&export=csv"
        )
        text = b"".join(answer.streaming_content if answer.streaming else [answer.content])
        assert "Of which stock used" in text.decode("utf-8-sig")


class TestAnotherHome:
    def test_the_occurrence_of_another_home_is_not_found(
        self,
        alice: User,
        bob: User,
        alice_home: Home,
        bob_home: Home,
        needs_both: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        services.complete(alice, owner_occurrence)
        client = APIClient()
        client.force_authenticate(bob)
        answer = client.get(f"/api/v1/homes/{alice_home.id}/occurrences/{owner_occurrence.id}/")
        assert answer.status_code in (403, 404)
        answer = client.get(f"/api/v1/homes/{bob_home.id}/occurrences/{owner_occurrence.id}/")
        assert answer.status_code == 404
        assert client.get(f"/api/v1/homes/{bob_home.id}/occurrences/cost/").json()["total"] == (
            "0.00"
        )

    def test_the_budget_of_another_home_counts_none_of_it(
        self, alice: User, bob_home: Home, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        assert costs.stock_used_in(bob_home.pk) == {}
        natures = budget.by_nature(budget.load(bob_home), 2026)
        assert all(line.amounts.paid == 0 for line in natures)


# ---------- the sync API: the server owns the price ----------


@pytest.fixture
def _fresh_rate_limits() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def phone(alice: User, alice_home: Home) -> Connected:
    return connect(alice, [alice_home])


@pytest.mark.usefixtures("_fresh_rate_limits")
class TestSync:
    def test_a_phone_may_not_write_the_price(
        self,
        phone: Connected,
        alice: User,
        alice_home: Home,
        needs_both: Task,
        owner_occurrence: Occurrence,
        paint: Article,
    ) -> None:
        services.complete(alice, owner_occurrence)
        draw_id = ids.draw(alice_home.pk, owner_occurrence.pk, paint.pk)
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "tasks.materialdraw", draw_id, {"unit_price": "0.01"})],
        )
        assert result_of(answer)["code"] == "forbidden"
        assert MaterialDraw.objects.get(pk=draw_id).unit_price == D("45.50")

    def test_a_draw_a_phone_makes_takes_the_price_of_the_article(
        self,
        phone: Connected,
        alice: User,
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
        paint: Article,
    ) -> None:
        draw_id = ids.draw(alice_home.pk, owner_occurrence.pk, paint.pk)
        fields: dict[str, Any] = {
            "occurrence_id": str(owner_occurrence.pk),
            "article_id": str(paint.pk),
            "wanted": "2.00",
            "drawn": "2.00",
        }
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "tasks.materialdraw", draw_id, fields, c=stamp(1_000))],
        )
        assert result_of(answer)["status"] in ("applied", "repaired"), answer.content
        assert MaterialDraw.objects.get(pk=draw_id).unit_price == D("45.50")

    def test_a_later_write_of_the_phone_keeps_the_price(
        self,
        phone: Connected,
        alice: User,
        alice_home: Home,
        needs_both: Task,
        owner_occurrence: Occurrence,
        paint: Article,
    ) -> None:
        services.complete(alice, owner_occurrence)
        stock_services.update_article(alice, paint, unit_price=D("99.00"))
        draw_id = ids.draw(alice_home.pk, owner_occurrence.pk, paint.pk)
        answer = send(
            phone.access,
            alice_home.pk,
            [
                op(
                    alice_home.pk,
                    "tasks.materialdraw",
                    draw_id,
                    {"wanted": "1.00", "drawn": "1.00"},
                    c=stamp(60_000),
                )
            ],
        )
        assert result_of(answer)["status"] == "applied", answer.content
        assert MaterialDraw.objects.get(pk=draw_id).unit_price == D("45.50")
        assert real(owner_occurrence) == D("105.50")


class TestMigration:
    def test_the_draws_of_before_take_the_price_of_now(
        self, alice: User, alice_home: Home, needs_both: Task, owner_occurrence: Occurrence
    ) -> None:
        brush = priced(alice, alice_home, "Brush", None)
        materials.add_material(alice, needs_both, brush, D("1"))
        services.complete(alice, owner_occurrence)
        MaterialDraw.all_objects.update(unit_price=None)  # as before the migration
        assert real(owner_occurrence) is None
        migration = importlib.import_module("housecairn.tasks.migrations.0013_unit_price_of_draws")
        migration.price_the_draws_of_before(django_apps, None)
        prices = sorted(
            (str(p) for p in MaterialDraw.objects.values_list("unit_price", flat=True)),
        )
        assert prices == ["45.50", "60.00", "None"]
        assert real(owner_occurrence) == D("151.00")


def test_the_token_of_another_home_cannot_read_the_cost(
    alice: User, bob: User, bob_home: Home, alice_home: Home, needs_both: Task
) -> None:
    _token, secret = token_services.create_token(
        bob, name="t", scopes=["read"], homes=[bob_home], lifetime_days=30
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
    answer = client.get(f"/api/v1/homes/{alice_home.id}/occurrences/cost/")
    assert answer.status_code in (403, 404)
    assert client.get(f"/api/v1/homes/{bob_home.id}/occurrences/cost/").status_code == 200
