"""Context of the application shell: the five sections of the navigation (docs/adr/0017).

The bar at the bottom of a phone and the column on the left of a computer carry the same
sections, Today, Rooms, Projects, Budget and More. The entry marked as current follows
the tree of the application: a page belongs to the section whose root leads to it.
"""

from typing import Any

from django.http import HttpRequest
from django.urls import Resolver404, ResolverMatch, reverse

from housecairn.core import more, navigation
from housecairn.core.prefix import resolve_url

# Sections of the signed-in navigation, in display order.
TODAY, ROOMS, PROJECTS, BUDGET, MORE = "today", "rooms", "projects", "budget", "more"

# Pages of an application namespace that belong to More rather than to its section:
# the contractors of a home (More, Home), the display preferences and the calendar
# feeds of the person (More, Me).
_IN_MORE = ("tasks:contractor", "calendar:feeds", "calendar:regenerate", "calendar:revoke")

# The roots of the five sections and their tabs, the pages without a back link (design
# guide, Return rule), and the entry pages that lead to them. On a phone only these keep
# the brand in the app bar; every other page starts with its back link, then its title
# (design/patterns.md, Header).
SECTION_ROOTS = frozenset(
    {
        "index",
        "tasks",
        "tasks:today",
        "tasks:list",
        "calendar:index",
        "calendar:week",
        "calendar:month",
        "calendar:year",
        "rooms",
        "rooms:list",
        "stock",
        "stock:list",
        "projects",
        "projects:list",
        "budget",
        "budget:overview",
        "budget:rooms",
        "budget:projects",
        "budget:tasks",
        "more",
    }
)


def nav_section(request: HttpRequest) -> str:
    """The navigation entry that matches the page being shown ("" on an error page).

    With a safe `next`, the section is that of the origin: a room opened from the budget
    keeps Budget marked, so the bar never changes tab under the finger (design guide,
    "Back link and breadcrumbs").
    """
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    origin = navigation.safe_next(request)
    if origin is not None:
        try:
            return section_of(resolve_url(origin))
        except Resolver404:
            pass
    return section_of(match)


def section_of(match: ResolverMatch) -> str:
    """The section a route belongs to, in the tree of the application."""
    view = match.view_name
    if view.startswith(_IN_MORE):
        return MORE
    if view in ("index", "tasks") or match.namespace in ("tasks", "calendar"):
        return TODAY
    if match.namespace in ("rooms", "stock") or view in ("rooms", "stock"):
        return ROOMS
    if match.namespace == "projects" or view == "projects":
        return PROJECTS
    if match.namespace == "budget" or view == "budget":
        return BUDGET
    return MORE


def is_section_root(request: HttpRequest) -> bool:
    """Whether the page is a root of its section (or a tab of one): it keeps the brand.

    A page that resolved to no route (an error page) counts as a root: it has no back link
    either, and must not start with nothing.
    """
    match = getattr(request, "resolver_match", None)
    return match is None or match.view_name in SECTION_ROOTS


def _home_id(request: HttpRequest) -> Any:
    """The home in the address of the page, if it has one."""
    match = getattr(request, "resolver_match", None)
    return match.kwargs.get("home_id") if match else None


def _entry(request: HttpRequest, route: str, entry: str) -> str:
    """The root of a section in the home being viewed, else its entry page."""
    home_id = _home_id(request)
    if home_id:
        return reverse(route, kwargs={"home_id": home_id})
    return reverse(entry)


def today_url(request: HttpRequest) -> str:
    """Address of the Today entry: Today in the home being viewed, else the home page."""
    home_id = _home_id(request)
    if home_id:
        return reverse("tasks:today", kwargs={"home_id": home_id})
    return reverse("index")


def rooms_url(request: HttpRequest) -> str:
    """Address of the Rooms entry: the rooms of the home being viewed, else the entry page."""
    return _entry(request, "rooms:list", "rooms")


def projects_url(request: HttpRequest) -> str:
    """Address of the Projects entry: the projects of the home being viewed, else the entry."""
    return _entry(request, "projects:list", "projects")


def budget_url(request: HttpRequest) -> str:
    """Address of the Budget entry: the budget of the home being viewed, else the entry."""
    return _entry(request, "budget:overview", "budget")


def stock_url(request: HttpRequest) -> str:
    """Address of the stock (a tab of Rooms): that of the home being viewed, else the entry."""
    return _entry(request, "stock:list", "stock")


def more_groups(request: HttpRequest) -> list[dict[str, Any]]:
    """The content of More, unfolded in the column of a computer (signed in only)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return []
    from housecairn.homes import current
    from housecairn.reports.services import waiting_for

    match = getattr(request, "resolver_match", None)
    home = current.for_request(request).home
    home_id = _home_id(request) or (home.pk if home else None)
    return more.for_page(
        user, home_id, match.view_name if match else "", waiting_for(request, home_id)
    )


def home_switcher(request: HttpRequest, section: str) -> dict[str, Any] | None:
    """The selector of the current home in the app bar: from two homes on, else None.

    It names the home of the page when its address has one, else "All homes" on Today
    when chosen, else the current home. "All homes" is offered on Today only: the other
    sections show one home at a time.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    from housecairn.homes import current

    found = current.for_request(request)
    if not found.has_selector:
        return None
    match = getattr(request, "resolver_match", None)
    shown = found.find(_home_id(request)) if _home_id(request) else None
    if shown is None and match is not None and match.view_name == "calendar:week":
        shown = found.find(request.GET.get("home", "")) if request.GET.get("home") else None
    on_today = section == TODAY
    all_shown = shown is None and on_today and found.all_homes
    if shown is None and not all_shown:
        shown = found.home
    return {
        "homes": found.homes,
        "shown": shown,
        "all_offered": on_today,
        "all_shown": all_shown,
        "next": navigation.current_path(request),
    }


def shell(request: HttpRequest) -> dict[str, Any]:
    """Context of the application shell: the current section and the entry addresses."""
    section = nav_section(request)
    return {
        "home_switcher": home_switcher(request, section),
        "nav_more": more_groups(request),
        "nav_section": section,
        "section_root": is_section_root(request),
        "today_url": today_url(request),
        "rooms_url": rooms_url(request),
        "projects_url": projects_url(request),
        "budget_url": budget_url(request),
        "stock_url": stock_url(request),
    }
