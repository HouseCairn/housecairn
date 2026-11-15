"""Projects, their budget lines, quotes, files and task links (docs/adr/0050).

A project is a piece of work with an end, bigger than a task: redo the bathroom, paint
the living room. Its budget is lines, each with three amounts: planned (typed), committed
(the quotes accepted on it, worked out when read) and paid (typed). Amounts are `Decimal`
in the currency of the home, never a float. The tasks of a project are the tasks of
`housecairn.tasks`, tied to it by `ProjectTask`, so that `tasks` holds nothing of projects.
"""

from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from housecairn.homes.models import HomeOwnedModel
from housecairn.projects.storage import project_storage
from housecairn.rooms.models import Room, WebUrlField, notes_field
from housecairn.tasks import recurrence
from housecairn.tasks.models import Contractor, Priority, Season, Task

MAX_PHOTOS_PER_PROJECT = 30
MAX_FILES_PER_QUOTE = 5
MAX_LINKS_PER_PROJECT = 30
LINK_TITLE_MAX_LENGTH = 200


class PreviewState(models.TextChoices):
    """Where the reading of the page of a link stands (docs/adr/0070).

    Empty: the page is not read (previews off on the instance, when the link was added or
    before the worker got to it). `running` ends in `done` (the page was read, whatever
    title or picture it gave) or `failed` (the page could not be read: unreachable, refused
    by the guard, not a web page, an error; or no end within
    `links.PREVIEW_DEADLINE_SECONDS` after `preview_started_at`).
    """

    NONE = "", _("Page not read")
    RUNNING = "running", _("Page being read")
    DONE = "done", _("Page read")
    FAILED = "failed", _("Page could not be read")


# A project lasts at most a year of elapsed time (a task, thirty days).
MAX_PROJECT_MINUTES = 365 * 24 * 60
ENVELOPE_YEARS = (2000, 2200)
ZERO = Decimal("0")


class ProjectStatus(models.TextChoices):
    IDEA = "idea", _("Idea")
    PLANNED = "planned", pgettext_lazy("project status", "Planned")
    IN_PROGRESS = "in_progress", _("In progress")
    DONE = "done", _("Finished")
    ABANDONED = "abandoned", _("Abandoned")


# Shown first: what is going on, then what is coming, then what is over.
STATUS_ORDER = (
    ProjectStatus.IN_PROGRESS,
    ProjectStatus.PLANNED,
    ProjectStatus.IDEA,
    ProjectStatus.DONE,
    ProjectStatus.ABANDONED,
)
ACTIVE_STATUSES = (ProjectStatus.IN_PROGRESS, ProjectStatus.PLANNED, ProjectStatus.IDEA)


class BudgetNature(models.TextChoices):
    """What a budget line pays for: a world-wide list plus Other (docs/adr/0045)."""

    MATERIAL = "material", _("Materials")
    LABOUR = "labour", _("Labour")
    RENTAL = "rental", _("Rental")
    OTHER = "other", _("Other")


class QuoteStatus(models.TextChoices):
    REQUESTED = "requested", _("Requested")
    RECEIVED = "received", _("Received")
    ACCEPTED = "accepted", _("Accepted")
    REJECTED = "rejected", _("Rejected")


def money_field(label: Any, **options: Any) -> models.DecimalField[Decimal, Decimal]:
    """An amount of the currency of the home: 0 to 9,999,999,999.99, two decimals."""
    return models.DecimalField(
        label,
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(ZERO)],
        **options,
    )


class Project(HomeOwnedModel):
    """A piece of work to plan, price and follow. Only the title is required."""

    title = models.CharField(_("title"), max_length=200)
    goal = notes_field(_("goal"))
    status = models.CharField(
        _("status"), max_length=12, choices=ProjectStatus.choices, default=ProjectStatus.IDEA
    )
    priority = models.CharField(
        _("priority"), max_length=8, choices=Priority.choices, default=Priority.NORMAL
    )
    rooms = models.ManyToManyField(
        Room, blank=True, related_name="+", verbose_name=_("rooms concerned")
    )
    estimated_minutes = models.PositiveIntegerField(
        _("estimated duration (minutes)"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_PROJECT_MINUTES)],
    )
    # The season the work is wished for; empty: any time. The months of a predefined season
    # are those of the hemisphere of the home, worked out when read (`season_months`); the
    # two fields below hold the person's own months of a custom season only.
    season = models.CharField(
        _("preferred season"), max_length=10, choices=Season.choices, blank=True, default=""
    )
    season_start_month = models.PositiveSmallIntegerField(
        _("season starts in"), null=True, blank=True
    )
    season_end_month = models.PositiveSmallIntegerField(_("season ends in"), null=True, blank=True)

    class Meta:
        verbose_name = _("project")
        verbose_name_plural = _("projects")
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["home", "status"], name="project_home_status")]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(season_start_month__isnull=True, season_end_month__isnull=True)
                    | models.Q(season_start_month__isnull=False, season_end_month__isnull=False)
                ),
                name="project_season_both_or_none",
            )
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def season_months(self) -> tuple[int, int] | None:
        """First and last month of the preferred season, in the hemisphere of the home."""
        if not self.season:
            return None
        if self.season == Season.CUSTOM:
            if self.season_start_month is None or self.season_end_month is None:
                return None
            return (self.season_start_month, self.season_end_month)
        return recurrence.season_window(self.season, self.home.hemisphere)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        if self.season == Season.CUSTOM:
            months = (self.season_start_month, self.season_end_month)
            if None in months or not all(1 <= m <= 12 for m in months if m is not None):
                errors["season_start_month"] = _("Give the first and the last month of the season.")
        elif self.season_start_month is not None or self.season_end_month is not None:
            errors["season_start_month"] = _("Months are for a custom season only.")
        if errors:
            raise ValidationError(errors)


class BudgetLine(HomeOwnedModel):
    """One line of the budget of a project: planned and paid are typed, committed is derived."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="lines")
    label = models.CharField(_("label"), max_length=120)
    nature = models.CharField(
        _("nature"), max_length=10, choices=BudgetNature.choices, default=BudgetNature.OTHER
    )
    planned = money_field(_("planned"), default=ZERO)
    paid = money_field(
        _("paid"),
        default=ZERO,
        help_text=_("What has been paid so far; Sure will check it against the real payments."),
    )

    class Meta:
        verbose_name = _("budget line")
        verbose_name_plural = _("budget lines")
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(planned__gte=0, paid__gte=0), name="budgetline_amounts_positive"
            )
        ]

    def __str__(self) -> str:
        return self.label


def file_path(stored: "ProjectFile", filename: str) -> str:
    """Storage name: home and file identifiers only, never a name chosen by the sender."""
    return f"{stored.home_id}/{stored.id}{filename}"


class Quote(HomeOwnedModel):
    """A quote from a contractor. Accepting it commits its amount on a budget line."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="quotes")
    contractor = models.ForeignKey(
        Contractor,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("contractor"),
    )
    amount = money_field(_("amount"), default=ZERO)
    issued_on = models.DateField(_("date"), null=True, blank=True)
    valid_until = models.DateField(_("valid until"), null=True, blank=True)
    status = models.CharField(
        _("status"), max_length=10, choices=QuoteStatus.choices, default=QuoteStatus.REQUESTED
    )
    # The line the amount is committed on; set when the quote is accepted, empty otherwise.
    # CASCADE, not SET_NULL: a line that is really erased (the home or the account goes) takes
    # its accepted quotes with it, since the database refuses an accepted quote without a line.
    # The services never erase a line, they soft-delete it and release its quotes first.
    budget_line = models.ForeignKey(
        BudgetLine,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="quotes",
        verbose_name=_("budget line"),
    )
    note = models.CharField(_("note"), max_length=200, blank=True)
    # Room for the document in Paperless-ngx (a later lot fills them): nothing reads them yet.
    paperless_id = models.PositiveIntegerField(
        _("Paperless document number"), null=True, blank=True
    )
    paperless_url = WebUrlField(_("Paperless document link"), max_length=500, blank=True)

    class Meta:
        verbose_name = _("quote")
        verbose_name_plural = _("quotes")
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gte=0), name="quote_amount_positive"),
            models.CheckConstraint(
                condition=~models.Q(status="accepted") | models.Q(budget_line__isnull=False),
                name="quote_accepted_has_line",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.project_id} {self.amount}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        if self.issued_on and self.valid_until and self.valid_until < self.issued_on:
            errors["valid_until"] = _("The validity cannot end before the date of the quote.")
        if self.contractor is not None and self.contractor.home_id != self.home_id:
            errors["contractor"] = _("This contractor is not in this home.")
        line = self.budget_line
        if line is not None and line.project_id != self.project_id:
            errors["budget_line"] = _("This budget line is not in this project.")
        if self.status == QuoteStatus.ACCEPTED and line is None:
            errors["budget_line"] = _("Choose the budget line the quote is committed on.")
        if errors:
            raise ValidationError(errors)


class ProjectFile(HomeOwnedModel):
    """A photo of a project, or a file of one of its quotes (docs/adr/0050)."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="files")
    quote = models.ForeignKey(
        Quote, null=True, blank=True, on_delete=models.CASCADE, related_name="files"
    )
    # Name shown to people, cleaned of any path and control character; never used on disk.
    name = models.CharField(_("name"), max_length=120)
    file = models.FileField(
        storage=project_storage, upload_to=file_path, max_length=200, editable=False
    )
    thumbnail = models.FileField(
        storage=project_storage, upload_to=file_path, max_length=200, editable=False, blank=True
    )
    content_type = models.CharField(max_length=100, editable=False)
    is_image = models.BooleanField(default=False, editable=False)
    size_bytes = models.PositiveIntegerField(editable=False)
    width = models.PositiveIntegerField(null=True, editable=False)
    height = models.PositiveIntegerField(null=True, editable=False)

    class Meta:
        verbose_name = _("project file")
        verbose_name_plural = _("project files")
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return self.name


class ProjectLink(HomeOwnedModel):
    """A link of inspiration: an address, and the title and picture the page gave (adr 0050).

    Title and picture are read once, by the worker, just after the link is added, if the
    instance allows it (docs/adr/0070). The picture is a `ProjectFile` of the project, stored
    here like a photo and never fetched again; a page never points to the remote one.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="links")
    url = WebUrlField(_("address"), max_length=500)
    title = models.CharField(_("title"), max_length=LINK_TITLE_MAX_LENGTH, blank=True)
    image = models.OneToOneField(
        ProjectFile,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="link",
    )
    preview_state = models.CharField(
        max_length=8,
        choices=PreviewState.choices,
        default=PreviewState.NONE,
        blank=True,
        editable=False,
    )
    preview_started_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("link")
        verbose_name_plural = _("links")
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return self.title or self.url


class ProjectTask(HomeOwnedModel):
    """Ties a task to its project. A task is in one project at most; `tasks` knows nothing."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="task_links")
    task = models.OneToOneField(Task, on_delete=models.CASCADE, related_name="project_link")

    class Meta:
        verbose_name = _("task of a project")
        verbose_name_plural = _("tasks of projects")
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.project_id} {self.task_id}"


class AnnualEnvelope(HomeOwnedModel):
    """What the household wants to spend on work in a year (the lot on budgets builds on it)."""

    year = models.PositiveSmallIntegerField(
        _("year"),
        validators=[MinValueValidator(ENVELOPE_YEARS[0]), MaxValueValidator(ENVELOPE_YEARS[1])],
    )
    amount = money_field(_("envelope"))

    class Meta:
        verbose_name = _("annual envelope")
        verbose_name_plural = _("annual envelopes")
        ordering = ["-year"]
        constraints = [
            models.UniqueConstraint(
                fields=["home", "year"],
                condition=models.Q(deleted_at__isnull=True),
                name="one_envelope_per_year",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="envelope_amount_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.year} {self.amount}"
