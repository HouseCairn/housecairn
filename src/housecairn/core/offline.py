"""What the service worker may keep for reading offline, and when it must forget it.

The decision is the server's (docs/adr/0065). The service worker keeps a page only when
its response carries `HouseCairn-Offline: <key>`, which this middleware sets on Today and
on the task list of the current home, for a signed-in person, on a 200 that is not
`no-store`, and on nothing else. The key is bound to the person, to the session and to the
home: a new sign-in, another person or another home gives another key, and the service
worker drops every page kept under any other key.

Any response that says the person is gone carries `HouseCairn-Offline: purge`: every HTML
page of a signed-out visitor (the sign-in page, the welcome page after signing out), and
every 401 and 403. Not the manifest nor any other file, which some proxies fetch without
the cookie. The service worker then drops every page it kept.
"""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.utils import translation
from django.utils.cache import patch_cache_control
from django.utils.crypto import salted_hmac

HEADER = "HouseCairn-Offline"
PURGE = "purge"
# The only pages kept for reading offline: Today and the task list of a home.
OFFLINE_ROUTES: frozenset[str] = frozenset({"tasks:today", "tasks:list"})
_SALT = "housecairn.core.offline"
# Set in the session when the person changes language: the next page purges.
_PURGE_FLAG = "housecairn_offline_purge"


def purge_on_next_page(request: HttpRequest) -> None:
    """The next page the person opens makes the service worker drop every page it kept."""
    request.session[_PURGE_FLAG] = True


def cache_key(request: HttpRequest, home_id: object) -> str:
    """A key for the pages of this person, in this session, for this home.

    An HMAC with the secret key: the session key never leaves the server, and a key seen in
    a browser tells nothing about another one.
    """
    session_key = request.session.session_key or ""
    # The language too: a page kept in one language is dropped when the person reads the
    # next one in another (docs/adr/0073).
    value = f"{request.user.pk}:{session_key}:{home_id}:{translation.get_language()}"
    return salted_hmac(_SALT, value, algorithm="sha256").hexdigest()[:32]


def _may_keep(request: HttpRequest, response: HttpResponse) -> bool:
    match = request.resolver_match
    if request.method != "GET" or match is None or match.view_name not in OFFLINE_ROUTES:
        return False
    if response.status_code != 200 or not request.user.is_authenticated:
        return False
    if "no-store" in response.get("Cache-Control", ""):
        return False
    if not response.get("Content-Type", "").startswith("text/html"):
        return False
    # The page of a home becomes the current home (homes.scoping): only that one is kept.
    from housecairn.homes import current

    home = current.for_request(request).home
    return home is not None and str(home.pk) == str(match.kwargs.get("home_id"))


def _purge_asked(request: HttpRequest, response: HttpResponse) -> bool:
    """A page opened after a change of language, once: the flag goes with it."""
    session = getattr(request, "session", None)
    if session is None or not session.get(_PURGE_FLAG):
        return False
    if request.method != "GET" or response.status_code != 200:
        return False
    if not response.get("Content-Type", "").startswith("text/html"):
        return False
    del session[_PURGE_FLAG]
    return True


class OfflinePagesMiddleware:
    """Mark the pages the service worker may keep, and the responses that make it forget."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        user = getattr(request, "user", None)
        is_page = response.get("Content-Type", "").startswith("text/html")
        signed_out = user is None or not user.is_authenticated
        if (
            response.status_code in (401, 403)
            or (signed_out and is_page)
            or _purge_asked(request, response)
        ):
            response.headers[HEADER] = PURGE
        elif _may_keep(request, response) and request.resolver_match is not None:
            home_id = request.resolver_match.kwargs.get("home_id")
            response.headers[HEADER] = cache_key(request, home_id)
            # Kept by this browser only, never by a shared cache.
            patch_cache_control(response, private=True)
        return response
