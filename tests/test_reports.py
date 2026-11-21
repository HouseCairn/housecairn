"""Reports: who reports and who deals with it, photos, tasks, notices (docs/adr/0047)."""

import datetime
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from housecairn.accounts import names
from housecairn.accounts.models import User
from housecairn.audit.models import AuditEvent
from housecairn.homes.models import Home, Role
from housecairn.notifications import alerts, outbound
from housecairn.notifications import services as notification_services
from housecairn.notifications.models import Channel
from housecairn.reports import services
from housecairn.reports.models import MAX_OPEN_PER_REPORTER, Report, ReportStatus
from housecairn.rooms import conf
from housecairn.rooms.models import Item, Room
from housecairn.stock.models import Article
from housecairn.tasks.models import Task
from tests.rooms_support import image_bytes, noise_image, upload

pytest_plugins = ("tests.rooms_fixtures", "tests.stock_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root")]

URL = "tgram://123456:N0t-A-Real-B0t-Token-Anywhere/987654"


def url(name: str, home: Home, **kwargs: object) -> str:
    return reverse(f"reports:{name}", kwargs={"home_id": home.id, **kwargs})


@pytest.fixture
def reported(reader: User, alice_home: Home) -> Report:
    return services.create_report(reader, alice_home, title="The tap drips")


class TestReporting:
    def test_only_what_is_wrong_is_required(self, alice_home: Home, reader: User) -> None:
        report = services.create_report(reader, alice_home, title="  The tap\n drips  ")
        assert (report.title, report.status, report.place) == ("The tap drips", "open", "")
        assert report.reporter == reader
        assert report.task is None
        assert not report.has_photo

    def test_the_place_is_a_room_an_item_or_a_stock_item_written_down_once(
        self, alice_home: Home, reader: User, owner_room: Room, owner_item: Item, article: Article
    ) -> None:
        in_room = services.create_report(reader, alice_home, title="a", room=owner_room)
        on_item = services.create_report(reader, alice_home, title="b", item=owner_item)
        in_stock = services.create_report(reader, alice_home, title="c", article=article)
        assert [r.place for r in (in_room, on_item, in_stock)] == [
            "Living room",
            "Living room · Boiler",
            "Stock · Boiler filter",
        ]
        owner_room.name = "Lounge"
        owner_room.save()
        in_room.refresh_from_db()
        assert in_room.place == "Living room"
        assert in_room.where.name == "Lounge"

    def test_one_place_at_most_and_only_of_this_home(
        self,
        alice_home: Home,
        reader: User,
        owner_room: Room,
        owner_item: Item,
        foreign_item: Item,
        foreign_article: Article,
    ) -> None:
        for fields in (
            {"room": owner_room, "item": owner_item},
            {"item": foreign_item},
            {"room": foreign_item.room},
            {"article": foreign_article},
        ):
            with pytest.raises(ValidationError):
                services.create_report(reader, alice_home, title="x", **fields)
        assert Report.objects.count() == 0

    def test_the_lists_of_places_offered_hold_this_home_only(
        self,
        alice_home: Home,
        owner_room: Room,
        owner_item: Item,
        foreign_item: Item,
        foreign_article: Article,
    ) -> None:
        offered = services.places_for_forms(alice_home)
        assert set(offered["room"]) == {owner_room}
        assert foreign_item not in offered["item"]
        assert owner_item in offered["item"]
        assert foreign_article not in offered["article"]

    @pytest.mark.parametrize("title", ["", "   ", "\x07\u200b", "x" * 201])
    def test_what_is_wrong_must_say_something_and_fit(
        self, alice_home: Home, reader: User, title: str
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            services.create_report(reader, alice_home, title=title)
        assert "title" in caught.value.message_dict

    def test_control_characters_never_reach_the_title(self, alice_home: Home, reader: User) -> None:
        report = services.create_report(reader, alice_home, title="Leak\u202e under\x00 sink")
        assert report.title == "Leak under sink"

    def test_every_member_reports_a_reader_included_and_no_one_else(
        self, alice_home: Home, alice: User, member: User, reader: User, bob: User
    ) -> None:
        for person in (alice, member, reader):
            assert services.create_report(person, alice_home, title="x").reporter == person
        with pytest.raises(PermissionDenied):
            services.create_report(bob, alice_home, title="x")
        assert Report.objects.count() == 3

    def test_a_reader_has_a_limited_number_of_open_reports(
        self, alice: User, alice_home: Home, reader: User, member: User
    ) -> None:
        for i in range(MAX_OPEN_PER_REPORTER):
            services.create_report(reader, alice_home, title=f"n{i}")
        with pytest.raises(ValidationError):
            services.create_report(reader, alice_home, title="one more")
        assert Report.objects.filter(reporter=reader).count() == MAX_OPEN_PER_REPORTER
        services.create_report(member, alice_home, title="another person is not limited")
        report = Report.objects.filter(reporter=reader).first()
        assert report is not None
        services.close_report(alice, report, resolution="resolved")
        services.create_report(reader, alice_home, title="room again")

    def test_the_owner_and_a_member_who_writes_are_not_limited(
        self, alice: User, alice_home: Home, member: User
    ) -> None:
        for person in (alice, member):
            for i in range(MAX_OPEN_PER_REPORTER + 3):
                services.create_report(person, alice_home, title=f"n{i}")
            assert Report.objects.filter(reporter=person).count() == MAX_OPEN_PER_REPORTER + 3

    def test_a_report_made_into_a_task_frees_a_place(
        self, alice: User, alice_home: Home, reader: User
    ) -> None:
        for i in range(MAX_OPEN_PER_REPORTER):
            services.create_report(reader, alice_home, title=f"n{i}")
        with pytest.raises(ValidationError):
            services.create_report(reader, alice_home, title="full")
        first = Report.objects.filter(reporter=reader).order_by("created_at").first()
        assert first is not None
        services.make_task(alice, first)
        assert Report.objects.filter(reporter=reader, status=ReportStatus.TASK).count() == 1
        services.create_report(reader, alice_home, title="one place freed")
        with pytest.raises(ValidationError):
            services.create_report(reader, alice_home, title="full again")

    def test_a_closed_report_frees_a_place_whatever_the_closing(
        self, alice: User, alice_home: Home, reader: User
    ) -> None:
        for i in range(MAX_OPEN_PER_REPORTER):
            services.create_report(reader, alice_home, title=f"n{i}")
        mine = list(Report.objects.filter(reporter=reader)[:2])
        services.close_report(alice, mine[0], resolution="resolved")
        services.close_report(alice, mine[1], resolution="no_action")
        services.create_report(reader, alice_home, title="a")
        services.create_report(reader, alice_home, title="b")
        with pytest.raises(ValidationError):
            services.create_report(reader, alice_home, title="c")

    def test_the_form_refuses_a_reader_past_the_limit_and_lets_the_owner_through(
        self, client: Any, alice: User, alice_home: Home, reader: User
    ) -> None:
        for i in range(MAX_OPEN_PER_REPORTER):
            services.create_report(reader, alice_home, title=f"n{i}")
            services.create_report(alice, alice_home, title=f"n{i}")
        client.force_login(reader)
        refused = client.post(url("create", alice_home), {"title": "one more"})
        assert refused.status_code == 200
        assert "open reports" in refused.content.decode()
        assert Report.objects.filter(reporter=reader).count() == MAX_OPEN_PER_REPORTER
        client.force_login(alice)
        allowed = client.post(url("create", alice_home), {"title": "one more"})
        assert allowed.status_code == 302
        assert Report.objects.filter(reporter=alice).count() == MAX_OPEN_PER_REPORTER + 1

    def test_creation_is_recorded_with_the_title_only(self, alice_home: Home, reader: User) -> None:
        services.create_report(reader, alice_home, title="Mould in the corner", room=None)
        (event,) = AuditEvent.objects.filter(action="report.created")
        assert event.detail == {"title": "Mould in the corner"}
        assert event.actor == reader
        assert event.home_id == alice_home.id

    def test_a_reader_changes_nothing_else_by_reporting(
        self, alice_home: Home, reader: User, owner_room: Room
    ) -> None:
        before = (Room.objects.count(), Item.objects.count(), Task.objects.count())
        services.create_report(reader, alice_home, title="x", room=owner_room)
        assert (Room.objects.count(), Item.objects.count(), Task.objects.count()) == before


class TestPhoto:
    def test_a_photo_is_checked_stripped_and_stored_with_a_thumbnail(
        self, alice_home: Home, reader: User, media_root: Path
    ) -> None:
        data = image_bytes("JPEG", (400, 300), gps=True, comment=True)
        report = services.create_report(
            reader, alice_home, title="Crack", photo=upload(data, "IMG_0001 with-secret.jpg")
        )
        stored = Path(report.photo.path).read_bytes()
        assert report.has_photo
        assert report.photo_content_type == "image/jpeg"
        assert b"Test camera" not in stored
        assert b"secret comment" not in stored
        assert not Image.open(report.photo.path).getexif()
        assert Image.open(report.thumbnail.path).size[0] <= conf.THUMBNAIL_SIDE
        assert report.photo.name.startswith(f"{alice_home.id}/{report.id}")
        assert "IMG_0001" not in report.photo.name
        assert Path(report.photo.path).is_relative_to(media_root / "reports" / str(alice_home.id))

    @pytest.mark.parametrize(
        "content",
        [
            b"%PDF-1.4 not an image",
            b"GIF89a....",
            b"<svg xmlns='http://www.w3.org/2000/svg'/>",
            b"",
        ],
    )
    def test_something_that_is_not_a_photo_is_refused_whatever_its_name(
        self, alice_home: Home, reader: User, content: bytes
    ) -> None:
        with pytest.raises(ValidationError):
            services.create_report(
                reader, alice_home, title="x", photo=upload(content, "photo.jpg", "image/jpeg")
            )
        assert Report.objects.count() == 0

    def test_a_photo_over_the_limit_is_refused(self, alice_home: Home, reader: User) -> None:
        data = noise_image("PNG")
        assert len(data) > conf.MIN_PHOTO_MAX_BYTES
        with (
            override_settings(HOUSECAIRN_PHOTO_MAX_BYTES=len(data) - 1),
            pytest.raises(ValidationError, match="limit"),
        ):
            services.create_report(reader, alice_home, title="x", photo=upload(data, "a.png"))
        assert Report.objects.count() == 0

    def test_the_quota_of_the_home_applies(
        self, alice_home: Home, reader: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from housecairn.homes import quotas

        monkeypatch.setattr(quotas, "limits", lambda: quotas.Limits(bytes=10, files=1))
        with pytest.raises(ValidationError) as caught:
            services.create_report(
                reader, alice_home, title="x", photo=upload(image_bytes("JPEG", (400, 300)))
            )
        assert caught.value.code == "quota_bytes"
        assert Report.objects.count() == 0

    def test_a_failed_save_leaves_no_file_behind(
        self, alice_home: Home, reader: User, media_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("disk")

        monkeypatch.setattr(Report, "save", boom)
        with pytest.raises(RuntimeError):
            services.create_report(reader, alice_home, title="x", photo=upload(image_bytes()))
        assert not [p for p in (media_root / "reports").rglob("*") if p.is_file()]


class TestDealingWithIt:
    def test_a_writer_makes_it_a_task_with_no_date_and_keeps_the_link(
        self, alice: User, alice_home: Home, reader: User, owner_room: Room, owner_item: Item
    ) -> None:
        report = services.create_report(reader, alice_home, title="Boiler leaks", item=owner_item)
        task = services.make_task(alice, report)
        report.refresh_from_db()
        assert (report.status, report.task_id) == (ReportStatus.TASK, task.id)
        assert (task.title, task.item, task.room) == ("Boiler leaks", owner_item, owner_room)
        occurrence = task.occurrences.get()
        assert occurrence.due_on is None

    def test_a_report_about_a_stock_item_becomes_a_task_without_a_place(
        self, alice: User, alice_home: Home, reader: User, article: Article
    ) -> None:
        report = services.create_report(reader, alice_home, title="Out of filters", article=article)
        task = services.make_task(alice, report)
        assert (task.room, task.item) == (None, None)

    def test_a_reader_cannot_make_it_a_task_close_it_or_reopen_it(
        self, alice: User, alice_home: Home, reader: User, reported: Report
    ) -> None:
        with pytest.raises(PermissionDenied):
            services.make_task(reader, reported)
        with pytest.raises(PermissionDenied):
            services.close_report(reader, reported, resolution="resolved")
        services.close_report(alice, reported, resolution="resolved")
        with pytest.raises(PermissionDenied):
            services.reopen_report(reader, reported)
        assert Task.objects.count() == 0
        reported.refresh_from_db()
        assert reported.status == ReportStatus.RESOLVED

    def test_the_check_of_reports_itself_refuses_a_reader_before_any_task(
        self, reader: User, reported: Report, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Without this check, only `tasks.services` would refuse: it is tested alone here.
        def never(*args: object, **kwargs: object) -> None:
            raise AssertionError("the task service was reached")

        monkeypatch.setattr(services.task_services, "create_task", never)
        with pytest.raises(PermissionDenied):
            services.make_task(reader, reported)

    def test_it_is_made_a_task_once(self, alice: User, reported: Report) -> None:
        services.make_task(alice, reported)
        with pytest.raises(ValidationError):
            services.make_task(alice, reported)
        assert Task.objects.count() == 1

    def test_a_task_that_cannot_be_linked_is_not_left_behind(
        self, alice: User, reported: Report
    ) -> None:
        Report.objects.filter(pk=reported.pk).update(status=ReportStatus.RESOLVED)
        reported.status = ReportStatus.OPEN  # a stale copy, as a second request would hold
        with pytest.raises(ValidationError):
            services.make_task(alice, reported)
        assert Task.objects.count() == 0

    @pytest.mark.parametrize("resolution", ["resolved", "no_action"])
    def test_closing_records_who_and_when(
        self, alice: User, reported: Report, resolution: str
    ) -> None:
        closed = services.close_report(alice, reported, resolution=resolution)
        assert closed.status == resolution
        assert closed.closed_by == alice
        assert closed.closed_at
        with pytest.raises(ValidationError):
            services.close_report(alice, closed, resolution="resolved")

    def test_closing_needs_a_closed_status(self, alice: User, reported: Report) -> None:
        for bad in ("open", "task", "", "done"):
            with pytest.raises(ValidationError):
                services.close_report(alice, reported, resolution=bad)
        reported.refresh_from_db()
        assert reported.status == ReportStatus.OPEN

    def test_a_task_can_be_closed_with_its_report(self, alice: User, reported: Report) -> None:
        services.make_task(alice, reported)
        assert services.close_report(alice, reported, resolution="resolved").status == "resolved"

    def test_reopening_goes_back_to_the_task_if_there_was_one(
        self, alice: User, alice_home: Home, reader: User
    ) -> None:
        with_task = services.create_report(reader, alice_home, title="a")
        without = services.create_report(reader, alice_home, title="b")
        services.make_task(alice, with_task)
        for report in (with_task, without):
            services.close_report(alice, report, resolution="no_action")
        assert services.reopen_report(alice, with_task).status == ReportStatus.TASK
        reopened = services.reopen_report(alice, without)
        assert (reopened.status, reopened.closed_at, reopened.closed_by) == ("open", None, None)
        with pytest.raises(ValidationError):
            services.reopen_report(alice, without)  # it is not closed

    def test_reopening_after_the_task_was_deleted_goes_back_to_open(
        self, alice: User, reported: Report
    ) -> None:
        from housecairn.tasks import services as task_services

        task = services.make_task(alice, reported)
        services.close_report(alice, reported, resolution="resolved")
        task_services.delete_task(alice, task)
        assert services.reopen_report(alice, reported).status == ReportStatus.OPEN

    def test_the_audit_log_holds_the_creation_the_conversion_and_the_closing_by_title(
        self, alice: User, alice_home: Home, reader: User
    ) -> None:
        one = services.create_report(reader, alice_home, title="Draught")
        two = services.create_report(reader, alice_home, title="Squeaky door")
        services.make_task(alice, one)
        services.close_report(alice, two, resolution="no_action")
        services.reopen_report(alice, two)
        events = AuditEvent.objects.filter(action__startswith="report.").order_by("created_at")
        assert [(e.action, e.detail) for e in events] == [
            ("report.created", {"title": "Draught"}),
            ("report.created", {"title": "Squeaky door"}),
            ("report.converted", {"title": "Draught"}),
            ("report.closed", {"title": "Squeaky door", "kind": "no_action"}),
        ]


class TestWhoSeesWhat:
    def test_a_writer_sees_all_and_a_reader_only_their_own(
        self, alice: User, alice_home: Home, reader: User, member: User, bob: User
    ) -> None:
        mine = services.create_report(reader, alice_home, title="mine")
        other = services.create_report(member, alice_home, title="theirs")
        assert set(services.visible_reports(alice, alice_home)) == {mine, other}
        assert set(services.visible_reports(member, alice_home)) == {mine, other}
        assert set(services.visible_reports(reader, alice_home)) == {mine}
        assert set(services.visible_reports(bob, alice_home)) == set()

    def test_the_reporter_is_shown_by_name_never_by_address(
        self, make_user: Callable[..., User]
    ) -> None:
        nameless = make_user("someone@example.com")
        named = make_user("named@example.com", first_name="Sam", last_name="Lee")
        assert services.reporter_name(nameless) == "someone"
        assert services.reporter_name(named) == "Sam Lee"
        assert services.reporter_name(None) == "a former member"
        for user in (nameless, named):
            assert "@" not in services.reporter_name(user)

    def test_the_name_is_the_one_of_the_helper_of_the_accounts_filler_characters_included(
        self, make_user: Callable[..., User]
    ) -> None:
        faked = make_user("faked@example.com", display_name="Alice\u3164")
        assert services.reporter_name(faked) == names.person_name(faked) == "Alice"

    def test_two_people_of_one_name_are_told_apart_in_a_list(
        self, alice_home: Home, make_user: Callable[..., User], add_member: Callable[..., object]
    ) -> None:
        first = make_user("first@example.com", display_name="Alice")
        second = make_user("second@example.com", display_name="Alice\u3164")
        other = make_user("other@example.com", display_name="Bea")
        for person in (first, second, other):
            add_member(alice_home, person, Role.READ_ONLY)
        reports = [
            services.create_report(person, alice_home, title="x")
            for person in (first, second, other)
        ]
        shown = services.reporter_labels(
            Report.objects.select_related("reporter").filter(pk__in=[r.pk for r in reports])
        )
        assert shown[reports[0].id] == "Alice (first@example.com)"
        assert shown[reports[1].id] == "Alice (second@example.com)"
        assert shown[reports[2].id] == "Bea"

    def test_a_deleted_account_is_a_former_member_in_a_list(
        self, alice_home: Home, reader: User
    ) -> None:
        report = services.create_report(reader, alice_home, title="x")
        User.objects.filter(pk=reader.pk).delete()
        report.refresh_from_db()
        assert services.reporter_labels([report]) == {report.id: "a former member"}

    def test_the_count_that_waits_is_the_open_reports_only(
        self, alice: User, alice_home: Home, reader: User
    ) -> None:
        for title in ("a", "b", "c"):
            services.create_report(reader, alice_home, title=title)
        first, second, _third = Report.objects.order_by("created_at")
        services.make_task(alice, first)
        services.close_report(alice, second, resolution="resolved")
        assert services.open_count(alice_home) == 1


class TestAlerts:
    @pytest.fixture(autouse=True)
    def _notifications_on(self) -> Iterator[None]:
        with override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=True, TIME_ZONE="UTC"):
            yield

    @pytest.fixture
    def outbox(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
        box: list[tuple[str, str, str]] = []

        def record(sealed: str, *, title: str, body: str) -> bool:
            box.append((sealed, title, body))
            return True

        monkeypatch.setattr(outbound, "send", record)
        return box

    def reachable(self, user: User, chat: int = 1) -> str:
        """Give the person a channel; the stored (sealed) address, which `send` receives."""
        url = URL.rsplit("/", 1)[0] + f"/{chat}"
        notification_services.add_channel(user, name="Phone", url=url)
        return str(Channel.objects.get(user=user).url)

    def test_the_people_who_write_and_have_a_channel_hear_of_it(
        self,
        alice: User,
        member: User,
        reader: User,
        alice_home: Home,
        make_user: Callable[..., User],
        add_member: Callable[..., object],
        outbox: list[tuple[str, str, str]],
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        # The report comes from `member`: the reader is left out as a reader, not as the author.
        silent = make_user("silent@example.com")
        add_member(alice_home, silent, Role.OWNER)  # a writer with no channel
        heard = self.reachable(alice, 1)
        self.reachable(member, 2)
        self.reachable(reader, 3)
        with django_capture_on_commit_callbacks(execute=True):
            services.create_report(member, alice_home, title="Smell of gas")
        assert [sealed for sealed, _title, _body in outbox] == [heard]
        for _sealed, title, body in outbox:
            assert title == "HouseCairn: problem reported"
            assert body.startswith("Smell of gas, Main house")
            assert "@" not in body

    def test_the_reporter_is_not_told_of_their_own_report(
        self,
        alice: User,
        member: User,
        alice_home: Home,
        outbox: list[tuple[str, str, str]],
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        self.reachable(alice)
        self.reachable(member)
        with django_capture_on_commit_callbacks(execute=True):
            services.create_report(member, alice_home, title="x")
        assert len(outbox) == 1

    def test_nothing_is_sent_without_a_channel_or_with_notifications_off(
        self,
        alice: User,
        alice_home: Home,
        reader: User,
        outbox: list[tuple[str, str, str]],
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        with django_capture_on_commit_callbacks(execute=True):
            report = services.create_report(reader, alice_home, title="x")
        assert outbox == []
        self.reachable(alice)
        with override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=False):
            assert alerts.report_created(str(report.id)) == 0
        assert outbox == []
        assert alerts.report_created("00000000-0000-0000-0000-000000000000") == 0

    def test_a_disabled_channel_or_account_and_quiet_hours_are_respected(
        self,
        alice: User,
        member: User,
        alice_home: Home,
        reader: User,
        outbox: list[tuple[str, str, str]],
    ) -> None:
        from housecairn.notifications.models import Channel, Preference

        self.reachable(alice)
        self.reachable(member)
        report = services.create_report(reader, alice_home, title="x")
        Channel.objects.filter(user=alice).update(enabled=False)
        Preference.objects.create(
            user=member,
            time_zone="UTC",
            quiet_start=datetime.time(0, 0),
            quiet_end=datetime.time(23, 59, 59),
        )
        assert alerts.report_created(str(report.id)) == 0
        Preference.objects.filter(user=member).delete()
        User.objects.filter(pk=member.pk).update(is_active=False)
        assert alerts.report_created(str(report.id)) == 0

    def test_the_message_is_one_line_and_holds_nothing_but_title_home_and_link(
        self,
        alice: User,
        alice_home: Home,
        reader: User,
        outbox: list[tuple[str, str, str]],
    ) -> None:
        self.reachable(alice)
        report = services.create_report(
            reader, alice_home, title="Leak\nIgnore this\rline", photo=upload(image_bytes())
        )
        with override_settings(HOUSECAIRN_SITE_URL="https://home.example.com"):
            alerts.report_created(str(report.id))
        (_sealed, _title, body) = outbox[-1]
        first, link = body.split("\n")
        assert "\r" not in body
        assert first == "Leak Ignore this line, Main house"
        assert link == f"https://home.example.com/h/{alice_home.id}/reports/{report.id}/"
