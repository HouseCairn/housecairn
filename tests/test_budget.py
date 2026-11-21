"""The budget: figures by room, project and task, the envelope, CSV, access (docs/adr/0051)."""

import csv
import datetime
import io
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from housecairn.accounts.models import User
from housecairn.budget import csvfile, services
from housecairn.core.forms import MoneyField
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.projects import services as project_services
from housecairn.projects.forms import MoneyField as ProjectsMoneyField
from housecairn.projects.models import BudgetLine, Project
from housecairn.projects.services import Amounts
from housecairn.rooms.models import Room
from housecairn.stock import services as stock_services
from housecairn.tasks import forms as task_forms
from housecairn.tasks import materials, recurrence
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Occurrence, Task

pytest_plugins = ("tests.rooms_fixtures", "tests.projects_fixtures")
pytestmark = pytest.mark.django_db

D = Decimal
TODAY = datetime.date(2026, 11, 9)


def url(name: str, home: Home | None = None) -> str:
    return reverse(f"budget:{name}", kwargs={"home_id": home.id}) if home else reverse("budget")


def make_task(
    alice: User,
    home: Home,
    title: str = "Clean the gutters",
    *,
    due: datetime.date | None = None,
    **fields: object,
) -> Task:
    return task_services.create_task(alice, home, title=title, due_on=due or TODAY, **fields)


def finish(alice: User, task: Task, *, on: datetime.date, cost: str | None = None) -> None:
    open_one = Occurrence.objects.get(task=task, status="planned")
    task_services.complete(alice, open_one, on=on, actual_cost=D(cost) if cost else None)


def pin(project: Project, year: int = 2026) -> None:
    """A project with no dated work is counted in the year it was created: pin that year."""
    Project.objects.filter(id=project.id).update(
        created_at=datetime.datetime(year, 3, 1, 12, tzinfo=datetime.UTC)
    )


def csv_rows(response: object) -> list[list[str]]:
    content = response.content.decode()  # type: ignore[attr-defined]
    return list(csv.reader(io.StringIO(content)))


class TestPaintingTheLivingRoom:
    """The scenario of the specification: two quotes, 800 planned, an expense, the gap."""

    @pytest.fixture
    def painted(
        self,
        alice: User,
        alice_home: Home,
        owner_room: Room,
        living_room_project: Project,
        labour_line: BudgetLine,
    ) -> Project:
        pin(living_room_project)
        project_services.update_project(alice, living_room_project, rooms=[owner_room])
        cheap = project_services.create_quote(alice, living_room_project, amount=D("620.00"))
        project_services.create_quote(alice, living_room_project, amount=D("700.00"))
        project_services.accept_quote(alice, cheap, labour_line)
        paint = BudgetLine.objects.get(project=living_room_project, label="Paint")
        project_services.update_line(alice, paint, paid=D("350"))
        # Upkeep in the same room: done for more than planned.
        task = make_task(
            alice, alice_home, "Wash the walls", room=owner_room, labour_budget=D("50")
        )
        finish(alice, task, on=TODAY, cost="80")
        return living_room_project

    def test_the_gap_is_seen_by_room_by_project_and_by_task(
        self, alice_home: Home, painted: Project
    ) -> None:
        ledger = services.load(alice_home, TODAY)
        room = services.by_room(ledger, 2026).lines[0]
        assert (room.label, room.amounts.planned, room.amounts.engaged) == (
            "Living room",
            D("850.00"),
            D("1050.00"),
        )
        assert room.amounts.gap == D("-200.00")  # 800 - (350 + 620) and 50 - 80
        project = services.by_project(ledger).lines[0]
        assert (project.amounts.planned, project.amounts.committed, project.amounts.paid) == (
            D("800.00"),
            D("620.00"),
            D("350.00"),
        )
        assert project.amounts.gap == D("-170.00")
        task = services.by_task(ledger, 2026).lines[0]
        assert (task.label, task.amounts.gap) == ("Wash the walls", D("-30.00"))

    def test_the_pages_say_it_in_words(
        self, signed_in: Client, alice_home: Home, painted: Project
    ) -> None:
        year = 2026
        for page, words in [
            ("rooms", "Over budget by"),
            ("projects", "Over budget by"),
            ("tasks", "Over budget by"),
        ]:
            html = signed_in.get(url(page, alice_home) + f"?year={year}").content.decode()
            assert words in html, page
            assert "<table" in html
            assert "aria-label=" in html  # the gauge is labelled
        projects = signed_in.get(url("projects", alice_home)).content.decode()
        assert "Paint the living room" in projects
        assert "170.00" in projects

    def test_the_amount_used_is_shown_beside_the_gauge_and_in_the_file(
        self, signed_in: Client, alice_home: Home, painted: Project
    ) -> None:
        # Used is 350 + 620 = 970 for the project: the amount, then the gauge and its percentage.
        html = signed_in.get(url("projects", alice_home)).content.decode()
        assert "970.00" in html
        assert "121 % used" in html
        file = signed_in.get(url("projects", alice_home) + "?export=csv")
        head, row = csv_rows(file)[:2]
        assert row[head.index("Used")] == "970.00"

    def test_the_figure_of_a_row_gauge_shares_the_line_of_the_bar(
        self, signed_in: Client, alice_home: Home, painted: Project
    ) -> None:
        html = signed_in.get(url("projects", alice_home)).content.decode()
        gauge = html.split('<span class="gauge">', 1)[1].split("</span>\n  </span>", 1)[0]
        assert '<progress class="over"' in gauge  # 121 %: beyond the plan
        assert "gauge-figure" in gauge
        assert "121 % used" in gauge


class TestToPayIsCountedLineByLine:
    def test_a_line_paid_above_its_quotes_does_not_lower_what_another_owes(self) -> None:
        first = Amounts(D("100"), D("100"), D("150"))
        second = Amounts(D("200"), D("200"), D("50"))
        total = first + second
        assert total.to_pay == D("150")  # not 300 - 200
        assert total.engaged == D("350")
        assert total.gap == D("-50")


class TestAnnualisation:
    @pytest.mark.parametrize(
        ("mode", "unit", "interval", "estimate", "expected"),
        [
            ("after_last", "year", 3, "120", "40.00"),
            ("after_last", "month", 6, "100", "200.00"),
            ("fixed", "month", 1, "25.50", "306.00"),
            ("after_last", "week", 1, "7.50", "390.00"),
            ("after_last", "day", 30, "10", "121.67"),
            ("after_last", "week", 7, "100", "742.86"),  # 52/7 times, rounded once, half up
            # The rate 1/48 is rounded to 28 digits (0.0208333...): the product with 0.24 then
            # falls just under half a cent. Multiplying first gives the exact 0.005, so 0.01.
            ("after_last", "year", 48, "0.24", "0.01"),
        ],
    )
    def test_the_cost_of_a_year_follows_the_repetition(
        self,
        alice: User,
        alice_home: Home,
        mode: str,
        unit: str,
        interval: int,
        estimate: str,
        expected: str,
    ) -> None:
        make_task(
            alice,
            alice_home,
            repeat_mode=mode,
            repeat_unit=unit,
            repeat_interval=interval,
            material_budget=D(estimate),
        )
        row = services.load(alice_home, TODAY).tasks[0]
        assert row.annual_cost == D(expected)

    def test_a_task_that_does_not_repeat_is_not_annualised(
        self, alice: User, alice_home: Home
    ) -> None:
        # There is no repetition by use (every 200 hours of running) yet: when there is, it
        # will not be annualised either, since its pace is not on the calendar (ADR 0051).
        make_task(alice, alice_home, labour_budget=D("90"))
        row = services.load(alice_home, TODAY).tasks[0]
        assert row.per_year is None
        assert row.annual_cost is None

    def test_labour_and_materials_add_up_and_no_estimate_has_no_cost(
        self, alice: User, alice_home: Home
    ) -> None:
        make_task(
            alice,
            alice_home,
            "Filter",
            repeat_mode="after_last",
            repeat_unit="year",
            repeat_interval=1,
            labour_budget=D("20"),
            material_budget=D("14.99"),
        )
        make_task(alice, alice_home, "Nothing", repeat_mode="after_last", repeat_unit="year")
        rows = {r.task.title: r for r in services.load(alice_home, TODAY).tasks}
        assert rows["Filter"].annual_cost == D("34.99")
        assert rows["Nothing"].annual_cost is None


class TestPlannedYearFollowsTheRepetition:
    """The plan of a year counts what the repetition will still bring, not only the rows."""

    @staticmethod
    def repeating(
        alice: User,
        home: Home,
        unit: str,
        interval: int,
        *,
        first: datetime.date,
        mode: str = "fixed",
        estimate: str = "25",
    ) -> Task:
        return make_task(
            alice,
            home,
            due=first,
            repeat_mode=mode,
            repeat_unit=unit,
            repeat_interval=interval,
            material_budget=D(estimate),
        )

    @staticmethod
    def planned(home: Home, today: datetime.date, year: int) -> Decimal:
        return services.by_task(services.load(home, today), year).lines[0].amounts.planned

    def test_a_monthly_task_read_in_february_plans_twelve_times_its_estimate(
        self, alice: User, alice_home: Home
    ) -> None:
        task = self.repeating(alice, alice_home, "month", 1, first=datetime.date(2026, 1, 1))
        finish(alice, task, on=datetime.date(2026, 1, 1), cost="25")
        today = datetime.date(2026, 2, 10)
        row = services.load(alice_home, today).tasks[0]
        assert row.annual_cost == D("300.00")
        # One done in January, the open one of February, ten more to come: 12 times.
        assert self.planned(alice_home, today, 2026) == D("300.00")
        figures = row.figures(services.in_year(2026), year=2026)
        assert (figures.count, figures.projected, figures.paid) == (12, 10, D("25.00"))

    def test_every_six_months_plans_two_times(self, alice: User, alice_home: Home) -> None:
        self.repeating(alice, alice_home, "month", 6, first=datetime.date(2026, 2, 1))
        today = datetime.date(2026, 2, 10)
        assert self.planned(alice_home, today, 2026) == D("50.00")  # February and August

    def test_every_three_years_plans_nothing_in_a_year_without_an_occurrence(
        self, alice: User, alice_home: Home
    ) -> None:
        self.repeating(alice, alice_home, "year", 3, first=datetime.date(2026, 3, 1))
        today = datetime.date(2026, 3, 2)
        assert self.planned(alice_home, today, 2026) == D("25.00")
        assert self.planned(alice_home, today, 2027) == D("0.00")
        assert self.planned(alice_home, today, 2028) == D("0.00")
        assert self.planned(alice_home, today, 2029) == D("25.00")

    def test_a_year_already_over_counts_only_what_happened(
        self, alice: User, alice_home: Home
    ) -> None:
        task = self.repeating(alice, alice_home, "month", 1, first=datetime.date(2026, 1, 1))
        finish(alice, task, on=datetime.date(2026, 1, 1), cost="25")
        today = datetime.date(2027, 3, 1)  # the open occurrence of February 2026 is long late
        ledger = services.load(alice_home, today)
        line = services.by_task(ledger, 2026).lines[0]
        # Only January happened in 2026: the late occurrence is not a plan of a year that is over.
        assert line.amounts.planned == D("25.00")
        assert line.amounts.paid == D("25.00")
        assert ledger.tasks[0].projected(2026) == 0

    def test_a_late_occurrence_of_a_year_over_is_counted_in_the_current_year_once(
        self, alice: User, alice_home: Home
    ) -> None:
        task = self.repeating(alice, alice_home, "month", 1, first=datetime.date(2026, 1, 1))
        finish(alice, task, on=datetime.date(2026, 1, 1), cost="25")
        today = datetime.date(2027, 3, 1)
        ledger = services.load(alice_home, today)
        # The late February, then the eleven months it still brings: twelve, as the cost a year.
        assert services.by_task(ledger, 2027).lines[0].amounts.planned == D("300.00")
        assert ledger.tasks[0].annual_cost == D("300.00")
        assert 2026 in ledger.years()
        assert services.envelope(ledger, 2026).upkeep.planned == D("25.00")

    def test_the_next_year_is_projected_whole(self, alice: User, alice_home: Home) -> None:
        self.repeating(alice, alice_home, "month", 1, first=datetime.date(2026, 2, 1))
        today = datetime.date(2026, 2, 10)
        assert self.planned(alice_home, today, 2027) == D("300.00")
        assert self.planned(alice_home, today, 2030) == D("300.00")

    def test_a_task_counted_from_the_last_time_is_projected_from_its_open_date(
        self, alice: User, alice_home: Home
    ) -> None:
        self.repeating(
            alice, alice_home, "month", 1, first=datetime.date(2026, 3, 1), mode="after_last"
        )
        today = datetime.date(2026, 2, 10)
        assert self.planned(alice_home, today, 2026) == D("250.00")  # March to December
        assert self.planned(alice_home, today, 2027) == D("300.00")

    def test_a_task_that_does_not_repeat_plans_only_its_open_occurrence(
        self, alice: User, alice_home: Home
    ) -> None:
        make_task(alice, alice_home, due=datetime.date(2026, 5, 1), material_budget=D("25"))
        today = datetime.date(2026, 2, 10)
        assert self.planned(alice_home, today, 2026) == D("25.00")
        assert self.planned(alice_home, today, 2027) == D("0.00")

    def test_the_envelope_the_rooms_and_the_natures_use_the_same_projection(
        self, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        year = 2026
        today = datetime.date(2026, 2, 10)
        task = self.repeating(alice, alice_home, "month", 1, first=datetime.date(2026, 1, 1))
        Task.objects.filter(id=task.id).update(room=owner_room)
        finish(alice, task, on=datetime.date(2026, 1, 1), cost="25")
        project_services.set_envelope(alice, alice_home, year=year, amount=D("1000"))
        ledger = services.load(alice_home, today)
        report = services.envelope(ledger, year)
        assert report.total.planned == D("300.00")
        assert report.left_to_plan == D("700.00")
        assert report.left_to_spend == D("975.00")  # 1000 less the 25 paid
        assert services.by_year(ledger)[0].upkeep.planned == D("300.00")
        assert services.by_room(ledger, year).lines[0].amounts.planned == D("300.00")
        natures = {line.label: line.amounts for line in services.by_nature(ledger, year)}
        assert natures["material"].planned == D("300.00")

    def test_a_daily_task_counted_from_the_last_time_is_projected_whole_in_a_far_year(
        self, alice: User, alice_home: Home
    ) -> None:
        self.repeating(
            alice, alice_home, "day", 1, first=datetime.date(2026, 2, 1), mode="after_last"
        )
        today = datetime.date(2026, 2, 10)
        row = services.load(alice_home, today).tasks[0]
        # 2032 is a leap year: 366 days, not the first hundred and a bit of a bounded walk.
        assert row.projected(2032) == 366
        assert self.planned(alice_home, today, 2032) == D("9150.00")
        assert row.projected(2035) == 365
        assert row.projected(2200) == 365

    def test_a_weekly_task_counted_from_the_last_time_is_projected_whole_forty_years_on(
        self, alice: User, alice_home: Home
    ) -> None:
        self.repeating(
            alice, alice_home, "week", 1, first=datetime.date(2026, 2, 1), mode="after_last"
        )
        today = datetime.date(2026, 2, 10)
        row = services.load(alice_home, today).tasks[0]
        first = datetime.date(2026, 2, 1)
        expected = sum(
            1 for k in range(1, 3000) if (first + datetime.timedelta(weeks=k)).year == 2066
        )
        assert expected in (52, 53)
        assert row.projected(2066) == expected

    def test_a_far_year_is_the_same_as_walking_the_calendar_to_it(
        self, alice: User, alice_home: Home
    ) -> None:
        # A far year is not an estimate: it holds the dates the calendar brings, one by one.
        for mode in ("fixed", "after_last"):
            task = self.repeating(
                alice, alice_home, "month", 2, first=datetime.date(2026, 1, 31), mode=mode
            )
            row = services.load(alice_home, datetime.date(2026, 2, 10)).tasks[-1]
            rule = task.rule
            walked = recurrence.preview(
                rule, anchor=task.series_start, due_on=datetime.date(2026, 1, 31), count=500
            )
            for year in (2027, 2040, 2080):
                assert row.projected(year) == sum(1 for day in walked if day.year == year)

    def test_a_far_year_of_many_daily_tasks_is_quick_and_costs_no_more_queries(
        self, alice: User, alice_home: Home
    ) -> None:
        tasks = [
            Task(
                home=alice_home,
                title=f"Task {n}",
                labour_budget=D("1"),
                repeat_mode="fixed" if n % 2 else "after_last",
                repeat_unit="day",
                repeat_interval=1,
                series_start=TODAY,
            )
            for n in range(500)
        ]
        Task.objects.bulk_create(tasks)
        Occurrence.objects.bulk_create(
            [Occurrence(home=alice_home, task=t, due_on=TODAY) for t in tasks]
        )
        started = time.perf_counter()
        with CaptureQueriesContext(connection) as context:
            ledger = services.load(alice_home, TODAY)
            report = services.by_task(ledger, 2040)
        elapsed = time.perf_counter() - started
        assert all(line.amounts.planned == D("366.00") for line in report.lines)  # 2040: leap
        assert len(context) <= 12
        assert elapsed < 2  # about 14 s when every date of 14 years was rebuilt one by one


class TestYearOfAProject:
    def test_a_project_over_two_years_is_counted_whole_in_the_year_it_starts(
        self, alice: User, alice_home: Home, living_room_project: Project
    ) -> None:
        project_services.add_task(
            alice, living_room_project, title="Prepare", due_on=datetime.date(2026, 12, 20)
        )
        project_services.add_task(
            alice, living_room_project, title="Paint", due_on=datetime.date(2027, 2, 10)
        )
        ledger = services.load(alice_home, TODAY)
        assert ledger.projects[0].year == 2026
        assert services.envelope(ledger, 2026).projects.planned == D("800.00")
        assert services.envelope(ledger, 2027).projects.planned == D("0")

    def test_an_accepted_quote_can_start_the_year(
        self,
        alice: User,
        alice_home: Home,
        living_room_project: Project,
        labour_line: BudgetLine,
    ) -> None:
        offer = project_services.create_quote(
            alice, living_room_project, amount=D("500"), issued_on=datetime.date(2025, 12, 30)
        )
        project_services.accept_quote(alice, offer, labour_line)
        project_services.add_task(
            alice, living_room_project, title="Paint", due_on=datetime.date(2026, 3, 1)
        )
        assert services.load(alice_home, TODAY).projects[0].year == 2025

    def test_without_a_date_it_is_the_year_it_was_created(
        self, alice_home: Home, living_room_project: Project
    ) -> None:
        assert services.load(alice_home, TODAY).projects[0].year == datetime.date.today().year

    def test_an_abandoned_project_plans_nothing_but_what_was_paid_stays_seen(
        self, alice: User, alice_home: Home, living_room_project: Project
    ) -> None:
        paint = BudgetLine.objects.get(project=living_room_project, label="Paint")
        project_services.update_line(alice, paint, paid=D("120"))
        project_services.update_project(alice, living_room_project, status="abandoned")
        row = services.load(alice_home, TODAY).projects[0]
        assert row.amounts.planned == D("0")
        assert row.amounts.paid == D("120")
        assert row.amounts.gap == D("-120")


class TestRoundingAndSharing:
    def test_a_project_in_three_rooms_shares_cents_without_losing_one(
        self, alice: User, alice_home: Home
    ) -> None:
        rooms = [
            Room.objects.create(home=alice_home, name=name, position=i)
            for i, name in enumerate(["Hall", "Kitchen", "Study"])
        ]
        project_services.create_project(
            alice, alice_home, title="Floors", rooms=rooms, budget=D("100.00")
        )
        report = services.by_room(services.load(alice_home, TODAY), datetime.date.today().year)
        assert [line.amounts.planned for line in report.lines] == [
            D("33.34"),
            D("33.33"),
            D("33.33"),
        ]
        assert report.total.planned == D("100.00")
        assert report.total.engaged == sum((line.amounts.engaged for line in report.lines), D(0))

    def test_a_project_without_a_room_and_a_task_without_a_room_are_in_no_room(
        self, alice: User, alice_home: Home
    ) -> None:
        project_services.create_project(alice, alice_home, title="Fence", budget=D("10"))
        make_task(alice, alice_home, labour_budget=D("5"))
        report = services.by_room(services.load(alice_home, TODAY), TODAY.year)
        assert [(line.id, line.amounts.planned) for line in report.lines] == [(None, D("15.00"))]

    def test_amounts_are_never_floats(self, alice: User, alice_home: Home) -> None:
        make_task(alice, alice_home, labour_budget=D("0.10"), material_budget=D("0.20"))
        row = services.load(alice_home, TODAY).tasks[0]
        assert row.estimate == D("0.30")
        assert isinstance(row.estimate, Decimal)


class TestWindowsOfTime:
    def test_the_last_12_months_start_the_day_after_the_same_day_last_year(
        self, alice: User, alice_home: Home
    ) -> None:
        inside = make_task(alice, alice_home, "In", material_budget=D("10"))
        outside = make_task(alice, alice_home, "Out", material_budget=D("10"))
        finish(alice, inside, on=datetime.date(2025, 11, 10), cost="12")
        finish(alice, outside, on=datetime.date(2025, 11, 9), cost="99")
        report = services.rolling(services.load(alice_home, TODAY))
        assert report.since == datetime.date(2025, 11, 10)
        assert (report.upkeep.planned, report.upkeep.paid) == (D("10.00"), D("12.00"))

    def test_a_task_done_without_a_cost_is_planned_and_flagged_not_hidden(
        self, alice: User, alice_home: Home
    ) -> None:
        """Its materials are its stock item (ADR 0081), which the stock did not hold: nothing
        was typed, taken or planned beyond the stock, so nothing was paid (ADR 0091)."""
        task = make_task(alice, alice_home, material_budget=D("40"))
        guard = stock_services.create_article(
            alice, alice_home, name="Gutter guard", unit="piece", unit_price=D("40")
        )
        materials.add_material(alice, task, guard, D("1"))
        finish(alice, task, on=TODAY)
        line = services.by_task(services.load(alice_home, TODAY), 2026).lines[0]
        assert line.extra["missing_costs"] == 1
        assert line.amounts.paid == D("0")

    def test_a_task_done_with_nothing_typed_has_paid_its_plan(
        self, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home, material_budget=D("40"))
        finish(alice, task, on=TODAY)
        line = services.by_task(services.load(alice_home, TODAY), 2026).lines[0]
        assert line.extra["missing_costs"] == 0
        assert (line.amounts.paid, line.extra["total_from_plan"]) == (D("40.00"), D("40.00"))

    def test_years_with_figures_are_listed_newest_first(
        self, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home, material_budget=D("10"))
        finish(alice, task, on=datetime.date(2024, 3, 1), cost="9")
        project_services.set_envelope(alice, alice_home, year=2028, amount=D("1000"))
        years = [line.year for line in services.by_year(services.load(alice_home, TODAY))]
        assert years == [2028, 2026, 2024]

    def test_by_nature_shares_the_real_cost_of_a_task_as_its_estimate_is(
        self, alice: User, alice_home: Home, living_room_project: Project
    ) -> None:
        task = make_task(
            alice, alice_home, labour_budget=D("30"), material_budget=D("20"), repeat_mode="once"
        )
        finish(alice, task, on=TODAY, cost="60")
        pin(living_room_project)
        lines = {
            line.label: line.amounts
            for line in services.by_nature(services.load(alice_home, TODAY), 2026)
        }
        assert lines["labour"].planned == D("530.00")  # 500 of the project, 30 of the upkeep
        assert lines["material"].planned == D("320.00")
        # 60 paid on an estimate of 20 materials and 30 labour: 24 and 36, the sum exact.
        assert lines["material"].paid == D("24.00")
        assert lines["labour"].paid == D("36.00")
        assert lines["other"].paid == D("0.00")

    def test_the_share_is_in_whole_cents_and_loses_none(self) -> None:
        figures = services.TaskFigures(1, D("1"), D("2"), D("100.00"), 0)
        material, labour = services.real_cost_split(figures, D("1"), D("2"))
        assert (material, labour) == (D("33.33"), D("66.67"))
        assert material + labour == D("100.00")

    def test_a_cost_on_a_task_with_no_estimate_has_no_share_and_is_counted_under_other(
        self, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home, repeat_mode="once")
        finish(alice, task, on=TODAY, cost="60")
        lines = {
            line.label: line.amounts
            for line in services.by_nature(services.load(alice_home, TODAY), 2026)
        }
        assert lines["other"].paid == D("60.00")
        assert lines["material"].paid == lines["labour"].paid == D("0.00")

    def test_the_upkeep_on_target_is_not_shown_as_an_overrun(
        self, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home, material_budget=D("40"), repeat_mode="once")
        finish(alice, task, on=TODAY, cost="40")
        lines = {
            line.label: line.amounts
            for line in services.by_nature(services.load(alice_home, TODAY), 2026)
        }
        assert lines["material"].gap == D("0.00")
        assert lines["other"].gap == D("0.00")  # not -40 against a plan of nothing


class TestEnvelope:
    def test_planned_engaged_and_paid_against_the_envelope(
        self, alice: User, alice_home: Home, living_room_project: Project
    ) -> None:
        year = 2026
        pin(living_room_project)
        project_services.set_envelope(alice, alice_home, year=year, amount=D("1000"))
        task = make_task(alice, alice_home, labour_budget=D("150"), due=datetime.date(year, 6, 1))
        report = services.envelope(services.load(alice_home, TODAY), year)
        assert report.envelope == D("1000.00")
        assert report.total.planned == D("950.00")  # the project, and the upkeep coming
        assert report.left_to_plan == D("50.00")
        assert report.left_to_spend == D("1000.00")
        assert report.sure_budget is None
        finish(alice, task, on=datetime.date(year, 6, 2), cost="200")
        report = services.envelope(services.load(alice_home, TODAY), year)
        assert report.total.engaged == D("200.00")
        assert report.left_to_plan == D("50.00")

    def test_no_envelope_is_not_zero(self, alice_home: Home) -> None:
        report = services.envelope(services.load(alice_home, TODAY), 2026)
        assert report.envelope is None
        assert report.left_to_plan is None


class TestSeveralHomesAndCurrencies:
    def test_homes_are_added_by_currency_and_never_converted(
        self, alice: User, alice_home: Home
    ) -> None:
        second = home_services.create_home(alice, name="Flat", currency="EUR")
        third = home_services.create_home(alice, name="Chalet", currency="CHF")
        for home, amount in ((alice_home, "100"), (second, "50"), (third, "70")):
            project_services.create_project(alice, home, title="Work", budget=D(amount))
        year = datetime.date.today().year
        result = services.combined([alice_home, second, third], year)
        assert list(result) == ["CHF", "EUR"]
        assert result["EUR"]["total"].planned == D("150.00")
        assert result["CHF"]["total"].planned == D("70.00")
        assert [h.name for h, _report in result["EUR"]["homes"]] == ["Main house", "Flat"]

    def test_the_entry_opens_the_budget_of_the_current_home_in_its_own_currency(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        chalet = home_services.create_home(alice, name="Chalet", currency="CHF")
        project_services.create_project(alice, alice_home, title="Work", budget=D("100"))
        project_services.create_project(alice, chalet, title="Work", budget=D("70"))
        # Before any choice: the first home by name ("Chalet" < "Main house"), never an addition.
        entry = signed_in.get(url(""))
        assert entry.status_code == 302
        assert entry["Location"] == url("overview", chalet)
        page = signed_in.get(entry["Location"]).content.decode()
        assert "CHF" in page
        assert "never converted" not in page
        # Choosing the other home: the entry follows it, and so does the currency.
        signed_in.post(reverse("homes:current"), {"home": str(alice_home.id)})
        entry = signed_in.get(url(""))
        assert entry["Location"] == url("overview", alice_home)
        page = signed_in.get(entry["Location"]).content.decode()
        assert "€" in page  # the money filter writes the symbol of the currency
        assert "CHF" not in page

    def test_the_entry_of_an_account_with_no_home_offers_to_add_one(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        client.force_login(make_user("alone@example.com"))
        response = client.get(url(""))
        assert response.status_code == 200
        assert reverse("homes:create") in response.content.decode()

    def test_one_home_goes_straight_to_its_budget(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        response = signed_in.get(url(""))
        assert response.status_code == 302
        assert response["Location"] == url("overview", alice_home)


class TestQueries:
    def test_the_number_of_queries_does_not_grow_with_the_tasks(
        self, alice: User, alice_home: Home, living_room_project: Project
    ) -> None:
        def count() -> int:
            with CaptureQueriesContext(connection) as context:
                ledger = services.load(alice_home, TODAY)
                services.by_room(ledger, 2026)
                services.by_project(ledger)
                services.by_task(ledger, 2026)
                services.by_year(ledger)
                services.by_nature(ledger, 2026)
                services.rolling(ledger)
            return len(context)

        make_task(alice, alice_home, labour_budget=D("5"))
        few = count()
        tasks = [
            Task(
                home=alice_home,
                title=f"Task {n}",
                labour_budget=D("3.50"),
                repeat_mode="fixed",
                repeat_unit="month",
                repeat_interval=1,
                series_start=TODAY,
            )
            for n in range(500)
        ]
        Task.objects.bulk_create(tasks)
        Occurrence.objects.bulk_create(
            [Occurrence(home=alice_home, task=t, due_on=TODAY) for t in tasks]
        )
        many = count()
        assert many == few
        assert many <= 12


class TestCsv:
    HOSTILE = ['=HYPERLINK("http://example.com")', "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"]

    def test_a_text_cell_cannot_be_a_formula(self) -> None:
        for text in self.HOSTILE:
            assert csvfile.safe(text).startswith("'"), text
            assert csvfile.safe(text)[1:] == text
        assert csvfile.safe("Paint") == "Paint"

    def test_numbers_made_by_the_program_stay_numbers(self) -> None:
        assert csvfile.safe(D("-170")) == "-170.00"
        assert csvfile.safe(2026) == "2026"
        assert csvfile.safe(None) == ""

    def test_the_files_of_the_pages_neutralise_what_people_typed(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        year = datetime.date.today().year
        for n, text in enumerate(self.HOSTILE):
            room = Room.objects.create(home=alice_home, name=text, position=n)
            project_services.create_project(
                alice, alice_home, title=text, rooms=[room], budget=D("10")
            )
            make_task(alice, alice_home, text, room=room, labour_budget=D("5"))
        for page in ("rooms", "projects", "tasks", "overview"):
            response = signed_in.get(url(page, alice_home) + f"?year={year}&export=csv")
            assert response.status_code == 200
            assert response["Content-Type"].startswith("text/csv")
            assert "attachment" in response["Content-Disposition"]
            for row in csv_rows(response):
                for cell in row:
                    assert not cell.startswith(csvfile.FORMULA_STARTS) or _is_number(cell), (
                        page,
                        cell,
                    )

    def test_a_negative_gap_is_written_as_a_number(
        self,
        signed_in: Client,
        alice: User,
        alice_home: Home,
        living_room_project: Project,
        labour_line: BudgetLine,
    ) -> None:
        offer = project_services.create_quote(alice, living_room_project, amount=D("970"))
        project_services.accept_quote(alice, offer, labour_line)
        rows = csv_rows(signed_in.get(url("projects", alice_home) + "?export=csv"))
        assert rows[0][0] == "Project"
        assert rows[1][:1] + rows[1][3:8] == [
            "Paint the living room",
            "800.00",
            "970.00",
            "0.00",
            "970.00",
            "-170.00",
        ]


def _is_number(cell: str) -> bool:
    try:
        D(cell)
    except ArithmeticError:
        return False
    return True


class TestAmountsAreTypedLikeEverywhere:
    def test_the_forms_of_tasks_use_the_shared_field_of_projects(self) -> None:
        assert MoneyField is ProjectsMoneyField
        for name in ("labour_budget", "material_budget"):
            assert isinstance(task_forms.TaskForm.base_fields[name], MoneyField)
        assert isinstance(task_forms.CompletionForm.base_fields["actual_cost"], MoneyField)

    def test_a_french_amount_is_accepted_and_an_ambiguous_one_is_not(self) -> None:
        field = MoneyField(required=False)
        with translation.override("fr"):
            assert field.clean("1 250,50") == D("1250.50")
            assert field.clean("1250.50") == D("1250.50")
            for ambiguous in ("1,250.50", "1.250,50", "1,2,3"):
                with pytest.raises(ValidationError):
                    field.clean(ambiguous)
        with translation.override("en"):
            assert field.clean("1250.50") == D("1250.50")


class TestOnTheTaskPages:
    def test_the_page_of_a_task_shows_its_cost_and_the_form_of_what_was_done_its_estimate(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = make_task(
            alice,
            alice_home,
            "Boiler service",
            repeat_mode="after_last",
            repeat_unit="year",
            repeat_interval=2,
            labour_budget=D("80"),
            material_budget=D("20"),
        )
        page = signed_in.get(
            reverse("tasks:detail", kwargs={"home_id": alice_home.id, "task_id": task.id})
        ).content.decode()
        assert "Cost a year" in page
        assert "50" in page  # 100 every 2 years
        finish(alice, task, on=TODAY)
        done = Occurrence.objects.get(task=task, status="done")
        form = signed_in.get(
            reverse(
                "tasks:occurrence-edit",
                kwargs={"home_id": alice_home.id, "task_id": task.id, "occurrence_id": done.id},
            )
        )
        assert form.status_code == 200
        assert "Estimated at €100.00" in form.content.decode()

    def test_a_task_of_a_project_shows_no_cost_of_its_own(
        self, signed_in: Client, alice: User, alice_home: Home, living_room_project: Project
    ) -> None:
        task = project_services.add_task(
            alice, living_room_project, title="Sand", labour_budget=D("99")
        )
        page = signed_in.get(
            reverse("tasks:detail", kwargs={"home_id": alice_home.id, "task_id": task.id})
        ).content.decode()
        assert "Cost a year" not in page
        assert services.task_row(task) is None


class TestAccess:
    PAGES = ("overview", "rooms", "projects", "tasks")

    def test_a_reader_reads_every_page_and_gets_no_button_that_changes_something(
        self, client: Client, reader: User, alice_home: Home, living_room_project: Project
    ) -> None:
        client.force_login(reader)
        for page in self.PAGES:
            assert client.get(url(page, alice_home)).status_code == 200, page
        html = client.get(url("overview", alice_home)).content.decode()
        assert "Set the envelope" not in html

    def test_an_owner_can_reach_the_envelope_from_the_overview(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        assert "Set the envelope" in signed_in.get(url("overview", alice_home)).content.decode()

    @pytest.mark.parametrize("who", ["bob", "site_admin"])
    def test_another_home_and_the_instance_administrator_see_nothing(
        self,
        request: pytest.FixtureRequest,
        client: Client,
        who: str,
        alice_home: Home,
        living_room_project: Project,
    ) -> None:
        client.force_login(request.getfixturevalue(who))
        for page in self.PAGES:
            assert client.get(url(page, alice_home)).status_code == 404, page
            assert client.get(url(page, alice_home) + "?export=csv").status_code == 404, page

    def test_anonymous_visitors_are_sent_to_sign_in(self, client: Client, alice_home: Home) -> None:
        for page in self.PAGES:
            assert client.get(url(page, alice_home)).status_code == 302
        assert client.get(url("")).status_code == 302

    def test_the_budget_has_no_table_of_its_own_so_the_export_and_the_erasure_cover_it(
        self,
    ) -> None:
        assert list(apps.get_app_config("budget").get_models()) == []

    def test_a_bad_year_is_today_s_year(self, signed_in: Client, alice_home: Home) -> None:
        for raw in ("abc", "99", "99999", ""):
            assert signed_in.get(url("rooms", alice_home) + f"?year={raw}").status_code == 200


def test_the_budget_has_one_row_of_tabs(signed_in: Client, alice_home: Home) -> None:
    html = signed_in.get(url("rooms", alice_home)).content.decode()
    assert 'href="' + url("overview", alice_home) + '"' in html
    assert html.count('<nav class="tabs') == 1
    assert "Upkeep" in html


class TestSummaryAtAGlance:
    """The top of the page: three figures and a gauge; the rest is in the details."""

    def page(self, client: Client, home: Home, *, language: str = "en") -> str:
        response = client.get(
            url("overview", home) + "?year=2026", headers={"Accept-Language": language}
        )
        return response.content.decode()

    def top(self, html: str) -> str:
        """The envelope card without its details."""
        card = html.split('aria-labelledby="envelope-title"', 1)[1].split("</section>", 1)[0]
        return card.split("<details", 1)[0]

    def test_three_figures_and_a_gauge_with_an_envelope(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        project_services.set_envelope(alice, alice_home, year=2026, amount=D("1000"))
        task = make_task(alice, alice_home, labour_budget=D("150"), due=datetime.date(2026, 6, 1))
        finish(alice, task, on=datetime.date(2026, 6, 2), cost="250")
        top = self.top(self.page(signed_in, alice_home))
        assert top.count("<dt>") == 3
        for label in ("Envelope", "Spent", "Left"):
            assert f"<dt>{label}</dt>" in top
        for amount in ("1,000.00", "250.00", "750.00"):
            assert amount in top, amount
        assert "<progress" in top
        assert 'value="25"' in top
        assert "25 % spent" in top  # the words carry it, not the bar
        assert "<progress class=" not in top  # within the envelope: the normal colour

    def test_the_details_hold_the_other_figures(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        project_services.set_envelope(alice, alice_home, year=2026, amount=D("1000"))
        html = self.page(signed_in, alice_home)
        top = self.top(html)
        for gone in ("Left to plan", "Paid", "Projects", "Upkeep", "Sure"):
            assert gone not in top, gone
        details = html.split("<details", 1)[1]
        for kept in ("Left to plan", "Planned, projects and upkeep", "Paid", "Sure"):
            assert kept in details, kept  # still on the page, below the summary
        assert "Left to spend" not in html  # the same figure as "Left" above

    def test_an_overrun_is_said_in_words(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        project_services.set_envelope(alice, alice_home, year=2026, amount=D("100"))
        task = make_task(alice, alice_home, labour_budget=D("50"), due=datetime.date(2026, 6, 1))
        finish(alice, task, on=datetime.date(2026, 6, 2), cost="250")
        top = self.top(self.page(signed_in, alice_home))
        assert "Over by" in top
        assert "150.00" in top
        assert "250 % spent: over budget" in top
        assert 'value="100"' in top  # the bar is full, not past it
        assert '<progress class="over"' in top  # and in the warning colour

    def test_without_an_envelope_the_plans_are_the_reference(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        make_task(alice, alice_home, labour_budget=D("150"), due=datetime.date(2026, 6, 1))
        top = self.top(self.page(signed_in, alice_home))
        assert top.count("<dt>") == 3
        assert "<dt>Planned</dt>" in top
        assert "150.00" in top
        assert "0 % spent" in top

    def test_nothing_to_set_aside_has_no_gauge(self, signed_in: Client, alice_home: Home) -> None:
        top = self.top(self.page(signed_in, alice_home))
        assert top.count("<dt>") == 3
        assert "<progress" not in top

    def test_french(self, signed_in: Client, alice: User, alice_home: Home) -> None:
        project_services.set_envelope(alice, alice_home, year=2026, amount=D("100"))
        task = make_task(alice, alice_home, labour_budget=D("50"), due=datetime.date(2026, 6, 1))
        finish(alice, task, on=datetime.date(2026, 6, 2), cost="250")
        top = self.top(self.page(signed_in, alice_home, language="fr"))
        for word in ("Enveloppe", "Dépensé", "Reste", "Dépassement", "dépensé"):
            assert word in top, word


class TestGaugeStyle:
    """Every gauge is one bar of the charter, with its figure on its own line."""

    def test_the_stylesheet_draws_every_progress_with_the_tokens(self) -> None:
        css = (
            Path(__file__).parent.parent / "src/housecairn/static/css/components.css"
        ).read_text()
        rule = css.split("\nprogress {", 1)[1].split("}", 1)[0]
        assert "height: 8px" in rule
        assert "appearance: none" in rule
        assert "var(--hc-color-surface-muted)" in rule
        assert "var(--hc-color-primary)" in css.split("progress::-webkit-progress-value", 1)[1]
        assert "var(--hc-color-warning)" in css.split("progress.over", 1)[1]
        assert "forced-colors: active" in css  # still visible in a high-contrast mode
