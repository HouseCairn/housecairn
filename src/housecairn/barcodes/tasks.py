"""The weekly refresh of the local catalogue, by slices of the worker (docs/adr/0083).

A slice works at most `SLICE_SECONDS`, under the 300 seconds of the worker: it downloads the
export (resumed by ranges), then imports it (resumed by rows read), and queues the next slice
until the import is done. The schedule exists only while the catalogue is on.
"""

import logging

from django.db import DatabaseError

from housecairn.barcodes import catalogue, download
from housecairn.barcodes.models import CatalogueImport, ImportState
from housecairn.instance.config import get_setting

logger = logging.getLogger("housecairn.barcodes")

SCHEDULE = "housecairn.barcodes.refresh"
SLICE_SECONDS = 240.0


def refresh() -> None:
    """Start a refresh: forget the previous file, then run the first slice."""
    if not get_setting("barcode_catalogue_enabled"):
        return
    final = download.target()
    if final.exists():
        final.unlink()
    step()


def step() -> None:
    """One slice of the refresh; queues the next one while there is work left."""
    from django_q.tasks import async_task

    if not get_setting("barcode_catalogue_enabled"):
        return
    final = download.target()
    if not final.exists() and not download.fetch(SLICE_SECONDS):
        async_task("housecairn.barcodes.tasks.step", ack_failure=True)
        return
    progress = catalogue.import_file(final, seconds=SLICE_SECONDS)
    if progress.state == ImportState.IMPORTING:
        async_task("housecairn.barcodes.tasks.step", ack_failure=True)


def schedule(**_kwargs: object) -> None:
    """Create the weekly schedule while the catalogue is on, remove it otherwise.

    Called after `migrate`; the setting screen calls it again when the setting changes.
    """
    from django_q.models import Schedule

    try:
        if not get_setting("barcode_catalogue_enabled"):
            Schedule.objects.filter(name=SCHEDULE).delete()
            return
        Schedule.objects.get_or_create(
            name=SCHEDULE,
            defaults={
                "func": "housecairn.barcodes.tasks.refresh",
                "schedule_type": Schedule.WEEKLY,
                "repeats": -1,
            },
        )
    except DatabaseError:
        logger.warning("The schedule of the barcode catalogue could not be written.")


def status() -> CatalogueImport | None:
    return CatalogueImport.objects.order_by("-started_at").first()
