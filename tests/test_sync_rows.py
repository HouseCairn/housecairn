"""The one helper of the change log, `sync/rows.py`, and the purge that uses it (ADR 0077, 0079).

Every lot of the change log (M3b to M3e) writes through this module; these tests pin what it
promises them: derived fields are never sent, `save(update_fields=...)` compares the fields it
writes only, a queryset or a row is soft-deleted with a tombstone each, a restoration is
recorded, and the retention purge records the references it empties.
"""

import datetime
import decimal
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps

from housecairn.accounts.models import User
from housecairn.homes import restore, services
from housecairn.homes.models import Home, Role
from housecairn.rooms.models import Item, Room
from housecairn.stock.models import Article
from housecairn.sync import retention, rows
from housecairn.sync.models import Change, RowClocks
from housecairn.tasks.models import Contractor, Occurrence
from tests.data_support import archive_bytes, fill_home

pytest_plugins = ("tests.rooms_fixtures",)
pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 11, 15, 3, 30, tzinfo=datetime.UTC)


def feed(home: Home, entity: str | None = None) -> list[Change]:
    found = Change.objects.filter(home=home)
    if entity is not None:
        found = found.filter(entity=entity)
    return list(found.order_by("pos"))


# ---------- derived fields ----------


def test_a_derived_field_is_never_a_field_of_a_change() -> None:
    assert rows.derived(Occurrence) == frozenset({"visible_from"})
    occurrence = Occurrence(due_on=datetime.date(2026, 11, 20))
    occurrence.visible_from = datetime.date(2026, 11, 13)
    assert "visible_from" not in rows.raw_values(occurrence)
    assert "due_on" in rows.raw_values(occurrence)


def test_restoring_an_archive_records_no_derived_field(
    media_root: Path,
    alice: User,
    alice_home: Home,
    make_user: Callable[..., User],
    add_member: Callable[..., Any],
) -> None:
    member = make_user("member@example.com")
    add_member(alice_home, member, Role.MEMBER)
    fill_home(alice, member, alice_home)
    data = archive_bytes(alice_home)
    services.erase_home(alice_home)

    home = restore.restore_bytes(alice, data, as_copy=True).home
    occurrences = feed(home, "tasks.occurrence")
    assert occurrences, "the archive holds an occurrence"
    assert all("due_on" in c.fields for c in occurrences)
    leaked = [
        (c.entity, name)
        for c in feed(home)
        for name in c.fields
        if name in rows.derived(apps.get_model(c.entity))
    ]
    assert leaked == []


# ---------- save(update_fields=...) ----------


def test_saving_some_fields_records_those_fields_only(alice: User, owner_room: Room) -> None:
    before = len(feed(owner_room.home))
    owner_room.name = "Study"
    owner_room.paint_colour = "Sage"  # changed on the object, not written
    recorded = rows.save(owner_room, update_fields=["name", "updated_at"])
    assert recorded is not None
    [change] = feed(owner_room.home)[before:]
    assert change.fields == {"name": "Study"}
    assert Room.objects.get(pk=owner_room.pk).paint_colour == ""


def test_saving_some_fields_that_did_not_change_records_nothing(owner_room: Room) -> None:
    before = len(feed(owner_room.home))
    owner_room.paint_colour = "Sage"
    assert rows.save(owner_room, update_fields=["name", "updated_at"]) is None
    assert len(feed(owner_room.home)) == before


# ---------- soft_delete and restore ----------


def test_soft_delete_takes_a_queryset_and_writes_a_tombstone_per_row(
    owner_room: Room, owner_item: Item
) -> None:
    other = Item.objects.create(home=owner_room.home, room=owner_room, name="Boiler")
    deleted = rows.soft_delete(Item.objects.filter(room=owner_room))
    assert deleted == 2
    tombstones = {c.row_id for c in feed(owner_room.home, "rooms.item") if c.kind == "delete"}
    assert tombstones == {owner_item.pk, other.pk}
    assert rows.soft_delete(Item.all_objects.filter(room=owner_room)) == 0


def test_restoring_a_row_records_its_restoration_once(owner_item: Item) -> None:
    rows.soft_delete(owner_item)
    recorded = rows.restore(owner_item)
    assert recorded is not None
    change = feed(owner_item.home, "rooms.item")[-1]
    assert (change.kind, change.row_id, change.fields) == ("restore", owner_item.pk, {})
    assert Item.objects.filter(pk=owner_item.pk).exists()
    assert rows.restore(owner_item) is None


def test_an_amount_is_written_with_the_places_of_its_field() -> None:
    assert rows.encode(Article, "threshold", decimal.Decimal("2.5")) == "2.50"
    assert rows.encode(Article, "threshold", decimal.Decimal("1E+1")) == "10.00"


# ---------- the purge records the references it empties ----------


def _tombstone_long_ago(row: Any) -> None:
    rows.soft_delete(row)
    RowClocks.objects.filter(entity=rows.entity_of(type(row)), row_id=row.pk).update(
        tombstoned_at=NOW - datetime.timedelta(days=401)
    )


def test_purging_an_article_records_the_room_whose_paint_it_was(
    alice_home: Home, owner_room: Room
) -> None:
    article = Article.objects.create(home=alice_home, name="Tin of paint")
    rows.update_queryset(Room.objects.filter(pk=owner_room.pk), paint_article=article)
    _tombstone_long_ago(article)
    before = len(feed(alice_home, "rooms.room"))

    done = retention.purge(NOW)

    assert (done.tombstones, done.rows) == (1, 1)
    assert Room.objects.get(pk=owner_room.pk).paint_article_id is None
    changes = feed(alice_home, "rooms.room")[before:]
    assert [(c.kind, c.row_id, c.fields) for c in changes] == [
        ("put", owner_room.pk, {"paint_article_id": None})
    ]


def test_purging_a_contractor_records_the_items_whose_contract_it_held(
    alice_home: Home, owner_item: Item
) -> None:
    contractor = Contractor.objects.create(home=alice_home, name="Heating company")
    rows.update_queryset(Item.objects.filter(pk=owner_item.pk), contract_contractor=contractor)
    _tombstone_long_ago(contractor)
    before = len(feed(alice_home, "rooms.item"))

    retention.purge(NOW)

    assert Item.objects.get(pk=owner_item.pk).contract_contractor_id is None
    changes = feed(alice_home, "rooms.item")[before:]
    assert [(c.row_id, c.fields) for c in changes] == [
        (owner_item.pk, {"contract_contractor_id": None})
    ]


def test_a_purge_that_empties_no_reference_records_nothing(
    alice_home: Home, owner_room: Room
) -> None:
    article = Article.objects.create(home=alice_home, name="Tin of paint")
    _tombstone_long_ago(article)
    before = len(feed(alice_home))
    retention.purge(NOW)
    assert len(feed(alice_home)) == before
