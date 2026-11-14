"""Problems reported by the members of a home, readers included (docs/adr/0047).

A report is a message to the people who run the home: what is wrong, where, and a photo
if there is one. It needs no date and no owner. A person who writes then makes it a task
(the link is kept), closes it, or reopens it.
"""

from typing import Any

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from housecairn.homes.models import HomeOwnedModel
from housecairn.reports.storage import report_storage
from housecairn.rooms.models import Item, Room
from housecairn.stock.models import Article
from housecairn.tasks.models import Task

TITLE_MAX_LENGTH = 200
# Open reports one person may have at the same time: a reader can write nothing else, so
# this is what keeps a tenant from filling the screens of the people who run the home.
MAX_OPEN_PER_REPORTER = 20


class ReportStatus(models.TextChoices):
    OPEN = "open", _("Open")
    TASK = "task", _("Made into a task")
    RESOLVED = "resolved", _("Resolved")
    NO_ACTION = "no_action", _("Closed without action")


CLOSED_STATUSES = (ReportStatus.RESOLVED, ReportStatus.NO_ACTION)
# What still needs somebody's attention: shown on Today to the people who write.
ATTENTION_STATUSES = (ReportStatus.OPEN,)


def report_photo_path(report: "Report", filename: str) -> str:
    """Storage name: home and report identifiers only, never a name chosen by the sender."""
    return f"{report.home_id}/{report.id}{filename}"


class Report(HomeOwnedModel):
    title = models.CharField(_("what is wrong"), max_length=TITLE_MAX_LENGTH)
    status = models.CharField(
        _("status"), max_length=16, choices=ReportStatus.choices, default=ReportStatus.OPEN
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Where: at most one of them. What is shown is `place`, written when the report is made,
    # so that it still reads right after the room has been renamed or deleted.
    room = models.ForeignKey(
        Room, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    article = models.ForeignKey(
        Article, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    place = models.CharField(_("where"), max_length=250, blank=True)
    # `reports` lets a task find the reports it was made from (its page, the list, the API).
    task = models.ForeignKey(
        Task, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports"
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Photo, re-encoded on upload (housecairn.rooms.images), and its thumbnail.
    photo = models.FileField(
        storage=report_storage,
        upload_to=report_photo_path,
        max_length=200,
        editable=False,
        blank=True,
    )
    thumbnail = models.FileField(
        storage=report_storage,
        upload_to=report_photo_path,
        max_length=200,
        editable=False,
        blank=True,
    )
    photo_content_type = models.CharField(max_length=32, editable=False, blank=True)
    photo_size_bytes = models.PositiveIntegerField(editable=False, default=0)

    class Meta:
        verbose_name = _("report")
        verbose_name_plural = _("reports")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["home", "status", "-created_at"], name="report_home_status")
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(room__isnull=True, item__isnull=True)
                    | models.Q(room__isnull=True, article__isnull=True)
                    | models.Q(item__isnull=True, article__isnull=True)
                ),
                name="report_one_place_at_most",
            )
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_open(self) -> bool:
        return self.status == ReportStatus.OPEN

    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED_STATUSES

    @property
    def has_photo(self) -> bool:
        return bool(self.photo)

    @property
    def where(self) -> Any:
        """The object the report is about, if it still exists."""
        for target in (self.item, self.room, self.article):
            if target is not None and target.deleted_at is None:
                return target
        return None
