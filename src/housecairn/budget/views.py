"""Web views of the budget: figures only, so every role that can open the home can read them.

A read-only member sees the same pages as everyone (docs/adr/0020, 0051). Nothing here
changes data; the envelope is set from the projects screen.
"""

from functools import cached_property
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView

from housecairn.budget import csvfile, services
from housecairn.homes import current, exports
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.homes.scoping import WebHomeScopedMixin
from housecairn.projects.models import ENVELOPE_YEARS, BudgetNature
from housecairn.rooms.models import Room


def chosen_year(raw: str | None, default: int) -> int:
    """The `year` of the address; anything else than a plausible year is today's."""
    try:
        year = int(raw) if raw is not None else default
    except ValueError:
        return default
    return year if ENVELOPE_YEARS[0] <= year <= ENVELOPE_YEARS[1] else default


class EntryView(LoginRequiredMixin, TemplateView):
    """The Budget entry: the budget of the current home, else the page to add a home."""

    template_name = "budget/entry.html"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        target = current.section_root(request, "budget:overview")
        if target:
            return redirect(target)
        return self.render_to_response({"combined": {}, "homes": []})


class BudgetPage(WebHomeScopedMixin, TemplateView):
    """A page of one home. `?export=csv` gives the same figures as a file."""

    request: HttpRequest
    kind = ""

    @cached_property
    def home(self) -> Home:
        return self.get_home()

    @cached_property
    def ledger(self) -> services.Ledger:
        return services.load(self.home)

    @cached_property
    def year(self) -> int:
        return chosen_year(self.request.GET.get("year"), self.ledger.today.year)

    def table(self) -> tuple[list[str], list[list[Any]]]:
        raise NotImplementedError

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if request.GET.get("export") == "csv":
            # The example home of the guide never leaves the instance (docs/adr/0084).
            exports.refuse_example(self.home)
            head, rows = self.table()
            return csvfile.response(self.kind, head, rows, year=self.year)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(
            home=self.home,
            year=self.year,
            years=self.ledger.years(),
            kind=self.kind,
            can_write=home_services.can_write(self.request.user, self.home),
            **kwargs,
        )


class OverviewView(BudgetPage):
    template_name = "budget/overview.html"
    kind = "overview"

    def table(self) -> tuple[list[str], list[list[Any]]]:
        return csvfile.overview(
            services.by_year(self.ledger),
            services.by_nature(self.ledger, self.year),
            services.rolling(self.ledger),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(
            envelope=services.envelope(self.ledger, self.year),
            natures=[
                (BudgetNature(line.label).label, line.amounts)
                for line in services.by_nature(self.ledger, self.year)
            ],
            yearly=services.by_year(self.ledger),
            rolling=services.rolling(self.ledger),
            **kwargs,
        )


class RoomsView(BudgetPage):
    template_name = "budget/rooms.html"
    kind = "rooms"

    def table(self) -> tuple[list[str], list[list[Any]]]:
        return csvfile.rooms(services.by_room(self.ledger, self.year))

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(report=services.by_room(self.ledger, self.year), **kwargs)


class RoomView(BudgetPage):
    """The budget of one room, in Budget: its projects and its upkeep, a link to the room."""

    template_name = "budget/room.html"
    kind = "room"

    @cached_property
    def room(self) -> Room:
        return get_object_or_404(Room, home=self.home, pk=self.kwargs["room_id"])

    @cached_property
    def detail(self) -> services.RoomBudget:
        return services.room_budget(self.ledger, self.year, self.room)

    def table(self) -> tuple[list[str], list[list[Any]]]:
        report = services.by_room(self.ledger, self.year)
        report.lines[:] = [line for line in report.lines if line.id == self.room.id]
        return csvfile.rooms(report)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(room=self.room, detail=self.detail, **kwargs)


class ProjectsView(BudgetPage):
    template_name = "budget/projects.html"
    kind = "projects"

    def table(self) -> tuple[list[str], list[list[Any]]]:
        return csvfile.projects(services.by_project(self.ledger))

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(report=services.by_project(self.ledger), **kwargs)


class TasksView(BudgetPage):
    template_name = "budget/tasks.html"
    kind = "tasks"

    def table(self) -> tuple[list[str], list[list[Any]]]:
        return csvfile.tasks(services.by_task(self.ledger, self.year))

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(report=services.by_task(self.ledger, self.year), **kwargs)
