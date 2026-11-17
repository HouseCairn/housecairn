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
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, overload

from django.core.exceptions import FieldDoesNotExist
from django.db import models, transaction
from django.utils import timezone

from housecairn.homes.models import Home
from housecairn.sync import changes, clock, ids
from housecairn.sync.models import ErasedHome, RowClocks

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
    # Bookkeeping columns a model sends all the same (`created_at` of the stock and reports).
    send: frozenset[str] = frozenset()
    # File fields sent by the name of the stored file, empty as null (the label of an article,
    # the photo of a report); the reading maps them to a reference (`sync.files`,
    # docs/adr/0085). The photos, attachments and files of a project send theirs as a
    # `virtual` field (`sync.files`, lot M6).
    file_names: frozenset[str] = frozenset()


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
        if (f.name not in BOOKKEEPING or f.name in spec.send)
        and f.attname not in left_out
        and f.name not in left_out
        and (not isinstance(f, models.FileField) or f.name in spec.file_names)
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
    spec = spec_of(model)
    if name in spec.write_clock:
        return None if value is None else changes.WRITE_CLOCK
    if name in spec.file_names:
        return (str(value) or None) if value is not None else None
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
    deleted: bool = False


def read(model: type[models.Model], pk: Any) -> Before:
    """The row `pk` of `model` as the database holds it now, deleted or not (`before`)."""
    stored = model._base_manager.filter(pk=pk).first()
    return Before(None, None) if stored is None else _before_of(stored)


def record_save(model: type[models.Model], pk: Any, was: Before | None) -> None:
    """Record a write of the row `pk` against `was` (None: a new row), read as stored now.

    For a write `save` cannot wrap, whose instance may not hold what the database does: the
    row is read again. A deletion or a restoration that the write made is recorded too.
    """
    stored = model._base_manager.filter(pk=pk).first()
    if stored is None:
        return
    was = was or Before(None, None)
    deleted = getattr(stored, "deleted_at", None) is not None
    record_saved(stored, was)
    if was.values is None:
        if deleted:  # a row made already deleted: only a restore import does
            changes.record_change(home_of(stored), entity_of(model), pk, changes.DELETE)
        return
    if deleted != was.deleted:
        changes.record_change(
            home_of(stored),
            entity_of(model),
            pk,
            changes.DELETE if deleted else changes.RESTORE,
            updated_at=was.updated_at,
        )


def before(instance: models.Model) -> Before:
    """Read the row from the database (not from the instance, which may be stale)."""
    stored = type(instance)._base_manager.filter(pk=instance.pk).first()
    if stored is None:
        return Before(None, None)
    return _before_of(stored)


def _before_of(stored: models.Model) -> Before:
    """A row just read from the database, as `before` gives it."""
    return Before(
        raw_values(stored),
        getattr(stored, "updated_at", None),
        getattr(stored, "deleted_at", None) is not None,
    )


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
        # One read of the rows before the write, locked: it is what each change compares.
        targets = list(model._base_manager.filter(pk__in=rows.values("pk")).select_for_update())
        was = {row.pk: _before_of(row) for row in targets}
        count = 0
        for chunk in _chunks([row.pk for row in targets]):
            count += model._base_manager.filter(pk__in=chunk).update(**values)
        for row in targets:
            for name, value in values.items():
                setattr(row, name, value)
            record_saved(row, was[row.pk])
        return count


# The names the stock and the reports write with since M3d: the same functions.
soft_delete_all = soft_delete_queryset
update_rows = update_queryset
link_id = ids.link


def record_created(instances: Iterable[models.Model]) -> None:
    """Record the rows just inserted in bulk (restoring an archive): every field of each."""
    for instance in instances:
        record_saved(instance, Before(None, None))


def values_of(instance: models.Model) -> Mapping[str, Any]:
    """The fields a new row would send (tests, documentation)."""
    return {name: encode(type(instance), name, v) for name, v in raw_values(instance).items()}


def create_or_revive(instance: Any) -> Any:
    """Write a row whose id is deterministic (`sync.ids`), and record its change.

    The id names the row on every side, so a row of that id may exist already: one that an
    undo or a removal deleted, which comes back (`restore`) with the values of `instance`,
    or a live one, which takes them. Otherwise the row is created. Returns the row written.
    """
    model = type(instance)
    with transaction.atomic():
        stored = model._base_manager.select_for_update().filter(pk=instance.pk).first()
        if stored is None:
            instance.save(force_insert=True)
            record_created([instance])
            return instance
        if stored.deleted_at is not None:
            restore(stored)
        for f in model._meta.concrete_fields:
            if f.name not in BOOKKEEPING:
                setattr(stored, f.attname, getattr(instance, f.attname))
        save(stored)
        return stored


# ---------- a write made on a phone (a push, docs/adr/0077) ----------


def apply_stamped(
    model: type[models.Model],
    home_id: uuid.UUID,
    pk: uuid.UUID,
    kind: str,
    fields: Mapping[str, Any] | None,
    values: Mapping[str, Any],
    *,
    stamp: tuple[str, bytes],
    known: Collection[str],
    settle: Callable[[Any, dict[str, Any], str], dict[str, Any]] | None = None,
) -> changes.Recorded:
    """Write a row as a phone's operation says, field by field, and record its change.

    The other writers of this module write the row, then record what changed with the server's
    clock. A phone's write carries its own clock, which may be older than what the row holds: so
    the change is recorded first, which decides per field which writes win (`record_change`),
    and only the fields that won are written to the row. A field that lost is not written.

    - `fields`: the fields of a `put` as the protocol writes them (they go to the feed), those
      without a column included (`extra`); None for a `delete` or a `restore`.
    - `values`: the same fields that have a column, as Python holds them, by column name.
    - `known`: the columns of the model the phone may write.

    A `put` of a row the table does not hold creates it, with the fields that won; a `delete`
    and a `restore` need the row (the caller checks it).

    `settle(stored, columns, kind)`, when given, is called once the fields that won are known
    and before the row is written, with the row as stored (None: new) and the columns that won.
    It returns columns written with them that no change records: a value the server derives
    (the effect of a stock movement), or a value held for the time of the push, which the
    repair rules settle before the push ends (`sync.repairs`).
    """
    with transaction.atomic():
        stored = model._base_manager.select_for_update().filter(pk=pk).first()
        if kind != changes.PUT and stored is None:
            raise ValueError("A deletion or a restoration needs its row.")
        recorded = changes.record_change(
            home_id,
            entity_of(model),
            pk,
            kind,
            fields if kind == changes.PUT else None,
            stamp=stamp,
            known=known,
            updated_at=getattr(stored, "updated_at", None),
        )
        if recorded.change is None:
            return recorded
        won = recorded.change.fields
        columns = {name: values[name] for name in won if name in values}
        held = {} if settle is None else settle(stored, columns, kind)
        row: Any = stored
        if kind in (changes.DELETE, changes.RESTORE):
            for name, value in held.items():
                setattr(row, name, value)
            if held:
                row.save(update_fields=[*held, "updated_at"])
            if kind == changes.DELETE:
                row.soft_delete()
            else:
                row.restore()
        else:
            columns = {**columns, **held}
            if stored is None:
                _create_stamped(model, home_id, pk, columns, known)
            elif columns:
                for name, value in columns.items():
                    setattr(stored, name, value)
                stored.save(update_fields=[*columns, "updated_at"])
        return recorded


def _create_stamped(
    model: type[models.Model],
    home_id: uuid.UUID,
    pk: uuid.UUID,
    columns: Mapping[str, Any],
    known: Collection[str],
) -> None:
    """Insert the row a phone made, with its id; a row whose deletion came first stays deleted.

    A column the phone did not set holds its default, which nobody wrote: it gets the zero
    clock, so that any later write of it wins. Without it, the field would read as the
    `updated_at` of the insert, the server's time, and a write the phone made before that
    instant (sent in a later push, or by another phone) would lose to nobody.
    """
    instance = model(pk=pk, **({} if model is Home else {"home_id": home_id}))
    for name, value in columns.items():
        setattr(instance, name, value)
    clocks = RowClocks.objects.get(home_id=home_id, entity=entity_of(model), row_id=pk)
    if clocks.tombstoned_at is not None:
        instance.deleted_at = clocks.tombstoned_at  # type: ignore[attr-defined]
    instance.save(force_insert=True)
    unset = [name for name in (*known, changes.DELETED_AT) if name not in clocks.clocks]
    if unset:
        never = [clock.ZERO_CLOCK, clock.device_to_b64(changes.server_device())]
        for name in unset:
            clocks.clocks[name] = never
        clocks.save(update_fields=["clocks"])
    # `auto_now_add` sets the instant of the insert: an instant the phone sent is kept.
    stamped = {
        f.attname: columns[f.attname]
        for f in model._meta.concrete_fields
        if getattr(f, "auto_now_add", False) and f.attname in columns
    }
    if stamped:
        model._base_manager.filter(pk=pk).update(**stamped)


def write_link(through: type[models.Model], columns: Mapping[str, Any]) -> None:
    """Make the link row of `columns` live in its table, after its change was recorded."""
    manager: Any = through._default_manager
    if not manager.filter(**columns).exists():
        manager.create(**columns)


def remove_link(through: type[models.Model], columns: Mapping[str, Any]) -> None:
    """Remove the link row of `columns` from its table, after its deletion was recorded."""
    manager: Any = through._default_manager
    manager.filter(**columns).delete()


# ---------- link tables ----------


def link_row_id(
    home_id: uuid.UUID, through: type[models.Model], owner: Any, other: Any
) -> uuid.UUID:
    """The id of the row of a link table that links `owner` to `other` in a home.

    A link table (`projects.project_rooms`) has no id of its own that a phone could share: its
    row is named by its entity type, the id of the row that holds the link and the id of the
    row it links to (`sync.ids.link`).
    """
    return ids.link(home_id, entity_of(through), owner, other)


def link_fields(relation: models.ManyToManyField[Any, Any]) -> tuple[str, str]:
    """The column names of the two sides of a link table: the owner's, then the other's."""
    through: Any = relation.remote_field.through
    owner = through._meta.get_field(relation.m2m_field_name()).attname
    other = through._meta.get_field(relation.m2m_reverse_field_name()).attname
    return owner, other


def set_links(owner: models.Model, relation: str, targets: Iterable[models.Model]) -> None:
    """Make `targets` the elements of the set of links `owner.<relation>`, and record the change.

    An element added is a `put` of the link row, or a `restore` when the row was removed
    before; an element removed is a `delete`. The elements are read from the table itself,
    before and after.
    """
    field: Any = type(owner)._meta.get_field(relation)
    through: Any = field.remote_field.through
    entity = entity_of(through)
    owner_column, other_column = link_fields(field)
    home_id = home_of(owner)

    def elements() -> set[uuid.UUID]:
        found = through._default_manager.filter(**{owner_column: owner.pk})
        return set(found.values_list(other_column, flat=True))

    # The row of the owner is locked, so that two edits of its links at once do not both add
    # the same element: the table is written with plain inserts, which a conflict would refuse.
    type(owner)._base_manager.select_for_update().get(pk=owner.pk)
    was = elements()
    wanted = {target.pk for target in targets}
    if wanted - was:
        through._default_manager.bulk_create(
            [through(**{owner_column: owner.pk, other_column: o}) for o in wanted - was]
        )
    if was - wanted:
        through._default_manager.filter(
            **{owner_column: owner.pk, f"{other_column}__in": was - wanted}
        ).delete()
    now = elements()
    for other in sorted(now - was, key=str):
        row_id = link_row_id(home_id, through, owner.pk, other)
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
    for other in sorted(was - now, key=str):
        changes.record_change(
            home_id, entity, link_row_id(home_id, through, owner.pk, other), changes.DELETE
        )


# ---------- what a hard deletion elsewhere does to the rows of a home ----------


def home_is_erased(home_id: uuid.UUID) -> bool:
    """True once `erase_home` has written its row: the home and its change log go together."""
    return ErasedHome.objects.filter(pk=home_id).exists()


def clear_reference(model: type[models.Model], column: str, target: Any) -> None:
    """Record that rows of `model` lose the reference `column` to a row that is deleted for good.

    Called before the deletion, by the receivers of `pre_delete`: the database sets the
    column to NULL (`on_delete=SET_NULL`) without going through a service.
    """
    entity = entity_of(model)
    found = list(
        model._base_manager.filter(**{column: target}).values_list("pk", "home_id", "updated_at")
    )
    for pk, home_id, updated_at in found:
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
