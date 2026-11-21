"""A task done with nothing typed counts what it planned as paid (ADR 0091, amends 0090).

The maintainer's rule: a task with only a labour or a material budget, marked done, has paid
those amounts. The real cost is the stock used plus the rest, which is the amount typed when
there is one (0 included), else the plan kept when the task was marked done: the labour
planned, and the material budget typed when the stock does not work the materials out.
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
from housecairn.budget import services as budget
from housecairn.homes import csv_export
from housecairn.homes.models import Home
from housecairn.projects import services as project_services
from housecairn.projects.models import BudgetNature
from housecairn.stock import services as stock_services
from housecairn.stock.models import Article
from housecairn.tasks import agenda, costs, materials, services
from housecairn.tasks.models import Occurrence, Status, Task
from tests.oauth_support import Connected, connect
from tests.test_sync_push_api import op, result_of, send, stamp

pytest_plugins = ("tests.tasks_fixtures", "tests.stock_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root", "frozen_today")]

D = Decimal
DUE = datetime.date(2026, 6, 1)


def priced(alice: User, home: Home, name: str, price: str, stock: str = "10") -> Article:
    article = stock_services.create_article(
        alice, home, name=name, unit="piece", unit_price=D(price)
    )
    stock_services.record_movement(alice, article, kind="entry", quantity=D(stock))
    return article


@pytest.fixture
def with_labour(alice: User, owner_task: Task) -> Task:
    return services.update_task(alice, owner_task, labour_budget=D("120"))


@pytest.fixture
def with_stock(alice: User, alice_home: Home, owner_task: Task) -> Task:
    """2 x 45.50 of paint and 1 x 60.00 of filler: 151.00, and 80.00 of labour planned."""
    materials.add_material(alice, owner_task, priced(alice, alice_home, "Paint", "45.50"), D(2))
    materials.add_material(alice, owner_task, priced(alice, alice_home, "Filler", "60.00"), D(1))
    return services.update_task(alice, owner_task, labour_budget=D("80"))


def fresh(occurrence: Occurrence) -> Occurrence:
    return Occurrence.all_objects.get(pk=occurrence.pk)


def real(occurrence: Occurrence) -> Decimal | None:
    return costs.cost_of(fresh(occurrence)).total


def natures(home: Home) -> dict[str, Decimal]:
    return {line.label: line.amounts.paid for line in budget.by_nature(budget.load(home), 2026)}


def edit_page(client: Client, home: Home, occurrence: Occurrence) -> str:
    url = reverse(
        "tasks:occurrence-edit",
        kwargs={
            "home_id": home.id,
            "task_id": occurrence.task_id,
            "occurrence_id": occurrence.id,
        },
    )
    return client.get(url).content.decode()


class TestTheRule:
    def test_done_with_nothing_typed_pays_the_labour_planned(
        self, alice: User, alice_home: Home, with_labour: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        row = fresh(owner_occurrence)
        assert (row.planned_labour, row.planned_material) == (D("120.00"), None)
        assert real(owner_occurrence) == D("120.00")
        paid = natures(alice_home)
        assert paid[BudgetNature.LABOUR] == D("120.00")
        assert paid[BudgetNature.MATERIAL] == paid[BudgetNature.OTHER] == 0

    def test_an_amount_typed_replaces_the_plan(
        self, alice: User, with_labour: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence, actual_cost=D("90"))
        assert real(owner_occurrence) == D("90.00")

    def test_zero_typed_is_zero_not_the_plan(
        self, alice: User, alice_home: Home, with_labour: Task, owner_occurrence: Occurrence
    ) -> None:
        """Mutant: 0 read as empty (`typed or plan`) makes 120.00 here."""
        services.complete(alice, owner_occurrence, actual_cost=D("0"))
        assert real(owner_occurrence) == D("0.00")
        assert all(amount == 0 for amount in natures(alice_home).values())
        row = next(r for r in budget.load(alice_home).tasks if r.task.id == with_labour.id)
        assert row.figures(budget.anytime).missing == 0

    def test_a_budget_changed_afterwards_changes_nothing(
        self, alice: User, alice_home: Home, with_labour: Task, owner_occurrence: Occurrence
    ) -> None:
        """Mutant: the plan read from the task of now makes 300.00 here."""
        services.complete(alice, owner_occurrence)
        services.update_task(alice, with_labour, labour_budget=D("300"), material_budget=D("50"))
        assert real(owner_occurrence) == D("120.00")
        assert natures(alice_home)[BudgetNature.LABOUR] == D("120.00")
        assert agenda.cost_of(Occurrence.objects.filter(pk=owner_occurrence.pk)).total == D(
            "120.00"
        )

    def test_undo_leaves_nothing(
        self, alice: User, with_labour: Task, owner_occurrence: Occurrence
    ) -> None:
        """Mutant: an undo that keeps the plan leaves 120.00 on the open occurrence."""
        services.complete(alice, owner_occurrence)
        services.undo(alice, fresh(owner_occurrence))
        row = fresh(owner_occurrence)
        assert (row.planned_labour, row.planned_material) == (None, None)
        assert real(owner_occurrence) is None
        services.update_task(alice, with_labour, labour_budget=D("200"))
        services.complete(alice, fresh(owner_occurrence))
        assert real(owner_occurrence) == D("200.00")  # closing again keeps the plan of then

    def test_stock_and_labour_planned_add_up(
        self, alice: User, alice_home: Home, with_stock: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        assert real(owner_occurrence) == D("231.00")
        paid = natures(alice_home)
        assert (paid[BudgetNature.MATERIAL], paid[BudgetNature.LABOUR]) == (D("151.00"), D("80"))
        assert paid[BudgetNature.OTHER] == 0

    def test_the_material_budget_is_not_counted_again_over_the_stock(
        self, alice: User, alice_home: Home, with_stock: Task, owner_occurrence: Occurrence
    ) -> None:
        """Mutant: the material budget kept over a priced stock makes 281.00 here."""
        services.update_task(alice, with_stock, material_budget=D("50"))
        services.complete(alice, owner_occurrence)
        assert fresh(owner_occurrence).planned_material is None
        assert real(owner_occurrence) == D("231.00")
        assert natures(alice_home)[BudgetNature.MATERIAL] == D("151.00")

    def test_a_material_budget_without_a_priced_stock_is_materials(
        self, alice: User, alice_home: Home, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        services.update_task(alice, owner_task, material_budget=D("35"))
        services.complete(alice, owner_occurrence)
        assert real(owner_occurrence) == D("35.00")
        assert natures(alice_home)[BudgetNature.MATERIAL] == D("35.00")

    def test_with_stock_an_amount_typed_replaces_the_plan_not_the_stock(
        self, alice: User, alice_home: Home, with_stock: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence, actual_cost=D("40"))
        assert real(owner_occurrence) == D("191.00")
        paid = natures(alice_home)
        # Estimate 151.00 of materials from the stock and 80.00 of labour: 40 shared by it.
        assert paid[BudgetNature.MATERIAL] + paid[BudgetNature.LABOUR] == D("191.00")
        assert paid[BudgetNature.LABOUR] == D("13.85")

    def test_a_task_without_a_plan_is_still_done_without_a_cost(
        self, alice: User, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        assert real(owner_occurrence) is None

    def test_a_task_of_a_project_keeps_no_plan(
        self, alice: User, alice_home: Home, owner_occurrence: Occurrence, with_labour: Task
    ) -> None:
        project = project_services.create_project(alice, alice_home, title="Kitchen")
        project_services.attach_task(alice, project, with_labour)
        services.complete(alice, owner_occurrence)
        assert costs.planned_of(fresh(owner_occurrence)) == costs.NO_PLAN
        assert real(owner_occurrence) is None


class TestScreensAndFiles:
    def test_the_form_says_what_will_be_counted(
        self, alice: User, alice_home: Home, with_stock: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        client = Client()
        client.force_login(alice)
        body = edit_page(client, alice_home, owner_occurrence)
        assert "Stock used: €151.00" in body
        assert "Labour planned: €80.00" in body
        assert "Materials planned" not in body
        assert "An amount typed replaces them; the stock used stays counted." in body

    def test_the_form_without_stock(
        self, alice: User, alice_home: Home, with_labour: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        client = Client()
        client.force_login(alice)
        body = edit_page(client, alice_home, owner_occurrence)
        assert "Labour planned: €120.00" in body
        assert "Stock used" not in body
        assert "an amount typed, 0 included, replaces them" in body

    def test_zero_typed_in_the_form_stays_zero(
        self, alice: User, alice_home: Home, with_labour: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        client = Client()
        client.force_login(alice)
        url = reverse(
            "tasks:occurrence-edit",
            kwargs={
                "home_id": alice_home.id,
                "task_id": with_labour.id,
                "occurrence_id": owner_occurrence.id,
            },
        )
        data = {"completed_on": "2026-06-15", "actual_cost": "0", "completion_note": ""}
        assert client.post(url, data).status_code == 302
        assert fresh(owner_occurrence).actual_cost == D("0")
        assert real(owner_occurrence) == D("0.00")
        data["actual_cost"] = ""
        assert client.post(url, data).status_code == 302
        assert fresh(owner_occurrence).actual_cost is None
        assert real(owner_occurrence) == D("120.00")

    def test_the_task_page_and_the_history(
        self, alice: User, alice_home: Home, with_labour: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        client = Client()
        client.force_login(alice)
        page = client.get(
            reverse("tasks:detail", kwargs={"home_id": alice_home.id, "task_id": with_labour.id})
        ).content.decode()
        assert "data-from-plan" in page
        assert "€120.00" in page
        listed = client.get(
            reverse("tasks:list", kwargs={"home_id": alice_home.id}) + "?status=done"
        )
        assert "€120.00" in listed.content.decode()
        cost = agenda.cost_of(agenda.history(alice_home, agenda.HistoryFilter()))
        assert (cost.total, cost.with_cost) == (D("120.00"), 1)

    def test_the_api_and_the_files(
        self, alice: User, alice_home: Home, with_labour: Task, owner_occurrence: Occurrence
    ) -> None:
        services.complete(alice, owner_occurrence)
        client = APIClient()
        client.force_authenticate(alice)
        url = f"/api/v1/homes/{alice_home.id}/occurrences/{owner_occurrence.id}/"
        body = client.get(url).json()
        assert (body["actual_cost"], body["planned_labour"], body["real_cost"]) == (
            None,
            "120.00",
            "120.00",
        )
        answer = client.patch(url, {"actual_cost": "0.00", "planned_labour": "1.00"}, format="json")
        assert answer.status_code == 200, answer.content
        assert (answer.json()["actual_cost"], answer.json()["real_cost"]) == ("0.00", "0.00")
        assert fresh(owner_occurrence).planned_labour == D("120.00")
        answer = client.patch(url, {"actual_cost": None}, format="json")
        assert answer.json()["real_cost"] == "120.00"
        data, _name = csv_export.build_csv(alice_home, csv_export.ENTITIES["history"])
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        done = [r for r in rows if r["status"] == "done"]
        assert [(r["planned_labour"], r["real_cost"]) for r in done] == [("120.00", "120.00")]
        tasks = client.get(f"/api/v1/homes/{alice_home.id}/budget/tasks/?year=2026").json()
        (line,) = tasks["lines"]
        assert (line["total_paid"], line["total_from_plan"]) == ("120.00", "120.00")

    def test_another_home_sees_none_of_it(
        self,
        alice: User,
        bob: User,
        alice_home: Home,
        bob_home: Home,
        with_labour: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        services.complete(alice, owner_occurrence)
        client = APIClient()
        client.force_authenticate(bob)
        answer = client.get(f"/api/v1/homes/{alice_home.id}/occurrences/{owner_occurrence.id}/")
        assert answer.status_code in (403, 404)
        answer = client.get(f"/api/v1/homes/{bob_home.id}/occurrences/{owner_occurrence.id}/")
        assert answer.status_code == 404
        assert all(amount == 0 for amount in natures(bob_home).values())


# ---------- the sync API: the server keeps the plan ----------


@pytest.fixture
def _fresh_rate_limits() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def phone(alice: User, alice_home: Home) -> Connected:
    return connect(alice, [alice_home])


def push(phone: Connected, home: Home, row: Occurrence, fields: dict[str, Any], at: int) -> Any:
    """One push of one operation; `at`, the offset of its clock, numbers the push too."""
    answer = send(
        phone.access,
        home.pk,
        [op(home.pk, "tasks.occurrence", row.pk, fields, c=stamp(at))],
        number=at,
    )
    return result_of(answer)


@pytest.mark.usefixtures("_fresh_rate_limits")
class TestSync:
    def test_a_closing_pushed_keeps_the_plan_and_an_undo_pushed_empties_it(
        self,
        phone: Connected,
        alice: User,
        alice_home: Home,
        with_labour: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        done = {"status": "done", "completed_on": "2026-06-15"}
        assert push(phone, alice_home, owner_occurrence, done, 1_000)["status"] in (
            "applied",
            "repaired",
        )
        row = fresh(owner_occurrence)
        assert (row.status, row.planned_labour) == (Status.DONE, D("120.00"))
        assert real(owner_occurrence) == D("120.00")
        # A later write of the done occurrence keeps the plan of its closing.
        services.update_task(alice, with_labour, labour_budget=D("300"))
        push(phone, alice_home, owner_occurrence, {"completion_note": "Done"}, 2_000)
        assert fresh(owner_occurrence).planned_labour == D("120.00")
        undone = {"status": "planned", "completed_on": None}
        push(phone, alice_home, owner_occurrence, undone, 3_000)
        assert costs.planned_of(fresh(owner_occurrence)) == costs.NO_PLAN

    def test_zero_pushed_stays_zero(
        self,
        phone: Connected,
        alice_home: Home,
        with_labour: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        done = {"status": "done", "completed_on": "2026-06-15", "actual_cost": "0.00"}
        push(phone, alice_home, owner_occurrence, done, 1_000)
        assert fresh(owner_occurrence).actual_cost == D("0")
        assert real(owner_occurrence) == D("0.00")

    def test_a_phone_may_not_write_the_plan(
        self,
        phone: Connected,
        alice: User,
        alice_home: Home,
        with_labour: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        services.complete(alice, owner_occurrence)
        for name in ("planned_labour", "planned_material"):
            answer = push(phone, alice_home, owner_occurrence, {name: "1.00"}, len(name) * 1_000)
            assert answer["code"] == "forbidden"
        assert fresh(owner_occurrence).planned_labour == D("120.00")


class TestMigration:
    def test_the_times_done_before_keep_the_plan_of_now(
        self, alice: User, alice_home: Home, with_stock: Task, owner_occurrence: Occurrence
    ) -> None:
        services.update_task(alice, with_stock, material_budget=D("50"))
        services.complete(alice, owner_occurrence)
        other = services.create_task(
            alice, alice_home, title="Sweep", material_budget=D("12"), due_on=DUE
        )
        services.complete(alice, services.open_occurrence_of(other))
        bare = services.create_task(alice, alice_home, title="Look", due_on=DUE)
        services.complete(alice, services.open_occurrence_of(bare))
        project = project_services.create_project(alice, alice_home, title="Kitchen")
        tiled = project_services.add_task(
            alice, project, title="Tile", due_on=DUE, labour_budget=D("500")
        )
        services.complete(alice, services.open_occurrence_of(tiled))
        Occurrence.all_objects.update(planned_labour=None, planned_material=None)  # as before
        assert real(owner_occurrence) == D("151.00")
        migration = importlib.import_module("housecairn.tasks.migrations.0014_plan_kept_when_done")
        migration.keep_the_plan_of_now(django_apps, None)
        assert real(owner_occurrence) == D("231.00")  # the material budget is not counted
        swept = Occurrence.objects.get(task=other, status=Status.DONE)
        assert (swept.planned_labour, swept.planned_material) == (None, D("12.00"))
        for none in (bare, tiled):  # nothing planned; a task of a project
            done = Occurrence.objects.get(task=none, status=Status.DONE)
            assert costs.planned_of(done) == costs.NO_PLAN
        assert not Occurrence.objects.filter(
            status__in=[Status.PLANNED, Status.POSTPONED], planned_labour__isnull=False
        ).exists()
