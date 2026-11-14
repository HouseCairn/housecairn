"""A task made from a report shows the report and its photo (docs/adr/0047)."""

import datetime
from collections.abc import Callable
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.homes.models import Home, Role
from housecairn.reports import services
from housecairn.reports.models import Report
from housecairn.tasks.models import Occurrence, Task
from tests.rooms_support import image_bytes, upload
from tests.tasks_fixtures import TODAY

pytest_plugins = ("tests.rooms_fixtures", "tests.tasks_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root", "frozen_today")]


def photo() -> Any:
    return upload(image_bytes("JPEG", (300, 200)))


def made_task(reporter: User, writer: User, home: Home, title: str, *, with_photo: bool) -> Task:
    report = services.create_report(
        reporter, home, title=title, photo=photo() if with_photo else None
    )
    return services.make_task(writer, report)


def many(
    count: int,
    writer: User,
    home: Home,
    make_user: Callable[..., User],
    add_member: Callable[..., object],
) -> list[Task]:
    """`count` tasks made from reports with a photo, by several readers (a reader may have 20)."""
    readers = []
    for n in range(-(-count // 10)):
        reader = make_user(f"many{count}-{n}@example.com")
        add_member(home, reader, Role.READ_ONLY)
        readers.append(reader)
    return [
        made_task(readers[n // 10], writer, home, f"Leak {n}", with_photo=True)
        for n in range(count)
    ]


def detail(home: Home, task: Task) -> str:
    return reverse("tasks:detail", kwargs={"home_id": home.id, "task_id": task.id})


def signed(client: Client, user: User) -> Client:
    client.force_login(user)
    return client


@pytest.fixture
def task(alice: User, reader: User, alice_home: Home) -> Task:
    return made_task(reader, alice, alice_home, "The tap drips", with_photo=True)


class TestTaskPage:
    def test_it_shows_who_reported_when_the_text_the_photo_and_a_link(
        self, client: Client, alice: User, reader: User, alice_home: Home, task: Task
    ) -> None:
        report = Report.objects.get()
        body = signed(client, alice).get(detail(alice_home, task)).content.decode()
        assert "Reported problem" in body
        assert "The tap drips" in body
        assert "Reported on" in body
        assert "reader@example.com" in body or "reader" in body.lower()
        thumbnail = reverse(
            "reports:photo-thumbnail", kwargs={"home_id": alice_home.id, "report_id": report.id}
        )
        full = reverse("reports:photo", kwargs={"home_id": alice_home.id, "report_id": report.id})
        page = reverse("reports:detail", kwargs={"home_id": alice_home.id, "report_id": report.id})
        assert f'src="{thumbnail}"' in body
        assert f'href="{full}"' in body
        assert page in body

    def test_several_reports_are_all_there_the_most_recent_first(
        self, client: Client, alice: User, reader: User, alice_home: Home, task: Task
    ) -> None:
        later = services.create_report(reader, alice_home, title="It drips more")
        Report.objects.filter(pk=later.pk).update(task=task)
        Report.objects.filter(pk=Report.objects.exclude(pk=later.pk).get().pk).update(
            created_at=later.created_at - datetime.timedelta(days=3), title="Older drip"
        )
        body = signed(client, alice).get(detail(alice_home, task)).content.decode()
        assert body.index("It drips more") < body.index("Older drip")

    def test_a_report_without_photo_shows_the_block_without_a_thumbnail(
        self, client: Client, alice: User, reader: User, alice_home: Home
    ) -> None:
        plain = made_task(reader, alice, alice_home, "A crack in the wall", with_photo=False)
        body = signed(client, alice).get(detail(alice_home, plain)).content.decode()
        assert "A crack in the wall" in body
        assert "Reported problem" in body
        assert 'class="report-photo"' not in body

    def test_a_task_not_made_from_a_report_has_no_such_block(
        self, client: Client, alice: User, alice_home: Home, owner_task: Task
    ) -> None:
        body = signed(client, alice).get(detail(alice_home, owner_task)).content.decode()
        assert "Reported problem" not in body

    def test_the_text_is_escaped(
        self, client: Client, alice: User, reader: User, alice_home: Home
    ) -> None:
        odd = made_task(reader, alice, alice_home, "<b>bold</b>", with_photo=False)
        body = signed(client, alice).get(detail(alice_home, odd)).content.decode()
        assert "&lt;b&gt;bold&lt;/b&gt;" in body
        assert "<b>bold</b>" not in body

    def test_a_reader_is_shown_their_own_reports_only(
        self,
        client: Client,
        alice: User,
        reader: User,
        alice_home: Home,
        task: Task,
        make_user: Callable[..., User],
        add_member: Callable[..., object],
    ) -> None:
        other = make_user("other@example.com")
        add_member(alice_home, other, Role.READ_ONLY)
        assert "Reported problem" not in (
            signed(client, other).get(detail(alice_home, task)).content.decode()
        )
        assert "Reported problem" in (
            signed(client, reader).get(detail(alice_home, task)).content.decode()
        )

    def test_the_page_takes_the_same_queries_for_one_report_or_several(
        self, client: Client, alice: User, reader: User, alice_home: Home, task: Task
    ) -> None:
        signed(client, alice)
        client.get(detail(alice_home, task))  # warm what is cached
        with CaptureQueriesContext(connection) as one:
            client.get(detail(alice_home, task))
        for n in range(4):
            extra = services.create_report(reader, alice_home, title=f"More {n}", photo=photo())
            Report.objects.filter(pk=extra.pk).update(task=task)
        with CaptureQueriesContext(connection) as five:
            client.get(detail(alice_home, task))
        assert len(five) == len(one)


class TestPhotoAccess:
    def test_the_thumbnail_and_photo_are_served_by_the_protected_views_to_members_only(
        self, client: Client, alice: User, bob: User, alice_home: Home, task: Task
    ) -> None:
        report = Report.objects.get()
        body = signed(client, alice).get(detail(alice_home, task)).content.decode()
        for name in ("photo", "photo-thumbnail"):
            address = reverse(
                f"reports:{name}", kwargs={"home_id": alice_home.id, "report_id": report.id}
            )
            assert address in body
            assert signed(client, alice).get(address).status_code == 200
            assert signed(client, bob).get(address).status_code == 404
        # Never an address of the storage.
        assert "/media/" not in body
        assert str(report.photo.name) not in body

    def test_a_report_of_another_home_is_not_served_under_the_home_of_the_asker(
        self, client: Client, bob: User, bob_home: Home, task: Task
    ) -> None:
        report = Report.objects.get()
        for name in ("photo", "photo-thumbnail"):
            address = reverse(
                f"reports:{name}", kwargs={"home_id": bob_home.id, "report_id": report.id}
            )
            assert signed(client, bob).get(address).status_code == 404

    def test_a_person_of_another_home_gets_a_404_on_the_task_too(
        self, client: Client, bob: User, alice_home: Home, task: Task
    ) -> None:
        assert signed(client, bob).get(detail(alice_home, task)).status_code == 404


class TestLists:
    def test_the_list_and_today_mark_a_task_made_from_a_report_with_a_photo(
        self, client: Client, alice: User, reader: User, alice_home: Home, task: Task
    ) -> None:
        plain = made_task(reader, alice, alice_home, "A loose tile", with_photo=False)
        # A report has no date: Today shows the tasks that were given one.
        Occurrence.objects.filter(task__in=[task, plain]).update(due_on=TODAY)
        signed(client, alice)
        for name in ("tasks:list", "tasks:today"):
            body = client.get(reverse(name, kwargs={"home_id": alice_home.id})).content.decode()
            assert "The tap drips" in body
            assert "A loose tile" in body
            assert body.count('class="pill pill-ghost">Photo<') == 1

    def test_the_row_sent_back_after_an_action_in_place_keeps_the_mark(
        self, client: Client, alice: User, alice_home: Home, task: Task
    ) -> None:
        occurrence = Occurrence.objects.get(task=task)
        address = reverse("tasks:list", kwargs={"home_id": alice_home.id})
        signed(client, alice)
        for name in ("complete", "undo"):
            response = client.post(
                reverse(
                    f"tasks:{name}",
                    kwargs={
                        "home_id": alice_home.id,
                        "task_id": task.id,
                        "occurrence_id": occurrence.id,
                    },
                ),
                {"next": address, "in_place": "row"},
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 200
            if name == "undo":
                assert 'class="pill pill-ghost">Photo<' in response.content.decode()

    def test_a_reader_sees_no_mark_for_the_report_of_somebody_else(
        self,
        client: Client,
        alice: User,
        alice_home: Home,
        task: Task,
        make_user: Callable[..., User],
        add_member: Callable[..., object],
    ) -> None:
        other = make_user("other@example.com")
        add_member(alice_home, other, Role.READ_ONLY)
        body = (
            signed(client, other)
            .get(reverse("tasks:list", kwargs={"home_id": alice_home.id}))
            .content.decode()
        )
        assert 'class="pill pill-ghost">Photo<' not in body

    def test_the_list_takes_as_many_queries_for_30_tasks_as_for_3(
        self,
        client: Client,
        alice: User,
        alice_home: Home,
        make_user: Callable[..., User],
        add_member: Callable[..., object],
        django_assert_num_queries: Any,
    ) -> None:
        address = reverse("tasks:list", kwargs={"home_id": alice_home.id})
        many(3, alice, alice_home, make_user, add_member)
        signed(client, alice).get(address)
        with CaptureQueriesContext(connection) as small:
            client.get(address)
        Task.objects.all().delete()
        many(30, alice, alice_home, make_user, add_member)
        with django_assert_num_queries(len(small.captured_queries)):
            body = client.get(address).content.decode()
        assert body.count('class="pill pill-ghost">Photo<') == 30


class TestApi:
    def test_a_task_lists_the_reports_it_was_made_from(
        self, alice: User, alice_home: Home, task: Task
    ) -> None:
        client = APIClient()
        client.force_authenticate(alice)
        report = Report.objects.get()
        body = client.get(
            reverse(
                "api-v1:home-task-detail", kwargs={"home_id": alice_home.id, "task_id": task.id}
            )
        ).json()
        assert len(body["reports"]) == 1
        row = body["reports"][0]
        assert row["id"] == str(report.id)
        assert row["has_photo"] is True
        assert row["created_at"]
        assert row["photo_url"].endswith(
            reverse(
                "api-v1:home-report-photo",
                kwargs={"home_id": alice_home.id, "report_id": report.id},
            )
        )
        assert client.get(row["photo_url"]).status_code == 200

    def test_a_report_without_photo_has_no_photo_url_and_a_plain_task_no_reports(
        self, alice: User, reader: User, alice_home: Home, owner_task: Task
    ) -> None:
        plain = made_task(reader, alice, alice_home, "A crack", with_photo=False)
        client = APIClient()
        client.force_authenticate(alice)
        one = client.get(
            reverse(
                "api-v1:home-task-detail", kwargs={"home_id": alice_home.id, "task_id": plain.id}
            )
        ).json()
        assert one["reports"][0]["has_photo"] is False
        assert one["reports"][0]["photo_url"] is None
        other = client.get(
            reverse(
                "api-v1:home-task-detail",
                kwargs={"home_id": alice_home.id, "task_id": owner_task.id},
            )
        ).json()
        assert other["reports"] == []

    def test_the_list_takes_as_many_queries_for_30_tasks_as_for_3(
        self,
        alice: User,
        alice_home: Home,
        make_user: Callable[..., User],
        add_member: Callable[..., object],
        django_assert_num_queries: Any,
    ) -> None:
        client = APIClient()
        client.force_authenticate(alice)
        address = reverse("api-v1:home-task-list", kwargs={"home_id": alice_home.id})
        many(3, alice, alice_home, make_user, add_member)
        client.get(address)
        with CaptureQueriesContext(connection) as small:
            client.get(address)
        Task.objects.all().delete()
        many(30, alice, alice_home, make_user, add_member)
        with django_assert_num_queries(len(small.captured_queries)):
            rows = client.get(address).json()["results"]
        assert len(rows) == 30
        assert all(len(row["reports"]) == 1 for row in rows)

    def test_a_reader_gets_only_their_own_reports_in_the_task(
        self,
        alice_home: Home,
        task: Task,
        make_user: Callable[..., User],
        add_member: Callable[..., object],
    ) -> None:
        other = make_user("other@example.com")
        add_member(alice_home, other, Role.READ_ONLY)
        client = APIClient()
        client.force_authenticate(other)
        body = client.get(
            reverse(
                "api-v1:home-task-detail", kwargs={"home_id": alice_home.id, "task_id": task.id}
            )
        ).json()
        assert body["reports"] == []

    def test_the_schema_documents_the_reports(self, alice: User) -> None:
        client = APIClient()
        client.force_authenticate(alice)
        schema = client.get(reverse("api-schema") + "?format=json").json()
        task = schema["components"]["schemas"]["Task"]["properties"]["reports"]
        assert task["readOnly"] is True
        assert task["items"]["$ref"].endswith("/LinkedReport")
        linked = schema["components"]["schemas"]["LinkedReport"]["properties"]
        assert {"id", "created_at", "has_photo", "photo_url"} <= set(linked)
