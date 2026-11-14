"""Every amount planned on a task is in Planned, once (docs/adr/0073, completing 0051).

Two kinds of task planned nothing in the budget before: an open task of the upkeep with no
date, and every task of a project. One test per case; each fails on the code before it.
"""

import csv
import datetime
import io
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.budget import services
from housecairn.homes.models import Home
from housecairn.projects import services as project_services
from housecairn.projects.models import BudgetNature, Project
from housecairn.projects.services import Amounts
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Room
from housecairn.stock import services as stock_services
from housecairn.tasks import materials
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Occurrence, Task

pytest_plugins = ("tests.rooms_fixtures", "tests.projects_fixtures")
pytestmark = pytest.mark.django_db

D = Decimal
TODAY = datetime.date(2026, 11, 9)
YEAR = TODAY.year


def upkeep(
    alice: User, home: Home, title: str, due: datetime.date | None, **fields: object
) -> Task:
    return task_services.create_task(alice, home, title=title, due_on=due, **fields)


def project(
    alice: User,
    home: Home,
    title: str,
    status: str,
    *,
    rooms: tuple[Room, ...] = (),
    lines: tuple[tuple[str, str], ...] = (),
) -> Project:
    """A project created in `YEAR`, with budget lines (nature, planned)."""
    found = project_services.create_project(
        alice, home, title=title, status=status, rooms=list(rooms)
    )
    for nature, planned in lines:
        project_services.create_line(
            alice, found, label=nature.title(), nature=nature, planned=D(planned)
        )
    Project.objects.filter(id=found.id).update(
        created_at=datetime.datetime(YEAR, 3, 1, 12, tzinfo=datetime.UTC)
    )
    return found


def with_stock(alice: User, home: Home, task: Task, quantity: str, price: str) -> None:
    article = stock_services.create_article(
        alice, home, name=f"Filter {task.title}", unit="piece", unit_price=D(price)
    )
    materials.add_material(alice, task, article, D(quantity))


def ledger(home: Home, today: datetime.date = TODAY) -> services.Ledger:
    return services.load(home, today)


def planned_of(home: Home, title: str) -> Decimal:
    (row,) = [p for p in ledger(home).projects if p.project.title == title]
    return row.amounts.planned


def natures(home: Home, year: int = YEAR) -> dict[str, Decimal]:
    return {line.label: line.amounts.planned for line in services.by_nature(ledger(home), year)}


class TestAnOpenTaskWithNoDate:
    """An open task with no date is to be done now: it counts in the current year, once."""

    def test_its_estimate_is_planned_in_the_current_year_everywhere(
        self, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        task = upkeep(
            alice,
            alice_home,
            "Fix the tap",
            None,
            room=owner_room,
            labour_budget=D("30.03"),
            material_budget=D("12.10"),
        )
        book = ledger(alice_home)
        assert services.envelope(book, YEAR).upkeep.planned == D("42.13")
        (room,) = services.by_room(book, YEAR).lines
        assert (room.id, room.amounts.planned) == (owner_room.id, D("42.13"))
        detail = services.room_budget(book, YEAR, owner_room)
        assert [(line.id, line.amounts.planned) for line in detail.tasks] == [(task.id, D("42.13"))]
        (line,) = services.by_task(book, YEAR).lines
        assert line.amounts.planned == D("42.13")
        assert natures(alice_home)[BudgetNature.MATERIAL] == D("12.10")
        assert natures(alice_home)[BudgetNature.LABOUR] == D("30.03")
        (this_year,) = [y for y in services.by_year(book) if y.year == YEAR]
        assert this_year.upkeep.planned == D("42.13")

    def test_it_is_counted_once_in_the_current_year_and_in_no_other(
        self, alice: User, alice_home: Home
    ) -> None:
        upkeep(alice, alice_home, "Fix the tap", None, labour_budget=D("30.03"))
        book = ledger(alice_home)
        assert services.maintenance_amounts(book, YEAR).planned == D("30.03")
        assert services.maintenance_amounts(book, YEAR + 1).planned == D("0")
        assert services.maintenance_amounts(book, YEAR - 1).planned == D("0")
        # Read next year, it moves with the current year: still planned once.
        later = ledger(alice_home, datetime.date(YEAR + 1, 2, 1))
        assert services.maintenance_amounts(later, YEAR + 1).planned == D("30.03")
        assert services.maintenance_amounts(later, YEAR).planned == D("0")

    def test_with_stock_items_only_and_no_room(self, alice: User, alice_home: Home) -> None:
        task = upkeep(alice, alice_home, "Change the filter", None)
        with_stock(alice, alice_home, task, "2", "12.34")
        book = ledger(alice_home)
        (room,) = services.by_room(book, YEAR).lines
        assert (room.id, room.amounts.planned) == (None, D("24.68"))  # No room
        assert natures(alice_home)[BudgetNature.MATERIAL] == D("24.68")

    def test_done_its_cost_stays_on_the_day_it_was_done(
        self, alice: User, alice_home: Home
    ) -> None:
        task = upkeep(alice, alice_home, "Fix the tap", None, labour_budget=D("30.03"))
        open_one = Occurrence.objects.get(task=task, status="planned")
        task_services.complete(alice, open_one, on=datetime.date(YEAR, 2, 3), actual_cost=D("31"))
        amounts = services.maintenance_amounts(ledger(alice_home), YEAR)
        assert (amounts.planned, amounts.paid) == (D("30.03"), D("31"))


class TestTheTasksOfAProject:
    """A project plans its lines, and what its tasks plan beyond the lines of their nature."""

    @pytest.mark.parametrize("status", ["idea", "planned", "in_progress"])
    def test_with_no_line_the_project_plans_its_tasks(
        self, alice: User, alice_home: Home, owner_room: Room, status: str
    ) -> None:
        found = project(alice, alice_home, "Kitchen", status, rooms=(owner_room,))
        project_services.add_task(
            alice,
            found,
            title="Tiles",
            due_on=datetime.date(YEAR, 12, 10),
            labour_budget=D("200.02"),
        )
        project_services.add_task(
            alice, found, title="Plan it", due_on=None, material_budget=D("66.06")
        )
        book = ledger(alice_home)
        (row,) = book.projects
        assert (row.amounts.planned, row.tasks_planned) == (D("266.08"), D("266.08"))
        assert services.envelope(book, YEAR).projects.planned == D("266.08")
        assert services.envelope(book, YEAR).upkeep.planned == D("0")
        (room,) = services.by_room(book, YEAR).lines
        assert room.amounts.planned == D("266.08")
        assert services.room_budget(book, YEAR, owner_room).total.planned == D("266.08")
        assert natures(alice_home)[BudgetNature.LABOUR] == D("200.02")
        assert natures(alice_home)[BudgetNature.MATERIAL] == D("66.06")
        assert services.by_task(book, YEAR).lines == []  # not in the upkeep: never twice

    def test_a_line_that_covers_the_tasks_is_not_counted_twice(
        self, alice: User, alice_home: Home
    ) -> None:
        found = project(alice, alice_home, "Bedroom", "planned", lines=(("labour", "500.00"),))
        project_services.add_task(
            alice, found, title="Paint", due_on=None, labour_budget=D("300.03")
        )
        assert planned_of(alice_home, "Bedroom") == D("500.00")
        (row,) = ledger(alice_home).projects
        assert row.tasks_planned == D("300.03")

    def test_tasks_above_their_line_add_what_the_line_does_not_hold(
        self, alice: User, alice_home: Home
    ) -> None:
        found = project(alice, alice_home, "Attic", "in_progress", lines=(("labour", "100.00"),))
        line = found.lines.get()
        quote = project_services.create_quote(alice, found, amount=D("90.00"))
        project_services.accept_quote(alice, quote, line)
        project_services.add_task(
            alice, found, title="Insulate", due_on=None, labour_budget=D("250.05")
        )
        (row,) = ledger(alice_home).projects
        # 100 on the line, 150.05 more from the task; the quote stays committed on the line.
        assert (row.amounts.planned, row.amounts.committed, row.amounts.engaged) == (
            D("250.05"),
            D("90.00"),
            D("90.00"),
        )
        assert row.amounts.gap == D("160.05")
        assert row.by_nature[BudgetNature.LABOUR].planned == D("250.05")

    def test_a_line_covers_the_tasks_of_its_own_nature_only(
        self, alice: User, alice_home: Home
    ) -> None:
        found = project(alice, alice_home, "Roof", "planned", lines=(("rental", "400.00"),))
        project_services.add_task(
            alice, found, title="Tiles", due_on=None, labour_budget=D("300.00")
        )
        # The scaffolding does not pay the roofer: 400 + 300.
        assert planned_of(alice_home, "Roof") == D("700.00")
        assert natures(alice_home) == {
            BudgetNature.MATERIAL: D("0"),
            BudgetNature.LABOUR: D("300.00"),
            BudgetNature.RENTAL: D("400.00"),
            BudgetNature.OTHER: D("0"),
        }

    def test_the_stock_items_of_a_task_of_a_project_are_materials(
        self, alice: User, alice_home: Home
    ) -> None:
        found = project(alice, alice_home, "Boiler", "in_progress", lines=(("material", "10.00"),))
        task = project_services.add_task(
            alice, found, title="Filters", due_on=datetime.date(YEAR, 12, 1), material_budget=D("5")
        )
        with_stock(alice, alice_home, task, "2", "12.34")
        assert planned_of(alice_home, "Boiler") == D("29.68")  # 10 covered, then 19.68 more

    def test_each_time_done_or_open_is_planned_and_a_skipped_one_is_not(
        self, alice: User, alice_home: Home
    ) -> None:
        found = project(alice, alice_home, "Garden", "in_progress")
        task = project_services.add_task(
            alice,
            found,
            title="Water",
            due_on=datetime.date(YEAR, 6, 1),
            labour_budget=D("10.00"),
            repeat_mode="fixed",
            repeat_unit="month",
            repeat_interval=1,
        )
        for day in (datetime.date(YEAR, 6, 1), datetime.date(YEAR, 7, 1)):
            open_one = Occurrence.objects.get(task=task, status="planned")
            task_services.complete(alice, open_one, on=day)
        Occurrence.objects.filter(task=task, status="planned").update(status="skipped")
        upkeep(alice, alice_home, "Spare", None)  # no estimate: plans nothing
        assert planned_of(alice_home, "Garden") == D("20.00")

    def test_an_abandoned_project_plans_nothing_from_its_tasks_either(
        self, alice: User, alice_home: Home
    ) -> None:
        found = project(alice, alice_home, "Pool", "abandoned", lines=(("labour", "80"),))
        project_services.add_task(alice, found, title="Dig", due_on=None, labour_budget=D("11.11"))
        (row,) = ledger(alice_home).projects
        assert (row.amounts.planned, row.tasks_planned) == (D("0"), D("0"))

    def test_the_rooms_share_the_whole_plan_to_the_cent(
        self, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        other = room_services.create_room(alice, alice_home, name="Hall")
        found = project(alice, alice_home, "Floors", "planned", rooms=(owner_room, other))
        project_services.add_task(
            alice, found, title="Sand", due_on=None, labour_budget=D("100.01")
        )
        lines = services.by_room(ledger(alice_home), YEAR).lines
        assert [line.amounts.planned for line in lines] == [D("50.01"), D("50.00")]


class TestTheScreensTheFilesAndTheApi:
    """The same figure on the pages, in the files and in the API."""

    @pytest.fixture
    def kitchen(self, alice: User, alice_home: Home, owner_room: Room) -> Project:
        year = task_services.today().year
        found = project_services.create_project(
            alice, alice_home, title="Kitchen", status="idea", rooms=[owner_room]
        )
        project_services.create_line(
            alice, found, label="Labour", nature="labour", planned=D("100.00")
        )
        Project.objects.filter(id=found.id).update(
            created_at=datetime.datetime(year, 3, 1, 12, tzinfo=datetime.UTC)
        )
        project_services.add_task(
            alice, found, title="Tiles", due_on=None, labour_budget=D("250.05")
        )
        upkeep(alice, alice_home, "Fix the tap", None, room=owner_room, labour_budget=D("30.03"))
        return found

    def test_the_pages_show_the_plan_and_what_the_tasks_plan(
        self, signed_in: Client, alice_home: Home, owner_room: Room, kitchen: Project
    ) -> None:
        home = {"home_id": alice_home.id}
        projects = signed_in.get(reverse("budget:projects", kwargs=home)).content.decode()
        assert "€250.05" in projects
        assert 'data-label="Planned by its tasks">€250.05' in projects
        rooms = signed_in.get(reverse("budget:rooms", kwargs=home)).content.decode()
        assert "€280.08" in rooms  # 250.05 of the project and 30.03 of the tap
        room = signed_in.get(reverse("rooms:detail", kwargs={**home, "room_id": owner_room.id}))
        assert "€280.08" in room.content.decode()
        overview = signed_in.get(reverse("budget:overview", kwargs=home)).content.decode()
        assert "€280.08" in overview
        tasks = signed_in.get(reverse("budget:tasks", kwargs=home)).content.decode()
        assert "€30.03" in tasks
        assert "Tiles" not in tasks

    def test_the_file_of_the_projects_has_a_column_for_the_tasks(
        self, signed_in: Client, alice_home: Home, kitchen: Project
    ) -> None:
        answer = signed_in.get(
            reverse("budget:projects", kwargs={"home_id": alice_home.id}), {"export": "csv"}
        )
        head, row = list(csv.reader(io.StringIO(answer.content.decode())))
        assert head[-1] == "Planned by its tasks"
        assert (row[0], row[3], row[-1]) == ("Kitchen", "250.05", "250.05")

    def test_the_api_gives_the_same_figures(
        self, client: Client, alice: User, alice_home: Home, kitchen: Project
    ) -> None:
        client.force_login(alice)
        home = {"home_id": alice_home.id}
        projects = client.get(reverse("api-v1:home-budget-projects", kwargs=home)).json()
        (line,) = projects["lines"]
        assert (line["amounts"]["planned"], line["tasks_planned"]) == ("250.05", "250.05")
        overview = client.get(reverse("api-v1:home-budget", kwargs=home)).json()
        assert overview["envelope"]["projects"]["planned"] == "250.05"
        assert overview["envelope"]["upkeep"]["planned"] == "30.03"
        assert overview["envelope"]["total"]["planned"] == "280.08"
        rooms = client.get(reverse("api-v1:home-budget-rooms", kwargs=home)).json()
        assert rooms["total"]["planned"] == "280.08"


def test_project_plan_adds_only_what_no_line_holds() -> None:
    lines: dict[str, Amounts] = {
        BudgetNature.LABOUR: Amounts(D("100"), D("90"), D("20")),
        BudgetNature.RENTAL: Amounts(D("50")),
    }
    tasks: dict[str, Decimal] = {BudgetNature.LABOUR: D("80"), BudgetNature.MATERIAL: D("12.34")}
    result = services.project_plan(lines, tasks)
    assert result[BudgetNature.LABOUR] == lines[BudgetNature.LABOUR]  # covered: unchanged
    assert result[BudgetNature.MATERIAL] == Amounts(D("12.34"))
    assert result[BudgetNature.RENTAL] == lines[BudgetNature.RENTAL]
    assert lines[BudgetNature.LABOUR].planned == D("100")  # the input is not changed
