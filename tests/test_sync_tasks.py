"""The change log of the `tasks` app: every write records the fields it changed (lot M3c).

docs/adr/0077 (needs 3 to 5) and 0079: contractors, tasks, task materials, occurrences, draws
and attachments record their changes, only for the fields whose value changed; deletions are
tombstones, the occurrence an undo takes back included; the purge leaves nothing behind.
"""

import datetime
import hashlib
import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.homes.models import Home
from housecairn.rooms.models import Item
from housecairn.stock.models import Article
from housecairn.sync import retention, rows
from housecairn.sync.models import Change, RowClocks
from housecairn.tasks import materials, services
from housecairn.tasks.models import Attachment, Contractor, Occurrence, Task, TaskMaterial
from tests.rooms_support import upload
from tests.tasks_fixtures import PDF

pytestmark = pytest.mark.django_db
pytest_plugins = ("tests.tasks_fixtures",)


def changes_of(row: Any) -> list[Change]:
    return list(Change.objects.filter(row_id=row.pk).order_by("pos"))


def last_change(row: Any) -> Change:
    found = changes_of(row)
    assert found, f"no change recorded for {row!r}"
    return found[-1]


@pytest.fixture
def article(alice_home: Home) -> Article:
    return Article.objects.create(home=alice_home, name="AA battery")


@pytest.fixture
def plain_task(alice: User, alice_home: Home) -> Task:
    return services.create_task(alice, alice_home, title="Bleed the radiators")


# ---------- contractors ----------


def test_a_new_contractor_records_every_field(alice: User, alice_home: Home) -> None:
    contractor = services.create_contractor(alice, alice_home, name="Pipes Ltd", trade="plumber")
    change = last_change(contractor)
    assert (change.entity, change.kind) == ("tasks.contractor", "put")
    assert change.fields["name"] == "Pipes Ltd"
    assert change.fields["trade"] == "plumber"
    assert {"id", "home_id", "created_at", "updated_at", "deleted_at"}.isdisjoint(change.fields)


def test_an_edit_of_a_contractor_records_only_what_changed(alice: User, alice_home: Home) -> None:
    contractor = services.create_contractor(alice, alice_home, name="Pipes Ltd", phone="0102")
    services.update_contractor(alice, contractor, name="Pipes Ltd", phone="0199", email="")
    assert last_change(contractor).fields == {"phone": "0199"}


def test_an_edit_that_changes_nothing_records_nothing(alice: User, alice_home: Home) -> None:
    contractor = services.create_contractor(alice, alice_home, name="Pipes Ltd")
    count = Change.objects.count()
    services.update_contractor(alice, contractor, name="Pipes Ltd")
    assert Change.objects.count() == count


def test_deleting_a_contractor_takes_it_off_its_tasks_with_a_change(
    alice: User, alice_home: Home, plain_task: Task
) -> None:
    contractor = services.create_contractor(alice, alice_home, name="Pipes Ltd")
    services.update_task(alice, plain_task, contractor=contractor)
    assert last_change(plain_task).fields == {"contractor_id": str(contractor.pk)}
    services.delete_contractor(alice, contractor)
    assert last_change(plain_task).fields == {"contractor_id": None}
    assert last_change(contractor).kind == "delete"
    assert RowClocks.objects.get(row_id=contractor.pk).tombstoned_at is not None


def test_deleting_a_contractor_takes_it_off_the_item_contracts_with_a_change(
    alice: User, alice_home: Home, owner_item: Item
) -> None:
    contractor = services.create_contractor(alice, alice_home, name="Boilers Ltd")
    Item.all_objects.filter(pk=owner_item.pk).update(
        contract_contractor=contractor, contract_number="C-42"
    )
    services.delete_contractor(alice, contractor)
    owner_item.refresh_from_db()
    assert owner_item.contract_contractor_id is None
    assert owner_item.contract_number == "C-42"
    assert last_change(owner_item).fields == {"contract_contractor_id": None}


# ---------- tasks ----------


def test_a_new_task_records_the_task_and_its_first_occurrence(
    alice: User, alice_home: Home
) -> None:
    task = services.create_task(
        alice, alice_home, title="Smoke alarm", due_on=datetime.date(2026, 7, 1)
    )
    assert last_change(task).fields["title"] == "Smoke alarm"
    assert last_change(task).fields["series_start"] == "2026-07-01"
    occurrence = Occurrence.objects.get(task=task)
    first = last_change(occurrence)
    assert first.fields["task_id"] == str(task.pk)
    assert first.fields["due_on"] == "2026-07-01"
    assert first.fields["status"] == "planned"
    assert "visible_from" not in first.fields  # derived, never synced (mobile ADR 0003)


def test_an_edit_of_a_task_records_only_what_changed(alice: User, plain_task: Task) -> None:
    services.update_task(
        alice, plain_task, title="Bleed every radiator", priority="normal", notes=""
    )
    assert last_change(plain_task).fields == {"title": "Bleed every radiator"}


def test_an_object_changed_before_the_service_still_records_the_change(
    alice: User, plain_task: Task
) -> None:
    """A form may set its values on the object first: the database says what they were."""
    plain_task.title = "Bleed every radiator"
    services.update_task(alice, plain_task, title="Bleed every radiator")
    assert last_change(plain_task).fields == {"title": "Bleed every radiator"}


def test_amounts_are_decimal_strings(alice: User, plain_task: Task) -> None:
    services.update_task(alice, plain_task, labour_budget=Decimal("12.5"))
    assert last_change(plain_task).fields == {"labour_budget": "12.50"}


def test_the_edit_form_sends_every_field_and_records_one(
    signed_in: Client, alice_home: Home, plain_task: Task
) -> None:
    edit = reverse("tasks:edit", kwargs={"home_id": alice_home.id, "task_id": plain_task.id})
    form = {
        "title": "Bleed every radiator",
        "priority": "normal",
        "repeat_mode": "once",
        "repeat_interval": "1",
        "repeat_unit": "month",
        "hide_days_before": "0",
    }
    assert signed_in.post(edit, form).status_code == 302
    assert last_change(plain_task).fields == {"title": "Bleed every radiator"}


def test_the_api_records_only_the_field_it_patches(
    api_client: Any, alice: User, alice_home: Home, plain_task: Task
) -> None:
    api_client.force_authenticate(alice)
    detail = f"/api/v1/homes/{alice_home.id}/tasks/{plain_task.id}/"
    response = api_client.patch(detail, {"priority": "high"}, format="json")
    assert response.status_code == 200, response.content
    assert last_change(plain_task).fields == {"priority": "high"}


def test_moving_the_due_date_records_it_on_the_open_occurrence(
    alice: User, plain_task: Task
) -> None:
    services.update_task(alice, plain_task, due_on=datetime.date(2026, 9, 1))
    occurrence = Occurrence.objects.get(task=plain_task)
    assert last_change(occurrence).fields == {"due_on": "2026-09-01"}


def test_a_new_season_window_records_its_months(alice: User, alice_home: Home) -> None:
    task = services.create_task(alice, alice_home, title="Mow", season="summer")
    alice_home.hemisphere = "south"
    alice_home.save()
    assert services.resync_seasons(alice_home) == 1
    assert set(last_change(task).fields) == {"season_start_month", "season_end_month"}


def test_deleting_a_task_writes_a_tombstone_for_it_and_its_children(
    alice: User, owner_task: Task, media_root: Path
) -> None:
    attachment = services.add_attachment(alice, owner_task, upload=upload(PDF, "manual.pdf"))
    occurrence = Occurrence.objects.get(task=owner_task)
    services.delete_task(alice, owner_task)
    for row in (owner_task, occurrence, attachment):
        assert last_change(row).kind == "delete"
        assert RowClocks.objects.get(row_id=row.pk).tombstoned_at is not None


# ---------- occurrences ----------


def test_marking_done_records_the_closing_and_the_next_occurrence(
    alice: User, owner_task: Task, owner_occurrence: Occurrence, frozen_today: datetime.date
) -> None:
    closing = services.complete(alice, owner_occurrence, actual_cost=Decimal("4"), note="AAA")
    fields = last_change(owner_occurrence).fields
    assert fields["status"] == "done"
    assert fields["status_before"] == "planned"
    assert fields["completed_on"] == frozen_today.isoformat()
    assert str(fields["completed_by_id"]) == str(alice.pk)
    assert fields["actual_cost"] == "4.00"
    assert closing.next is not None
    assert last_change(closing.next).fields["follows_id"] == str(owner_occurrence.pk)


@pytest.fixture
def done_for_good(alice: User, alice_home: Home, frozen_today: datetime.date) -> Occurrence:
    """A task done once and for good: closed, with no occurrence after it."""
    task = services.create_task(
        alice, alice_home, title="Paint the fence", due_on=frozen_today - datetime.timedelta(days=3)
    )
    closing = services.complete(alice, Occurrence.objects.get(task=task))
    assert closing.next is None
    assert services.open_occurrence_of(task) is None
    return closing.occurrence


def test_a_rule_given_to_a_task_done_for_good_records_its_start_and_its_occurrence(
    alice: User, done_for_good: Occurrence, frozen_today: datetime.date
) -> None:
    task = done_for_good.task
    services.update_task(alice, task, repeat_mode="after_last", repeat_unit="month")
    task.refresh_from_db()
    reopened = services.open_occurrence_of(task)
    assert reopened is not None
    # A month after the last closing, the day it was done.
    assert task.series_start == reopened.due_on
    assert reopened.due_on is not None
    assert reopened.due_on > frozen_today
    # The edit puts the rule, then the reopening puts the start counted from the last closing.
    assert last_change(task).fields == {"series_start": task.series_start.isoformat()}
    created = last_change(reopened)
    assert created.kind == "put"
    assert created.fields["follows_id"] == str(done_for_good.pk)
    assert created.fields["due_on"] == reopened.due_on.isoformat()
    assert created.fields["task_id"] == str(task.pk)


def test_a_date_given_to_a_task_done_for_good_records_its_new_occurrence(
    alice: User, done_for_good: Occurrence, frozen_today: datetime.date
) -> None:
    task = done_for_good.task
    day = frozen_today + datetime.timedelta(days=10)
    services.update_task(alice, task, due_on=day)
    reopened = services.open_occurrence_of(task)
    assert reopened is not None
    assert last_change(task).fields == {"series_start": day.isoformat()}
    created = last_change(reopened)
    assert created.fields["follows_id"] == str(done_for_good.pk)
    assert created.fields["due_on"] == day.isoformat()


def test_deleting_an_account_takes_it_off_the_tasks_of_its_homes_with_a_change(
    alice: User, bob: User, alice_home: Home, frozen_today: datetime.date
) -> None:
    from housecairn.accounts import services as account_services
    from housecairn.homes.models import Membership, Role

    Membership.objects.create(home=alice_home, user=bob, role=Role.MEMBER)
    task = services.create_task(
        alice, alice_home, title="Sweep the chimney", assignee=bob, due_on=frozen_today
    )
    closing = services.complete(bob, Occurrence.objects.get(task=task))
    done = closing.occurrence
    other = services.create_task(alice, alice_home, title="Oil the hinges", assignee=alice)
    before = Change.objects.count()
    account_services.delete_account(bob)
    task.refresh_from_db()
    done.refresh_from_db()
    assert task.assignee_id is None
    assert done.completed_by_id is None
    assert [c.fields for c in changes_of(task)][-1] == {"assignee_id": None}
    assert [c.fields for c in changes_of(done)][-1] == {"completed_by_id": None}
    # One change per row the deletion empties, and none for a row that does not name it.
    assert Change.objects.count() - before == 2
    assert len(changes_of(other)) == 1


def test_postponing_records_the_new_date(
    alice: User, owner_occurrence: Occurrence, frozen_today: datetime.date
) -> None:
    services.postpone(alice, owner_occurrence, frozen_today + datetime.timedelta(days=3))
    assert set(last_change(owner_occurrence).fields) == {"due_on", "status", "postponed_from"}


def test_an_undo_deletes_the_next_occurrence_with_a_tombstone(
    alice: User, owner_task: Task, owner_occurrence: Occurrence, frozen_today: datetime.date
) -> None:
    closing = services.complete(alice, owner_occurrence)
    following = closing.next
    assert following is not None
    services.undo(alice, closing.occurrence)
    kept = Occurrence.all_objects.get(pk=following.pk)
    assert kept.deleted_at is not None
    assert kept.follows_id is None
    put, delete = changes_of(following)[-2:]
    assert (put.kind, put.fields) == ("put", {"follows_id": None})
    assert delete.kind == "delete"
    assert RowClocks.objects.get(row_id=following.pk).tombstoned_at is not None
    assert last_change(owner_occurrence).fields["status"] == "planned"
    # The closing can be made again: the deleted one no longer holds the occurrence it followed.
    again = services.complete(alice, Occurrence.objects.get(pk=owner_occurrence.pk))
    assert again.next is not None
    assert again.next.pk != following.pk


def test_completing_the_details_records_them(
    alice: User, owner_occurrence: Occurrence, frozen_today: datetime.date
) -> None:
    services.complete(alice, owner_occurrence)
    services.update_occurrence(alice, owner_occurrence, completion_note="Two AAA")
    assert last_change(owner_occurrence).fields == {"completion_note": "Two AAA"}


# ---------- materials and draws ----------


def test_material_lines_record_their_writes(
    alice: User, plain_task: Task, article: Article
) -> None:
    line = materials.add_material(alice, plain_task, article, Decimal("2"))
    assert last_change(line).fields["quantity"] == "2.00"
    materials.update_material(alice, line, quantity=Decimal("3"))
    assert last_change(line).fields == {"quantity": "3.00"}
    count = Change.objects.count()
    materials.set_materials(alice, plain_task, [(article, Decimal("3"))])
    assert Change.objects.count() == count
    materials.set_materials(alice, plain_task, [])
    assert last_change(line).kind == "delete"


def test_draws_record_their_writes_and_the_undo_ends_them(
    alice: User,
    alice_home: Home,
    owner_task: Task,
    owner_occurrence: Occurrence,
    article: Article,
    frozen_today: datetime.date,
) -> None:
    TaskMaterial.objects.create(
        home=alice_home, task=owner_task, article=article, quantity=Decimal("1")
    )
    services.complete(alice, owner_occurrence)
    draw = owner_occurrence.draws.get()
    assert last_change(draw).fields["wanted"] == "1.00"
    services.update_occurrence(alice, owner_occurrence, used={article.pk: Decimal("0")})
    assert last_change(draw).fields == {"wanted": "0.00"}
    services.undo(alice, Occurrence.objects.get(pk=owner_occurrence.pk))
    assert last_change(draw).kind == "delete"


# ---------- attachments ----------


def test_attachments_record_their_writes(alice: User, plain_task: Task, media_root: Path) -> None:
    attachment = services.add_attachment(alice, plain_task, upload=upload(PDF, "manual.pdf"))
    created = last_change(attachment).fields
    assert created["name"] == "manual.pdf"
    assert created["file"] == {
        "file": str(attachment.pk),
        "size": len(PDF),
        "type": "application/pdf",
        "width": None,
        "height": None,
    }
    assert "content_type" not in created
    services.update_attachment(alice, attachment, name="boiler.pdf")
    assert last_change(attachment).fields == {"name": "boiler.pdf"}
    services.delete_attachment(alice, attachment)
    assert last_change(attachment).kind == "delete"


# ---------- restore as a copy ----------


def test_a_copy_records_every_row_of_its_tasks(
    alice: User, alice_home: Home, owner_task: Task, article: Article, media_root: Path
) -> None:
    from housecairn.homes import restore
    from tests.data_support import archive_bytes

    materials.add_material(alice, owner_task, article, Decimal("1"))
    services.create_contractor(alice, alice_home, name="Pipes Ltd")
    copy = restore.restore_bytes(alice, archive_bytes(alice_home), as_copy=True).home
    for model in (Task, Occurrence, TaskMaterial, Contractor):
        assert model.all_objects.filter(home=copy).exists()
        for row in model.all_objects.filter(home=copy):
            change = last_change(row)
            assert change.home_id == copy.pk
            assert change.kind == "put"


def _with_deleted_task(data: bytes) -> bytes:
    """The archive with `deleted_at` forged on its task, and a manifest that agrees with it."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    document = json.loads(files["data/tasks.json"])
    document["sections"]["tasks"][0]["deleted_at"] = "2026-01-05T10:00:00+00:00"
    files["data/tasks.json"] = json.dumps(document).encode()
    manifest = json.loads(files["manifest.json"])
    manifest["entries"] = {
        name: {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
        for name, body in files.items()
        if name != "manifest.json"
    }
    files["manifest.json"] = json.dumps(manifest).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return out.getvalue()


def test_a_restore_brings_back_no_deleted_row_so_it_records_no_tombstone(
    alice: User, alice_home: Home, owner_task: Task, media_root: Path
) -> None:
    """An archive holds live rows only: a deleted row of the home is not restored, and a
    `deleted_at` forged in the archive is not read, so a restore has no deletion to record."""
    from housecairn.homes import restore
    from tests.data_support import archive_bytes

    gone = services.create_contractor(alice, alice_home, name="Gone Ltd")
    services.delete_contractor(alice, gone)
    data = _with_deleted_task(archive_bytes(alice_home))
    copy = restore.restore_bytes(alice, data, as_copy=True).home
    assert not Contractor.all_objects.filter(home=copy).exists()
    restored = Task.all_objects.get(home=copy)
    assert restored.deleted_at is None
    assert [c.kind for c in changes_of(restored)] == ["put"]
    assert RowClocks.objects.get(row_id=restored.pk).tombstoned_at is None
    assert not Change.objects.filter(home=copy, kind="delete").exists()


# ---------- the purge ----------


def _expire(row: Any) -> None:
    old = retention.limit() - datetime.timedelta(days=1)
    RowClocks.objects.filter(row_id=row.pk).update(tombstoned_at=old)


def test_the_purge_of_a_task_leaves_no_clock_and_no_file_of_its_children(
    alice: User,
    owner_task: Task,
    media_root: Path,
    django_capture_on_commit_callbacks: Any,
) -> None:
    attachment = services.add_attachment(alice, owner_task, upload=upload(PDF, "manual.pdf"))
    occurrence = Occurrence.objects.get(task=owner_task)
    stored = Path(attachment.file.path)
    # A phone deletes the task alone: its children are hidden on read, still on the disk.
    rows.soft_delete(owner_task)
    assert stored.exists()
    _expire(owner_task)
    with django_capture_on_commit_callbacks(execute=True):
        purged = retention.purge()
    assert purged.rows == 3  # the task, its occurrence and its attachment
    assert not Task.all_objects.filter(pk=owner_task.pk).exists()
    assert not Attachment.all_objects.filter(pk=attachment.pk).exists()
    for row in (owner_task, occurrence, attachment):
        assert not RowClocks.objects.filter(row_id=row.pk).exists()
    assert not stored.exists()


def test_the_purge_records_a_reference_it_empties(
    alice: User, alice_home: Home, plain_task: Task
) -> None:
    contractor = services.create_contractor(alice, alice_home, name="Pipes Ltd")
    services.delete_contractor(alice, contractor)
    # A live task still naming the deleted contractor (written before the change log).
    Task.all_objects.filter(pk=plain_task.pk).update(contractor=contractor)
    _expire(contractor)
    retention.purge()
    assert not Contractor.all_objects.filter(pk=contractor.pk).exists()
    plain_task.refresh_from_db()
    assert plain_task.contractor_id is None
    assert last_change(plain_task).fields == {"contractor_id": None}
