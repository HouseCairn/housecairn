"""Recording a change: positions, clocks per field, `extra`, tombstones (docs/adr/0077)."""

import datetime
import threading
import uuid

import pytest
from django.db import connection, connections, transaction

from housecairn.homes.models import Home
from housecairn.sync import changes, clock
from housecairn.sync.models import Change, HomeHead, RowClocks, ServerDevice

pytestmark = pytest.mark.django_db

ENTITY = "rooms.room"
WALL = 1_763_251_200_000
PHONE = bytes(range(16))
PHONE_B64 = clock.device_to_b64(PHONE)
OTHER = bytes(range(1, 17))


def stamp_at(ms: int, counter: int = 0, device: bytes = PHONE) -> tuple[str, bytes]:
    return clock.format_clock(clock.make_clock(ms, counter)), device


def put(
    home: Home, row: uuid.UUID, fields: dict[str, object], **kwargs: object
) -> changes.Recorded:
    return changes.record_change(home, ENTITY, row, changes.PUT, fields, wall=WALL, **kwargs)  # type: ignore[arg-type]


def test_the_first_change_of_a_home_has_position_one(alice_home: Home) -> None:
    done = put(alice_home, uuid.uuid4(), {"name": "Kitchen"})
    assert done.change is not None
    assert done.change.pos == 1
    assert done.change.entity == ENTITY
    assert done.change.kind == "put"
    assert done.change.fields == {"name": "Kitchen"}
    assert HomeHead.objects.get(home=alice_home).pos == 1


def test_positions_follow_one_another_without_gap_in_one_home(alice_home: Home) -> None:
    rows = [uuid.uuid4() for _ in range(5)]
    for row in rows:
        put(alice_home, row, {"name": "x"})
    put(alice_home, rows[0], {"name": "y"})
    positions = list(
        Change.objects.filter(home=alice_home).order_by("pos").values_list("pos", flat=True)
    )
    assert positions == [1, 2, 3, 4, 5, 6]


def test_each_home_counts_its_own_positions(alice_home: Home, make_user: object) -> None:
    other = Home.objects.create(name="Other")
    put(alice_home, uuid.uuid4(), {"name": "a"})
    put(alice_home, uuid.uuid4(), {"name": "b"})
    first = put(other, uuid.uuid4(), {"name": "c"})
    assert first.change is not None
    assert first.change.pos == 1


def test_a_rolled_back_write_leaves_no_hole(alice_home: Home) -> None:
    put(alice_home, uuid.uuid4(), {"name": "a"})

    def fail_after_recording() -> None:
        with transaction.atomic():
            put(alice_home, uuid.uuid4(), {"name": "b"})
            raise RuntimeError

    with pytest.raises(RuntimeError):
        fail_after_recording()
    done = put(alice_home, uuid.uuid4(), {"name": "c"})
    assert done.change is not None
    assert done.change.pos == 2
    assert Change.objects.filter(home=alice_home).count() == 2


def test_the_write_of_a_server_gets_the_clock_and_the_device_of_the_server(
    alice_home: Home,
) -> None:
    done = put(alice_home, uuid.uuid4(), {"name": "a"})
    assert done.change is not None
    assert done.change.clock == clock.format_clock(clock.make_clock(WALL, 0))
    assert bytes(done.change.device) == bytes(ServerDevice.objects.get(pk=1).device)
    assert len(bytes(done.change.device)) == 16


def test_the_clocks_of_a_home_rise_even_when_the_wall_clock_goes_back(alice_home: Home) -> None:
    row = uuid.uuid4()
    seen = []
    for wall in (WALL, WALL - 10_000, WALL - 20_000, WALL + 1):
        done = changes.record_change(alice_home, ENTITY, row, changes.PUT, {"name": "n"}, wall=wall)
        assert done.change is not None
        seen.append(done.change.clock)
    assert seen == sorted(seen)
    assert len(set(seen)) == 4


def test_the_server_clock_lives_in_the_head_of_the_home(alice_home: Home) -> None:
    done = put(alice_home, uuid.uuid4(), {"name": "a"})
    assert done.change is not None
    assert HomeHead.objects.get(home=alice_home).clock == done.change.clock


def test_a_field_is_stored_with_its_clock_and_its_device(alice_home: Home) -> None:
    row = uuid.uuid4()
    done = put(alice_home, row, {"name": "a", "notes": "n"})
    assert done.change is not None
    clocks = RowClocks.objects.get(home=alice_home, entity=ENTITY, row_id=row).clocks
    server = clock.device_to_b64(bytes(ServerDevice.objects.get(pk=1).device))
    assert clocks == {"name": [done.change.clock, server], "notes": [done.change.clock, server]}


def test_the_later_stamp_wins_per_field_and_the_other_field_is_kept(alice_home: Home) -> None:
    row = uuid.uuid4()
    put(alice_home, row, {"name": "web title", "notes": "web notes"})
    phone = put(alice_home, row, {"name": "phone title"}, stamp=stamp_at(WALL + 50))
    assert phone.change is not None
    assert phone.change.fields == {"name": "phone title"}
    clocks = RowClocks.objects.get(home=alice_home, entity=ENTITY, row_id=row).clocks
    assert clocks["name"][1] == PHONE_B64
    assert clocks["notes"][1] != PHONE_B64


def test_an_older_stamp_loses_and_says_who_won(alice_home: Home) -> None:
    row = uuid.uuid4()
    first = put(alice_home, row, {"name": "web"})
    assert first.change is not None
    older = put(alice_home, row, {"name": "phone"}, stamp=stamp_at(WALL - 1))
    assert older.change is None
    server = clock.device_to_b64(bytes(ServerDevice.objects.get(pk=1).device))
    assert older.lost == {"name": (first.change.clock, server)}
    assert Change.objects.filter(home=alice_home).count() == 1
    assert HomeHead.objects.get(home=alice_home).pos == 1


def test_a_write_that_partly_loses_records_only_what_won(alice_home: Home) -> None:
    row = uuid.uuid4()
    put(alice_home, row, {"name": "web"}, stamp=stamp_at(WALL + 100, device=OTHER))
    mixed = put(alice_home, row, {"name": "late", "notes": "new"}, stamp=stamp_at(WALL + 10))
    assert mixed.change is not None
    assert mixed.change.fields == {"notes": "new"}
    assert list(mixed.lost) == ["name"]


def test_equal_clocks_go_to_the_greater_device_in_any_order(alice_home: Home) -> None:
    row = uuid.uuid4()
    same = stamp_at(WALL + 5, 3, PHONE)
    greater = stamp_at(WALL + 5, 3, OTHER)
    low = put(alice_home, row, {"name": "low"}, stamp=same)
    high = put(alice_home, row, {"name": "high"}, stamp=greater)
    assert low.change is not None
    assert high.change is not None
    again = put(alice_home, row, {"name": "low again"}, stamp=same)
    assert again.change is None
    assert again.lost["name"] == (same[0], clock.device_to_b64(OTHER))


def test_the_same_stamp_twice_is_a_repeat(alice_home: Home) -> None:
    row = uuid.uuid4()
    stamp = stamp_at(WALL + 5)
    assert put(alice_home, row, {"name": "a"}, stamp=stamp).change is not None
    repeat = put(alice_home, row, {"name": "a"}, stamp=stamp)
    assert repeat.change is None
    assert Change.objects.filter(home=alice_home).count() == 1


def test_a_stamp_from_the_phone_moves_the_clock_of_the_home_on(alice_home: Home) -> None:
    put(alice_home, uuid.uuid4(), {"name": "a"}, stamp=stamp_at(WALL + 3_600_000))
    done = put(alice_home, uuid.uuid4(), {"name": "b"})
    assert done.change is not None
    assert clock.physical_ms(clock.parse_clock(done.change.clock)) >= WALL + 3_600_000


def test_a_stamp_a_week_ahead_does_not_move_the_clock_of_the_home(alice_home: Home) -> None:
    week = 7 * 24 * 3_600_000
    put(alice_home, uuid.uuid4(), {"name": "a"}, stamp=stamp_at(WALL + week))
    done = put(alice_home, uuid.uuid4(), {"name": "b"})
    assert done.change is not None
    assert clock.physical_ms(clock.parse_clock(done.change.clock)) < WALL + 60_000


def test_a_losing_write_still_moves_the_clock_of_the_home(alice_home: Home) -> None:
    """A field that reads as a later `updated_at` beats the write; the server has read its clock."""
    row = uuid.uuid4()
    updated = datetime.datetime.fromtimestamp((WALL + 100_000) / 1000, tz=datetime.UTC)
    lost = put(alice_home, row, {"name": "b"}, stamp=stamp_at(WALL + 500), updated_at=updated)
    assert lost.change is None
    saved = clock.parse_clock(HomeHead.objects.get(home=alice_home).clock)
    assert saved > clock.make_clock(WALL + 500, 0)


def test_a_field_with_no_clock_reads_as_the_updated_at_of_its_row(alice_home: Home) -> None:
    row = uuid.uuid4()
    updated = datetime.datetime.fromtimestamp((WALL + 1_000) / 1000, tz=datetime.UTC)
    stale = put(alice_home, row, {"name": "phone"}, stamp=stamp_at(WALL + 999), updated_at=updated)
    assert stale.change is None
    assert clock.parse_clock(stale.lost["name"][0]) == clock.make_clock(WALL + 1_000, 0)
    assert stale.lost["name"][1] == clock.device_to_b64(
        bytes(ServerDevice.objects.get(pk=1).device)
    )
    fresh = put(
        alice_home, row, {"name": "phone"}, stamp=stamp_at(WALL + 1_001), updated_at=updated
    )
    assert fresh.change is not None


def test_the_updated_at_gives_way_to_a_clock_the_field_has(alice_home: Home) -> None:
    row = uuid.uuid4()
    put(alice_home, row, {"name": "first"}, stamp=stamp_at(WALL - 5_000))
    updated = datetime.datetime.fromtimestamp((WALL + 60_000) / 1000, tz=datetime.UTC)
    done = put(
        alice_home, row, {"name": "second"}, stamp=stamp_at(WALL - 4_000), updated_at=updated
    )
    assert done.change is not None  # the field has its own clock; the later updated_at is not read


def test_a_field_the_server_does_not_know_is_kept_in_extra(alice_home: Home) -> None:
    row = uuid.uuid4()
    done = put(
        alice_home,
        row,
        {"name": "a", "future": {"k": [1, 2]}},
        stamp=stamp_at(WALL + 5),
        known={"name"},
    )
    assert done.change is not None
    assert done.change.fields == {"name": "a", "future": {"k": [1, 2]}}
    state = RowClocks.objects.get(home=alice_home, entity=ENTITY, row_id=row)
    assert state.extra == {"future": [{"k": [1, 2]}, done.change.clock, PHONE_B64]}
    assert "future" not in state.clocks


def test_an_unknown_field_merges_by_the_same_rule(alice_home: Home) -> None:
    row = uuid.uuid4()
    kw = {"known": {"name"}}
    put(alice_home, row, {"future": "new"}, stamp=stamp_at(WALL + 20, device=OTHER), **kw)
    older = put(alice_home, row, {"future": "old"}, stamp=stamp_at(WALL + 10), **kw)
    assert older.change is None
    assert older.lost == {
        "future": (clock.format_clock(clock.make_clock(WALL + 20)), clock.device_to_b64(OTHER))
    }
    newer = put(alice_home, row, {"future": "newest"}, stamp=stamp_at(WALL + 30), **kw)
    assert newer.change is not None
    extra = RowClocks.objects.get(home=alice_home, entity=ENTITY, row_id=row).extra
    assert extra["future"][0] == "newest"


def test_a_known_field_and_an_unknown_one_of_the_same_name_do_not_meet(alice_home: Home) -> None:
    row = uuid.uuid4()
    put(alice_home, row, {"name": "a"}, stamp=stamp_at(WALL + 50), known={"name"})
    done = put(alice_home, row, {"name": "b"}, stamp=stamp_at(WALL + 10), known={"other"})
    assert done.change is not None  # for this caller `name` is unknown: its own clock
    state = RowClocks.objects.get(home=alice_home, entity=ENTITY, row_id=row)
    assert "name" in state.clocks
    assert "name" in state.extra


def test_a_deletion_is_a_change_that_writes_deleted_at(alice_home: Home) -> None:
    row = uuid.uuid4()
    put(alice_home, row, {"name": "a"})
    done = changes.record_change(alice_home, ENTITY, row, changes.DELETE, wall=WALL)
    assert done.change is not None
    assert done.change.kind == "delete"
    assert done.change.fields == {}
    state = RowClocks.objects.get(home=alice_home, entity=ENTITY, row_id=row)
    assert state.clocks["deleted_at"][0] == done.change.clock
    assert state.tombstoned_at is not None


def test_a_deletion_is_never_an_unknown_field(alice_home: Home) -> None:
    row = uuid.uuid4()
    changes.record_change(
        alice_home, ENTITY, row, changes.DELETE, stamp=stamp_at(WALL + 10), known={"name"}
    )
    state = RowClocks.objects.get(home=alice_home, entity=ENTITY, row_id=row)
    assert "deleted_at" in state.clocks
    assert state.extra == {}


def test_a_restoration_clears_the_tombstone_only_with_a_later_clock(alice_home: Home) -> None:
    row = uuid.uuid4()
    changes.record_change(alice_home, ENTITY, row, changes.DELETE, stamp=stamp_at(WALL + 10))
    old = changes.record_change(
        alice_home, ENTITY, row, changes.RESTORE, stamp=stamp_at(WALL + 5), wall=WALL
    )
    assert old.change is None
    assert RowClocks.objects.get(home=alice_home, row_id=row).tombstoned_at is not None
    late = changes.record_change(
        alice_home, ENTITY, row, changes.RESTORE, stamp=stamp_at(WALL + 20), wall=WALL
    )
    assert late.change is not None
    assert late.change.kind == "restore"
    assert RowClocks.objects.get(home=alice_home, row_id=row).tombstoned_at is None


def test_an_edit_of_another_field_does_not_bring_a_deleted_row_back(alice_home: Home) -> None:
    row = uuid.uuid4()
    changes.record_change(alice_home, ENTITY, row, changes.DELETE, wall=WALL)
    put(alice_home, row, {"name": "edited"})
    assert RowClocks.objects.get(home=alice_home, row_id=row).tombstoned_at is not None


def test_a_deletion_that_loses_leaves_the_row_alive(alice_home: Home) -> None:
    row = uuid.uuid4()
    changes.record_change(alice_home, ENTITY, row, changes.DELETE, stamp=stamp_at(WALL + 10))
    changes.record_change(alice_home, ENTITY, row, changes.RESTORE, stamp=stamp_at(WALL + 20))
    older = changes.record_change(
        alice_home, ENTITY, row, changes.DELETE, stamp=stamp_at(WALL + 15)
    )
    assert older.change is None
    assert RowClocks.objects.get(home=alice_home, row_id=row).tombstoned_at is None


@pytest.mark.parametrize(
    "arguments",
    [
        {"kind": "erase"},
        {"kind": "erase", "fields": None},
        {"entity": "Room"},
        {"entity": "rooms.Room"},
        {"entity": "rooms."},
        {"kind": "delete", "fields": {"name": "x"}},
        {"fields": {}},
        {"fields": None},
    ],
)
def test_a_malformed_change_is_refused(alice_home: Home, arguments: dict[str, object]) -> None:
    call = {"entity": ENTITY, "kind": "put", "fields": {"name": "x"}} | arguments
    with pytest.raises(ValueError, match=r"Unknown kind|entity type|at least one field|no field"):
        changes.record_change(
            alice_home, call["entity"], uuid.uuid4(), call["kind"], call["fields"]
        )  # type: ignore[arg-type]
    assert not Change.objects.exists()


def test_a_stamp_must_be_a_clock_and_sixteen_bytes(alice_home: Home) -> None:
    with pytest.raises(ValueError, match="16 lower-case"):
        put(alice_home, uuid.uuid4(), {"name": "x"}, stamp=("zz", PHONE))
    with pytest.raises(ValueError, match="16 bytes"):
        put(alice_home, uuid.uuid4(), {"name": "x"}, stamp=("0" * 16, b"short"))
    assert not Change.objects.exists()


def test_a_change_of_a_home_that_does_not_exist_is_refused() -> None:
    with pytest.raises(Home.DoesNotExist):
        changes.record_change(uuid.uuid4(), ENTITY, uuid.uuid4(), changes.PUT, {"name": "x"})


def test_a_soft_deleted_home_still_takes_changes(alice_home: Home) -> None:
    alice_home.soft_delete()
    assert put(alice_home, uuid.uuid4(), {"name": "x"}).change is not None


def test_the_row_of_the_home_is_locked_before_the_position_is_read(alice_home: Home) -> None:
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as context:
        put(alice_home, uuid.uuid4(), {"name": "x"})
    sql = [q["sql"] for q in context.captured_queries]
    first_head = next(i for i, s in enumerate(sql) if "sync_homehead" in s)
    lock = next(i for i, s in enumerate(sql) if 'FROM "homes_home"' in s)
    assert lock < first_head
    if connection.features.has_select_for_update:
        assert "FOR UPDATE" in sql[lock]


def test_the_row_of_the_home_is_locked_for_update(alice_home: Home) -> None:
    from unittest import mock

    from django.db.models import QuerySet

    original = QuerySet.select_for_update
    with mock.patch.object(
        QuerySet, "select_for_update", autospec=True, side_effect=original
    ) as spy:
        put(alice_home, uuid.uuid4(), {"name": "x"})
    homes = [c for c in spy.call_args_list if c.args[0].model is Home]
    assert len(homes) == 1


def test_two_changes_cannot_share_a_position_in_a_home(alice_home: Home) -> None:
    from django.db import IntegrityError

    done = put(alice_home, uuid.uuid4(), {"name": "x"})
    assert done.change is not None
    with pytest.raises(IntegrityError), transaction.atomic():
        Change.objects.create(
            home=alice_home,
            pos=done.change.pos,
            entity=ENTITY,
            row_id=uuid.uuid4(),
            kind="put",
            clock="0" * 16,
            device=bytes(16),
        )


def test_the_server_device_is_made_once_and_kept() -> None:
    first = changes.server_device()
    assert len(first) == 16
    assert changes.server_device() == first
    assert ServerDevice.objects.count() == 1


def test_a_database_without_its_device_gets_one_and_keeps_it() -> None:
    ServerDevice.objects.all().delete()
    made = changes.server_device()
    assert len(made) == 16
    assert changes.server_device() == made


def test_the_first_migration_makes_the_device_and_never_replaces_it() -> None:
    import importlib

    from django.apps import apps

    migration = importlib.import_module("housecairn.sync.migrations.0001_initial")
    ServerDevice.objects.all().delete()
    migration.create_server_device(apps, None)
    made = bytes(ServerDevice.objects.get().device)
    assert len(made) == 16
    migration.create_server_device(apps, None)
    assert bytes(ServerDevice.objects.get().device) == made


def test_only_one_device_row_can_exist() -> None:
    from django.db import IntegrityError

    with pytest.raises(IntegrityError), transaction.atomic():
        ServerDevice.objects.create(pk=2, device=bytes(16))


# Concurrency: PostgreSQL only. SQLite writes one transaction at a time.


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql", reason="The lock of the home's row is PostgreSQL's"
)
def test_positions_have_no_gap_and_no_repeat_under_concurrent_writers() -> None:
    home = Home.objects.create(name="Busy")
    writers, each = 6, 15
    rows = [uuid.uuid4() for _ in range(3)]
    errors: list[BaseException] = []
    barrier = threading.Barrier(writers)

    def work(n: int) -> None:
        try:
            barrier.wait(timeout=30)
            for i in range(each):
                changes.record_change(home, ENTITY, rows[i % 3], changes.PUT, {"name": f"w{n}-{i}"})
        except BaseException as error:
            errors.append(error)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=work, args=(n,)) for n in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert errors == []
    positions = sorted(Change.objects.filter(home=home).values_list("pos", flat=True))
    assert positions == list(range(1, writers * each + 1))
    assert HomeHead.objects.get(home=home).pos == writers * each
    # The order of the positions is the order of the clocks of the server.
    by_pos = list(Change.objects.filter(home=home).order_by("pos").values_list("clock", flat=True))
    assert by_pos == sorted(by_pos)
    assert len(set(by_pos)) == len(by_pos)
