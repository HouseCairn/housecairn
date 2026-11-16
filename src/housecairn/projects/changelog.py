"""The writes of the projects in the change log of their home (docs/adr/0077, 0079 lot M3e).

Every write path of a project, a budget line, a quote, a file, a link, a task of a project, an
annual envelope and the rooms of a project calls one of these functions in the transaction
that writes the row, so that the sync API gives it to the phones (`sync.changes`). A write
records the fields whose value changed only: a form that sends every field does not give a
clock to a field nobody touched.

Fields of the protocol are the columns of the model, a foreign key by its column name
(`project_id`); amounts are decimal strings, dates `YYYY-MM-DD`. Not carried: the id, the home,
the times the server keeps (`created_at`, `updated_at`), `deleted_at` (a deletion or a
restoration), where the reading of a link's page stands (not data of the home, as in the
export), and the stored file of a `ProjectFile`, carried as its file reference instead
(`file`, docs/adr/0077 need 10, lot M6 of docs/adr/0079).
"""

import datetime
import uuid
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from django.db import models

from housecairn.sync import changes, clock, files
from housecairn.sync.models import RowClocks
from housecairn.sync.rows import entity_of, link_fields, link_row_id

Values = dict[str, Any]
# A row as the database held it before a write: its carried fields and its `updated_at`.
Stored = tuple[Values, datetime.datetime | None]

_SKIPPED = frozenset({"id", "home", "created_at", "updated_at", "deleted_at"})
# Columns of a model that are not data of the protocol, with the reason above.
NOT_CARRIED: dict[str, frozenset[str]] = {
    "projects.projectfile": frozenset(
        {"file", "thumbnail", "content_type", "size_bytes", "width", "height"}
    ),
    "projects.projectlink": frozenset({"preview_state", "preview_started_at"}),
}


def carried_fields(model: type[models.Model]) -> list[models.Field[Any, Any]]:
    """The columns of `model` that a change carries."""
    skipped = _SKIPPED | NOT_CARRIED.get(entity_of(model), frozenset())
    return [f for f in model._meta.concrete_fields if f.name not in skipped]


def to_json(field: models.Field[Any, Any], value: Any) -> Any:
    """A value of a column as the protocol writes it."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        places = getattr(field, "decimal_places", None) or 0
        return str(value.quantize(Decimal(1).scaleb(-places)))
    if isinstance(value, datetime.datetime):
        return clock.format_clock(clock.clock_of_instant(value))
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def values_of(instance: models.Model) -> Values:
    """The carried fields of a row as it is in memory, by column name."""
    values = {
        f.attname: to_json(f, getattr(instance, f.attname)) for f in carried_fields(type(instance))
    }
    holder = files.holder_of(type(instance))
    if holder is not None:
        values[holder.field] = files.reference_of(instance, holder)
    return values


def stored(instance: models.Model) -> Stored | None:
    """The carried fields of a row as the database holds it, with its `updated_at`.

    None when the row is not written yet. Read from the database, not from the instance: a
    form may have changed the instance before the service is called.
    """
    model = type(instance)
    fields = carried_fields(model)
    row = (
        model._base_manager.filter(pk=instance.pk)
        .values(*[f.attname for f in fields], "updated_at")
        .first()
    )
    if row is None:
        return None
    values = {f.attname: to_json(f, row[f.attname]) for f in fields}
    holder = files.holder_of(model)
    if holder is not None:
        values[holder.field] = files.reference_of(model._base_manager.get(pk=instance.pk), holder)
    return values, row["updated_at"]


def saved(instance: models.Model, before: Stored | None = None) -> None:
    """Record the write of a row: every carried field when `before` is None (a new row), the
    fields whose value changed otherwise."""
    after = values_of(instance)
    if before is None:
        written, updated_at = after, None
    else:
        old, updated_at = before
        written = {name: value for name, value in after.items() if old.get(name) != value}
    if written:
        changes.record_change(
            _home_id(instance),
            entity_of(type(instance)),
            instance.pk,
            changes.PUT,
            written,
            updated_at=updated_at,
        )


def deleted(instance: models.Model) -> None:
    """Record the deletion of a row: soft, or a hard one that leaves this tombstone."""
    changes.record_change(
        _home_id(instance), entity_of(type(instance)), instance.pk, changes.DELETE
    )


def soft_delete(queryset: "models.QuerySet[Any]") -> int:
    """Soft-delete the rows of a queryset, each with its tombstone in the change log."""
    rows = list(queryset.values_list("pk", "home_id"))
    count: int = queryset.soft_delete()  # type: ignore[attr-defined]
    entity = entity_of(queryset.model)
    for pk, home_id in rows:
        changes.record_change(home_id, entity, pk, changes.DELETE)
    return count


def hard_delete(queryset: "models.QuerySet[Any]") -> int:
    """Delete the rows of a queryset for good, each leaving a tombstone (docs/adr/0077, need 4)."""
    rows = list(queryset.values_list("pk", "home_id"))
    entity = entity_of(queryset.model)
    for pk, home_id in rows:
        changes.record_change(home_id, entity, pk, changes.DELETE)
    count: int = queryset.delete()[0]
    return count


def update(queryset: "models.QuerySet[Any]", **values: Any) -> int:
    """`queryset.update(**values)`, each row recording the fields whose value changed."""
    model = queryset.model
    fields = {name: model._meta.get_field(name) for name in values}
    attnames = {name: field.attname for name, field in fields.items()}
    rows = list(queryset.values("pk", "home_id", "updated_at", *attnames.values()))
    count: int = queryset.update(**values)
    entity = entity_of(model)
    for row in rows:
        written = {}
        for name, value in values.items():
            field: Any = fields[name]
            new = value.pk if isinstance(value, models.Model) else value
            if to_json(field, row[attnames[name]]) != to_json(field, new):
                written[attnames[name]] = to_json(field, new)
        if written:
            changes.record_change(
                row["home_id"],
                entity,
                row["pk"],
                changes.PUT,
                written,
                updated_at=row["updated_at"],
            )
    return count


def links_of(owner: models.Model, relation_name: str) -> set[uuid.UUID]:
    """The ids of the rows `owner` links to through a link table, deleted rows included."""
    relation: Any = type(owner)._meta.get_field(relation_name)
    through: Any = relation.remote_field.through
    source, target = link_fields(relation)
    return set(through.objects.filter(**{source: owner.pk}).values_list(target, flat=True))


def links_changed(
    owner: models.Model, relation_name: str, before: Iterable[uuid.UUID] | None
) -> None:
    """Record the rows of a link table added and removed since `before` (None: no row)."""
    relation: Any = type(owner)._meta.get_field(relation_name)
    through: Any = relation.remote_field.through
    source, target = link_fields(relation)
    old = set(before or ())
    now = links_of(owner, relation_name)
    home_id = _home_id(owner)
    entity = entity_of(through)
    for other in sorted(old - now):
        changes.record_change(
            home_id, entity, link_row_id(home_id, through, owner.pk, other), changes.DELETE
        )
    for other in sorted(now - old):
        row_id = link_row_id(home_id, through, owner.pk, other)
        if RowClocks.objects.filter(
            home_id=home_id, entity=entity, row_id=row_id, tombstoned_at__isnull=False
        ).exists():
            # The same two rows linked again: the link row comes back from its tombstone.
            changes.record_change(home_id, entity, row_id, changes.RESTORE)
        changes.record_change(
            home_id,
            entity,
            row_id,
            changes.PUT,
            {source: str(owner.pk), target: str(other)},
        )


def restored(instances: Iterable[models.Model]) -> None:
    """Record the links of the projects a restore wrote (docs/adr/0069).

    The restore records the rows themselves (`sync.rows.record_created`); a link table has no
    row of its own there, so the links are recorded here, once they are written.
    """
    for instance in instances:
        for relation in type(instance)._meta.many_to_many:
            links_changed(instance, relation.name, None)


def _home_id(instance: models.Model) -> uuid.UUID:
    home_id: uuid.UUID = instance.home_id  # type: ignore[attr-defined]
    return home_id
