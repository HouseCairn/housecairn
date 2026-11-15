"""The change log of the stock and of the reports (docs/adr/0077, 0079, lot M3d).

Every write path of `stock` (article, supplier, movement, the rooms and the compatible items of
an article) and of `reports` records its change, for the fields whose value changed only; the
soft deletions by queryset write tombstones; a hard deletion leaves no clock and no file behind.
The scenarios below feed the guard of `tests/test_sync_guard.py`: each runs the code the
application runs for that write, on rows made without going through it, so that only the code
under test can record the change.
"""

import datetime
import re
import uuid
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import models

from housecairn.accounts.models import User
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.reports import services as report_services
from housecairn.reports.models import Report
from housecairn.rooms.models import Item, Room
from housecairn.stock import changelog, services
from housecairn.stock.models import Article, Movement, Supplier
from housecairn.sync import retention
from housecairn.sync.models import Change, ErasedHome, RowClocks
from housecairn.tasks.models import Occurrence
from tests.rooms_support import image_bytes, upload
from tests.sync_scenarios_stock_reports import (
    ARTICLE,
    ITEMS,
    NOT_APPLICABLE,
    OWNER_ONLY,
    ROOMS,
    SCENARIOS,
    article_in,
    report_in,
)
from tests.test_sync_guard import KINDS, guard_problems

pytest_plugins = ("tests.rooms_fixtures", "tests.stock_fixtures", "tests.tasks_fixtures")
pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 11, 15, 3, 30, tzinfo=datetime.UTC)
LONG_AGO = NOW - datetime.timedelta(days=401)
HLC = re.compile(r"[0-9a-f]{16}")


def test_every_write_path_of_the_stock_and_the_reports_records_its_change(
    alice_home: Home,
) -> None:
    models_of_the_lot = _our_models()
    problems = guard_problems(
        alice_home, OWNER_ONLY, SCENARIOS, NOT_APPLICABLE, only=models_of_the_lot
    )
    assert problems == [], "\n".join(problems)
    assert {m._meta.label for m in models_of_the_lot} == {
        ARTICLE,
        "stock.Supplier",
        "stock.Movement",
        ROOMS,
        ITEMS,
        "reports.Report",
    }


def _our_models() -> list[type[models.Model]]:
    from tests.test_sync_guard import home_models

    return [m for m in home_models() if m._meta.app_label in OWNER_ONLY]


def test_every_kind_of_every_model_is_covered_or_says_why_not() -> None:
    for model in _our_models():
        for kind in changelog_kinds(model):
            assert (model._meta.label, kind) in SCENARIOS or (
                model._meta.label,
                kind,
            ) in NOT_APPLICABLE, f"{model._meta.label} {kind}"


def changelog_kinds(model: type[models.Model]) -> tuple[str, ...]:
    return ("save", "hard_delete") if model._meta.auto_created else KINDS


@pytest.mark.parametrize("name", ["record_save", "soft_delete", "soft_delete_all", "update_rows"])
def test_the_guard_fails_when_a_write_path_stops_recording(
    alice_home: Home, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Mutant: a recording function that records nothing makes the guard report the paths."""
    real = getattr(changelog, name)
    if name == "record_save":
        monkeypatch.setattr(changelog, name, lambda *args, **kwargs: None)
    elif name == "soft_delete":
        monkeypatch.setattr(changelog, name, lambda instance: instance.soft_delete())
    elif name == "soft_delete_all":
        monkeypatch.setattr(changelog, name, lambda qs: qs.soft_delete())
    else:
        monkeypatch.setattr(changelog, name, lambda qs, **values: qs.update(**values))
    del real
    problems = guard_problems(alice_home, OWNER_ONLY, SCENARIOS, NOT_APPLICABLE, only=_our_models())
    assert any("records no change" in p for p in problems)


def test_the_guard_fails_when_the_links_stop_recording(
    alice_home: Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    def plain(owner: Any, relation: str, targets: Any) -> None:
        field: Any = type(owner)._meta.get_field(relation)
        through: Any = field.remote_field.through
        owner_column = f"{field.m2m_field_name()}_id"
        other_column = f"{field.m2m_reverse_field_name()}_id"
        through._default_manager.filter(**{owner_column: owner.pk}).delete()
        through._default_manager.bulk_create(
            [through(**{owner_column: owner.pk, other_column: t.pk}) for t in targets]
        )

    monkeypatch.setattr(changelog, "set_links", plain)
    problems = guard_problems(alice_home, OWNER_ONLY, SCENARIOS, NOT_APPLICABLE, only=_our_models())
    assert f"{ROOMS} save: the write records no change" in problems
    assert f"{ROOMS} hard_delete: the write records no change" in problems
    assert f"{ITEMS} save: the write records no change" in problems
    assert f"{ITEMS} hard_delete: the write records no change" in problems


# ---------- what is recorded ----------


def changes_of(home: Home, entity: str | None = None) -> list[Change]:
    found = Change.objects.filter(home=home).order_by("pos")
    return list(found.filter(entity=entity) if entity else found)


def last_change(home: Home, entity: str) -> Change:
    return changes_of(home, entity)[-1]


class TestArticles:
    def test_a_new_article_is_a_put_of_every_field(self, alice: User, alice_home: Home) -> None:
        article = services.create_article(
            alice,
            alice_home,
            name="Boiler filter",
            unit="piece",
            threshold=Decimal("2"),
            unit_price=Decimal("12.5"),
            notes="Spare",
        )
        (change,) = changes_of(alice_home, ARTICLE.lower())
        assert (change.kind, change.row_id) == ("put", article.pk)
        assert change.fields["name"] == "Boiler filter"
        assert change.fields["threshold"] == "2.00"
        assert change.fields["unit_price"] == "12.50"
        assert change.fields["notes"] == "Spare"
        assert change.fields["label"] is None
        assert HLC.fullmatch(change.fields["created_at"])
        assert not {"id", "home_id", "updated_at", "deleted_at", "label_content_type"} & set(
            change.fields
        )

    def test_an_edit_records_the_fields_that_changed_and_no_other(
        self, alice: User, article: Article
    ) -> None:
        services.update_article(alice, article, name="Better filter", threshold=Decimal("2.00"))
        change = last_change(article.home, "stock.article")
        assert change.fields == {"name": "Better filter"}

    def test_an_edit_that_changes_nothing_records_nothing(
        self, alice: User, article: Article
    ) -> None:
        before = Change.objects.count()
        services.update_article(alice, article, name=article.name, threshold=Decimal("2"))
        assert Change.objects.count() == before

    def test_a_form_that_sends_every_field_still_records_only_the_changed_ones(
        self, alice: User, article: Article
    ) -> None:
        services.update_article(
            alice,
            article,
            name=article.name,
            manufacturer_reference=article.manufacturer_reference,
            dimensions="10 x 20",
            storage_location=article.storage_location,
            unit=article.unit,
            unit_other="",
            threshold=article.threshold,
            unit_price=None,
            notes=article.notes,
        )
        assert last_change(article.home, "stock.article").fields == {"dimensions": "10 x 20"}

    def test_the_label_is_a_field_of_its_own(self, alice: User, article: Article) -> None:
        services.update_article(alice, article, label=upload(image_bytes()))
        article.refresh_from_db()
        assert last_change(article.home, "stock.article").fields == {"label": article.label.name}
        services.update_article(alice, article, clear_label=True)
        assert last_change(article.home, "stock.article").fields == {"label": None}

    def test_a_deletion_is_a_tombstone_for_the_article_and_every_child(
        self, alice: User, article: Article
    ) -> None:
        supplier = services.create_supplier(alice, article, name="Shop")
        other = services.create_supplier(alice, article, name="Web shop")
        movement = services.record_movement(alice, article, kind="entry", quantity=Decimal("3"))
        services.delete_article(alice, article)
        for entity, row in (
            ("stock.article", article),
            ("stock.supplier", supplier),
            ("stock.supplier", other),
            ("stock.movement", movement),
        ):
            deleted = Change.objects.get(
                home=article.home, entity=entity, row_id=row.pk, kind="delete"
            )
            assert deleted.fields == {}
            assert (
                RowClocks.objects.get(home=article.home, entity=entity, row_id=row.pk).tombstoned_at
                is not None
            )

    def test_the_rooms_of_an_article_are_link_rows_with_deterministic_ids(
        self, alice: User, article: Article, owner_room: Room
    ) -> None:
        services.update_article(alice, article, rooms=[owner_room])
        row = changelog.link_id(article.home_id, "stock.article_rooms", article.pk, owner_room.pk)
        assert row == uuid.uuid5(
            article.home_id, f"stock.article_rooms:{article.pk}:{owner_room.pk}"
        )
        change = last_change(article.home, "stock.article_rooms")
        assert (change.kind, change.row_id) == ("put", row)
        assert change.fields == {"article_id": str(article.pk), "room_id": str(owner_room.pk)}

    def test_removing_and_adding_a_link_again_is_a_delete_then_a_restore(
        self, alice: User, article: Article, owner_room: Room
    ) -> None:
        services.update_article(alice, article, rooms=[owner_room])
        services.update_article(alice, article, rooms=[])
        services.update_article(alice, article, rooms=[owner_room])
        kinds = [c.kind for c in changes_of(article.home, "stock.article_rooms")]
        assert kinds == ["put", "delete", "restore"]
        assert len({c.row_id for c in changes_of(article.home, "stock.article_rooms")}) == 1

    def test_a_link_that_stays_is_not_written_again(
        self, alice: User, article: Article, owner_room: Room
    ) -> None:
        services.update_article(alice, article, rooms=[owner_room])
        before = Change.objects.count()
        services.update_article(alice, article, rooms=[owner_room])
        assert Change.objects.count() == before

    def test_the_compatible_items_are_link_rows_too(
        self, alice: User, alice_home: Home, owner_item: Item
    ) -> None:
        article = services.create_article(alice, alice_home, name="Filter")
        services.update_article(alice, article, compatible_items=[owner_item])
        change = last_change(alice_home, "stock.article_compatible_items")
        assert change.fields == {"article_id": str(article.pk), "item_id": str(owner_item.pk)}
        services.update_article(alice, article, compatible_items=[])
        assert last_change(alice_home, "stock.article_compatible_items").kind == "delete"

    def test_a_new_article_with_links_records_the_article_before_its_links(
        self, alice: User, alice_home: Home, owner_room: Room, owner_item: Item
    ) -> None:
        services.create_article(
            alice, alice_home, name="Filter", rooms=[owner_room], compatible_items=[owner_item]
        )
        entities = [c.entity for c in changes_of(alice_home) if c.entity.startswith("stock.")]
        assert entities[:1] == ["stock.article"]
        assert set(entities[1:]) == {"stock.article_rooms", "stock.article_compatible_items"}

    def test_a_refused_write_records_nothing(self, alice: User, alice_home: Home) -> None:
        before = Change.objects.count()
        with pytest.raises(ValidationError):
            services.create_article(alice, alice_home, name="x", unit="other")
        assert Change.objects.count() == before


class TestSuppliers:
    def test_a_supplier_is_a_put_and_its_price_fills_the_unit_price_of_the_article(
        self, alice: User, article: Article
    ) -> None:
        supplier = services.create_supplier(alice, article, name="Shop", last_price=Decimal("9.9"))
        put = last_change(article.home, "stock.supplier")
        assert put.row_id == supplier.pk
        assert put.fields["article_id"] == str(article.pk)
        assert put.fields["last_price"] == "9.90"
        assert last_change(article.home, "stock.article").fields == {"unit_price": "9.90"}

    def test_an_edit_records_the_changed_field_only(self, alice: User, article: Article) -> None:
        supplier = services.create_supplier(alice, article, name="Shop", reference="A1")
        services.update_supplier(alice, supplier, name="Shop", reference="A2")
        assert last_change(article.home, "stock.supplier").fields == {"reference": "A2"}

    def test_a_price_that_prefills_the_article_is_a_change_of_the_article(
        self, alice: User, article: Article
    ) -> None:
        supplier = services.create_supplier(alice, article, name="Shop")
        services.update_supplier(alice, supplier, last_price=Decimal("4"))
        assert last_change(article.home, "stock.supplier").fields == {"last_price": "4.00"}
        assert last_change(article.home, "stock.article").fields == {"unit_price": "4.00"}

    def test_a_deletion_is_a_tombstone(self, alice: User, article: Article) -> None:
        supplier = services.create_supplier(alice, article, name="Shop")
        services.delete_supplier(alice, supplier)
        assert last_change(article.home, "stock.supplier").kind == "delete"


class TestMovements:
    def test_a_movement_is_a_put_without_its_effect(self, alice: User, article: Article) -> None:
        movement = services.record_movement(
            alice, article, kind="entry", quantity=Decimal("3"), note="Delivery"
        )
        change = last_change(article.home, "stock.movement")
        assert change.row_id == movement.pk
        assert change.fields["kind"] == "entry"
        assert change.fields["quantity"] == "3.00"
        assert change.fields["occurred_on"] == movement.occurred_on.isoformat()
        assert change.fields["occurrence_id"] is None
        assert "delta" not in change.fields

    def test_an_inventory_stores_the_count(self, alice: User, article: Article) -> None:
        services.record_movement(alice, article, kind="entry", quantity=Decimal("5"))
        services.record_movement(alice, article, kind="inventory", quantity=Decimal("2"))
        change = last_change(article.home, "stock.movement")
        assert (change.fields["kind"], change.fields["quantity"]) == ("inventory", "2.00")

    def test_the_stock_that_follows_a_task_is_recorded_with_the_occurrence(
        self, alice: User, article: Article, owner_occurrence: Occurrence
    ) -> None:
        services.record_movement(alice, article, kind="entry", quantity=Decimal("5"))
        services.draw_for_task(alice, article, Decimal("2"), occurrence=owner_occurrence)
        drawn = last_change(article.home, "stock.movement")
        assert drawn.fields["kind"] == "exit"
        assert drawn.fields["occurrence_id"] == str(owner_occurrence.pk)
        services.give_back_for_task(alice, article, Decimal("1"), occurrence=owner_occurrence)
        back = last_change(article.home, "stock.movement")
        assert (back.fields["kind"], back.fields["quantity"]) == ("entry", "1.00")

    def test_nothing_taken_records_nothing(
        self, alice: User, article: Article, owner_occurrence: Occurrence
    ) -> None:
        before = Change.objects.count()
        assert (
            services.draw_for_task(alice, article, Decimal("2"), occurrence=owner_occurrence) == 0
        )
        assert Change.objects.count() == before

    def test_a_refused_exit_records_nothing(self, alice: User, article: Article) -> None:
        before = Change.objects.count()
        with pytest.raises(ValidationError):
            services.record_movement(alice, article, kind="exit", quantity=Decimal("1"))
        assert Change.objects.count() == before


class TestReports:
    def test_a_report_is_a_put_of_every_field_even_from_a_reader(
        self, alice_home: Home, reader: User, owner_room: Room
    ) -> None:
        report = report_services.create_report(
            reader, alice_home, title="The tap drips", room=owner_room
        )
        (change,) = changes_of(alice_home, "reports.report")
        assert change.row_id == report.pk
        assert change.fields["title"] == "The tap drips"
        assert change.fields["status"] == "open"
        assert change.fields["reporter_id"] == str(reader.pk)
        assert change.fields["room_id"] == str(owner_room.pk)
        assert change.fields["place"] == "Living room"
        assert change.fields["closed_at"] is None
        assert not {"thumbnail", "photo_content_type", "photo_size_bytes"} & set(change.fields)

    def test_the_photo_is_a_field_of_the_report(self, alice_home: Home, reader: User) -> None:
        report = report_services.create_report(
            reader, alice_home, title="Crack", photo=upload(image_bytes())
        )
        (change,) = changes_of(alice_home, "reports.report")
        assert change.fields["photo"] == report.photo.name != ""

    def test_making_a_task_records_the_status_and_the_task(
        self, alice: User, alice_home: Home, reader: User
    ) -> None:
        report = report_services.create_report(reader, alice_home, title="The tap drips")
        task = report_services.make_task(alice, report)
        assert last_change(alice_home, "reports.report").fields == {
            "status": "task",
            "task_id": str(task.pk),
        }

    def test_closing_and_reopening_record_the_fields_they_change(
        self, alice: User, alice_home: Home, reader: User
    ) -> None:
        report = report_services.create_report(reader, alice_home, title="The tap drips")
        report_services.close_report(alice, report, resolution="resolved")
        closed = last_change(alice_home, "reports.report").fields
        assert set(closed) == {"status", "closed_at", "closed_by_id"}
        assert closed["status"] == "resolved"
        assert closed["closed_by_id"] == str(alice.pk)
        assert HLC.fullmatch(closed["closed_at"])
        report_services.reopen_report(alice, report)
        assert last_change(alice_home, "reports.report").fields == {
            "status": "open",
            "closed_at": None,
            "closed_by_id": None,
        }

    def test_an_update_that_matches_no_row_records_nothing(
        self, alice: User, alice_home: Home, reader: User
    ) -> None:
        report = report_services.create_report(reader, alice_home, title="The tap drips")
        report_services.close_report(alice, report, resolution="resolved")
        before = Change.objects.count()
        with pytest.raises(ValidationError):
            report_services.close_report(alice, report, resolution="no_action")
        assert Change.objects.count() == before


class TestQuerysets:
    def test_a_queryset_update_writes_one_change_per_row_for_the_fields_that_differ(
        self, alice_home: Home
    ) -> None:
        one, two = (report_in(alice_home) for _ in range(2))
        Report.objects.filter(pk=two.pk).update(status="resolved")
        found = changelog.update_rows(
            Report.objects.filter(pk__in=[one.pk, two.pk]), status="resolved"
        )
        assert found == 2
        changed = {c.row_id: c.fields for c in changes_of(alice_home, "reports.report")}
        assert changed == {one.pk: {"status": "resolved"}}

    def test_a_queryset_soft_deletion_writes_a_tombstone_per_row(self, alice_home: Home) -> None:
        article = article_in(alice_home)
        rows = [
            Movement.objects.create(
                home=alice_home,
                article=article,
                kind="entry",
                quantity=Decimal("1"),
                delta=Decimal("1"),
            )
            for _ in range(3)
        ]
        assert changelog.soft_delete_all(Movement.objects.filter(article=article)) == 3
        deleted = {c.row_id for c in changes_of(alice_home, "stock.movement") if c.kind == "delete"}
        assert deleted == {row.pk for row in rows}
        assert not Movement.objects.filter(article=article).exists()


# ---------- what a hard deletion leaves ----------


def clocks(home: Home, *entities: str) -> int:
    return RowClocks.objects.filter(home=home, entity__in=entities).count()


ALL_ENTITIES = (
    "stock.article",
    "stock.supplier",
    "stock.movement",
    "stock.article_rooms",
    "stock.article_compatible_items",
    "reports.report",
)


class TestRetention:
    def test_the_purge_leaves_no_clock_and_no_file_behind_the_children_of_an_article(
        self,
        alice: User,
        alice_home: Home,
        owner_room: Room,
        owner_item: Item,
        media_root: Path,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        article = services.create_article(
            alice,
            alice_home,
            name="Filter",
            rooms=[owner_room],
            compatible_items=[owner_item],
            label=upload(image_bytes()),
        )
        services.create_supplier(alice, article, name="Shop")
        services.record_movement(alice, article, kind="entry", quantity=Decimal("2"))
        stored = media_root / "stock" / article.label.name
        assert stored.exists()
        assert clocks(alice_home, *ALL_ENTITIES) == 5
        services.delete_article(alice, article)
        RowClocks.objects.filter(home=alice_home, tombstoned_at__isnull=False).update(
            tombstoned_at=LONG_AGO
        )
        with django_capture_on_commit_callbacks(execute=True):
            done = retention.purge(NOW)
        # The article, its supplier and its movement, and its two link rows (the cascade).
        assert done.rows == 5
        assert not Article.all_objects.filter(pk=article.pk).exists()
        assert not Supplier.all_objects.exists()
        assert not Movement.all_objects.exists()
        assert clocks(alice_home, *ALL_ENTITIES) == 0
        assert not stored.exists()

    def test_an_article_deleted_for_good_takes_the_clocks_of_its_live_children_with_it(
        self, alice: User, article: Article
    ) -> None:
        services.create_supplier(alice, article, name="Shop")
        services.record_movement(alice, article, kind="entry", quantity=Decimal("2"))
        assert clocks(article.home, "stock.supplier", "stock.movement") == 2
        Article.all_objects.filter(pk=article.pk).delete()
        assert clocks(article.home, *ALL_ENTITIES) == 0

    def test_the_receivers_are_held_by_a_strong_reference(self) -> None:
        """Signals keep weak references by default: a receiver built in a closure would vanish."""
        import weakref

        from django.db.models.signals import pre_delete

        ours = [
            (key[0], receiver)
            for key, receiver, *_rest in pre_delete.receivers
            if isinstance(key[0], str) and key[0].startswith(("stock.", "reports."))
        ]
        assert {uid for uid, _receiver in ours} >= {
            "stock.article_is_going",
            "stock.room_is_going",
            "stock.item_is_going",
            "stock.occurrence_is_going",
            "reports.report_is_going",
            "reports.lost.rooms.Room",
            "reports.lost.rooms.Item",
            "reports.lost.stock.Article",
            "reports.lost.tasks.Task",
        }
        assert [uid for uid, receiver in ours if isinstance(receiver, weakref.ReferenceType)] == []

    def test_the_purge_forgets_the_links_of_a_room_that_is_purged(
        self, alice: User, alice_home: Home, owner_room: Room, owner_item: Item
    ) -> None:
        article = services.create_article(
            alice, alice_home, name="Filter", rooms=[owner_room], compatible_items=[owner_item]
        )
        assert clocks(alice_home, "stock.article_rooms", "stock.article_compatible_items") == 2
        Room.all_objects.filter(pk=owner_room.pk).delete()
        assert clocks(alice_home, "stock.article_rooms") == 0
        Item.all_objects.filter(pk=owner_item.pk).delete()
        assert clocks(alice_home, "stock.article_compatible_items") == 0
        assert Article.objects.filter(pk=article.pk).exists()

    def test_a_report_that_loses_what_it_pointed_at_records_it(
        self, alice: User, alice_home: Home, reader: User, owner_room: Room, article: Article
    ) -> None:
        in_room = report_services.create_report(reader, alice_home, title="a", room=owner_room)
        in_stock = report_services.create_report(reader, alice_home, title="b", article=article)
        Article.all_objects.filter(pk=article.pk).delete()
        assert last_change(alice_home, "reports.report").fields == {"article_id": None}
        assert last_change(alice_home, "reports.report").row_id == in_stock.pk
        Room.all_objects.filter(pk=owner_room.pk).delete()
        assert last_change(alice_home, "reports.report").row_id == in_room.pk
        assert last_change(alice_home, "reports.report").fields == {"room_id": None}
        in_room.refresh_from_db()
        assert in_room.room_id is None

    def test_a_report_made_into_a_task_loses_the_task_that_is_purged(
        self, alice: User, alice_home: Home, reader: User
    ) -> None:
        report = report_services.create_report(reader, alice_home, title="The tap drips")
        task = report_services.make_task(alice, report)
        task.__class__.all_objects.filter(pk=task.pk).delete()
        assert last_change(alice_home, "reports.report").fields == {"task_id": None}

    def test_a_movement_loses_the_occurrence_that_is_purged(
        self, alice: User, article: Article, owner_occurrence: Occurrence
    ) -> None:
        services.record_movement(alice, article, kind="entry", quantity=Decimal("5"))
        taken = services.draw_for_task(alice, article, Decimal("2"), occurrence=owner_occurrence)
        assert taken == 2
        Occurrence.all_objects.filter(pk=owner_occurrence.pk).delete()
        assert last_change(article.home, "stock.movement").fields == {"occurrence_id": None}

    def test_the_account_of_a_reporter_that_is_deleted_is_recorded(
        self, alice_home: Home, reader: User
    ) -> None:
        report = report_services.create_report(reader, alice_home, title="The tap drips")
        reader.delete()
        change = last_change(alice_home, "reports.report")
        assert (change.row_id, change.fields) == (report.pk, {"reporter_id": None})

    def test_a_report_deleted_for_good_leaves_no_clock_and_no_photo(
        self,
        alice_home: Home,
        reader: User,
        media_root: Path,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        report = report_services.create_report(
            reader, alice_home, title="Crack", photo=upload(image_bytes())
        )
        photo, thumbnail = (
            media_root / "reports" / n for n in (report.photo.name, report.thumbnail.name)
        )
        assert photo.exists()
        assert thumbnail.exists()
        with django_capture_on_commit_callbacks(execute=True):
            Report.all_objects.filter(pk=report.pk).delete()
        assert clocks(alice_home, "reports.report") == 0
        assert not photo.exists()
        assert not thumbnail.exists()

    def test_erasing_a_home_with_stock_and_reports_works_and_leaves_nothing(
        self, alice: User, alice_home: Home, reader: User, owner_room: Room, article: Article
    ) -> None:
        services.create_supplier(alice, article, name="Shop")
        services.update_article(alice, article, rooms=[owner_room])
        report_services.create_report(reader, alice_home, title="x", article=article)
        home_id = alice_home.pk
        home_services.erase_home(alice_home)
        assert ErasedHome.objects.filter(pk=home_id).exists()
        assert not Change.objects.filter(home_id=home_id).exists()
        assert not RowClocks.objects.filter(home_id=home_id).exists()
        assert not Article.all_objects.filter(home_id=home_id).exists()


class TestOtherWritePaths:
    def test_the_csv_import_writes_through_the_service_and_records(
        self, alice: User, alice_home: Home
    ) -> None:
        from housecairn.homes import csv_import

        texts = {"stock": "name,unit\nTape,piece\nGlue,\n"}
        before = len(changes_of(alice_home))
        preview = csv_import.run(alice, alice_home, texts, confirm=False)
        assert not preview.written
        assert len(changes_of(alice_home)) == before  # the preview is rolled back with its changes
        report = csv_import.run(alice, alice_home, texts, confirm=True)
        assert report.written
        names = [c.fields["name"] for c in changes_of(alice_home, "stock.article")]
        assert sorted(names) == ["Glue", "Tape"]
