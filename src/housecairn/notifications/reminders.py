"""Which reminders are due, and sending each one once (docs/adr/0043).

`dispatch` runs in the background worker every few minutes (docs/adr/0008). For each open
occurrence with a due date, the person to remind is the one in charge of the task, if their
account is active, they may still write in the home and they have a channel on; otherwise
(nobody in charge, a reader, no channel, an inactive account) every member of the home who
may write, with an active account and a channel on. Readers never are. A person's
first reminder goes out `days_before` days before the due date, from `send_at` in their
time zone; follow-ups come every `FOLLOW_UP_DAYS[priority]` days while the occurrence stays
open. Postponing moves the due date, so the reminders start again from the new date; done
and skipped close the occurrence, so they stop.

Each message has a key (occurrence, due date, follow-up number; or the day of a summary).
It is claimed by inserting a `Delivery` row before anything is sent: the unique constraint
lets one worker only send it, even when two runs cross. A message claimed and not sent (the
service failed, the worker stopped) is not tried again; the next follow-up is.

A run starts no new message after `RUN_SECONDS`: what is left is not claimed, so the next
run sends it, under the same key. With `MAX_CHANNELS` channels a person, each given up
after `outbound.SEND_SECONDS`, a run lasts at most `LONGEST_RUN_SECONDS` (`RUN_SECONDS`
plus one message), within the five minutes django-q2 gives a task (`Q_CLUSTER["timeout"]`).
"""

import datetime
import logging
import re
import time
import zoneinfo
from collections import defaultdict
from typing import Any

from django.db import IntegrityError, models, transaction
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from housecairn.accounts.models import User
from housecairn.core import dates
from housecairn.core.language import language_of
from housecairn.core.links import external_url
from housecairn.homes.models import WRITE_ROLES, Membership
from housecairn.instance.config import get_setting, instance_zone
from housecairn.notifications import devices, outbound, services, webpush
from housecairn.notifications.models import (
    MAX_CHANNELS,
    MAX_DEVICES,
    Channel,
    Delivery,
    Device,
    Kind,
    Preference,
)
from housecairn.tasks.models import OPEN_STATUSES, Occurrence

logger = logging.getLogger("housecairn.notifications")

FOLLOW_UP_DAYS = {"high": 1, "normal": 3, "low": 7}
MAX_DAYS_BEFORE = 30
KEEP_DELIVERIES_DAYS = 90
DIGEST_LIMIT = 30
# No new message after this many seconds of a run (see the docstring of the module).
RUN_SECONDS = 120.0
# The longest a run can last, then: the last message started, to every channel and every
# device of a person, with a second per send for the grace of Apprise and the closing of
# its connections.
LONGEST_RUN_SECONDS = (
    RUN_SECONDS
    + MAX_CHANNELS * (outbound.SEND_SECONDS + 1)
    + MAX_DEVICES * (webpush.SEND_SECONDS + 1)
)
_clock = time.monotonic


def zone_of(prefs: Preference) -> datetime.tzinfo:
    if prefs.time_zone:
        try:
            return zoneinfo.ZoneInfo(prefs.time_zone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            pass
    return instance_zone()


def is_quiet(prefs: Preference, local: datetime.datetime) -> bool:
    """Within the quiet hours, which may run past midnight (22:00 to 07:00)."""
    start, end = prefs.quiet_start, prefs.quiet_end
    if start is None or end is None or start == end:
        return False
    moment = local.time()
    return start <= moment < end if start < end else (moment >= start or moment < end)


def first_reminder(due_on: datetime.date, prefs: Preference) -> datetime.datetime:
    day = due_on - datetime.timedelta(days=prefs.days_before)
    return datetime.datetime.combine(day, prefs.send_at, tzinfo=zone_of(prefs))


def digest_day(prefs: Preference, local: datetime.datetime) -> datetime.date:
    """The day whose summary is due at `local`: the summary of a day is due from `send_at`
    that day until `send_at` the next, so quiet hours delay it, they never skip it."""
    shift = datetime.timedelta(hours=prefs.send_at.hour, minutes=prefs.send_at.minute)
    return (local.replace(tzinfo=None) - shift).date()


def skip_pending_digest(
    user: User, prefs: Preference, now: datetime.datetime | None = None
) -> None:
    """When the summary is turned on, or its time or time zone changes, before `send_at`:
    the summary still due is that of the day before, which the person never asked for. It
    is marked as claimed, so the first summary goes out at `send_at`, once."""
    local = (now or timezone.now()).astimezone(zone_of(prefs))
    day = digest_day(prefs, local)
    if day < local.date():
        Delivery.objects.get_or_create(user=user, kind=Kind.DIGEST, key=day.isoformat())


def _link(occurrence: Occurrence) -> str:
    site = str(get_setting("site_url") or "")
    path = reverse(
        "tasks:detail", kwargs={"home_id": occurrence.home_id, "task_id": occurrence.task_id}
    )
    return external_url(path, site)


# Control characters (Unicode category Cc), and the line and paragraph separators.
BREAKS = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]+")


def one_line(text: str) -> str:
    """A name typed by a person, on one line: each run of line breaks or other control
    characters becomes one space, so it cannot start a line of its own in the message."""
    return BREAKS.sub(" ", text)


def _line(occurrence: Occurrence) -> str:
    """Title, home, due date and link: nothing else of the task leaves the instance."""
    text = _("%(title)s, %(home)s, due %(date)s") % {
        "title": one_line(occurrence.task.title),
        "home": one_line(occurrence.home.name),
        "date": dates.day(occurrence.due_on),
    }
    link = _link(occurrence)
    return f"{text}\n{link}" if link else text


def reminder_message(occurrence: Occurrence, prefs: Preference) -> tuple[str, str]:
    """Plain text: `outbound.send` lets Apprise escape it for each channel. The title of a
    message is fixed, since some services render it as markup of their own."""
    with translation.override(language_of(prefs.user)):
        return _("HouseCairn reminder"), _line(occurrence)


def digest_message(rows: list[Occurrence], prefs: Preference) -> tuple[str, str]:
    with translation.override(language_of(prefs.user)):
        lines = [_line(o) for o in rows[:DIGEST_LIMIT]]
        if len(rows) > DIGEST_LIMIT:
            lines.append(_("And %(n)d more.") % {"n": len(rows) - DIGEST_LIMIT})
        return _("HouseCairn: tasks to do"), "\n\n".join(lines)


def _push_path(occurrence: Occurrence | None, user: User) -> str:
    """The screen a notification on a device opens: the task, or Today for a summary."""
    if occurrence is not None:
        return reverse(
            "tasks:detail", kwargs={"home_id": occurrence.home_id, "task_id": occurrence.task_id}
        )
    return reverse("index")


def _deliver(
    user: User,
    kind: Kind,
    key: str,
    message: tuple[str, str],
    channels: list[Channel],
    occurrence: Occurrence | None = None,
    pushed: list[Device] | None = None,
) -> bool:
    """Claim the message, then send it to each channel on and each device. False if
    already claimed."""
    try:
        with transaction.atomic():
            delivery = Delivery.objects.create(user=user, kind=kind, key=key, occurrence=occurrence)
    except IntegrityError:
        return False
    title, body = message
    results = [outbound.send(c.url, title=title, body=body) for c in channels]
    if pushed:
        # The link is the screen to open, not a line of the text.
        text = body if occurrence is None else body.rsplit("\n", 1)[0]
        results += devices.send_all(
            pushed, kind=str(kind), title=title, body=text, path=_push_path(occurrence, user)
        )
    if any(results):
        Delivery.objects.filter(pk=delivery.pk).update(sent=True)
    return True


def _recipients(
    occurrence: Occurrence, writers: dict[Any, set[Any]], reachable: set[Any]
) -> set[Any]:
    """The person in charge if they may write and have a channel on; otherwise everyone
    of the home who may and has. `reachable` holds active accounts only."""
    allowed = writers.get(occurrence.home_id, set()) & reachable
    assignee = occurrence.task.assignee_id
    if assignee in allowed:
        return {assignee}
    return allowed


def dispatch(now: datetime.datetime | None = None) -> int:
    """Send what is due; the number of messages claimed. Nothing when notifications are off,
    nor on a demonstration instance, which sends nothing (docs/adr/0071)."""
    from housecairn.demo import mode

    if not services.is_enabled() or mode.is_on():
        return 0
    now = now or timezone.now()
    started = _clock()

    def out_of_time() -> bool:
        return _clock() - started >= RUN_SECONDS

    Delivery.objects.filter(
        claimed_at__lt=now - datetime.timedelta(days=KEEP_DELIVERIES_DAYS)
    ).delete()
    channels: dict[Any, list[Channel]] = defaultdict(list)
    for channel in Channel.objects.filter(enabled=True).select_related("user"):
        if channel.user.is_active:
            channels[channel.user_id].append(channel)
    pushed = devices.reachable()
    for user_id in pushed:
        channels[user_id]  # a device is a way to reach the person, as a channel is
    if not channels:
        return 0
    writers: dict[Any, set[Any]] = defaultdict(set)
    for home_id, user_id in Membership.objects.filter(
        user_id__in=channels, role__in=WRITE_ROLES
    ).values_list("home_id", "user_id"):
        writers[home_id].add(user_id)
    prefs = {p.user_id: p for p in Preference.objects.filter(user_id__in=channels)}
    users = {u.pk: u for u in User.objects.filter(pk__in=channels)}

    def prefs_of(user_id: Any) -> Preference:
        return prefs.get(user_id) or Preference(user=users[user_id])

    horizon = timezone.localdate(now, instance_zone()) + datetime.timedelta(
        days=MAX_DAYS_BEFORE + 1
    )
    rows = (
        Occurrence.objects.filter(
            home_id__in=writers,
            status__in=OPEN_STATUSES,
            due_on__isnull=False,
            due_on__lte=horizon,
            task__deleted_at__isnull=True,
        )
        .select_related("task", "home")
        .order_by(models.F("due_on").asc(), "created_at", "id")
    )
    claimed = 0
    digests: dict[Any, list[Occurrence]] = defaultdict(list)
    stopped = False
    for occurrence in rows:
        due_on = occurrence.due_on
        if due_on is None:
            continue
        if stopped:
            break
        for user_id in _recipients(occurrence, writers, set(channels)):
            pref = prefs_of(user_id)
            local = now.astimezone(zone_of(pref))
            start = first_reminder(due_on, pref)
            if local < start:
                continue
            if pref.digest:
                digests[user_id].append(occurrence)
                continue
            if is_quiet(pref, local):
                continue  # sent at the first run after the quiet hours
            step = datetime.timedelta(days=FOLLOW_UP_DAYS.get(occurrence.task.priority, 3))
            number = (local - start) // step
            key = f"{occurrence.id}:{due_on.isoformat()}:{number}"
            if out_of_time():
                stopped = True  # left unclaimed: the next run sends it
                break
            message = reminder_message(occurrence, pref)
            claimed += _deliver(
                users[user_id],
                Kind.REMINDER,
                key,
                message,
                channels[user_id],
                occurrence,
                pushed.get(user_id),
            )
    for user_id, found in digests.items():
        pref = prefs_of(user_id)
        local = now.astimezone(zone_of(pref))
        if is_quiet(pref, local):
            continue
        if stopped or out_of_time():
            break
        day = digest_day(pref, local)
        message = digest_message(found, pref)
        claimed += _deliver(
            users[user_id],
            Kind.DIGEST,
            day.isoformat(),
            message,
            channels[user_id],
            pushed=pushed.get(user_id),
        )
    return claimed
