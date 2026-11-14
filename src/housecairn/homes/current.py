"""The current home: where a section opens when its address carries no home.

Most households have one home; some have two or more. The home last chosen in the
selector of the app bar, or last opened by a page of a home, is kept in the session, so
that Rooms, Projects, Budget and Today open on it without a page that asks which home
(docs/developer/design-guide.md, "Current home").

A value kept in a session is a preference, never a permission. It is read back only
among the homes the person may open now and that are not archived
(`active_homes_for_request`, docs/adr/0079), at every request:
a forged id, a home left or deleted, a home locked behind a sign-in provider is never
returned, and the next home of the person (by name) takes its place. Every page of a home
still takes its home from its address and checks it (`HomeScopedMixin`); this module only
chooses the address a section root opens.

API requests never read nor write it: an API token has no session, and its scope is
the list of homes of the token.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from django.http import HttpRequest

from housecairn.homes.models import Home, Membership

# Session keys: the id of the current home, and whether Today shows all homes.
HOME_KEY = "housecairn.current_home"
ALL_KEY = "housecairn.current_all"

# The selector is shown from this many homes on: one choice is noise.
SELECTOR_FROM = 2

_CACHE = "_housecairn_current"


@dataclass(frozen=True)
class Current:
    """The homes the person may open now, the current one, and the choice "All homes"."""

    homes: list[Home] = field(default_factory=list)
    home: Home | None = None
    all_homes: bool = False
    roles: dict[UUID, str] = field(default_factory=dict)

    @property
    def has_selector(self) -> bool:
        return len(self.homes) >= SELECTOR_FROM

    def find(self, raw: Any) -> Home | None:
        """The home of id `raw` among the person's open homes, else None."""
        wanted = str(raw)
        return next((h for h in self.homes if str(h.pk) == wanted), None)


def for_request(request: HttpRequest) -> Current:
    """The current home of a signed-in web request, read once per request.

    A stored id that is not among the open homes of the person is dropped from the
    session; the first open home by name stands in for it (none: no current home).
    """
    cached: Current | None = getattr(request, _CACHE, None)
    if cached is not None:
        return cached
    user = getattr(request, "user", None)
    session = getattr(request, "session", None)
    if user is None or not user.is_authenticated or session is None:
        found = Current()
        setattr(request, _CACHE, found)
        return found
    from housecairn.homes.scoping import active_homes_for_request

    homes = list(active_homes_for_request(request).order_by("name", "id"))
    stored = session.get(HOME_KEY)
    home = next((h for h in homes if str(h.pk) == stored), None)
    if stored is not None and home is None:
        session.pop(HOME_KEY, None)
    if home is None and homes:
        home = homes[0]
    chosen = home is not None and str(home.pk) == stored
    # Today shows all homes when the person chose it, and before any home was chosen.
    all_homes = len(homes) >= SELECTOR_FROM and (bool(session.get(ALL_KEY)) or not chosen)
    if session.get(ALL_KEY) and len(homes) < SELECTOR_FROM:
        session.pop(ALL_KEY, None)
    roles = dict(
        Membership.objects.filter(user=user, home__in=homes).values_list("home_id", "role")
    )
    found = Current(homes=homes, home=home, all_homes=all_homes, roles=roles)
    setattr(request, _CACHE, found)
    return found


def section_root(request: HttpRequest, route: str) -> str | None:
    """The address of the root `route` (a route of one home) in the current home, if any."""
    from django.urls import reverse

    home = for_request(request).home
    return reverse(route, kwargs={"home_id": home.pk}) if home else None


def known(request: HttpRequest) -> Current | None:
    """The current home if this request already read it, without reading it."""
    return getattr(request, _CACHE, None)


def remember(request: HttpRequest, home: Home) -> None:
    """Make `home` the current home. The caller has checked that the person may open it.

    It leaves the choice "All homes" of Today as it is: opening a task of one home from
    the page of all homes must not change what that page shows when the person comes back.
    """
    session = getattr(request, "session", None)
    if session is None:
        return
    if session.get(HOME_KEY) != str(home.pk):
        session[HOME_KEY] = str(home.pk)
        if hasattr(request, _CACHE):
            delattr(request, _CACHE)


def choose(request: HttpRequest, home: Home | None) -> None:
    """The choice of the selector: one home, or all of them (`None`) for Today."""
    if home is None:
        request.session[ALL_KEY] = True
    else:
        request.session[HOME_KEY] = str(home.pk)
        request.session.pop(ALL_KEY, None)
    if hasattr(request, _CACHE):
        delattr(request, _CACHE)
