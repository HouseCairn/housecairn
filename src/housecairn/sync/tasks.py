"""The nightly retention purge, run by the task worker (docs/adr/0008, 0077)."""

import datetime
import zoneinfo

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from housecairn.sync import retention

SCHEDULE = "housecairn.sync.purge"
PURGE_HOUR = 3
PURGE_MINUTE = 30


def next_run(now: datetime.datetime | None = None) -> datetime.datetime:
    """The next 03:30 in the time zone of the instance after `now`."""
    zone = zoneinfo.ZoneInfo(settings.TIME_ZONE)
    local = (now or timezone.now()).astimezone(zone)
    at = datetime.time(PURGE_HOUR, PURGE_MINUTE)
    candidate = datetime.datetime.combine(local.date(), at, tzinfo=zone)
    if candidate <= local:
        candidate = datetime.datetime.combine(local.date() + datetime.timedelta(days=1), at, tzinfo=zone)
    return candidate


def purge() -> dict[str, int]:
    """Remove what the retention has expired; the counts go to the task result."""
    done = retention.purge()
    return {"changes": done.changes, "tombstones": done.tombstones, "rows": done.rows}


def schedule(**_kwargs: object) -> None:
    """Create the daily schedule of `purge` if it is not there; called after `migrate`."""
    from django_q.models import Schedule

    try:
        Schedule.objects.get_or_create(
            name=SCHEDULE,
            defaults={
                "func": "housecairn.sync.tasks.purge",
                "schedule_type": Schedule.DAILY,
                "repeats": -1,
                "next_run": next_run(),
            },
        )
    except DatabaseError:  # before django_q's tables exist
        return
