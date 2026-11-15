"""Business rules of projects, budget lines, quotes, files and envelopes (docs/adr/0002, 0050).

Web views and API views call these functions; neither holds rules of its own.
Reading is limited to the user's homes by the views (`housecairn.homes.scoping`);
writing is checked here, by role: owners and members write, read-only reads.
"""

import datetime
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, SuspiciousOperation, ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db import IntegrityError, models, transaction
from django.utils.translation import gettext as _

from housecairn.accounts.models import User
from housecairn.audit import services as audit
from housecairn.homes import quotas
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.projects import changelog
from housecairn.projects.models import (
    ACTIVE_STATUSES,
    MAX_FILES_PER_QUOTE,
    MAX_PHOTOS_PER_PROJECT,
    STATUS_ORDER,
    ZERO,
    AnnualEnvelope,
    BudgetLine,
    BudgetNature,
    Project,
    ProjectFile,
    ProjectLink,
    ProjectTask,
    Quote,
    QuoteStatus,
)
from housecairn.projects.storage import project_storage
from housecairn.rooms import images
from housecairn.rooms.models import Room
from housecairn.rooms.services import _read_upload
from housecairn.tasks import files as task_files
from housecairn.tasks import recurrence
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Contractor, Season, Task

logger = logging.getLogger(__name__)

AnyUser = User | AnonymousUser

PROJECT_FIELDS = frozenset(
    {
        "title",
        "goal",
        "status",
        "priority",
        "estimated_minutes",
        "season",
        "season_start_month",
        "season_end_month",
    }
)
LINE_FIELDS = frozenset({"label", "nature", "planned", "paid"})
QUOTE_FIELDS = frozenset(
    {"amount", "issued_on", "valid_until", "note", "paperless_id", "paperless_url"}
)
_MONEY_FIELDS = ("planned", "paid", "amount")
_UNSET: Any = object()
_SUPPORTED_MONEY = Decimal("9999999999.99")


def _require_write(actor: AnyUser, home: Home) -> None:
    if not home_services.can_write(actor, home):
        raise PermissionDenied


def _apply(instance: models.Model, allowed: frozenset[str], changes: dict[str, Any]) -> None:
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"Cannot change: {', '.join(sorted(unknown))}")
    for field in _MONEY_FIELDS:
        if field in changes:
            changes[field] = _money(field, changes[field])
    for field, value in changes.items():
        setattr(instance, field, value)


def _money(field: str, value: Any) -> Decimal:
    """An amount as an exact finite `Decimal` of at most two decimals; never a float.

    A float, a NaN, an infinity or a number out of range is refused here, before the
    model's own checks, which a NaN would trip with a comparison error.
    """
    refused = ValidationError({field: _("Give an amount of money, such as 1250.50.")})
    if isinstance(value, bool | float):
        raise refused
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except ArithmeticError:
        raise refused from None
    if not amount.is_finite():
        raise refused
    if amount < 0:
        raise ValidationError({field: _("The amount cannot be negative.")})
    if amount > _SUPPORTED_MONEY:
        raise ValidationError({field: _("This amount is too large.")})
    if amount != amount.quantize(Decimal("0.01")):
        raise ValidationError({field: _("At most two decimals.")})
    return amount.quantize(Decimal("0.01"))


# ---------- reading ----------


def projects_of(home: Home) -> models.QuerySet[Project]:
    return Project.objects.filter(home=home)


def lines_of(home: Home) -> models.QuerySet[BudgetLine]:
    return BudgetLine.objects.filter(home=home, project__deleted_at__isnull=True)


def quotes_of(home: Home) -> models.QuerySet[Quote]:
    return Quote.objects.filter(home=home, project__deleted_at__isnull=True).select_related(
        "contractor", "budget_line"
    )


def files_of(home: Home) -> models.QuerySet[ProjectFile]:
    """Every stored file of the projects of the home, the pictures of links included."""
    return ProjectFile.objects.filter(home=home, project__deleted_at__isnull=True)


def photos_of(home: Home) -> models.QuerySet[ProjectFile]:
    """The photos of projects and the files of quotes: not the picture of a link."""
    return files_of(home).filter(link__isnull=True)


def inspiration_links_of(home: Home) -> models.QuerySet[ProjectLink]:
    return ProjectLink.objects.filter(home=home, project__deleted_at__isnull=True).select_related(
        "image"
    )


def links_of(home: Home) -> models.QuerySet[ProjectTask]:
    """Links to tasks that exist: a deleted task, or project, is out of sight."""
    return ProjectTask.objects.filter(
        home=home, task__deleted_at__isnull=True, project__deleted_at__isnull=True
    ).select_related("task", "project")


def envelopes_of(home: Home) -> models.QuerySet[AnnualEnvelope]:
    return AnnualEnvelope.objects.filter(home=home)


def search_projects(
    home: Home,
    *,
    query: str = "",
    status: str = "",
    room: UUID | None = None,
    priority: str = "",
    season: str = "",
) -> models.QuerySet[Project]:
    """Projects of the home, the ones going on first, filtered; never a kanban of columns."""
    rank = models.Case(
        *[models.When(status=s, then=i) for i, s in enumerate(STATUS_ORDER)],
        output_field=models.IntegerField(),
    )
    urgency = models.Case(
        models.When(priority="high", then=0),
        models.When(priority="normal", then=1),
        default=2,
        output_field=models.IntegerField(),
    )
    rows = projects_of(home).annotate(rank=rank, urgency=urgency)
    query = query.strip()
    if query:
        rows = rows.filter(models.Q(title__icontains=query) | models.Q(goal__icontains=query))
    if status:
        rows = rows.filter(status=status)
    if room is not None:
        rows = rows.filter(rooms=room)
    if priority:
        rows = rows.filter(priority=priority)
    if season:
        rows = rows.filter(season=season)
    return rows.order_by("rank", "urgency", "created_at", "id")


# ---------- amounts ----------


@dataclass(frozen=True)
class Amounts:
    """The three amounts of a line or a project, and what they say together.

    `committed` is the sum of the accepted quotes. A line is spent for the more of what is
    committed and what is paid, and its gap is the planned amount less that. The gap of a
    project is the sum of the gaps of its lines (`engaged` adds up line by line, never from
    the totals): an overrun on one line is not hidden by the room left on another.
    Negative: over budget. What is still to pay follows the same rule, line by line.
    """

    planned: Decimal = ZERO
    committed: Decimal = ZERO
    paid: Decimal = ZERO
    # Sums over the lines of max(committed, paid) and of max(committed - paid, 0); worked out
    # for one line when left empty.
    engaged_sum: Decimal | None = dataclass_field(default=None, repr=False)
    to_pay_sum: Decimal | None = dataclass_field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.engaged_sum is None:
            object.__setattr__(self, "engaged_sum", max(self.committed, self.paid))
        if self.to_pay_sum is None:
            object.__setattr__(self, "to_pay_sum", max(self.committed - self.paid, ZERO))

    @property
    def engaged(self) -> Decimal:
        return ZERO if self.engaged_sum is None else self.engaged_sum

    def __add__(self, other: "Amounts") -> "Amounts":
        return Amounts(
            self.planned + other.planned,
            self.committed + other.committed,
            self.paid + other.paid,
            self.engaged + other.engaged,
            self.to_pay + other.to_pay,
        )

    @property
    def gap(self) -> Decimal:
        return self.planned - self.engaged

    @property
    def state(self) -> str:
        """`over`, `under` or `on`: said in words on the screens, never by colour alone."""
        return "over" if self.gap < 0 else "under" if self.gap > 0 else "on"

    @property
    def over_by(self) -> Decimal:
        return -self.gap

    @property
    def to_pay(self) -> Decimal:
        """Committed and not paid yet, line by line: a line paid above its quotes owes nothing,
        and does not lower what another line still owes."""
        return ZERO if self.to_pay_sum is None else self.to_pay_sum


@dataclass(frozen=True)
class LineSummary:
    line: BudgetLine
    amounts: Amounts
    quotes: list[Quote]


def line_summaries(project: Project) -> list[LineSummary]:
    """The lines of a project with their three amounts."""
    lines = list(BudgetLine.objects.filter(project=project))
    accepted = list(
        Quote.objects.filter(project=project, status=QuoteStatus.ACCEPTED).select_related(
            "contractor"
        )
    )
    return [_summary(line, [q for q in accepted if q.budget_line_id == line.id]) for line in lines]


def _summary(line: BudgetLine, quotes: list[Quote]) -> LineSummary:
    committed = sum((q.amount for q in quotes), ZERO)
    return LineSummary(line, Amounts(line.planned, committed, line.paid), quotes)


def amounts_by_project(projects: Iterable[Project]) -> dict[UUID, Amounts]:
    """The total of each project, in two queries; sums are made in `Decimal`, not in SQL.

    The total is the sum of the amounts of the lines, each line spent for the more of its
    commitment and its payments, so that the gap of the project is the sum of the gaps.
    """
    ids = [p.id for p in projects]
    totals: dict[UUID, Amounts] = {pk: Amounts() for pk in ids}
    lines = {line.id: line for line in BudgetLine.objects.filter(project__in=ids)}
    committed = dict.fromkeys(lines, ZERO)
    for quote in Quote.objects.filter(
        project__in=ids, status=QuoteStatus.ACCEPTED, budget_line__in=list(lines)
    ):
        if quote.budget_line_id is not None:
            committed[quote.budget_line_id] += quote.amount
    for line in lines.values():
        totals[line.project_id] += Amounts(line.planned, committed[line.id], line.paid)
    return totals


def committed_by_line(lines: Iterable[BudgetLine]) -> dict[UUID, Decimal]:
    """What the accepted quotes commit on each of these lines."""
    committed = {line.id: ZERO for line in lines}
    for quote in Quote.objects.filter(budget_line__in=list(committed), status=QuoteStatus.ACCEPTED):
        if quote.budget_line_id is not None:
            committed[quote.budget_line_id] += quote.amount
    return committed


def amounts_of(project: Project) -> Amounts:
    return amounts_by_project([project])[project.id]


@dataclass(frozen=True)
class EnvelopeSummary:
    year: int
    envelope: Decimal | None
    planned: Decimal  # planned budget of the projects not finished nor abandoned
    paid: Decimal

    @property
    def left(self) -> Decimal | None:
        return None if self.envelope is None else self.envelope - self.planned


def envelope_summary(home: Home, year: int) -> EnvelopeSummary:
    """The envelope of a year and what the projects going on plan to spend (docs/adr/0050)."""
    envelope = envelopes_of(home).filter(year=year).first()
    active = list(projects_of(home).filter(status__in=ACTIVE_STATUSES))
    totals = amounts_by_project(active)
    planned = sum((t.planned for t in totals.values()), ZERO)
    paid = sum((t.paid for t in totals.values()), ZERO)
    return EnvelopeSummary(year, envelope.amount if envelope else None, planned, paid)


@transaction.atomic
def set_envelope(actor: AnyUser, home: Home, *, year: int, amount: Any) -> AnnualEnvelope:
    _require_write(actor, home)
    envelope = envelopes_of(home).filter(year=year).first() or AnnualEnvelope(home=home, year=year)
    before = changelog.stored(envelope)
    envelope.amount = _money("amount", amount)
    envelope.full_clean()
    envelope.save()
    changelog.saved(envelope, before)
    audit.record_object("envelope.set", actor=actor, home_id=home.id, title=str(year))
    return envelope


# ---------- projects ----------


def _check_rooms(home: Home, rooms: Iterable[Room]) -> list[Room]:
    chosen = list(rooms)
    if any(room.home_id != home.id for room in chosen):
        raise ValidationError({"rooms": _("This room is not in this home.")})
    return chosen


def _clean_season(project: Project) -> None:
    """A predefined season has no months of its own; no season has none either.

    The heating season of a client of before becomes its months, as a custom season.
    """
    legacy = recurrence.legacy_season(project.season, project.home.hemisphere)
    if legacy is not None:
        project.season = Season.CUSTOM
        project.season_start_month, project.season_end_month = legacy
    elif project.season != Season.CUSTOM:
        project.season_start_month = project.season_end_month = None


@transaction.atomic
def create_project(
    actor: AnyUser,
    home: Home,
    *,
    rooms: Iterable[Room] = (),
    budget: Any = None,
    **fields: Any,
) -> Project:
    """Create a project; `budget`, if given, becomes its first budget line."""
    _require_write(actor, home)
    project = Project(home=home)
    _apply(project, PROJECT_FIELDS, fields)
    _clean_season(project)
    chosen = _check_rooms(home, rooms)
    project.full_clean()
    project.save()
    changelog.saved(project)
    project.rooms.set(chosen)
    changelog.links_changed(project, "rooms", None)
    audit.record_object("project.created", actor=actor, home_id=home.id, title=project.title)
    if budget is not None:
        create_line(actor, project, label=_("Budget"), nature=BudgetNature.OTHER, planned=budget)
    return project


@transaction.atomic
def update_project(
    actor: AnyUser, project: Project, *, rooms: Iterable[Room] | None = None, **changes: Any
) -> Project:
    _require_write(actor, project.home)
    before = changelog.stored(project)
    _apply(project, PROJECT_FIELDS, changes)
    _clean_season(project)
    chosen = None if rooms is None else _check_rooms(project.home, rooms)
    project.full_clean()
    project.save()
    changelog.saved(project, before)
    if chosen is not None:
        linked = changelog.links_of(project, "rooms")
        project.rooms.set(chosen)
        changelog.links_changed(project, "rooms", linked)
    audit.record_object(
        "project.updated",
        actor=actor,
        home_id=project.home_id,
        title=project.title,
        fields=sorted([*changes, *([] if rooms is None else ["rooms"])]),
    )
    return project


@transaction.atomic
def delete_project(actor: AnyUser, project: Project) -> None:
    """Soft-delete the project with its lines, quotes and files; its tasks stay, unlinked."""
    _require_write(actor, project.home)
    doomed = list(ProjectFile.objects.filter(project=project))
    # The links to tasks go for good, each leaving its tombstone (docs/adr/0077, need 4).
    changelog.hard_delete(ProjectTask.objects.filter(project=project))
    changelog.soft_delete(ProjectLink.objects.filter(project=project))
    changelog.soft_delete(ProjectFile.objects.filter(project=project))
    changelog.soft_delete(Quote.objects.filter(project=project))
    changelog.soft_delete(BudgetLine.objects.filter(project=project))
    project.soft_delete()
    changelog.deleted(project)
    _erase_files(doomed)
    audit.record_object(
        "project.deleted", actor=actor, home_id=project.home_id, title=project.title
    )


# ---------- tasks ----------


def tasks_of(project: Project) -> list[ProjectTask]:
    """The links of the project, each with its task (`select_related`), oldest first."""
    return list(
        ProjectTask.objects.filter(project=project, task__deleted_at__isnull=True).select_related(
            "task", "task__room"
        )
    )


def project_of_task(task: Task) -> Project | None:
    link = (
        ProjectTask.objects.filter(
            task=task, task__deleted_at__isnull=True, project__deleted_at__isnull=True
        )
        .select_related("project")
        .first()
    )
    return link.project if link else None


def attachable_tasks(home: Home) -> models.QuerySet[Task]:
    """Tasks of the home that are in no project yet."""
    return task_services.tasks_of(home).filter(project_link__isnull=True).order_by("title", "id")


@transaction.atomic
def add_task(
    actor: AnyUser, project: Project, *, due_on: datetime.date | None = None, **fields: Any
) -> Task:
    """Create a task of the project: `tasks.services.create_task`, then the link."""
    _require_write(actor, project.home)
    task = task_services.create_task(actor, project.home, due_on=due_on, **fields)
    changelog.saved(ProjectTask.objects.create(home=project.home, project=project, task=task))
    audit.record_object(
        "project.task_added", actor=actor, home_id=project.home_id, title=project.title
    )
    return task


def _task_is_taken(task: Task) -> bool:
    return ProjectTask.all_objects.filter(task=task).exists()


@transaction.atomic
def attach_task(actor: AnyUser, project: Project, task: Task) -> ProjectTask:
    """Put an existing task into the project; a task is in one project at most."""
    _require_write(actor, project.home)
    if task.home_id != project.home_id or task.deleted_at is not None:
        raise ValidationError({"task": _("This task is not in this home.")})
    if _task_is_taken(task):
        raise ValidationError({"task": _("This task is already in a project.")})
    try:
        # A savepoint: two people putting the same task in at once meet the unique constraint of
        # the database, which is answered like the check above, not with a server error.
        with transaction.atomic():
            link: ProjectTask = ProjectTask.objects.create(
                home=project.home, project=project, task=task
            )
    except IntegrityError:
        raise ValidationError({"task": _("This task is already in a project.")}) from None
    changelog.saved(link)
    audit.record_object(
        "project.task_added", actor=actor, home_id=project.home_id, title=project.title
    )
    return link


@transaction.atomic
def detach_task(actor: AnyUser, link: ProjectTask) -> None:
    """Take the task out of the project; the task itself stays.

    The link goes for good, so that the task may join another project, and leaves its
    tombstone in the change log (docs/adr/0077, need 4).
    """
    _require_write(actor, link.home)
    changelog.deleted(link)
    link.delete()
    audit.record_object(
        "project.task_removed", actor=actor, home_id=link.home_id, title=link.project.title
    )


# ---------- budget lines ----------


@transaction.atomic
def create_line(actor: AnyUser, project: Project, **fields: Any) -> BudgetLine:
    _require_write(actor, project.home)
    line = BudgetLine(home=project.home, project=project)
    _apply(line, LINE_FIELDS, fields)
    line.full_clean()
    line.save()
    changelog.saved(line)
    audit.record_object(
        "budget_line.created", actor=actor, home_id=project.home_id, title=project.title
    )
    return line


@transaction.atomic
def update_line(actor: AnyUser, line: BudgetLine, **changes: Any) -> BudgetLine:
    _require_write(actor, line.home)
    before = changelog.stored(line)
    _apply(line, LINE_FIELDS, changes)
    line.full_clean()
    line.save()
    changelog.saved(line, before)
    audit.record_object(
        "budget_line.updated",
        actor=actor,
        home_id=line.home_id,
        title=line.project.title,
        fields=sorted(changes),
    )
    return line


@transaction.atomic
def delete_line(actor: AnyUser, line: BudgetLine) -> None:
    """Soft-delete the line; its accepted quotes go back to Received, committed on nothing."""
    _require_write(actor, line.home)
    changelog.update(
        Quote.objects.filter(budget_line=line, status=QuoteStatus.ACCEPTED),
        status=QuoteStatus.RECEIVED,
    )
    changelog.update(Quote.objects.filter(budget_line=line), budget_line=None)
    line.soft_delete()
    changelog.deleted(line)
    audit.record_object(
        "budget_line.deleted", actor=actor, home_id=line.home_id, title=line.project.title
    )


# ---------- quotes ----------


def contractor_named(actor: AnyUser, home: Home, name: str) -> Contractor | None:
    """The single "choose or create" field of a quote, as on a task (docs/adr/0045)."""
    return task_services.contractor_named(actor, home, name)


def _check_quote_dates(quote: Quote) -> None:
    low, high = task_services.date_bounds()
    for field in ("issued_on", "valid_until"):
        value = getattr(quote, field)
        if value is not None and not low <= value <= high:
            raise ValidationError(
                {
                    field: _("Choose a date within %(years)d years of today.")
                    % {"years": task_services.DATE_SPAN_YEARS}
                }
            )


def _save_quote(quote: Quote, before: changelog.Stored | None = None) -> None:
    # Only an accepted quote is committed on a line.
    if quote.status != QuoteStatus.ACCEPTED:
        quote.budget_line = None
    _check_quote_dates(quote)
    quote.full_clean()
    quote.save()
    changelog.saved(quote, before)


@transaction.atomic
def create_quote(
    actor: AnyUser,
    project: Project,
    *,
    contractor: Contractor | None = None,
    status: str = QuoteStatus.REQUESTED,
    budget_line: BudgetLine | None = None,
    **fields: Any,
) -> Quote:
    _require_write(actor, project.home)
    quote = Quote(
        home=project.home,
        project=project,
        contractor=contractor,
        status=status,
        budget_line=budget_line,
    )
    _apply(quote, QUOTE_FIELDS, fields)
    _save_quote(quote)
    audit.record_object("quote.created", actor=actor, home_id=project.home_id, title=project.title)
    return quote


@transaction.atomic
def update_quote(
    actor: AnyUser,
    quote: Quote,
    *,
    contractor: Contractor | None = _UNSET,
    status: str = _UNSET,
    budget_line: BudgetLine | None = _UNSET,
    **changes: Any,
) -> Quote:
    """Change a quote. Leaving Accepted releases the line; entering it needs one."""
    _require_write(actor, quote.home)
    before = changelog.stored(quote)
    _apply(quote, QUOTE_FIELDS, changes)
    if contractor is not _UNSET:
        quote.contractor = contractor
    if status is not _UNSET:
        quote.status = status
    if budget_line is not _UNSET:
        quote.budget_line = budget_line
    _save_quote(quote, before)
    audit.record_object(
        "quote.updated",
        actor=actor,
        home_id=quote.home_id,
        title=quote.project.title,
        fields=sorted(
            [
                *changes,
                *([] if contractor is _UNSET else ["contractor"]),
                *([] if status is _UNSET else ["status"]),
                *([] if budget_line is _UNSET else ["budget_line"]),
            ]
        ),
    )
    return quote


@transaction.atomic
def accept_quote(actor: AnyUser, quote: Quote, line: BudgetLine) -> Quote:
    """Accept the quote: its amount is committed on the chosen line of its project."""
    _require_write(actor, quote.home)
    if line.project_id != quote.project_id or line.deleted_at is not None:
        raise ValidationError({"budget_line": _("This budget line is not in this project.")})
    before = changelog.stored(quote)
    quote.status = QuoteStatus.ACCEPTED
    quote.budget_line = line
    _save_quote(quote, before)
    audit.record_object(
        "quote.accepted", actor=actor, home_id=quote.home_id, title=quote.project.title
    )
    return quote


@transaction.atomic
def delete_quote(actor: AnyUser, quote: Quote) -> None:
    _require_write(actor, quote.home)
    doomed = list(ProjectFile.objects.filter(quote=quote))
    changelog.soft_delete(ProjectFile.objects.filter(quote=quote))
    quote.soft_delete()
    changelog.deleted(quote)
    _erase_files(doomed)
    audit.record_object(
        "quote.deleted", actor=actor, home_id=quote.home_id, title=quote.project.title
    )


# ---------- files ----------


def _looks_like_image(data: bytes) -> bool:
    return data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")) or (
        data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    )


def save_stored(
    stored: ProjectFile, content: bytes, thumbnail: bytes | None, extension: str
) -> None:
    """Write the file and its thumbnail under the quotas of the home, then the record.

    Called inside the transaction of the service; a failure leaves no file behind.
    """
    stored.full_clean(exclude=["file", "thumbnail"])
    with quotas.room_for(stored.home, size=len(content) + len(thumbnail or b"")):
        stored.file.save(extension, ContentFile(content), save=False)
        if thumbnail is not None:
            stored.thumbnail.save("-thumb.jpg", ContentFile(thumbnail), save=False)
        try:
            stored.save()
        except Exception:
            stored.file.delete(save=False)
            stored.thumbnail.delete(save=False)
            raise
    changelog.saved(stored)


@transaction.atomic
def add_file(
    actor: AnyUser,
    project: Project,
    *,
    upload: UploadedFile,
    quote: Quote | None = None,
    name: str = "",
) -> ProjectFile:
    """Add a photo to the project, or a photo or document to one of its quotes.

    The content decides the type, as for the attachments of tasks (docs/adr/0034): an
    image is decoded and written again without its metadata, a document of a quote must
    be on the allow-list. A photo of the project is an image.
    """
    _require_write(actor, project.home)
    if quote is not None and quote.project_id != project.id:
        raise ValidationError({"quote": _("This quote is not in this project.")})
    held = ProjectFile.objects.filter(project=project)
    if quote is None:
        if held.filter(quote__isnull=True, link__isnull=True).count() >= MAX_PHOTOS_PER_PROJECT:
            raise ValidationError(
                _("At most %(max)d photos for a project."), params={"max": MAX_PHOTOS_PER_PROJECT}
            )
    elif held.filter(quote=quote).count() >= MAX_FILES_PER_QUOTE:
        raise ValidationError(
            _("At most %(max)d files for a quote."), params={"max": MAX_FILES_PER_QUOTE}
        )
    data = _read_upload(upload)
    display = task_files.clean_name(name or upload.name)
    try:
        processed = images.process(data)
    except ValidationError as image_error:
        if quote is None or _looks_like_image(data):
            raise image_error from None
        kind = task_files.detect(data)
        stored = ProjectFile(
            home=project.home,
            project=project,
            quote=quote,
            name=display,
            content_type=kind.content_type,
            is_image=False,
            size_bytes=len(data),
        )
        content, thumbnail, extension = data, None, kind.extension
    else:
        stored = ProjectFile(
            home=project.home,
            project=project,
            quote=quote,
            name=display,
            content_type=processed.content_type,
            is_image=True,
            size_bytes=len(processed.data),
            width=processed.width,
            height=processed.height,
        )
        content, thumbnail, extension = processed.data, processed.thumbnail, processed.extension
    save_stored(stored, content, thumbnail, extension)
    audit.record_object(
        "project_file.added", actor=actor, home_id=project.home_id, title=project.title
    )
    return stored


@transaction.atomic
def delete_file(actor: AnyUser, stored: ProjectFile) -> None:
    """Soft-delete the record and erase its files, once the deletion is committed."""
    _require_write(actor, stored.home)
    stored.soft_delete()
    changelog.deleted(stored)
    _erase_files([stored])
    audit.record_object(
        "project_file.deleted", actor=actor, home_id=stored.home_id, title=stored.project.title
    )


def _erase_files(stored_files: list[ProjectFile]) -> None:
    """Remove the file and thumbnail of each record from the disk (docs/adr/0036).

    Done once the deletion of the records is committed. A failure is logged, never
    raised: the records are gone either way and nothing could retry.
    """
    names = [name for f in stored_files for name in (f.file.name, f.thumbnail.name) if name]

    def erase() -> None:
        for name in names:
            try:
                project_storage.delete(name)
            except (OSError, ValueError, SuspiciousOperation):
                logger.error("Could not erase the file of a deleted project: %s", name)

    transaction.on_commit(erase)


# ---------- comparison ----------


@dataclass(frozen=True)
class Comparison:
    project: Project
    amounts: Amounts


def compare(home: Home, ids: Iterable[UUID]) -> list[Comparison]:
    """The chosen projects of the home side by side, in the order of the list."""
    chosen = list(search_projects(home).filter(id__in=list(ids)))
    totals = amounts_by_project(chosen)
    return [Comparison(project, totals[project.id]) for project in chosen]
