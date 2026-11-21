"""Tasks, their occurrences, contractors and attachments (docs/adr/0031 to 0034).

A task is the thing to do ("change the smoke detector battery"); an occurrence is one
time it has to be done, with its due date and its outcome. A task that repeats always
has exactly one open occurrence; closing it creates the next.
"""

import datetime
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import (
    MaxLengthValidator,
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from housecairn.core.models import BaseModel
from housecairn.homes.models import HomeOwnedModel
from housecairn.rooms.models import Item, Room, notes_field
from housecairn.tasks import durations, recurrence
from housecairn.tasks.storage import attachment_storage

PROCEDURE_MAX_LENGTH = 20000


def validate_language(value: str) -> None:
    """A language the instance ships; empty means "follow the browser" (docs/adr/0042)."""
    if value and value not in {code for code, _name in settings.LANGUAGES}:
        raise ValidationError(_("This language is not offered."), code="invalid_language")


MAX_ATTACHMENTS_PER_TASK = 30


class Priority(models.TextChoices):
    LOW = "low", _("Low")
    NORMAL = "normal", _("Normal")
    HIGH = "high", _("High")


class RepeatMode(models.TextChoices):
    ONCE = recurrence.ONCE, _("Once")
    FIXED = recurrence.FIXED, _("Fixed date")
    AFTER_LAST = recurrence.AFTER_LAST, _("After the last time")


class RepeatUnit(models.TextChoices):
    DAY = recurrence.DAY, _("days")
    WEEK = recurrence.WEEK, _("weeks")
    MONTH = recurrence.MONTH, _("months")
    YEAR = recurrence.YEAR, _("years")


class Season(models.TextChoices):
    """Predefined seasons follow the hemisphere of the home; Custom takes its own months.

    The heating season is no longer one: it is custom months (`recurrence.legacy_season`).
    """

    SPRING = recurrence.SPRING, _("Spring")
    SUMMER = recurrence.SUMMER, _("Summer")
    AUTUMN = recurrence.AUTUMN, _("Autumn")
    WINTER = recurrence.WINTER, _("Winter")
    CUSTOM = recurrence.CUSTOM, _("Custom months")


class Status(models.TextChoices):
    PLANNED = "planned", _("Planned")
    DONE = "done", pgettext_lazy("task", "Done")
    POSTPONED = "postponed", _("Postponed")
    SKIPPED = "skipped", _("Skipped")


OPEN_STATUSES = (Status.PLANNED, Status.POSTPONED)
CLOSED_STATUSES = (Status.DONE, Status.SKIPPED)


def _money(label: Any) -> models.DecimalField[Any, Any]:
    return models.DecimalField(
        label,
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )


class Trade(models.TextChoices):
    """The trades of a contractor: a world-wide list plus Other (docs/adr/0045)."""

    PLUMBER = "plumber", _("Plumber")
    ELECTRICIAN = "electrician", _("Electrician")
    HEATING_ENGINEER = "heating_engineer", _("Heating engineer")
    ROOFER = "roofer", _("Roofer")
    CARPENTER = "carpenter", _("Carpenter")
    PAINTER = "painter", _("Painter")
    MASON = "mason", _("Mason")
    GARDENER = "gardener", _("Gardener")
    LOCKSMITH = "locksmith", _("Locksmith")
    CHIMNEY_SWEEP = "chimney_sweep", _("Chimney sweep")
    OTHER = "other", _("Other")


PHONE_PATTERN = r"^[0-9+()./ -]{3,40}$"
ADDRESS_MAX_LENGTH = 400


class Contractor(HomeOwnedModel):
    """Someone who does work in the home: a trade, a company, a neighbour (docs/adr/0045)."""

    name = models.CharField(_("name"), max_length=120)
    # A code of `Trade`; with Other, what the trade is is said in `trade_other`.
    trade = models.CharField(_("trade"), max_length=20, choices=Trade.choices, blank=True)
    trade_other = models.CharField(
        _("which trade"),
        max_length=120,
        blank=True,
        help_text=_("Only with the trade Other. Example: Pool maintenance."),
    )
    phone = models.CharField(
        _("phone"),
        max_length=40,
        blank=True,
        validators=[RegexValidator(PHONE_PATTERN, _("Digits, spaces and + ( ) - . / only."))],
    )
    email = models.EmailField(_("email address"), blank=True)
    address = models.TextField(
        _("address"), blank=True, validators=[MaxLengthValidator(ADDRESS_MAX_LENGTH)]
    )
    note = notes_field(_("note"))

    class Meta:
        verbose_name = _("contractor")
        verbose_name_plural = _("contractors")
        ordering = ["name", "created_at", "id"]

    def __str__(self) -> str:
        return self.name

    @property
    def trade_label(self) -> str:
        """The trade as people read it: the list's label, or what Other stands for."""
        if self.trade == Trade.OTHER:
            return self.trade_other or str(Trade.OTHER.label)
        return str(Trade(self.trade).label) if self.trade else ""

    def clean(self) -> None:
        super().clean()
        if self.trade == Trade.OTHER and not self.trade_other.strip():
            raise ValidationError({"trade_other": _("Say which trade.")})


class Task(HomeOwnedModel):
    """Something to do, once or again and again. Only the title is required."""

    title = models.CharField(_("title"), max_length=200)
    room = models.ForeignKey(
        Room, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks"
    )
    item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks"
    )
    priority = models.CharField(
        _("priority"), max_length=8, choices=Priority.choices, default=Priority.NORMAL
    )
    estimated_minutes = models.PositiveIntegerField(
        _("estimated duration (minutes)"),
        null=True,
        blank=True,
        validators=[MaxValueValidator(durations.MAX_MINUTES)],
    )
    labour_budget = _money(_("labour budget"))
    material_budget = _money(_("material budget"))
    notes = notes_field(_("notes"))
    # Markdown, rendered safely (docs/adr/0031): HTML escaped, `http(s)` links only.
    procedure = models.TextField(
        _("procedure"), blank=True, validators=[MaxLengthValidator(PROCEDURE_MAX_LENGTH)]
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("assignee"),
    )
    contractor = models.ForeignKey(
        Contractor,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
        verbose_name=_("contractor"),
    )
    repeat_mode = models.CharField(
        _("repeat"), max_length=12, choices=RepeatMode.choices, default=RepeatMode.ONCE
    )
    repeat_unit = models.CharField(
        _("unit"), max_length=8, choices=RepeatUnit.choices, default=RepeatUnit.MONTH
    )
    repeat_interval = models.PositiveIntegerField(
        _("every"), default=1, validators=[MinValueValidator(1)]
    )
    # The season the task is limited to; empty: the whole year. The months below always hold
    # the window: those of the predefined season in the hemisphere of the home (kept up to
    # date when the hemisphere changes), or the person's own for a custom one.
    season = models.CharField(
        _("season"), max_length=10, choices=Season.choices, blank=True, default=""
    )
    season_start_month = models.PositiveSmallIntegerField(
        _("season starts in"), null=True, blank=True
    )
    season_end_month = models.PositiveSmallIntegerField(_("season ends in"), null=True, blank=True)
    hide_days_before = models.PositiveSmallIntegerField(
        _("hide until this many days before"),
        default=0,
        validators=[MaxValueValidator(365)],
        help_text=_("0 shows the task as soon as it exists."),
    )
    # First due date of a fixed series: it carries the day of the month (docs/adr/0031).
    series_start = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("task")
        verbose_name_plural = _("tasks")
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["home", "created_at"], name="task_home_created")]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(season_start_month__isnull=True, season_end_month__isnull=True)
                    | models.Q(season_start_month__isnull=False, season_end_month__isnull=False)
                ),
                name="task_season_both_or_none",
            )
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def rule(self) -> recurrence.Rule:
        return recurrence.Rule(
            mode=self.repeat_mode,
            unit=self.repeat_unit,
            interval=self.repeat_interval,
            season_start=self.season_start_month,
            season_end=self.season_end_month,
        )

    @property
    def repeats(self) -> bool:
        return self.repeat_mode != RepeatMode.ONCE

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        room, item, contractor = self.room, self.item, self.contractor
        if room and self.home_id and room.home_id != self.home_id:
            errors["room"] = _("This room is not in this home.")
        if item and self.home_id and item.home_id != self.home_id:
            errors["item"] = _("This item is not in this home.")
        elif item and room and item.room_id != room.id:
            errors["item"] = _("This item is not in this room.")
        if contractor and self.home_id and contractor.home_id != self.home_id:
            errors["contractor"] = _("This contractor is not in this home.")
        found = recurrence.problems(self.rule)
        if "interval" in found:
            errors["repeat_interval"] = _("This interval is out of range.")
        if "season" in found or (self.season == Season.CUSTOM and self.season_start_month is None):
            errors["season_start_month"] = _("Give the first and the last month of the season.")
        if errors:
            raise ValidationError(errors)


class TaskMaterial(HomeOwnedModel):
    """An article of the stock that a task needs, with the quantity planned (docs/adr/0046).

    The article lives in the stock of the same home; the task only names it and says how
    much. Marking the task done takes that quantity from the stock (`MaterialDraw`).
    """

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="materials")
    article = models.ForeignKey(
        "stock.Article",
        on_delete=models.CASCADE,
        related_name="task_materials",
        verbose_name=_("stock item"),
    )
    quantity = models.DecimalField(
        _("quantity"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    class Meta:
        verbose_name = _("task material")
        verbose_name_plural = _("task materials")
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "article"],
                condition=models.Q(deleted_at__isnull=True),
                name="one_material_line_per_article",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="task_material_quantity_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.article_id} x {self.quantity}"


class Occurrence(HomeOwnedModel):
    """One time a task has to be done: when, and how it ended."""

    # Recomputed from the due date on every side, never synced (mobile ADR 0003, "Derived").
    SYNC_DERIVED = ("visible_from",)

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="occurrences")
    due_on = models.DateField(_("due on"), null=True, blank=True)
    # The day the occurrence starts to show: the due date minus the task's masking delay.
    visible_from = models.DateField(null=True, blank=True, editable=False)
    status = models.CharField(
        _("status"), max_length=10, choices=Status.choices, default=Status.PLANNED
    )
    # Status before the occurrence was closed, to restore it on undo (docs/adr/0033).
    status_before = models.CharField(max_length=10, blank=True, editable=False)
    postponed_from = models.DateField(null=True, blank=True, editable=False)
    completed_on = models.DateField(_("done on"), null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actual_cost = _money(_("actual cost"))
    # What the plan of the task said when it was marked done, kept for its real cost when no
    # amount is typed (docs/adr/0091): the labour planned, and the material budget typed when
    # the stock does not work the materials out (0081). The server writes them, never a
    # phone, which reads them (0092). Empty on an occurrence that is not done.
    planned_labour = models.DecimalField(
        _("labour planned when done"),
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        editable=False,
        validators=[MinValueValidator(0)],
    )
    planned_material = models.DecimalField(
        _("materials planned when done"),
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        editable=False,
        validators=[MinValueValidator(0)],
    )
    completion_note = notes_field(_("note"))
    # The occurrence whose closing created this one.
    follows = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="next_occurrence"
    )

    class Meta:
        verbose_name = _("occurrence")
        verbose_name_plural = _("occurrences")
        ordering = ["due_on", "created_at", "id"]
        indexes = [
            models.Index(fields=["home", "status", "due_on"], name="occ_home_status_due"),
            models.Index(fields=["task", "status"], name="occ_task_status"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["task"],
                condition=models.Q(status__in=["planned", "postponed"], deleted_at__isnull=True),
                name="one_open_occurrence_per_task",
            )
        ]

    def __str__(self) -> str:
        return f"{self.task_id} {self.due_on or '-'} {self.status}"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def is_overdue(self, today: datetime.date) -> bool:
        return self.is_open and self.due_on is not None and self.due_on < today


class MaterialDraw(HomeOwnedModel):
    """What a closed occurrence took from the stock for one article (docs/adr/0046).

    `wanted` is the quantity the occurrence is held to have used (the plan when it was marked
    done, then what was entered when completing it); `drawn` is what the stock could give
    (never more than it held). The ledger lines themselves are in `stock.Movement`, which
    point back to the occurrence. Taking the closing back ends the draw.

    `unit_price` is the unit price of the article when the stock was taken, kept so that a
    price changed later does not change the real cost of what was done (docs/adr/0090); empty
    when the article had none. The server writes it, never a phone, which reads it (0092).
    """

    occurrence = models.ForeignKey(Occurrence, on_delete=models.CASCADE, related_name="draws")
    article = models.ForeignKey("stock.Article", on_delete=models.CASCADE, related_name="+")
    wanted = models.DecimalField(_("quantity used"), max_digits=10, decimal_places=2)
    drawn = models.DecimalField(_("quantity taken from the stock"), max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(
        _("unit price when taken"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        editable=False,
        validators=[MinValueValidator(0)],
    )

    class Meta:
        verbose_name = _("stock draw")
        verbose_name_plural = _("stock draws")
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["occurrence", "article"],
                condition=models.Q(deleted_at__isnull=True),
                name="one_draw_per_article_and_occurrence",
            ),
            models.CheckConstraint(
                condition=models.Q(wanted__gte=0, drawn__gte=0, drawn__lte=models.F("wanted")),
                name="draw_between_zero_and_wanted",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.article_id} {self.drawn}/{self.wanted}"

    @property
    def missing(self) -> Decimal:
        """What the stock could not give."""
        return self.wanted - self.drawn

    @property
    def value(self) -> Decimal:
        """What was taken, at the unit price of when it was taken; nothing without a price.

        Not rounded: the real cost of an occurrence adds its draws, then rounds once
        (`tasks.costs`).
        """
        if self.unit_price is None:
            return Decimal(0)
        return self.drawn * self.unit_price


class TodayGroup(models.TextChoices):
    URGENCY = "urgency", _("Urgency")
    ROOM = "room", _("Room")
    PERSON = "person", _("Person")
    CALENDAR = "calendar", _("Calendar")


class Horizon(models.IntegerChoices):
    WEEK = 7, _("1 week")
    TWO_WEEKS = 14, _("2 weeks")
    MONTH = 30, _("1 month")
    TWO_MONTHS = 60, _("2 months")
    QUARTER = 90, _("3 months")


class DisplayPreference(BaseModel):
    """How a person wants the Today screen laid out (docs/adr/0037).

    It belongs to the person, not to a home: one setting for every home they open.
    A person who never changed anything has no row; the defaults below apply.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="display_preference"
    )
    today_group = models.CharField(
        _("group by"), max_length=10, choices=TodayGroup.choices, default=TodayGroup.URGENCY
    )
    horizon_days = models.PositiveSmallIntegerField(
        _("look ahead"), choices=Horizon.choices, default=Horizon.WEEK
    )
    show_overdue = models.BooleanField(_("overdue"), default=True)
    show_upcoming = models.BooleanField(_("coming up"), default=True)
    show_season = models.BooleanField(_("in season"), default=True)
    # Empty: follow the browser, then the default of the instance (docs/adr/0042).
    language = models.CharField(
        _("language"), max_length=8, blank=True, default="", validators=[validate_language]
    )

    class Meta:
        verbose_name = _("display preference")
        verbose_name_plural = _("display preferences")


def attachment_path(attachment: "Attachment", filename: str) -> str:
    """Storage name: home and attachment identifiers only, never a name chosen by the sender."""
    return f"{attachment.home_id}/{attachment.id}{filename}"


class Attachment(HomeOwnedModel):
    """A photo or a file attached to a task, or to one of its occurrences (docs/adr/0034)."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    occurrence = models.ForeignKey(
        Occurrence, null=True, blank=True, on_delete=models.CASCADE, related_name="attachments"
    )
    # Name shown to people, cleaned of any path and control character; never used on disk.
    name = models.CharField(_("name"), max_length=120)
    file = models.FileField(
        storage=attachment_storage, upload_to=attachment_path, max_length=200, editable=False
    )
    thumbnail = models.FileField(
        storage=attachment_storage,
        upload_to=attachment_path,
        max_length=200,
        editable=False,
        blank=True,
    )
    content_type = models.CharField(max_length=100, editable=False)
    is_image = models.BooleanField(default=False, editable=False)
    size_bytes = models.PositiveIntegerField(editable=False)
    width = models.PositiveIntegerField(null=True, editable=False)
    height = models.PositiveIntegerField(null=True, editable=False)

    class Meta:
        verbose_name = _("attachment")
        verbose_name_plural = _("attachments")
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return self.name
