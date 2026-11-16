from typing import Any

from django import template

from housecairn.projects import services
from housecairn.tasks.models import Task

register = template.Library()


@register.inclusion_tag("projects/_gap.html")
def budget_gap(amounts: services.Amounts, currency: str) -> dict[str, Any]:
    """What the budget says, in words: left, over, or on budget. Never by colour alone."""
    return {"amounts": amounts, "currency": currency}


@register.inclusion_tag("projects/_task_link.html", takes_context=True)
def task_project(context: template.Context, task: Task, home: Any) -> dict[str, Any]:
    """The project a task belongs to, as one line of the page of the task (docs/adr/0050)."""
    return {
        "project": services.project_of_task(task),
        "task": task,
        "home": home,
        "can_write": context.get("can_write", False),
        "request": context["request"],
    }
