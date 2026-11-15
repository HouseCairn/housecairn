"""The write paths of homes and rooms record their change (docs/adr/0077, 0079; lot M3b).

The guard (`tests/test_sync_guard.py`) proves that each write of a model records a change;
this module reads what is recorded: which fields, which values, which kind, and that a form
that changes one field gives a clock to that field only.
"""

import datetime
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.db import models
from django.utils import timezone

from housecairn.accounts.models import User
from housecairn.homes import restore, services
from housecairn.homes.models import Home
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Item, Photo, Room
from housecairn.sync import clock, retention, rows
from housecairn.sync.models import Change, RowClocks
from housecairn.tasks import services as task_services
from tests import test_sync_guard
from tests.data_support import archive_bytes, fill_home
from tests.rooms_support import image_bytes, upload
from tests.test_restore_security import edit, entries, rebuild
from tests.test_sync_guard import KINDS, guard_problems

pytest_plugins = ("tests.rooms_fixtures",)
pytestmark = pytest.mark.django_db


def feed(home: Home, entity: str | None = None) -> list[Change]:
    found = Change.objects.filter(home=home)
    if entity is not None:
        found = found.filter(entity=entity)
    return list(found.order_by("pos"))


def last(home: Home, entity: str) -> Change:
    return feed(home, entity)[-1]


# ---------- the home ----------


def test_creating_a_home_records_its_fields_and_not_the_rule_of_the_instance(
    alice: User,
) -> None:
    home = services.create_home(alice, name="Cottage", currency="GBP", hemisphere="south")
    [change] = feed(home)
    assert (change.entity, change.row_id, change.kind) == ("homes.home", home.pk, "put")
    assert change.fields == {
        "name": "Cottage",
        "currency": "GBP",
        "hemisphere": "south",
        "archived_at": None,
    }
    assert change.pos == 1


def test_changing_a_home_records_the_fields_that_changed_only(
    alice: User, alice_home: Home
) -> None:
    services.update_home(alice, alice_home, name="Main house", currency="USD")
    assert last(alice_home, "homes.home").fields == {"currency": "USD"}


def test_a_home_saved_with_the_same_values_records_nothing(alice: User, alice_home: Home) -> None:
    before = len(feed(alice_home))
    services.update_home(alice, alice_home, name="Main house", currency="EUR")
    assert len(feed(alice_home)) == before


def test_the_hemisphere_alone_is_recorded_when_it_alone_changes(
    alice: User, alice_home: Home
) -> None:
    services.update_home(alice, alice_home, hemisphere="south")
    assert last(alice_home, "homes.home").fields == {"hemisphere": "south"}


def test_the_clock_of_a_field_nobody_touched_stays_with_its_first_writer(
    alice: User, alice_home: Home
) -> None:
    """A form sends every field; only the changed one gets a new clock."""
    state = RowClocks.objects.get(home=alice_home, entity="homes.home")
    name_clock = state.clocks["name"]
    services.update_home(alice, alice_home, name="Main house", currency="USD")
    state.refresh_from_db()
    assert state.clocks["name"] == name_clock
    assert state.clocks["currency"] != name_clock


def test_an_archive_made_on_the_web_reads_as_the_clock_of_its_write(
    alice: User, alice_home: Home
) -> None:
    services.archive_home(alice, alice_home)
    change = last(alice_home, "homes.home")
    assert change.fields == {"archived_at": change.clock}
    assert len(change.clock) == 16


def test_unarchiving_records_an_empty_value_and_archiving_twice_records_nothing(
    alice: User, alice_home: Home
) -> None:
    services.archive_home(alice, alice_home)
    after_archive = len(feed(alice_home))
    services.archive_home(alice, alice_home)
    assert len(feed(alice_home)) == after_archive
    services.unarchive_home(alice, alice_home)
    assert last(alice_home, "homes.home").fields == {"archived_at": None}


def test_the_sign_in_rule_of_a_home_is_not_synced(alice: User, alice_home: Home) -> None:
    before = len(feed(alice_home))
    services.set_required_provider(
        alice, alice_home, "acme", declared=["acme"], signed_in_with=frozenset({"acme"})
    )
    services.lift_unavailable_requirement(alice, alice_home, declared=[])
    assert len(feed(alice_home)) == before
    assert Home.objects.get(pk=alice_home.pk).required_provider == ""


def test_lifting_every_unavailable_requirement_writes_the_homes_without_a_change(
    alice: User, alice_home: Home
) -> None:
    Home.objects.filter(pk=alice_home.pk).update(required_provider="gone")
    before = len(feed(alice_home))
    assert services.lift_all_unavailable_requirements(declared=[]) == 1
    assert Home.objects.get(pk=alice_home.pk).required_provider == ""
    assert len(feed(alice_home)) == before


# ---------- rooms ----------


def test_a_new_room_records_every_field_under_its_column_names(
    alice: User, alice_home: Home
) -> None:
    room = room_services.create_room(alice, alice_home, name="Hall", kind="hallway")
    change = last(alice_home, "rooms.room")
    assert (change.row_id, change.kind) == (room.pk, "put")
    assert change.fields == {
        "name": "Hall",
        "kind": "hallway",
        "position": 0,
        "notes": "",
        "paint_colour": "",
        "paint_brand": "",
        "paint_reference": "",
        "paint_batch": "",
        "paint_article_id": None,
        "floor_covering": "",
        "wall_covering": "",
        "memory_notes": "",
    }
    assert json.dumps(change.fields)  # plain JSON values


def test_updating_a_room_records_the_fields_that_changed_only(
    alice: User, owner_room: Room
) -> None:
    room_services.update_room(alice, owner_room, name="Living room", notes="North side")
    assert last(owner_room.home, "rooms.room").fields == {"notes": "North side"}


def test_an_update_that_changes_nothing_records_nothing(alice: User, owner_room: Room) -> None:
    before = len(feed(owner_room.home))
    room_services.update_room(alice, owner_room, name="Living room")
    assert len(feed(owner_room.home)) == before


def test_the_write_is_compared_with_the_database_not_with_the_instance(
    alice: User, owner_room: Room
) -> None:
    """A stale instance saved with the value the database already has changes nothing."""
    stale = Room.objects.get(pk=owner_room.pk)
    Room.objects.filter(pk=owner_room.pk).update(name="Changed elsewhere")
    stale.name = "Changed elsewhere"
    before = len(feed(owner_room.home))
    rows.save(stale)
    assert len(feed(owner_room.home)) == before


def test_reordering_records_the_rooms_whose_position_changed(alice: User, alice_home: Home) -> None:
    a, b, c = (room_services.create_room(alice, alice_home, name=n) for n in "abc")
    before = len(feed(alice_home, "rooms.room"))
    room_services.reorder_rooms(alice, alice_home, [a.pk, c.pk, b.pk])
    recorded = feed(alice_home, "rooms.room")[before:]
    assert {(c_.row_id, tuple(c_.fields.items())) for c_ in recorded} == {
        (c.pk, (("position", 1),)),
        (b.pk, (("position", 2),)),
    }


def test_deleting_a_room_deletes_its_items_and_photos_with_a_tombstone_each(
    alice: User, owner_room: Room, owner_item: Item, media_root: Path
) -> None:
    on_room = room_services.add_photo(alice, room=owner_room, upload=upload(image_bytes()))
    on_item = room_services.add_photo(alice, item=owner_item, upload=upload(image_bytes()))
    room_services.delete_room(alice, owner_room)
    home = owner_room.home
    for entity, row in (
        ("rooms.room", owner_room),
        ("rooms.item", owner_item),
        ("rooms.photo", on_room),
        ("rooms.photo", on_item),
    ):
        change = [c for c in feed(home, entity) if c.row_id == row.pk][-1]
        assert (change.kind, change.fields) == ("delete", {})
        assert RowClocks.objects.get(home=home, entity=entity, row_id=row.pk).tombstoned_at


# ---------- items ----------


def test_a_new_item_records_its_fields_and_the_room_by_its_column(
    alice: User, owner_room: Room
) -> None:
    item = room_services.create_item(
        alice,
        owner_room,
        name="Boiler",
        installed_on=datetime.date(2024, 3, 1),
        links=[{"url": "https://example.com"}],
    )
    change = last(owner_room.home, "rooms.item")
    assert change.row_id == item.pk
    assert change.fields["room_id"] == str(owner_room.pk)
    assert change.fields["installed_on"] == "2024-03-01"
    assert change.fields["links"] == [{"url": "https://example.com"}]
    assert change.fields["warranty_ends_on"] is None
    assert "room" not in change.fields
    assert "home_id" not in change.fields


def test_moving_an_item_records_its_new_room_only(
    alice: User, owner_room: Room, owner_item: Item
) -> None:
    other = room_services.create_room(alice, owner_room.home, name="Cellar")
    room_services.update_item(alice, owner_item, room=other)
    assert last(owner_room.home, "rooms.item").fields == {"room_id": str(other.pk)}


def test_deleting_an_item_deletes_its_photos(
    alice: User, owner_item: Item, media_root: Path
) -> None:
    photo = room_services.add_photo(alice, item=owner_item, upload=upload(image_bytes()))
    room_services.delete_item(alice, owner_item)
    home = owner_item.home
    assert last(home, "rooms.item").kind == "delete"
    assert [c.kind for c in feed(home, "rooms.photo") if c.row_id == photo.pk] == [
        "put",
        "delete",
    ]


def test_deleting_a_contractor_clears_the_contract_of_its_items_one_change_each(
    alice: User, owner_room: Room
) -> None:
    contractor = task_services.create_contractor(
        alice, owner_room.home, name="Acme Heating", trade="other", trade_other="Maintenance"
    )
    mine = room_services.create_item(
        alice, owner_room, name="Boiler", contract_contractor=contractor, contract_number="C-1"
    )
    other = room_services.create_item(alice, owner_room, name="Oven")
    before = len(feed(owner_room.home, "rooms.item"))
    assert room_services.clear_contract_contractor(contractor) == 1
    recorded = feed(owner_room.home, "rooms.item")[before:]
    assert [(c.row_id, c.fields) for c in recorded] == [(mine.pk, {"contract_contractor_id": None})]
    assert Item.objects.get(pk=mine.pk).contract_number == "C-1"
    assert Item.objects.get(pk=other.pk).contract_contractor_id is None


# ---------- photos ----------


def test_a_new_photo_records_its_file_reference_and_not_its_thumbnail(
    alice: User, owner_room: Room, media_root: Path
) -> None:
    photo = room_services.add_photo(
        alice, room=owner_room, upload=upload(image_bytes()), caption="Window", category="before"
    )
    change = last(owner_room.home, "rooms.photo")
    assert change.row_id == photo.pk
    assert change.fields == {
        "room_id": str(owner_room.pk),
        "item_id": None,
        "category": "before",
        "caption": "Window",
        "attribution": {},
        "image": {
            "file": str(photo.pk),
            "size": photo.size_bytes,
            "type": "image/jpeg",
            "width": photo.width,
            "height": photo.height,
        },
    }


def test_editing_a_caption_records_the_caption_only(
    alice: User, owner_room: Room, media_root: Path
) -> None:
    photo = room_services.add_photo(alice, room=owner_room, upload=upload(image_bytes()))
    room_services.update_photo(alice, photo, caption="After")
    assert last(owner_room.home, "rooms.photo").fields == {"caption": "After"}


def test_a_photo_refused_after_its_files_were_written_records_nothing(
    alice: User, owner_room: Room, media_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(instance: Any, **kwargs: Any) -> None:
        raise RuntimeError("refused")

    before = len(feed(owner_room.home))
    monkeypatch.setattr(Photo, "save", refuse)
    with pytest.raises(RuntimeError):
        room_services.add_photo(alice, room=owner_room, upload=upload(image_bytes()))
    assert len(feed(owner_room.home)) == before


# ---------- restoring an archive ----------


@pytest.fixture
def exported(
    media_root: Path,
    alice: User,
    alice_home: Home,
    make_user: Callable[..., User],
    add_member: Callable[..., Any],
) -> bytes:
    from housecairn.homes.models import Role

    member = make_user("member@example.com")
    add_member(alice_home, member, Role.MEMBER)
    fill_home(alice, member, alice_home)
    data = archive_bytes(alice_home)
    services.erase_home(alice_home)
    return data


@pytest.mark.parametrize("as_copy", [False, True])
def test_restoring_an_archive_records_the_home_and_every_row(
    exported: bytes, alice: User, as_copy: bool
) -> None:
    result = restore.restore_bytes(alice, exported, as_copy=as_copy)
    home = result.home
    assert feed(home, "homes.home")[0].fields["name"] == home.name
    for model in (Room, Item, Photo):
        label = model._meta.label_lower
        live = list(model.objects.filter(home=home))
        assert live
        recorded = {c.row_id for c in feed(home, label) if c.kind == "put"}
        assert recorded == {row.pk for row in live}, label
        assert RowClocks.objects.filter(home=home, entity=label).count() == len(live)
    # Whatever the application: the rows of the other lots are recorded as well.
    task_changes = feed(home, "tasks.task")
    assert task_changes
    assert all(c.pos > 0 for c in feed(home))
    positions = [c.pos for c in feed(home)]
    assert positions == list(range(1, len(positions) + 1))


def test_an_updated_at_a_day_ahead_of_the_clock_is_refused_in_a_row_and_in_a_home(
    exported: bytes, alice: User
) -> None:
    far = (timezone.now() + datetime.timedelta(days=2)).isoformat()

    def in_a_room(document: dict[str, Any]) -> None:
        document["sections"]["rooms"][0]["updated_at"] = far

    def in_the_home(document: dict[str, Any]) -> None:
        document["home"]["updated_at"] = far

    for app, change in (("rooms", in_a_room), ("homes", in_the_home)):
        with pytest.raises(restore.ArchiveRefusedError, match="not valid"):
            restore.restore_bytes(alice, rebuild(edit(entries(exported), app, change)))
    assert not Home.all_objects.filter(name="Main house").exists()


def test_an_updated_at_within_a_day_of_the_clock_is_accepted(exported: bytes, alice: User) -> None:
    near = (timezone.now() + datetime.timedelta(hours=23)).isoformat()

    def in_a_room(document: dict[str, Any]) -> None:
        document["sections"]["rooms"][0]["updated_at"] = near

    result = restore.restore_bytes(alice, rebuild(edit(entries(exported), "rooms", in_a_room)))
    assert Room.objects.filter(home=result.home).exists()


# ---------- the retention purge ----------

NOW = datetime.datetime(2026, 11, 15, 3, 30, tzinfo=datetime.UTC)


def test_the_purge_of_a_room_leaves_no_clock_and_no_file_of_its_children(
    alice: User,
    owner_room: Room,
    owner_item: Item,
    media_root: Path,
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    on_room = room_services.add_photo(alice, room=owner_room, upload=upload(image_bytes()))
    on_item = room_services.add_photo(alice, item=owner_item, upload=upload(image_bytes()))
    files = [f.name for p in (on_room, on_item) for f in (p.image, p.thumbnail)]
    storage = on_room.image.storage
    assert all(storage.exists(name) for name in files)
    room_services.delete_room(alice, owner_room)
    # Only the room is old enough: its children were deleted later, within the 400 days.
    old = NOW - datetime.timedelta(days=401)
    RowClocks.objects.filter(entity="rooms.room", row_id=owner_room.pk).update(tombstoned_at=old)
    RowClocks.objects.exclude(entity="rooms.room").update(tombstoned_at=NOW)
    with django_capture_on_commit_callbacks(execute=True):
        done = retention.purge(NOW)
    assert done.tombstones == 1
    assert not Room.all_objects.filter(pk=owner_room.pk).exists()
    assert not Item.all_objects.filter(pk=owner_item.pk).exists()
    assert not Photo.all_objects.exists()
    assert not RowClocks.objects.filter(entity__in=["rooms.item", "rooms.photo"]).exists()
    assert not any(storage.exists(name) for name in files)


def test_the_purge_of_a_photo_removes_its_files(
    alice: User,
    owner_room: Room,
    media_root: Path,
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    photo = room_services.add_photo(alice, room=owner_room, upload=upload(image_bytes()))
    names = [photo.image.name, photo.thumbnail.name]
    storage = photo.image.storage
    room_services.delete_photo(alice, photo)
    RowClocks.objects.filter(entity="rooms.photo").update(
        tombstoned_at=NOW - datetime.timedelta(401)
    )
    with django_capture_on_commit_callbacks(execute=True):
        retention.purge(NOW)
    assert not any(storage.exists(name) for name in names)
    assert Room.objects.filter(pk=owner_room.pk).exists()


# ---------- the guard, mutated ----------


def problems(home: Home) -> list[str]:
    return guard_problems(
        home,
        test_sync_guard.COVERED_APPS,
        test_sync_guard.SCENARIOS,
        test_sync_guard.NOT_APPLICABLE,
    )


def test_the_guard_passes_on_homes_and_rooms(alice_home: Home, media_root: Path) -> None:
    assert problems(alice_home) == []
    assert {"homes", "rooms"} <= set(test_sync_guard.COVERED_APPS)


def test_the_guard_fails_when_a_save_forgets_its_change(
    alice_home: Home, media_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rows, "save", lambda instance, **kwargs: instance.save(**kwargs))
    found = problems(alice_home)
    for label in ("homes.Home", "rooms.Room", "rooms.Item", "rooms.Photo"):
        assert f"{label} save: the write records no change" in found


def test_the_guard_fails_when_a_queryset_deletion_forgets_its_tombstones(
    alice_home: Home, media_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def plain(rows_: models.QuerySet[Any]) -> int:
        return rows_.update(deleted_at=timezone.now())

    monkeypatch.setattr(rows, "soft_delete_queryset", plain)
    found = problems(alice_home)
    assert "rooms.Item queryset_update: the write records no change" in found
    assert "rooms.Photo queryset_update: the write records no change" in found


def test_the_guard_fails_when_a_soft_deletion_forgets_its_tombstone(
    alice_home: Home, media_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rows, "soft_delete", lambda instance: instance.soft_delete())
    found = problems(alice_home)
    for label in ("rooms.Room", "rooms.Item", "rooms.Photo"):
        assert f"{label} soft_delete: the write records no change" in found


def test_every_kind_of_every_covered_model_has_a_scenario_or_a_reason() -> None:
    covered = [
        model
        for model in test_sync_guard.home_models()
        if model._meta.app_label in {"homes", "rooms"}
    ]
    assert covered
    for model in covered:
        for kind in KINDS:
            key = (model._meta.label, kind)
            assert (key in test_sync_guard.SCENARIOS) != (key in test_sync_guard.NOT_APPLICABLE)


# ---------- the values ----------

ENCODABLE = (
    models.BooleanField,
    models.CharField,
    models.DateField,
    models.DateTimeField,
    models.DecimalField,
    models.FileField,
    models.ForeignKey,
    models.IntegerField,
    models.JSONField,
    models.OneToOneField,
    models.TextField,
    models.TimeField,
    models.UUIDField,
)


def test_every_column_of_the_models_of_a_home_has_a_way_to_be_sent() -> None:
    """A restore records the rows of every application: no column may end in an error."""
    unsupported = [
        f"{model._meta.label}.{f.name}"
        for model in test_sync_guard.home_models()
        for f in model._meta.concrete_fields
        if not isinstance(f, ENCODABLE)
    ]
    assert unsupported == []


def test_values_are_written_as_the_protocol_asks() -> None:
    import decimal
    import uuid

    home = Home(name="x")
    assert rows.encode(Home, "name", "x") == "x"
    assert rows.encode(Room, "n", datetime.date(2026, 11, 15)) == "2026-11-15"
    assert rows.encode(Room, "n", decimal.Decimal("12.50")) == "12.50"
    assert rows.encode(Room, "n", uuid.UUID(int=1)) == str(uuid.UUID(int=1))
    assert rows.encode(Room, "n", datetime.time(8, 30)) == "08:30:00"
    instant = datetime.datetime(2026, 11, 15, 3, 30, tzinfo=datetime.UTC)
    assert rows.encode(Room, "n", instant) == clock.format_clock(clock.clock_of_instant(instant))
    with pytest.raises(TypeError):
        rows.encode(Room, "n", object())
    assert home.archived_at is None
