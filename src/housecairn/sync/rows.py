"""Write a row of a home and record its change in one step (docs/adr/0077, 0079).

`changes.record_change` takes the fields whose value changed. The services of an application do
not compute them by hand: they write through this module, which reads the row as the database
holds it, writes, and records the difference. It is the one helper of every lot of the change
log (M3b to M3e): a service of a covered model never calls `save()`, `soft_delete()`,
`restore()` or `queryset.update()` on its own, or the guard (`tests/test_sync_guard.py`) fails.

Writing a row:

- `save(instance, **kwargs) -> Recorded | None`: `instance.save(**kwargs)`, then its change. A
  new row records every field, an existing one only those whose value changed; with
  `update_fields`, only those fields are compared and recorded. None when nothing changed.
- `soft_delete(instance) -> Recorded | None`: `instance.soft_delete()` and its tombstone; a row
  already deleted records nothing.
- `soft_delete(queryset) -> int`, the same as `soft_delete_queryset(queryset)`: the live rows
  of a queryset soft-deleted, a tombstone each; the number of rows deleted.
- `restore(instance) -> Recorded | None`: `instance.restore()` and its restoration; a row that
  is not deleted records nothing.
- `update_queryset(queryset, **values) -> int`: `queryset.update(**values)` with plain values
  (a row or None for a foreign key, by the name of the field), then one change per row with the
  fields whose value changed; the number of rows written.

Recording a write made elsewhere:

- `before(instance) -> Before`, read before the write, then `record_saved(instance, was) ->
  Recorded | None` after it: the two halves of `save`, for a write that `save` cannot wrap.
- `record_created(instances: Iterable[Model]) -> None`: rows inserted in bulk (restoring an
  archive), every field of each.

What a model sends is its concrete fields under their column names (`room_id`), except `id`,
`home` (the home is the key of the change), `created_at`, `updated_at` and `deleted_at`, and
except its derived fields: a model lists them in `SYNC_DERIVED` (`tasks.Occurrence.visible_from`);
they are recomputed on every side and never synced (mobile ADR 0003, "Derived"). A model adjusts
the rest with `register(model, Spec(...))` from the `ready()` of its application: fields left
out, fields without a column (a file reference), fields that are an instant made by the write
itself. A file field is never sent as such: a model that syncs one registers a `virtual` field.
Dates are `YYYY-MM-DD`, amounts decimal strings with the places of their field, instants hybrid
clocks (`encode`).
"""

import datetime
import decimal
import uuid
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, overload

from django.core.exceptions import FieldDoesNotExist
from django.db import models, transaction
from django.utils import timezone

from housecairn.homes.models import Home
from housecairn.sync import changes, clock

# Never sent as fields: the key, the home (it keys the change), the bookkeeping columns.
BOOKKEEPING = frozenset({"id", "home", "created_at", "updated_at", "deleted_at"})


@dataclass(frozen=True)
class Spec:
    """How the fields of one model are sent.

    - `skip`: columns that are not synced (a thumbnail the phone makes itself, a setting that
      belongs to the instance).
    - `write_clock`: instants that read as the clock of the write that sets them
      (`homes.home.archived_at`); empty stays empty.
    - `virtual`: fields that have no column, computed from the instance (a file reference).
    """

    skip: frozenset[str] = frozenset()
    write_clock: frozenset[str] = frozenset()
    virtual: Callable[[Any], dict[str, Any]] | None = None


_SPECS: dict[str, Spec] = {}
_DEFAULT = Spec()


def register(model: type[models.Model], spec: Spec) -> None:
    _SPECS[model._meta.label_lower] = spec


def spec_of(model: type[models.Model]) -> Spec:
    return _SPECS.get(model._meta.label_lower, _DEFAULT)


def entity_of(model: type[models.Model]) -> str:
    """The entity type of the protocol: `<app>.<model>` in lower case."""
    return model._meta.label_lower


def home_of(instance: models.Model) -> uuid.UUID:
    """The home a row belongs to: a home belongs to itself."""
    if isinstance(instance, Home):
        return instance.pk
    home_id: uuid.UUID = instance.home_id  # type: ignore[attr-defined]
    return home_id


def derived(model: type[models.Model]) -> frozenset[str]:
    """The derived fields of a model (`SYNC_DERIVED`): recomputed on every side, never synced."""
    return frozenset(getattr(model, "SYNC_DERIVED", ()))


def _fields(model: type[models.Model]) -> list[models.Field[Any, Any]]:
    spec = spec_of(model)
    left_out = spec.skip | derived(model)
    return [
        f
        for f in model._meta.concrete_fields
        if f.name not in BOOKKEEPING
        and f.attname not in left_out
        and f.name not in left_out
        and not isinstance(f, models.FileField)
    ]


def raw_values(instance: models.Model, only: Collection[str] | None = None) -> dict[str, Any]:
    """The values of the synced columns, as Python holds them, under their column names.

    `only`: the fields to read, by name or column, as `save(update_fields=...)` gives them; the
    virtual fields are read when one of the columns they stand for is.
    """
    model = type(instance)
    values = {
        f.attname: getattr(instance, f.attname)
        for f in _fields(model)
        if only is None or f.name in only or f.attname in only
    }
    virtual = spec_of(model).virtual
    if virtual is not None and (only is None or _touches_hidden(model, only)):
        values.update(virtual(instance))
    return values


def _touches_hidden(model: type[models.Model], only: Collection[str]) -> bool:
    """Whether a write of the fields `only` writes a column the model does not send as such.

    Those columns (a file, its size and type) are what the virtual fields stand for.
    """
    sent = set(_fields(model))
    return any(
        f not in sent and (f.name in only or f.attname in only)
        for f in model._meta.concrete_fields
        if f.name not in BOOKKEEPING
    )


def encode(model: type[models.Model], name: str, value: Any) -> Any:
    """A value as the protocol writes it (JSON): text, number, boolean or null."""
    if name in spec_of(model).write_clock:
        return None if value is None else changes.WRITE_CLOCK
    if value is None or isinstance(value, bool | int | float | str | list | dict):
        return _jsonable(value)
    if isinstance(value, datetime.datetime):
        return clock.format_clock(clock.clock_of_instant(value))
    if isinstance(value, datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return _amount(model, name, value)
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"{model._meta.label}.{name}: cannot send a {type(value).__name__}.")


def _amount(model: type[models.Model], name: str, value: decimal.Decimal) -> str:
    """An amount with the places of its field (`12.50`, not `12.5`), never in exponent form."""
    try:
        places = getattr(model._meta.get_field(name), "decimal_places", None)
    except FieldDoesNotExist:
        places = None
    if places is not None:
        value = value.quantize(decimal.Decimal(1).scaleb(-places))
    return format(value, "f")


def _jsonable(value: Any) -> Any:
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


@dataclass(frozen=True)
class Before:
    """What the database held of a row before a write: None values mean the row is new."""

    values: dict[str, Any] | None
    updated_at: datetime.datetime | None


def before(instance: models.Model) -> Before:
    """Read the row from the database (not from the instance, which may be stale)."""
    stored = type(instance)._base_manager.filter(pk=instance.pk).first()
    if stored is None:
        return Before(None, None)
    return Before(raw_values(stored), getattr(stored, "updated_at", None))


def _diff(
    instance: models.Model, was: Before, only: Collection[str] | None = None
) -> dict[str, Any]:
    now = raw_values(instance, only)
    model = type(instance)
    return {
        name: encode(model, name, value)
        for name, value in now.items()
        if was.values is None or was.values.get(name) != value
    }


def record_saved(
    instance: models.Model, was: Before, only: Collection[str] | None = None
) -> changes.Recorded | None:
    """Record what the write of `instance` changed against `was`; None when nothing did.

    `only`: the fields the write wrote (`update_fields`); the others are not compared.
    """
    diff = _diff(instance, was, only)
    if not diff:
        return None
    return changes.record_change(
        home_of(instance),
        entity_of(type(instance)),
        instance.pk,
        changes.PUT,
        diff,
        updated_at=was.updated_at,
    )


def save(instance: Any, **kwargs: Any) -> changes.Recorded | None:
    """`instance.save(**kwargs)`, then its change: the fields whose value changed only.

    With `update_fields`, only those fields are written, so only those are compared: a field
    changed on the instance but not written is not recorded.
    """
    written = kwargs.get("update_fields")
    only = None if written is None else frozenset(written)
    with transaction.atomic():
        was = before(instance)
        instance.save(**kwargs)
        return record_saved(instance, was, only)


@overload
def soft_delete(target: models.QuerySet[Any]) -> int: ...


@overload
def soft_delete(target: models.Model) -> changes.Recorded | None: ...


def soft_delete(target: Any) -> changes.Recorded | int | None:
    """Soft-delete a row, or the live rows of a queryset, with a tombstone each.

    A row: `row.soft_delete()`, then its deletion; None when it was deleted already. A queryset:
    `soft_delete_queryset(queryset)`, the number of rows it deleted.
    """
    if isinstance(target, models.QuerySet):
        return soft_delete_queryset(target)
    return _soft_delete_row(target)


def _soft_delete_row(instance: Any) -> changes.Recorded | None:
    with transaction.atomic():
        stored = type(instance)._base_manager.filter(pk=instance.pk).first()
        if stored is None or stored.deleted_at is not None:
            instance.soft_delete()
            return None
        instance.soft_delete()
        return changes.record_change(
            home_of(instance),
            entity_of(type(instance)),
            instance.pk,
            changes.DELETE,
            updated_at=getattr(stored, "updated_at", None),
        )


def restore(instance: Any) -> changes.Recorded | None:
    """`instance.restore()`, then its restoration; a row that is not deleted records nothing."""
    with transaction.atomic():
        stored = type(instance)._base_manager.filter(pk=instance.pk).first()
        if stored is None or stored.deleted_at is None:
            instance.restore()
            return None
        instance.restore()
        return changes.record_change(
            home_of(instance),
            entity_of(type(instance)),
            instance.pk,
            changes.RESTORE,
            updated_at=getattr(stored, "updated_at", None),
        )


# Identifiers per statement: SQLite refuses more than a few hundred variables in old builds.
CHUNK = 500


def _chunks(items: list[Any]) -> Iterable[list[Any]]:
    for start in range(0, len(items), CHUNK):
        yield items[start : start + CHUNK]


def soft_delete_queryset(rows: models.QuerySet[Any]) -> int:
    """Soft-delete the rows of a queryset, and write a tombstone for each row it deleted."""
    model = rows.model
    with transaction.atomic():
        alive = list(
            model._base_manager.filter(pk__in=rows.values("pk"), deleted_at__isnull=True)
            .select_for_update()
            .values_list("pk", "updated_at", *([] if model is Home else ["home_id"]))
        )
        now = timezone.now()
        count = 0
        for chunk in _chunks(alive):
            count += model._base_manager.filter(
                pk__in=[row[0] for row in chunk], deleted_at__isnull=True
            ).update(deleted_at=now)
        for pk, updated_at, *home in alive:
            changes.record_change(
                pk if model is Home else home[0],
                entity_of(model),
                pk,
                changes.DELETE,
                updated_at=updated_at,
            )
        return count


def update_queryset(rows: models.QuerySet[Any], **values: Any) -> int:
    """`rows.update(**values)` with plain values, then a change per row and field that changed."""
    model = rows.model
    with transaction.atomic():
        targets = list(model._base_manager.filter(pk__in=rows.values("pk")).select_for_update())
        was = {row.pk: before(row) for row in targets}
        count = 0
        for chunk in _chunks([row.pk for row in targets]):
            count += model._base_manager.filter(pk__in=chunk).update(**values)
        for row in targets:
            for name, value in values.items():
                setattr(row, name, value)
            record_saved(row, was[row.pk])
        return count


def record_created(instances: Iterable[models.Model]) -> None:
    """Record the rows just inserted in bulk (restoring an archive): every field of each."""
    for instance in instances:
        record_saved(instance, Before(None, None))


def values_of(instance: models.Model) -> Mapping[str, Any]:
    """The fields a new row would send (tests, documentation)."""
    return {name: encode(type(instance), name, v) for name, v in raw_values(instance).items()}
