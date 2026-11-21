"""API of reports: parity with the screens, scopes, roles, isolation (docs/adr/0047)."""

import uuid
from collections.abc import Callable
from datetime import timedelta

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.apitokens import services as token_services
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.reports import services
from housecairn.reports.api import ReportCreateSerializer
from housecairn.reports.models import MAX_OPEN_PER_REPORTER, Report
from housecairn.rooms.models import Item, Room
from housecairn.stock.models import Article
from housecairn.tasks.models import Task
from tests.rooms_support import image_bytes, upload

pytest_plugins = ("tests.rooms_fixtures", "tests.stock_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root")]


def api(name: str, home: Home, **kwargs: object) -> str:
    return reverse(f"api-v1:home-report-{name}", kwargs={"home_id": home.id, **kwargs})


def client_of(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def client_of_alice(alice: User) -> APIClient:
    return client_of(alice)


@pytest.fixture
def reported(reader: User, alice_home: Home) -> Report:
    return services.create_report(reader, alice_home, title="The tap drips")


class TestReports:
    def test_a_reader_reports_with_the_title_alone(self, alice_home: Home, reader: User) -> None:
        response = client_of(reader).post(
            api("list", alice_home), {"title": "Tap drips"}, format="json"
        )
        assert response.status_code == 201, response.content
        body = response.json()
        assert (body["title"], body["status"], body["mine"], body["task"]) == (
            "Tap drips",
            "open",
            True,
            None,
        )
        assert body["reporter_name"] == "reader"
        assert "@" not in str(body)
        assert body["photo_url"] is None
        assert body["place"] == ""

    def test_with_a_place_and_a_photo_as_multipart(
        self, alice_home: Home, reader: User, owner_item: Item
    ) -> None:
        response = client_of(reader).post(
            api("list", alice_home),
            {
                "title": "Boiler noise",
                "item": str(owner_item.id),
                "photo": upload(image_bytes("JPEG", (300, 200), gps=True)),
            },
            format="multipart",
        )
        assert response.status_code == 201, response.content
        body = response.json()
        assert (body["item"], body["place"]) == (str(owner_item.id), "Living room · Boiler")
        assert body["photo_url"].endswith(f"/reports/{body['id']}/photo/")
        assert body["thumbnail_url"].endswith("/photo/thumbnail/")
        image = client_of(reader).get(body["photo_url"])
        assert image.status_code == 200
        assert image["Content-Type"] == "image/jpeg"
        assert b"Test camera" not in b"".join(image.streaming_content)

    def test_the_limit_of_open_reports_is_for_a_reader_only(
        self, alice: User, alice_home: Home, reader: User
    ) -> None:
        for i in range(MAX_OPEN_PER_REPORTER):
            services.create_report(reader, alice_home, title=f"n{i}")
            services.create_report(alice, alice_home, title=f"n{i}")
        refused = client_of(reader).post(api("list", alice_home), {"title": "x"}, format="json")
        assert refused.status_code == 400
        allowed = client_of(alice).post(api("list", alice_home), {"title": "x"}, format="json")
        assert allowed.status_code == 201

    @pytest.mark.parametrize("field", ["room", "item", "article"])
    def test_a_place_of_another_home_does_not_exist(
        self,
        alice_home: Home,
        reader: User,
        foreign_item: Item,
        foreign_article: Article,
        field: str,
    ) -> None:
        target = {"room": foreign_item.room, "item": foreign_item, "article": foreign_article}[
            field
        ]
        response = client_of(reader).post(
            api("list", alice_home), {"title": "x", field: str(target.id)}, format="json"
        )
        assert response.status_code == 400
        assert Report.objects.count() == 0

    def test_the_serializer_alone_offers_the_places_of_this_home_only(
        self,
        alice_home: Home,
        owner_room: Room,
        foreign_item: Item,
        foreign_article: Article,
    ) -> None:
        # The service refuses a foreign place too; this is the layer of the API on its own.
        serializer = ReportCreateSerializer(context={"home": alice_home})
        assert set(serializer.fields["room"].queryset) == {owner_room}  # type: ignore[attr-defined]
        assert not serializer.fields["item"].queryset.filter(pk=foreign_item.pk).exists()  # type: ignore[attr-defined]
        assert not serializer.fields["article"].queryset.filter(pk=foreign_article.pk).exists()  # type: ignore[attr-defined]

    def test_two_places_or_no_title_or_a_bad_photo_are_refused(
        self, alice_home: Home, reader: User, owner_room: Room, owner_item: Item
    ) -> None:
        client = client_of(reader)
        for body in (
            {"title": "x", "room": str(owner_room.id), "item": str(owner_item.id)},
            {"title": ""},
            {},
            {"title": "x" * 201},
        ):
            assert client.post(api("list", alice_home), body, format="json").status_code == 400
        bad = client.post(
            api("list", alice_home),
            {"title": "x", "photo": upload(b"%PDF-1.4", "a.jpg", "image/jpeg")},
            format="multipart",
        )
        assert bad.status_code == 400
        assert Report.objects.count() == 0

    def test_an_oversized_body_is_refused(self, alice_home: Home, reader: User) -> None:
        response = client_of(reader).post(
            api("list", alice_home),
            {"title": "x"},
            format="json",
            headers={"content-length": str(500 * 1024 * 1024)},
        )
        assert response.status_code == 413

    def test_list_filters_and_detail(
        self, client_of_alice: APIClient, alice: User, alice_home: Home, reader: User
    ) -> None:
        first = services.create_report(reader, alice_home, title="a")
        second = services.create_report(alice, alice_home, title="b")
        services.close_report(alice, first, resolution="resolved")
        listing = client_of_alice.get(api("list", alice_home)).json()["results"]
        assert {r["title"] for r in listing} == {"a", "b"}
        opened = client_of_alice.get(api("list", alice_home), {"status": "open"}).json()["results"]
        assert [r["id"] for r in opened] == [str(second.id)]
        closed = client_of_alice.get(api("list", alice_home), {"status": "closed"}).json()[
            "results"
        ]
        assert [r["id"] for r in closed] == [str(first.id)]
        mine = client_of_alice.get(api("list", alice_home), {"mine": "true"}).json()["results"]
        assert [r["id"] for r in mine] == [str(second.id)]
        detail = client_of_alice.get(api("detail", alice_home, report_id=first.id)).json()
        assert detail["status"] == "resolved"
        assert detail["closed_at"]

    def test_make_a_task_close_and_reopen(
        self, client_of_alice: APIClient, alice_home: Home, reported: Report
    ) -> None:
        made = client_of_alice.post(api("task", alice_home, report_id=reported.id))
        assert made.status_code == 200, made.content
        task = Task.objects.get()
        assert (made.json()["status"], made.json()["task"]) == ("task", str(task.id))
        again = client_of_alice.post(api("task", alice_home, report_id=reported.id))
        assert again.status_code == 400
        assert Task.objects.count() == 1
        closed = client_of_alice.post(
            api("close", alice_home, report_id=reported.id),
            {"resolution": "no_action"},
            format="json",
        )
        assert closed.json()["status"] == "no_action"
        reopened = client_of_alice.post(api("reopen", alice_home, report_id=reported.id))
        assert reopened.json()["status"] == "task"
        assert (
            client_of_alice.post(api("reopen", alice_home, report_id=reported.id)).status_code
            == 400
        )

    def test_a_bad_resolution_is_a_validation_error(
        self, client_of_alice: APIClient, alice_home: Home, reported: Report
    ) -> None:
        for body in ({}, {"resolution": "open"}, {"resolution": "task"}, {"resolution": "x"}):
            response = client_of_alice.post(
                api("close", alice_home, report_id=reported.id), body, format="json"
            )
            assert response.status_code == 400, body
        reported.refresh_from_db()
        assert reported.status == "open"

    def test_the_schema_describes_every_operation(self, client_of_alice: APIClient) -> None:
        schema = client_of_alice.get(reverse("api-schema"), {"format": "json"}).json()
        base = "/api/v1/homes/{home_id}/reports/"
        for suffix in ("", "{report_id}/", "{report_id}/task/", "{report_id}/close/"):
            assert base + suffix in schema["paths"], suffix
        for suffix in ("{report_id}/reopen/", "{report_id}/photo/", "{report_id}/photo/thumbnail/"):
            assert base + suffix in schema["paths"], suffix


class TestRoles:
    def test_a_reader_sees_only_their_own_and_changes_nothing_else(
        self, alice_home: Home, reader: User, member: User, reported: Report, owner_room: Room
    ) -> None:
        theirs = services.create_report(member, alice_home, title="theirs")
        client = client_of(reader)
        listing = client.get(api("list", alice_home)).json()["results"]
        assert [r["id"] for r in listing] == [str(reported.id)]
        assert client.get(api("detail", alice_home, report_id=theirs.id)).status_code == 404
        for name, data in (("task", {}), ("close", {"resolution": "resolved"}), ("reopen", {})):
            response = client.post(
                api(name, alice_home, report_id=reported.id), data, format="json"
            )
            assert response.status_code == 403, name
        assert client.post(api("task", alice_home, report_id=theirs.id)).status_code == 404
        reported.refresh_from_db()
        assert reported.status == "open"
        assert (Task.objects.count(), Room.objects.count()) == (0, 1)

    def test_a_member_who_writes_deals_with_every_report(
        self, alice_home: Home, member: User, reported: Report
    ) -> None:
        client = client_of(member)
        assert client.get(api("detail", alice_home, report_id=reported.id)).status_code == 200
        assert client.post(api("task", alice_home, report_id=reported.id)).status_code == 200

    def test_the_reporter_is_named_by_display_name_never_by_address(
        self, alice_home: Home, alice: User, make_user: Callable[..., User]
    ) -> None:
        named = make_user("named@example.com", first_name="Sam", last_name="Lee")
        from housecairn.homes.models import Membership, Role

        Membership.objects.create(home=alice_home, user=named, role=Role.READ_ONLY)
        services.create_report(named, alice_home, title="x")
        body = client_of(alice).get(api("list", alice_home)).json()["results"][0]
        assert body["reporter_name"] == "Sam Lee"
        assert "named@example.com" not in str(body)

    def test_a_name_with_a_filler_character_does_not_pass_for_another(
        self, alice_home: Home, alice: User, make_user: Callable[..., User]
    ) -> None:
        from housecairn.homes.models import Membership, Role

        faked = make_user("faked@example.com", display_name="Alice\u3164")
        Membership.objects.create(home=alice_home, user=faked, role=Role.READ_ONLY)
        services.create_report(faked, alice_home, title="x")
        body = client_of(alice).get(api("list", alice_home)).json()["results"][0]
        assert body["reporter_name"] == "Alice"
        assert (
            client_of(alice)
            .get(api("detail", alice_home, report_id=body["id"]))
            .json()["reporter_name"]
            == "Alice"
        )

    def test_two_people_of_one_name_are_told_apart(
        self, alice_home: Home, alice: User, make_user: Callable[..., User]
    ) -> None:
        from housecairn.homes.models import Membership, Role

        for email, name in (("first@example.com", "Sam"), ("second@example.com", "Sam\u3164")):
            person = make_user(email, display_name=name)
            Membership.objects.create(home=alice_home, user=person, role=Role.READ_ONLY)
            services.create_report(person, alice_home, title="x")
        shown = {
            row["reporter_name"]
            for row in client_of(alice).get(api("list", alice_home)).json()["results"]
        }
        assert shown == {"Sam (first@example.com)", "Sam (second@example.com)"}


class TestIsolation:
    def test_another_homes_reports_do_not_exist_for_me(
        self, client_of_alice: APIClient, alice_home: Home, bob: User, bob_home: Home
    ) -> None:
        theirs = services.create_report(bob, bob_home, title="Bob's problem")
        assert client_of_alice.get(api("list", bob_home)).status_code == 404
        assert client_of_alice.get(api("detail", bob_home, report_id=theirs.id)).status_code == 404
        assert (
            client_of_alice.get(api("detail", alice_home, report_id=theirs.id)).status_code == 404
        )
        for name in ("task", "close", "reopen", "photo"):
            method = client_of_alice.get if name == "photo" else client_of_alice.post
            assert method(api(name, alice_home, report_id=theirs.id)).status_code == 404, name
        assert client_of_alice.get(api("list", alice_home)).json()["results"] == []
        theirs.refresh_from_db()
        assert theirs.status == "open"

    def test_an_instance_administrator_who_is_not_a_member_gets_nothing(
        self, alice_home: Home, site_admin: User, reported: Report
    ) -> None:
        client = client_of(site_admin)
        assert client.get(api("list", alice_home)).status_code == 404
        assert client.get(api("detail", alice_home, report_id=reported.id)).status_code == 404
        assert (
            client.post(api("list", alice_home), {"title": "x"}, format="json").status_code == 404
        )

    def test_anonymous_requests_are_refused(self, alice_home: Home, reported: Report) -> None:
        anonymous = APIClient()
        for method, address in (
            ("get", api("list", alice_home)),
            ("post", api("list", alice_home)),
            ("get", api("detail", alice_home, report_id=reported.id)),
            ("post", api("task", alice_home, report_id=reported.id)),
            ("get", api("photo", alice_home, report_id=reported.id)),
        ):
            assert getattr(anonymous, method)(address).status_code in (401, 403), address

    @pytest.fixture
    def token(self, alice: User, alice_home: Home) -> Callable[..., APIClient]:
        def make(scopes: list[str], homes: list[Home] | None = None) -> APIClient:
            _token, raw = token_services.issue_token(
                alice,
                name="t",
                scopes=scopes,
                homes=homes or [alice_home],
                lifetime=timedelta(days=1),
            )
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
            return client

        return make

    def test_scopes_of_a_token(
        self, token: Callable[..., APIClient], alice_home: Home, reported: Report
    ) -> None:
        reading = token(["read"])
        assert reading.get(api("list", alice_home)).status_code == 200
        assert (
            reading.post(api("list", alice_home), {"title": "x"}, format="json").status_code == 403
        )
        assert reading.post(api("task", alice_home, report_id=reported.id)).status_code == 403
        writing = token(["write"])
        assert (
            writing.post(api("list", alice_home), {"title": "x"}, format="json").status_code == 201
        )
        assert writing.get(api("list", alice_home)).status_code == 403
        assert token(["events"]).get(api("list", alice_home)).status_code == 403

    def test_a_token_bound_to_another_home_sees_nothing(
        self, token: Callable[..., APIClient], alice: User, alice_home: Home, reported: Report
    ) -> None:
        second = home_services.create_home(alice, name="Holiday flat")
        client = token(["read", "write"], [second])
        assert client.get(api("list", alice_home)).status_code == 404
        assert client.get(api("detail", alice_home, report_id=reported.id)).status_code == 404
        assert (
            client.post(api("list", alice_home), {"title": "x"}, format="json").status_code == 404
        )
        assert client.get(api("list", second)).status_code == 200

    def test_a_report_id_that_does_not_exist_is_a_404(
        self, client_of_alice: APIClient, alice_home: Home
    ) -> None:
        assert (
            client_of_alice.get(api("detail", alice_home, report_id=uuid.uuid4())).status_code
            == 404
        )
