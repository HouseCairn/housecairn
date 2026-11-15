"""API of tasks, occurrences, contractors, attachments: filters, scopes, roles, isolation."""

import datetime
import time
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
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Item, Room
from housecairn.tasks import services
from housecairn.tasks.models import Attachment, Occurrence, Task
from tests.rooms_support import image_bytes, upload
from tests.tasks_fixtures import PDF, TODAY

pytest_plugins = ("tests.tasks_fixtures",)
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root", "frozen_today")]

DAY = datetime.timedelta(days=1)


def api(name: str, home: Home, **kwargs: object) -> str:
    return reverse(f"api-v1:home-{name}", kwargs={"home_id": home.id, **kwargs})


@pytest.fixture
def client_of_alice(api_client: APIClient, alice: User) -> APIClient:
    api_client.force_authenticate(alice)
    return api_client


class TestTasks:
    def test_crud(
        self, client_of_alice: APIClient, alice_home: Home, owner_room: Room, owner_item: Item
    ) -> None:
        created = client_of_alice.post(
            api("task-list", alice_home),
            {
                "title": "Service the boiler",
                "room": str(owner_room.id),
                "item": str(owner_item.id),
                "priority": "high",
                "due_on": str(TODAY + 3 * DAY),
                "repeat_mode": "fixed",
                "repeat_unit": "year",
                "repeat_interval": 1,
                "labour_budget": "80.00",
                "procedure": "1. Switch off",
                "hide_days_before": 7,
            },
            format="json",
        )
        assert created.status_code == 201, created.json()
        body = created.json()
        assert (body["title"], body["due_on"], body["priority"]) == (
            "Service the boiler",
            str(TODAY + 3 * DAY),
            "high",
        )
        assert body["open_occurrence"]
        assert body["labour_budget"] == "80.00"
        detail = api("task-detail", alice_home, task_id=body["id"])
        assert client_of_alice.get(detail).json()["procedure"] == "1. Switch off"
        patched = client_of_alice.patch(
            detail, {"title": "Renamed", "due_on": str(TODAY + 9 * DAY)}, format="json"
        )
        assert patched.json()["title"] == "Renamed"
        assert patched.json()["due_on"] == str(TODAY + 9 * DAY)
        assert client_of_alice.delete(detail).status_code == 204
        assert client_of_alice.get(detail).status_code == 404

    def test_only_the_title_is_required(self, client_of_alice: APIClient, alice_home: Home) -> None:
        assert (
            client_of_alice.post(api("task-list", alice_home), {}, format="json").status_code == 400
        )
        created = client_of_alice.post(api("task-list", alice_home), {"title": "x"}, format="json")
        assert created.status_code == 201
        assert created.json()["due_on"] is None
        assert created.json()["repeat_mode"] == "once"

    def test_validation_errors_are_reported_by_field(
        self, client_of_alice: APIClient, alice_home: Home
    ) -> None:
        for data, field in (
            ({"title": "x", "repeat_mode": "fixed"}, "due_on"),
            ({"title": "x", "season_start_month": 4}, "season_start_month"),
            ({"title": "x", "repeat_interval": 0}, "repeat_interval"),
            ({"title": "x", "priority": "urgent"}, "priority"),
            ({"title": "x", "labour_budget": "-1"}, "labour_budget"),
            ({"title": "x", "due_on": "tomorrow"}, "due_on"),
        ):
            response = client_of_alice.post(api("task-list", alice_home), data, format="json")
            assert response.status_code == 400, data
            assert field in response.json(), data

    def test_things_of_another_home_are_unknown(
        self, client_of_alice: APIClient, bob: User, alice_home: Home, bob_home: Home
    ) -> None:
        theirs = room_services.create_room(bob, bob_home, name="Theirs")
        their_contractor = services.create_contractor(bob, bob_home, name="B")
        for field, value in (
            ("room", theirs.id),
            ("contractor", their_contractor.id),
            ("assignee", bob.id),
        ):
            response = client_of_alice.post(
                api("task-list", alice_home), {"title": "x", field: str(value)}, format="json"
            )
            assert response.status_code == 400
            assert field in response.json()

    def test_the_assignee_is_a_member(
        self, client_of_alice: APIClient, alice_home: Home, member: User
    ) -> None:
        response = client_of_alice.post(
            api("task-list", alice_home), {"title": "x", "assignee": str(member.id)}, format="json"
        )
        assert response.json()["assignee"] == str(member.id)

    def test_filters(
        self,
        client_of_alice: APIClient,
        alice: User,
        alice_home: Home,
        owner_room: Room,
        owner_item: Item,
        member: User,
    ) -> None:
        a = services.create_task(
            alice,
            alice_home,
            title="Boiler check",
            room=owner_room,
            item=owner_item,
            due_on=TODAY - DAY,
            assignee=member,
        )
        b = services.create_task(alice, alice_home, title="Gutters", due_on=TODAY + 30 * DAY)
        c = services.create_task(alice, alice_home, title="Fence", due_on=TODAY)
        services.complete(alice, services.open_occurrence_of(c))  # type: ignore[arg-type]

        def ids(query: str) -> set[str]:
            response = client_of_alice.get(api("task-list", alice_home) + query)
            assert response.status_code == 200, response.content
            return {t["id"] for t in response.json()["results"]}

        assert ids("") == {str(a.id), str(b.id), str(c.id)}
        assert ids("?status=open") == {str(a.id), str(b.id)}
        assert ids("?status=closed") == {str(c.id)}
        assert ids(f"?room={owner_room.id}") == {str(a.id)}
        assert ids(f"?item={owner_item.id}") == {str(a.id)}
        assert ids(f"?assignee={member.id}") == {str(a.id)}
        assert ids("?q=gutt") == {str(b.id)}
        assert ids(f"?due_before={TODAY}") == {str(a.id)}
        assert ids(f"?due_after={TODAY + DAY}") == {str(b.id)}
        for bad in ("?status=maybe", "?room=nope", "?due_before=soon"):
            assert client_of_alice.get(api("task-list", alice_home) + bad).status_code == 400

    def test_listing_is_paginated_by_cursor(
        self, client_of_alice: APIClient, alice: User, alice_home: Home
    ) -> None:
        for number in range(5):
            services.create_task(alice, alice_home, title=f"t{number}")
        first = client_of_alice.get(api("task-list", alice_home) + "?page_size=2").json()
        assert len(first["results"]) == 2
        assert first["next"]
        assert "cursor=" in first["next"]
        second = client_of_alice.get(first["next"]).json()
        assert {t["id"] for t in first["results"]}.isdisjoint({t["id"] for t in second["results"]})

    def test_methods_not_offered(
        self, client_of_alice: APIClient, alice_home: Home, owner_task: Task
    ) -> None:
        detail = api("task-detail", alice_home, task_id=owner_task.id)
        assert client_of_alice.put(detail, {"title": "x"}, format="json").status_code == 405


class TestUndatedTasks:
    """`?due=none`: the active tasks with nothing planned, for an integration that polls."""

    @pytest.fixture
    def tasks(self, alice: User, alice_home: Home, bob: User, bob_home: Home) -> dict[str, Task]:
        dated = services.create_task(alice, alice_home, title="dated", due_on=TODAY + DAY)
        undated = services.create_task(alice, alice_home, title="undated")
        undated_two = services.create_task(alice, alice_home, title="undated two")
        done = services.create_task(alice, alice_home, title="done")
        services.complete(alice, services.open_occurrence_of(done))  # type: ignore[arg-type]
        archived = services.create_task(alice, alice_home, title="archived")
        services.delete_task(alice, archived)
        foreign = services.create_task(bob, bob_home, title="foreign")
        return {
            "dated": dated,
            "undated": undated,
            "undated_two": undated_two,
            "done": done,
            "foreign": foreign,
        }

    @staticmethod
    def titles(client: APIClient, home: Home, query: str = "?due=none") -> list[str]:
        response = client.get(api("task-list", home) + query)
        assert response.status_code == 200, response.content
        return sorted(t["title"] for t in response.json()["results"])

    def test_only_the_active_tasks_without_a_date(
        self, client_of_alice: APIClient, alice_home: Home, tasks: dict[str, Task]
    ) -> None:
        assert self.titles(client_of_alice, alice_home) == ["undated", "undated two"]

    def test_the_filter_combines_with_the_others_and_the_cursor(
        self, client_of_alice: APIClient, alice_home: Home, tasks: dict[str, Task]
    ) -> None:
        assert self.titles(client_of_alice, alice_home, "?due=none&q=two") == ["undated two"]
        assert self.titles(client_of_alice, alice_home, "?due=none&status=closed") == []
        assert self.titles(client_of_alice, alice_home, "?due=none&due_after=2000-01-01") == []
        first = client_of_alice.get(api("task-list", alice_home) + "?due=none&page_size=1").json()
        assert len(first["results"]) == 1
        assert "due=none" in first["next"]
        second = client_of_alice.get(first["next"]).json()
        assert sorted([first["results"][0]["title"], second["results"][0]["title"]]) == [
            "undated",
            "undated two",
        ]
        assert second["next"] is None
        assert all(t["due_on"] is None for t in first["results"] + second["results"])

    def test_without_the_filter_everything_active_still_comes(
        self, client_of_alice: APIClient, alice_home: Home, tasks: dict[str, Task]
    ) -> None:
        assert self.titles(client_of_alice, alice_home, "") == [
            "dated",
            "done",
            "undated",
            "undated two",
        ]

    @pytest.mark.parametrize("bad", ["soon", "all", "None", "1"])
    def test_another_value_is_a_400(
        self, client_of_alice: APIClient, alice_home: Home, bad: str
    ) -> None:
        response = client_of_alice.get(api("task-list", alice_home) + f"?due={bad}")
        assert response.status_code == 400
        assert "due" in response.json()

    def test_a_token_that_reads_the_home_gets_the_same_answer(
        self, alice: User, alice_home: Home, tasks: dict[str, Task]
    ) -> None:
        _token, raw = token_services.issue_token(
            alice,
            name="t",
            scopes=["read"],
            homes=[alice_home],
            lifetime=timedelta(days=1),
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        assert self.titles(client, alice_home) == ["undated", "undated two"]

    def test_the_schema_lists_the_parameter(self, client_of_alice: APIClient) -> None:
        schema = client_of_alice.get(reverse("api-schema") + "?format=json").json()
        listing = schema["paths"]["/api/v1/homes/{home_id}/tasks/"]["get"]
        names = {q["name"]: q for q in listing["parameters"]}
        assert names["due"]["schema"]["enum"] == ["none"]


class TestDateBounds:
    """A date out of the window around today is a 400, never a long computation or a 500."""

    def test_task_dates_out_of_the_window_are_refused(
        self, client_of_alice: APIClient, alice_home: Home, owner_task: Task
    ) -> None:
        listing = api("task-list", alice_home)
        for day in ("0001-01-01", "9999-12-31"):
            refused = client_of_alice.post(listing, {"title": "x", "due_on": day}, format="json")
            assert refused.status_code == 400, refused.content
            assert "due_on" in refused.json()
        detail = api("task-detail", alice_home, task_id=owner_task.id)
        assert (
            client_of_alice.patch(detail, {"due_on": "0001-01-01"}, format="json").status_code
            == 400
        )

    def test_the_masking_delay_on_the_oldest_date_is_a_400_not_a_500(
        self, client_of_alice: APIClient, alice_home: Home
    ) -> None:
        refused = client_of_alice.post(
            api("task-list", alice_home),
            {"title": "x", "due_on": "0001-01-02", "hide_days_before": 365},
            format="json",
        )
        assert refused.status_code == 400, refused.content

    def test_occurrence_dates_out_of_the_window_are_refused(
        self, client_of_alice: APIClient, alice_home: Home, owner_occurrence: Occurrence
    ) -> None:
        complete = api("occurrence-complete", alice_home, occurrence_id=owner_occurrence.id)
        assert (
            client_of_alice.post(
                complete, {"completed_on": "0001-01-01"}, format="json"
            ).status_code
            == 400
        )
        postpone = api("occurrence-postpone", alice_home, occurrence_id=owner_occurrence.id)
        assert (
            client_of_alice.post(postpone, {"due_on": "9999-01-01"}, format="json").status_code
            == 400
        )
        done = client_of_alice.post(complete, {}, format="json")
        assert done.status_code == 200
        detail = api("occurrence-detail", alice_home, occurrence_id=owner_occurrence.id)
        assert (
            client_of_alice.patch(detail, {"completed_on": "0001-01-01"}, format="json").status_code
            == 400
        )

    def test_the_sheet_of_a_daily_task_on_the_oldest_date_is_quick(
        self, client_of_alice: APIClient, alice_home: Home
    ) -> None:
        low = services.date_bounds()[0]
        created = client_of_alice.post(
            api("task-list", alice_home),
            {
                "title": "Daily",
                "due_on": str(low),
                "repeat_mode": "fixed",
                "repeat_unit": "day",
                "repeat_interval": 1,
            },
            format="json",
        )
        assert created.status_code == 201, created.content
        occurrence = created.json()["open_occurrence"]
        began = time.perf_counter()
        done = client_of_alice.post(
            api("occurrence-complete", alice_home, occurrence_id=occurrence), {}, format="json"
        )
        assert done.status_code == 200
        assert time.perf_counter() - began < 0.5


class TestComesBack:
    def test_a_done_task_given_a_date_and_a_rule_gets_its_occurrence(
        self, client_of_alice: APIClient, alice_home: Home, alice: User
    ) -> None:
        created = client_of_alice.post(
            api("task-list", alice_home),
            {"title": "Sweep", "due_on": str(TODAY)},
            format="json",
        ).json()
        done = client_of_alice.post(
            api("occurrence-complete", alice_home, occurrence_id=created["open_occurrence"]),
            {},
            format="json",
        )
        assert done.status_code == 200
        detail = api("task-detail", alice_home, task_id=created["id"])
        assert client_of_alice.get(detail).json()["open_occurrence"] is None
        patched = client_of_alice.patch(
            detail,
            {
                "due_on": "2027-03-01",
                "repeat_mode": "fixed",
                "repeat_unit": "year",
                "repeat_interval": 1,
            },
            format="json",
        )
        assert patched.status_code == 200, patched.content
        body = patched.json()
        assert body["open_occurrence"] is not None
        assert body["due_on"] == "2027-03-01"


class TestOccurrences:
    def test_done_creates_the_next_and_undo_takes_it_back(
        self,
        client_of_alice: APIClient,
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        before = list(
            Occurrence.all_objects.filter(task=owner_task).values_list("id", "status", "due_on")
        )
        done = client_of_alice.post(
            api("occurrence-complete", alice_home, occurrence_id=owner_occurrence.id),
            {},
            format="json",
        )
        assert done.status_code == 200, done.content
        body = done.json()
        assert body["occurrence"]["status"] == "done"
        assert body["occurrence"]["completed_on"] == str(TODAY)
        assert body["next"]["status"] == "planned"
        assert body["next"]["due_on"] == "2027-06-15"
        undo = client_of_alice.post(
            api("occurrence-undo", alice_home, occurrence_id=owner_occurrence.id)
        )
        assert undo.status_code == 200
        assert undo.json()["status"] == "planned"
        # The next occurrence is taken back as a deleted row (a tombstone of the change log).
        assert (
            list(Occurrence.objects.filter(task=owner_task).values_list("id", "status", "due_on"))
            == before
        )

    def test_done_with_a_date_a_cost_and_a_note(
        self, client_of_alice: APIClient, alice_home: Home, owner_occurrence: Occurrence
    ) -> None:
        response = client_of_alice.post(
            api("occurrence-complete", alice_home, occurrence_id=owner_occurrence.id),
            {"completed_on": str(TODAY - 4 * DAY), "actual_cost": "7.50", "note": "Two AAA"},
            format="json",
        )
        occurrence = response.json()["occurrence"]
        assert (
            occurrence["completed_on"],
            occurrence["actual_cost"],
            occurrence["completion_note"],
        ) == (
            str(TODAY - 4 * DAY),
            "7.50",
            "Two AAA",
        )
        patched = client_of_alice.patch(
            api("occurrence-detail", alice_home, occurrence_id=owner_occurrence.id),
            {"actual_cost": "9.00", "status": "planned", "due_on": "2030-01-01"},
            format="json",
        )
        assert patched.json()["actual_cost"] == "9.00"
        assert patched.json()["status"] == "done"
        assert patched.json()["due_on"] != "2030-01-01"

    def test_errors_of_the_rules_come_back_as_400(
        self, client_of_alice: APIClient, alice_home: Home, owner_occurrence: Occurrence
    ) -> None:
        complete = api("occurrence-complete", alice_home, occurrence_id=owner_occurrence.id)
        assert (
            client_of_alice.post(
                complete, {"completed_on": str(TODAY + DAY)}, format="json"
            ).status_code
            == 400
        )
        assert (
            client_of_alice.post(
                api("occurrence-undo", alice_home, occurrence_id=owner_occurrence.id)
            ).status_code
            == 400
        )
        assert (
            client_of_alice.patch(
                api("occurrence-detail", alice_home, occurrence_id=owner_occurrence.id),
                {"actual_cost": "1"},
                format="json",
            ).status_code
            == 400
        )
        assert client_of_alice.post(complete, {}, format="json").status_code == 200
        again = client_of_alice.post(complete, {}, format="json")
        assert again.status_code == 400
        assert "already closed" in str(again.json())

    def test_skip_and_postpone(
        self,
        client_of_alice: APIClient,
        alice_home: Home,
        alice: User,
        owner_task: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        postpone = api("occurrence-postpone", alice_home, occurrence_id=owner_occurrence.id)
        assert (
            client_of_alice.post(
                postpone, {"due_on": str(TODAY - 9 * DAY)}, format="json"
            ).status_code
            == 400
        )
        assert client_of_alice.post(postpone, {}, format="json").status_code == 400
        moved = client_of_alice.post(
            postpone, {"due_on": str(TODAY + 5 * DAY)}, format="json"
        ).json()
        assert moved["status"] == "postponed"
        assert moved["due_on"] == str(TODAY + 5 * DAY)
        skipped = client_of_alice.post(
            api("occurrence-skip", alice_home, occurrence_id=owner_occurrence.id)
        ).json()
        assert skipped["occurrence"]["status"] == "skipped"
        assert skipped["next"]["status"] == "planned"

    def test_filters_and_order(
        self, client_of_alice: APIClient, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        late = services.create_task(
            alice, alice_home, title="late", due_on=TODAY - 10 * DAY, room=owner_room
        )
        services.create_task(alice, alice_home, title="undated")
        soon = services.create_task(alice, alice_home, title="soon", due_on=TODAY + 2 * DAY)
        hidden = services.create_task(
            alice, alice_home, title="hidden", due_on=TODAY + 90 * DAY, hide_days_before=5
        )
        closed = services.create_task(alice, alice_home, title="closed", due_on=TODAY)
        services.complete(alice, services.open_occurrence_of(closed))  # type: ignore[arg-type]

        def titles(query: str) -> list[str]:
            response = client_of_alice.get(api("occurrence-list", alice_home) + query)
            assert response.status_code == 200, response.content
            return [o["task_title"] for o in response.json()["results"]]

        assert titles("?status=open") == ["late", "soon", "undated"]
        assert titles("?status=open&include_hidden=1") == ["late", "soon", "hidden", "undated"]
        assert titles("?overdue=true") == ["late"]
        assert titles("?status=done") == ["closed"]
        assert titles("?status=closed") == ["closed"]
        assert titles(f"?room={owner_room.id}") == ["late"]
        assert titles(f"?task={soon.id}") == ["soon"]
        assert titles(f"?due_after={TODAY}&due_before={TODAY + 5 * DAY}&status=open") == ["soon"]
        assert titles(f"?item={uuid.uuid4()}") == []
        late_row = client_of_alice.get(
            api("occurrence-list", alice_home) + f"?task={late.id}"
        ).json()["results"][0]
        assert late_row["is_overdue"] is True
        assert late_row["room"] == str(owner_room.id)
        for bad in ("?status=maybe", "?task=x", "?due_after=x"):
            assert client_of_alice.get(api("occurrence-list", alice_home) + bad).status_code == 400
        del hidden

    def test_pagination_keeps_the_arrears_first(
        self, client_of_alice: APIClient, alice: User, alice_home: Home
    ) -> None:
        for number in range(4):
            services.create_task(
                alice, alice_home, title=f"t{number}", due_on=TODAY + (number - 2) * DAY
            )
        services.create_task(alice, alice_home, title="undated")
        seen: list[str] = []
        address: str | None = api("occurrence-list", alice_home) + "?page_size=2"
        while address:
            page = client_of_alice.get(address).json()
            seen += [o["task_title"] for o in page["results"]]
            address = page["next"]
        assert seen == ["t0", "t1", "t2", "t3", "undated"]

    def test_methods_not_offered(
        self, client_of_alice: APIClient, alice_home: Home, owner_occurrence: Occurrence
    ) -> None:
        assert (
            client_of_alice.delete(
                api("occurrence-detail", alice_home, occurrence_id=owner_occurrence.id)
            ).status_code
            == 405
        )
        assert client_of_alice.post(api("occurrence-list", alice_home), {}).status_code == 405


class TestContractors:
    def test_crud(self, client_of_alice: APIClient, alice_home: Home) -> None:
        created = client_of_alice.post(
            api("contractor-list", alice_home),
            {"name": "A. Roofer", "trade": "roofer", "note": "n"},
            format="json",
        )
        assert created.status_code == 201
        detail = api("contractor-detail", alice_home, contractor_id=created.json()["id"])
        assert (
            client_of_alice.patch(
                detail, {"trade": "other", "trade_other": "Slater"}, format="json"
            ).json()["trade_label"]
            == "Slater"
        )
        assert (
            client_of_alice.get(api("contractor-list", alice_home)).json()["results"][0]["name"]
            == "A. Roofer"
        )
        assert (
            client_of_alice.post(api("contractor-list", alice_home), {}, format="json").status_code
            == 400
        )
        assert client_of_alice.delete(detail).status_code == 204
        assert client_of_alice.get(detail).status_code == 404


class TestAttachments:
    @pytest.fixture
    def document(self, alice: User, owner_task: Task) -> Attachment:
        return services.add_attachment(alice, owner_task, upload=upload(PDF, "manual.pdf"))

    def test_upload_read_rename_delete(
        self,
        client_of_alice: APIClient,
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        created = client_of_alice.post(
            api("attachment-list", alice_home),
            {
                "task": str(owner_task.id),
                "occurrence": str(owner_occurrence.id),
                "file": upload(image_bytes("PNG"), "x.png"),
                "name": "Before",
            },
            format="multipart",
        )
        assert created.status_code == 201, created.content
        body = created.json()
        assert (body["name"], body["is_image"], body["occurrence"]) == (
            "Before",
            True,
            str(owner_occurrence.id),
        )
        assert body["thumbnail_url"]
        assert body["content_url"]
        detail = api("attachment-detail", alice_home, attachment_id=body["id"])
        assert (
            client_of_alice.patch(detail, {"name": "../After.png"}, format="json").json()["name"]
            == "After.png"
        )
        content = client_of_alice.get(
            api("attachment-content", alice_home, attachment_id=body["id"]),
            HTTP_ACCEPT="application/json",
        )
        assert content.status_code == 200
        assert content["Content-Type"] == "image/png"
        thumbnail = client_of_alice.get(
            api("attachment-thumbnail", alice_home, attachment_id=body["id"])
        )
        assert thumbnail["Content-Type"] == "image/jpeg"
        assert client_of_alice.delete(detail).status_code == 204
        assert client_of_alice.get(detail).status_code == 404

    def test_a_document_is_a_download_without_thumbnail(
        self, client_of_alice: APIClient, alice_home: Home, document: Attachment
    ) -> None:
        assert (
            client_of_alice.get(
                api("attachment-detail", alice_home, attachment_id=document.id)
            ).json()["thumbnail_url"]
            is None
        )
        response = client_of_alice.get(
            api("attachment-content", alice_home, attachment_id=document.id)
        )
        assert response["Content-Type"] == "application/pdf"
        assert response["Content-Disposition"].startswith("attachment;")
        assert response["X-Content-Type-Options"] == "nosniff"
        assert (
            client_of_alice.get(
                api("attachment-thumbnail", alice_home, attachment_id=document.id)
            ).status_code
            == 404
        )

    def test_bad_uploads(
        self,
        client_of_alice: APIClient,
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
        alice: User,
    ) -> None:
        address = api("attachment-list", alice_home)
        other = services.create_task(alice, alice_home, title="other")
        for data in (
            {
                "task": str(owner_task.id),
                "file": upload(b"MZ\x90\x00", "evil.pdf", "application/pdf"),
            },
            {"task": str(owner_task.id)},
            {"file": upload(PDF)},
            {"task": str(other.id), "occurrence": str(owner_occurrence.id), "file": upload(PDF)},
        ):
            assert client_of_alice.post(address, data, format="multipart").status_code == 400, data
        assert (
            client_of_alice.post(
                address, {"task": str(uuid.uuid4()), "file": upload(PDF)}, format="multipart"
            ).status_code
            == 404
        )
        assert not Attachment.objects.exists()

    def test_an_oversized_body_is_refused_before_it_is_read(
        self, client_of_alice: APIClient, alice_home: Home, settings: object
    ) -> None:
        from housecairn.rooms import conf

        settings.HOUSECAIRN_PHOTO_MAX_BYTES = conf.MIN_PHOTO_MAX_BYTES  # type: ignore[attr-defined]
        response = client_of_alice.post(
            api("attachment-list", alice_home),
            data=b"x",
            content_type="multipart/form-data; boundary=x",
            headers={"Content-Length": str(conf.MIN_PHOTO_MAX_BYTES + conf.MULTIPART_OVERHEAD + 1)},
        )
        assert response.status_code == 413

    def test_filters(
        self,
        client_of_alice: APIClient,
        alice: User,
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
        document: Attachment,
    ) -> None:
        other = services.create_task(alice, alice_home, title="other")
        services.add_attachment(
            alice, other, upload=upload(PDF), occurrence=services.open_occurrence_of(other)
        )
        listing = client_of_alice.get(
            api("attachment-list", alice_home) + f"?task={owner_task.id}"
        ).json()["results"]
        assert [a["id"] for a in listing] == [str(document.id)]
        by_occurrence = client_of_alice.get(
            api("attachment-list", alice_home)
            + f"?occurrence={services.open_occurrence_of(other).id}"
        ).json()["results"]  # type: ignore[union-attr]
        assert len(by_occurrence) == 1
        assert by_occurrence[0]["task"] == str(other.id)


class TestRolesScopesAndIsolation:
    @pytest.fixture
    def document(self, alice: User, owner_task: Task) -> Attachment:
        return services.add_attachment(
            alice, owner_task, upload=upload(image_bytes("PNG"), "manual.png")
        )

    @pytest.fixture
    def contractor(self, alice: User, alice_home: Home) -> object:
        return services.create_contractor(alice, alice_home, name="A")

    def endpoints(
        self, home: Home, task: Task, occ: Occurrence, doc: Attachment, contractor: object
    ) -> list[tuple[str, str]]:
        return [
            ("get", api("task-list", home)),
            ("get", api("task-detail", home, task_id=task.id)),
            ("get", api("occurrence-list", home)),
            ("get", api("occurrence-detail", home, occurrence_id=occ.id)),
            ("get", api("contractor-list", home)),
            ("get", api("contractor-detail", home, contractor_id=contractor.id)),  # type: ignore[attr-defined]
            ("get", api("contractor-vcard", home, contractor_id=contractor.id)),  # type: ignore[attr-defined]
            ("get", api("attachment-list", home)),
            ("get", api("attachment-detail", home, attachment_id=doc.id)),
            ("get", api("attachment-content", home, attachment_id=doc.id)),
            ("get", api("attachment-thumbnail", home, attachment_id=doc.id)),
        ]

    def writes(
        self, home: Home, task: Task, occ: Occurrence, doc: Attachment, contractor: object
    ) -> list[tuple[str, str, dict[str, object]]]:
        return [
            ("post", api("task-list", home), {"title": "x"}),
            ("patch", api("task-detail", home, task_id=task.id), {"title": "x"}),
            ("delete", api("task-detail", home, task_id=task.id), {}),
            ("post", api("occurrence-complete", home, occurrence_id=occ.id), {}),
            ("post", api("occurrence-skip", home, occurrence_id=occ.id), {}),
            ("post", api("occurrence-undo", home, occurrence_id=occ.id), {}),
            (
                "post",
                api("occurrence-postpone", home, occurrence_id=occ.id),
                {"due_on": str(TODAY + DAY)},
            ),
            (
                "patch",
                api("occurrence-detail", home, occurrence_id=occ.id),
                {"completion_note": "x"},
            ),
            ("post", api("contractor-list", home), {"name": "x"}),
            ("patch", api("contractor-detail", home, contractor_id=contractor.id), {"name": "x"}),  # type: ignore[attr-defined]
            ("delete", api("contractor-detail", home, contractor_id=contractor.id), {}),  # type: ignore[attr-defined]
            ("post", api("attachment-list", home), {"task": str(task.id), "file": upload(PDF)}),
            ("patch", api("attachment-detail", home, attachment_id=doc.id), {"name": "x"}),
            ("delete", api("attachment-detail", home, attachment_id=doc.id), {}),
        ]

    @staticmethod
    def send(client: APIClient, method: str, address: str, data: dict[str, object]) -> int:
        fmt = "multipart" if "file" in data else "json"
        return (
            int(getattr(client, method)(address, data, format=fmt).status_code)
            if method != "get" and method != "delete"
            else int(getattr(client, method)(address).status_code)
        )

    @pytest.mark.parametrize("who", ["bob", "site_admin"])
    def test_a_stranger_and_the_instance_administrator_see_and_change_nothing(
        self,
        api_client: APIClient,
        request: pytest.FixtureRequest,
        who: str,
        alice_home: Home,
        bob_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
        document: Attachment,
        contractor: object,
    ) -> None:
        api_client.force_authenticate(request.getfixturevalue(who))
        for method, address in self.endpoints(
            alice_home, owner_task, owner_occurrence, document, contractor
        ):
            assert getattr(api_client, method)(address).status_code == 404, address
        for method, address, data in self.writes(
            alice_home, owner_task, owner_occurrence, document, contractor
        ):
            assert self.send(api_client, method, address, data) == 404, (method, address)
        # Their own home does not know the identifiers of the other one either.
        if who == "bob":
            for method, address in (
                self.endpoints(bob_home, owner_task, owner_occurrence, document, contractor)[1:2]
                + self.endpoints(bob_home, owner_task, owner_occurrence, document, contractor)[3:4]
            ):
                assert getattr(api_client, method)(address).status_code == 404, address
        assert Task.objects.get(id=owner_task.id).title == owner_task.title
        assert Occurrence.objects.get(id=owner_occurrence.id).status == "planned"
        assert Attachment.objects.filter(id=document.id).exists()

    def test_read_only_reads_everything_and_changes_nothing(
        self,
        api_client: APIClient,
        reader: User,
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
        document: Attachment,
        contractor: object,
    ) -> None:
        api_client.force_authenticate(reader)
        for method, address in self.endpoints(
            alice_home, owner_task, owner_occurrence, document, contractor
        )[:]:
            assert getattr(api_client, method)(address).status_code == 200, address
        for method, address, data in self.writes(
            alice_home, owner_task, owner_occurrence, document, contractor
        ):
            assert self.send(api_client, method, address, data) == 403, (method, address)
        assert Occurrence.objects.get(id=owner_occurrence.id).status == "planned"
        assert Task.objects.filter(id=owner_task.id, title=owner_task.title).exists()

    def test_a_member_writes(
        self, api_client: APIClient, member: User, alice_home: Home, owner_occurrence: Occurrence
    ) -> None:
        api_client.force_authenticate(member)
        assert (
            api_client.post(api("task-list", alice_home), {"title": "x"}, format="json").status_code
            == 201
        )
        assert (
            api_client.post(
                api("occurrence-complete", alice_home, occurrence_id=owner_occurrence.id),
                {},
                format="json",
            ).status_code
            == 200
        )

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
        self,
        token: Callable[..., APIClient],
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
        document: Attachment,
        contractor: object,
    ) -> None:
        reading = token(["read"])
        for method, address in self.endpoints(
            alice_home, owner_task, owner_occurrence, document, contractor
        ):
            assert getattr(reading, method)(address).status_code == 200, address
        for method, address, data in self.writes(
            alice_home, owner_task, owner_occurrence, document, contractor
        ):
            assert self.send(reading, method, address, data) == 403, (method, address)
        writing = token(["write"])
        assert (
            writing.post(api("task-list", alice_home), {"title": "x"}, format="json").status_code
            == 201
        )
        assert (
            writing.post(
                api("occurrence-complete", alice_home, occurrence_id=owner_occurrence.id),
                {},
                format="json",
            ).status_code
            == 200
        )
        assert writing.get(api("task-list", alice_home)).status_code == 403
        events_only = token(["events"])
        assert events_only.get(api("task-list", alice_home)).status_code == 403
        assert (
            events_only.post(
                api("task-list", alice_home), {"title": "y"}, format="json"
            ).status_code
            == 403
        )

    def test_a_token_bound_to_another_home_sees_nothing(
        self,
        token: Callable[..., APIClient],
        alice: User,
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
        document: Attachment,
        contractor: object,
    ) -> None:
        second = home_services.create_home(alice, name="Holiday flat")
        client = token(["read", "write"], [second])
        for method, address in self.endpoints(
            alice_home, owner_task, owner_occurrence, document, contractor
        ):
            assert getattr(client, method)(address).status_code == 404, address
        for method, address, data in self.writes(
            alice_home, owner_task, owner_occurrence, document, contractor
        ):
            assert self.send(client, method, address, data) == 404, (method, address)
        assert client.get(api("task-list", second)).status_code == 200
        assert client.get(api("task-list", second)).json()["results"] == []
        assert Occurrence.objects.get(id=owner_occurrence.id).status == "planned"

    def test_the_identifiers_of_a_task_of_one_home_do_not_work_in_another_home_of_the_same_user(
        self,
        client_of_alice: APIClient,
        alice: User,
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
    ) -> None:
        second = home_services.create_home(alice, name="Holiday flat")
        assert (
            client_of_alice.get(api("task-detail", second, task_id=owner_task.id)).status_code
            == 404
        )
        assert (
            client_of_alice.post(
                api("occurrence-complete", second, occurrence_id=owner_occurrence.id),
                {},
                format="json",
            ).status_code
            == 404
        )
        mixed = client_of_alice.post(
            api("task-list", second), {"title": "x", "room": str(owner_task.room_id)}, format="json"
        )
        assert mixed.status_code == 400

    def test_anonymous_requests_are_refused_and_change_nothing(
        self,
        alice_home: Home,
        owner_task: Task,
        owner_occurrence: Occurrence,
        document: Attachment,
        contractor: object,
    ) -> None:
        anonymous = APIClient()
        for method, address in self.endpoints(
            alice_home, owner_task, owner_occurrence, document, contractor
        ):
            assert getattr(anonymous, method)(address).status_code in {401, 403}, address
        for method, address, data in self.writes(
            alice_home, owner_task, owner_occurrence, document, contractor
        ):
            assert self.send(anonymous, method, address, data) in {401, 403}, (method, address)
        assert Occurrence.objects.get(id=owner_occurrence.id).status == "planned"
        assert Attachment.objects.filter(id=document.id).exists()

    def test_unknown_and_foreign_identifiers_answer_the_same_404_body(
        self, client_of_alice: APIClient, bob: User, alice_home: Home, bob_home: Home
    ) -> None:
        """Byte for byte: nothing tells an unknown identifier from one of another home."""
        theirs = services.create_task(bob, bob_home, title="Theirs", due_on=TODAY)
        occurrence = services.open_occurrence_of(theirs)
        assert occurrence is not None
        attachment = services.add_attachment(bob, theirs, upload=upload(PDF))
        contractor = services.create_contractor(bob, bob_home, name="B")
        pairs = [
            ("task-detail", "task_id", theirs.id),
            ("occurrence-detail", "occurrence_id", occurrence.id),
            ("contractor-detail", "contractor_id", contractor.id),
            ("attachment-detail", "attachment_id", attachment.id),
            ("attachment-content", "attachment_id", attachment.id),
            ("attachment-thumbnail", "attachment_id", attachment.id),
        ]
        for name, key, foreign_id in pairs:
            foreign = client_of_alice.get(api(name, alice_home, **{key: foreign_id}))
            unknown = client_of_alice.get(api(name, alice_home, **{key: uuid.uuid4()}))
            assert foreign.status_code == unknown.status_code == 404, name
            assert foreign.content == unknown.content, name
            assert foreign["Content-Type"] == unknown["Content-Type"], name
