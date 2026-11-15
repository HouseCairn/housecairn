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
from typing import Any

from django.apps import apps
from django.core.exceptions import SuspiciousOperation
from django.db import models, router, transaction
from django.db.models import Max
from django.db.models.deletion import Collector
from django.utils import timezone

from housecairn.core.models import BaseModel
from housecairn.sync.links import entity_of, link_row_id
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
                ids = list(chunk.values_list("row_id", flat=True))
                doomed = model.all_objects.filter(pk__in=ids, deleted_at__isnull=False)
                removed += _forget_what_goes_with(doomed)
                removed_rows += doomed.delete()[0]
            removed += chunk.delete()[0]
    return removed, removed_rows


def _forget_what_goes_with(doomed: "models.QuerySet[Any]") -> int:
    """Remove the clocks and the files of every row the deletion of `doomed` takes with it.

    The deletion cascades to the children of the rows (the quotes of a project, the rows of
    its link tables), which have no foreign key to their clocks: their clocks would be left
    behind, and their files on the disk. The files go once the purge is committed.
    """
    collector = Collector(using=router.db_for_write(doomed.model))
    collector.collect(list(doomed))
    rows: dict[str, set[uuid.UUID]] = {}
    files: list[models.fields.files.FieldFile] = []
    batches: list[tuple[type[models.Model], Iterable[models.Model]]] = list(collector.data.items())
    batches += [(qs.model, qs) for qs in collector.fast_deletes if isinstance(qs, models.QuerySet)]
    for model, instances in batches:
        file_fields = [f for f in model._meta.concrete_fields if isinstance(f, models.FileField)]
        for instance in instances:
            row_id = _row_id(model, instance)
            if row_id is not None:
                rows.setdefault(entity_of(model), set()).add(row_id)
            files += [getattr(instance, f.attname) for f in file_fields]
    removed = 0
    for entity, ids in rows.items():
        removed += RowClocks.objects.filter(entity=entity, row_id__in=ids).delete()[0]
    names: list[tuple[Any, str]] = [(f.storage, f.name) for f in files if f.name]
    transaction.on_commit(lambda: _erase(names))
    return removed


def _row_id(model: type[models.Model], instance: models.Model) -> uuid.UUID | None:
    """The id of a row in the change log: its own, or that of a link table (`sync.links`)."""
    if not model._meta.auto_created:
        return instance.pk if isinstance(instance.pk, uuid.UUID) else None
    owner_field, other_field = (
        f for f in model._meta.concrete_fields if isinstance(f, models.ForeignKey)
    )
    owner: Any = getattr(instance, owner_field.name)
    home_id = getattr(owner, "home_id", None)
    if home_id is None:
        return None
    return link_row_id(
        home_id,
        model,
        getattr(instance, owner_field.attname),
        getattr(instance, other_field.attname),
    )


def _erase(names: list[tuple[Any, str]]) -> None:
    for storage, name in names:
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
