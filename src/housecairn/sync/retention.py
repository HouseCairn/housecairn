"""How long the change log keeps what it holds: 400 days (docs/adr/0077, 0079).

A change of the feed, a deleted row and its tombstone stay 400 days, so a phone offline for a
year still learns of a deletion. Then the nightly task removes them. A cursor below the
`floor` of its home then asks for the snapshot again (410 `cursor_expired`).
"""

import datetime
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

from django.apps import apps
from django.core.exceptions import SuspiciousOperation
from django.core.files.storage import Storage
from django.db import models, router, transaction
from django.db.models import Max
from django.db.models.deletion import Collector
from django.utils import timezone

from housecairn.core.models import BaseModel
from housecairn.sync import changes
from housecairn.sync import rows as rows_module
from housecairn.sync.models import Change, HomeHead, RowClocks

logger = logging.getLogger(__name__)

RETENTION = datetime.timedelta(days=400)
# Rows removed per statement, to keep the lists of identifiers short.
CHUNK = 500


@dataclass(frozen=True)
class Purged:
    changes: int
    tombstones: int
    rows: int


def limit(now: datetime.datetime | None = None) -> datetime.datetime:
    """The instant before which what was written is expired: exactly 400 days before `now`.

    Something written exactly 400 days ago is still kept; a microsecond older is purged.
    """
    return (now or timezone.now()) - RETENTION


def purge(now: datetime.datetime | None = None) -> Purged:
    """Remove the changes and the tombstones that the retention has expired."""
    expired = limit(now)
    with transaction.atomic():
        changes = _purge_changes(expired)
        tombstones, rows = _purge_tombstones(expired)
    return Purged(changes, tombstones, rows)


def _purge_changes(expired: datetime.datetime) -> int:
    old = Change.objects.filter(created_at__lt=expired)
    # The floor first: it says up to which position the feed no longer answers.
    for home_id, pos in old.values_list("home_id").annotate(last=Max("pos")):
        HomeHead.objects.filter(home_id=home_id, floor__lt=pos).update(floor=pos)
    return old.delete()[0]


def _purge_tombstones(expired: datetime.datetime) -> tuple[int, int]:
    """Remove the clocks of rows deleted before `expired`, and those rows themselves.

    A row is removed only if it is still deleted. The entity of a link table has no model of
    its own: its clocks go, there is no row to remove.
    """
    removed_rows = 0
    old = RowClocks.objects.filter(tombstoned_at__lt=expired)
    ids_by_entity: dict[str, list[int]] = {}
    for pk, entity in old.values_list("pk", "entity"):
        ids_by_entity.setdefault(entity, []).append(pk)
    removed = 0
    for entity, pks in ids_by_entity.items():
        model = _model_of(entity)
        for start in range(0, len(pks), CHUNK):
            chunk = RowClocks.objects.filter(pk__in=pks[start : start + CHUNK])
            if model is not None:
                removed_rows += _remove_rows(model, list(chunk.values_list("row_id", flat=True)))
            removed += chunk.delete()[0]
    return removed, removed_rows


def _remove_rows(model: type[BaseModel], ids: list[uuid.UUID]) -> int:
    """Remove the rows that are still deleted, and what goes with them by cascade.

    The rows the cascade takes (the occurrences of a task, its attachments) leave no clock
    behind and no file on the disk; a row whose reference the cascade empties (`SET_NULL`)
    records that change, as any write of a row of a home does.
    """
    doomed = list(model.all_objects.filter(pk__in=ids, deleted_at__isnull=False))
    if not doomed:
        return 0
    collector = Collector(using=router.db_for_write(model))
    collector.collect(doomed)
    gone: dict[str, set[uuid.UUID]] = {}
    files: list[tuple[Storage, str]] = []
    cascaded = [row for rows in collector.data.values() for row in rows]
    cascaded += [row for rows in collector.fast_deletes for row in rows]
    for row in cascaded:
        gone.setdefault(row._meta.label_lower, set()).add(row.pk)
        for field in row._meta.concrete_fields:
            if isinstance(field, models.FileField):
                name = getattr(row, field.attname).name
                if name:
                    files.append((field.storage, name))
    # Django keeps, per (field, value), lists of rows or querysets: read them before deleting.
    emptied: list[tuple[Any, Any, list[Any]]] = [
        (field, value, list(cast(Iterable[Any], rows)))
        for (field, value), batches in collector.field_updates.items()
        for rows in batches
    ]
    entity = model._meta.label_lower
    removed = sum(1 for row in doomed if row.pk in gone.get(entity, set()))
    # The clocks of the rows themselves go with their chunk (`_purge_tombstones`).
    gone[entity] = gone.get(entity, set()) - {row.pk for row in doomed}
    collector.delete()
    for label, row_ids in gone.items():
        RowClocks.objects.filter(entity=label, row_id__in=row_ids).delete()
    for field, value, rows in emptied:
        for row in rows:
            if (
                isinstance(row, BaseModel)
                and hasattr(row, "home_id")
                and row.pk not in gone.get(row._meta.label_lower, set())
            ):
                changes.record_change(
                    row.home_id,
                    row._meta.label_lower,
                    row.pk,
                    changes.PUT,
                    {field.attname: rows_module.wire_value(field, value)},
                    updated_at=row.updated_at,
                )
    transaction.on_commit(lambda: _erase(files))
    return removed


def _erase(files: list[tuple[Storage, str]]) -> None:
    """Remove the files of the rows the purge removed; a failure is logged, never raised."""
    for storage, name in files:
        try:
            storage.delete(name)
        except (OSError, ValueError, SuspiciousOperation):
            logger.error("Could not erase the file of a purged row: %s", name)


def _model_of(entity: str) -> type[BaseModel] | None:
    """The model an entity type names (`rooms.room`), None for a link table or an unknown one."""
    app_label, _dot, model_name = entity.partition(".")
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return None
    return model if issubclass(model, BaseModel) else None
