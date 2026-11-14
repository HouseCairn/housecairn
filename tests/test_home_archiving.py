"""Archiving a home: listed apart, silent, still openable (docs/adr/0079, lot M1).

Each rule has a test that fails without it: owners only, hidden from Today and the selector,
no reminder, an empty calendar feed that stays valid, `GET /api/v1/homes/` that keeps
listing the home, and the export format 4 that carries the date.
"""

import datetime
import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse
from icalendar import Calendar
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.apitokens import services as token_services
from housecairn.apitokens.models import Scope
from housecairn.audit.models import AuditEvent
from housecairn.calendar import feeds
from housecairn.homes import current, exports, restore
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Role
from housecairn.notifications import alerts, reminders
from housecairn.notifications import services as notification_services
from housecairn.reports import services as report_services
from housecairn.tasks import services as task_services
from tests.data_support import archive_bytes
from tests.test_restore_security import entries, rebuild

pytest_plugins = ("tests.rooms_fixtures",)
pytestmark = pytest.mark.django_db

URL = "tgram://123456789:fixture-not-a-token/987654"
UTC = datetime.UTC


def open_ids(client: Client) -> list[str]:
    return [str(h.pk) for h in current.for_request(client.get("/").wsgi_request).homes]


@pytest.fixture
def flat(alice: User) -> Home:
    """A second home of Alice, after the first one by name."""
    return home_services.create_home(alice, name="Seaside flat")


@pytest.fixture
def member(
    make_user: Callable[..., User], alice_home: Home, add_member: Callable[..., object]
) -> User:
    user = make_user("member@example.com")
    add_member(alice_home, user, Role.MEMBER)
    return user


@pytest.fixture
def reader(
    make_user: Callable[..., User], alice_home: Home, add_member: Callable[..., object]
) -> User:
    user = make_user("reader@example.com")
    add_member(alice_home, user, Role.READ_ONLY)
    return user


def archive_url(home: Home) -> str:
    return reverse("homes:archive", kwargs={"home_id": home.id})


def unarchive_url(home: Home) -> str:
    return reverse("homes:unarchive", kwargs={"home_id": home.id})


class TestOwnersOnly:
    def test_an_owner_archives_and_brings_the_home_back(
        self, alice: User, alice_home: Home
    ) -> None:
        home_services.archive_home(alice, alice_home)
        alice_home.refresh_from_db()
        assert alice_home.archived_at is not None
        assert AuditEvent.objects.filter(action="home.archived", home_id=alice_home.id).count() == 1
        home_services.unarchive_home(alice, alice_home)
        alice_home.refresh_from_db()
        assert alice_home.archived_at is None
        assert AuditEvent.objects.filter(action="home.unarchived", home_id=alice_home.id).exists()

    def test_doing_it_twice_changes_and_records_nothing_more(
        self, alice: User, alice_home: Home
    ) -> None:
        home_services.archive_home(alice, alice_home)
        first = Home.objects.get(pk=alice_home.pk).archived_at
        home_services.archive_home(alice, alice_home)
        assert Home.objects.get(pk=alice_home.pk).archived_at == first
        assert AuditEvent.objects.filter(action="home.archived").count() == 1

    @pytest.mark.parametrize("who", ["member", "reader", "stranger"])
    def test_nobody_else_archives_or_brings_back(
        self, request: pytest.FixtureRequest, alice: User, alice_home: Home, bob: User, who: str
    ) -> None:
        person = bob if who == "stranger" else request.getfixturevalue(who)
        with pytest.raises(PermissionDenied):
            home_services.archive_home(person, alice_home)
        home_services.archive_home(alice, alice_home)
        with pytest.raises(PermissionDenied):
            home_services.unarchive_home(person, alice_home)
        assert Home.objects.get(pk=alice_home.pk).archived_at is not None

    @pytest.mark.parametrize("who", ["member", "reader"])
    def test_the_screens_refuse_a_member_who_is_not_an_owner(
        self, request: pytest.FixtureRequest, client: Client, alice_home: Home, who: str
    ) -> None:
        client.force_login(request.getfixturevalue(who))
        assert client.get(archive_url(alice_home)).status_code == 403
        assert client.post(archive_url(alice_home)).status_code == 403
        assert Home.objects.get(pk=alice_home.pk).archived_at is None
        home_services.archive_home(alice_home.memberships.get(role=Role.OWNER).user, alice_home)
        assert client.get(unarchive_url(alice_home)).status_code == 403
        assert client.post(unarchive_url(alice_home)).status_code == 403
        assert Home.objects.get(pk=alice_home.pk).archived_at is not None

    def test_the_home_page_offers_the_buttons_to_owners_only(
        self, client: Client, alice: User, member: User, alice_home: Home
    ) -> None:
        detail = reverse("homes:detail", kwargs={"home_id": alice_home.id})
        client.force_login(member)
        assert archive_url(alice_home) not in client.get(detail).content.decode()
        client.force_login(alice)
        page = client.get(detail).content.decode()
        assert archive_url(alice_home) in page
        assert unarchive_url(alice_home) not in page
        home_services.archive_home(alice, alice_home)
        page = client.get(detail).content.decode()
        assert unarchive_url(alice_home) in page
        assert archive_url(alice_home) not in page
        assert "This home is archived" in page


class TestScreens:
    def test_a_confirmation_comes_first_then_the_post_archives(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        page = signed_in.get(archive_url(alice_home))
        assert page.status_code == 200
        assert "Archive this home?" in page.content.decode()
        assert Home.objects.get(pk=alice_home.pk).archived_at is None
        response = signed_in.post(archive_url(alice_home))
        assert response.status_code == 302
        assert Home.objects.get(pk=alice_home.pk).archived_at is not None

    def test_the_return_follows_next_else_the_home(
        self, signed_in: Client, alice_home: Home, flat: Home
    ) -> None:
        detail = reverse("homes:detail", kwargs={"home_id": alice_home.id})
        assert signed_in.post(archive_url(alice_home))["Location"] == detail
        assert signed_in.post(unarchive_url(alice_home), {"next": "/h/"})["Location"] == "/h/"
        # The cancel link of the confirmation page goes back to `next` too.
        page = signed_in.get(archive_url(flat), {"next": "/h/"}).content.decode()
        assert 'href="/h/"' in page
        # A next that leaves the site is ignored.
        response = signed_in.post(archive_url(flat), {"next": "https://evil.example.org/"})
        assert response["Location"] == reverse("homes:detail", kwargs={"home_id": flat.id})

    def test_the_list_shows_an_archived_home_apart(
        self, signed_in: Client, alice: User, alice_home: Home, flat: Home
    ) -> None:
        home_services.archive_home(alice, flat)
        html = signed_in.get(reverse("homes:list")).content.decode()
        assert "Archived" in html
        assert html.index("Main house") < html.index("Archived") < html.index("Seaside flat")

    def test_an_archived_home_is_still_listed_when_it_is_the_only_one(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        home_services.archive_home(alice, alice_home)
        html = signed_in.get(reverse("homes:list")).content.decode()
        assert "Main house" in html
        assert "You are not a member of any home yet." not in html

    def test_an_archived_home_stays_readable_and_editable_by_its_address(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        home_services.archive_home(alice, alice_home)
        kwargs = {"home_id": alice_home.id}
        assert signed_in.get(reverse("tasks:today", kwargs=kwargs)).status_code == 200
        assert signed_in.get(reverse("rooms:list", kwargs=kwargs)).status_code == 200
        response = signed_in.post(
            reverse("homes:edit", kwargs=kwargs),
            {"name": "Renamed", "currency": "EUR", "hemisphere": "north"},
        )
        assert response.status_code == 302
        alice_home.refresh_from_db()
        assert alice_home.name == "Renamed"
        assert alice_home.archived_at is not None  # editing does not bring it back


class TestHiddenFromTheLists:
    def test_the_selector_and_today_leave_out_an_archived_home(
        self, signed_in: Client, alice: User, alice_home: Home, flat: Home
    ) -> None:
        assert len(open_ids(signed_in)) == 2
        home_services.archive_home(alice, flat)
        assert open_ids(signed_in) == [str(alice_home.id)]
        # Opening it by its address does not bring it into the lists.
        signed_in.get(reverse("tasks:today", kwargs={"home_id": flat.id}))
        assert open_ids(signed_in) == [str(alice_home.id)]
        html = signed_in.get(reverse("tasks:today", kwargs={"home_id": alice_home.id}))
        assert "home-switcher" not in html.content.decode()

    def test_the_selector_refuses_to_choose_an_archived_home(
        self, signed_in: Client, alice: User, alice_home: Home, flat: Home
    ) -> None:
        home_services.archive_home(alice, flat)
        assert signed_in.post(reverse("homes:current"), {"home": str(flat.id)}).status_code == 404

    def test_the_root_opens_a_home_that_is_not_archived(
        self, signed_in: Client, alice: User, alice_home: Home, flat: Home
    ) -> None:
        home_services.archive_home(alice, alice_home)
        response = signed_in.get(reverse("rooms"))
        assert response["Location"] == reverse("rooms:list", kwargs={"home_id": flat.id})

    def test_my_home_and_the_calendar_of_the_api_leave_it_out(
        self, api_client: APIClient, alice: User, alice_home: Home, flat: Home
    ) -> None:
        api_client.force_authenticate(alice)
        rows = api_client.get(reverse("api-v1:my-home")).json()
        assert {row["home"] for row in rows} == {str(alice_home.id), str(flat.id)}
        home_services.archive_home(alice, flat)
        rows = api_client.get(reverse("api-v1:my-home")).json()
        assert {row["home"] for row in rows} == {str(alice_home.id)}
        assert api_client.get(reverse("api-v1:my-home"), {"home": str(flat.id)}).status_code == 404
        today = datetime.date.today()
        window = {"from": today.isoformat(), "to": (today + datetime.timedelta(days=3)).isoformat()}
        assert api_client.get(reverse("api-v1:calendar"), window).status_code == 200
        assert (
            api_client.get(reverse("api-v1:calendar"), {**window, "home": str(flat.id)}).status_code
            == 404
        )

    def test_the_budget_of_all_homes_leaves_it_out(
        self, api_client: APIClient, alice: User, alice_home: Home, flat: Home
    ) -> None:
        api_client.force_authenticate(alice)
        url = reverse("api-v1:budget-combined")
        assert {h["id"] for row in api_client.get(url).json() for h in row["homes"]} == {
            str(alice_home.id),
            str(flat.id),
        }
        home_services.archive_home(alice, flat)
        assert {h["id"] for row in api_client.get(url).json() for h in row["homes"]} == {
            str(alice_home.id)
        }


@pytest.fixture(autouse=False)
def notifications() -> Iterator[None]:
    with override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=True, TIME_ZONE="UTC"):
        yield


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    box: list[tuple[str, str]] = []

    def record(sealed: str, *, title: str, body: str) -> bool:
        box.append((title, body))
        return True

    monkeypatch.setattr(reminders.outbound, "send", record)
    monkeypatch.setattr(alerts.outbound, "send", record)
    return box


@pytest.mark.usefixtures("notifications")
class TestNoReminder:
    def due_task(self, alice: User, home: Home) -> datetime.date:
        due = task_services.today() + datetime.timedelta(days=5)
        task_services.create_task(alice, home, title="Change the filter", due_on=due)
        return due

    def at(self, day: datetime.date) -> datetime.datetime:
        return datetime.datetime.combine(day, datetime.time(8, 0), tzinfo=UTC)

    def test_an_archived_home_sends_no_reminder_and_the_reminders_resume(
        self, alice: User, alice_home: Home, outbox: list[tuple[str, str]]
    ) -> None:
        from housecairn.notifications.models import Preference

        notification_services.add_channel(alice, name="Phone", url=URL)
        Preference.objects.update_or_create(
            user=alice, defaults={"time_zone": "UTC", "days_before": 0}
        )
        due = self.due_task(alice, alice_home)
        home_services.archive_home(alice, alice_home)
        assert reminders.dispatch(self.at(due)) == 0
        assert outbox == []
        home_services.unarchive_home(alice, alice_home)
        assert reminders.dispatch(self.at(due)) == 1
        assert len(outbox) == 1

    def test_only_the_archived_home_is_silent(
        self, alice: User, alice_home: Home, flat: Home, outbox: list[tuple[str, str]]
    ) -> None:
        from housecairn.notifications.models import Preference

        notification_services.add_channel(alice, name="Phone", url=URL)
        Preference.objects.update_or_create(
            user=alice, defaults={"time_zone": "UTC", "days_before": 0}
        )
        due = self.due_task(alice, flat)
        self.due_task(alice, alice_home)
        home_services.archive_home(alice, flat)
        assert reminders.dispatch(self.at(due)) == 1
        assert all("Seaside" not in body for _title, body in outbox)

    def test_a_report_in_an_archived_home_sends_no_alert(
        self,
        alice: User,
        alice_home: Home,
        reader: User,
        outbox: list[tuple[str, str]],
    ) -> None:
        notification_services.add_channel(alice, name="Phone", url=URL)
        report = report_services.create_report(reader, alice_home, title="The tap drips")
        outbox.clear()
        home_services.archive_home(alice, alice_home)
        assert alerts.report_created(str(report.id)) == 0
        assert outbox == []
        home_services.unarchive_home(alice, alice_home)
        assert alerts.report_created(str(report.id)) == 1


def events(body: bytes) -> list[Any]:
    return list(Calendar.from_ical(body).walk("VEVENT"))


class TestCalendarFeeds:
    @pytest.fixture(autouse=True)
    def _fresh(self) -> None:
        cache.clear()

    def fetch(self, address: str) -> Any:
        return Client().get(reverse("calendar:feed", kwargs={"token": address}))

    @pytest.mark.parametrize("combined", [False, True])
    def test_the_feed_stays_valid_but_empty_and_the_events_come_back(
        self, alice: User, alice_home: Home, combined: bool
    ) -> None:
        task_services.create_task(
            alice, alice_home, title="Boiler", due_on=task_services.today() + datetime.timedelta(1)
        )
        _feed, raw = feeds.create_feed(alice, None if combined else alice_home)
        assert [str(e["SUMMARY"]) for e in events(self.fetch(raw).content)] == ["Boiler"]
        home_services.archive_home(alice, alice_home)
        response = self.fetch(raw)
        assert response.status_code == 200
        assert events(response.content) == []
        home_services.unarchive_home(alice, alice_home)
        assert [str(e["SUMMARY"]) for e in events(self.fetch(raw).content)] == ["Boiler"]

    def test_the_combined_feed_keeps_the_other_homes(
        self, alice: User, alice_home: Home, flat: Home
    ) -> None:
        tomorrow = task_services.today() + datetime.timedelta(1)
        task_services.create_task(alice, alice_home, title="Boiler", due_on=tomorrow)
        task_services.create_task(alice, flat, title="Gutter", due_on=tomorrow)
        _feed, raw = feeds.create_feed(alice, None)
        home_services.archive_home(alice, flat)
        assert [str(e["SUMMARY"]) for e in events(self.fetch(raw).content)] == ["Boiler"]


class TestApi:
    @pytest.fixture
    def authed(self, api_client: APIClient, alice: User) -> APIClient:
        api_client.force_authenticate(alice)
        return api_client

    def test_the_list_keeps_listing_an_archived_home_with_the_field(
        self, authed: APIClient, alice: User, alice_home: Home, flat: Home
    ) -> None:
        home_services.archive_home(alice, flat)
        body = authed.get(reverse("api-v1:home-list")).json()
        rows = body["results"] if isinstance(body, dict) else body
        by_id = {row["id"]: row for row in rows}
        assert set(by_id) == {str(alice_home.id), str(flat.id)}
        assert by_id[str(flat.id)]["archived_at"] is not None
        assert by_id[str(alice_home.id)]["archived_at"] is None
        detail = authed.get(reverse("api-v1:home-detail", kwargs={"home_id": flat.id}))
        assert detail.json()["archived_at"] == by_id[str(flat.id)]["archived_at"]

    def test_the_date_is_not_written_by_a_patch(self, authed: APIClient, alice_home: Home) -> None:
        response = authed.patch(
            reverse("api-v1:home-detail", kwargs={"home_id": alice_home.id}),
            {"archived_at": "2026-01-01T00:00:00Z"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["archived_at"] is None
        assert Home.objects.get(pk=alice_home.pk).archived_at is None

    def test_owners_archive_and_bring_back(self, authed: APIClient, alice_home: Home) -> None:
        kwargs = {"home_id": alice_home.id}
        response = authed.post(reverse("api-v1:home-archive", kwargs=kwargs))
        assert response.status_code == 200
        assert response.json()["archived_at"] is not None
        response = authed.post(reverse("api-v1:home-unarchive", kwargs=kwargs))
        assert response.status_code == 200
        assert response.json()["archived_at"] is None

    @pytest.mark.parametrize("action", ["archive", "unarchive"])
    def test_a_member_who_is_not_an_owner_is_refused(
        self, api_client: APIClient, member: User, alice: User, alice_home: Home, action: str
    ) -> None:
        home_services.archive_home(alice, alice_home)
        api_client.force_authenticate(member)
        response = api_client.post(
            reverse(f"api-v1:home-{action}", kwargs={"home_id": alice_home.id})
        )
        assert response.status_code == 403
        assert Home.objects.get(pk=alice_home.pk).archived_at is not None

    def test_a_stranger_gets_a_404(
        self, api_client: APIClient, bob: User, alice_home: Home
    ) -> None:
        api_client.force_authenticate(bob)
        url = reverse("api-v1:home-archive", kwargs={"home_id": alice_home.id})
        assert api_client.post(url).status_code == 404

    def test_a_token_cannot_archive_but_still_lists_the_home(
        self, api_client: APIClient, alice: User, alice_home: Home
    ) -> None:
        _token, raw = token_services.issue_token(
            alice, name="test", scopes=[Scope.READ, Scope.WRITE], homes=[alice_home]
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        url = reverse("api-v1:home-archive", kwargs={"home_id": alice_home.id})
        assert api_client.post(url).status_code == 403
        assert Home.objects.get(pk=alice_home.pk).archived_at is None
        home_services.archive_home(alice, alice_home)
        body = api_client.get(reverse("api-v1:home-list")).json()
        rows = body["results"] if isinstance(body, dict) else body
        assert [row["id"] for row in rows] == [str(alice_home.id)]


class TestExportAndRestore:
    def test_the_archive_is_format_4_and_carries_the_date(
        self, alice: User, alice_home: Home
    ) -> None:
        assert exports.FORMAT_VERSION == 4
        data = archive_bytes(alice_home)
        files = entries(data)
        assert json.loads(files["manifest.json"])["version"] == 4
        assert json.loads(files["data/homes.json"])["home"]["archived_at"] is None
        home_services.archive_home(alice, alice_home)
        alice_home.refresh_from_db()
        files = entries(archive_bytes(alice_home))
        exported = json.loads(files["data/homes.json"])["home"]["archived_at"]
        assert exported == alice_home.archived_at.isoformat()

    def test_a_restore_brings_the_home_back_archived(self, alice: User, alice_home: Home) -> None:
        home_services.archive_home(alice, alice_home)
        alice_home.refresh_from_db()
        stamp = alice_home.archived_at
        data = archive_bytes(alice_home)
        home_services.erase_home(alice_home)
        result = restore.restore_bytes(alice, data)
        assert Home.objects.get(pk=result.home.pk).archived_at == stamp

    def test_an_archive_of_format_3_is_restored_and_not_archived(
        self, alice: User, alice_home: Home
    ) -> None:
        files = entries(archive_bytes(alice_home))
        manifest = json.loads(files["manifest.json"])
        manifest["version"] = 3
        files["manifest.json"] = json.dumps(manifest).encode()
        document = json.loads(files["data/homes.json"])
        del document["home"]["archived_at"]  # format 3 has no such field
        files["data/homes.json"] = json.dumps(document).encode()
        data = rebuild(files)
        home_services.erase_home(alice_home)
        result = restore.restore_bytes(alice, data)
        assert Home.objects.get(pk=result.home.pk).archived_at is None

    def test_a_format_4_archive_with_a_bad_date_is_refused(
        self, alice: User, alice_home: Home
    ) -> None:
        files = entries(archive_bytes(alice_home))
        document = json.loads(files["data/homes.json"])
        document["home"]["archived_at"] = "yesterday"
        files["data/homes.json"] = json.dumps(document).encode()
        data = rebuild(files)
        home_services.erase_home(alice_home)
        with pytest.raises(restore.ArchiveRefusedError):
            restore.restore_bytes(alice, data)
