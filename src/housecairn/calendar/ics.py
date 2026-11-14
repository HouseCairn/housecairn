"""The iCalendar document of a feed (RFC 5545), written by the `icalendar` library (ADR 0052).

The library escapes commas, semicolons, backslashes and line breaks and folds lines at 75
octets; titles are also reduced to one line of printable characters before they get there.
A task carries here its title, home, room, due date, status and a link, nothing else: no
note, cost, procedure, attachment, person or email address.
"""

import datetime
import hashlib
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _
from django.utils.translation import pgettext
from icalendar import Calendar, Event

from housecairn.calendar import services
from housecairn.calendar.feeds import feed_homes
from housecairn.calendar.models import Feed
from housecairn.core.language import language_of
from housecairn.core.links import external_url
from housecairn.homes.models import Home
from housecairn.instance.config import instance_name
from housecairn.rooms.models import Room
from housecairn.tasks.models import Occurrence, Status, Task

PAST_DAYS = 30
FUTURE_DAYS = 365
REFRESH = datetime.timedelta(hours=6)
CACHE_SECONDS = 300  # a rendered document and its ETag, per feed (docs/adr/0052)
_STATUS_ICS = {Status.SKIPPED: "CANCELLED"}


def uid_of(entry: services.Entry) -> str:
    """`<task id>-<date>@housecairn`, for a forecast and for the occurrence that replaces it.

    The date is the one the occurrence was first due on (`postponed_from` when it was
    postponed), so a forecast that becomes an occurrence, and an occurrence that is postponed,
    keep their identity: the calendar application moves or updates the event, it does not
    delete one and create another.
    """
    return f"{entry.task_id}-{entry.identity_day.isoformat()}@housecairn"


def _unique_uid(entry: services.Entry, seen: set[str]) -> str | None:
    """The identity above, unless another event of the document already has it.

    Two occurrences of a task can share a first date (a postponement that lands back on the
    date of the next occurrence): the second one then takes its own id, which is unique. A
    forecast that meets an existing identity is dropped: the recorded occurrence stands.
    """
    uid = uid_of(entry)
    if uid in seen:
        if entry.occurrence_id is None:
            return None
        uid = f"{entry.occurrence_id}@housecairn"
    seen.add(uid)
    return uid


def _event(entry: services.Entry, base_url: str, uid: str) -> Event:
    status = str(Status(entry.status).label)
    home = services.clean_title(entry.home.name)
    summary = entry.title
    if entry.status in (Status.DONE, Status.SKIPPED):
        summary = f"[{status}] {summary}"
    state = status + (" (" + _("forecast") + ")" if entry.projected else "")
    lines = [_("Status") + ": " + state, pgettext("calendar", "Home") + ": " + home]
    if entry.room:
        lines.append(_("Room") + ": " + services.clean_title(entry.room))
    event = Event()
    event.add("uid", uid)
    event.add("dtstamp", entry.updated_at.astimezone(datetime.UTC))
    event.add("dtstart", entry.day)
    event.add("dtend", entry.day + datetime.timedelta(days=1))
    event.add("summary", summary)
    event.add("description", "\n".join(lines))
    event.add("location", home)
    event.add("status", _STATUS_ICS.get(Status(entry.status), "CONFIRMED"))
    event.add("transp", "TRANSPARENT")
    path = reverse("tasks:detail", kwargs={"home_id": entry.home.id, "task_id": entry.task_id})
    event.add("url", external_url(path, base_url))
    return event


def render(feed: Feed, base_url: str, *, now: datetime.datetime | None = None) -> bytes:
    """The calendar of a feed, in the language and the time zone of its person.

    The body depends on the data only (no clock in it besides the window around today), so
    the same data gives the same bytes, and the same ETag.
    """
    user = feed.user
    zone = services.zone_of(user)
    today = services.today_of(user, now)
    language = language_of(user)
    homes: list[Any] = [feed.home] if feed.home else list(feed_homes(user))
    with translation.override(language):
        result = services.entries(
            user,
            homes,
            today - datetime.timedelta(days=PAST_DAYS),
            today + datetime.timedelta(days=FUTURE_DAYS),
        )
        calendar = Calendar()
        calendar.add("prodid", "-//HouseCairn//Calendar//EN")
        calendar.add("version", "2.0")
        calendar.add("calscale", "GREGORIAN")
        calendar.add("method", "PUBLISH")
        name = services.clean_title(feed.home.name) if feed.home else _("All my homes")
        calendar.add("x-wr-calname", f"{instance_name()}: {name}")
        calendar.add("x-wr-timezone", str(zone))
        calendar.add("refresh-interval", REFRESH, parameters={"VALUE": "DURATION"})
        calendar.add("x-published-ttl", REFRESH)
        seen: set[str] = set()
        for entry in result.entries:
            uid = _unique_uid(entry, seen)
            if uid is not None:
                calendar.add_component(_event(entry, base_url, uid))
        return bytes(calendar.to_ical())


def etag_of(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def _stamp(model: Any, homes: list[Any]) -> str:
    """How many rows (deleted ones included) and the last change, in a single query."""
    found = model.all_objects.filter(home__in=homes).aggregate(
        n=Count("pk"), last=Max("updated_at")
    )
    return f"{found['n']}:{found['last'].isoformat() if found['last'] else '-'}"


def _fingerprint(feed: Feed, base_url: str, now: datetime.datetime | None) -> str:
    """Everything the document is made of, read in five small queries instead of rendered.

    The tasks, occurrences, rooms and homes of the feed (count and last `updated_at`: a
    creation, an edit, a soft deletion and a hard deletion all change one of the two), the homes
    it covers, the day, and the language and time zone of the person. Computed in the database, so
    every worker of the server sees the same value and a change made through any of them
    invalidates the document at once; nothing in `tasks` is touched.
    """
    user = feed.user
    homes: list[Any] = [feed.home] if feed.home else list(feed_homes(user))
    ids = sorted(str(h.pk) for h in homes)
    parts = [
        str(feed.pk),
        base_url,
        # Its links are under the fixed prefix, whatever the request (core.links, ADR 0055).
        settings.HOUSECAIRN_URL_PREFIX,
        ",".join(ids),
        str(services.zone_of(user)),
        language_of(user),
        services.today_of(user, now).isoformat(),
        instance_name(),
        _stamp(Task, homes),
        _stamp(Occurrence, homes),
        _stamp(Room, homes),
        str(Home.all_objects.filter(pk__in=ids).aggregate(last=Max("updated_at"))["last"]),
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def cached_render(
    feed: Feed, base_url: str, *, now: datetime.datetime | None = None
) -> tuple[bytes, str]:
    """The document and its ETag, kept five minutes per feed and content.

    Rendering 500 repeating tasks takes seconds and an ETag needs the whole body, so a client
    that polls would pay for it each time. The key is a fingerprint of the data (see above), so
    a change shows at once; the five minutes only bound what the fingerprint cannot see (a bulk
    update that skips `updated_at`, the name of the instance). A revoked or unknown secret never
    gets here: the feed is looked up first.
    """
    key = "calendar:feed:" + _fingerprint(feed, base_url, now)
    hit = cache.get(key)
    if hit is not None:
        return hit  # type: ignore[no-any-return]
    body = render(feed, base_url, now=now)
    value = (body, etag_of(body))
    cache.set(key, value, CACHE_SECONDS)
    return value
