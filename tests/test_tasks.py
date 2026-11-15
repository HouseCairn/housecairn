"""Tasks: services (create, done, undo, skip, postpone), web screens, roles and isolation."""

import datetime
import time
import zipfile
from decimal import Decimal
from unittest import mock

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.homes import exports
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Item, Room
from housecairn.tasks import services
from housecairn.tasks.forms import TaskForm
from housecairn.tasks.models import Occurrence, Status, Task
from tests.data_support import archive_text, export_data
from tests.tasks_fixtures import TODAY

pytest_plugins = ("tests.tasks_fixtures",)
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root", "frozen_today")]

DAY = datetime.timedelta(days=1)


def url(name: str, home: Home, **kwargs: object) -> str:
    return reverse(f"tasks:{name}", kwargs={"home_id": home.id, **kwargs})


def state(task: Task) -> list[tuple[object, ...]]:
    """Everything that tells where a task stands: its live occurrences, in order of creation.

    An occurrence an undo took back stays as a deleted row, the tombstone of the change log.
    """
    return [
        (
            o.id,
            o.status,
            o.due_on,
            o.completed_on,
            o.actual_cost,
            o.completion_note,
            o.postponed_from,
        )
        for o in Occurrence.all_objects.filter(task=task, deleted_at__isnull=True).order_by(
            "created_at", "id"
        )
    ]


class TestCreate:
    def test_only_the_title_is_required(self, alice: User, alice_home: Home) -> None:
        task = services.create_task(alice, alice_home, title="Check the gutters")
        assert (task.priority, task.repeat_mode, task.room, task.item) == (
            "normal",
            "once",
            None,
            None,
        )
        occurrence = services.open_occurrence_of(task)
        assert occurrence is not None
        assert (occurrence.status, occurrence.due_on) == (Status.PLANNED, None)

    def test_a_title_is_required(self, alice: User, alice_home: Home) -> None:
        with pytest.raises(ValidationError):
            services.create_task(alice, alice_home, title="")

    def test_every_field(
        self, alice: User, alice_home: Home, owner_room: Room, owner_item: Item
    ) -> None:
        contractor = services.create_contractor(
            alice, alice_home, name="J. Plumber", trade="plumber"
        )
        task = services.create_task(
            alice,
            alice_home,
            title="Service the boiler",
            room=owner_room,
            item=owner_item,
            priority="high",
            due_on=TODAY + 10 * DAY,
            estimated_minutes=90,
            labour_budget=Decimal("80.50"),
            material_budget=Decimal("12"),
            notes="Ask for the invoice",
            procedure="1. Switch off\n2. Call",
            assignee=alice,
            contractor=contractor,
            repeat_mode="fixed",
            repeat_unit="year",
            repeat_interval=1,
            season_start_month=9,
            season_end_month=11,
            hide_days_before=14,
        )
        occurrence = services.open_occurrence_of(task)
        assert occurrence is not None
        assert occurrence.visible_from == TODAY - 4 * DAY
        assert task.series_start == TODAY + 10 * DAY

    def test_the_room_follows_the_item(
        self, alice: User, alice_home: Home, owner_room: Room, owner_item: Item
    ) -> None:
        task = services.create_task(alice, alice_home, title="x", item=owner_item)
        assert task.room_id == owner_room.id

    def test_an_item_outside_the_room_is_refused(
        self, alice: User, alice_home: Home, owner_item: Item
    ) -> None:
        other = room_services.create_room(alice, alice_home, name="Cellar")
        with pytest.raises(ValidationError):
            services.create_task(alice, alice_home, title="x", room=other, item=owner_item)

    def test_things_of_another_home_are_refused(
        self, alice: User, bob: User, alice_home: Home, bob_home: Home
    ) -> None:
        theirs = room_services.create_room(bob, bob_home, name="Theirs")
        their_item = room_services.create_item(bob, theirs, name="Their boiler")
        their_contractor = services.create_contractor(bob, bob_home, name="B.")
        for field, value in (
            ("room", theirs),
            ("item", their_item),
            ("contractor", their_contractor),
            ("assignee", bob),
        ):
            with pytest.raises(ValidationError):
                services.create_task(alice, alice_home, title="x", **{field: value})
        assert not Task.objects.filter(home=alice_home).exists()

    def test_the_assignee_must_be_a_member(
        self, alice: User, alice_home: Home, member: User
    ) -> None:
        assert (
            services.create_task(alice, alice_home, title="x", assignee=member).assignee == member
        )

    def test_a_fixed_repeat_needs_a_date(self, alice: User, alice_home: Home) -> None:
        with pytest.raises(ValidationError, match="needs a date"):
            services.create_task(alice, alice_home, title="x", repeat_mode="fixed")

    @pytest.mark.parametrize(
        "bad",
        [
            {"repeat_interval": 0},
            {"repeat_mode": "fixed", "repeat_unit": "year", "repeat_interval": 99},
            {"season_start_month": 4},
            {"season_start_month": 4, "season_end_month": 13},
            {"hide_days_before": 400},
            {"labour_budget": Decimal("-1")},
            {"priority": "urgent"},
            {"procedure": "x" * 20001},
        ],
    )
    def test_bad_values_are_refused(
        self, alice: User, alice_home: Home, bad: dict[str, object]
    ) -> None:
        with pytest.raises(ValidationError):
            services.create_task(alice, alice_home, title="x", due_on=TODAY, **bad)

    def test_unknown_fields_are_refused(self, alice: User, alice_home: Home) -> None:
        with pytest.raises(ValueError, match="Cannot change"):
            services.create_task(alice, alice_home, title="x", deleted_at=None)

    def test_update_moves_the_open_occurrence(self, alice: User, owner_task: Task) -> None:
        services.update_task(
            alice, owner_task, title="Renamed", due_on=TODAY + 3 * DAY, hide_days_before=2
        )
        occurrence = services.open_occurrence_of(owner_task)
        assert occurrence is not None
        assert (occurrence.due_on, occurrence.visible_from) == (TODAY + 3 * DAY, TODAY + DAY)
        services.update_task(alice, owner_task, notes="n")
        assert services.open_occurrence_of(owner_task).due_on == TODAY + 3 * DAY  # type: ignore[union-attr]

    def test_changing_the_rule_restarts_the_series_from_the_open_date(
        self, alice: User, alice_home: Home
    ) -> None:
        task = services.create_task(
            alice,
            alice_home,
            title="x",
            due_on=D(2026, 1, 31),
            repeat_mode="fixed",
            repeat_unit="month",
        )
        services.update_task(alice, task, due_on=D(2026, 7, 30))
        assert Task.objects.get(id=task.id).series_start == D(2026, 7, 30)

    def test_delete_hides_the_task_and_its_occurrences(
        self, alice: User, alice_home: Home, owner_task: Task
    ) -> None:
        services.delete_task(alice, owner_task)
        assert not services.tasks_of(alice_home).exists()
        assert not services.occurrences_of(alice_home).exists()
        assert Task.all_objects.filter(id=owner_task.id).exists()


D = datetime.date


class TestDoneAndUndo:
    def test_done_creates_the_next_occurrence_after_the_last_time(
        self, alice: User, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        closing = services.complete(alice, owner_occurrence)
        assert (closing.occurrence.status, closing.occurrence.completed_on) == (Status.DONE, TODAY)
        assert closing.occurrence.completed_by_id == alice.id
        assert closing.next is not None
        assert (closing.next.status, closing.next.due_on) == (Status.PLANNED, D(2027, 6, 15))
        assert services.open_occurrence_of(owner_task) == closing.next

    def test_done_then_undo_gives_back_the_initial_state(
        self, alice: User, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        before = state(owner_task)
        closing = services.complete(
            alice, owner_occurrence, actual_cost=Decimal("4.5"), note="Two AAA"
        )
        assert state(owner_task) != before
        reopened = services.undo(alice, closing.occurrence)
        assert reopened.status == Status.PLANNED
        assert state(owner_task) == before
        assert Occurrence.objects.filter(task=owner_task).count() == 1
        # The occurrence taken back is deleted, not erased: a phone learns of it.
        withdrawn = Occurrence.all_objects.get(task=owner_task, deleted_at__isnull=False)
        assert withdrawn.follows_id is None

    def test_a_postponed_occurrence_comes_back_postponed(
        self, alice: User, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        services.postpone(alice, owner_occurrence, TODAY + 5 * DAY)
        before = state(owner_task)
        closing = services.complete(alice, Occurrence.objects.get(id=owner_occurrence.id))
        services.undo(alice, closing.occurrence)
        assert state(owner_task) == before
        assert Occurrence.objects.get(id=owner_occurrence.id).status == Status.POSTPONED

    def test_a_task_done_once_has_no_next_and_undo_reopens_it(
        self, alice: User, alice_home: Home
    ) -> None:
        task = services.create_task(alice, alice_home, title="Paint the fence", due_on=TODAY)
        occurrence = services.open_occurrence_of(task)
        assert occurrence is not None
        closing = services.complete(alice, occurrence)
        assert closing.next is None
        assert services.open_occurrence_of(task) is None
        services.undo(alice, closing.occurrence)
        assert services.open_occurrence_of(task) is not None

    def test_a_fixed_series_keeps_its_dates(self, alice: User, alice_home: Home) -> None:
        task = services.create_task(
            alice,
            alice_home,
            title="x",
            due_on=D(2026, 1, 31),
            repeat_mode="fixed",
            repeat_unit="month",
        )
        occurrence = services.open_occurrence_of(task)
        assert occurrence is not None
        # Done on 3 February, late: the next date is the end of February, then 31 March.
        first = services.complete(alice, occurrence, on=D(2026, 2, 3)).next
        assert first is not None
        assert first.due_on == D(2026, 2, 28)
        second = services.complete(alice, first, on=D(2026, 2, 28)).next
        assert second is not None
        assert second.due_on == D(2026, 3, 31)

    def test_the_next_occurrence_follows_the_masking_delay(
        self, alice: User, alice_home: Home
    ) -> None:
        task = services.create_task(
            alice,
            alice_home,
            title="x",
            due_on=TODAY,
            repeat_mode="after_last",
            repeat_unit="month",
            hide_days_before=10,
        )
        closing = services.complete(alice, services.open_occurrence_of(task))  # type: ignore[arg-type]
        assert closing.next is not None
        assert closing.next.visible_from == closing.next.due_on - 10 * DAY  # type: ignore[operator]
        assert not services.open_occurrences(alice_home, day=TODAY).exists()
        assert services.open_occurrences(alice_home, day=TODAY, include_hidden=True).count() == 1
        later = closing.next.due_on - 10 * DAY  # type: ignore[operator]
        assert services.open_occurrences(alice_home, day=later).count() == 1

    def test_done_twice_is_refused(self, alice: User, owner_occurrence: Occurrence) -> None:
        services.complete(alice, owner_occurrence)
        with pytest.raises(ValidationError, match="already closed"):
            services.complete(alice, owner_occurrence)
        assert Occurrence.objects.filter(task=owner_occurrence.task).count() == 2

    def test_the_date_cannot_be_in_the_future(
        self, alice: User, owner_occurrence: Occurrence
    ) -> None:
        with pytest.raises(ValidationError):
            services.complete(alice, owner_occurrence, on=TODAY + DAY)
        assert (
            services.complete(alice, owner_occurrence, on=TODAY - 3 * DAY).occurrence.completed_on
            == TODAY - 3 * DAY
        )

    def test_undo_needs_a_closed_occurrence(
        self, alice: User, owner_occurrence: Occurrence
    ) -> None:
        with pytest.raises(ValidationError, match="done or skipped"):
            services.undo(alice, owner_occurrence)

    def test_undo_locks_the_next_occurrence_as_well_as_the_closed_one(
        self, alice: User, owner_occurrence: Occurrence
    ) -> None:
        closing = services.complete(alice, owner_occurrence)
        locked: list[str] = []
        real = models.QuerySet.select_for_update

        def spy(queryset: models.QuerySet[Occurrence], *args: object, **kwargs: object) -> object:
            locked.append(queryset.query.__str__())
            return real(queryset, *args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(models.QuerySet, "select_for_update", spy):
            services.undo(alice, closing.occurrence)
        # The change log locks the row of the home too; the occurrences are locked twice.
        locked = [query for query in locked if 'FROM "tasks_occurrence"' in query]
        assert len(locked) == 2
        assert "follows_id" in locked[1]

    def test_undo_is_refused_when_the_next_occurrence_was_closed_meanwhile(
        self, alice: User, owner_occurrence: Occurrence
    ) -> None:
        closing = services.complete(alice, owner_occurrence)
        assert closing.next is not None
        services.complete(alice, closing.next)
        with pytest.raises(ValidationError, match="cannot be undone"):
            services.undo(alice, closing.occurrence)

    def test_undo_is_refused_once_the_next_occurrence_was_worked_on(
        self, alice: User, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        closing = services.complete(alice, owner_occurrence)
        assert closing.next is not None
        services.postpone(alice, closing.next, TODAY + 400 * DAY)
        with pytest.raises(ValidationError, match="changed since"):
            services.undo(alice, closing.occurrence)
        assert Occurrence.objects.get(id=closing.occurrence.id).status == Status.DONE

    def test_undo_is_refused_when_the_next_occurrence_has_a_note_or_a_file(
        self, alice: User, owner_occurrence: Occurrence
    ) -> None:
        closing = services.complete(alice, owner_occurrence)
        assert closing.next is not None
        closing.next.completion_note = "Prepared the batteries"
        closing.next.save()
        with pytest.raises(ValidationError):
            services.undo(alice, closing.occurrence)

    def test_skip_closes_without_doing_and_creates_the_next(
        self, alice: User, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        before = state(owner_task)
        closing = services.skip(alice, owner_occurrence)
        assert closing.occurrence.status == Status.SKIPPED
        assert closing.occurrence.completed_on is None
        assert closing.next is not None
        assert closing.next.due_on == D(2027, 6, 15)
        services.undo(alice, closing.occurrence)
        assert state(owner_task) == before

    def test_postpone(self, alice: User, owner_occurrence: Occurrence) -> None:
        original = owner_occurrence.due_on
        moved = services.postpone(alice, owner_occurrence, TODAY + 7 * DAY)
        assert (moved.status, moved.due_on, moved.postponed_from) == (
            Status.POSTPONED,
            TODAY + 7 * DAY,
            original,
        )
        again = services.postpone(alice, moved, TODAY + 9 * DAY)
        assert again.postponed_from == original
        with pytest.raises(ValidationError):
            services.postpone(alice, again, TODAY - DAY)

    def test_the_details_come_after(self, alice: User, owner_occurrence: Occurrence) -> None:
        with pytest.raises(ValidationError, match="once the task is done"):
            services.update_occurrence(alice, owner_occurrence, actual_cost=Decimal("1"))
        done = services.complete(alice, owner_occurrence).occurrence
        services.update_occurrence(
            alice,
            done,
            completed_on=TODAY - 5 * DAY,
            actual_cost=Decimal("19.90"),
            completion_note="Bought two",
        )
        done.refresh_from_db()
        assert (done.completed_on, done.actual_cost, done.completion_note) == (
            TODAY - 5 * DAY,
            Decimal("19.90"),
            "Bought two",
        )
        with pytest.raises(ValidationError):
            services.update_occurrence(alice, done, completed_on=TODAY + DAY)
        with pytest.raises(ValueError, match="Cannot change"):
            services.update_occurrence(alice, done, status="planned")


class TestDateBounds:
    """Dates are held to 100 years around today: no input can make a series long to count."""

    LOW = datetime.date(1926, 6, 15)
    HIGH = datetime.date(2126, 6, 15)

    def test_the_window_is_a_hundred_years_around_today(self) -> None:
        assert services.date_bounds() == (self.LOW, self.HIGH)

    @pytest.mark.parametrize("day", [datetime.date(1, 1, 1), datetime.date(1926, 6, 14)])
    def test_create_and_update_refuse_a_date_before_the_window(
        self, alice: User, alice_home: Home, day: datetime.date
    ) -> None:
        with pytest.raises(ValidationError, match="within 100 years"):
            services.create_task(alice, alice_home, title="Old", due_on=day)
        task = services.create_task(alice, alice_home, title="Ok", due_on=self.LOW)
        with pytest.raises(ValidationError, match="within 100 years"):
            services.update_task(alice, task, due_on=day)
        with pytest.raises(ValidationError, match="within 100 years"):
            services.create_task(
                alice, alice_home, title="Far", due_on=self.HIGH + datetime.timedelta(days=1)
            )

    def test_the_sheet_of_a_daily_task_on_the_oldest_date_is_quick(
        self, alice: User, alice_home: Home
    ) -> None:
        task = services.create_task(
            alice,
            alice_home,
            title="Daily",
            due_on=self.LOW,
            repeat_mode="fixed",
            repeat_unit="day",
            repeat_interval=1,
        )
        began = time.perf_counter()
        dates = services.upcoming_dates(task)
        assert dates == [self.LOW + datetime.timedelta(days=n) for n in (1, 2, 3)]
        occurrence = services.open_occurrence_of(task)
        assert occurrence is not None
        services.complete(alice, occurrence)
        assert time.perf_counter() - began < 0.2

    def test_completion_and_postponement_dates_are_held_to_the_window(
        self, alice: User, owner_occurrence: Occurrence
    ) -> None:
        with pytest.raises(ValidationError, match="within 100 years"):
            services.complete(alice, owner_occurrence, on=datetime.date(1, 1, 1))
        with pytest.raises(ValidationError, match="within 100 years"):
            services.postpone(alice, owner_occurrence, self.HIGH + datetime.timedelta(days=1))
        closing = services.complete(alice, owner_occurrence, on=self.LOW)
        with pytest.raises(ValidationError, match="within 100 years"):
            services.update_occurrence(
                alice, closing.occurrence, completed_on=datetime.date(1, 1, 1)
            )

    def test_the_masking_delay_cannot_overflow(self, alice: User, alice_home: Home) -> None:
        task = Task(hide_days_before=365)
        assert services._visible_from(task, datetime.date.min) == datetime.date.min
        made = services.create_task(
            alice, alice_home, title="Hidden", due_on=self.LOW, hide_days_before=365
        )
        occurrence = services.open_occurrence_of(made)
        assert occurrence is not None
        assert occurrence.visible_from == self.LOW - datetime.timedelta(days=365)

    def test_the_forms_show_the_message(
        self, signed_in: Client, alice_home: Home, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        form = {
            "title": "Old",
            "priority": "normal",
            "repeat_mode": "once",
            "repeat_interval": "1",
            "repeat_unit": "month",
            "hide_days_before": "0",
            "due_on": "0001-01-01",
        }
        page = signed_in.post(url("create", alice_home), form)
        assert page.status_code == 200
        assert "within 100 years of today" in page.content.decode()
        assert not Task.objects.filter(title="Old").exists()
        page = signed_in.post(url("edit", alice_home, task_id=owner_task.id), form)
        assert page.status_code == 200
        assert "within 100 years of today" in page.content.decode()
        page = signed_in.post(
            url("postpone", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id),
            {"due_on": "9000-01-01"},
        )
        assert page.status_code == 200
        assert "within 100 years of today" in page.content.decode()


class TestComesBack:
    """A task done for good returns when it gets a date or a rule (docs/adr/0031)."""

    def done_once(self, alice: User, alice_home: Home) -> Task:
        task = services.create_task(alice, alice_home, title="Sweep", due_on=TODAY)
        occurrence = services.open_occurrence_of(task)
        assert occurrence is not None
        services.complete(alice, occurrence)
        assert services.open_occurrence_of(task) is None
        return task

    def test_a_done_task_made_yearly_with_a_date_gets_its_occurrence(
        self, alice: User, alice_home: Home
    ) -> None:
        task = self.done_once(alice, alice_home)
        services.update_task(
            alice,
            task,
            due_on=D(2027, 3, 1),
            repeat_mode="fixed",
            repeat_unit="year",
            repeat_interval=1,
        )
        current = services.open_occurrence_of(task)
        assert current is not None
        assert current.due_on == D(2027, 3, 1)
        assert task.series_start == D(2027, 3, 1)
        assert Occurrence.objects.filter(task=task).count() == 2
        # and it goes on: done, the next year is there
        assert services.complete(alice, current, on=TODAY).next.due_on == D(2028, 3, 1)  # type: ignore[union-attr]

    def test_a_date_alone_brings_it_back(self, alice: User, alice_home: Home) -> None:
        task = self.done_once(alice, alice_home)
        services.update_task(alice, task, due_on=D(2026, 9, 1))
        current = services.open_occurrence_of(task)
        assert current is not None
        assert current.due_on == D(2026, 9, 1)

    def test_a_rule_without_a_date_counts_from_the_last_time(
        self, alice: User, alice_home: Home
    ) -> None:
        task = self.done_once(alice, alice_home)
        services.update_task(
            alice, task, repeat_mode="after_last", repeat_unit="month", repeat_interval=6
        )
        current = services.open_occurrence_of(task)
        assert current is not None
        assert current.due_on == D(2026, 12, 15)

    def test_a_fixed_rule_without_a_date_is_refused(self, alice: User, alice_home: Home) -> None:
        task = self.done_once(alice, alice_home)
        with pytest.raises(ValidationError):
            services.update_task(alice, task, repeat_mode="fixed")
        assert services.open_occurrence_of(task) is None

    def test_a_title_change_leaves_a_done_task_done(self, alice: User, alice_home: Home) -> None:
        task = self.done_once(alice, alice_home)
        services.update_task(alice, task, title="Sweep the yard")
        assert services.open_occurrence_of(task) is None

    def test_undo_of_the_last_closing_removes_the_one_that_came_back(
        self, alice: User, alice_home: Home
    ) -> None:
        task = self.done_once(alice, alice_home)
        services.update_task(alice, task, due_on=D(2027, 3, 1))
        closed = Occurrence.objects.get(task=task, status=Status.DONE)
        services.undo(alice, closed)
        assert Occurrence.objects.filter(task=task).count() == 1
        assert services.open_occurrence_of(task) == closed

    def test_the_chain_continues_from_the_last_closing(self, alice: User, alice_home: Home) -> None:
        task = services.create_task(
            alice, alice_home, title="Chain", due_on=TODAY - DAY, repeat_mode="after_last"
        )
        first = services.open_occurrence_of(task)
        assert first is not None
        second = services.complete(alice, first).next
        assert second is not None
        services.update_task(alice, task, repeat_mode="once")
        services.complete(alice, second)
        services.update_task(alice, task, due_on=D(2027, 1, 1), repeat_mode="once")
        reopened = services.open_occurrence_of(task)
        assert reopened is not None
        assert reopened.follows_id == second.id

    def test_the_screen_gives_the_task_its_date(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = self.done_once(alice, alice_home)
        response = signed_in.post(
            url("edit", alice_home, task_id=task.id),
            {
                "title": "Sweep",
                "priority": "normal",
                "repeats": "on",
                "repeat_from": "fixed",
                "repeat_interval": "1",
                "repeat_unit": "year",
                "hide_days_before": "0",
                "due_on": "2027-03-01",
            },
        )
        assert response.status_code == 302
        current = services.open_occurrence_of(task)
        assert current is not None
        assert current.due_on == D(2027, 3, 1)
        assert "2027" in signed_in.get(response["Location"]).content.decode()


class TestQueries:
    def test_arrears_stay_on_top_and_undated_come_last(self, alice: User, alice_home: Home) -> None:
        late = services.create_task(alice, alice_home, title="late", due_on=TODAY - 10 * DAY)
        services.create_task(alice, alice_home, title="undated")
        soon = services.create_task(alice, alice_home, title="soon", due_on=TODAY + 2 * DAY)
        services.create_task(alice, alice_home, title="later", due_on=TODAY + 60 * DAY)
        titles = [o.task.title for o in services.open_occurrences(alice_home, day=TODAY)]
        assert titles == ["late", "soon", "later", "undated"]
        assert [o.task_id for o in services.overdue(alice_home, day=TODAY)] == [late.id]
        assert [o.task_id for o in services.upcoming(alice_home, days=7, day=TODAY)] == [soon.id]

    def test_done_lists_the_most_recent_first(self, alice: User, alice_home: Home) -> None:
        first = services.create_task(alice, alice_home, title="a", due_on=TODAY)
        second = services.create_task(alice, alice_home, title="b", due_on=TODAY)
        services.complete(alice, services.open_occurrence_of(first), on=TODAY - 3 * DAY)  # type: ignore[arg-type]
        services.complete(alice, services.open_occurrence_of(second), on=TODAY - DAY)  # type: ignore[arg-type]
        assert [o.task.title for o in services.done(alice_home)] == ["b", "a"]

    def test_the_next_dates_of_a_series(self, owner_task: Task) -> None:
        assert services.upcoming_dates(owner_task, 2) == [D(2027, 6, 13), D(2028, 6, 13)]

    def test_no_next_dates_for_a_task_done_once(self, alice: User, alice_home: Home) -> None:
        task = services.create_task(alice, alice_home, title="x", due_on=TODAY)
        assert services.upcoming_dates(task) == []


class TestContractors:
    def test_crud(self, alice: User, alice_home: Home) -> None:
        contractor = services.create_contractor(
            alice, alice_home, name="A. Roofer", trade="roofer", note="n"
        )
        services.update_contractor(alice, contractor, trade="other", trade_other="Slater")
        task = services.create_task(alice, alice_home, title="x", contractor=contractor)
        services.delete_contractor(alice, contractor)
        task.refresh_from_db()
        assert task.contractor is None
        assert not services.contractors_of(alice_home).exists()

    def test_a_name_is_required(self, alice: User, alice_home: Home) -> None:
        with pytest.raises(ValidationError):
            services.create_contractor(alice, alice_home, name="")


class TestRoles:
    def test_read_only_cannot_write(
        self,
        reader: User,
        alice: User,
        owner_task: Task,
        owner_occurrence: Occurrence,
        alice_home: Home,
    ) -> None:
        attempts = [
            lambda: services.create_task(reader, alice_home, title="x"),
            lambda: services.update_task(reader, owner_task, title="x"),
            lambda: services.delete_task(reader, owner_task),
            lambda: services.complete(reader, owner_occurrence),
            lambda: services.skip(reader, owner_occurrence),
            lambda: services.postpone(reader, owner_occurrence, TODAY + DAY),
            lambda: services.create_contractor(reader, alice_home, name="x"),
        ]
        for attempt in attempts:
            with pytest.raises(PermissionDenied):
                attempt()
        done = services.complete(alice, owner_occurrence)
        with pytest.raises(PermissionDenied):
            services.undo(reader, done.occurrence)
        with pytest.raises(PermissionDenied):
            services.update_occurrence(reader, done.occurrence, completion_note="x")
        assert Task.objects.get(id=owner_task.id).title == owner_task.title

    def test_a_member_writes(self, member: User, alice_home: Home) -> None:
        assert services.create_task(member, alice_home, title="x").id


class TestScreens:
    def test_the_list_shows_arrears_first_and_done_has_a_button(
        self, signed_in: Client, alice_home: Home, owner_task: Task, alice: User
    ) -> None:
        services.create_task(alice, alice_home, title="Later", due_on=TODAY + 5 * DAY)
        response = signed_in.get(url("list", alice_home))
        content = response.content.decode()
        assert response.status_code == 200
        assert content.index("Change the smoke detector") < content.index("Later")
        assert "2 days late" in content
        assert "tasks/" in content
        assert "/complete/" in content

    def test_the_done_button_has_its_own_48_px_class(
        self, signed_in: Client, alice_home: Home, owner_occurrence: Occurrence
    ) -> None:
        content = signed_in.get(url("today", alice_home)).content.decode()
        assert 'class="button-small button-done"' in content
        css = (settings.BASE_DIR / "static" / "css" / "tasks.css").read_text(encoding="utf-8")
        assert ".button-done {\n  min-height: var(--hc-layout-touch-target-lg);" in css

    def test_a_material_row_wraps_so_the_name_keeps_its_width(self) -> None:
        css = (settings.BASE_DIR / "static" / "css" / "tasks.css").read_text(encoding="utf-8")
        assert ".material-row {\n  flex-wrap: wrap;" in css
        assert ".material-row .row-body {\n  flex: 1 1 14rem;" in css

    def test_done_in_one_gesture_then_undo(
        self, signed_in: Client, alice_home: Home, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        before = state(owner_task)
        address = url(
            "complete", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id
        )
        response = signed_in.post(address, {"next": url("list", alice_home)})
        assert response.status_code == 302
        # Back to the row, the banner kept for that page only, never in the address (NAV-12).
        assert response["Location"] == f"{url('list', alice_home)}#occ-{owner_occurrence.id}"
        page = signed_in.get(response["Location"])
        assert "Marked done." in page.content.decode()
        undo = url("undo", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id)
        assert undo in page.content.decode()
        assert signed_in.post(undo, {"next": url("list", alice_home)}).status_code == 302
        assert state(owner_task) == before

    def test_the_undo_banner_and_the_hidden_link_keep_the_filters(
        self,
        signed_in: Client,
        alice: User,
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
        owner_room: Room,
    ) -> None:
        services.create_task(
            alice,
            alice_home,
            title="Far off",
            room=owner_room,
            due_on=TODAY + 60 * DAY,
            hide_days_before=7,
        )
        listing = f"{url('list', alice_home)}?room={owner_room.id}&page=1"
        address = url(
            "complete", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id
        )
        response = signed_in.post(address, {"next": listing})
        page = signed_in.get(response["Location"]).content.decode()
        undo_next = f'name="next" value="{url("list", alice_home)}?room={owner_room.id}&amp;page=1"'
        assert undo_next in page
        assert "undo=" not in page.split('name="next"')[1].split(">")[0]
        assert f"?room={owner_room.id}&amp;page=1&amp;hidden=1" in page

    def test_the_pages_keep_the_hidden_filter(
        self, signed_in: Client, alice: User, alice_home: Home, owner_task: Task
    ) -> None:
        with mock.patch("housecairn.tasks.views.PAGE_SIZE", 1):
            services.create_task(alice, alice_home, title="Second", due_on=TODAY + DAY)
            page = signed_in.get(f"{url('list', alice_home)}?hidden=1").content.decode()
        assert "&amp;hidden=1&amp;page=2" in page

    def test_the_return_address_must_be_on_this_site(
        self, signed_in: Client, alice_home: Home, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        address = url(
            "complete", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id
        )
        response = signed_in.post(address, {"next": "https://evil.example.com/x"})
        assert response["Location"].startswith(f"/h/{alice_home.id}/tasks/{owner_task.id}/")

    def test_a_return_address_with_a_control_character_is_refused(
        self, signed_in: Client, alice_home: Home, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        address = url(
            "complete", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id
        )
        response = signed_in.post(address, {"next": "//\r"})
        assert response["Location"].startswith(f"/h/{alice_home.id}/tasks/{owner_task.id}/")

    def test_a_second_tap_shows_the_reason(
        self, signed_in: Client, alice_home: Home, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        address = url(
            "complete", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id
        )
        signed_in.post(address)
        again = signed_in.post(address, follow=True)
        assert "already closed" in again.content.decode()

    def test_skip_and_postpone(
        self, signed_in: Client, alice_home: Home, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        address = url(
            "postpone", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id
        )
        assert signed_in.get(address).status_code == 200
        bad = signed_in.post(address, {"due_on": str(TODAY - 3 * DAY)})
        assert bad.status_code == 200
        assert "not in the past" in bad.content.decode()
        ok = signed_in.post(address, {"due_on": str(TODAY + 4 * DAY)})
        assert ok.status_code == 302
        page = signed_in.get(url("detail", alice_home, task_id=owner_task.id)).content.decode()
        assert "Postponed" in page
        skip = url("skip", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id)
        assert signed_in.post(skip).status_code == 302
        assert Occurrence.objects.get(id=owner_occurrence.id).status == Status.SKIPPED

    def test_create_with_only_a_title(self, signed_in: Client, alice_home: Home) -> None:
        assert signed_in.get(url("create", alice_home)).status_code == 200
        response = signed_in.post(
            url("create", alice_home),
            {
                "title": "Clean the gutters",
                "priority": "normal",
                "repeat_mode": "once",
                "repeat_interval": "1",
                "repeat_unit": "month",
                "hide_days_before": "0",
            },
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        task = Task.objects.get(title="Clean the gutters")
        assert response["Location"] == url("detail", alice_home, task_id=task.id)

    def test_create_from_a_room_prefills_it(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        response = signed_in.get(url("create", alice_home) + f"?item={owner_item.id}")
        assert f'value="{owner_item.room_id}" selected' in response.content.decode()
        assert signed_in.get(url("create", alice_home) + "?item=nonsense").status_code == 200

    def test_create_a_repeating_task_with_a_new_contractor(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        response = signed_in.post(
            url("create", alice_home),
            {
                "title": "Service the boiler",
                "priority": "high",
                "room": str(owner_room.id),
                "due_on": str(TODAY + DAY),
                "repeats": "on",
                "repeat_from": "fixed",
                "repeat_interval": "12",
                "repeat_unit": "month",
                "season_start_month": "9",
                "season_end_month": "11",
                "hide_days_before": "30",
                "contractor": "new",
                "new_contractor_name": "A. Plumber",
                "new_contractor_trade": "plumber",
                "labour_budget": "80",
                "procedure": "**Switch off** first",
                "duration_value": "1",
                "duration_unit": "hour",
            },
        )
        assert response.status_code == 302, response.content.decode()[:3000]
        task = Task.objects.get(title="Service the boiler")
        assert task.contractor
        assert task.contractor.name == "A. Plumber"
        assert task.estimated_minutes == 60
        page = signed_in.get(response["Location"]).content.decode()
        assert "<strong>Switch off</strong>" in page
        assert "Every 12 months, from September to November" in page

    def test_errors_of_the_service_are_shown_on_the_form(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        response = signed_in.post(
            url("create", alice_home),
            {
                "title": "x",
                "priority": "normal",
                "repeats": "on",
                "repeat_from": "fixed",
                "repeat_interval": "1",
                "repeat_unit": "month",
                "hide_days_before": "0",
            },
        )
        assert response.status_code == 200
        assert "needs a date" in response.content.decode()

    def test_a_contractor_is_chosen_in_the_list_or_added_in_place(
        self, signed_in: Client, alice_home: Home, alice: User
    ) -> None:
        known = services.create_contractor(alice, alice_home, name="X")
        base = {
            "priority": "normal",
            "repeat_mode": "once",
            "repeat_interval": "1",
            "repeat_unit": "month",
            "hide_days_before": "0",
        }
        create = url("create", alice_home)
        signed_in.post(create, {**base, "title": "a", "contractor": str(known.id)})
        new = {"contractor": "new", "new_contractor_name": " New  One ", "new_contractor_trade": ""}
        signed_in.post(create, {**base, "title": "b", **new})
        # The fields of a new one are ignored without the choice "Add a contractor…".
        signed_in.post(create, {**base, "title": "c", "new_contractor_name": "Ignored"})
        assert Task.objects.get(title="a").contractor == known
        made = Task.objects.get(title="b").contractor
        assert made is not None
        assert (made.name, made.trade) == ("New One", "")
        assert Task.objects.get(title="c").contractor is None
        assert services.contractors_of(alice_home).count() == 2

    @pytest.mark.parametrize(
        ("added", "error"),
        [
            ({"new_contractor_name": "  "}, "Give the name of the new contractor."),
            ({"new_contractor_name": "pipes LTD"}, "is already a contractor of this home"),
            (
                {"new_contractor_name": "Pools", "new_contractor_trade": "other"},
                "Say which trade.",
            ),
        ],
    )
    def test_a_new_contractor_is_refused_empty_twice_or_without_its_trade(
        self, signed_in: Client, alice_home: Home, alice: User, added: dict[str, str], error: str
    ) -> None:
        services.create_contractor(alice, alice_home, name="Pipes Ltd")
        form = {
            "title": "a",
            "priority": "normal",
            "repeat_interval": "1",
            "repeat_unit": "month",
            "hide_days_before": "0",
            "contractor": "new",
            **added,
        }
        response = signed_in.post(url("create", alice_home), form)
        assert response.status_code == 200
        assert error in response.text
        assert not Task.objects.filter(title="a").exists()
        assert services.contractors_of(alice_home).count() == 1

    def test_with_the_trade_other_the_words_are_kept(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        form = {
            "title": "a",
            "priority": "normal",
            "repeat_interval": "1",
            "repeat_unit": "month",
            "hide_days_before": "0",
            "contractor": "new",
            "new_contractor_name": "Pools",
            "new_contractor_trade": "other",
            "new_contractor_trade_other": "Pool maintenance",
        }
        assert signed_in.post(url("create", alice_home), form).status_code == 302
        made = Task.objects.get(title="a").contractor
        assert made is not None
        assert (made.trade, made.trade_other) == ("other", "Pool maintenance")

    def test_the_field_is_a_list_of_the_home_ending_with_add(
        self, signed_in: Client, alice_home: Home, alice: User, bob: User, bob_home: Home
    ) -> None:
        services.create_contractor(alice, alice_home, name="Pipes Ltd", trade="plumber")
        services.create_contractor(bob, bob_home, name="Not Mine")
        page = signed_in.get(url("create", alice_home)).text
        field = page.split('name="contractor"')[1].split("</select>")[0]
        assert "Pipes Ltd</option>" in field
        assert "Not Mine" not in field
        assert field.rstrip().endswith('<option value="new">Add a contractor…</option>')
        assert 'name="contractor_name"' not in page
        assert "<datalist" not in page
        assert 'data-shown-when="contractor=new"' in page
        # The choices are translated at each request, in the language of the reader.
        page = signed_in.get(url("create", alice_home), HTTP_ACCEPT_LANGUAGE="fr").text
        assert "Ajouter un intervenant…" in page
        assert "semaines</option>" in page

    def test_editing_keeps_the_contractor_and_the_dash_clears_it(
        self, signed_in: Client, alice_home: Home, alice: User, owner_task: Task
    ) -> None:
        contractor = services.create_contractor(alice, alice_home, name="Pipes Ltd")
        services.update_task(alice, owner_task, contractor=contractor)
        edit = url("edit", alice_home, task_id=owner_task.id)
        assert f'<option value="{contractor.id}" selected>' in signed_in.get(edit).text
        form = {
            "title": owner_task.title,
            "priority": "normal",
            "repeat_mode": "once",
            "repeat_interval": "1",
            "repeat_unit": "month",
            "hide_days_before": "0",
        }
        signed_in.post(edit, {**form, "contractor": str(contractor.id)})
        owner_task.refresh_from_db()
        assert owner_task.contractor == contractor
        signed_in.post(edit, {**form, "contractor": ""})
        owner_task.refresh_from_db()
        assert owner_task.contractor is None

    def test_a_contractor_of_another_home_is_refused(
        self, signed_in: Client, alice_home: Home, bob: User, bob_home: Home
    ) -> None:
        theirs = services.create_contractor(bob, bob_home, name="Theirs")
        form = {
            "title": "a",
            "priority": "normal",
            "repeat_interval": "1",
            "repeat_unit": "month",
            "hide_days_before": "0",
            "contractor": str(theirs.id),
        }
        assert signed_in.post(url("create", alice_home), form).status_code == 200
        assert not Task.objects.filter(title="a").exists()

    def test_edit_and_delete(self, signed_in: Client, alice_home: Home, owner_task: Task) -> None:
        edit = url("edit", alice_home, task_id=owner_task.id)
        page = signed_in.get(edit)
        assert page.status_code == 200
        assert 'name="uploads"' not in page.content.decode()
        changed = signed_in.post(
            edit,
            {
                "title": "New title",
                "priority": "low",
                "repeat_mode": "once",
                "repeat_interval": "1",
                "repeat_unit": "month",
                "hide_days_before": "0",
                "due_on": str(TODAY + 9 * DAY),
            },
        )
        assert changed.status_code == 302
        owner_task.refresh_from_db()
        assert owner_task.title == "New title"
        delete = url("delete", alice_home, task_id=owner_task.id)
        assert signed_in.get(delete).status_code == 200
        assert signed_in.post(delete).status_code == 302
        assert signed_in.get(url("detail", alice_home, task_id=owner_task.id)).status_code == 404

    def test_the_sheet_and_the_history(
        self,
        signed_in: Client,
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
        alice: User,
    ) -> None:
        done = services.complete(
            alice, owner_occurrence, actual_cost=Decimal("3.20"), note="Done in five minutes"
        ).occurrence
        page = signed_in.get(url("detail", alice_home, task_id=owner_task.id)).content.decode()
        assert "Done in five minutes" in page
        assert "€3.20" in page
        details = url("occurrence-edit", alice_home, task_id=owner_task.id, occurrence_id=done.id)
        assert details in page
        form = signed_in.get(details)
        assert form.status_code == 200
        saved = signed_in.post(
            details,
            {"completed_on": str(TODAY - DAY), "actual_cost": "5", "completion_note": "Corrected"},
        )
        assert saved.status_code == 302
        done.refresh_from_db()
        assert done.actual_cost == 5
        invalid = signed_in.post(details, {"completed_on": str(TODAY + DAY)})
        assert invalid.status_code == 200

    def test_the_done_filter_and_the_room_filter(
        self,
        signed_in: Client,
        alice_home: Home,
        owner_room: Room,
        owner_task: Task,
        alice: User,
        owner_occurrence: Occurrence,
    ) -> None:
        services.complete(alice, owner_occurrence)
        done = signed_in.get(url("list", alice_home) + "?status=done").content.decode()
        assert "Change the smoke detector battery" in done
        mine = signed_in.get(url("list", alice_home) + f"?room={owner_room.id}").content.decode()
        assert "Change the smoke detector battery" in mine
        other = room_services.create_room(alice, alice_home, name="Attic")
        empty = signed_in.get(url("list", alice_home) + f"?room={other.id}").content.decode()
        assert "Change the smoke detector battery" not in empty
        assert "Nothing to do" in empty
        assert (
            "Nothing to do"
            in signed_in.get(url("list", alice_home) + "?room=garbage").content.decode()
        )

    def test_hidden_tasks_are_counted_and_can_be_shown(
        self, signed_in: Client, alice_home: Home, alice: User
    ) -> None:
        services.create_task(
            alice, alice_home, title="Far ahead", due_on=TODAY + 90 * DAY, hide_days_before=7
        )
        page = signed_in.get(url("list", alice_home)).content.decode()
        assert "Far ahead" not in page
        assert "1 task is hidden" in page
        assert "Far ahead" in signed_in.get(url("list", alice_home) + "?hidden=1").content.decode()

    def test_the_list_is_paginated(self, signed_in: Client, alice_home: Home, alice: User) -> None:
        for number in range(35):
            services.create_task(
                alice, alice_home, title=f"Task {number:02d}", due_on=TODAY + number * DAY
            )
        first = signed_in.get(url("list", alice_home)).content.decode()
        assert "Page 1 of 2" in first
        assert "Task 34" not in first
        assert "Task 34" in signed_in.get(url("list", alice_home) + "?page=2").content.decode()

    def test_the_entry_of_the_navigation(
        self, signed_in: Client, alice_home: Home, alice: User
    ) -> None:
        response = signed_in.get(reverse("tasks"))
        assert response["Location"] == url("list", alice_home)
        signed_in.get(url("list", alice_home))
        home_services.create_home(alice, name="Holiday flat")
        # With two homes, the current one (the one last opened), without asking.
        assert signed_in.get(reverse("tasks"))["Location"] == url("list", alice_home)
        # The navigation entry and the "All tasks" tab.
        assert (
            signed_in.get(url("list", alice_home)).content.decode().count('aria-current="page"')
            == 2
        )

    def test_the_entry_without_a_home(self, client: Client, make_user: object) -> None:
        user = make_user("lonely@example.com")  # type: ignore[operator]
        client.force_login(user)
        assert "no home" in client.get(reverse("tasks")).content.decode()

    def test_the_sheets_of_a_room_and_an_item_link_to_their_tasks(
        self,
        signed_in: Client,
        alice_home: Home,
        owner_room: Room,
        owner_item: Item,
        owner_task: Task,
    ) -> None:
        room_page = signed_in.get(
            reverse("rooms:detail", kwargs={"home_id": alice_home.id, "room_id": owner_room.id})
        ).content.decode()
        item_page = signed_in.get(
            reverse(
                "rooms:item-detail", kwargs={"home_id": alice_home.id, "item_id": owner_item.id}
            )
        ).content.decode()
        for page in (room_page, item_page):
            assert url("detail", alice_home, task_id=owner_task.id) in page
        assert f"?room={owner_room.id}" in room_page
        assert f"?item={owner_item.id}" in item_page

    def test_a_deleted_task_leaves_the_room_sheet(
        self, signed_in: Client, alice: User, alice_home: Home, owner_room: Room, owner_task: Task
    ) -> None:
        services.delete_task(alice, owner_task)
        page = signed_in.get(
            reverse("rooms:detail", kwargs={"home_id": alice_home.id, "room_id": owner_room.id})
        ).content.decode()
        assert "No task for this yet" in page


class TestWebRolesAndIsolation:
    def test_read_only_sees_everything_and_no_button(
        self,
        client: Client,
        reader: User,
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        client.force_login(reader)
        listing = client.get(url("list", alice_home)).content.decode()
        sheet = client.get(url("detail", alice_home, task_id=owner_task.id)).content.decode()
        assert "Change the smoke detector battery" in listing
        for page in (listing, sheet):
            assert "/complete/" not in page
            assert "Add a task" not in page
            assert "Edit the task" not in page
        writes = [
            ("get", url("create", alice_home)),
            ("get", url("edit", alice_home, task_id=owner_task.id)),
            ("get", url("delete", alice_home, task_id=owner_task.id)),
            ("post", url("delete", alice_home, task_id=owner_task.id)),
            (
                "post",
                url(
                    "complete", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id
                ),
            ),
            (
                "post",
                url("skip", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id),
            ),
            (
                "post",
                url("undo", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id),
            ),
            (
                "get",
                url(
                    "postpone", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id
                ),
            ),
            (
                "get",
                url(
                    "occurrence-edit",
                    alice_home,
                    task_id=owner_task.id,
                    occurrence_id=owner_occurrence.id,
                ),
            ),
            ("get", url("attachment-add", alice_home, task_id=owner_task.id)),
        ]
        for method, address in writes:
            assert getattr(client, method)(address).status_code == 403, address
        assert Occurrence.objects.get(id=owner_occurrence.id).status == Status.PLANNED

    @pytest.mark.parametrize("who", ["bob", "site_admin"])
    def test_a_stranger_and_the_instance_administrator_get_404_everywhere(
        self,
        client: Client,
        request: pytest.FixtureRequest,
        who: str,
        alice_home: Home,
        bob_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        client.force_login(request.getfixturevalue(who))
        for home in (alice_home, bob_home):
            addresses = [
                *([url("list", home), url("create", home)] if home == alice_home else []),
                url("detail", home, task_id=owner_task.id),
                url("edit", home, task_id=owner_task.id),
                url("delete", home, task_id=owner_task.id),
                url("postpone", home, task_id=owner_task.id, occurrence_id=owner_occurrence.id),
                url(
                    "occurrence-edit",
                    home,
                    task_id=owner_task.id,
                    occurrence_id=owner_occurrence.id,
                ),
                url("attachment-add", home, task_id=owner_task.id),
            ]
            for address in addresses:
                assert client.get(address).status_code == 404, address
            for name in ("complete", "skip", "undo"):
                address = url(name, home, task_id=owner_task.id, occurrence_id=owner_occurrence.id)
                assert client.post(address).status_code == 404, address
            assert client.post(url("delete", home, task_id=owner_task.id)).status_code == 404
        assert Occurrence.objects.get(id=owner_occurrence.id).status == Status.PLANNED
        assert Task.objects.filter(id=owner_task.id).exists()

    def test_an_occurrence_is_reached_only_through_its_own_task(
        self,
        signed_in: Client,
        alice: User,
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        other = services.create_task(alice, alice_home, title="Other")
        address = url("complete", alice_home, task_id=other.id, occurrence_id=owner_occurrence.id)
        assert signed_in.post(address).status_code == 404

    def test_anonymous_visitors_are_sent_to_sign_in(
        self, client: Client, alice_home: Home, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        for address in (
            url("list", alice_home),
            url("detail", alice_home, task_id=owner_task.id),
            url("create", alice_home),
            reverse("tasks"),
        ):
            assert client.get(address).status_code == 302
            assert "/accounts/login/" in client.get(address)["Location"]
        post = url("complete", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id)
        assert client.post(post).status_code == 302
        assert Occurrence.objects.get(id=owner_occurrence.id).status == Status.PLANNED

    def test_the_views_do_not_serve_methods_they_do_not_offer(
        self, signed_in: Client, alice_home: Home, owner_task: Task, owner_occurrence: Occurrence
    ) -> None:
        address = url(
            "complete", alice_home, task_id=owner_task.id, occurrence_id=owner_occurrence.id
        )
        assert signed_in.get(address).status_code == 405


class TestExportAndDeletion:
    def test_the_export_holds_the_tasks_of_the_home_and_nothing_of_another(
        self,
        alice: User,
        bob: User,
        alice_home: Home,
        bob_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        contractor = services.create_contractor(
            alice, alice_home, name="A. Plumber", trade="plumber"
        )
        services.update_task(alice, owner_task, contractor=contractor, assignee=alice)
        done = services.complete(alice, owner_occurrence, actual_cost=Decimal("4.5"), note="AAA")
        services.create_task(bob, bob_home, title="Their secret", due_on=TODAY)
        archive, _name = exports.export_home(alice, alice_home)
        zipped = zipfile.ZipFile(archive)
        raw = archive_text(zipped)
        data = export_data(zipped)
        people = {p["id"]: p["email"] for p in data["people"]}
        [task] = data["tasks"]
        assert task["title"] == "Change the smoke detector battery"
        assert (people[task["assignee"]], task["contractor"]) == (
            "alice@example.com",
            str(contractor.id),
        )
        assert task["repeat_mode"] == "after_last"
        assert [c["name"] for c in data["contractors"]] == ["A. Plumber"]
        by_status = {o["status"]: o for o in data["occurrences"]}
        assert people[by_status["done"]["completed_by"]] == "alice@example.com"
        assert by_status["done"]["actual_cost"] == "4.50"
        assert by_status["done"]["completion_note"] == "AAA"
        assert by_status["planned"]["due_on"] == str(done.next.due_on)  # type: ignore[union-attr]
        assert "Their secret" not in raw

    def test_deleting_the_home_removes_its_tasks_and_only_them(
        self,
        client: Client,
        alice: User,
        bob: User,
        alice_home: Home,
        bob_home: Home,
        owner_task: Task,
    ) -> None:
        theirs = services.create_task(bob, bob_home, title="Theirs", due_on=TODAY)
        client.force_login(alice)
        response = client.post(
            reverse("homes:delete", kwargs={"home_id": alice_home.id}),
            {"confirmation": "Main house"},
        )
        assert response.status_code == 302
        assert not Task.all_objects.filter(home_id=alice_home.id).exists()
        assert not Occurrence.all_objects.filter(home_id=alice_home.id).exists()
        assert Task.objects.filter(id=theirs.id).exists()
        assert Occurrence.objects.filter(task=theirs).count() == 1


class TestFieldTemplate:
    def test_the_help_text_is_escaped(self, alice_home: Home) -> None:
        form = TaskForm(home=alice_home)
        form.fields["notes"].help_text = "<script>alert(1)</script> & more"
        html = render_to_string("tasks/_field.html", {"field": form["notes"]})
        assert "<script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; more" in html


class TestFrenchForm:
    def test_every_label_of_the_task_form_is_translated(self, alice_home: Home) -> None:
        from django.utils import translation

        with translation.override("fr"):
            labels = [str(field.label) for field in TaskForm(home=alice_home).fields.values()]
        assert "Pièce" in labels
        assert "Room" not in labels
        assert "Item" not in labels
