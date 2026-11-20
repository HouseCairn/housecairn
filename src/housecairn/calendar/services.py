"""What the calendar shows: the occurrences of some homes over a range of dates (docs/adr/0052).

Web and API views and the iCalendar feed call `entries`; none holds a rule of its own. It
only reads. The occurrences of the range come from the database; those of a task that
repeats and have not been created yet are projected by the recurrence engine of the tasks
(`tasks.recurrence`, RFC 5545 by python-dateutil), without being written. The number of
queries does not depend on the number of tasks.
"""

import datetime
import heapq
import itertools
import zoneinfo
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db import models
from django.db.models.functions import Coalesce
from django.utils import timezone

from housecairn.accounts.models import User
from housecairn.accounts.names import labels, person_name
from housecairn.calendar.models import Feed  # noqa: F401 - the app's models are loaded
from housecairn.core.text import without_control_characters
from housecairn.homes.models import WRITE_ROLES, Home, Membership
from housecairn.notifications import reminders
from housecairn.notifications import services as notification_services
from housecairn.tasks import recurrence
from housecairn.tasks.models import OPEN_STATUSES, Occurrence, Priority, RepeatMode, Status

MAX_SPAN_DAYS = 400
ENTRY_LIMIT = 5000
PROJECTED_TASKS_LIMIT = 1000
PROJECTION_STEPS = 400  # per task, whatever the range
NOBODY = "none"
MONDAY = 0


@dataclass(frozen=True)
class Entry:
    """One occurrence on one day, written in the database or only projected."""

    day: datetime.date
    status: str
    projected: bool
    title: str
    task_id: UUID
    occurrence_id: UUID | None
    home: Home
    room_id: UUID | None
    room: str
    assignee_id: int | UUID | None
    person: str
    priority: str
    can_write: bool
    updated_at: datetime.datetime
    # The date the occurrence was first due, when it was postponed: its calendar identity.
    first_due: datetime.date | None = None

    @property
    def identity_day(self) -> datetime.date:
        """The date in the identity of the entry: where it first stood, so a move keeps it."""
        return self.first_due or self.day

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def status_label(self) -> str:
        return str(Status(self.status).label)

    @property
    def can_complete(self) -> bool:
        return self.can_write and self.is_open and not self.projected


@dataclass(frozen=True)
class Result:
    entries: list[Entry]
    truncated: bool


@dataclass(frozen=True)
class Filters:
    room: UUID | None = None
    person: UUID | str | None = None  # an identifier, or NOBODY
    priority: str = ""


def clean_title(title: str) -> str:
    return without_control_characters(" ".join(title.split()))


def zone_of(user: User) -> zoneinfo.ZoneInfo | Any:
    """The time zone of a person: their own, else the instance's (docs/adr/0032)."""
    return reminders.zone_of(notification_services.preferences_of(user))


def today_of(user: User, now: datetime.datetime | None = None) -> datetime.date:
    return timezone.localdate(now or timezone.now(), zone_of(user))


def week_start(day: datetime.date) -> datetime.date:
    return day - datetime.timedelta(days=(day.weekday() - MONDAY) % 7)


def _task_filter(filters: Filters) -> models.Q:
    q = models.Q()
    if filters.room is not None:
        q &= models.Q(task__room_id=filters.room)
    if filters.person == NOBODY:
        q &= models.Q(task__assignee__isnull=True)
    elif filters.person is not None:
        q &= models.Q(task__assignee_id=filters.person)
    if filters.priority in Priority.values:
        q &= models.Q(task__priority=filters.priority)
    return q


_RANK = {Priority.HIGH: 0, Priority.NORMAL: 1, Priority.LOW: 2}


def _order(entry: Entry) -> tuple[datetime.date, int, str]:
    """By day, then by priority, then by title."""
    return (entry.day, _RANK.get(Priority(entry.priority), 1), entry.title.casefold())


def _project(occurrence: Occurrence, end: datetime.date) -> Iterator[datetime.date]:
    """The dates after an open occurrence up to `end`, were each closed on its due date."""
    task = occurrence.task
    current = occurrence.due_on
    for _step in range(PROJECTION_STEPS):
        if current is None:
            return
        current = recurrence.next_due(
            task.rule, anchor=task.series_start, due_on=current, done_on=current
        )
        if current is None or current > end:
            return
        yield current


def entries(
    user: User,
    homes: Sequence[Home],
    start: datetime.date,
    end: datetime.date,
    filters: Filters = Filters(),  # noqa: B008 - frozen
) -> Result:
    """Occurrences of `homes` from `start` to `end` (included), by day then by priority.

    Three queries at most: the roles of the person, the occurrences of the range, and the
    open occurrences of the tasks that repeat (to project what comes after them).
    """
    if end < start or (end - start).days > MAX_SPAN_DAYS:
        raise ValueError("The range is empty or longer than MAX_SPAN_DAYS days.")
    if not homes:
        return Result([], False)
    roles = dict(
        Membership.objects.filter(user=user, home__in=homes).values_list("home_id", "role")
    )
    base = Occurrence.objects.filter(home__in=homes, task__deleted_at__isnull=True).filter(
        _task_filter(filters)
    )
    base = base.select_related("task", "task__room", "task__assignee", "home")

    def entry(occurrence: Occurrence, day: datetime.date, *, projected: bool) -> Entry:
        task = occurrence.task
        return Entry(
            day=day,
            status=Status.PLANNED if projected else occurrence.status,
            projected=projected,
            title=clean_title(task.title),
            task_id=task.id,
            occurrence_id=None if projected else occurrence.id,
            home=occurrence.home,
            room_id=task.room_id,
            room=task.room.name if task.room else "",
            assignee_id=task.assignee_id,
            person=person_name(task.assignee),
            priority=task.priority,
            # An archived home is read only (docs/adr/0089).
            can_write=roles.get(occurrence.home_id) in WRITE_ROLES
            and occurrence.home.archived_at is None,
            updated_at=occurrence.updated_at,
            first_due=None if projected else occurrence.postponed_from,
        )

    def forecast(occurrence: Occurrence) -> Iterator[Entry]:
        for day in _project(occurrence, end):
            if day >= start:
                yield entry(occurrence, day, projected=True)

    real = (
        base.annotate(cal_day=Coalesce("due_on", "completed_on"))
        .filter(cal_day__range=(start, end))
        .order_by("cal_day", "created_at", "id")[: ENTRY_LIMIT + 1]
    )
    recorded = sorted((entry(o, o.cal_day, projected=False) for o in real), key=_order)
    repeating = list(
        base.filter(
            status__in=OPEN_STATUSES,
            due_on__isnull=False,
            due_on__lte=end,
            task__repeat_mode__in=[RepeatMode.FIXED, RepeatMode.AFTER_LAST],
        ).order_by("due_on", "id")[: PROJECTED_TASKS_LIMIT + 1]
    )
    tasks_cut = len(repeating) > PROJECTED_TASKS_LIMIT
    # Every stream is in ascending order: the merge hands out the entries day by day, whatever
    # the number of tasks, and stops at the limit, so the cut falls on the farthest dates and
    # never removes a near date of one task while keeping a far date of another.
    merged = heapq.merge(
        recorded, *(forecast(o) for o in repeating[:PROJECTED_TASKS_LIMIT]), key=_order
    )
    found = list(itertools.islice(merged, ENTRY_LIMIT + 1))
    return Result(found[:ENTRY_LIMIT], len(found) > ENTRY_LIMIT or tasks_cut)


def by_day(items: Iterable[Entry]) -> dict[datetime.date, list[Entry]]:
    grouped: dict[datetime.date, list[Entry]] = {}
    for entry in items:
        grouped.setdefault(entry.day, []).append(entry)
    return grouped


def filters_from(params: Any, *, strict: bool = False) -> Filters:
    """The filters of a request (`room`, `person`, `priority`).

    A value that does not parse is dropped; `strict` (the API) raises `ValueError(name)`.
    """
    values: dict[str, Any] = {}
    for name in ("room", "person"):
        raw = params.get(name, "")
        if not raw:
            continue
        try:
            values[name] = raw if name == "person" and raw == NOBODY else UUID(raw)
        except ValueError:
            if strict:
                raise ValueError(name) from None
    priority = params.get("priority", "")
    if priority and priority not in Priority.values:
        if strict:
            raise ValueError("priority")
        priority = ""
    return Filters(values.get("room"), values.get("person"), priority)


def people_of(homes: Sequence[Home]) -> list[tuple[Any, str]]:
    """The members of `homes` as (identifier, name), by name: the choices of the person filter."""
    users = {
        membership.user_id: membership.user
        for membership in Membership.objects.filter(home__in=homes).select_related("user")
    }
    return sorted(labels(users.values()).items(), key=lambda pair: pair[1].casefold())


def counts_of(items: Iterable[Entry]) -> dict[str, int]:
    """How many entries have each status."""
    counts = dict.fromkeys(Status.values, 0)
    for entry in items:
        counts[entry.status] += 1
    return counts
