"""The reading screens of tasks: Today, My home and History (docs/adr/0037).

Web views and API views both call these functions; neither holds a rule of its own. They
only read, always through the queries of `services`, which are limited to one home; the
caller has already checked that the person belongs to it.
"""

import datetime
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models.functions import Coalesce, TruncDate
from django.utils.translation import gettext as _

from housecairn.accounts.models import User
from housecairn.accounts.names import person_name
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.rooms.models import Item
from housecairn.tasks import costs, recurrence, services
from housecairn.tasks.models import (
    CLOSED_STATUSES,
    DisplayPreference,
    Occurrence,
    TodayGroup,
)

AnyUser = User | AnonymousUser

# A screen is a day-to-day glance, not a report: each list is cut here (History pages).
ROW_LIMIT = 200
DIGEST_LIMIT = 20
# Warranties ending within this many days are announced on My home.
WARRANTY_REMINDER_DAYS = 60

PREFERENCE_FIELDS = frozenset(
    {"today_group", "horizon_days", "show_overdue", "show_upcoming", "show_season", "language"}
)
_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}
_FAR = datetime.date.max


# ---------- display preferences ----------


def preferences_of(user: User) -> DisplayPreference:
    """The person's preferences; the defaults, not saved, if they never changed any."""
    found: DisplayPreference | None = DisplayPreference.objects.filter(user=user).first()
    return found or DisplayPreference(user=user)


def update_preferences(actor: AnyUser, **changes: Any) -> DisplayPreference:
    """Change the preferences of the person signed in, and only theirs."""
    if not isinstance(actor, User) or not actor.is_authenticated:
        raise PermissionDenied
    unknown = set(changes) - PREFERENCE_FIELDS
    if unknown:
        raise ValueError(f"Cannot change: {', '.join(sorted(unknown))}")
    prefs: DisplayPreference
    prefs, _created = DisplayPreference.objects.get_or_create(user=actor)
    for field, value in changes.items():
        setattr(prefs, field, value)
    # Only what is sent is validated: a value stored behind the model's back (a language the
    # instance no longer ships) must not refuse a change of something else.
    untouched = [f.name for f in prefs._meta.fields if f.name not in changes]
    prefs.full_clean(exclude=untouched)
    prefs.save()
    return prefs


# ---------- Today ----------


@dataclass(frozen=True)
class Section:
    """A titled run of occurrences. `key` says what it is; `ref` names a room or a person."""

    key: str
    title: str
    rows: list[Occurrence]
    ref: str | None = None
    day: datetime.date | None = None


def _urgency(occurrence: Occurrence) -> tuple[datetime.date, int]:
    """Oldest date first, then the more important task first."""
    return (
        occurrence.due_on or _FAR,
        _PRIORITY_RANK.get(occurrence.task.priority, 1),
    )


def _room_name(occurrence: Occurrence) -> str:
    room = occurrence.task.room
    return room.name if room else ""


def _person(occurrence: Occurrence) -> str:
    who = occurrence.task.assignee
    return person_name(who) if who else ""


def _grouped(rows: list[Occurrence], group: str, day: datetime.date) -> list[Section]:
    rows = sorted(rows, key=_urgency)
    if not rows:
        return []
    if group == TodayGroup.ROOM:
        by_room: dict[Any, list[Occurrence]] = {}
        for occurrence in rows:
            by_room.setdefault(occurrence.task.room_id, []).append(occurrence)
        named = sorted(
            (key for key in by_room if key is not None),
            key=lambda key: _room_name(by_room[key][0]).casefold(),
        )
        sections = [
            Section("room", _room_name(by_room[key][0]), by_room[key], ref=str(key))
            for key in named
        ]
        if None in by_room:
            sections.append(Section("room", _("No room"), by_room[None]))
        return sections
    if group == TodayGroup.PERSON:
        by_person: dict[Any, list[Occurrence]] = {}
        for occurrence in rows:
            by_person.setdefault(occurrence.task.assignee_id, []).append(occurrence)
        named = sorted(
            (key for key in by_person if key is not None),
            key=lambda key: _person(by_person[key][0]).casefold(),
        )
        sections = [
            Section("person", _person(by_person[key][0]), by_person[key], ref=str(key))
            for key in named
        ]
        if None in by_person:
            sections.append(Section("person", _("Nobody yet"), by_person[None]))
        return sections
    if group == TodayGroup.CALENDAR:
        by_day: dict[datetime.date | None, list[Occurrence]] = {}
        for occurrence in rows:
            by_day.setdefault(occurrence.due_on, []).append(occurrence)
        return [
            Section("day", "" if due is None else due.isoformat(), by_day[due], day=due)
            for due in sorted(by_day, key=lambda due: due or _FAR)
        ]
    sections = []
    now = [o for o in rows if o.due_on == day]
    later = [o for o in rows if o.due_on != day]
    if now:
        sections.append(Section("today", _("Today"), now))
    if later:
        sections.append(Section("upcoming", _("Coming up"), later))
    return sections


def today_sections(
    home: Home, prefs: DisplayPreference, *, day: datetime.date | None = None
) -> list[Section]:
    """What to do in `home`, laid out as the person's preferences say.

    The arrears come first, whatever the grouping. Then what falls within the horizon,
    grouped; then, apart, what is in season but further away or without a date.
    """
    day = day or services.today()
    end = day + datetime.timedelta(days=prefs.horizon_days)
    base = services.open_occurrences(home, day=day)
    sections: list[Section] = []
    if prefs.show_overdue:
        late = sorted(base.filter(due_on__lt=day)[:ROW_LIMIT], key=_urgency)
        if late:
            sections.append(Section("overdue", _("Overdue"), late))
    if prefs.show_upcoming:
        coming = list(base.filter(due_on__gte=day, due_on__lte=end)[:ROW_LIMIT])
        sections += _grouped(coming, prefs.today_group, day)
    if prefs.show_season:
        seasonal = base.filter(
            models.Q(due_on__gt=end) | models.Q(due_on__isnull=True),
            task__season_start_month__isnull=False,
        )[: ROW_LIMIT * 2]
        in_season = [o for o in seasonal if recurrence.in_season(o.task.rule, day)][:ROW_LIMIT]
        if in_season:
            sections.append(Section("season", _("In season"), in_season))
    return sections


# ---------- My home ----------


@dataclass(frozen=True)
class Digest:
    """What concerns one person in one home."""

    home: Home
    can_write: bool
    mine: list[Occurrence]
    overdue: list[Occurrence]
    unassigned: list[Occurrence]
    warranties: list[Item]

    @property
    def empty(self) -> bool:
        return not (self.mine or self.overdue or self.unassigned or self.warranties)


def my_home(user: User, homes: Iterable[Home], *, day: datetime.date | None = None) -> list[Digest]:
    """For each home: the person's tasks, the arrears of the others, tasks without anybody.

    The three lists share no row: the person's own late tasks are in `mine`; `overdue` is
    what is late for others or for nobody; `unassigned` is what nobody has taken, not late.
    """
    day = day or services.today()
    digests = []
    for home in homes:
        base = services.open_occurrences(home, day=day)
        mine = list(base.filter(task__assignee=user)[:DIGEST_LIMIT])
        late = list(base.filter(due_on__lt=day).exclude(task__assignee=user)[:DIGEST_LIMIT])
        free = list(
            base.filter(task__assignee__isnull=True).filter(
                models.Q(due_on__gte=day) | models.Q(due_on__isnull=True)
            )[:DIGEST_LIMIT]
        )
        warranties = list(
            Item.objects.filter(
                home=home,
                warranty_ends_on__gte=day,
                warranty_ends_on__lte=day + datetime.timedelta(days=WARRANTY_REMINDER_DAYS),
            )
            .select_related("room")
            .order_by("warranty_ends_on", "name")[:DIGEST_LIMIT]
        )
        digests.append(
            Digest(
                home,
                home_services.can_write(user, home),
                sorted(mine, key=_urgency),
                sorted(late, key=_urgency),
                sorted(free, key=_urgency),
                warranties,
            )
        )
    return digests


# ---------- History ----------


@dataclass(frozen=True)
class HistoryFilter:
    """Which closed occurrences to look at; every field is optional."""

    room: UUID | None = None
    item: UUID | None = None
    contractor: UUID | None = None
    person: UUID | None = None
    query: str = ""
    status: str = ""
    after: datetime.date | None = None
    before: datetime.date | None = None


def history(home: Home, selection: HistoryFilter) -> models.QuerySet[Occurrence]:
    """Done and skipped occurrences of `home`, the most recent first.

    A skipped occurrence has no completion date: the day it was closed stands for it.
    """
    rows = (
        services.occurrences_of(home)
        .select_related("task__contractor", "completed_by")
        .annotate(closed_on=Coalesce("completed_on", TruncDate("updated_at")))
    )
    rows = rows.filter(status__in=CLOSED_STATUSES)
    if selection.status in ("done", "skipped"):
        rows = rows.filter(status=selection.status)
    for field, value in (
        ("task__room_id", selection.room),
        ("task__item_id", selection.item),
        ("task__contractor_id", selection.contractor),
        ("completed_by_id", selection.person),
    ):
        if value is not None:
            rows = rows.filter(**{field: value})
    if selection.query:
        rows = rows.filter(
            models.Q(task__title__icontains=selection.query)
            | models.Q(completion_note__icontains=selection.query)
        )
    if selection.after is not None:
        rows = rows.filter(closed_on__gte=selection.after)
    if selection.before is not None:
        rows = rows.filter(closed_on__lte=selection.before)
    return rows.order_by("-closed_on", "-updated_at", "-id")


@dataclass(frozen=True)
class Cost:
    """The real cost of a selection, over the occurrences that have one (docs/adr/0090, 0091).

    The real cost of each is the stock it used plus the amount typed, or the plan it kept when
    nothing is typed (`tasks.costs`); `with_cost` counts those where any says something.
    Added in `Decimal`, not by SQL.
    """

    total: Decimal
    with_cost: int
    count: int


def cost_of(rows: models.QuerySet[Occurrence]) -> Cost:
    found = list(
        rows.order_by().values_list("id", "actual_cost", "planned_labour", "planned_material")
    )
    used = costs.stock_used(rows.order_by().values("id"))
    real = [
        costs.real_cost(typed, used.get(pk, costs.NOTHING).amount, costs.Planned(labour, material))
        for pk, typed, labour, material in found
    ]
    known = [cost for cost in real if cost is not None]
    total = sum(known, Decimal(0)).quantize(costs.CENT)
    return Cost(total, len(known), len(found))
