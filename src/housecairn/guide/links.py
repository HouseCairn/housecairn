"""Where a step of the guide leads: the real screen, and its form filled in (docs/adr/0084).

A step names a screen of `docs/screens/` and, for a form, a row of the example to fill it
with (`suggest`, limited to `suggest_fields`). Its link opens the screen in the example home,
with `next` back to the page of the scenario, so that the form comes back to it once saved
(design guide, \"After a form\"). When the row the step names is already in the home, the
form is that of the row (its brand, its repetition, its payment); else the form that adds it.
The values go in the query, and the form checks them as input (`guide.prefill`).
"""

import datetime
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.db import models
from django.urls import reverse

from housecairn.guide import content
from housecairn.homes.models import Home
from housecairn.projects.models import AnnualEnvelope, BudgetLine, Project, Quote
from housecairn.reports.models import Report
from housecairn.rooms.models import Item, Room
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Contractor, Task

# The field of a row that says it was made from the guide's form of a new home.
GUIDE_FLAG = "guide"


@dataclass(frozen=True)
class Links:
    """The address of the screen of a step, and the same filled in (or None)."""

    open: str | None
    fill: str | None


def _rows() -> dict[str, dict[str, Any]]:
    return {row["key"]: row for row in content.example_rows()}


class _Finder:
    """The rows of the example home that match rows of the file, found as `fill_example` does."""

    def __init__(self, home: Home, language: str) -> None:
        self.home = home
        self.language = language
        self.rows = _rows()
        self.found: dict[str, models.Model | None] = {}

    def text(self, row: dict[str, Any], name: str) -> Any:
        return content.text(row["fields"][name], self.language)

    def get(self, key: str | None) -> Any:
        if key is None or key not in self.rows:
            return None
        if key not in self.found:
            self.found[key] = self._find(self.rows[key])
        return self.found[key]

    def _find(self, row: dict[str, Any]) -> models.Model | None:
        entity, fields, home = row["entity"], row["fields"], self.home
        if entity == "rooms.room":
            return Room.objects.filter(home=home, name=self.text(row, "name")).first()
        if entity == "rooms.item":
            room = self.get(fields.get("room"))
            return Item.objects.filter(home=home, room=room, name=self.text(row, "name")).first()
        if entity == "tasks.task":
            return Task.objects.filter(home=home, title=self.text(row, "title")).first()
        if entity == "reports.report":
            return Report.objects.filter(home=home, title=self.text(row, "title")).first()
        if entity == "projects.project":
            return Project.objects.filter(home=home, title=self.text(row, "title")).first()
        if entity == "projects.budgetline":
            project = self.get(fields.get("project"))
            return BudgetLine.objects.filter(
                home=home, project=project, label=self.text(row, "label")
            ).first()
        if entity == "tasks.contractor":
            return Contractor.objects.filter(home=home, name=self.text(row, "name")).first()
        if entity == "projects.quote":
            project = self.get(fields.get("project"))
            contractor = self.get(fields.get("contractor"))
            return Quote.objects.filter(home=home, project=project, contractor=contractor).first()
        if entity == "projects.annualenvelope":
            return AnnualEnvelope.objects.filter(home=home, year=task_services.today().year).first()
        return None  # pragma: no cover - the file test lists the entities

    def pk(self, key: str | None) -> str | None:
        found = self.get(key)
        return str(found.pk) if found is not None else None


def _day(offset: int) -> str:
    return (task_services.today() + datetime.timedelta(days=offset)).isoformat()


def _query(finder: _Finder, row: dict[str, Any]) -> dict[str, list[tuple[str, Any]]]:
    """For each field of the row, the pairs of the query that fill it in its form."""
    fields = row["fields"]
    pairs: dict[str, list[tuple[str, Any]]] = {}
    for name, raw in fields.items():
        value = content.text(raw, finder.language)
        if name in {"room", "item", "budget_line"}:
            pk = finder.pk(raw)
            pairs[name] = [(name, pk)] if pk else []
        elif name == "rooms":
            pairs[name] = [("rooms", finder.pk(key)) for key in raw if finder.pk(key)]
        elif name in {"project", "task"}:
            continue  # in the address of the form
        elif name == "contractor":
            pairs[name] = [("contractor_name", finder.text(finder.rows[raw], "name"))]
        elif name == "repeat_mode":
            once = value == "once"
            pairs[name] = [] if once else [("repeats", "on"), ("repeat_from", value)]
        elif name == "estimated_minutes":
            pairs[name] = [("duration_value", value), ("duration_unit", "minute")]
        elif name == "due_offset_days":
            pairs[name] = [("due_on", _day(value))]
        elif name == "issued_offset_days":
            pairs[name] = [("issued_on", _day(value))]
        elif name == "valid_days":
            issued = fields.get("issued_offset_days", 0) + value
            pairs[name] = [("valid_until", _day(issued))]
        elif name == "year":
            pairs[name] = [("year", task_services.today().year if value == "current" else value)]
        else:
            pairs[name] = [(name, value)]
    return pairs


def _form_of(finder: _Finder, row: dict[str, Any], existing: Any) -> str | None:
    """The address of the form that adds the row, or of the form of the row already there."""
    home_id = finder.home.pk
    entity = row["entity"]
    if entity == "rooms.room":
        if existing is not None:
            return reverse("rooms:edit", kwargs={"home_id": home_id, "room_id": existing.pk})
        return reverse("rooms:create", kwargs={"home_id": home_id})
    if entity == "rooms.item":
        if existing is not None:
            return reverse("rooms:item-edit", kwargs={"home_id": home_id, "item_id": existing.pk})
        return reverse("rooms:item-add", kwargs={"home_id": home_id})
    if entity == "tasks.task":
        if existing is not None:
            return reverse("tasks:edit", kwargs={"home_id": home_id, "task_id": existing.pk})
        return reverse("tasks:create", kwargs={"home_id": home_id})
    if entity == "reports.report":
        return reverse("reports:create", kwargs={"home_id": home_id})
    if entity == "projects.project":
        if existing is not None:
            return reverse("projects:edit", kwargs={"home_id": home_id, "project_id": existing.pk})
        return reverse("projects:create", kwargs={"home_id": home_id})
    if entity == "projects.annualenvelope":
        return reverse("projects:envelope", kwargs={"home_id": home_id})
    project = finder.get(row["fields"].get("project"))
    if project is None:
        return None  # a line or a quote needs its project first
    if entity == "projects.budgetline":
        if existing is not None:
            return reverse(
                "projects:line-edit", kwargs={"home_id": home_id, "line_id": existing.pk}
            )
        return reverse(
            "projects:line-create", kwargs={"home_id": home_id, "project_id": project.pk}
        )
    if existing is not None:
        return reverse("projects:quote-edit", kwargs={"home_id": home_id, "quote_id": existing.pk})
    return reverse("projects:quote-create", kwargs={"home_id": home_id, "project_id": project.pk})


def _with(url: str, pairs: list[tuple[str, Any]], next_url: str) -> str:
    return f"{url}?{urlencode([*pairs, ('next', next_url)])}"


def new_home(language: str, next_url: str) -> str:
    """The form of a new home, filled in with the example and marked as the guide's."""
    home = content.example_home()
    pairs = [
        (GUIDE_FLAG, "1"),
        ("name", content.text(home["name"], language)),
        ("currency", home["currency"]),
        ("hemisphere", home["hemisphere"]),
    ]
    return _with(reverse("homes:create"), pairs, next_url)


def _screen(finder: _Finder, screen: str) -> str | None:
    """The page of a screen that shows the home rather than a form of one row."""
    home_id = finder.home.pk
    if screen == "home":
        return reverse("homes:detail", kwargs={"home_id": home_id})
    if screen == "today":
        return reverse("tasks:today", kwargs={"home_id": home_id})
    if screen == "projects":
        return reverse("projects:list", kwargs={"home_id": home_id})
    if screen == "report":
        report = Report.objects.filter(home=finder.home).order_by("-created_at").first()
        if report is None:
            return reverse("reports:list", kwargs={"home_id": home_id})
        return reverse("reports:detail", kwargs={"home_id": home_id, "report_id": report.pk})
    return None


def for_step(step: dict[str, Any], home: Home | None, language: str, next_url: str) -> Links:
    """Where the step leads in `home`, the example home (None before scenario 1 is done)."""
    if step["screen"] == "new-home":
        return Links(open=new_home(language, next_url) if home is None else None, fill=None)
    if home is None:
        return Links(open=None, fill=None)
    finder = _Finder(home, language)
    if "suggest" not in step:
        url = _screen(finder, step["screen"])
        return Links(open=_with(url, [], next_url) if url else None, fill=None)
    row = finder.rows[step["suggest"]]
    existing = finder.get(step["suggest"])
    url = _form_of(finder, row, existing)
    if url is None:
        return Links(open=None, fill=None)
    by_field = _query(finder, row)
    wanted = step.get("suggest_fields") or list(row["fields"])
    pairs = [pair for name in wanted for pair in by_field.get(name, [])]
    return Links(open=_with(url, [], next_url), fill=_with(url, pairs, next_url))
