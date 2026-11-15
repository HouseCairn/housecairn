"""Business rules of tasks, occurrences, contractors and attachments (docs/adr/0002, 0031-0034).

Web views and API views call these functions; neither holds rules of its own.
Reading is limited to the user's homes by the views (`housecairn.homes.scoping`);
writing is checked here, by role: owners and members write, read-only reads.
"""

import datetime
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, SuspiciousOperation, ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from housecairn.accounts.models import User
from housecairn.audit import services as audit
from housecairn.core.text import without_control_characters
from housecairn.homes import quotas
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Membership
from housecairn.instance.config import instance_zone
from housecairn.rooms import images
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Item, Room
from housecairn.rooms.services import _read_upload
from housecairn.sync import rows as changelog
from housecairn.tasks import durations, files, materials, recurrence
from housecairn.tasks.models import (
    MAX_ATTACHMENTS_PER_TASK,
    OPEN_STATUSES,
    Attachment,
    Contractor,
    Occurrence,
    RepeatMode,
    Season,
    Status,
    Task,
    Trade,
)
from housecairn.tasks.storage import attachment_storage

logger = logging.getLogger(__name__)

AnyUser = User | AnonymousUser

TASK_FIELDS = frozenset(
    {
        "title",
        "room",
        "item",
        "priority",
        "estimated_minutes",
        "labour_budget",
        "material_budget",
        "notes",
        "procedure",
        "assignee",
        "contractor",
        "repeat_mode",
        "repeat_unit",
        "repeat_interval",
        "season",
        "season_start_month",
        "season_end_month",
        "hide_days_before",
    }
)
RULE_FIELDS = frozenset(
    {
        "repeat_mode",
        "repeat_unit",
        "repeat_interval",
        "season",
        "season_start_month",
        "season_end_month",
    }
)
_SEASON_FIELDS = frozenset({"season", "season_start_month", "season_end_month"})
CONTRACTOR_FIELDS = frozenset({"name", "trade", "trade_other", "phone", "email", "address", "note"})
_CONTRACTOR_LINE_FIELDS = frozenset({"name", "trade_other", "phone", "email"})
COMPLETION_FIELDS = frozenset({"completed_on", "actual_cost", "completion_note"})
_UNSET: Any = object()
DATE_SPAN_YEARS = 100


def today() -> datetime.date:
    """Today in the time zone of the instance (docs/adr/0032, 0042), in a worker too."""
    return timezone.localdate(timezone=instance_zone())


def date_bounds(day: datetime.date | None = None) -> tuple[datetime.date, datetime.date]:
    """The earliest and the latest date a task can carry: `DATE_SPAN_YEARS` around `day` (today).

    Every due, postponement, series start and completion date is held to this window
    (docs/adr/0032): a date of year 1 is of no use and makes a series long to count.
    """
    day = day or today()
    span = relativedelta(years=DATE_SPAN_YEARS)
    return day - span, day + span


def _check_date(value: datetime.date | None, field: str) -> None:
    if value is None:
        return
    low, high = date_bounds()
    if not low <= value <= high:
        raise ValidationError(
            {
                field: _("Choose a date within %(years)d years of today.")
                % {"years": DATE_SPAN_YEARS}
            }
        )


def _require_write(actor: AnyUser, home: Home) -> None:
    if not home_services.can_write(actor, home):
        raise PermissionDenied


def _apply(instance: models.Model, allowed: frozenset[str], changes: dict[str, Any]) -> None:
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"Cannot change: {', '.join(sorted(unknown))}")
    for field, value in changes.items():
        setattr(instance, field, value)


# ---------- reading ----------


def tasks_of(home: Home) -> models.QuerySet[Task]:
    return Task.objects.filter(home=home).select_related("room", "item", "assignee", "contractor")


def contractors_of(home: Home) -> models.QuerySet[Contractor]:
    return Contractor.objects.filter(home=home)


def occurrences_of(home: Home) -> models.QuerySet[Occurrence]:
    """Occurrences of the home's tasks, in no particular order: arrears first is `order_open`."""
    return Occurrence.objects.filter(home=home, task__deleted_at__isnull=True).select_related(
        "task", "task__room", "task__item", "task__assignee", "completed_by"
    )


def attachments_of(home: Home) -> models.QuerySet[Attachment]:
    return Attachment.objects.filter(home=home, task__deleted_at__isnull=True)


def order_open(rows: models.QuerySet[Occurrence]) -> models.QuerySet[Occurrence]:
    """The oldest due date first, so that the arrears stay on top; no date last."""
    return rows.order_by(models.F("due_on").asc(nulls_last=True), "created_at", "id")


def visible(rows: models.QuerySet[Occurrence], day: datetime.date) -> models.QuerySet[Occurrence]:
    """Occurrences not hidden at `day` (a task can be hidden until N days before its date)."""
    return rows.filter(models.Q(visible_from__isnull=True) | models.Q(visible_from__lte=day))


def open_occurrences(
    home: Home, *, day: datetime.date | None = None, include_hidden: bool = False
) -> models.QuerySet[Occurrence]:
    rows = occurrences_of(home).filter(status__in=OPEN_STATUSES)
    if not include_hidden:
        rows = visible(rows, day or today())
    return order_open(rows)


def overdue(home: Home, *, day: datetime.date | None = None) -> models.QuerySet[Occurrence]:
    """Open occurrences whose date has passed, the oldest first (queries for the next screens)."""
    return order_open(
        visible(occurrences_of(home), day or today()).filter(
            status__in=OPEN_STATUSES, due_on__lt=day or today()
        )
    )


def upcoming(
    home: Home, *, days: int, day: datetime.date | None = None
) -> models.QuerySet[Occurrence]:
    """Open occurrences due from `day` on, up to `days` days ahead."""
    start = day or today()
    return order_open(
        visible(occurrences_of(home), start).filter(
            status__in=OPEN_STATUSES,
            due_on__gte=start,
            due_on__lte=start + datetime.timedelta(days=days),
        )
    )


def done(home: Home) -> models.QuerySet[Occurrence]:
    """Closed occurrences (done or skipped), the most recent first."""
    return (
        occurrences_of(home)
        .filter(status__in=[Status.DONE, Status.SKIPPED])
        .order_by("-completed_on", "-updated_at", "-id")
    )


def open_occurrence_of(task: Task) -> Occurrence | None:
    current: Occurrence | None = task.occurrences.filter(status__in=OPEN_STATUSES).first()
    return current


def upcoming_dates(task: Task, count: int = 3) -> list[datetime.date]:
    """The dates after the open occurrence, were it closed on time (for the task page)."""
    current = open_occurrence_of(task)
    if not task.repeats or current is None or current.due_on is None:
        return []
    return recurrence.preview(
        task.rule, anchor=task.series_start, due_on=current.due_on, count=count
    )


# ---------- contractors ----------


def _one_line(value: str) -> str:
    return " ".join(without_control_characters(" ".join(value.split())).split())


def _lines(value: str) -> str:
    """Several lines, each without control characters; the breaks stay."""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(without_control_characters(line).rstrip() for line in text.split("\n")).strip()


def _apply_contractor(contractor: Contractor, changes: dict[str, Any]) -> None:
    unknown = set(changes) - CONTRACTOR_FIELDS
    if unknown:
        raise ValueError(f"Cannot change: {', '.join(sorted(unknown))}")
    for field, value in changes.items():
        if isinstance(value, str):
            value = _one_line(value) if field in _CONTRACTOR_LINE_FIELDS else _lines(value)
        setattr(contractor, field, value)
    if contractor.trade != Trade.OTHER:
        contractor.trade_other = ""


@transaction.atomic
def create_contractor(actor: AnyUser, home: Home, **fields: Any) -> Contractor:
    _require_write(actor, home)
    contractor = Contractor(home=home)
    _apply_contractor(contractor, fields)
    contractor.full_clean()
    contractor.save()
    changelog.record_created([contractor])
    audit.record_object("contractor.created", actor=actor, home_id=home.id, title=contractor.name)
    return contractor


@transaction.atomic
def update_contractor(actor: AnyUser, contractor: Contractor, **changes: Any) -> Contractor:
    _require_write(actor, contractor.home)
    before = changelog.before(contractor)
    _apply_contractor(contractor, changes)
    contractor.full_clean()
    contractor.save()
    changelog.record_saved(contractor, before)
    audit.record_object(
        "contractor.updated",
        actor=actor,
        home_id=contractor.home_id,
        title=contractor.name,
        fields=sorted(changes),
    )
    return contractor


@transaction.atomic
def delete_contractor(actor: AnyUser, contractor: Contractor) -> None:
    _require_write(actor, contractor.home)
    changelog.update_queryset(Task.objects.filter(contractor=contractor), contractor=None)
    # The contract of an item loses its provider and keeps its number and its link.
    room_services.clear_contract_contractor(contractor)
    changelog.soft_delete(contractor)
    audit.record_object(
        "contractor.deleted", actor=actor, home_id=contractor.home_id, title=contractor.name
    )


def contractor_named(actor: AnyUser, home: Home, name: str) -> Contractor | None:
    """The contractor of the home with this name, made on the spot if there is none yet.

    The single field of a task (docs/adr/0045): a name picked from the list finds its
    contractor, a new name creates one. Case is not a difference; empty means nobody.
    """
    wanted = _one_line(name)
    if not wanted:
        return None
    for known in contractors_of(home):
        if known.name.casefold() == wanted.casefold():
            return known
    return create_contractor(actor, home, name=wanted)


def tasks_of_contractor(contractor: Contractor) -> models.QuerySet[Task]:
    return Task.objects.filter(home=contractor.home_id, contractor=contractor).select_related(
        "room"
    )


# ---------- tasks ----------


def _sync_season(task: Task, changes: dict[str, Any]) -> None:
    """Make the months of the task agree with its season (docs/adr/0045).

    A predefined season takes the window of the hemisphere of the home. Months given without
    a season (a client that predates seasons) make a custom one; no season means no months.
    """
    if "season" not in changes and {"season_start_month", "season_end_month"} & set(changes):
        given = task.season_start_month is not None or task.season_end_month is not None
        task.season = Season.CUSTOM if given else ""
    legacy = recurrence.legacy_season(task.season, task.home.hemisphere)
    if legacy is not None:  # the heating season of a client of before: its months, custom
        task.season = Season.CUSTOM
        task.season_start_month, task.season_end_month = legacy
    window = recurrence.season_window(task.season, task.home.hemisphere)
    if window is not None:
        task.season_start_month, task.season_end_month = window
    elif not task.season:
        task.season_start_month = task.season_end_month = None


@transaction.atomic
def resync_seasons(home: Home) -> int:
    """Give the tasks of a home on a predefined season the window of its hemisphere."""
    changed = 0
    for task in Task.objects.filter(home=home, season__in=recurrence.PREDEFINED_SEASONS):
        window = recurrence.season_window(task.season, home.hemisphere)
        if window is not None and window != (task.season_start_month, task.season_end_month):
            before = changelog.before(task)
            task.season_start_month, task.season_end_month = window
            task.save(update_fields=["season_start_month", "season_end_month", "updated_at"])
            changelog.record_saved(task, before)
            changed += 1
    return changed


def _check_estimate(task: Task, *, before: int | None = None, creating: bool = True) -> None:
    """The estimated duration is between a minute and `durations.MAX_MINUTES` (ADR 0045).

    Held only when it is set or changed: a task kept from before the bound (a duration of 0
    was accepted) can still be edited for something else.
    """
    minutes = task.estimated_minutes
    if not creating and minutes == before:
        return
    if minutes is not None and not durations.within_bounds(minutes):
        raise ValidationError({"estimated_minutes": durations.bounds_message()})


def _check_links(home: Home, task: Task) -> None:
    """Room, item and assignee of the task belong to its home; the room follows the item."""
    if task.item is not None and not task.room_id:
        task.room = task.item.room
    if (
        task.assignee_id
        and not Membership.objects.filter(home=home, user_id=task.assignee_id).exists()
    ):
        raise ValidationError({"assignee": _("This person is not a member of this home.")})


def _visible_from(task: Task, due_on: datetime.date | None) -> datetime.date | None:
    if due_on is None or not task.hide_days_before:
        return None
    try:
        return due_on - datetime.timedelta(days=task.hide_days_before)
    except OverflowError:  # a date at the start of the calendar: shown from the first day
        return datetime.date.min


def _check_due(task: Task, due_on: datetime.date | None) -> None:
    if due_on is None and task.repeat_mode == RepeatMode.FIXED:
        raise ValidationError({"due_on": _("A task that repeats on a fixed date needs a date.")})


@transaction.atomic
def create_task(
    actor: AnyUser, home: Home, *, due_on: datetime.date | None = None, **fields: Any
) -> Task:
    """Create a task and its first occurrence (due on `due_on`, or with no date)."""
    _require_write(actor, home)
    task = Task(home=home)
    _apply(task, TASK_FIELDS, fields)
    _sync_season(task, fields)
    _check_estimate(task)
    _check_links(home, task)
    _check_date(due_on, "due_on")
    _check_due(task, due_on)
    task.series_start = due_on
    task.full_clean()
    task.save()
    changelog.record_created([task])
    first = Occurrence.objects.create(
        home=home, task=task, due_on=due_on, visible_from=_visible_from(task, due_on)
    )
    changelog.record_created([first])
    audit.record_object("task.created", actor=actor, home_id=home.id, title=task.title)
    return task


@transaction.atomic
def update_task(
    actor: AnyUser, task: Task, *, due_on: datetime.date | None = _UNSET, **changes: Any
) -> Task:
    """Change a task. `due_on`, if given, moves the occurrence that is open."""
    _require_write(actor, task.home)
    before = changelog.before(task)
    estimate_before = task.estimated_minutes
    _apply(task, TASK_FIELDS, changes)
    if _SEASON_FIELDS & set(changes):
        _sync_season(task, changes)
    _check_estimate(task, before=estimate_before, creating=False)
    current = open_occurrence_of(task)
    new_due = (current.due_on if current else None) if due_on is _UNSET else due_on
    _check_links(task.home, task)
    if due_on is not _UNSET:
        _check_date(due_on, "due_on")
    _check_due(task, new_due)
    if RULE_FIELDS & set(changes) or due_on is not _UNSET:
        task.series_start = new_due
    task.full_clean()
    task.save()
    changelog.record_saved(task, before)
    if current is None:
        # A task that was done for good comes back when it gets a date or a rule.
        if new_due is not None or task.repeats:
            _reopen(task, new_due)
    elif due_on is not _UNSET or "hide_days_before" in changes:
        current_before = changelog.before(current)
        current.due_on = new_due
        current.visible_from = _visible_from(task, new_due)
        if due_on is not _UNSET and current.status == Status.POSTPONED:
            current.status = Status.PLANNED
            current.postponed_from = None
        current.save(
            update_fields=["due_on", "visible_from", "status", "postponed_from", "updated_at"]
        )
        changelog.record_saved(current, current_before)
    changed = sorted([*changes, *([] if due_on is _UNSET else ["due_on"])])
    audit.record_object(
        "task.updated", actor=actor, home_id=task.home_id, title=task.title, fields=changed
    )
    return task


def _reopen(task: Task, due_on: datetime.date | None) -> None:
    """Give a task with no open occurrence the one it lacks (docs/adr/0031).

    With no date given, a rule that counts from the last time starts from the last
    closing; a task that never had a date stays without one.
    """
    last = (
        task.occurrences.filter(
            status__in=[Status.DONE, Status.SKIPPED], next_occurrence__isnull=True
        )
        .order_by("-completed_on", "-updated_at", "-id")
        .first()
    )
    if due_on is None and last is not None and last.completed_on is not None:
        due_on = recurrence.next_due(
            task.rule, anchor=None, due_on=last.due_on, done_on=last.completed_on
        )
        before = changelog.before(task)
        task.series_start = due_on
        task.save(update_fields=["series_start", "updated_at"])
        changelog.record_saved(task, before)
    reopened = Occurrence.objects.create(
        home=task.home,
        task=task,
        due_on=due_on,
        visible_from=_visible_from(task, due_on),
        follows=last,
    )
    changelog.record_created([reopened])


def forget_person(user: User) -> int:
    """Take an account that is being deleted off the tasks and occurrences that name it.

    The database would empty these references (`SET_NULL`) without a change; done here, each
    row records it, so that a phone does not keep a person who no longer exists
    (docs/adr/0077). The number of rows written.
    """
    with transaction.atomic():
        count = changelog.update_queryset(Task.all_objects.filter(assignee=user), assignee=None)
        count += changelog.update_queryset(
            Occurrence.all_objects.filter(completed_by=user), completed_by=None
        )
    return count


@transaction.atomic
def delete_task(actor: AnyUser, task: Task) -> None:
    """Soft-delete the task with its occurrences and attachments."""
    _require_write(actor, task.home)
    changelog.soft_delete(Occurrence.objects.filter(task=task))
    doomed = list(Attachment.objects.filter(task=task))
    changelog.soft_delete(Attachment.objects.filter(task=task))
    changelog.soft_delete(task)
    _erase_files(doomed)
    audit.record_object("task.deleted", actor=actor, home_id=task.home_id, title=task.title)


# ---------- occurrences ----------


def _require_open(occurrence: Occurrence) -> None:
    if not occurrence.is_open:
        raise ValidationError(_("This occurrence is already closed."))


def _lock(occurrence: Occurrence) -> Occurrence:
    locked: Occurrence = (
        Occurrence.objects.select_for_update().select_related("task", "home").get(pk=occurrence.pk)
    )
    return locked


def _spawn_next(occurrence: Occurrence, *, done_on: datetime.date) -> Occurrence | None:
    """Create the occurrence that follows a closed one, if the task repeats."""
    task = occurrence.task
    due = recurrence.next_due(
        task.rule, anchor=task.series_start, due_on=occurrence.due_on, done_on=done_on
    )
    if due is None:  # a task done once, or a date beyond what a calendar holds
        return None
    created: Occurrence = Occurrence.objects.create(
        home=occurrence.home,
        task=task,
        due_on=due,
        visible_from=_visible_from(task, due),
        follows=occurrence,
    )
    changelog.record_created([created])
    return created


@dataclass(frozen=True)
class Closing:
    """An occurrence just closed and the one that follows it, if any.

    `short` lists the stock items the stock could not give in full (docs/adr/0046).
    """

    occurrence: Occurrence
    next: Occurrence | None
    short: tuple[materials.Shortage, ...] = ()


def _close(
    actor: AnyUser,
    occurrence: Occurrence,
    status: Status,
    *,
    on: datetime.date | None,
    **extra: Any,
) -> Closing:
    _require_write(actor, occurrence.home)
    locked = _lock(occurrence)
    _require_open(locked)
    day = today()
    if on is not None and on > day:
        raise ValidationError({"completed_on": _("The date cannot be in the future.")})
    _check_date(on, "completed_on")
    closed_on = on or day
    before = changelog.before(locked)
    locked.status_before = locked.status
    locked.status = status
    locked.completed_by = actor if isinstance(actor, User) else None
    if status == Status.DONE:
        locked.completed_on = closed_on
    for field, value in extra.items():
        setattr(locked, field, value)
    locked.full_clean()
    locked.save()
    changelog.record_saved(locked, before)
    short = materials.draw_for_done(actor, locked, on=closed_on) if status == Status.DONE else []
    return Closing(locked, _spawn_next(locked, done_on=closed_on), tuple(short))


@transaction.atomic
def complete(
    actor: AnyUser,
    occurrence: Occurrence,
    *,
    on: datetime.date | None = None,
    actual_cost: Decimal | None = None,
    note: str = "",
) -> Closing:
    """Mark the occurrence done (today unless `on`), and create the next one.

    One gesture for every task. Cost and note can be given now or later
    (`update_occurrence`). `undo` reverses it (docs/adr/0033).
    """
    return _close(
        actor, occurrence, Status.DONE, on=on, actual_cost=actual_cost, completion_note=note
    )


@transaction.atomic
def skip(actor: AnyUser, occurrence: Occurrence) -> Closing:
    """Ignore this time: closed without being done; the next one is created as for done."""
    return _close(actor, occurrence, Status.SKIPPED, on=None)


@transaction.atomic
def postpone(actor: AnyUser, occurrence: Occurrence, to: datetime.date) -> Occurrence:
    """Move the open occurrence to the day `to`, which cannot be in the past."""
    _require_write(actor, occurrence.home)
    locked = _lock(occurrence)
    _require_open(locked)
    if to < today():
        raise ValidationError({"due_on": _("Choose a date that is not in the past.")})
    _check_date(to, "due_on")
    before = changelog.before(locked)
    if locked.postponed_from is None:
        locked.postponed_from = locked.due_on
    locked.due_on = to
    locked.status = Status.POSTPONED
    locked.visible_from = _visible_from(locked.task, to)
    locked.save()
    changelog.record_saved(locked, before)
    return locked


def _untouched(occurrence: Occurrence) -> bool:
    """The occurrence made by a closing has not been changed since: it can go away."""
    return (
        occurrence.status == Status.PLANNED
        and occurrence.postponed_from is None
        and not occurrence.actual_cost
        and not occurrence.completion_note
        and not occurrence.attachments.exists()
    )


@transaction.atomic
def undo(actor: AnyUser, occurrence: Occurrence) -> Occurrence:
    """Take back a `complete` or a `skip`: the occurrence is open again, the next one is gone.

    Refused when the next occurrence has been worked on since (postponed, noted,
    with a photo, closed): taking back would lose that work.
    """
    _require_write(actor, occurrence.home)
    locked = _lock(occurrence)
    if locked.status not in (Status.DONE, Status.SKIPPED):
        raise ValidationError(_("Only a task that was marked done or skipped can be undone."))
    # Locked as well: a "Done" on the next occurrence meanwhile makes this a 400, not a 500.
    following = Occurrence.objects.select_for_update().filter(follows=locked).first()
    if following is not None:
        if not _untouched(following):
            raise ValidationError(
                _("The next occurrence has been changed since: it cannot be undone any more.")
            )
        _withdraw(following)
    materials.return_draws(actor, locked, on=today())
    before = changelog.before(locked)
    locked.status = locked.status_before or Status.PLANNED
    locked.status_before = ""
    locked.completed_on = None
    locked.completed_by = None
    locked.actual_cost = None
    locked.completion_note = ""
    locked.save()
    changelog.record_saved(locked, before)
    return locked


def _withdraw(following: Occurrence) -> None:
    """The occurrence an undo takes back: deleted with a tombstone, no longer erased.

    A phone that saw it learns of the deletion (docs/adr/0077, need 4). It lets go of the
    occurrence it followed, so that closing that one again makes a new one.
    """
    following.follows = None
    changelog.save(following, update_fields=["follows", "updated_at"])
    changelog.soft_delete(following)


@transaction.atomic
def update_occurrence(
    actor: AnyUser,
    occurrence: Occurrence,
    *,
    used: Mapping[UUID, Decimal] | None = None,
    **changes: Any,
) -> Occurrence:
    """Complete what is known of a task that is done: the date, the real cost, a note.

    `used` gives the quantity really used of the stock items the closing took (by article
    identifier): the stock follows, by entries or exits (docs/adr/0046).
    """
    _require_write(actor, occurrence.home)
    locked = _lock(occurrence)
    if locked.status != Status.DONE:
        raise ValidationError(_("The details can be filled in once the task is done."))
    before = changelog.before(locked)
    _apply(locked, COMPLETION_FIELDS, changes)
    if locked.completed_on and locked.completed_on > today():
        raise ValidationError({"completed_on": _("The date cannot be in the future.")})
    _check_date(locked.completed_on, "completed_on")
    locked.full_clean()
    locked.save()
    changelog.record_saved(locked, before)
    if used:
        materials.adjust_draws(actor, locked, used, on=locked.completed_on or today())
    # The caller's object follows the row, as it did when it was the one saved.
    for field in COMPLETION_FIELDS:
        setattr(occurrence, field, getattr(locked, field))
    occurrence.updated_at = locked.updated_at
    return occurrence


# ---------- attachments ----------


def add_attachment(
    actor: AnyUser,
    task: Task,
    *,
    upload: UploadedFile,
    occurrence: Occurrence | None = None,
    name: str = "",
) -> Attachment:
    """Attach a photo or a file to a task (or to one of its occurrences).

    The content decides the type: an image is decoded and written again as a photo
    is (`rooms.images.process`), a document must be on the allow-list
    (`files.detect`). The name sent with it is only kept, cleaned, to be shown.
    """
    _require_write(actor, task.home)
    if occurrence is not None and occurrence.task_id != task.id:
        raise ValidationError({"occurrence": _("This occurrence is not one of this task.")})
    if Attachment.objects.filter(task=task).count() >= MAX_ATTACHMENTS_PER_TASK:
        raise ValidationError(
            _("At most %(max)d attachments for a task."), params={"max": MAX_ATTACHMENTS_PER_TASK}
        )
    data = _read_upload(upload)
    display = files.clean_name(name or upload.name)
    try:
        processed = images.process(data)
    except ValidationError as image_error:
        if _looks_like_image(data):
            raise image_error from None
        kind = files.detect(data)
        attachment = Attachment(
            home=task.home,
            task=task,
            occurrence=occurrence,
            name=display,
            content_type=kind.content_type,
            is_image=False,
            size_bytes=len(data),
        )
        stored, thumbnail, extension = data, None, kind.extension
    else:
        attachment = Attachment(
            home=task.home,
            task=task,
            occurrence=occurrence,
            name=display,
            content_type=processed.content_type,
            is_image=True,
            size_bytes=len(processed.data),
            width=processed.width,
            height=processed.height,
        )
        stored, thumbnail, extension = processed.data, processed.thumbnail, processed.extension
    attachment.full_clean(exclude=["file", "thumbnail"])
    with quotas.room_for(task.home, size=len(stored) + len(thumbnail or b"")):
        attachment.file.save(extension, ContentFile(stored), save=False)
        if thumbnail is not None:
            attachment.thumbnail.save("-thumb.jpg", ContentFile(thumbnail), save=False)
        try:
            with transaction.atomic():
                attachment.save()
                changelog.record_created([attachment])
        except Exception:
            attachment.file.delete(save=False)
            attachment.thumbnail.delete(save=False)
            raise
    audit.record_object("attachment.added", actor=actor, home_id=task.home_id, title=task.title)
    return attachment


def _looks_like_image(data: bytes) -> bool:
    """The start of the file is that of a JPEG, PNG or WebP: its error is the image's own."""
    return data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")) or (
        data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    )


@transaction.atomic
def update_attachment(actor: AnyUser, attachment: Attachment, *, name: str) -> Attachment:
    _require_write(actor, attachment.home)
    before = changelog.before(attachment)
    attachment.name = files.clean_name(name)
    attachment.full_clean(exclude=["file", "thumbnail"])
    attachment.save()
    changelog.record_saved(attachment, before)
    return attachment


@transaction.atomic
def delete_attachment(actor: AnyUser, attachment: Attachment) -> None:
    """Soft-delete the record and erase its files, once the deletion is committed."""
    _require_write(actor, attachment.home)
    changelog.soft_delete(attachment)
    _erase_files([attachment])
    audit.record_object(
        "attachment.deleted",
        actor=actor,
        home_id=attachment.home_id,
        title=attachment.task.title,
    )


def _erase_files(attachments: list[Attachment]) -> None:
    """Remove the file and thumbnail of each attachment from the disk (docs/adr/0034).

    Done once the deletion of the records is committed. A failure is logged, never
    raised: the records are gone either way and nothing could retry (docs/adr/0036).
    """
    names = [name for a in attachments for name in (a.file.name, a.thumbnail.name) if name]

    def erase() -> None:
        for name in names:
            try:
                attachment_storage.delete(name)
            except (OSError, ValueError, SuspiciousOperation):
                logger.error("Could not erase the file of a deleted attachment: %s", name)

    transaction.on_commit(erase)


# ---------- helpers shared by the views ----------


def rooms_for_forms(home: Home) -> models.QuerySet[Room]:
    return Room.objects.filter(home=home)


def items_for_forms(home: Home) -> models.QuerySet[Item]:
    return Item.objects.filter(home=home).select_related("room").order_by("room__position", "name")


def members_for_forms(home: Home) -> models.QuerySet[User]:
    return User.objects.filter(
        memberships__home=home, memberships__deleted_at__isnull=True
    ).order_by("email")
