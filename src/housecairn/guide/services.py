"""The example home of the interactive guide (docs/adr/0084).

A disposable home with fictional values, made by the person in scenario 1, filled scenario
after scenario, and deleted in one gesture. It is a home like another for every service
(rooms, tasks, reports, projects go through their ordinary services), with one mark,
`Home.is_example`, that keeps it on the instance: no change recorded, no export, no backup, no
list of homes. This module is the one place that sets the mark.
"""

import datetime
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from housecairn.accounts.models import User
from housecairn.audit import models as audit_models
from housecairn.core.language import language_of
from housecairn.guide import content, states
from housecairn.guide.models import StepTick
from housecairn.homes import files
from housecairn.homes import services as home_services
from housecairn.homes.models import Hemisphere, Home, Membership, Role
from housecairn.projects import services as project_services
from housecairn.projects.models import AnnualEnvelope, BudgetLine, Project, Quote
from housecairn.reports import services as report_services
from housecairn.reports.models import CLOSED_STATUSES, Report, ReportStatus
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Item, Room
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Contractor, Occurrence, Status, Task


def example_home_of(user: User) -> Home | None:
    """The example home of the account, if it has one: there is at most one.

    Only its owner has it: a member that some other path put there would see no banner, no
    guide, no tick and no filling (defence in depth: nothing adds a member to an example home).
    """
    owned = Membership.objects.filter(user=user, role=Role.OWNER).values("home_id")
    return (
        home_services.homes_for(user, include_example=True)
        .filter(is_example=True, id__in=owned)
        .first()
    )


def _language(actor: User, language: str | None) -> str:
    return content.language_in_file(language or language_of(actor))


@transaction.atomic
def create_example_home(
    owner: User,
    *,
    language: str | None = None,
    name: str | None = None,
    currency: str | None = None,
    hemisphere: str | None = None,
) -> Home:
    """Make the example home of `owner`, empty: the person fills it scenario after scenario.

    The name, the currency and the hemisphere are those of the form of step 1 of the guide,
    pre-filled with the values of the file: what is left out reads from the file, in the
    language of the person (docs/adr/0074). One example home per account: a second is refused
    until the first is deleted.
    """
    if example_home_of(owner) is not None:
        raise ValidationError(_("You already have an example home. Delete it to make another."))
    home = content.example_home()
    chosen = _language(owner, language)
    return home_services.create_example_home(
        owner,
        name=name or content.text(home["name"], chosen),
        currency=currency or home["currency"],
        hemisphere=hemisphere or home.get("hemisphere", Hemisphere.NORTH),
    )


@transaction.atomic
def remove_example(actor: User, home: Home) -> None:
    """Delete the example home in one gesture: no typed name, no offer to export.

    The rows, the files and the ticks go with it, and so do the entries of its audit log (their
    `home_id` is no foreign key, so the cascade does not reach them). It writes no row of erased
    home and no audit entry of its own: the home never reached a sync, so no connected app has
    to be told that it is gone.
    """
    if not home.is_example:
        raise ValueError("Only the example home is removed this way.")
    if not home_services.is_owner(actor, home):
        raise PermissionDenied
    home_id = home.pk
    Home.all_objects.filter(pk=home_id).delete()
    audit_models.AuditEvent.objects.filter(home_id=home_id).delete()
    transaction.on_commit(lambda: files.erase_home_files(home_id))


def tick_by_hand(actor: User, home: Home, step_id: str) -> bool:
    """Tick a step that has no state of its own (`manual`), on the example home of `actor`.

    Only the steps of the file marked `manual` are ticked this way: the others tick by their
    state. Returns whether a tick was made (False when it was there already).
    """
    if not home.is_example:
        raise ValueError("Only the example home has ticks.")
    if not home_services.can_write(actor, home):
        raise PermissionDenied
    manual = {step["id"] for step in content.steps() if step.get("manual")}
    if step_id not in manual:
        raise ValueError(f"{step_id!r} is not a step ticked by hand.")
    _tick, made = StepTick.objects.get_or_create(home=home, step_id=step_id)
    return made


# ---------- catching up ----------


class _Maker:
    """Adds the rows of the file that the home lacks, through the ordinary services."""

    def __init__(self, actor: User, home: Home, language: str) -> None:
        self.actor = actor
        self.home = home
        self.language = language
        self.today = task_services.today()
        self.made: dict[str, Any] = {}

    def value(self, name: str, raw: Any) -> Any:
        """A field of the file as the services take it: text in the language, a row for a key."""
        if isinstance(raw, list):
            return [self.made[key] for key in raw]
        if isinstance(raw, str) and name not in {"kind", "status", "nature", "trade"}:
            return self.made.get(raw, raw)
        return content.text(raw, self.language)

    def fields(self, row: dict[str, Any]) -> dict[str, Any]:
        return {name: self.value(name, raw) for name, raw in row["fields"].items()}

    def offset(self, fields: dict[str, Any], name: str) -> datetime.date | None:
        days = fields.pop(name, None)
        return None if days is None else self.today + datetime.timedelta(days=days)

    def add(self, row: dict[str, Any]) -> None:
        entity = row["entity"]
        fields = self.fields(row)
        make = getattr(self, "_" + entity.replace(".", "_"))
        self.made[row["key"]] = make(fields)

    # One method per entity of the file: find the row if it is there, else make it.

    def _rooms_room(self, fields: dict[str, Any]) -> Room:
        found: Room | None = Room.objects.filter(home=self.home, name=fields["name"]).first()
        return found or room_services.create_room(self.actor, self.home, **fields)

    def _rooms_item(self, fields: dict[str, Any]) -> Item:
        room = fields.pop("room")
        found: Item | None = Item.objects.filter(
            home=self.home, room=room, name=fields["name"]
        ).first()
        return found or room_services.create_item(self.actor, room, **fields)

    def _tasks_task(self, fields: dict[str, Any]) -> Task:
        found: Task | None = Task.objects.filter(home=self.home, title=fields["title"]).first()
        if found is not None:
            return found
        due_on = self.offset(fields, "due_offset_days")
        return task_services.create_task(self.actor, self.home, due_on=due_on, **fields)

    def _tasks_occurrence(self, fields: dict[str, Any]) -> Occurrence | None:
        task: Task = fields["task"]
        done: Occurrence | None = Occurrence.objects.filter(
            home=self.home, task=task, status=Status.DONE
        ).first()
        if done is not None:
            return done
        open_one = task_services.open_occurrence_of(task)
        if open_one is None:
            return None
        on = self.offset(fields, "completed_offset_days")
        return task_services.complete(self.actor, open_one, on=on).occurrence

    def _reports_report(self, fields: dict[str, Any]) -> Report:
        found: Report | None = Report.objects.filter(home=self.home, title=fields["title"]).first()
        if found is not None:
            return found
        status = fields.pop("status", ReportStatus.OPEN)
        report = report_services.create_report(self.actor, self.home, **fields)
        if status in CLOSED_STATUSES:
            report = report_services.close_report(self.actor, report, resolution=status)
        return report

    def _projects_project(self, fields: dict[str, Any]) -> Project:
        found: Project | None = Project.objects.filter(
            home=self.home, title=fields["title"]
        ).first()
        if found is not None:
            return found
        return project_services.create_project(self.actor, self.home, priority="normal", **fields)

    def _projects_budgetline(self, fields: dict[str, Any]) -> BudgetLine:
        project: Project = fields.pop("project")
        found: BudgetLine | None = BudgetLine.objects.filter(
            home=self.home, project=project, label=fields["label"]
        ).first()
        if found is not None:
            return found
        amounts = {
            name: Decimal(fields.pop(name)) for name in ("planned", "paid") if name in fields
        }
        return project_services.create_line(self.actor, project, **fields, **amounts)

    def _tasks_contractor(self, fields: dict[str, Any]) -> Contractor:
        found: Contractor | None = Contractor.objects.filter(
            home=self.home, name=fields["name"]
        ).first()
        return found or task_services.create_contractor(self.actor, self.home, **fields)

    def _projects_quote(self, fields: dict[str, Any]) -> Quote:
        project: Project = fields.pop("project")
        contractor: Contractor = fields.pop("contractor")
        found: Quote | None = Quote.objects.filter(
            home=self.home, project=project, contractor=contractor
        ).first()
        if found is not None:
            return found
        issued = self.offset(fields, "issued_offset_days")
        valid_days = fields.pop("valid_days", None)
        if issued is not None:
            fields["issued_on"] = issued
            if valid_days is not None:
                fields["valid_until"] = issued + datetime.timedelta(days=valid_days)
        fields["amount"] = Decimal(fields["amount"])
        return project_services.create_quote(self.actor, project, contractor=contractor, **fields)

    def _projects_annualenvelope(self, fields: dict[str, Any]) -> AnnualEnvelope:
        year = self.today.year if fields["year"] == "current" else int(fields["year"])
        found: AnnualEnvelope | None = AnnualEnvelope.objects.filter(
            home=self.home, year=year
        ).first()
        return found or project_services.set_envelope(
            self.actor, self.home, year=year, amount=Decimal(fields["amount"])
        )


@transaction.atomic
def fill_example(actor: User, home: Home, *, up_to: int, language: str | None = None) -> list[str]:
    """Add what the scenarios before number `up_to` make and the home lacks.

    "Add what the earlier scenarios make", for a person who opens scenario 3 without having done
    scenario 2. It adds the rows of the file whose `stage` is below `up_to` that are missing,
    through the ordinary services, in the language of the person: never a row of stage `up_to`
    or later, so that it ticks nothing ahead. Returns the steps ticked by what it made.
    """
    if not home.is_example:
        raise ValueError("Only the example home is filled in.")
    if not home_services.can_write(actor, home):
        raise PermissionDenied
    maker = _Maker(actor, home, _language(actor, language))
    for row in content.example_rows():
        if row["stage"] < up_to:
            maker.add(row)
    return states.tick(home)
