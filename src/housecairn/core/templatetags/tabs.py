"""`{% tab_bar "today" %}`: the tabs of a section of the application (docs/adr/0017).

One component for every sub-navigation, so that the current tab, the touch targets and
the focus ring are the same everywhere. Tabs switch between sister views of one section,
four at most, all visible at 360 px: Today, All, Calendar; Rooms, Equipment, Stock; In progress,
Ideas, Finished; Summary, Rooms, Projects, Upkeep. A group names the tabs of a section;
a tab is shown only to the people who may open it.
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django import template
from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

register = template.Library()

# The tabs of Projects: the statuses each one lists, by the value of `?view=`.
PROJECT_VIEWS: dict[str, tuple[str, ...]] = {
    "": ("in_progress", "planned"),
    "ideas": ("idea",),
    "finished": ("done", "abandoned"),
}


@dataclass(frozen=True)
class Tab:
    label: Any
    url: str
    current: bool


def _home_id(context: template.Context, request: HttpRequest) -> Any:
    home = context.get("home")
    if home is not None and getattr(home, "pk", None):
        return home.pk
    match = getattr(request, "resolver_match", None)
    return match.kwargs.get("home_id") if match else None


def _calendar_home(context: template.Context, request: HttpRequest) -> Any:
    """The home of the calendar: the one in its address, else the current home.

    The calendar has no home in its path. With "All homes" chosen in the selector it
    shows every home together, and its tabs lead to the page of all homes.
    """
    from housecairn.homes import current

    homes = list(context.get("homes") or ())
    raw = request.GET.get("home", "")
    try:
        wanted = UUID(raw) if raw else None
    except ValueError:
        wanted = None
    if wanted is not None:
        return next((h.pk for h in homes if h.pk == wanted), None)
    chosen = current.for_request(request)
    if chosen.all_homes or chosen.home is None:
        return None
    return chosen.home.pk


def _all_homes(request: HttpRequest, view: str, on_calendar: bool) -> list[Tab]:
    """Today and the calendar of all homes together: no list of all tasks across homes."""
    from housecairn.homes import current

    if not current.for_request(request).all_homes:
        return []
    return [
        Tab(_("Today"), reverse("index"), view == "index"),
        Tab(_("Calendar"), reverse("calendar:week"), on_calendar),
    ]


def _today(context: template.Context, request: HttpRequest, view: str) -> list[Tab]:
    on_calendar = view.startswith("calendar:")
    home_id = _calendar_home(context, request) if on_calendar else _home_id(context, request)
    if home_id is None:
        return _all_homes(request, view, on_calendar) if on_calendar or view == "index" else []
    kwargs = {"home_id": home_id}
    return [
        Tab(_("Today"), reverse("tasks:today", kwargs=kwargs), view == "tasks:today"),
        Tab(
            pgettext_lazy("tasks", "All"),
            reverse("tasks:list", kwargs=kwargs),
            view in ("tasks:list", "tasks:history"),
        ),
        Tab(_("Calendar"), f"{reverse('calendar:week')}?home={home_id}", on_calendar),
    ]


def _rooms(context: template.Context, request: HttpRequest, view: str) -> list[Tab]:
    home_id = _home_id(context, request)
    if home_id is None:
        return []
    kwargs = {"home_id": home_id}
    return [
        Tab(_("Rooms"), reverse("rooms:list", kwargs=kwargs), view == "rooms:list"),
        Tab(
            pgettext_lazy("tab", "Equipment"),
            reverse("rooms:items", kwargs=kwargs),
            view == "rooms:items",
        ),
        Tab(_("Stock"), reverse("stock:list", kwargs=kwargs), view == "stock:list"),
    ]


def _projects(context: template.Context, request: HttpRequest, view: str) -> list[Tab]:
    home_id = _home_id(context, request)
    if home_id is None:
        return []
    base = reverse("projects:list", kwargs={"home_id": home_id})
    shown = request.GET.get("view", "")
    shown = shown if shown in PROJECT_VIEWS else ""
    return [
        Tab(_("In progress"), base, shown == ""),
        Tab(_("Ideas"), f"{base}?view=ideas", shown == "ideas"),
        Tab(
            pgettext_lazy("projects tab", "Finished"),
            f"{base}?view=finished",
            shown == "finished",
        ),
    ]


def _budget(context: template.Context, request: HttpRequest, view: str) -> list[Tab]:
    home_id = _home_id(context, request)
    if home_id is None:
        return []
    kwargs = {"home_id": home_id}
    return [
        Tab(_("Summary"), reverse("budget:overview", kwargs=kwargs), view == "budget:overview"),
        Tab(
            pgettext_lazy("budget tab", "Rooms"),
            reverse("budget:rooms", kwargs=kwargs),
            view == "budget:rooms",
        ),
        Tab(
            pgettext_lazy("budget tab", "Projects"),
            reverse("budget:projects", kwargs=kwargs),
            view == "budget:projects",
        ),
        Tab(_("Upkeep"), reverse("budget:tasks", kwargs=kwargs), view == "budget:tasks"),
    ]


def _home(context: template.Context, request: HttpRequest, view: str) -> list[Tab]:
    home_id = _home_id(context, request)
    if home_id is None:
        return []
    kwargs = {"home_id": home_id}
    tabs = [
        Tab(_("Home settings"), reverse("homes:detail", kwargs=kwargs), view == "homes:detail"),
        Tab(
            _("Household and roles"),
            reverse("homes:household", kwargs=kwargs),
            view in ("homes:household", "homes:member"),
        ),
    ]
    if context.get("is_owner") or context.get("role") == "owner":
        tabs.append(
            Tab(
                # Its own entry: the French label is shorter than the title of the page.
                pgettext_lazy("tab", "Home audit log"),
                reverse("homes:audit", kwargs=kwargs),
                view == "homes:audit",
            )
        )
    return tabs


def _instance(context: template.Context, request: HttpRequest, view: str) -> list[Tab]:
    return [
        # The files of the homes are reached from the overview: four tabs at most.
        Tab(
            _("Overview"),
            reverse("instance:overview"),
            view in ("instance:overview", "instance:usage"),
        ),
        Tab(_("Settings"), reverse("instance:settings"), view == "instance:settings"),
        Tab(
            # Its own entry: the French label is shorter than the title of the page.
            pgettext_lazy("tab", "Sign-in providers"),
            reverse("instance:providers"),
            view in ("instance:providers", "instance:provider"),
        ),
        Tab(_("Instance audit log"), reverse("instance:audit"), view == "instance:audit"),
    ]


_GROUPS = {
    "today": (_("Today"), _today),
    "rooms": (_("Rooms"), _rooms),
    "projects": (_("Projects"), _projects),
    "budget": (_("Budget"), _budget),
    "home": (_("Home"), _home),
    "instance": (_("Instance administration"), _instance),
}


@register.inclusion_tag("components/tabs.html", takes_context=True)
def tab_bar(context: template.Context, group: str) -> dict[str, Any]:
    label, build = _GROUPS[group]
    request = context["request"]
    match = getattr(request, "resolver_match", None)
    view = match.view_name if match else ""
    return {"label": label, "tabs": build(context, request, view)}


@register.inclusion_tag("components/tabs.html", takes_context=True)
def filter_tabs(
    context: template.Context,
    label: Any,
    param: str,
    current: str,
    choices: Any,
    default: str = "",
    keep: str = "",
) -> dict[str, Any]:
    """Segmented tabs for a filter of state (design/patterns.md, "Filters and search").

    One tab per `(value, title)` of `choices`; each is a link to the same page with `?<param>=`
    set to its value (left out for the `default` one), so that the filter works without script.
    Only the parameters named in `keep` (comma separated) follow from one tab to another, such
    as the words searched; the page number never does.
    """
    request = context["request"]
    kept = {name for name in keep.split(",") if name}
    tabs = []
    for value, title in choices:
        params = [
            (name, text)
            for name, text in request.GET.items()
            if name in kept and name != param and text
        ]
        if value != default:
            params.append((param, value))
        query = urlencode(params)
        tabs.append(
            Tab(title, f"{request.path}?{query}" if query else request.path, value == current)
        )
    return {"label": label, "tabs": tabs}
