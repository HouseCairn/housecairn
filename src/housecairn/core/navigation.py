"""The return rule: one place reads `next`, one place names where it leads.

People come back to the screen they came from, and to the parent in the tree when it
is not known (docs/developer/design-guide.md, "Return rule"; docs/adr/0053). A link
that opens a record or a form carries `next=<current path>`; the page reads it with
`back_url`, the back link and Cancel lead there, and so does the form once sent.

`next` is a parameter anyone can forge in a link. So it is:

- checked by `core.redirects.is_safe_redirect` (a safe shape, then the host);
- a path of the application, starting with one `/` and, under a path prefix, with the
  prefix (`core.prefix.in_application`, docs/adr/0055);
- never nested: a `next` inside `next` is removed;
- cut to `MAX_LENGTH`: past it the query goes, and past it again the address is refused;
- never shown: the label of a back link comes from the route the path resolves to, and
  a record is named only when it belongs to a home the person may open.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.http import HttpRequest
from django.urls import Resolver404, reverse
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from housecairn.core import prefix
from housecairn.core.prefix import resolve_url
from housecairn.core.redirects import is_safe_redirect

# Longer than any path HouseCairn builds with its filters, short enough for a log line.
MAX_LENGTH = 512

# Query keys that never travel inside `next`: a nested return, a transient banner.
DROPPED_KEYS = ("next", "undo")


def clean_next(url: str) -> str:
    """The address without nested `next` nor transient keys, cut to `MAX_LENGTH`.

    An empty string when nothing usable is left. It does not check the host: that is
    `back_url`'s job, on the value it returns.
    """
    if not url:
        return ""
    parts = urlsplit(url)
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in DROPPED_KEYS
    ]
    cleaned = urlunsplit(parts._replace(query=urlencode(query), fragment=""))
    if len(cleaned) > MAX_LENGTH:
        cleaned = urlunsplit(parts._replace(query="", fragment=""))
    return cleaned if len(cleaned) <= MAX_LENGTH else ""


def current_path(request: HttpRequest) -> str:
    """The page being shown, as a `next` for the links it holds."""
    return clean_next(request.get_full_path())


def _raw_next(request: HttpRequest) -> str:
    raw = request.POST.get("next") if request.method == "POST" else None
    return raw or request.GET.get("next", "")


def safe_next(request: HttpRequest) -> str | None:
    """The `next` of the request when it is a safe path of this site, else None."""
    raw = _raw_next(request)
    if (
        not raw.startswith("/")
        or not is_safe_redirect(raw, request)
        or not prefix.in_application(raw)
    ):
        return None
    target = clean_next(raw)
    # The check runs again on the value returned, written out so that it is on that value.
    if (
        not target.startswith("/")
        or not url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        )
        or not prefix.in_application(target)
    ):
        return None
    return target


def nested_next(request: HttpRequest) -> str:
    """The safe `next` carried by the `next` of the request ("" when there is none).

    `safe_next` drops it on purpose; a page that is left and then reached again by an action
    in place (the page of a task) hands it back through `with_next`.
    """
    if safe_next(request) is None:
        return ""
    inner = dict(parse_qsl(urlsplit(_raw_next(request)).query)).get("next", "")
    if (
        not inner.startswith("/")
        or not is_safe_redirect(inner, request)
        or not prefix.in_application(inner)
    ):
        return ""
    return clean_next(inner)


def back_url(request: HttpRequest, default: str) -> str:
    """Where to go back: the `next` of the request when it is safe, else `default`."""
    return safe_next(request) or default


def route_name(url: str) -> str | None:
    """The namespaced name of the route `url` resolves to, or None."""
    try:
        return resolve_url(url).view_name
    except Resolver404:
        return None


# ---------- labels ----------


@dataclass(frozen=True)
class Record:
    """A route that shows one record: the label is a field of it."""

    model: str  # "app_label.Model"
    kwarg: str  # the URL keyword that holds its id
    field: str  # the field shown as label


# Route name -> a fixed label, or the record whose field names it. A route missing here
# has no label: a back link to it falls back to its default.
LABELS: dict[str, Any] = {
    "tasks:today": _("Today"),
    "tasks:list": _("Tasks"),
    "tasks:history": _("History"),
    "tasks:detail": Record("tasks.Task", "task_id", "title"),
    "tasks:contractors": _("Contractors"),
    "tasks:contractor-detail": Record("tasks.Contractor", "contractor_id", "name"),
    "rooms:list": _("Rooms"),
    "rooms:items": pgettext_lazy("tab", "Equipment"),
    "rooms:search": pgettext_lazy("screen", "Search"),
    "rooms:detail": Record("rooms.Room", "room_id", "name"),
    "rooms:item-detail": Record("rooms.Item", "item_id", "name"),
    "homes:detail": Record("homes.Home", "home_id", "name"),
    "homes:list": _("My homes"),
    "homes:household": _("Household and roles"),
    "api-tokens:list": _("API tokens"),
    "stock:list": _("Stock"),
    "stock:detail": Record("stock.Article", "article_id", "name"),
    "projects:list": _("Projects"),
    "projects:compare": _("Compare"),
    "projects:detail": Record("projects.Project", "project_id", "title"),
    "budget:overview": _("Budget"),
    "budget:rooms": _("Budget"),
    "budget:room": _("Budget"),
    "budget:projects": _("Budget"),
    "budget:tasks": _("Budget"),
    "reports:list": _("Reports"),
    "reports:detail": Record("reports.Report", "report_id", "title"),
    "labels:list": _("QR labels"),
    "labels:detail": _("QR label"),
    "index": _("Today"),
    "more": _("More"),
}


def _record_label(record: Record, kwargs: dict[str, Any], request: HttpRequest) -> str | None:
    from django.apps import apps

    from housecairn.homes.scoping import open_homes_for_request

    model = apps.get_model(record.model)
    pk = kwargs.get(record.kwarg)
    if pk is None:
        return None
    homes = open_homes_for_request(request)
    found = model.objects.filter(pk=pk)
    found = (
        found.filter(pk__in=homes) if record.model == "homes.Home" else found.filter(home__in=homes)
    )
    value = found.values_list(record.field, flat=True).first()
    return str(value) if value else None


def _index_label(request: HttpRequest) -> Any:
    """`/` is Today for a person with a home to open, and the welcome page otherwise."""
    from housecairn.homes import current

    return LABELS["index"] if current.for_request(request).homes else _("Welcome")


def label_for(url: str, request: HttpRequest) -> str | None:
    """The title of the screen `url` leads to, from its route; never from the address."""
    try:
        match = resolve_url(url)
    except Resolver404:
        return None
    if match.view_name == "index":
        return str(_index_label(request))
    entry = LABELS.get(match.view_name)
    if entry is None:
        return None
    if isinstance(entry, Record):
        return _record_label(entry, match.kwargs, request)
    return str(entry)


@dataclass(frozen=True)
class BackLink:
    url: str
    label: str
    route: str | None


def back_link(request: HttpRequest, default: str, default_label: str | None = None) -> BackLink:
    """The back link of a page: `next` when it is safe and named, else `default`.

    `default_label` names `default` when the template already knows it (a record shown on
    the page); otherwise the label comes from the route of `default` too.
    """
    target = safe_next(request)
    if target is not None:
        label = label_for(target, request)
        if label:
            return BackLink(target, label, route_name(target))
    label = default_label or label_for(default, request) or ""
    return BackLink(default, str(label), route_name(default))


# ---------- breadcrumbs ----------

# Titles of the pages that are not a record but a form, by route name. A record page is
# titled by its `Record` in LABELS; the budget of a room by the room it is about.
FORM_TITLES: dict[str, Any] = {
    "tasks:create": _("Add a task"),
    "tasks:edit": _("Edit the task"),
    "rooms:create": _("Add a room"),
    "rooms:edit": _("Edit the room"),
    "rooms:item-create": _("Add an item"),
    "rooms:item-add": _("Add an item"),
    "rooms:item-edit": _("Edit the item"),
    "stock:create": _("Add a stock item"),
    "stock:edit": _("Edit the stock item"),
    "projects:create": _("Add a project"),
    "projects:edit": _("Edit the project"),
    "reports:create": _("Report a problem"),
    "rooms:room-photo-add": _("Add a photo"),
    "rooms:item-photo-add": _("Add a photo"),
    "stock:supplier-create": _("Add a supplier"),
    "stock:supplier-edit": _("Edit the supplier"),
    "projects:quote-create": _("Add a quote"),
    "projects:quote-edit": _("Edit the quote"),
    "projects:line-create": _("Add a budget line"),
    "projects:line-edit": _("Edit the budget line"),
    "projects:envelope": _("Annual envelope"),
    "tasks:contractor-create": _("Add a contractor"),
    "tasks:contractor-edit": _("Edit the contractor"),
    "api-tokens:new": _("New API token"),
    "api-tokens:revoke": _("Revoke the token"),
    "homes:import": _("Import tables"),
    "tasks:templates": _("Add from a template"),
}

RECORD_TITLES: dict[str, Record] = {
    "budget:room": Record("rooms.Room", "room_id", "name"),
}


def _home(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {"home_id": kwargs["home_id"]}


def _room_of_item(kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    from housecairn.rooms.models import Item

    room_id = (
        Item.objects.filter(pk=kwargs["item_id"], home_id=kwargs["home_id"])
        .values_list("room_id", flat=True)
        .first()
    )
    if room_id is None:
        return None
    return "rooms:detail", {**_home(kwargs), "room_id": room_id}


# Route -> where it hangs in the tree: the route of its parent and its keywords. A route
# missing here is a root of its section, or hangs from the page that names it as default.
PARENTS: dict[str, Callable[[dict[str, Any]], tuple[str, dict[str, Any]] | None]] = {
    "rooms:detail": lambda kw: ("rooms:list", _home(kw)),
    "rooms:item-detail": _room_of_item,
    "rooms:items": lambda kw: ("rooms:list", _home(kw)),
    "stock:detail": lambda kw: ("stock:list", _home(kw)),
    "stock:list": lambda kw: ("rooms:list", _home(kw)),
    "tasks:detail": lambda kw: ("tasks:list", _home(kw)),
    "tasks:list": lambda kw: ("tasks:today", _home(kw)),
    "projects:detail": lambda kw: ("projects:list", _home(kw)),
    "reports:detail": lambda kw: ("reports:list", _home(kw)),
    "reports:list": lambda kw: ("homes:detail", _home(kw)),
    "tasks:contractor-detail": lambda kw: ("tasks:contractors", _home(kw)),
    "tasks:contractors": lambda kw: ("homes:detail", _home(kw)),
    "labels:detail": lambda kw: ("labels:list", _home(kw)),
    "labels:list": lambda kw: ("homes:detail", _home(kw)),
    "homes:household": lambda kw: ("homes:detail", _home(kw)),
    "budget:room": lambda kw: ("budget:rooms", _home(kw)),
    "api-tokens:new": lambda kw: ("api-tokens:list", {}),
    "api-tokens:revoke": lambda kw: ("api-tokens:list", {}),
}


@dataclass(frozen=True)
class Crumb:
    url: str | None  # None for the page itself
    label: str


def _title(request: HttpRequest) -> str | None:
    """The title of the page being shown, for the last crumb; None when it has none."""
    match = request.resolver_match
    if match is None:
        return None
    name = match.view_name
    if name in FORM_TITLES:
        return str(FORM_TITLES[name])
    entry = RECORD_TITLES.get(name) or LABELS.get(name)
    if isinstance(entry, Record):
        return _record_label(entry, match.kwargs, request)
    return None


def is_form(request: HttpRequest) -> bool:
    match = request.resolver_match
    return match is not None and match.view_name in FORM_TITLES


def breadcrumbs(
    request: HttpRequest, default: str, default_label: str | None = None
) -> list[Crumb]:
    """The tree from the section down to this page: `Section > ... > Record`.

    It follows the tree, not the origin: it is built from `default`, the parent of the
    page, and the parents of that parent (`PARENTS`). Every crumb is named after its
    route, so the trail is empty when the page or one of its ancestors has no label, and
    when the parent is More, which has no page on a computer.
    """
    current = _title(request)
    if current is None or route_name(default) == "more":
        return []
    trail: list[Crumb] = []
    url: str | None = default
    label = default_label
    while url is not None:
        name = route_name(url)
        text = label or label_for(url, request)
        if name is None or not text:
            return []
        trail.insert(0, Crumb(url, str(text)))
        label = None
        parent = PARENTS.get(name)
        if parent is None:
            break
        found = parent(resolve_url(url).kwargs)
        url = reverse(found[0], kwargs=found[1]) if found else None
    return [*trail, Crumb(None, current)]


def with_next(url: str, next_url: str) -> str:
    """`url` with `next=<next_url>` in its query, any `next` it had replaced."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "next"]
    if next_url:
        query.append(("next", next_url))
    return urlunsplit(parts._replace(query=urlencode(query)))


def stay(request: HttpRequest, page: str) -> str:
    """`page` again after an action in place, still knowing where it was opened from."""
    return with_next(page, safe_next(request) or "")


def is_section_list(url: str | None, name: str) -> bool:
    """True when `url` is the list `name` itself (any filter): a creation there opens the record."""
    return url is not None and route_name(url) == name


# ---------- after a form ----------


def added_message(added: Any, record_url: str) -> SafeString:
    """ "Task added · Open": the creation said, and the new record one tap away."""
    return format_html(
        '{} · <a href="{}">{}</a>', added, record_url, pgettext_lazy("added message", "Open")
    )


def after_create(
    request: HttpRequest, record_url: str, section_list: str, added: Any, saved: Any
) -> str:
    """Where a creation goes (decision Q2): back to its context, with "Added · Open".

    From the list of its own section (`section_list`, a route name), or without a safe
    `next`, the new record opens: the next step is there. `added` is the short message
    joined to Open, `saved` the full sentence shown on the record.
    """
    target = safe_next(request)
    if target is None or is_section_list(target, section_list) or target == record_url:
        messages.success(request, saved)
        return record_url
    # Open leads to the record, which then comes back to where the person was working.
    messages.success(request, added_message(added, with_next(record_url, target)))
    return target


def after_delete(request: HttpRequest, parent: str, gone: Iterable[Any]) -> str:
    """Where a deletion goes: `next` if safe and not about what is gone, else `parent`.

    `gone` holds the ids of the deleted object and of its children (items of a room,
    photos, occurrences): a `next` whose route names one of them leads to a page that
    no longer exists, so the person lands on the parent instead.
    """
    target = safe_next(request)
    if target is None:
        return parent
    try:
        match = resolve_url(target)
    except Resolver404:
        return parent
    ids = {str(pk) for pk in gone}
    if any(str(value) in ids for value in match.kwargs.values()):
        return parent
    return target
