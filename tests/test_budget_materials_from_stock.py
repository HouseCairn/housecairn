"""The materials of a task are worked out from its priced stock items, never typed twice (ADR 0081)."""

import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.budget import services as budget
from housecairn.homes.models import Home
from housecairn.stock import services as stock_services
from housecairn.tasks import materials
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Task

pytest_plugins = ("tests.rooms_fixtures", "tests.stock_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root")]

D = Decimal
TODAY = datetime.date(2026, 1, 5)


def descaling(alice: User, home: Home, **fields: object) -> Task:
    """The maintainer's example: descale the espresso machine every 3 months, one filter."""
    task = task_services.create_task(
        alice,
        home,
        title="Descale the espresso machine",
        due_on=TODAY,
        repeat_mode="after_last",
        repeat_unit="month",
        repeat_interval=3,
        **fields,
    )
    water_filter = stock_services.create_article(
        alice, home, name="Water filter", unit="piece", unit_price=D("7.90")
    )
    materials.add_material(alice, task, water_filter, D("1"))
    return task


def row_of(home: Home, task: Task) -> budget.TaskRow:
    return next(r for r in budget.load(home, TODAY).tasks if r.task.id == task.id)


class TestTheRule:
    def test_the_example_of_the_maintainer(self, alice: User, alice_home: Home) -> None:
        row = row_of(alice_home, descaling(alice, alice_home))
        assert row.material_from_stock
        assert row.estimate == D("7.90")
        assert row.annual_cost == D("31.60")

    def test_a_typed_budget_is_not_added_to_the_priced_stock(
        self, alice: User, alice_home: Home
    ) -> None:
        row = row_of(alice_home, descaling(alice, alice_home, material_budget=D("20")))
        assert row.estimate == D("7.90")
        assert row.annual_cost == D("31.60")
        (line,) = budget.by_task(budget.load(alice_home, TODAY), 2026).lines
        assert line.amounts.planned == D("31.60")  # four times in 2026

    def test_without_priced_stock_the_typed_budget_counts(
        self, alice: User, alice_home: Home
    ) -> None:
        task = task_services.create_task(
            alice, alice_home, title="Paint", due_on=TODAY, material_budget=D("20")
        )
        brush = stock_services.create_article(alice, alice_home, name="Brush")
        materials.add_material(alice, task, brush, D("1"))
        row = row_of(alice_home, task)
        assert not row.material_from_stock
        assert row.estimate == D("20")
        assert row.stock.unpriced == 1

    def test_the_rule_is_one_function(self) -> None:
        priced = budget.StockPlan(D("7.90"), 1, 0)
        assert budget.material_of(D("20"), priced) == D("7.90")
        assert budget.material_of(D("20"), budget.StockPlan()) == D("20")
        assert budget.material_of(None, budget.StockPlan(D("0"), 1, 0)) == D("0")
        assert budget.material_of(None, budget.StockPlan()) == D("0")


class TestTheScreens:
    def test_the_task_page_shows_planned_and_real_apart(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = descaling(alice, alice_home)
        page = signed_in.get(
            reverse("tasks:detail", kwargs={"home_id": alice_home.id, "task_id": task.id})
        ).text
        cost = page.split("data-task-cost")[1].split("</section>")[0]
        planned = cost.split("data-planned")[1].split("</dl>")[0]
        real = cost.split("data-real")[1].split("</dl>")[0]
        assert "€7.90" in planned
        assert "Materials, from the stock" in planned
        assert "Paid so far" in real and "€7.90" not in real
        assert cost.index(">Planned<") < cost.index(">Real<")
        page = signed_in.get(
            reverse("tasks:detail", kwargs={"home_id": alice_home.id, "task_id": task.id}),
            HTTP_ACCEPT_LANGUAGE="fr",
        ).text
        assert ">Prévu<" in page and ">Réel<" in page

    def test_the_form_asks_for_no_second_figure(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = descaling(alice, alice_home, material_budget=D("20"))
        page = signed_in.get(
            reverse("tasks:edit", kwargs={"home_id": alice_home.id, "task_id": task.id})
        ).text
        assert 'type="number" name="material_budget"' not in page
        assert 'type="hidden" name="material_budget" value="20.00"' in page
        assert "€7.90 each time, calculated from the stock items of the task." in page

    def test_the_form_without_priced_stock_keeps_the_field(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = task_services.create_task(alice, alice_home, title="Paint", due_on=TODAY)
        page = signed_in.get(
            reverse("tasks:edit", kwargs={"home_id": alice_home.id, "task_id": task.id})
        ).text
        assert 'name="material_budget"' in page
        assert 'type="hidden" name="material_budget"' not in page
        assert "data-material-from-stock" not in page

    def test_the_budget_of_the_upkeep_says_the_materials_come_from_the_stock(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        descaling(alice, alice_home, material_budget=D("20"))
        page = signed_in.get(
            reverse("budget:tasks", kwargs={"home_id": alice_home.id}) + "?year=2026"
        ).text
        assert "materials from the stock: €7.90" in page
