from decimal import Decimal
from typing import Any

from django import template

from housecairn.budget import services
from housecairn.projects.models import ZERO
from housecairn.projects.services import Amounts
from housecairn.rooms.models import Room
from housecairn.tasks.models import Occurrence, Task

register = template.Library()


@register.inclusion_tag("budget/_gauge.html")
def gauge(amounts: Amounts) -> dict[str, Any]:
    """Engaged against planned, as a labelled gauge. The words carry the meaning, not the bar."""
    if amounts.planned <= ZERO:
        return {"percent": None, "amounts": amounts}
    percent = int((amounts.engaged * 100 / amounts.planned).quantize(Decimal(1)))
    return {"percent": percent, "shown": min(percent, 100), "amounts": amounts}


@register.inclusion_tag("budget/_task_cost.html")
def task_cost(task: Task, home: Any) -> dict[str, Any]:
    """The cost of a task on its page: the estimate, what it comes to in a year, what it cost."""
    row = services.task_row(task)
    if row is None:  # a task of a project: its money is in the budget lines of the project
        return {"row": None}
    total = row.figures(services.anytime)
    return {"row": row, "home": home, "paid": total.paid, "missing": total.missing}


@register.inclusion_tag("budget/_occurrence_cost.html")
def occurrence_cost(occurrence: Occurrence, home: Any) -> dict[str, Any]:
    """On the page of what was done: the estimate, next to the field of the real cost."""
    task = occurrence.task
    plan = services.stock_plan_of(task)
    parts = [task.labour_budget, task.material_budget]
    estimate = (
        None
        if all(part is None for part in parts) and plan.priced == 0
        else services.material_of(task.material_budget, plan) + (task.labour_budget or ZERO)
    )
    return {"estimate": estimate, "home": home}


@register.inclusion_tag("budget/_room_budget.html", takes_context=True)
def room_budget(context: template.Context, room: Room) -> dict[str, Any]:
    """The block "Budget of the room" of a room page: this year's total, a link to the detail."""
    ledger = services.load(room.home)
    year = ledger.today.year
    detail = services.room_budget(ledger, year, room)
    return {
        "request": context["request"],
        "room": room,
        "home": room.home,
        "year": year,
        "detail": detail,
    }
