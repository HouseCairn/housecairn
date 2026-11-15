"""What a hard deletion leaves behind in the reports, and in the change log (docs/adr/0077, M3d).

Rows go for good by the retention purge of the change log, the erasure of a home and the
deletion of an account. A report that points at a row deleted for good loses the reference
(`on_delete=SET_NULL`) without a service: the receivers below record it. A report deleted for
good leaves neither its clocks nor its photo behind.
"""

from typing import Any

from django.conf import settings
from django.db.models.signals import pre_delete

from housecairn.reports.models import Report
from housecairn.reports.storage import report_storage
from housecairn.stock import changelog
from housecairn.stock.cascades import erase_file_on_commit


def report_is_going(sender: Any, instance: Report, **_kwargs: Any) -> None:
    changelog.forget_clocks(instance.home_id, changelog.entity_of(Report), [instance.pk])
    erase_file_on_commit(report_storage, instance.photo.name if instance.photo else "")
    erase_file_on_commit(report_storage, instance.thumbnail.name if instance.thumbnail else "")


def references_are_lost(*columns: str) -> Any:
    """The receiver for a row that reports point at by these columns."""

    def receiver(sender: Any, instance: Any, **_kwargs: Any) -> None:
        for column in columns:
            changelog.clear_reference(Report, column, instance.pk)

    return receiver


def connect() -> None:
    """Connect the receivers; called by `ReportsConfig.ready`."""
    from django.apps import apps

    pre_delete.connect(report_is_going, sender=Report, dispatch_uid="reports.report_is_going")
    targets = {
        "rooms.Room": ("room_id",),
        "rooms.Item": ("item_id",),
        "stock.Article": ("article_id",),
        "tasks.Task": ("task_id",),
        settings.AUTH_USER_MODEL: ("reporter_id", "closed_by_id"),
    }
    for label, columns in targets.items():
        pre_delete.connect(
            references_are_lost(*columns),
            sender=apps.get_model(label),
            dispatch_uid=f"reports.lost.{label}",
        )
