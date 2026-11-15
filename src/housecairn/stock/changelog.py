"""What the stock and the reports write goes to the change log (docs/adr/0077, 0079, lot M3d).

Every write path of a row of `stock` and `reports` calls one of the functions below, in the
transaction that writes the row; they all end in `housecairn.sync.changes.record_change`.
Only the fields whose value changed are given to it: a form that sends every field must not
give a clock to a field nobody touched.

- `read` takes the state of a row before a write, `record_save` compares it with the state
  after and records what differs (the whole row for a new one, a deletion or a restoration
  when `deleted_at` moved);
- `soft_delete_all` and `update_rows` are the queryset versions, row by row;
- `set_links` records the elements added to and removed from a set of links (the rooms and the
  compatible items of an article), each a row of a link table with a deterministic id;
- `clear_reference` records a reference that a hard deletion elsewhere sets to NULL.

Values are those of the protocol (mobile protocol, section 2.2): fields named as the model's
columns (`article_id`), decimals as strings, dates `YYYY-MM-DD`, instants as clock strings. A
file field holds the name of the stored file until the lot of the files (M6) maps it to the
reference of ADR 0077; the columns derived from a file (its type, its size, its thumbnail) and
the effect of a movement (`delta`, derived on read) are not fields of the protocol.
"""

import dataclasses
import datetime
import uuid
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

from django.db import models, transaction

from housecairn.core.models import BaseModel
from housecairn.sync import changes, clock
from housecairn.sync.models import ErasedHome, RowClocks

CHUNK = 500

# Columns that are never fields of a change: the identity and the home of the row, the
# instant of the last write (the clock says it) and the deletion (a change of its own kind).
_NEVER = frozenset({"id", "home", "updated_at", "deleted_at"})
# Columns of a model that are derived, so that the protocol does not carry them.
_DERIVED: dict[str, frozenset[str]] = {
    "stock.article": frozenset({"label_content_type"}),
    "stock.movement": frozenset({"delta"}),
    "reports.report": frozenset({"thumbnail", "photo_content_type", "photo_size_bytes"}),
}


def entity_of(model: type[models.Model]) -> str:
    """The entity type of the protocol: `<app>.<model>` in lower case."""
    return model._meta.label_lower


def synced_fields(model: type[models.Model]) -> list[models.Field[Any, Any]]:
    skipped = _NEVER | _DERIVED.get(entity_of(model), frozenset())
    return [f for f in model._meta.concrete_fields if f.name not in skipped]


def _encode(field: models.Field[Any, Any], value: Any) -> Any:
    if value is None:
        return None
    if isinstance(field, models.FileField):
        return str(value) or None
    if isinstance(field, models.DecimalField):
        places = field.decimal_places or 0
        return format(Decimal(value).quantize(Decimal(1).scaleb(-places)), "f")
    if isinstance(field, models.DateTimeField):  # before DateField, which it extends
        return clock.format_clock(clock.clock_of_instant(value))
    if isinstance(field, models.DateField):
        return value.isoformat()
    if isinstance(field, models.UUIDField | models.ForeignKey):
        return str(value)
    return value


@dataclasses.dataclass(frozen=True)
class State:
    """A row as the protocol sees it, at one instant."""

    home_id: uuid.UUID
    updated_at: datetime.datetime
    deleted: bool
    fields: dict[str, Any]


def read(model: type[BaseModel], pk: uuid.UUID) -> State | None:
    """The state of a row now, deleted ones included; None when there is no such row."""
    fields = synced_fields(model)
    row = (
        model.all_objects.filter(pk=pk)
        .values("home_id", "updated_at", "deleted_at", *[f.attname for f in fields])
        .first()
    )
    if row is None:
        return None
    return State(
        home_id=row["home_id"],
        updated_at=row["updated_at"],
        deleted=row["deleted_at"] is not None,
        fields={f.attname: _encode(f, row[f.attname]) for f in fields},
    )


def record_save(model: type[BaseModel], pk: uuid.UUID, before: State | None) -> None:
    """Record what a write changed in a row, given its state before (None: a new row)."""
    after = read(model, pk)
    if after is None:
        return
    entity = entity_of(model)
    if before is None:
        changes.record_change(after.home_id, entity, pk, changes.PUT, after.fields)
        if after.deleted:  # a row made already deleted: only a restore import does
            changes.record_change(after.home_id, entity, pk, changes.DELETE)
        return
    changed = {name: v for name, v in after.fields.items() if before.fields.get(name) != v}
    if changed:
        changes.record_change(
            after.home_id, entity, pk, changes.PUT, changed, updated_at=before.updated_at
        )
    if after.deleted != before.deleted:
        kind = changes.DELETE if after.deleted else changes.RESTORE
        changes.record_change(after.home_id, entity, pk, kind, updated_at=before.updated_at)


def soft_delete(instance: BaseModel) -> None:
    """`instance.soft_delete()`, and the deletion in the change log."""
    before = read(type(instance), instance.pk)
    instance.soft_delete()
    record_save(type(instance), instance.pk, before)


def soft_delete_all(queryset: models.QuerySet[Any]) -> int:
    """Soft-delete every row of the queryset: one deletion each in the change log."""
    model = queryset.model
    entity = entity_of(model)
    with transaction.atomic():
        rows = list(queryset.select_for_update().values_list("pk", "home_id", "updated_at"))
        for start in range(0, len(rows), CHUNK):
            pks = [pk for pk, _home, _at in rows[start : start + CHUNK]]
            model.objects.filter(pk__in=pks).soft_delete()
        for pk, home_id, updated_at in rows:
            changes.record_change(home_id, entity, pk, changes.DELETE, updated_at=updated_at)
    return len(rows)


def update_rows(queryset: models.QuerySet[Any], **values: Any) -> int:
    """`queryset.update(**values)`, and the fields that changed in the change log, row by row."""
    model = queryset.model
    with transaction.atomic():
        pks = list(queryset.select_for_update().values_list("pk", flat=True))
        befores = {pk: read(model, pk) for pk in pks}
        for start in range(0, len(pks), CHUNK):
            model._base_manager.filter(pk__in=pks[start : start + CHUNK]).update(**values)
        for pk in pks:
            record_save(model, pk, befores[pk])
    return len(pks)


# ---------- sets of links ----------


def link_id(home_id: uuid.UUID, entity: str, owner: uuid.UUID, other: uuid.UUID) -> uuid.UUID:
    """The id of a link row (ADR 0077, need 2): named by its entity type, its owner, the other."""
    return uuid.uuid5(home_id, f"{entity}:{owner}:{other}")


def set_links(owner: models.Model, relation: str, targets: Iterable[models.Model]) -> None:
    """Make `targets` the elements of the set of links `owner.<relation>`, and record the change.

    An element added is a `put` of the link row, or a `restore` when the row was removed
    before; an element removed is a `delete`. The elements are read from the table itself,
    before and after.
    """
    field: Any = type(owner)._meta.get_field(relation)
    through: Any = field.remote_field.through
    entity = entity_of(through)
    owner_column = f"{field.m2m_field_name()}_id"
    other_column = f"{field.m2m_reverse_field_name()}_id"
    home_id = owner.home_id  # type: ignore[attr-defined]

    def elements() -> set[uuid.UUID]:
        rows = through._default_manager.filter(**{owner_column: owner.pk})
        return set(rows.values_list(other_column, flat=True))

    # The row of the owner is locked, so that two edits of its links at once do not both add
    # the same element: the table is written with plain inserts, which a conflict would refuse.
    type(owner)._base_manager.select_for_update().get(pk=owner.pk)
    before = elements()
    wanted = {target.pk for target in targets}
    if wanted - before:
        through._default_manager.bulk_create(
            [through(**{owner_column: owner.pk, other_column: o}) for o in wanted - before]
        )
    if before - wanted:
        through._default_manager.filter(
            **{owner_column: owner.pk, f"{other_column}__in": before - wanted}
        ).delete()
    after = elements()
    for other in sorted(after - before, key=str):
        row_id = link_id(home_id, entity, owner.pk, other)
        was_removed = RowClocks.objects.filter(
            home_id=home_id, entity=entity, row_id=row_id, tombstoned_at__isnull=False
        ).exists()
        if was_removed:
            changes.record_change(home_id, entity, row_id, changes.RESTORE)
        else:
            changes.record_change(
                home_id,
                entity,
                row_id,
                changes.PUT,
                {owner_column: str(owner.pk), other_column: str(other)},
            )
    for other in sorted(before - after, key=str):
        changes.record_change(
            home_id, entity, link_id(home_id, entity, owner.pk, other), changes.DELETE
        )


# ---------- what a hard deletion elsewhere does to our rows ----------


def home_is_erased(home_id: uuid.UUID) -> bool:
    """True once `erase_home` has written its row: the home and its change log go together."""
    return ErasedHome.objects.filter(pk=home_id).exists()


def clear_reference(model: type[BaseModel], column: str, target: Any) -> None:
    """Record that rows of `model` lose the reference `column` to a row that is deleted for good.

    Called before the deletion, by the receivers of `pre_delete`: the database sets the
    column to NULL (`on_delete=SET_NULL`) without going through a service.
    """
    entity = entity_of(model)
    rows = list(
        model.all_objects.filter(**{column: target}).values_list("pk", "home_id", "updated_at")
    )
    for pk, home_id, updated_at in rows:
        if home_is_erased(home_id):
            continue
        changes.record_change(
            home_id, entity, pk, changes.PUT, {column: None}, updated_at=updated_at
        )


def forget_clocks(home_id: uuid.UUID, entity: str, row_ids: Sequence[uuid.UUID]) -> None:
    """Remove the clocks of rows that are gone for good, so that none is left without its row."""
    for start in range(0, len(row_ids), CHUNK):
        RowClocks.objects.filter(
            home_id=home_id, entity=entity, row_id__in=row_ids[start : start + CHUNK]
        ).delete()
