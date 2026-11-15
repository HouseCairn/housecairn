"""The writes of the projects for the guard of the change log (tests/test_sync_guard.py, lot M3e).

Each scenario runs the code the application runs for one kind of write of one model, and
nothing else. What a scenario needs before the write (a project, a line, a link) is made with
the ORM, which records no change: a change of the same model made while preparing would hide
a write that records none.
"""

import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from decimal import Decimal

from django.test import override_settings

from housecairn.accounts.models import User
from housecairn.homes.models import Home, Membership, Role
from housecairn.projects import links, services
from housecairn.projects.models import (
    BudgetLine,
    Project,
    ProjectFile,
    ProjectLink,
    ProjectTask,
    Quote,
    QuoteStatus,
)
from housecairn.rooms.models import Room
from housecairn.tasks import services as task_services
from tests.rooms_support import image_bytes, upload

Scenario = Callable[[Home], None]


def _owner(home: Home) -> User:
    user: User = Membership.objects.get(home=home, role=Role.OWNER).user
    return user


def _project(home: Home) -> Project:
    project: Project = Project.objects.create(home=home, title="Bathroom")
    return project


@contextmanager
def _media() -> Iterator[None]:
    with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
        yield


def _project_save(home: Home) -> None:
    services.create_project(_owner(home), home, title="Bathroom")


def _project_soft_delete(home: Home) -> None:
    services.delete_project(_owner(home), _project(home))


def _line_save(home: Home) -> None:
    services.create_line(_owner(home), _project(home), label="Tiles", planned=Decimal("300"))


def _line_soft_delete(home: Home) -> None:
    project = _project(home)
    line = BudgetLine.objects.create(home=home, project=project, label="Tiles")
    services.delete_line(_owner(home), line)


def _quote_save(home: Home) -> None:
    services.create_quote(_owner(home), _project(home), amount=Decimal("620"))


def _quote_queryset_update(home: Home) -> None:
    project = _project(home)
    line = BudgetLine.objects.create(home=home, project=project, label="Tiles")
    Quote.objects.create(
        home=home, project=project, status=QuoteStatus.ACCEPTED, budget_line=line
    )
    # Deleting the line sends its accepted quotes back to Received, by a queryset update.
    services.delete_line(_owner(home), line)


def _quote_soft_delete(home: Home) -> None:
    quote = Quote.objects.create(home=home, project=_project(home))
    services.delete_quote(_owner(home), quote)


def _file_save(home: Home) -> None:
    with _media():
        services.add_file(_owner(home), _project(home), upload=upload(image_bytes()))


def _file_soft_delete(home: Home) -> None:
    stored = ProjectFile.objects.create(
        home=home, project=_project(home), name="photo", content_type="image/jpeg", size_bytes=1
    )
    services.delete_file(_owner(home), stored)


def _link_save(home: Home) -> None:
    links.add_link(_owner(home), _project(home), url="https://example.com/tiles")


def _link_soft_delete(home: Home) -> None:
    link = ProjectLink.objects.create(home=home, project=_project(home), url="https://example.com")
    links.delete_link(_owner(home), link)


def _task_link_save(home: Home) -> None:
    owner = _owner(home)
    task = task_services.create_task(owner, home, title="Seal the bath")
    services.attach_task(owner, _project(home), task)


def _task_link_hard_delete(home: Home) -> None:
    owner = _owner(home)
    task = task_services.create_task(owner, home, title="Seal the bath")
    link = ProjectTask.objects.create(home=home, project=_project(home), task=task)
    services.detach_task(owner, link)


def _envelope_save(home: Home) -> None:
    services.set_envelope(_owner(home), home, year=2027, amount=Decimal("5000"))


def _rooms_save(home: Home) -> None:
    room = Room.objects.create(home=home, name="Bathroom")
    services.update_project(_owner(home), _project(home), rooms=[room])


def _rooms_hard_delete(home: Home) -> None:
    project = _project(home)
    project.rooms.add(Room.objects.create(home=home, name="Bathroom"))
    services.update_project(_owner(home), project, rooms=[])


SCENARIOS: dict[tuple[str, str], Scenario] = {
    ("projects.Project", "save"): _project_save,
    ("projects.Project", "soft_delete"): _project_soft_delete,
    ("projects.BudgetLine", "save"): _line_save,
    ("projects.BudgetLine", "soft_delete"): _line_soft_delete,
    ("projects.Quote", "save"): _quote_save,
    ("projects.Quote", "queryset_update"): _quote_queryset_update,
    ("projects.Quote", "soft_delete"): _quote_soft_delete,
    ("projects.ProjectFile", "save"): _file_save,
    ("projects.ProjectFile", "soft_delete"): _file_soft_delete,
    ("projects.ProjectLink", "save"): _link_save,
    ("projects.ProjectLink", "soft_delete"): _link_soft_delete,
    ("projects.ProjectTask", "save"): _task_link_save,
    ("projects.ProjectTask", "hard_delete"): _task_link_hard_delete,
    ("projects.AnnualEnvelope", "save"): _envelope_save,
    ("projects.Project_rooms", "save"): _rooms_save,
    ("projects.Project_rooms", "hard_delete"): _rooms_hard_delete,
}

_SOFT_THEN_PURGED = "never deleted for good: soft deletion, then the retention purge"
_NO_QUERYSET_UPDATE = "no write path updates these rows by a queryset"

NOT_APPLICABLE: dict[tuple[str, str], str] = {
    ("projects.Project", "queryset_update"): _NO_QUERYSET_UPDATE,
    ("projects.Project", "hard_delete"): _SOFT_THEN_PURGED,
    ("projects.BudgetLine", "queryset_update"): _NO_QUERYSET_UPDATE,
    ("projects.BudgetLine", "hard_delete"): _SOFT_THEN_PURGED,
    ("projects.Quote", "hard_delete"): _SOFT_THEN_PURGED,
    ("projects.ProjectFile", "queryset_update"): _NO_QUERYSET_UPDATE,
    ("projects.ProjectFile", "hard_delete"): _SOFT_THEN_PURGED,
    ("projects.ProjectLink", "queryset_update"): (
        "its queryset updates write where the reading of the page stands (`preview_state`), "
        "which is not data of the home"
    ),
    ("projects.ProjectLink", "hard_delete"): _SOFT_THEN_PURGED,
    ("projects.ProjectTask", "queryset_update"): _NO_QUERYSET_UPDATE,
    ("projects.ProjectTask", "soft_delete"): (
        "taking a task out deletes its link for good, with a tombstone, so that the task "
        "may join another project"
    ),
    ("projects.AnnualEnvelope", "queryset_update"): _NO_QUERYSET_UPDATE,
    ("projects.AnnualEnvelope", "soft_delete"): "an envelope is set again, never deleted",
    ("projects.AnnualEnvelope", "hard_delete"): "an envelope is set again, never deleted",
}
