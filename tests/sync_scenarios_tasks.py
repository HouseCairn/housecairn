"""Scenarios of the guard of the change log for the app `tasks` (lot M3c, docs/adr/0079)."""

import datetime
import uuid
from collections.abc import Callable
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile

from housecairn.accounts import services as account_services
from housecairn.accounts.models import User
from housecairn.homes.models import Home, Membership, Role
from housecairn.stock.models import Article
from housecairn.tasks import materials, services
from housecairn.tasks.models import (
    Attachment,
    Contractor,
    MaterialDraw,
    Occurrence,
    Status,
    Task,
    TaskMaterial,
)

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
DAY = datetime.date(2026, 1, 5)

Scenario = Callable[[Home], None]


def _owner(home: Home) -> User:
    membership = Membership.objects.filter(home=home, role=Role.OWNER).select_related("user")
    return membership.get().user


def _task(home: Home, **fields: object) -> Task:
    """A task and its open occurrence, written straight in the tables: no change recorded."""
    task = Task.objects.create(home=home, title="Clean the gutters", **fields)
    Occurrence.objects.create(home=home, task=task, due_on=DAY)
    return task


def _occurrence(task: Task) -> Occurrence:
    return Occurrence.objects.get(task=task, status=Status.PLANNED)


def _article(home: Home) -> Article:
    return Article.objects.create(home=home, name="Gutter guard")


def contractor_save(home: Home) -> None:
    services.create_contractor(_owner(home), home, name="Roof & Co")


def contractor_soft_delete(home: Home) -> None:
    services.delete_contractor(_owner(home), Contractor.objects.create(home=home, name="Roofer"))


def task_save(home: Home) -> None:
    services.create_task(_owner(home), home, title="Bleed the radiators")


def task_queryset_update(home: Home) -> None:
    """Deleting a contractor takes it off its tasks by a queryset update."""
    contractor = Contractor.objects.create(home=home, name="Roofer")
    _task(home, contractor=contractor)
    services.delete_contractor(_owner(home), contractor)


def task_soft_delete(home: Home) -> None:
    services.delete_task(_owner(home), _task(home))


def material_save(home: Home) -> None:
    materials.add_material(_owner(home), _task(home), _article(home), Decimal("2"))


def material_soft_delete(home: Home) -> None:
    task = _task(home)
    line = TaskMaterial.objects.create(
        home=home, task=task, article=_article(home), quantity=Decimal("1")
    )
    materials.remove_material(_owner(home), line)


def occurrence_save(home: Home) -> None:
    services.skip(_owner(home), _occurrence(_task(home)))


def occurrence_queryset_update(home: Home) -> None:
    """Deleting an account takes it off the occurrences it closed, by a queryset update."""
    member = User.objects.create_user(email=f"{uuid.uuid4().hex}@example.com", password=None)
    Membership.objects.create(home=home, user=member, role=Role.MEMBER)
    occurrence = _occurrence(_task(home, assignee=member))
    Occurrence.all_objects.filter(pk=occurrence.pk).update(
        status=Status.DONE, completed_on=DAY, completed_by=member
    )
    account_services.delete_account(member)


def occurrence_soft_delete(home: Home) -> None:
    """Deleting a task soft-deletes its occurrences by a queryset."""
    services.delete_task(_owner(home), _task(home))


def draw_save(home: Home) -> None:
    task = _task(home)
    TaskMaterial.objects.create(home=home, task=task, article=_article(home), quantity=Decimal("1"))
    services.complete(_owner(home), _occurrence(task), on=DAY)


def draw_soft_delete(home: Home) -> None:
    """An undo ends the draws of the closing."""
    task = _task(home)
    occurrence = _occurrence(task)
    Occurrence.all_objects.filter(pk=occurrence.pk).update(
        status=Status.DONE, status_before=Status.PLANNED, completed_on=DAY
    )
    MaterialDraw.objects.create(
        home=home,
        occurrence=occurrence,
        article=_article(home),
        wanted=Decimal("1"),
        drawn=Decimal("0"),
    )
    services.undo(_owner(home), Occurrence.objects.get(pk=occurrence.pk))


def attachment_save(home: Home) -> None:
    upload = SimpleUploadedFile("manual.pdf", PDF, content_type="application/pdf")
    services.add_attachment(_owner(home), _task(home), upload=upload)


def attachment_soft_delete(home: Home) -> None:
    task = _task(home)
    attachment = Attachment.objects.create(
        home=home, task=task, name="manual.pdf", content_type="application/pdf", size_bytes=1
    )
    services.delete_attachment(_owner(home), attachment)


SCENARIOS: dict[tuple[str, str], Scenario] = {
    ("tasks.Contractor", "save"): contractor_save,
    ("tasks.Contractor", "soft_delete"): contractor_soft_delete,
    ("tasks.Task", "save"): task_save,
    ("tasks.Task", "queryset_update"): task_queryset_update,
    ("tasks.Task", "soft_delete"): task_soft_delete,
    ("tasks.TaskMaterial", "save"): material_save,
    ("tasks.TaskMaterial", "soft_delete"): material_soft_delete,
    ("tasks.Occurrence", "save"): occurrence_save,
    ("tasks.Occurrence", "queryset_update"): occurrence_queryset_update,
    ("tasks.Occurrence", "soft_delete"): occurrence_soft_delete,
    ("tasks.MaterialDraw", "save"): draw_save,
    ("tasks.MaterialDraw", "soft_delete"): draw_soft_delete,
    ("tasks.Attachment", "save"): attachment_save,
    ("tasks.Attachment", "soft_delete"): attachment_soft_delete,
}

_NO_HARD_DELETE = (
    "no write path erases it: deletions are soft, the undo included (docs/adr/0077, need 4); "
    "only the retention purge and the erasing of a home remove rows, with their changes"
)
_NO_QUERYSET_UPDATE = "no write path changes it by a queryset update"

NOT_APPLICABLE: dict[tuple[str, str], str] = {
    **{
        (label, "hard_delete"): _NO_HARD_DELETE
        for label in (
            "tasks.Contractor",
            "tasks.Task",
            "tasks.TaskMaterial",
            "tasks.Occurrence",
            "tasks.MaterialDraw",
            "tasks.Attachment",
        )
    },
    **{
        (label, "queryset_update"): _NO_QUERYSET_UPDATE
        for label in (
            "tasks.Contractor",
            "tasks.TaskMaterial",
            "tasks.MaterialDraw",
            "tasks.Attachment",
        )
    },
}
