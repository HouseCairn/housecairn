"""Reminders: first one, follow-ups, quiet hours, summary, once only (docs/adr/0043).

No network: `outbound.send` is replaced by a recorder, except where the escaping of a
title is checked through the real Telegram plugin, whose `send` is faked.
"""

import datetime
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from apprise.plugins.telegram import NotifyTelegram
from django.conf import settings
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from housecairn.accounts.models import User
from housecairn.homes.models import Home, Membership, Role
from housecairn.instance import config
from housecairn.notifications import outbound, reminders, services, tasks, webpush
from housecairn.notifications.models import MAX_CHANNELS, MAX_DEVICES, Delivery, Preference
from housecairn.tasks import agenda
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Occurrence, Task

pytestmark = pytest.mark.django_db

URL = "tgram://123456789:fixture-not-a-token/987654"
UTC = datetime.UTC


@pytest.fixture(autouse=True)
def _on() -> Iterator[None]:
    with override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=True, TIME_ZONE="UTC"):
        yield


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """(title, body) of every message, one per channel, instead of Apprise."""
    box: list[tuple[str, str]] = []

    def record(sealed: str, *, title: str, body: str) -> bool:
        box.append((title, body))
        return True

    monkeypatch.setattr(reminders.outbound, "send", record)
    return box


@pytest.fixture
def due() -> datetime.date:
    return task_services.today() + datetime.timedelta(days=5)


@pytest.fixture
def make_task(alice: User, alice_home: Home, due: datetime.date) -> Callable[..., Occurrence]:
    def make(title: str = "Change the filter", **fields: Any) -> Occurrence:
        task: Task = task_services.create_task(alice, alice_home, title=title, due_on=due, **fields)
        occurrence: Occurrence = Occurrence.objects.get(task=task)
        return occurrence

    return make


def at(day: datetime.date, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(day, datetime.time(hour, minute), tzinfo=UTC)


def reachable(user: User, **prefs: Any) -> User:
    """Give the person a channel and, if any, preferences; time zone UTC by default."""
    services.add_channel(user, name="Phone", url=URL)
    Preference.objects.update_or_create(user=user, defaults={"time_zone": "UTC", **prefs})
    return user


class TestFirstReminderAndFollowUps:
    def test_the_first_reminder_goes_out_days_before_from_the_chosen_time(
        self, alice: User, make_task: Callable[..., Occurrence], due: datetime.date, outbox: Any
    ) -> None:
        reachable(alice, days_before=2)
        make_task()
        day = due - datetime.timedelta(days=2)
        assert reminders.dispatch(at(day, 7, 59)) == 0
        assert reminders.dispatch(at(day, 8, 0)) == 1
        assert reminders.dispatch(at(day, 9, 0)) == 0  # sent once
        assert len(outbox) == 1

    @pytest.mark.parametrize(("priority", "days"), [("high", 1), ("normal", 3), ("low", 7)])
    def test_follow_ups_come_more_often_for_a_higher_priority(
        self,
        alice: User,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        outbox: Any,
        priority: str,
        days: int,
    ) -> None:
        reachable(alice, days_before=0)
        make_task(priority=priority)
        assert reminders.dispatch(at(due, 8)) == 1
        later = due + datetime.timedelta(days=days)
        assert reminders.dispatch(at(later, 7, 59)) == 0
        assert reminders.dispatch(at(later, 8)) == 1
        assert len(outbox) == 2

    @pytest.mark.parametrize("close", ["complete", "skip"])
    def test_done_or_skipped_stops_the_reminders(
        self,
        alice: User,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        outbox: Any,
        close: str,
    ) -> None:
        reachable(alice, days_before=0)
        occurrence = make_task()
        assert reminders.dispatch(at(due, 8)) == 1
        getattr(task_services, close)(alice, occurrence)
        for days in (1, 3, 10):
            assert reminders.dispatch(at(due + datetime.timedelta(days=days), 9)) == 0
        assert len(outbox) == 1

    def test_postponing_stops_them_until_the_new_date(
        self, alice: User, make_task: Callable[..., Occurrence], due: datetime.date, outbox: Any
    ) -> None:
        reachable(alice, days_before=0)
        occurrence = make_task(priority="high")
        assert reminders.dispatch(at(due, 8)) == 1
        new = due + datetime.timedelta(days=10)
        task_services.postpone(alice, occurrence, new)
        for days in (1, 5, 9):
            assert reminders.dispatch(at(due + datetime.timedelta(days=days), 9)) == 0
        assert reminders.dispatch(at(new, 8)) == 1

    def test_nothing_is_sent_when_notifications_are_off(
        self, alice: User, make_task: Callable[..., Occurrence], due: datetime.date, outbox: Any
    ) -> None:
        reachable(alice, days_before=0)
        make_task()
        with override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=False):
            assert reminders.dispatch(at(due, 9)) == 0
        assert outbox == []

    def test_a_disabled_channel_receives_nothing(
        self, alice: User, make_task: Callable[..., Occurrence], due: datetime.date, outbox: Any
    ) -> None:
        reachable(alice, days_before=0)
        alice.notification_channels.update(enabled=False)
        make_task()
        assert reminders.dispatch(at(due, 9)) == 0


class TestQuietHoursAndSummary:
    def test_quiet_hours_delay_the_reminder_to_their_end(
        self, alice: User, make_task: Callable[..., Occurrence], due: datetime.date, outbox: Any
    ) -> None:
        reachable(
            alice,
            days_before=0,
            quiet_start=datetime.time(7, 0),
            quiet_end=datetime.time(9, 30),
        )
        make_task()
        assert reminders.dispatch(at(due, 8, 5)) == 0
        assert reminders.dispatch(at(due, 9, 29)) == 0
        assert reminders.dispatch(at(due, 9, 30)) == 1  # delayed, not lost
        assert len(outbox) == 1

    def test_quiet_hours_across_midnight_in_the_person_time_zone(
        self, alice: User, make_task: Callable[..., Occurrence], due: datetime.date, outbox: Any
    ) -> None:
        # 07:00 in Paris is 05:00 or 06:00 UTC: the evening reminder waits for the morning.
        reachable(
            alice,
            days_before=0,
            send_at=datetime.time(0, 0),
            quiet_start=datetime.time(22, 0),
            quiet_end=datetime.time(7, 0),
            time_zone="Europe/Paris",
        )
        make_task()
        assert reminders.dispatch(at(due, 3)) == 0
        assert reminders.dispatch(at(due, 7)) == 1

    def test_the_daily_summary_groups_the_tasks(
        self,
        alice: User,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        outbox: list[tuple[str, str]],
    ) -> None:
        reachable(alice, days_before=0, digest=True, send_at=datetime.time(18, 0))
        make_task("Change the filter")
        make_task("Test the smoke alarm")
        assert reminders.dispatch(at(due, 17, 59)) == 0
        assert reminders.dispatch(at(due, 18)) == 1
        assert reminders.dispatch(at(due, 23)) == 0
        ((title, body),) = outbox
        assert title == "HouseCairn: tasks to do"
        assert "Change the filter" in body
        assert "Test the smoke alarm" in body
        next_day = due + datetime.timedelta(days=1)
        assert reminders.dispatch(at(next_day, 17)) == 0
        assert reminders.dispatch(at(next_day, 18)) == 1

    @pytest.mark.parametrize("change", ["turn on", "move the time"])
    def test_a_summary_set_before_its_time_goes_out_once_that_day(
        self,
        alice: User,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        outbox: Any,
        monkeypatch: pytest.MonkeyPatch,
        change: str,
    ) -> None:
        """Set at 03:00 for 08:00, a task already due for a reminder: not the summary of the
        day before at once and again at 08:00, but one summary at 08:00."""
        reachable(alice, days_before=1, digest=change != "turn on", send_at=datetime.time(2, 0))
        make_task()
        monkeypatch.setattr(reminders.timezone, "now", lambda: at(due, 3))
        if change == "turn on":
            services.update_preferences(alice, digest=True, send_at=datetime.time(8, 0))
        else:
            services.update_preferences(alice, send_at=datetime.time(8, 0))
        assert reminders.dispatch(at(due, 3)) == 0
        assert reminders.dispatch(at(due, 7, 59)) == 0
        assert reminders.dispatch(at(due, 8, 5)) == 1
        assert reminders.dispatch(at(due, 20)) == 0
        assert len(outbox) == 1

    def test_saving_the_same_summary_settings_skips_nothing(
        self,
        alice: User,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        outbox: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A summary delayed past midnight by quiet hours is still sent after a save."""
        reachable(
            alice,
            days_before=1,
            digest=True,
            send_at=datetime.time(23, 0),
            quiet_start=datetime.time(22, 0),
            quiet_end=datetime.time(7, 0),
        )
        make_task()
        before = due - datetime.timedelta(days=1)
        assert reminders.dispatch(at(before, 23, 30)) == 0  # quiet
        monkeypatch.setattr(reminders.timezone, "now", lambda: at(due, 3))
        services.update_preferences(alice, days_before=1, digest=True)
        assert reminders.dispatch(at(due, 7)) == 1  # the summary of the day before

    def test_quiet_hours_delay_the_summary(
        self, alice: User, make_task: Callable[..., Occurrence], due: datetime.date, outbox: Any
    ) -> None:
        reachable(
            alice,
            days_before=0,
            digest=True,
            quiet_start=datetime.time(7, 0),
            quiet_end=datetime.time(10, 0),
        )
        make_task()
        assert reminders.dispatch(at(due, 8)) == 0
        assert reminders.dispatch(at(due, 10)) == 1


class TestOnceOnly:
    def test_two_runs_that_cross_send_once(
        self,
        alice: User,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A second run starts while the first one is sending: it finds the claim."""
        reachable(alice, days_before=0)
        make_task()
        sends: list[str] = []
        inner: list[int] = []

        def slow_send(sealed: str, *, title: str, body: str) -> bool:
            sends.append(body)
            if len(inner) == 0:
                inner.append(reminders.dispatch(at(due, 8, 1)))
            return True

        monkeypatch.setattr(reminders.outbound, "send", slow_send)
        assert reminders.dispatch(at(due, 8)) == 1
        assert inner == [0]
        assert len(sends) == 1

    def test_a_claim_already_made_elsewhere_is_not_sent(
        self,
        alice: User,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        outbox: Any,
    ) -> None:
        reachable(alice, days_before=0)
        occurrence = make_task()
        Delivery.objects.create(
            user=alice, kind="reminder", key=f"{occurrence.id}:{due.isoformat()}:0"
        )
        assert reminders.dispatch(at(due, 8)) == 0
        assert outbox == []

    @pytest.mark.parametrize("digest", [False, True])
    def test_a_long_run_leaves_the_rest_to_the_next_one(
        self,
        alice: User,
        bob: User,
        alice_home: Home,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        add_member: Callable[[Home, User, Role], Membership],
        monkeypatch: pytest.MonkeyPatch,
        digest: bool,
    ) -> None:
        """Each send takes 100 s of the clock (130 s for a summary): a run starts no message
        after RUN_SECONDS, and the next runs send the rest, each message once, none lost."""
        add_member(alice_home, bob, Role.MEMBER)
        reachable(alice, days_before=0, digest=digest)
        reachable(bob, days_before=0, digest=digest)
        for title in ("One", "Two", "Three"):
            make_task(title)
        clock = [0.0]
        sends: list[str] = []

        def slow_send(sealed: str, *, title: str, body: str) -> bool:
            sends.append(body)
            clock[0] += 130 if digest else 100
            return True

        monkeypatch.setattr(reminders, "_clock", lambda: clock[0], raising=False)
        monkeypatch.setattr(reminders.outbound, "send", slow_send)
        runs = [reminders.dispatch(at(due, 8, minute)) for minute in range(0, 25, 5)]
        # Six reminders (three tasks, two writers), two a run; or two summaries, one a run.
        assert runs == ([1, 1, 0, 0, 0] if digest else [2, 2, 2, 0, 0])
        assert len(sends) == Delivery.objects.count() == (2 if digest else 6)
        assert Delivery.objects.filter(sent=True).count() == len(sends)
        if digest:
            assert all(body.count("\n\n") == 2 for body in sends)  # three tasks in each

    def test_the_longest_run_fits_the_timeout_of_the_worker(self) -> None:
        assert reminders.RUN_SECONDS < 300
        assert reminders.LONGEST_RUN_SECONDS == (
            reminders.RUN_SECONDS
            + MAX_CHANNELS * (outbound.SEND_SECONDS + 1)
            + MAX_DEVICES * (webpush.SEND_SECONDS + 1)
        )
        assert settings.Q_CLUSTER["timeout"] > reminders.LONGEST_RUN_SECONDS


class TestRecipients:
    def test_the_person_in_charge_only(
        self,
        alice: User,
        bob: User,
        alice_home: Home,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        add_member: Callable[[Home, User, Role], Membership],
        outbox: Any,
    ) -> None:
        add_member(alice_home, bob, Role.MEMBER)
        reachable(alice, days_before=0)
        reachable(bob, days_before=0)
        make_task(assignee=bob)
        assert reminders.dispatch(at(due, 8)) == 1
        assert Delivery.objects.get().user == bob

    def test_without_anyone_in_charge_the_members_who_may_write(
        self,
        alice: User,
        bob: User,
        make_user: Callable[..., User],
        alice_home: Home,
        bob_home: Home,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        add_member: Callable[[Home, User, Role], Membership],
        outbox: Any,
    ) -> None:
        reader = make_user("reader@example.com")
        stranger = make_user("stranger@example.com")
        add_member(alice_home, bob, Role.MEMBER)
        add_member(alice_home, reader, Role.READ_ONLY)
        for person in (alice, bob, reader, stranger):
            reachable(person, days_before=0)
        make_task()
        assert reminders.dispatch(at(due, 8)) == 2
        assert set(Delivery.objects.values_list("user__email", flat=True)) == {
            "alice@example.com",
            "bob@example.com",
        }

    @pytest.mark.parametrize("case", ["reader", "no channel", "inactive"])
    def test_a_person_in_charge_who_cannot_be_reminded_hands_over_to_the_writers(
        self,
        alice: User,
        bob: User,
        make_user: Callable[..., User],
        alice_home: Home,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        add_member: Callable[[Home, User, Role], Membership],
        outbox: Any,
        case: str,
    ) -> None:
        """A reader, someone without a channel or an inactive account: the writers get it."""
        carol = make_user("carol@example.com")
        add_member(alice_home, bob, Role.MEMBER)
        add_member(alice_home, carol, Role.READ_ONLY if case == "reader" else Role.MEMBER)
        reachable(alice, days_before=0)
        reachable(bob, days_before=0)
        if case != "no channel":
            reachable(carol, days_before=0)
        if case == "inactive":
            User.objects.filter(pk=carol.pk).update(is_active=False)
        make_task()
        Task.objects.update(assignee=carol)  # a reader cannot be chosen: forced here
        assert reminders.dispatch(at(due, 8)) == 2
        assert set(Delivery.objects.values_list("user__email", flat=True)) == {
            "alice@example.com",
            "bob@example.com",
        }

    @pytest.mark.parametrize("case", ["reader in charge", "inactive in charge", "nobody"])
    def test_when_no_writer_can_be_reminded_nothing_is_sent(
        self,
        alice: User,
        make_user: Callable[..., User],
        alice_home: Home,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        add_member: Callable[[Home, User, Role], Membership],
        outbox: Any,
        case: str,
    ) -> None:
        """No writer active with a channel: no message, never to a reader alone nor to an
        inactive account. Alice, the other writer, has no channel; a reader has one."""
        carol = make_user("carol@example.com")
        reader = make_user("reader@example.com")
        add_member(alice_home, carol, Role.READ_ONLY if case == "reader in charge" else Role.MEMBER)
        add_member(alice_home, reader, Role.READ_ONLY)
        reachable(reader, days_before=0)
        if case != "nobody":
            reachable(carol, days_before=0)
        if case == "inactive in charge":
            User.objects.filter(pk=carol.pk).update(is_active=False)
        make_task()
        if case != "nobody":
            Task.objects.update(assignee=carol)  # a reader cannot be chosen: forced here
        for day in (0, 1, 3, 7):
            assert reminders.dispatch(at(due + datetime.timedelta(days=day), 9)) == 0
        assert outbox == []
        assert not Delivery.objects.exists()

    def test_an_inactive_writer_is_not_reminded(
        self,
        alice: User,
        bob: User,
        alice_home: Home,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        add_member: Callable[[Home, User, Role], Membership],
        outbox: Any,
    ) -> None:
        add_member(alice_home, bob, Role.MEMBER)
        reachable(alice, days_before=0)
        reachable(bob, days_before=0)
        User.objects.filter(pk=bob.pk).update(is_active=False)
        make_task()
        assert reminders.dispatch(at(due, 8)) == 1
        assert Delivery.objects.get().user == alice

    def test_someone_who_left_the_home_is_not_reminded(
        self,
        alice: User,
        bob: User,
        alice_home: Home,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        add_member: Callable[[Home, User, Role], Membership],
        outbox: Any,
    ) -> None:
        membership = add_member(alice_home, bob, Role.MEMBER)
        reachable(bob, days_before=0)
        make_task(assignee=bob)
        membership.delete()
        Task.objects.update(assignee=bob)  # even if a stale assignee stayed
        assert reminders.dispatch(at(due, 8)) == 0


class TestMessage:
    def test_title_home_date_and_link_only(
        self,
        alice: User,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        outbox: list[tuple[str, str]],
    ) -> None:
        reachable(alice, days_before=0)
        occurrence = make_task(notes="Secret note", procedure="Step one", labour_budget="7777.77")
        with override_settings(HOUSECAIRN_SITE_URL="https://home.example.com"):
            reminders.dispatch(at(due, 8))
        ((title, body),) = outbox
        assert title == "HouseCairn reminder"
        assert "Change the filter" in body
        assert "Main house" in body
        link = reverse(
            "tasks:detail", kwargs={"home_id": occurrence.home_id, "task_id": occurrence.task_id}
        )
        assert f"https://home.example.com{link}" in body
        assert str(occurrence.task.labour_budget) == "7777.77"
        # The cost in every way a locale writes it; none of them can be part of a UUID.
        for private in (
            "Secret note",
            "Step one",
            "7777.77",
            "7777,77",
            "7,777",
            "7 777",
            "7\xa0777",
        ):
            assert private not in body

    @pytest.mark.parametrize("separator", ["\n", "\r\n", "\r", "\x0b", "\x85", "\u2028", "\u2029"])
    def test_a_title_or_a_home_cannot_add_a_line(
        self,
        alice: User,
        alice_home: Home,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        outbox: list[tuple[str, str]],
        separator: str,
    ) -> None:
        Home.objects.filter(pk=alice_home.pk).update(
            name=f"Main house{separator}https://evil.example.com/home"
        )
        make_task(f"Pay the bill{separator}https://evil.example.com/pay\tnow")
        reachable(alice, days_before=0)
        with override_settings(HOUSECAIRN_SITE_URL="https://home.example.com"):
            reminders.dispatch(at(due, 8))
        ((_title, body),) = outbox
        lines = body.splitlines()
        assert len(lines) == 2
        assert lines[0].startswith("Pay the bill https://evil.example.com/pay now, Main house ")
        assert lines[1].startswith("https://home.example.com/")

    def test_the_language_of_the_person(
        self,
        alice: User,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        outbox: list[tuple[str, str]],
    ) -> None:
        reachable(alice, days_before=0)
        agenda.update_preferences(alice, language="fr")
        make_task()
        reminders.dispatch(at(due, 8))
        ((title, _body),) = outbox
        assert title == "Rappel HouseCairn"

    def test_a_title_cannot_change_the_markup_of_the_channel(
        self,
        alice: User,
        make_task: Callable[..., Occurrence],
        due: datetime.date,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bodies: list[str] = []

        def fake(self: Any, body: str, title: str = "", **kwargs: Any) -> bool:
            bodies.append(body)
            return True

        monkeypatch.setattr(NotifyTelegram, "send", fake)
        reachable(alice, days_before=0)
        make_task('<a href="https://evil.example.com">Pay</a> & <b>now</b>')
        assert reminders.dispatch(at(due, 8)) == 1
        (body,) = bodies
        assert "<a href" not in body
        assert "<b>now" not in body
        assert "&lt;a href=" in body
        assert "&amp;" in body


class TestSchedule:
    def test_the_worker_runs_at_the_interval_of_the_settings(self) -> None:
        from django_q.models import Schedule

        tasks.schedule()
        assert Schedule.objects.get(name=tasks.SCHEDULE).minutes == 5
        with override_settings(HOUSECAIRN_NOTIFICATIONS_INTERVAL=15):
            config.reset()
            tasks.schedule()
        assert Schedule.objects.get(name=tasks.SCHEDULE).minutes == 15
        assert Schedule.objects.filter(name=tasks.SCHEDULE).count() == 1

    def test_a_run_dispatches_and_keeps_the_schedule(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(reminders, "dispatch", lambda: 3)
        assert tasks.dispatch() == 3
        from django_q.models import Schedule

        assert Schedule.objects.filter(name=tasks.SCHEDULE).exists()

    def test_old_records_are_pruned(self, alice: User) -> None:
        reachable(alice)
        old = Delivery.objects.create(user=alice, kind="digest", key="old")
        Delivery.objects.filter(pk=old.pk).update(
            claimed_at=timezone.now() - datetime.timedelta(days=reminders.KEEP_DELIVERIES_DAYS + 1)
        )
        reminders.dispatch()
        assert not Delivery.objects.filter(pk=old.pk).exists()
