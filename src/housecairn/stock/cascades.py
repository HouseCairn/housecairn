"""What a hard deletion leaves behind in the stock, and in the change log (docs/adr/0077, lot M3d).

Rows go for good in three cases: the retention purge of the change log (400 days after their
deletion), the erasure of a home, and the deletion of an account. A cascade then removes the
children of a row by queryset, without a service: the receivers below make it leave nothing
behind, neither the clocks of the children (they have no foreign key to their row), nor the
files on the disk, and record the references that become NULL.
"""

import logging
import uuid
from typing import Any

from django.core.exceptions import SuspiciousOperation
from django.core.files.storage import Storage
from django.db import transaction
from django.db.models.signals import pre_delete

from housecairn.stock import changelog
from housecairn.stock.models import Article, Movement, Supplier
from housecairn.stock.storage import stock_storage

logger = logging.getLogger(__name__)


def erase_file_on_commit(storage: Storage, name: str) -> None:
    """Remove a file from the disk once the deletion is committed; a failure is only logged."""
    if not name:
        return

    def erase() -> None:
        try:
            storage.delete(name)
        except (OSError, ValueError, SuspiciousOperation):
            logger.error("Could not erase a file of a deleted row: %s", name)

    transaction.on_commit(erase)


def article_is_going(sender: Any, instance: Article, **_kwargs: Any) -> None:
    """An article is deleted for good: its suppliers, movements and links go with it."""
    home_id = instance.home_id
    for model in (Supplier, Movement):
        ids = list(model.all_objects.filter(article=instance.pk).values_list("pk", flat=True))
        changelog.forget_clocks(home_id, changelog.entity_of(model), ids)
    for relation in ("rooms", "compatible_items"):
        field: Any = Article._meta.get_field(relation)
        through: Any = field.remote_field.through
        entity = changelog.entity_of(through)
        other = f"{field.m2m_reverse_field_name()}_id"
        others = list(
            through._default_manager.filter(article_id=instance.pk).values_list(other, flat=True)
        )
        changelog.forget_clocks(
            home_id,
            entity,
            [changelog.link_id(home_id, entity, instance.pk, o) for o in others],
        )
    if instance.deleted_at is None:  # a tombstone is counted and removed by the retention purge
        changelog.forget_clocks(home_id, changelog.entity_of(Article), [instance.pk])
    erase_file_on_commit(stock_storage, instance.label.name if instance.label else "")


def link_target_is_going(relation: str) -> Any:
    """The receiver for a room or an item deleted for good: the links that point at it go."""

    def receiver(sender: Any, instance: Any, **_kwargs: Any) -> None:
        field: Any = Article._meta.get_field(relation)
        through: Any = field.remote_field.through
        entity = changelog.entity_of(through)
        column = f"{field.m2m_reverse_field_name()}_id"
        pairs = list(
            through._default_manager.filter(**{column: instance.pk}).values_list(
                "article_id", "article__home_id"
            )
        )
        by_home: dict[uuid.UUID, list[uuid.UUID]] = {}
        for article_id, home_id in pairs:
            by_home.setdefault(home_id, []).append(
                changelog.link_id(home_id, entity, article_id, instance.pk)
            )
        for home_id, row_ids in by_home.items():
            changelog.forget_clocks(home_id, entity, row_ids)

    return receiver


def occurrence_is_going(sender: Any, instance: Any, **_kwargs: Any) -> None:
    """An occurrence is deleted for good: the movements it wrote lose their reference to it."""
    changelog.clear_reference(Movement, "occurrence_id", instance.pk)


def connect() -> None:
    """Connect the receivers; called by `StockConfig.ready`."""
    from housecairn.rooms.models import Item, Room
    from housecairn.tasks.models import Occurrence

    pre_delete.connect(
        article_is_going, sender=Article, dispatch_uid="stock.article_is_going", weak=False
    )
    pre_delete.connect(
        link_target_is_going("rooms"), sender=Room, dispatch_uid="stock.room_is_going", weak=False
    )
    pre_delete.connect(
        link_target_is_going("compatible_items"),
        sender=Item,
        dispatch_uid="stock.item_is_going",
        weak=False,
    )
    pre_delete.connect(
        occurrence_is_going, sender=Occurrence, dispatch_uid="stock.occurrence_is_going", weak=False
    )
