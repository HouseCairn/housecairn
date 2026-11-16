"""The archive of a large home, made by the worker and downloaded later (docs/adr/0048).

Under `HOUSECAIRN_EXPORT_INLINE_MAX_BYTES` of files the archive is made during the
request, as before. Above, the request only records an `ExportJob` and queues
`make_archive`; the owner downloads the archive from the page of the job, signed in,
at most `HOUSECAIRN_EXPORT_DOWNLOADS` times and for `HOUSECAIRN_EXPORT_KEEP_HOURS` hours.
Expired archives are removed every hour by the worker (`PURGE_SCHEDULE`), and also
whenever an export starts or a download is asked for; so is a partial archive (`.part`)
older than that, left by a worker stopped while it wrote it.
"""

import datetime
import logging
from pathlib import Path
from typing import IO, Any
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from housecairn.demo import mode
from housecairn.homes import exports
from housecairn.homes.models import ExportJob, ExportStatus, Home

logger = logging.getLogger("housecairn.files")

FOLDER = "exports"
PURGE_SCHEDULE = "housecairn.homes.purge_exports"


class ExportUnavailableError(Exception):
    """The archive is not there (any more): still being made, failed, expired or used up."""


def _path(job: ExportJob) -> Path:
    return Path(settings.MEDIA_ROOT) / FOLDER / str(job.home_id) / f"{job.id}.zip"


def is_large(home: Home) -> bool:
    return exports.estimated_size(home) > settings.HOUSECAIRN_EXPORT_INLINE_MAX_BYTES


def purge_expired() -> None:
    """Remove the archives past their time or their downloads, and their rows.

    Also removes the partial archives older than the time an archive is kept: a worker
    stopped while it wrote one (a restart, the timeout of a task) leaves it behind; and
    the previews of imports past their time (`csv_import.purge_previews`).
    """
    from housecairn.homes import csv_import

    csv_import.purge_previews()
    now = timezone.now()
    for job in ExportJob.all_objects.filter(
        Q(expires_at__lte=now) | Q(downloads_left=0) | Q(status=ExportStatus.FAILED)
    ):
        _path(job).unlink(missing_ok=True)
        _path(job).with_suffix(".part").unlink(missing_ok=True)
        job.delete()
    oldest = (now - datetime.timedelta(hours=settings.HOUSECAIRN_EXPORT_KEEP_HOURS)).timestamp()
    for partial in (Path(settings.MEDIA_ROOT) / FOLDER).glob("*/*.part"):
        try:
            if partial.stat().st_mtime < oldest:
                partial.unlink()
        except OSError:
            logger.warning("A partial archive could not be removed: %s", partial)


def schedule_purge(**_kwargs: object) -> None:
    """Create the hourly schedule of `purge_expired` once; called after `migrate`."""
    from django_q.models import Schedule

    try:
        Schedule.objects.get_or_create(
            name=PURGE_SCHEDULE,
            defaults={
                "func": "housecairn.homes.jobs.purge_expired",
                "schedule_type": Schedule.HOURLY,
                "repeats": -1,
            },
        )
    except DatabaseError:  # before django_q's tables exist
        return


def _check_demo_exports(home: Home) -> None:
    """On a demonstration instance, bound the archives kept on disk (docs/adr/0071).

    One archive at a time for a home, and a few for the whole instance, since anyone may
    create homes there: the archives are not part of the quota of the files (`quotas.usage`).
    """
    active = ExportJob.all_objects.exclude(status=ExportStatus.FAILED)
    if active.filter(home=home).exists():
        mode.refuse(
            _("This is a demonstration instance: one export at a time for a home. Try again later.")
        )
    if active.count() >= mode.EXPORT_MAX_ACTIVE:
        mode.refuse(
            _("This is a demonstration instance: too many exports are being kept. Try again later.")
        )


def start_export(actor: Any, home: Home) -> ExportJob:
    """Record the export of the home for an owner and queue its archive."""
    from django_q.tasks import async_task

    from housecairn.audit import services as audit

    exports.refuse_example(home)
    exports.check_owner(actor, home)
    purge_expired()
    keep_hours = settings.HOUSECAIRN_EXPORT_KEEP_HOURS
    if mode.is_on():
        _check_demo_exports(home)
        keep_hours = min(keep_hours, mode.EXPORT_KEEP_HOURS)
    job: ExportJob = ExportJob.objects.create(
        home=home,
        requested_by=actor,
        downloads_left=settings.HOUSECAIRN_EXPORT_DOWNLOADS,
        expires_at=timezone.now() + datetime.timedelta(hours=keep_hours),
    )
    audit.record(
        "home.exported",
        actor=actor,
        home_id=home.id,
        format=f"v{exports.FORMAT_VERSION}",
        by="background",
    )
    async_task("housecairn.homes.jobs.make_archive", str(job.id))
    job.refresh_from_db()
    return job


def make_archive(job_id: str) -> None:
    """Task of the worker: write the archive of the job, then mark it ready."""
    job = ExportJob.objects.select_related("home").filter(pk=UUID(job_id)).first()
    if job is None or job.status != ExportStatus.PENDING:
        return
    path = _path(job)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    partial = path.with_suffix(".part")
    try:
        with partial.open("wb") as handle:
            exports.write_archive(job.home, handle)
        partial.chmod(0o640)
        partial.replace(path)
    except Exception:
        partial.unlink(missing_ok=True)
        ExportJob.objects.filter(pk=job.pk).update(
            status=ExportStatus.FAILED, finished_at=timezone.now()
        )
        logger.exception("The archive of an export could not be written: %s", job.pk)
        return
    ExportJob.objects.filter(pk=job.pk).update(
        status=ExportStatus.READY,
        size_bytes=path.stat().st_size,
        file_name=exports.archive_name(job.home),
        finished_at=timezone.now(),
    )


def job_of(actor: Any, home: Home, job_id: UUID) -> ExportJob:
    """A job of the home, for one of its owners; ExportUnavailableError when there is none."""
    exports.check_owner(actor, home)
    job: ExportJob | None = ExportJob.objects.filter(home=home, pk=job_id).first()
    if job is None:
        raise ExportUnavailableError
    return job


def open_download(actor: Any, home: Home, job_id: UUID) -> tuple[IO[bytes], str]:
    """Take one download of the archive: the file, opened, and its name."""
    purge_expired()
    job = job_of(actor, home, job_id)
    taken = ExportJob.objects.filter(
        pk=job.pk,
        status=ExportStatus.READY,
        expires_at__gt=timezone.now(),
        downloads_left__gt=0,
    ).update(downloads_left=F("downloads_left") - 1)
    if not taken:
        raise ExportUnavailableError
    try:
        return _path(job).open("rb"), job.file_name
    except OSError:
        raise ExportUnavailableError from None


__all__ = [
    "ExportUnavailableError",
    "PermissionDenied",
    "is_large",
    "open_download",
    "start_export",
]
