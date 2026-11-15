"""How long the change log keeps what it holds: 400 days (docs/adr/0077, 0079).

A change of the feed, a deleted row and its tombstone stay 400 days, so a phone offline for a
year still learns of a deletion. Then the nightly task removes them. A cursor below the
`floor` of its home then asks for the snapshot again (410 `cursor_expired`).
"""

import contextlib
import datetime
import uuid
from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.db import models, transaction
from django.db.models import Max
from django.db.models.deletion import Collector
from django.utils import timezone

from housecairn.core.models import BaseModel
from housecairn.sync.models import Change, HomeHead, RowClocks

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
                ids = list(chunk.values_list("row_id", flat=True))
                removed_rows += _delete_rows(model, ids)
            removed += chunk.delete()[0]
    return removed, removed_rows


def _model_of(entity: str) -> type[BaseModel] | None:
    """The model an entity type names (`rooms.room`), None for a link table or an unknown one."""
    app_label, _dot, model_name = entity.partition(".")
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return None
    return model if issubclass(model, BaseModel) else None


def _delete_rows(model: type[BaseModel], ids: list[uuid.UUID]) -> int:
    """Delete rows that are still deleted, and what hangs off them.

    The children go by cascade (the items and photos of a room). Their clocks go with them, and
    so do their files, once the transaction is committed: neither may outlive the row.
    """
    rows = model.all_objects.filter(pk__in=ids, deleted_at__isnull=False)
    collector = Collector(using=rows.db)
    collector.collect(list(rows))
    doomed: dict[type[models.Model], list[models.Model]] = {}
    for kind, instances in collector.data.items():
        doomed.setdefault(kind, []).extend(instances)
    for fast in collector.fast_deletes:
        if isinstance(fast, models.QuerySet):
            doomed.setdefault(fast.model, []).extend(fast)
    files: list[tuple[Any, str]] = []
    for kind, instances in doomed.items():
        files.extend(_files_of(kind, instances))
        if issubclass(kind, BaseModel) and isinstance(kind._meta.pk, models.UUIDField):
            gone = [row.pk for row in instances if kind is not model or row.pk not in ids]
            for start in range(0, len(gone), CHUNK):
                RowClocks.objects.filter(
                    entity=kind._meta.label_lower, row_id__in=gone[start : start + CHUNK]
                ).delete()
    deleted: int = collector.delete()[0]
    if files:
        transaction.on_commit(lambda: _remove_files(files))
    return deleted


def _files_of(kind: type[models.Model], instances: list[models.Model]) -> list[tuple[Any, str]]:
    found = []
    for field in kind._meta.concrete_fields:
        if isinstance(field, models.FileField):
            for instance in instances:
                name = getattr(instance, field.attname).name
                if name:
                    found.append((field.storage, name))
    return found


def _remove_files(files: list[tuple[Any, str]]) -> None:
    for storage, name in files:
        with contextlib.suppress(OSError):
            storage.delete(name)
