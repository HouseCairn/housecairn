"""Calendar: range, projections, views, secret feeds and their ICS document (docs/adr/0052)."""

import datetime
import hashlib
import re
from collections.abc import Callable
from typing import Any

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from icalendar import Calendar
from rest_framework.test import APIClient

from housecairn.accounts import services as account_services
from housecairn.accounts.models import User
from housecairn.apitokens import services as token_services
from housecairn.audit.models import AuditEvent
from housecairn.calendar import feeds, ics, services
from housecairn.calendar.models import Feed
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Membership, Role
from housecairn.notifications.models import Preference
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Room
from housecairn.tasks import agenda
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Occurrence, Priority, Task
from tests.htmlscan import inline_code_problems
from tests.tasks_fixtures import PDF, TODAY

_REAL_TODAY_OF = services.today_of

pytest_plugins = ("tests.tasks_fixtures",)
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root", "frozen_today")]

DAY = datetime.timedelta(days=1)
CRLF = b"\r\n"


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The calendar's today is the day the tasks tests freeze."""
    monkeypatch.setattr(services, "today_of", lambda user, now=None: TODAY)
    cache.clear()


def make_task(user: User, home: Home, title: str, days: int = 0, **fields: Any) -> Task:
    return task_services.create_task(user, home, title=title, due_on=TODAY + days * DAY, **fields)


def occurrence_of(task: Task) -> Occurrence:
    found = task_services.open_occurrence_of(task)
    assert found is not None
    return found


def get_feed(client: Client, raw: str, **headers: str) -> Any:
    return client.get(reverse("calendar:feed", kwargs={"token": raw}), **headers)


def events_of(body: bytes) -> list[Any]:
    return list(Calendar.from_ical(body).walk("VEVENT"))


@pytest.fixture
def feed(alice: User, alice_home: Home) -> tuple[Feed, str]:
    return feeds.create_feed(alice, alice_home)


@pytest.fixture
def anonymous() -> Client:
    return Client()


class TestRange:
    def test_planned_done_postponed_and_skipped_are_shown_with_their_status(
        self, alice: User, alice_home: Home
    ) -> None:
        planned = make_task(alice, alice_home, "Planned", 1)
        done = make_task(alice, alice_home, "Done", 0)
        postponed = make_task(alice, alice_home, "Postponed", 0)
        skipped = make_task(alice, alice_home, "Skipped", 2)
        task_services.complete(alice, occurrence_of(done))
        task_services.postpone(alice, occurrence_of(postponed), TODAY + 3 * DAY)
        task_services.skip(alice, occurrence_of(skipped))
        result = services.entries(alice, [alice_home], TODAY, TODAY + 7 * DAY)
        seen = {(e.title, e.status, e.day) for e in result.entries}
        assert ("Planned", "planned", TODAY + DAY) in seen
        assert ("Done", "done", TODAY) in seen
        assert ("Postponed", "postponed", TODAY + 3 * DAY) in seen
        assert ("Skipped", "skipped", TODAY + 2 * DAY) in seen
        assert planned.id in {e.task_id for e in result.entries}

    def test_a_repeating_task_is_projected_by_the_recurrence_engine_and_nothing_is_written(
        self, alice: User, alice_home: Home
    ) -> None:
        make_task(
            alice,
            alice_home,
            "Filter",
            0,
            repeat_mode="fixed",
            repeat_unit="week",
            repeat_interval=1,
        )
        before = Occurrence.objects.count()
        result = services.entries(alice, [alice_home], TODAY, TODAY + 30 * DAY)
        days = [e.day for e in result.entries]
        assert days == [TODAY + n * 7 * DAY for n in range(5)]
        assert [e.projected for e in result.entries] == [False, True, True, True, True]
        assert Occurrence.objects.count() == before
        assert not any(e.can_complete for e in result.entries if e.projected)

    def test_a_season_is_respected_by_the_projection(self, alice: User, alice_home: Home) -> None:
        make_task(
            alice,
            alice_home,
            "Gutters",
            0,
            repeat_mode="fixed",
            repeat_unit="month",
            repeat_interval=3,
            season_start_month=9,
            season_end_month=11,
        )
        result = services.entries(alice, [alice_home], TODAY, TODAY + 400 * DAY)
        assert all(e.day.month in (9, 10, 11) or e == result.entries[0] for e in result.entries)

    def test_the_number_of_queries_does_not_depend_on_the_number_of_tasks(
        self, alice: User, alice_home: Home
    ) -> None:
        def count() -> int:
            with CaptureQueriesContext(connection) as queries:
                services.entries(alice, [alice_home], TODAY, TODAY + 120 * DAY)
            return len(queries)

        make_task(alice, alice_home, "One", 1, repeat_mode="fixed", repeat_unit="week")
        few = count()
        for n in range(25):
            make_task(alice, alice_home, f"Task {n}", n, repeat_mode="fixed", repeat_unit="month")
        assert count() == few <= 3

    def test_a_cut_removes_the_farthest_dates_and_no_task_loses_a_near_one(
        self, monkeypatch: pytest.MonkeyPatch, alice: User, alice_home: Home
    ) -> None:
        monkeypatch.setattr(services, "ENTRY_LIMIT", 500)
        tasks = [
            make_task(
                alice, alice_home, f"Daily {n:02d}", 0, repeat_mode="fixed", repeat_unit="day"
            )
            for n in range(12)
        ]
        wanted = TODAY + 30 * DAY
        shown = services.entries(alice, [alice_home], TODAY, TODAY + 365 * DAY)
        assert shown.truncated
        assert len(shown.entries) == 500
        by_day = services.by_day(shown.entries)
        assert {e.task_id for e in by_day[wanted]} == {t.id for t in tasks}
        # Every day but the last kept one is complete: the cut is on the farthest dates.
        last = max(by_day)
        assert all(len(by_day[d]) == len(tasks) for d in by_day if d < last)
        # The feed (the API goes through the same function) has each task at day 30 as well.
        body = ics.render(feeds.create_feed(alice, alice_home)[0], "https://h.example.com")
        in_feed = [str(e["URL"]) for e in events_of(body) if e["DTSTART"].dt == wanted]
        assert len(in_feed) == len(tasks)

    def test_more_projected_tasks_than_the_limit_is_reported_not_silent(
        self, monkeypatch: pytest.MonkeyPatch, alice: User, alice_home: Home
    ) -> None:
        monkeypatch.setattr(services, "PROJECTED_TASKS_LIMIT", 3)
        for n in range(5):
            make_task(alice, alice_home, f"Task {n}", n, repeat_mode="fixed", repeat_unit="week")
        assert services.entries(alice, [alice_home], TODAY, TODAY + 30 * DAY).truncated
        monkeypatch.setattr(services, "PROJECTED_TASKS_LIMIT", 5)
        assert not services.entries(alice, [alice_home], TODAY, TODAY + 30 * DAY).truncated

    def test_the_range_is_bounded(self, alice: User, alice_home: Home) -> None:
        with pytest.raises(ValueError, match="range"):
            services.entries(alice, [alice_home], TODAY, TODAY + 401 * DAY)
        with pytest.raises(ValueError, match="range"):
            services.entries(alice, [alice_home], TODAY, TODAY - DAY)

    def test_filters_by_room_person_and_priority(
        self, alice: User, alice_home: Home, owner_room: Any
    ) -> None:
        make_task(alice, alice_home, "In room", 0, room=owner_room, priority=Priority.HIGH)
        make_task(alice, alice_home, "Mine", 0, assignee=alice)
        make_task(alice, alice_home, "Nobody", 0)

        def titles(**filters: Any) -> set[str]:
            found = services.entries(alice, [alice_home], TODAY, TODAY, services.Filters(**filters))
            return {e.title for e in found.entries}

        assert titles(room=owner_room.id) == {"In room"}
        assert titles(person=alice.id) == {"Mine"}
        assert titles(person=services.NOBODY) == {"In room", "Nobody"}
        assert titles(priority="high") == {"In room"}

    def test_a_person_is_named_never_by_their_email(self, alice: User, alice_home: Home) -> None:
        make_task(alice, alice_home, "Mine", 0, assignee=alice)
        (entry,) = services.entries(alice, [alice_home], TODAY, TODAY).entries
        assert "@" not in entry.person
        alice.first_name, alice.last_name = "Alex", "Martin"
        alice.save()
        (entry,) = services.entries(alice, [alice_home], TODAY, TODAY).entries
        assert entry.person == "Alex Martin"
        alice.display_name = "Lex"
        alice.save()
        (entry,) = services.entries(alice, [alice_home], TODAY, TODAY).entries
        assert entry.person == "Lex"

    def test_two_people_of_the_same_name_are_told_apart_in_the_filter(
        self, alice: User, alice_home: Home, member: User
    ) -> None:
        alice.display_name = member.display_name = "Sam"
        alice.save()
        member.save()
        names = [name for _id, name in services.people_of([alice_home])]
        assert sorted(names) == sorted([f"Sam ({alice.email})", f"Sam ({member.email})"])


class TestFeedSecret:
    def test_the_secret_is_long_random_and_stored_hashed(
        self, alice: User, alice_home: Home
    ) -> None:
        created, raw = feeds.create_feed(alice, alice_home)
        _other, second = feeds.create_feed(alice)
        assert len(raw) >= 43
        assert raw != second
        assert created.digest == hashlib.sha256(raw.encode()).hexdigest()
        stored = [str(v) for row in Feed.objects.values() for v in row.values()]
        assert raw not in " ".join(stored)

    def test_one_feed_per_person_and_home_and_one_combined(
        self, alice: User, alice_home: Home, bob: User
    ) -> None:
        feeds.create_feed(alice, alice_home)
        feeds.create_feed(alice)
        with pytest.raises(ValidationError):
            feeds.create_feed(alice, alice_home)
        with pytest.raises(ValidationError):
            feeds.create_feed(alice)
        assert feeds.create_feed(bob)

    def test_nobody_creates_a_feed_for_a_home_they_do_not_belong_to(
        self, bob: User, alice_home: Home
    ) -> None:
        with pytest.raises(PermissionDenied):
            feeds.create_feed(bob, alice_home)

    def test_a_home_that_requires_a_provider_has_no_feed(
        self, alice: User, alice_home: Home
    ) -> None:
        alice_home.required_provider = "company"
        alice_home.save()
        with pytest.raises(PermissionDenied):
            feeds.create_feed(alice, alice_home)

    def test_regenerating_replaces_the_secret_and_revoking_deletes_the_feed(
        self, anonymous: Client, alice: User, feed: tuple[Feed, str]
    ) -> None:
        created, old = feed
        assert get_feed(anonymous, old).status_code == 200
        _same, new = feeds.regenerate_feed(alice, created)
        assert get_feed(anonymous, old).status_code == 404
        assert get_feed(anonymous, new).status_code == 200
        created.refresh_from_db()
        assert created.regenerated_at is not None
        feeds.revoke_feed(alice, created)
        assert get_feed(anonymous, new).status_code == 404
        assert not Feed.objects.exists()

    def test_nobody_changes_the_feed_of_someone_else(
        self, bob: User, feed: tuple[Feed, str]
    ) -> None:
        with pytest.raises(PermissionDenied):
            feeds.regenerate_feed(bob, feed[0])
        with pytest.raises(PermissionDenied):
            feeds.revoke_feed(bob, feed[0])
        assert not feeds.feeds_of(bob)

    def test_unknown_revoked_and_malformed_secrets_get_the_same_answer(
        self, anonymous: Client, alice: User, feed: tuple[Feed, str]
    ) -> None:
        created, raw = feed
        feeds.revoke_feed(alice, created)

        def shape(token: str) -> tuple[int, bytes, dict[str, str]]:
            response = get_feed(anonymous, token)
            headers = {k: v for k, v in response.items() if k.lower() != "date"}
            return response.status_code, response.content, headers

        revoked = shape(raw)
        assert revoked[0] == 404
        assert shape("x" * 43) == revoked
        assert shape("short") == revoked
        assert shape(raw[:-1] + ("A" if raw[-1] != "A" else "B")) == revoked

    def test_leaving_the_home_revokes_its_feed_but_not_the_combined_one(
        self, anonymous: Client, alice_home: Home, member: User
    ) -> None:
        own, own_raw = feeds.create_feed(member, alice_home)
        combined, combined_raw = feeds.create_feed(member)
        home_services.leave_home(member, alice_home)
        assert get_feed(anonymous, own_raw).status_code == 404
        assert not Feed.objects.filter(pk=own.pk).exists()
        body = get_feed(anonymous, combined_raw).content
        assert Feed.objects.filter(pk=combined.pk).exists()
        assert events_of(body) == []
        event = AuditEvent.objects.get(action="calendar.feed_revoked")
        assert event.detail == {"kind": "left"}
        assert event.actor == member
        assert event.target_user == member

    def test_removal_by_an_owner_revokes_it_too(
        self, alice: User, alice_home: Home, member: User
    ) -> None:
        feeds.create_feed(member, alice_home)
        membership = home_services.members(alice_home).get(user=member)
        home_services.remove_member(alice, membership)
        assert not Feed.objects.exists()
        # The audit names the owner who removed the person, not the person.
        event = AuditEvent.objects.get(action="calendar.feed_revoked")
        assert event.actor == alice
        assert event.target_user == member
        assert event.detail == {"kind": "left"}

    def test_a_feed_does_not_come_back_when_the_person_joins_the_home_again(
        self, anonymous: Client, alice: User, alice_home: Home, member: User
    ) -> None:
        _feed, raw = feeds.create_feed(member, alice_home)
        home_services.remove_member(alice, home_services.members(alice_home).get(user=member))
        Membership.objects.create(home=alice_home, user=member, role=Role.MEMBER)
        assert get_feed(anonymous, raw).status_code == 404

    def test_no_audit_entry_when_there_was_no_feed(
        self, alice: User, alice_home: Home, member: User
    ) -> None:
        home_services.remove_member(alice, home_services.members(alice_home).get(user=member))
        assert not AuditEvent.objects.filter(action="calendar.feed_revoked").exists()

    def test_an_inactive_account_has_no_feed(
        self, anonymous: Client, alice: User, feed: tuple[Feed, str]
    ) -> None:
        alice.is_active = False
        alice.save()
        assert get_feed(anonymous, feed[1]).status_code == 404
        with pytest.raises(PermissionDenied):
            feeds.create_feed(alice)

    def test_the_combined_feed_of_an_inactive_account_stops_too(
        self, anonymous: Client, alice: User, alice_home: Home
    ) -> None:
        # Only the account's state closes it: the person is still in the home.
        combined, raw = feeds.create_feed(alice)
        assert combined.home_id is None
        make_task(alice, alice_home, "Filter", 0)
        assert get_feed(anonymous, raw).status_code == 200
        alice.is_active = False
        alice.save()
        unknown = get_feed(anonymous, "x" * 43)
        stopped = get_feed(anonymous, raw)
        assert stopped.status_code == 404
        assert stopped.content == unknown.content
        assert b"Filter" not in stopped.content

    def test_a_home_that_later_requires_a_provider_stops_its_feed(
        self, anonymous: Client, alice_home: Home, feed: tuple[Feed, str]
    ) -> None:
        alice_home.required_provider = "company"
        alice_home.save()
        assert get_feed(anonymous, feed[1]).status_code == 404

    def test_the_audit_log_records_the_three_actions_without_the_secret(
        self, alice: User, alice_home: Home
    ) -> None:
        created, raw = feeds.create_feed(alice, alice_home)
        feeds.regenerate_feed(alice, created)
        feeds.revoke_feed(alice, created)
        actions = list(AuditEvent.objects.order_by("created_at").values_list("action", flat=True))
        assert [a for a in actions if a.startswith("calendar.")] == [
            "calendar.feed_created",
            "calendar.feed_regenerated",
            "calendar.feed_revoked",
        ]
        dump = " ".join(str(e.detail) for e in AuditEvent.objects.all())
        assert raw not in dump
        assert created.digest not in dump

    def test_the_feed_is_not_an_api_token(
        self, alice: User, alice_home: Home, feed: tuple[Feed, str]
    ) -> None:
        client = APIClient()
        response = client.get(reverse("api-v1:home-list"), HTTP_AUTHORIZATION=f"Bearer {feed[1]}")
        assert response.status_code in (401, 403)
        web = Client()
        assert (
            web.get(reverse("calendar:week"), HTTP_AUTHORIZATION=f"Bearer {feed[1]}").status_code
            == 302
        )


class TestIcs:
    def test_the_document_is_valid_and_read_back_by_the_library(
        self, anonymous: Client, alice: User, alice_home: Home, owner_room: Any, feed: Any
    ) -> None:
        make_task(alice, alice_home, "Smoke detector", 2, room=owner_room)
        make_task(alice, alice_home, "Filter", 0, repeat_mode="fixed", repeat_unit="week")
        response = get_feed(anonymous, feed[1])
        assert response.status_code == 200
        assert response["Content-Type"] == "text/calendar; charset=utf-8"
        body = response.content
        assert body.startswith(b"BEGIN:VCALENDAR\r\n")
        assert body.endswith(b"END:VCALENDAR\r\n")
        calendar = Calendar.from_ical(body)
        assert str(calendar["VERSION"]) == "2.0"
        assert str(calendar["X-WR-TIMEZONE"]) == "UTC"
        found = events_of(body)
        assert len(found) > 3  # the projections of the weekly task
        titles = {str(e["SUMMARY"]) for e in found}
        assert {"Smoke detector", "Filter"} <= titles
        # Dates without a time are VALUE=DATE.
        assert b"DTSTART;VALUE=DATE:" in body
        assert b"DTSTART:" not in body
        first = next(e for e in found if str(e["SUMMARY"]) == "Smoke detector")
        assert first["DTSTART"].dt == TODAY + 2 * DAY
        assert first["DTEND"].dt == TODAY + 3 * DAY
        assert "Living room" in str(first["DESCRIPTION"])
        assert "Main house" in str(first["DESCRIPTION"])
        assert str(first["URL"]).endswith(f"/h/{alice_home.id}/tasks/{first_task_id(first)}/")

    def test_uids_are_stable_and_unique(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any
    ) -> None:
        make_task(alice, alice_home, "Filter", 0, repeat_mode="fixed", repeat_unit="week")
        first = [str(e["UID"]) for e in events_of(get_feed(anonymous, feed[1]).content)]
        second = [str(e["UID"]) for e in events_of(get_feed(anonymous, feed[1]).content)]
        assert first == second
        assert len(set(first)) == len(first)

    @staticmethod
    def uids(client: Client, raw: str) -> dict[datetime.date, list[str]]:
        found: dict[datetime.date, list[str]] = {}
        for event in events_of(get_feed(client, raw).content):
            found.setdefault(event["DTSTART"].dt, []).append(str(event["UID"]))
        return found

    def test_a_forecast_keeps_its_uid_once_the_occurrence_exists(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any
    ) -> None:
        task = make_task(alice, alice_home, "Filter", 0, repeat_mode="fixed", repeat_unit="week")
        before = self.uids(anonymous, feed[1])
        assert before[TODAY + 7 * DAY] == [f"{task.id}-{TODAY + 7 * DAY}@housecairn"]
        task_services.complete(alice, occurrence_of(task))
        after = self.uids(anonymous, feed[1])
        # Each date keeps the identity it had, the closed occurrence included, and the next
        # occurrence (now recorded in the database) is the same event as its forecast.
        assert after == before
        assert occurrence_of(task).due_on == TODAY + 7 * DAY

    def test_a_postponed_occurrence_keeps_the_uid_of_its_first_date(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any
    ) -> None:
        task = make_task(alice, alice_home, "Filter", 3)
        (uid,) = self.uids(anonymous, feed[1])[TODAY + 3 * DAY]
        task_services.postpone(alice, occurrence_of(task), TODAY + 10 * DAY)
        after = self.uids(anonymous, feed[1])
        assert after == {TODAY + 10 * DAY: [uid]}
        task_services.complete(alice, occurrence_of(task))
        assert self.uids(anonymous, feed[1]) == {TODAY + 10 * DAY: [uid]}

    def test_a_forecast_that_misses_its_date_is_replaced_by_the_real_one_without_a_duplicate(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any
    ) -> None:
        # Repeating a week after the last time: closing two days early moves the next date, so
        # the forecast event of the old date goes (its identity cannot follow a date that no
        # longer exists) and the new date has its own; no date holds two events.
        task = make_task(
            alice, alice_home, "Filter", 0, repeat_mode="after_last", repeat_unit="week"
        )
        before = self.uids(anonymous, feed[1])
        task_services.complete(alice, occurrence_of(task), on=TODAY - 2 * DAY)
        after = self.uids(anonymous, feed[1])
        assert occurrence_of(task).due_on == TODAY + 5 * DAY
        assert TODAY + 7 * DAY not in after
        assert all(len(uids) == 1 for uids in after.values())
        assert before[TODAY + 7 * DAY] != after[TODAY + 5 * DAY]

    def test_two_occurrences_of_a_task_never_share_a_uid(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any
    ) -> None:
        task = make_task(alice, alice_home, "Filter", 0, repeat_mode="fixed", repeat_unit="week")
        # A closed occurrence postponed from the date of a forecast: the identity is taken once.
        Occurrence.objects.create(
            home=alice_home,
            task=task,
            due_on=TODAY + 2 * DAY,
            postponed_from=TODAY + 7 * DAY,
            status="done",
            completed_on=TODAY + 2 * DAY,
        )
        uids = [str(e["UID"]) for e in events_of(get_feed(anonymous, feed[1]).content)]
        assert len(uids) == len(set(uids))
        assert f"{task.id}-{TODAY + 7 * DAY}@housecairn" in uids

    @pytest.mark.parametrize(
        "title",
        [
            "Boiler\r\nBEGIN:VEVENT\r\nSUMMARY:injected",
            "Boiler\nEND:VCALENDAR\nBEGIN:VCALENDAR",
            "Semi; colon, comma \\ backslash",
            "Été " + "é" * 90,
        ],
    )
    def test_a_title_cannot_inject_lines_and_lines_stay_within_75_octets(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any, title: str
    ) -> None:
        task = make_task(alice, alice_home, "placeholder", 0)
        Task.objects.filter(pk=task.pk).update(title=title)
        body = get_feed(anonymous, feed[1]).content
        lines = body.split(CRLF)
        assert all(len(line) <= 75 for line in lines)
        assert lines.count(b"BEGIN:VCALENDAR") == 1
        assert lines.count(b"END:VCALENDAR") == 1
        assert lines.count(b"BEGIN:VEVENT") == 1 == len(events_of(body))
        assert not any(line.startswith(b"SUMMARY:injected") for line in lines)
        summary = str(events_of(body)[0]["SUMMARY"])
        assert "\n" not in summary
        assert "\r" not in summary

    @pytest.mark.parametrize(
        "hostile",
        [
            "Hall\r\nBEGIN:VEVENT\r\nSUMMARY:injected",
            "Hall\nEND:VCALENDAR\nBEGIN:VCALENDAR",
            "Semi; colon, comma \\ backslash",
            "Bidi \u202e\u2066 override \u2069",
            "Zero\u200bwidth \x1b[31m escape \x7f del",
        ],
    )
    def test_a_room_and_a_home_name_cannot_inject_lines_or_hide_text_either(
        self,
        anonymous: Client,
        alice: User,
        alice_home: Home,
        owner_room: Any,
        feed: Any,
        hostile: str,
    ) -> None:
        # Written under the validation of the forms: the feed must hold even then.
        make_task(alice, alice_home, "Boiler", 0, room=owner_room)
        Room.objects.filter(pk=owner_room.pk).update(name=hostile)
        Home.objects.filter(pk=alice_home.pk).update(name=hostile)
        body = get_feed(anonymous, feed[1]).content
        lines = body.split(CRLF)
        assert all(len(line) <= 75 for line in lines)
        assert lines.count(b"BEGIN:VCALENDAR") == lines.count(b"END:VCALENDAR") == 1
        assert lines.count(b"BEGIN:VEVENT") == lines.count(b"END:VEVENT") == 1
        assert not any(line.startswith(b"SUMMARY:injected") for line in lines)
        text = body.decode()
        for char in "\x00\x1b\x7f\u200b\u202e\u2066\u2069":
            assert char not in text
        (event,) = events_of(body)
        assert str(event["LOCATION"]).count("\n") == 0
        assert "injected" not in str(event["SUMMARY"])
        assert Calendar.from_ical(body)["X-WR-CALNAME"].count("\n") == 0

    def test_a_title_with_control_and_bidi_characters_is_cleaned(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any
    ) -> None:
        task = make_task(alice, alice_home, "placeholder", 0)
        Task.objects.filter(pk=task.pk).update(title="A\u202eB\u2066C\u200bD\x1bE\x7fF")
        text = get_feed(anonymous, feed[1]).content.decode()
        assert "ABCDEF" in text
        for char in "\x1b\x7f\u200b\u202e\u2066":
            assert char not in text

    def test_a_nul_byte_in_a_title_a_room_or_a_home_name_never_reaches_the_document(
        self, alice_home: Home
    ) -> None:
        # PostgreSQL refuses NUL in a text field, so it cannot be stored there; the document is
        # still built from whatever a row holds (SQLite stores it), so test the builder itself.
        home = Home(id=alice_home.id, name="Ho\x00me")
        entry = services.Entry(
            day=TODAY,
            status="planned",
            projected=False,
            title=services.clean_title("Boi\x00ler"),
            task_id=alice_home.id,
            occurrence_id=None,
            home=home,
            room_id=None,
            room="Li\x00ving",
            assignee_id=None,
            person="",
            priority="normal",
            can_write=False,
            updated_at=datetime.datetime(2026, 6, 15, tzinfo=datetime.UTC),
        )
        raw = ics._event(entry, "https://h.example.com", ics.uid_of(entry)).to_ical()
        assert b"\x00" not in raw
        assert b"Boiler" in raw
        assert b"Living" in raw
        assert b"Home" in raw

    def test_nothing_private_leaves_in_the_feed(
        self, anonymous: Client, alice: User, alice_home: Home, owner_room: Any, feed: Any
    ) -> None:
        task = make_task(
            alice,
            alice_home,
            "Boiler",
            0,
            room=owner_room,
            notes="NOTE-SECRET",
            procedure="PROCEDURE-SECRET",
            labour_budget="123.45",
            assignee=alice,
        )
        task_services.add_attachment(alice, task, upload=_pdf("ATTACHMENT-SECRET.pdf"))
        closed = task_services.complete(alice, occurrence_of(task)).occurrence
        task_services.update_occurrence(
            alice, closed, actual_cost="987.65", completion_note="DONE-SECRET"
        )
        text = get_feed(anonymous, feed[1]).content.decode()
        for secret in (
            "NOTE-SECRET",
            "PROCEDURE-SECRET",
            "123.45",
            "987.65",
            "DONE-SECRET",
            "ATTACHMENT-SECRET",
            alice.email,
            "ATTENDEE",
            "ORGANIZER",
            "ATTACH",
        ):
            assert secret not in text
        assert "Boiler" in text

    def test_headers_etag_and_conditional_requests(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any
    ) -> None:
        make_task(alice, alice_home, "Boiler", 0)
        first = get_feed(anonymous, feed[1])
        assert "private" in first["Cache-Control"]
        assert "max-age=900" in first["Cache-Control"]
        assert first["Referrer-Policy"] == "no-referrer"
        assert first["X-Robots-Tag"].startswith("noindex")
        assert "Set-Cookie" not in first
        etag = first["ETag"]
        assert get_feed(anonymous, feed[1], HTTP_IF_NONE_MATCH=etag).status_code == 304
        assert get_feed(anonymous, feed[1], HTTP_IF_NONE_MATCH='"other"').status_code == 200
        make_task(alice, alice_home, "Another", 1)
        assert get_feed(anonymous, feed[1], HTTP_IF_NONE_MATCH=etag).status_code == 200
        assert get_feed(anonymous, feed[1]).headers["ETag"] != etag

    def test_only_get_and_head_are_accepted(self, anonymous: Client, feed: Any) -> None:
        url = reverse("calendar:feed", kwargs={"token": feed[1]})
        assert anonymous.head(url).status_code == 200
        assert anonymous.post(url).status_code == 405

    @override_settings(ACCOUNT_RATE_LIMITS={"housecairn_calendar_feed": "3/m/ip"})
    def test_the_address_is_rate_limited(self, anonymous: Client, feed: Any) -> None:
        cache.clear()
        codes = [get_feed(anonymous, "guess").status_code for _ in range(5)]
        assert codes == [404, 404, 404, 429, 429]
        assert get_feed(anonymous, feed[1]).status_code == 429

    def test_the_document_follows_the_time_zone_and_language_of_the_person(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any
    ) -> None:
        Preference.objects.create(user=alice, time_zone="Pacific/Auckland")
        agenda.update_preferences(alice, language="fr")
        make_task(alice, alice_home, "Boiler", 0)
        body = get_feed(anonymous, feed[1]).content
        assert str(Calendar.from_ical(body)["X-WR-TIMEZONE"]) == "Pacific/Auckland"
        assert "Statut" in str(events_of(body)[0]["DESCRIPTION"])

    @pytest.mark.parametrize(
        ("zone", "instant", "expected"),
        [
            ("Europe/Paris", (2026, 3, 28, 23, 30), (2026, 3, 29)),  # 00:30 CET
            ("Europe/Paris", (2026, 3, 29, 0, 59), (2026, 3, 29)),  # 01:59 CET, before the gap
            ("Europe/Paris", (2026, 3, 29, 1, 0), (2026, 3, 29)),  # 03:00 CEST, after it
            ("Europe/Paris", (2026, 10, 25, 22, 30), (2026, 10, 25)),  # 23:30 CET, after the fall
            ("Europe/Paris", (2026, 10, 25, 23, 0), (2026, 10, 26)),  # 00:00 CET
            ("America/New_York", (2026, 3, 8, 6, 59), (2026, 3, 8)),  # 01:59 EST
            ("America/New_York", (2026, 3, 8, 7, 0), (2026, 3, 8)),  # 03:00 EDT
            ("Pacific/Auckland", (2026, 6, 15, 12, 30), (2026, 6, 16)),
        ],
    )
    def test_today_is_the_day_of_the_person_in_each_zone_around_midnight(
        self,
        monkeypatch: pytest.MonkeyPatch,
        alice: User,
        zone: str,
        instant: tuple[int, ...],
        expected: tuple[int, ...],
    ) -> None:
        monkeypatch.undo()
        Preference.objects.create(user=alice, time_zone=zone)
        now = datetime.datetime(*instant, tzinfo=datetime.UTC)  # type: ignore[arg-type, misc]
        assert services.today_of(alice, now) == datetime.date(*expected)  # type: ignore[arg-type, misc]

    def test_an_all_day_event_stays_on_its_date_when_the_day_changes_in_the_persons_zone(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any
    ) -> None:
        Preference.objects.create(user=alice, time_zone="Europe/Paris")
        task_services.create_task(
            alice, alice_home, title="Switch the heating", due_on=datetime.date(2026, 6, 20)
        )
        before = ics.render(feed[0], "https://h.example.com", now=_utc(2026, 6, 14, 22, 30))
        after = ics.render(feed[0], "https://h.example.com", now=_utc(2026, 6, 14, 23, 30))
        for body in (before, after):
            (event,) = events_of(body)
            assert event["DTSTART"].dt == datetime.date(2026, 6, 20)
            assert event["DTEND"].dt == datetime.date(2026, 6, 21)


class TestFeedCache:
    """A document and its ETag are kept for a few minutes per feed, and never stale."""

    @pytest.fixture
    def renders(self, monkeypatch: pytest.MonkeyPatch) -> list[int]:
        calls: list[int] = []
        real = ics.render

        def counting(*args: Any, **kwargs: Any) -> bytes:
            calls.append(1)
            return real(*args, **kwargs)

        monkeypatch.setattr(ics, "render", counting)
        return calls

    def test_a_second_request_is_not_rendered_again_and_a_304_needs_no_rendering(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any, renders: list[int]
    ) -> None:
        make_task(alice, alice_home, "Boiler", 0, repeat_mode="fixed", repeat_unit="week")
        first = get_feed(anonymous, feed[1])
        second = get_feed(anonymous, feed[1])
        assert len(renders) == 1
        assert first.content == second.content
        assert first["ETag"] == second["ETag"]
        again = get_feed(anonymous, feed[1], HTTP_IF_NONE_MATCH=first["ETag"])
        assert again.status_code == 304
        assert len(renders) == 1

    def test_the_hit_costs_a_fixed_number_of_queries_whatever_the_number_of_tasks(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any
    ) -> None:
        def hit() -> int:
            get_feed(anonymous, feed[1])
            with CaptureQueriesContext(connection) as queries:
                get_feed(anonymous, feed[1])
            return len(queries)

        make_task(alice, alice_home, "One", 0, repeat_mode="fixed", repeat_unit="week")
        few = hit()
        for n in range(25):
            make_task(alice, alice_home, f"Task {n}", n, repeat_mode="fixed", repeat_unit="month")
        assert hit() == few <= 12

    @pytest.mark.parametrize(
        "change",
        ["title", "done", "postpone", "delete", "new", "room", "home"],
    )
    def test_a_change_of_the_home_shows_at_once(
        self,
        anonymous: Client,
        alice: User,
        alice_home: Home,
        owner_room: Any,
        feed: Any,
        renders: list[int],
        change: str,
    ) -> None:
        task = make_task(alice, alice_home, "Boiler", 3, room=owner_room)
        before = get_feed(anonymous, feed[1])
        if change == "title":
            task_services.update_task(alice, task, title="Heat pump")
        elif change == "done":
            task_services.complete(alice, occurrence_of(task))
        elif change == "postpone":
            task_services.postpone(alice, occurrence_of(task), TODAY + 9 * DAY)
        elif change == "delete":
            task_services.delete_task(alice, task)
        elif change == "new":
            make_task(alice, alice_home, "Gutters", 1)
        elif change == "room":
            room_services.update_room(alice, owner_room, name="Cellar")
        else:
            home_services.update_home(alice, alice_home, name="Cottage")
        after = get_feed(anonymous, feed[1])
        assert len(renders) == 2
        assert after.content != before.content
        assert after["ETag"] != before["ETag"]

    def test_a_revoked_feed_is_never_served_from_the_cache(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any
    ) -> None:
        make_task(alice, alice_home, "Boiler", 0)
        assert get_feed(anonymous, feed[1]).status_code == 200
        feeds.revoke_feed(alice, feed[0])
        assert get_feed(anonymous, feed[1]).status_code == 404

    def test_a_regenerated_address_replaces_the_old_one_at_once(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any
    ) -> None:
        make_task(alice, alice_home, "Boiler", 0)
        assert get_feed(anonymous, feed[1]).status_code == 200
        _feed, new = feeds.regenerate_feed(alice, feed[0])
        assert get_feed(anonymous, feed[1]).status_code == 404
        assert get_feed(anonymous, new).status_code == 200

    def test_two_feeds_never_share_a_document(
        self, anonymous: Client, alice: User, alice_home: Home, bob: User, bob_home: Home, feed: Any
    ) -> None:
        make_task(alice, alice_home, "Alice only", 0)
        make_task(bob, bob_home, "Bob only", 0)
        _bobs, raw = feeds.create_feed(bob, bob_home)
        assert b"Alice only" in get_feed(anonymous, feed[1]).content
        mine = get_feed(anonymous, raw).content
        assert b"Bob only" in mine
        assert b"Alice only" not in mine

    def test_the_document_is_kept_five_minutes_at_most(
        self, monkeypatch: pytest.MonkeyPatch, anonymous: Client, alice: User, alice_home: Home
    ) -> None:
        timeouts: list[int] = []
        real = ics.cache.set

        def record(key: str, value: Any, timeout: int | None = None) -> None:
            if key.startswith("calendar:feed:"):
                timeouts.append(timeout or 0)
            real(key, value, timeout)

        monkeypatch.setattr(ics.cache, "set", record)
        _feed, raw = feeds.create_feed(alice, alice_home)
        get_feed(anonymous, raw)
        assert timeouts == [300]

    def test_another_day_is_another_document(
        self,
        monkeypatch: pytest.MonkeyPatch,
        alice: User,
        alice_home: Home,
        feed: Any,
        renders: list[int],
    ) -> None:
        monkeypatch.setattr(services, "today_of", lambda user, now=None: now.date())  # type: ignore[union-attr]
        make_task(alice, alice_home, "Boiler", 0)
        ics.cached_render(feed[0], "https://h.example.com", now=_utc(2026, 6, 15, 10))
        ics.cached_render(feed[0], "https://h.example.com", now=_utc(2026, 6, 15, 11))
        assert len(renders) == 1
        ics.cached_render(feed[0], "https://h.example.com", now=_utc(2026, 6, 16, 10))
        assert len(renders) == 2


class TestChangeOfClock:
    """Europe/Paris moves its clocks on 2026-03-29 (02:00 CET becomes 03:00 CEST)."""

    @pytest.fixture(autouse=True)
    def _real_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "today_of", _REAL_TODAY_OF)

    @staticmethod
    def days_in(body: bytes) -> list[datetime.date]:
        return sorted(e["DTSTART"].dt for e in events_of(body))

    def make(self, alice: User, alice_home: Home, *days: datetime.date) -> None:
        Preference.objects.create(user=alice, time_zone="Europe/Paris")
        for n, day in enumerate(days):
            task = task_services.create_task(
                alice, alice_home, title=f"Task {n}", due_on=TODAY + DAY
            )
            Occurrence.objects.filter(task=task).update(due_on=day)

    def test_today_is_one_day_before_and_after_the_jump_of_the_clock(self, alice: User) -> None:
        Preference.objects.create(user=alice, time_zone="Europe/Paris")
        before = services.today_of(alice, _utc(2026, 3, 29, 0, 59))  # 01:59 CET
        after = services.today_of(alice, _utc(2026, 3, 29, 1, 0))  # 03:00 CEST
        assert before == after == datetime.date(2026, 3, 29)
        # The 23-hour day: its last minute is still the 29th, the next one is the 30th.
        assert services.today_of(alice, _utc(2026, 3, 29, 21, 59)) == datetime.date(2026, 3, 29)
        assert services.today_of(alice, _utc(2026, 3, 29, 22, 0)) == datetime.date(2026, 3, 30)

    def test_events_around_the_jump_keep_their_dates_and_one_day_length(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any
    ) -> None:
        days = [datetime.date(2026, 3, d) for d in (28, 29, 30)]
        self.make(alice, alice_home, *days)
        for now in (_utc(2026, 3, 29, 0, 59), _utc(2026, 3, 29, 1, 0), _utc(2026, 3, 29, 21, 59)):
            body = ics.render(feed[0], "https://h.example.com", now=now)
            assert self.days_in(body) == days
            for event in events_of(body):
                assert event["DTEND"].dt - event["DTSTART"].dt == DAY
                assert type(event["DTSTART"].dt) is datetime.date

    def test_the_window_edge_is_a_date_not_a_number_of_hours(
        self, alice: User, alice_home: Home, feed: Any
    ) -> None:
        # Today is 2026-04-28 (CEST): 30 days back is 03-29, the day of the jump, and the
        # window keeps it whole; the day before is out. With hours it would slip by one.
        edge, outside = datetime.date(2026, 3, 29), datetime.date(2026, 3, 28)
        self.make(alice, alice_home, edge, outside)
        body = ics.render(feed[0], "https://h.example.com", now=_utc(2026, 4, 28, 10))
        assert self.days_in(body) == [edge]
        # And across the jump itself (today = 03-29, window back to 02-27, forward a year).
        body = ics.render(feed[0], "https://h.example.com", now=_utc(2026, 3, 29, 12))
        assert self.days_in(body) == [outside, edge]


def first_task_id(event: Any) -> str:
    match = re.search(r"/tasks/([0-9a-f-]{36})/$", str(event["URL"]))
    assert match
    return match.group(1)


def _utc(*parts: int) -> datetime.datetime:
    return datetime.datetime(*parts, tzinfo=datetime.UTC)  # type: ignore[arg-type, misc]


def _pdf(name: str) -> Any:
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


@pytest.fixture
def signed_in(alice: User) -> Client:
    client = Client()
    client.force_login(alice)
    return client


class TestScreens:
    def test_the_three_views_show_the_entries_with_their_status_in_words(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        make_task(alice, alice_home, "Planned task", 1)
        done = make_task(alice, alice_home, "Done task", 0)
        task_services.complete(alice, occurrence_of(done))
        skipped = make_task(alice, alice_home, "Skipped task", 2)
        task_services.skip(alice, occurrence_of(skipped))
        postponed = make_task(alice, alice_home, "Postponed task", 0)
        task_services.postpone(alice, occurrence_of(postponed), TODAY + 3 * DAY)
        for view in ("week", "month"):
            page = signed_in.get(reverse(f"calendar:{view}")).content.decode()
            for text in ("Planned task", "Done task", "Skipped task", "Postponed task"):
                assert text in page
            for word in ("Planned", "Done", "Skipped", "Postponed"):
                assert f">{word}" in page
        year = signed_in.get(reverse("calendar:year")).content.decode()
        assert "calendar-year" in year
        assert 'scope="row"' in year

    def test_navigation_is_labelled_and_has_prev_next_today_links(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        page = signed_in.get(reverse("calendar:month"), {"date": "2026-06-15"}).content.decode()
        assert 'rel="prev"' in page
        assert 'rel="next"' in page
        assert 'data-calendar="today"' in page
        assert "date=2026-05-01" in page
        assert "date=2026-07-01" in page
        assert 'aria-current="page"' in page
        assert 'aria-live="polite"' in page
        assert '<caption class="visually-hidden">June 2026</caption>' in page
        week = signed_in.get(reverse("calendar:week"), {"date": "2026-06-15"}).content.decode()
        assert "date=2026-06-08" in week
        assert "date=2026-06-22" in week

    def test_the_pages_carry_no_inline_script_or_style(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        make_task(alice, alice_home, "Boiler", 0)
        for name in ("week", "month", "year", "feeds"):
            html = signed_in.get(reverse(f"calendar:{name}")).content.decode()
            assert inline_code_problems(html) == []
        assert "js/calendar.js" in signed_in.get(reverse("calendar:week")).content.decode()

    def test_filters_narrow_the_view(
        self, signed_in: Client, alice: User, alice_home: Home, owner_room: Any
    ) -> None:
        make_task(alice, alice_home, "In room", 0, room=owner_room, priority=Priority.HIGH)
        make_task(alice, alice_home, "Elsewhere", 0)
        everything = signed_in.get(reverse("calendar:week")).content.decode()
        assert "In room" in everything
        assert "Elsewhere" in everything
        by_room = signed_in.get(reverse("calendar:week"), {"room": str(owner_room.id)})
        assert "Elsewhere" not in by_room.content.decode()
        by_priority = signed_in.get(reverse("calendar:week"), {"priority": "high"})
        assert "Elsewhere" not in by_priority.content.decode()
        by_nobody = signed_in.get(reverse("calendar:week"), {"person": "none"})
        assert "Elsewhere" in by_nobody.content.decode()
        # An invalid filter is ignored, not an error.
        assert (
            signed_in.get(reverse("calendar:week"), {"room": "x", "date": "nope"}).status_code
            == 200
        )

    def test_several_homes_are_combined_before_a_choice_and_one_can_be_chosen(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        flat = home_services.create_home(alice, name="Holiday flat")
        make_task(alice, alice_home, "In the house", 0)
        make_task(alice, flat, "In the flat", 0)
        combined = signed_in.get(reverse("calendar:week")).content.decode()
        assert "In the house" in combined
        assert "In the flat" in combined
        # The home is chosen in the selector of the app bar, not in a filter.
        assert 'id="filter-home"' not in combined
        assert 'name="home" value="all"' in combined
        only = signed_in.get(reverse("calendar:week"), {"home": str(flat.id)}).content.decode()
        assert "In the flat" in only
        assert "In the house" not in only

    def test_done_in_one_gesture_from_the_calendar_then_undo(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home, "Boiler", 0)
        occurrence = occurrence_of(task)
        page = signed_in.get(reverse("calendar:week")).content.decode()
        assert (
            reverse(
                "tasks:complete",
                kwargs={
                    "home_id": alice_home.id,
                    "task_id": task.id,
                    "occurrence_id": occurrence.id,
                },
            )
            in page
        )
        done = signed_in.post(
            reverse(
                "tasks:complete",
                kwargs={
                    "home_id": alice_home.id,
                    "task_id": task.id,
                    "occurrence_id": occurrence.id,
                },
            ),
            {"next": reverse("calendar:week")},
        )
        assert done.status_code == 302
        assert done["Location"].startswith(reverse("calendar:week"))
        back = signed_in.get(done["Location"]).content.decode()
        assert "Marked done." in back
        assert "Undo" in back
        undo = signed_in.post(
            reverse(
                "tasks:undo",
                kwargs={
                    "home_id": alice_home.id,
                    "task_id": task.id,
                    "occurrence_id": occurrence.id,
                },
            ),
            {"next": reverse("calendar:week")},
        )
        assert undo.status_code == 302
        occurrence.refresh_from_db()
        assert occurrence.status == "planned"

    def test_a_reader_reads_the_calendar_but_cannot_mark_done(
        self, alice: User, alice_home: Home, reader: User
    ) -> None:
        task = make_task(alice, alice_home, "Boiler", 0)
        client = Client()
        client.force_login(reader)
        page = client.get(reverse("calendar:week")).content.decode()
        assert "Boiler" in page
        assert "/complete/" not in page
        response = client.post(
            reverse(
                "tasks:complete",
                kwargs={
                    "home_id": alice_home.id,
                    "task_id": task.id,
                    "occurrence_id": occurrence_of(task).id,
                },
            )
        )
        assert response.status_code == 403
        assert occurrence_of(task).status == "planned"

    def test_other_homes_and_non_members_see_nothing(
        self,
        alice: User,
        alice_home: Home,
        bob: User,
        bob_home: Home,
        make_user: Callable[..., User],
    ) -> None:
        make_task(alice, alice_home, "Alice only", 0)
        admin = make_user("admin@example.com", is_staff=True, is_superuser=True)
        for person in (bob, admin):
            client = Client()
            client.force_login(person)
            assert "Alice only" not in client.get(reverse("calendar:week")).content.decode()
            assert (
                client.get(reverse("calendar:week"), {"home": str(alice_home.id)}).status_code
                == 404
            )

    def test_anonymous_people_are_sent_to_sign_in(self, anonymous: Client) -> None:
        for name in ("week", "month", "year", "feeds"):
            assert anonymous.get(reverse(f"calendar:{name}")).status_code == 302

    def test_the_feeds_page_shows_the_address_once(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        listing = signed_in.get(reverse("calendar:feeds")).content.decode()
        assert "Main house" in listing
        assert "No feed" in listing
        created = signed_in.post(reverse("calendar:feeds"), {"home": str(alice_home.id)})
        html = created.content.decode()
        match = re.search(r'value="(http://testserver/calendar/feed/([\w-]+)\.ics)"', html)
        assert match
        assert "no-store" in created["Cache-Control"]
        assert get_feed(Client(), match.group(2)).status_code == 200
        again = signed_in.get(reverse("calendar:feeds")).content.decode()
        assert match.group(2) not in again
        assert "Active since" in again
        feed = Feed.objects.get(user=alice)
        regenerated = signed_in.post(reverse("calendar:regenerate", kwargs={"feed_id": feed.id}))
        new = re.search(r"/calendar/feed/([\w-]+)\.ics", regenerated.content.decode())
        assert new
        assert new.group(1) != match.group(2)
        assert get_feed(Client(), match.group(2)).status_code == 404
        revoked = signed_in.post(reverse("calendar:revoke", kwargs={"feed_id": feed.id}))
        assert revoked.status_code == 302
        assert not Feed.objects.exists()

    def test_someone_elses_feed_is_a_404(
        self, bob: User, alice_home: Home, alice: User, feed: Any
    ) -> None:
        client = Client()
        client.force_login(bob)
        for name in ("regenerate", "revoke"):
            url = reverse(f"calendar:{name}", kwargs={"feed_id": feed[0].id})
            assert client.post(url).status_code == 404
        assert (
            client.post(reverse("calendar:feeds"), {"home": str(alice_home.id)}).status_code == 404
        )


class TestApi:
    def url(self) -> str:
        return reverse("api-v1:calendar")

    def test_the_range_matches_the_screen(
        self, alice: User, alice_home: Home, signed_in: Client
    ) -> None:
        make_task(alice, alice_home, "Boiler", 1, repeat_mode="fixed", repeat_unit="week")
        client = APIClient()
        client.force_authenticate(alice)
        start, end = TODAY, TODAY + 20 * DAY
        data = client.get(self.url(), {"from": str(start), "to": str(end)}).json()
        assert data["truncated"] is False
        assert data["start"] == str(start)
        web = services.entries(alice, [alice_home], start, end).entries
        assert [(r["date"], r["title"], r["projected"]) for r in data["results"]] == [
            (str(e.day), e.title, e.projected) for e in web
        ]
        assert all("@" not in r["person"] for r in data["results"])
        week = signed_in.get(reverse("calendar:week"), {"date": str(start)}).content.decode()
        assert all(r["title"] in week for r in data["results"] if r["date"] < str(start + 7 * DAY))

    def test_the_api_has_each_task_on_day_30_and_says_it_was_cut(
        self, monkeypatch: pytest.MonkeyPatch, alice: User, alice_home: Home
    ) -> None:
        monkeypatch.setattr(services, "ENTRY_LIMIT", 500)
        for n in range(12):
            make_task(
                alice, alice_home, f"Daily {n:02d}", 0, repeat_mode="fixed", repeat_unit="day"
            )
        client = APIClient()
        client.force_authenticate(alice)
        start, end = TODAY.isoformat(), (TODAY + 365 * DAY).isoformat()
        data = client.get(self.url(), {"from": start, "to": end}).json()
        assert data["truncated"] is True
        day_30 = (TODAY + 30 * DAY).isoformat()
        assert sum(1 for r in data["results"] if r["date"] == day_30) == 12

    def test_bad_ranges_and_filters_are_400(self, alice: User, alice_home: Home) -> None:
        client = APIClient()
        client.force_authenticate(alice)
        good = {"from": str(TODAY), "to": str(TODAY)}
        assert client.get(self.url()).status_code == 400
        assert client.get(self.url(), {**good, "to": "x"}).status_code == 400
        assert client.get(self.url(), {**good, "to": str(TODAY + 401 * DAY)}).status_code == 400
        assert client.get(self.url(), {**good, "to": str(TODAY - DAY)}).status_code == 400
        assert client.get(self.url(), {**good, "room": "x"}).status_code == 400
        assert client.get(self.url(), {**good, "priority": "x"}).status_code == 400
        assert client.get(self.url(), {**good, "home": str(bob_uuid())}).status_code == 404
        assert client.get(self.url(), good).status_code == 200

    def test_a_token_reads_only_the_homes_it_is_bound_to(
        self, alice: User, alice_home: Home, bob: User, bob_home: Home
    ) -> None:
        make_task(alice, alice_home, "House task", 0)
        make_task(alice, alice_home, "Flat task", 0)
        flat = home_services.create_home(alice, name="Flat")
        make_task(alice, flat, "Other home task", 0)
        _token, raw = token_services.issue_token(
            alice, name="ci", scopes=["read"], homes=[alice_home]
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        data = client.get(self.url(), {"from": str(TODAY), "to": str(TODAY)}).json()
        assert {r["title"] for r in data["results"]} == {"House task", "Flat task"}
        _bad, wrong = token_services.issue_token(bob, name="ci", scopes=["read"], homes=[bob_home])
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {wrong}")
        assert (
            client.get(self.url(), {"from": str(TODAY), "to": str(TODAY)}).json()["results"] == []
        )

    def test_a_reader_cannot_complete(self, alice: User, alice_home: Home, reader: User) -> None:
        make_task(alice, alice_home, "Boiler", 0)
        client = APIClient()
        client.force_authenticate(reader)
        (row,) = client.get(self.url(), {"from": str(TODAY), "to": str(TODAY)}).json()["results"]
        assert row["can_complete"] is False
        client.force_authenticate(alice)
        (row,) = client.get(self.url(), {"from": str(TODAY), "to": str(TODAY)}).json()["results"]
        assert row["can_complete"] is True

    def test_a_token_can_complete_only_with_the_write_scope(
        self, alice: User, alice_home: Home
    ) -> None:
        make_task(alice, alice_home, "Boiler", 0)
        client = APIClient()
        seen = {}
        for scopes in (["read"], ["read", "write"]):
            _token, raw = token_services.issue_token(
                alice, name="ci", scopes=scopes, homes=[alice_home]
            )
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
            (row,) = client.get(self.url(), {"from": str(TODAY), "to": str(TODAY)}).json()[
                "results"
            ]
            seen[tuple(scopes)] = row["can_complete"]
        assert seen == {("read",): False, ("read", "write"): True}

    def test_feeds_by_the_api_for_a_session_and_never_for_a_token(
        self, alice: User, alice_home: Home
    ) -> None:
        client = APIClient()
        client.force_authenticate(alice)
        created = client.post(
            reverse("api-v1:calendar-feed-list"), {"home": str(alice_home.id)}, format="json"
        )
        assert created.status_code == 201
        body = created.json()
        assert body["url"].startswith("http://testserver/calendar/feed/")
        assert get_feed(Client(), body["url"].rsplit("/", 1)[1][:-4]).status_code == 200
        listing = client.get(reverse("api-v1:calendar-feed-list")).json()
        assert [f["id"] for f in listing] == [body["id"]]
        assert "url" not in listing[0]
        assert (
            client.post(
                reverse("api-v1:calendar-feed-list"), {"home": str(alice_home.id)}, format="json"
            ).status_code
            == 400
        )
        detail = reverse("api-v1:calendar-feed-detail", kwargs={"feed_id": body["id"]})
        regen = reverse("api-v1:calendar-feed-regenerate", kwargs={"feed_id": body["id"]})
        assert client.post(regen).json()["url"] != body["url"]
        for scope in (["read"], ["read", "write"]):
            _token, raw = token_services.issue_token(
                alice, name="t", scopes=scope, homes=[alice_home]
            )
            token_client = APIClient()
            token_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
            assert token_client.get(reverse("api-v1:calendar-feed-list")).status_code == 403
            assert token_client.post(reverse("api-v1:calendar-feed-list"), {}).status_code == 403
            assert token_client.get(detail).status_code == 403
            assert token_client.post(regen).status_code == 403
            assert token_client.delete(detail).status_code == 403
        assert client.delete(detail).status_code == 204
        assert not Feed.objects.exists()

    def test_the_feed_of_someone_else_is_a_404_by_the_api(
        self, bob: User, feed: Any, alice_home: Home
    ) -> None:
        client = APIClient()
        client.force_authenticate(bob)
        detail = reverse("api-v1:calendar-feed-detail", kwargs={"feed_id": feed[0].id})
        assert client.get(detail).status_code == 404
        assert client.delete(detail).status_code == 404
        created = client.post(
            reverse("api-v1:calendar-feed-list"), {"home": str(alice_home.id)}, format="json"
        )
        assert created.status_code == 404


def bob_uuid() -> Any:
    import uuid

    return uuid.uuid4()


class TestFeedIsolation:
    def test_a_feed_carries_only_the_homes_of_its_person_and_its_own_scope(
        self, anonymous: Client, alice: User, alice_home: Home, bob: User, bob_home: Home
    ) -> None:
        make_task(alice, alice_home, "Alice task", 0)
        make_task(bob, bob_home, "Bob task", 0)
        _bob_feed, bob_raw = feeds.create_feed(bob)
        text = get_feed(anonymous, bob_raw).content.decode()
        assert "Bob task" in text
        assert "Alice task" not in text
        flat = home_services.create_home(alice, name="Flat")
        make_task(alice, flat, "Flat task", 0)
        _own, own_raw = feeds.create_feed(alice, alice_home)
        _all, all_raw = feeds.create_feed(alice)
        own = get_feed(anonymous, own_raw).content.decode()
        assert "Alice task" in own
        assert "Flat task" not in own
        both = get_feed(anonymous, all_raw).content.decode()
        assert "Alice task" in both
        assert "Flat task" in both

    def test_a_reader_has_a_feed_of_the_home(
        self, anonymous: Client, alice: User, alice_home: Home, reader: User
    ) -> None:
        make_task(alice, alice_home, "Boiler", 0)
        _feed, raw = feeds.create_feed(reader, alice_home)
        assert "Boiler" in get_feed(anonymous, raw).content.decode()

    def test_a_demoted_owner_keeps_reading_but_a_removed_member_does_not(
        self, anonymous: Client, alice: User, alice_home: Home, member: User
    ) -> None:
        make_task(alice, alice_home, "Boiler", 0)
        _feed, raw = feeds.create_feed(member, alice_home)
        membership = home_services.members(alice_home).get(user=member)
        home_services.change_role(alice, membership, Role.READ_ONLY)
        assert "Boiler" in get_feed(anonymous, raw).content.decode()


class TestDataLifecycle:
    def test_the_export_of_a_home_lists_its_feeds_without_the_secret(
        self, alice: User, alice_home: Home, feed: Any
    ) -> None:
        feeds.create_feed(alice)  # combined: belongs to the person, not to the home
        from tests.data_support import archive_bytes, export_data

        document = export_data(archive_bytes(alice_home))
        assert [f["id"] for f in document["calendar_feeds"]] == [str(feed[0].id)]
        dump = str(document)
        assert feed[1] not in dump
        assert feed[0].digest not in dump
        assert set(document["calendar_feeds"][0]) == {
            "id",
            "created_at",
            "updated_at",
            "user",
            "regenerated_at",
        }

    def test_erasing_a_home_or_an_account_removes_the_feeds(
        self, anonymous: Client, alice: User, alice_home: Home, feed: Any, bob: User
    ) -> None:
        _combined, combined_raw = feeds.create_feed(alice)
        home_services.erase_home(alice_home)
        assert get_feed(anonymous, feed[1]).status_code == 404
        assert get_feed(anonymous, combined_raw).status_code == 200
        account_services.delete_account(alice)
        assert get_feed(anonymous, combined_raw).status_code == 404
        assert not Feed.objects.exists()
        assert bob
