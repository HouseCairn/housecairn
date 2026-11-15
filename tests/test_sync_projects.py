"""Every write of the projects records its change, the changed fields only (docs/adr/0077, M3e)."""

import datetime
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.homes import restore
from housecairn.homes.models import Home
from housecairn.projects import changelog, links, preview, services
from housecairn.projects.models import (
    AnnualEnvelope,
    BudgetLine,
    Project,
    ProjectFile,
    ProjectLink,
    ProjectTask,
    Quote,
)
from housecairn.rooms.models import Room
from housecairn.sync import retention
from housecairn.sync.links import link_row_id
from housecairn.sync.models import Change, RowClocks
from housecairn.tasks import services as task_services
from tests.data_support import archive_bytes
from tests.rooms_support import image_bytes, upload

pytest_plugins = ("tests.rooms_fixtures",)
pytestmark = pytest.mark.django_db

ROOMS = "projects.project_rooms"


def changes_of(entity: str, row_id: Any = None, *, since: int = 0) -> list[Change]:
    rows = Change.objects.filter(entity=entity, pk__gt=since).order_by("pos")
    return list(rows if row_id is None else rows.filter(row_id=row_id))


def last_pk() -> int:
    return Change.objects.order_by("-pk").values_list("pk", flat=True).first() or 0


def rooms_row(project: Project, room: Room) -> uuid.UUID:
    return link_row_id(project.home_id, Project.rooms.through, project.pk, room.pk)


@pytest.fixture
def kitchen(alice_home: Home) -> Room:
    room: Room = Room.objects.create(home=alice_home, name="Kitchen")
    return room


@pytest.fixture
def project(alice: User, alice_home: Home, kitchen: Room) -> Project:
    return services.create_project(alice, alice_home, title="Kitchen", rooms=[kitchen])


class TestProjects:
    def test_a_new_project_records_every_field_and_its_rooms(
        self, project: Project, kitchen: Room
    ) -> None:
        (made,) = changes_of("projects.project", project.pk)
        assert made.kind == "put"
        assert made.fields == {
            "title": "Kitchen",
            "goal": "",
            "status": "idea",
            "priority": "normal",
            "estimated_minutes": None,
            "season": "",
            "season_start_month": None,
            "season_end_month": None,
        }
        (link,) = changes_of(ROOMS)
        assert link.row_id == rooms_row(project, kitchen)
        assert link.fields == {"project_id": str(project.pk), "room_id": str(kitchen.pk)}

    def test_an_edit_records_only_the_fields_whose_value_changed(
        self, alice: User, project: Project, kitchen: Room
    ) -> None:
        since = last_pk()
        # A form sends every field: those that did not change get no clock.
        services.update_project(
            alice, project, rooms=[kitchen], title="New kitchen", goal="", status="idea"
        )
        (edit,) = changes_of("projects.project", project.pk, since=since)
        assert edit.fields == {"title": "New kitchen"}
        assert changes_of(ROOMS, since=since) == []

    def test_an_edit_that_changes_nothing_records_nothing(
        self, alice: User, project: Project
    ) -> None:
        since = last_pk()
        services.update_project(alice, project, title="Kitchen")
        assert Change.objects.filter(pk__gt=since).count() == 0

    def test_the_instance_changed_by_a_form_is_compared_to_the_database(
        self, alice: User, project: Project
    ) -> None:
        since = last_pk()
        project.title = "Changed by a form before the service"
        services.update_project(alice, project, status="planned")
        (edit,) = changes_of("projects.project", project.pk, since=since)
        assert edit.fields == {"title": "Changed by a form before the service", "status": "planned"}

    def test_rooms_taken_out_leave_a_tombstone_and_come_back_from_it(
        self, alice: User, alice_home: Home, project: Project, kitchen: Room
    ) -> None:
        hall = Room.objects.create(home=alice_home, name="Hall")
        since = last_pk()
        services.update_project(alice, project, rooms=[hall])
        kinds = {(c.row_id, c.kind) for c in changes_of(ROOMS, since=since)}
        assert kinds == {(rooms_row(project, kitchen), "delete"), (rooms_row(project, hall), "put")}
        assert RowClocks.objects.get(row_id=rooms_row(project, kitchen)).tombstoned_at
        since = last_pk()
        services.update_project(alice, project, rooms=[hall, kitchen])
        back = changes_of(ROOMS, rooms_row(project, kitchen), since=since)
        assert [c.kind for c in back] == ["restore", "put"]
        assert RowClocks.objects.get(row_id=rooms_row(project, kitchen)).tombstoned_at is None

    def test_deleting_a_project_writes_a_tombstone_for_it_and_everything_it_holds(
        self, alice: User, alice_home: Home, project: Project, media_root: Path
    ) -> None:
        line = services.create_line(alice, project, label="Units", planned=Decimal("900"))
        quote = services.create_quote(alice, project, amount=Decimal("850"))
        stored = services.add_file(alice, project, upload=upload(image_bytes()))
        link = links.add_link(alice, project, url="https://example.com/units")
        task = services.add_task(alice, project, title="Measure")
        task_link = ProjectTask.objects.get(task=task)
        since = last_pk()
        services.delete_project(alice, project)
        deleted = {(c.entity, c.row_id) for c in Change.objects.filter(pk__gt=since, kind="delete")}
        assert deleted == {
            ("projects.project", project.pk),
            ("projects.budgetline", line.pk),
            ("projects.quote", quote.pk),
            ("projects.projectfile", stored.pk),
            ("projects.projectlink", link.pk),
            ("projects.projecttask", task_link.pk),
        }
        # The links to tasks go for good; the task stays.
        assert not ProjectTask.all_objects.filter(pk=task_link.pk).exists()
        assert task_services.tasks_of(alice_home).filter(pk=task.pk).exists()


class TestTasksOfAProject:
    def test_a_task_made_in_a_project_records_its_link(self, alice: User, project: Project) -> None:
        task = services.add_task(alice, project, title="Measure")
        link = ProjectTask.objects.get(task=task)
        (made,) = changes_of("projects.projecttask", link.pk)
        assert made.fields == {"project_id": str(project.pk), "task_id": str(task.pk)}

    def test_a_task_taken_out_leaves_a_tombstone_and_may_join_another_project(
        self, alice: User, alice_home: Home, project: Project
    ) -> None:
        task = task_services.create_task(alice, alice_home, title="Measure")
        link = services.attach_task(alice, project, task)
        link_id = link.pk
        assert [c.kind for c in changes_of("projects.projecttask", link_id)] == ["put"]
        services.detach_task(alice, link)
        assert [c.kind for c in changes_of("projects.projecttask", link_id)] == ["put", "delete"]
        assert not ProjectTask.all_objects.filter(pk=link_id).exists()
        assert RowClocks.objects.get(row_id=link_id).tombstoned_at is not None
        other = services.create_project(alice, alice_home, title="Other")
        again = services.attach_task(alice, other, task)
        assert again.pk != link_id
        assert changes_of("projects.projecttask", again.pk)[0].fields["project_id"] == str(other.pk)


class TestBudget:
    def test_a_line_records_its_amounts_as_decimal_strings(
        self, alice: User, project: Project
    ) -> None:
        line = services.create_line(alice, project, label="Units", planned=Decimal("900"))
        (made,) = changes_of("projects.budgetline", line.pk)
        assert made.fields == {
            "project_id": str(project.pk),
            "label": "Units",
            "nature": "other",
            "planned": "900.00",
            "paid": "0.00",
        }
        services.update_line(alice, line, label="Units", planned="900.00", paid="120")
        assert changes_of("projects.budgetline", line.pk)[-1].fields == {"paid": "120.00"}

    def test_deleting_a_line_records_what_its_quotes_lose(
        self, alice: User, project: Project
    ) -> None:
        line = services.create_line(alice, project, label="Units", planned=Decimal("900"))
        accepted = services.create_quote(alice, project, amount=Decimal("850"))
        services.accept_quote(alice, accepted, line)
        assert changes_of("projects.quote", accepted.pk)[-1].fields == {
            "status": "accepted",
            "budget_line_id": str(line.pk),
        }
        other = services.create_quote(alice, project, amount=Decimal("10"))
        since = last_pk()
        services.delete_line(alice, line)
        released = changes_of("projects.quote", since=since)
        assert {c.row_id for c in released} == {accepted.pk}
        # Two updates of the service, each with the field it changed.
        assert [c.fields for c in released] == [{"status": "received"}, {"budget_line_id": None}]
        assert changes_of("projects.quote", other.pk, since=since) == []
        assert [c.kind for c in changes_of("projects.budgetline", line.pk, since=since)] == [
            "delete"
        ]

    def test_a_quote_records_its_dates_and_its_edits(
        self, alice: User, project: Project, media_root: Path
    ) -> None:
        quote = services.create_quote(
            alice, project, amount=Decimal("850"), issued_on=datetime.date(2026, 11, 2)
        )
        made = changes_of("projects.quote", quote.pk)[0].fields
        assert (made["issued_on"], made["amount"], made["valid_until"]) == (
            "2026-11-02",
            "850.00",
            None,
        )
        services.update_quote(alice, quote, amount=Decimal("850"), note="With delivery")
        assert changes_of("projects.quote", quote.pk)[-1].fields == {"note": "With delivery"}
        stored = services.add_file(
            alice, project, quote=quote, upload=upload(image_bytes(), "scan.jpg")
        )
        since = last_pk()
        services.delete_quote(alice, quote)
        deleted = {(c.entity, c.row_id) for c in Change.objects.filter(pk__gt=since)}
        assert deleted == {("projects.quote", quote.pk), ("projects.projectfile", stored.pk)}

    def test_an_envelope_records_its_amount_when_it_changes(
        self, alice: User, alice_home: Home
    ) -> None:
        envelope = services.set_envelope(alice, alice_home, year=2027, amount="5000")
        assert changes_of("projects.annualenvelope", envelope.pk)[0].fields == {
            "year": 2027,
            "amount": "5000.00",
        }
        services.set_envelope(alice, alice_home, year=2027, amount="5000.00")
        services.set_envelope(alice, alice_home, year=2027, amount="6000")
        assert [c.fields for c in changes_of("projects.annualenvelope", envelope.pk)][1:] == [
            {"amount": "6000.00"}
        ]


class TestFilesAndLinks:
    def test_a_file_records_its_description_not_its_place_on_the_disk(
        self, alice: User, project: Project, media_root: Path
    ) -> None:
        stored = services.add_file(alice, project, upload=upload(image_bytes()))
        made = changes_of("projects.projectfile", stored.pk)[0].fields
        assert made["project_id"] == str(project.pk)
        assert made["is_image"] is True
        assert "file" not in made
        assert "thumbnail" not in made
        services.delete_file(alice, stored)
        assert changes_of("projects.projectfile", stored.pk)[-1].kind == "delete"

    def test_the_reading_of_a_page_records_its_title_and_picture_not_its_state(
        self, alice: User, project: Project, media_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        link = links.add_link(alice, project, url="https://example.com/units")
        assert "preview_state" not in changes_of("projects.projectlink", link.pk)[0].fields
        ProjectLink.objects.filter(pk=link.pk).update(
            preview_state="running", preview_started_at=datetime.datetime.now(datetime.UTC)
        )
        link.refresh_from_db()
        found = preview.Preview(title="Kitchen units", image=image_bytes())
        since = last_pk()
        links._fill(link.pk, link.preview_started_at, found)
        link.refresh_from_db()
        assert link.image is not None
        (filled,) = changes_of("projects.projectlink", link.pk, since=since)
        assert filled.fields == {"title": "Kitchen units", "image_id": str(link.image.pk)}
        assert changes_of("projects.projectfile", link.image.pk, since=since)[0].kind == "put"
        picture = link.image
        since = last_pk()
        links.delete_link(alice, link)
        deleted = {(c.entity, c.row_id) for c in Change.objects.filter(pk__gt=since)}
        assert deleted == {("projects.projectlink", link.pk), ("projects.projectfile", picture.pk)}


class TestOtherWritePaths:
    def test_an_edit_through_the_api_records_only_what_it_changed(
        self, api_client: APIClient, alice: User, alice_home: Home, project: Project
    ) -> None:
        api_client.force_authenticate(alice)
        url = reverse(
            "api-v1:home-project-detail",
            kwargs={"home_id": alice_home.id, "project_id": project.pk},
        )
        since = last_pk()
        answer = api_client.patch(url, {"title": "Kitchen", "status": "planned"}, format="json")
        assert answer.status_code == 200, answer.content
        (edit,) = changes_of("projects.project", project.pk, since=since)
        assert edit.fields == {"status": "planned"}

    def test_a_restore_as_a_copy_records_every_row_under_its_new_id(
        self, alice: User, alice_home: Home, project: Project, kitchen: Room, media_root: Path
    ) -> None:
        line = services.create_line(alice, project, label="Units", planned=Decimal("900"))
        services.set_envelope(alice, alice_home, year=2027, amount="5000")
        services.add_task(alice, project, title="Measure")
        data = archive_bytes(alice_home)
        copy = restore.restore_bytes(alice, data, as_copy=True).home
        new_project = Project.objects.get(home=copy)
        new_room = Room.objects.get(home=copy, name="Kitchen")
        assert new_project.pk != project.pk
        put = {
            (c.entity, c.row_id): c.fields
            for c in Change.objects.filter(home=copy, entity__startswith="projects.")
        }
        assert put[("projects.project", new_project.pk)]["title"] == "Kitchen"
        new_line = BudgetLine.objects.get(home=copy)
        assert put[("projects.budgetline", new_line.pk)]["planned"] == "900.00"
        assert new_line.pk != line.pk
        envelope = AnnualEnvelope.objects.get(home=copy)
        assert put[("projects.annualenvelope", envelope.pk)] == {"year": 2027, "amount": "5000.00"}
        task_link = ProjectTask.objects.get(home=copy)
        assert put[("projects.projecttask", task_link.pk)]["project_id"] == str(new_project.pk)
        assert put[(ROOMS, rooms_row(new_project, new_room))] == {
            "project_id": str(new_project.pk),
            "room_id": str(new_room.pk),
        }


class TestRetention:
    def test_purging_a_project_leaves_no_clock_of_its_children_nor_their_files(
        self,
        alice: User,
        project: Project,
        kitchen: Room,
        media_root: Path,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        quote = services.create_quote(alice, project, amount=Decimal("850"))
        stored = services.add_file(alice, project, upload=upload(image_bytes()))
        on_disk = [Path(stored.file.path), Path(stored.thumbnail.path)]
        assert all(path.exists() for path in on_disk)
        # A child the deletion of the project did not touch: the purge takes it by cascade.
        ProjectFile.objects.filter(pk=stored.pk).update(deleted_at=None)
        RowClocks.objects.filter(row_id=stored.pk).update(tombstoned_at=None)
        services.delete_project(alice, project)
        ProjectFile.all_objects.filter(pk=stored.pk).update(deleted_at=None)
        RowClocks.objects.filter(row_id=stored.pk).update(tombstoned_at=None)
        later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=401)
        with django_capture_on_commit_callbacks(execute=True):
            retention.purge(later)
        assert not Project.all_objects.filter(pk=project.pk).exists()
        for row_id in (project.pk, quote.pk, stored.pk, rooms_row(project, kitchen)):
            assert not RowClocks.objects.filter(row_id=row_id).exists(), row_id
        assert not any(path.exists() for path in on_disk)

    def test_the_purge_keeps_what_a_live_row_holds(
        self, alice: User, alice_home: Home, project: Project, kitchen: Room
    ) -> None:
        other = services.create_project(alice, alice_home, title="Other", rooms=[kitchen])
        services.delete_project(alice, project)
        later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=401)
        retention.purge(later)
        assert RowClocks.objects.filter(row_id=other.pk).exists()
        assert RowClocks.objects.filter(row_id=rooms_row(other, kitchen)).exists()
        assert Room.objects.filter(pk=kitchen.pk).exists()


def test_the_carried_fields_leave_out_what_is_not_data_of_the_home() -> None:
    names = {f.name for f in changelog.carried_fields(ProjectLink)}
    assert names == {"project", "url", "title", "image"}
    assert {f.name for f in changelog.carried_fields(Quote)} >= {"budget_line", "status"}
    assert "file" not in {f.name for f in changelog.carried_fields(ProjectFile)}
