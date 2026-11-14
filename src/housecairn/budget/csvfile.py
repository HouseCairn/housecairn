"""CSV files of the budget views. A text cell cannot become a formula (docs/adr/0051)."""

import csv
import datetime
from decimal import Decimal
from typing import Any

from django.http import HttpResponse
from django.utils.translation import gettext as _
from django.utils.translation import pgettext

from housecairn.budget import services
from housecairn.projects.models import BudgetNature, ProjectStatus
from housecairn.projects.services import Amounts

# What a spreadsheet reads as a formula when a cell starts with it.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def safe(value: Any) -> str:
    """A cell as text. A text starting like a formula gets a leading quote, so it stays text.

    Numbers made by the program (`Decimal`, `int`) are written as they are: they are not
    typed by anyone, and a negative gap must stay a number.
    """
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, int | datetime.date):
        return str(value)
    text = str(value)
    return "'" + text if text.startswith(FORMULA_STARTS) else text


def _amounts(amounts: Amounts) -> list[Decimal]:
    return [amounts.planned, amounts.committed, amounts.paid, amounts.engaged, amounts.gap]


def _amount_heads() -> list[str]:
    return [pgettext("amount", "Planned"), _("Committed"), _("Paid"), _("Used"), _("Gap")]


def rooms(report: services.Report) -> tuple[list[str], list[list[Any]]]:
    head = [_("Room"), *_amount_heads(), _("Projects used"), _("Upkeep paid")]
    rows = [
        [
            line.label or _("No room"),
            *_amounts(line.amounts),
            line.extra["projects"].engaged,
            line.extra["upkeep"].paid,
        ]
        for line in report.lines
    ]
    return head, rows


def projects(report: services.Report) -> tuple[list[str], list[list[Any]]]:
    head = [
        _("Project"),
        _("Status"),
        _("Counted in"),
        *_amount_heads(),
        _("To pay"),
        _("Planned by its tasks"),
    ]
    rows = [
        [
            line.label,
            ProjectStatus(line.extra["status"]).label,
            line.extra["year"],
            *_amounts(line.amounts),
            line.extra["to_pay"],
            line.extra["tasks_planned"],
        ]
        for line in report.lines
    ]
    return head, rows


def tasks(report: services.Report) -> tuple[list[str], list[list[Any]]]:
    head = [
        _("Task"),
        _("Room"),
        _("Estimate each time"),
        _("Cost a year"),
        _("Of which stock"),
        _("Stock items without a price"),
        pgettext("amount", "Planned"),
        _("Paid"),
        _("Gap"),
        _("Paid in all"),
        _("Done without a cost"),
    ]
    rows = [
        [
            line.label,
            line.extra["room"],
            line.extra["estimate"],
            line.extra["annual_cost"],
            line.extra["stock_estimate"],
            line.extra["unpriced_materials"],
            line.amounts.planned,
            line.amounts.paid,
            line.amounts.gap,
            line.extra["total_paid"],
            line.extra["missing_costs"],
        ]
        for line in report.lines
    ]
    return head, rows


def overview(
    years: list[services.YearLine], natures: list[services.Line], rolling: services.Rolling
) -> tuple[list[str], list[list[Any]]]:
    head = [_("Section"), _("Label"), *_amount_heads()]
    rows: list[list[Any]] = [[_("Year"), line.year, *_amounts(line.total)] for line in years]
    rows += [
        [_("Nature"), BudgetNature(line.label).label, *_amounts(line.amounts)] for line in natures
    ]
    rows.append(
        [_("Last 12 months"), f"{rolling.since} / {rolling.until}", *_amounts(rolling.upkeep)]
    )
    return head, rows


def response(
    name: str, head: list[str], rows: list[list[Any]], *, year: int | None = None
) -> HttpResponse:
    """The file, to download. UTF-8, a comma, dot decimals, so any tool reads it back."""
    stem = f"budget-{name}" + (f"-{year}" if year else "")
    result = HttpResponse(content_type="text/csv; charset=utf-8")
    result["Content-Disposition"] = f'attachment; filename="{stem}.csv"'
    writer = csv.writer(result, lineterminator="\r\n")
    writer.writerow([safe(cell) for cell in head])
    for row in rows:
        writer.writerow([safe(cell) for cell in row])
    return result
