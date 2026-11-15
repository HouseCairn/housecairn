"""Record the writes of the rows of a model in the change log (docs/adr/0077, 0079).

The services of an app call these helpers right after they write, in the same transaction:
`changes.record_change` then gets the entity, the row and only the fields whose value changed,
as the protocol writes them (`room_id`, decimal strings, `YYYY-MM-DD`, HLC strings).

- `snapshot` before an edit, `record_edit` after it: the fields that differ are recorded.
- `record_created` after an insert: every field of the row.
- `delete_row` and `restore_row` instead of `row.soft_delete()` and `row.restore()`.
- `soft_delete` and `update` replace `queryset.soft_delete()` and `queryset.update()`: they
  write the rows and record one change per row whose value changed.
"""

import datetime
import decimal
import uuid
from typing import Any

from django.db import models
from django.db.models.fields.files import FieldFile
from django.utils import timezone

from housecairn.core.models import BaseModel
from housecairn.sync import changes, clock

# Written by the base model or derived: never a field of an operation.
NOT_FIELDS = frozenset({"id", "home", "created_at", "updated_at", "deleted_at"})

Snapshot = dict[str, Any]


def entity_of(model: type[models.Model] | models.Model) -> str:
    """The entity type of the protocol: `<app>.<model>` in lower case."""
    return model._meta.label_lower


def synced_fields(model: type[models.Model] | models.Model) -> list[models.Field[Any, Any]]:
    """The fields of a row an operation carries, foreign keys included (by their column).

    A model lists its derived fields in `SYNC_DERIVED` (`visible_from`): they are recomputed
    on every side, never synced (mobile ADR 0003, "Derived").
    """
    derived = set(getattr(model, "SYNC_DERIVED", ()))
    return [
        field
        for field in model._meta.concrete_fields
        if field.name not in NOT_FIELDS and field.name not in derived
    ]


def wire_value(field: models.Field[Any, Any], value: Any) -> Any:
    """A value of a field as an operation carries it (mobile protocol, section 2.2)."""
    if value is None:
        return None
    if isinstance(value, FieldFile):
        return value.name or None
    if isinstance(value, decimal.Decimal):
        places = getattr(field, "decimal_places", None)
        if places is not None:
            value = value.quantize(decimal.Decimal(1).scaleb(-places))
        return format(value, "f")
    if isinstance(value, datetime.datetime):
        return clock.format_clock(clock.clock_of_instant(value))
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def snapshot(row: models.Model) -> Snapshot:
    """The values of the synced fields of a row as they are now, and its `updated_at`."""
    values: Snapshot = {
        field.attname: wire_value(field, getattr(row, field.attname))
        for field in synced_fields(row)
    }
    values["updated_at"] = getattr(row, "updated_at", None)
    return values


def stored(row: models.Model) -> Snapshot:
    """The snapshot of a row as the database holds it, whatever its object was given since.

    A form may have set its fields on the object already: only the database says what the
    values were before the write.
    """
    return snapshot(type(row)._base_manager.get(pk=row.pk))


def _home_of(row: models.Model) -> uuid.UUID:
    home_id: uuid.UUID = row.pk if row._meta.label == "homes.Home" else row.home_id  # type: ignore[attr-defined]
    return home_id


def record_created(row: models.Model) -> changes.Recorded:
    """A row just inserted: every field is written."""
    values = snapshot(row)
    values.pop("updated_at")
    return changes.record_change(_home_of(row), entity_of(row), row.pk, changes.PUT, values)


def record_edit(row: models.Model, before: Snapshot) -> changes.Recorded | None:
    """A row just saved: the fields whose value differs from `before`, None when none does."""
    after = snapshot(row)
    after.pop("updated_at")
    written = {name: value for name, value in after.items() if before.get(name) != value}
    if not written:
        return None
    return changes.record_change(
        _home_of(row),
        entity_of(row),
        row.pk,
        changes.PUT,
        written,
        updated_at=before.get("updated_at"),
    )


def record_deleted(row: models.Model, *, updated_at: datetime.datetime | None = None) -> None:
    """A row just soft-deleted: its tombstone. `updated_at`: the row's, before the deletion."""
    changes.record_change(
        _home_of(row), entity_of(row), row.pk, changes.DELETE, updated_at=updated_at
    )


def delete_row(row: BaseModel) -> None:
    """`row.soft_delete()`, with its tombstone."""
    before = row.updated_at
    row.soft_delete()
    record_deleted(row, updated_at=before)


def restore_row(row: BaseModel) -> None:
    """`row.restore()`, lifting its tombstone."""
    before = row.updated_at
    row.restore()
    changes.record_change(_home_of(row), entity_of(row), row.pk, changes.RESTORE, updated_at=before)


def soft_delete(rows: models.QuerySet[Any]) -> int:
    """Soft-delete the live rows of a queryset, with a tombstone for each."""
    doomed = list(rows.filter(deleted_at__isnull=True).select_for_update())
    if not doomed:
        return 0
    now = timezone.now()
    rows.model._base_manager.filter(pk__in=[row.pk for row in doomed]).update(
        deleted_at=now, updated_at=now
    )
    for row in doomed:
        record_deleted(row, updated_at=row.updated_at)
    return len(doomed)


def update(rows: models.QuerySet[Any], **values: Any) -> int:
    """`queryset.update(**values)`, recording the fields that change on each row.

    `values` name the fields as the model does (`contractor=None`); `updated_at` follows.
    """
    model = rows.model
    fields = {name: model._meta.get_field(name) for name in values}
    targets = list(rows.select_for_update())
    if not targets:
        return 0
    befores = {row.pk: snapshot(row) for row in targets}
    now = timezone.now()
    extra = {"updated_at": now} if issubclass(model, BaseModel) else {}
    model._base_manager.filter(pk__in=list(befores)).update(**values, **extra)
    wanted = {
        field.attname: wire_value(field, _attvalue(field, values[name]))
        for name, field in fields.items()
    }
    for row in targets:
        written = {k: v for k, v in wanted.items() if befores[row.pk].get(k) != v}
        if written:
            changes.record_change(
                _home_of(row),
                entity_of(row),
                row.pk,
                changes.PUT,
                written,
                updated_at=befores[row.pk]["updated_at"],
            )
    return len(targets)


def _attvalue(field: Any, value: Any) -> Any:
    """The value of the column of a field: the key of a row given to a foreign key."""
    if isinstance(value, models.Model):
        return value.pk
    return value
