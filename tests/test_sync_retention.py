"""The retention of the change log: 400 days, then the nightly purge (docs/adr/0077, 0079)."""

import datetime
import uuid

import pytest
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from housecairn.homes.models import Home
from housecairn.rooms.models import Room
from housecairn.sync import changes, retention, tasks
from housecairn.sync.models import Change, HomeHead, RowClocks

pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 11, 15, 3, 30, tzinfo=datetime.UTC)
DAYS = datetime.timedelta(days=1)
MICRO = datetime.timedelta(microseconds=1)


def change_made_at(home: Home, when: datetime.datetime) -> Change:
    done = changes.record_change(home, "rooms.room", uuid.uuid4(), changes.PUT, {"name": "x"})
    assert done.change is not None
    Change.objects.filter(pk=done.change.pk).update(created_at=when)
    done.change.refresh_from_db()
    return done.change


def tombstone(home: Home, when: datetime.datetime, row: uuid.UUID | None = None) -> RowClocks:
    row = row or uuid.uuid4()
    changes.record_change(home, "rooms.room", row, changes.DELETE)
    RowClocks.objects.filter(home=home, row_id=row).update(tombstoned_at=when)
    return RowClocks.objects.get(home=home, row_id=row)


def test_the_retention_is_four_hundred_days() -> None:
    assert datetime.timedelta(days=400) == retention.RETENTION
    assert retention.limit(NOW) == NOW - 400 * DAYS


def test_a_change_exactly_four_hundred_days_old_is_kept(alice_home: Home) -> None:
    change = change_made_at(alice_home, NOW - 400 * DAYS)
    assert retention.purge(NOW).changes == 0
    assert Change.objects.filter(pk=change.pk).exists()


def test_a_change_a_microsecond_older_than_four_hundred_days_is_purged(alice_home: Home) -> None:
    change = change_made_at(alice_home, NOW - 400 * DAYS - MICRO)
    assert retention.purge(NOW).changes == 1
    assert not Change.objects.filter(pk=change.pk).exists()


def test_the_changes_in_between_are_kept(alice_home: Home) -> None:
    kept = [
        change_made_at(alice_home, NOW - 399 * DAYS),
        change_made_at(alice_home, NOW - 1 * DAYS),
        change_made_at(alice_home, NOW),
    ]
    assert retention.purge(NOW).changes == 0
    assert Change.objects.filter(pk__in=[c.pk for c in kept]).count() == 3


def test_the_purge_remembers_the_highest_position_it_removed(alice_home: Home) -> None:
    change_made_at(alice_home, NOW - 500 * DAYS)  # pos 1
    change_made_at(alice_home, NOW - 401 * DAYS)  # pos 2
    change_made_at(alice_home, NOW - 10 * DAYS)  # pos 3
    retention.purge(NOW)
    head = HomeHead.objects.get(home=alice_home)
    assert head.floor == 2
    assert head.pos == 3
    assert list(Change.objects.filter(home=alice_home).values_list("pos", flat=True)) == [3]


def test_the_floor_is_per_home_and_never_goes_down(alice_home: Home) -> None:
    other = Home.objects.create(name="Other")
    change_made_at(other, NOW - 1 * DAYS)
    change_made_at(alice_home, NOW - 401 * DAYS)
    retention.purge(NOW)
    assert HomeHead.objects.get(home=alice_home).floor == 1
    assert HomeHead.objects.get(home=other).floor == 0
    HomeHead.objects.filter(home=alice_home).update(floor=9)
    change_made_at(alice_home, NOW - 402 * DAYS)  # pos 2, purged
    retention.purge(NOW)
    assert HomeHead.objects.get(home=alice_home).floor == 9


def test_the_next_position_goes_on_after_a_purge(alice_home: Home) -> None:
    change_made_at(alice_home, NOW - 500 * DAYS)
    retention.purge(NOW)
    done = changes.record_change(alice_home, "rooms.room", uuid.uuid4(), changes.PUT, {"name": "x"})
    assert done.change is not None
    assert done.change.pos == 2


def test_a_tombstone_exactly_four_hundred_days_old_is_kept(alice_home: Home) -> None:
    state = tombstone(alice_home, NOW - 400 * DAYS)
    assert retention.purge(NOW).tombstones == 0
    assert RowClocks.objects.filter(pk=state.pk).exists()


def test_a_tombstone_older_than_four_hundred_days_is_purged_with_its_row(
    alice_home: Home,
) -> None:
    room = Room.objects.create(home=alice_home, name="Old")
    room.soft_delete()
    state = tombstone(alice_home, NOW - 400 * DAYS - MICRO, room.pk)
    done = retention.purge(NOW)
    assert (done.tombstones, done.rows) == (1, 1)
    assert not RowClocks.objects.filter(pk=state.pk).exists()
    assert not Room.all_objects.filter(pk=room.pk).exists()


def test_a_row_deleted_since_then_is_not_removed(alice_home: Home) -> None:
    """The clock says deleted; the row says alive (restored elsewhere): the row stays."""
    room = Room.objects.create(home=alice_home, name="Back")
    tombstone(alice_home, NOW - 401 * DAYS, room.pk)
    done = retention.purge(NOW)
    assert (done.tombstones, done.rows) == (1, 0)
    assert Room.objects.filter(pk=room.pk).exists()


def test_a_live_row_keeps_its_clocks(alice_home: Home) -> None:
    row = uuid.uuid4()
    changes.record_change(alice_home, "rooms.room", row, changes.PUT, {"name": "x"})
    assert retention.purge(NOW + 1000 * DAYS).tombstones == 0
    assert RowClocks.objects.filter(row_id=row).exists()


def test_a_link_table_or_an_unknown_entity_keeps_no_clocks_and_removes_no_row(
    alice_home: Home,
) -> None:
    for entity in ("projects.project_rooms", "future.thing"):
        row = uuid.uuid4()
        changes.record_change(alice_home, entity, row, changes.DELETE)
        RowClocks.objects.filter(row_id=row).update(tombstoned_at=NOW - 401 * DAYS)
    done = retention.purge(NOW)
    assert (done.tombstones, done.rows) == (2, 0)


def test_a_large_purge_takes_a_bounded_number_of_statements(alice_home: Home) -> None:
    for _ in range(40):
        tombstone(alice_home, NOW - 401 * DAYS)
    with CaptureQueriesContext(connection) as small:
        retention.purge(NOW)
    for _ in range(80):
        tombstone(alice_home, NOW - 401 * DAYS)
    with CaptureQueriesContext(connection) as large:
        retention.purge(NOW)
    assert len(large) <= len(small) + 4


def test_a_purge_with_nothing_to_remove_removes_nothing(alice_home: Home) -> None:
    assert retention.purge(NOW) == retention.Purged(0, 0, 0)


# The nightly task


def test_the_task_returns_what_it_removed(alice_home: Home) -> None:
    change_made_at(alice_home, timezone.now() - 401 * DAYS)
    assert tasks.purge() == {"changes": 1, "tombstones": 0, "rows": 0}


def test_the_schedule_is_created_once_and_runs_every_day() -> None:
    from django_q.models import Schedule

    Schedule.objects.filter(name=tasks.SCHEDULE).delete()
    tasks.schedule()
    tasks.schedule()
    found = Schedule.objects.get(name=tasks.SCHEDULE)
    assert found.func == "housecairn.sync.tasks.purge"
    assert found.schedule_type == Schedule.DAILY
    assert found.repeats == -1
    assert found.next_run > timezone.now()


def test_the_schedule_is_created_after_migrate() -> None:
    from django.apps import apps
    from django.db.models.signals import post_migrate

    receivers = [r[1]() for r in post_migrate.receivers if r[1]() is not None]
    assert tasks.schedule in receivers
    assert apps.get_app_config("sync").name == "housecairn.sync"


def test_the_next_run_is_half_past_three_in_the_time_zone_of_the_instance() -> None:
    import zoneinfo

    zone = zoneinfo.ZoneInfo(settings.TIME_ZONE)
    before = datetime.datetime(2026, 11, 15, 1, 0, tzinfo=zone)
    assert tasks.next_run(before) == datetime.datetime(2026, 11, 15, 3, 30, tzinfo=zone)
    after = datetime.datetime(2026, 11, 15, 3, 30, tzinfo=zone)
    assert tasks.next_run(after) == datetime.datetime(2026, 11, 16, 3, 30, tzinfo=zone)
