"""Response headers set for every response, whichever view (or none) produced it."""

import re
import secrets
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext as _

from housecairn.core import navigation

# Browser features the application never uses, and the camera for its own pages only: the Scan
# button of a barcode field reads it (docs/adr/0083). Photos are sent through a file field; nothing
# records sound, reads a position, takes payments or talks to USB.
PERMISSIONS_POLICY = "camera=(self), microphone=(), geolocation=(), payment=(), usb=()"


class PermissionsPolicyMiddleware:
    """Add `Permissions-Policy` to every response (defence in depth next to the CSP)."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        return response


class RequestTooLargeMiddleware:
    """The screen and its reason, not the bare 400 page, for a post too large to read.

    For a view whose class offers `request_too_large(request, **kwargs)`: a post declared
    larger than its `max_request_bytes`, or whose form fields Django refuses to read
    (`RequestDataTooBig`, past `DATA_UPLOAD_MAX_MEMORY_SIZE`), gets that response. Before
    `CsrfViewMiddleware`, whose check reads the form first and would answer 400.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_view(
        self,
        request: HttpRequest,
        view: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> HttpResponse | None:
        view_class = getattr(view, "view_class", None)
        answer = getattr(view_class, "request_too_large", None)
        if request.method != "POST" or answer is None:
            return None
        limit = getattr(view_class, "max_request_bytes", None)
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            declared = 0
        if limit is not None and declared > limit:
            response: HttpResponse = answer(request, **kwargs)
            return response
        try:
            request.POST  # noqa: B018 - read here, where its refusal can be answered
        except RequestDataTooBig:
            # As Django's handler does: the form reads as empty from now on, rather than
            # raising again in whatever renders the page (the `next` field, say).
            request._mark_post_parse_error()  # type: ignore[attr-defined]
            response = answer(request, **kwargs)
            return response
        return None


# The hidden field of the one-time token of a creation form.
FORM_TOKEN_FIELD = "form_token"  # noqa: S105 - a field name, not a secret
# The tokens already used, in the session: the last ones are enough to stop a second
# sending, which comes seconds or minutes after the first.
USED_KEY = "housecairn.used_form_tokens"
USED_KEPT = 50
# The same in the cache, whose `add` is atomic: two sendings at the same instant, a
# double tap, cannot both pass. Shared by every worker when the cache is.
CACHE_PREFIX = "housecairn:form-token:"
CACHE_SECONDS = 24 * 60 * 60

# The hidden CSRF field Django renders in every form that posts.
_CSRF_FIELD = re.compile(rb'(<input type="hidden" name="csrfmiddlewaretoken" value="[^"]*">)')
# The opening tag of a form that posts, and what tells it already has a say on autofill.
_POST_FORM = re.compile(rb"<form\b[^>]*\bmethod=\"post\"[^>]*>", re.IGNORECASE)
_AUTOCOMPLETE = re.compile(rb"\sautocomplete=", re.IGNORECASE)


def is_creation_form(request: HttpRequest) -> bool:
    """A creation form: by the convention of the routes, an address that ends in `new/`."""
    match = request.resolver_match
    return match is not None and match.route.endswith("new/")


class CreationFormMiddleware:
    """`no-store` and a one-time token on every creation form (NAV-12).

    The browser's back button must give what the back link gives
    (docs/developer/design-guide.md, "The bar, and the browser's back button"): it must
    not show a form already sent from its cache, and a form sent again must not create
    a duplicate. So every page of a creation form is sent with `Cache-Control: no-store`
    and each of its forms gets a hidden token; a post that brings back a token already
    used is refused, and the person goes back where the form leads, with a message.
    A post without a token (an old page, a test client) is let through as before.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if is_creation_form(request):
            response.headers["Cache-Control"] = "no-store"
            _add_token(response)
        return response

    def process_view(
        self,
        request: HttpRequest,
        view: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> HttpResponse | None:
        # After CsrfViewMiddleware: a forged post is refused before this reads it.
        if request.method != "POST" or not is_creation_form(request) or not _body_read(request):
            return None
        token = request.POST.get(FORM_TOKEN_FIELD, "")
        if not token or _first_use(request, token[:64]):
            return None
        messages.info(request, _("This form was already sent. Nothing was added again."))
        return HttpResponseRedirect(navigation.back_url(request, reverse("index")))


def _body_read(request: HttpRequest) -> bool:
    """The form may be read here without reading a body a view would refuse unread.

    The CSRF check has already read it (`_post`, the case of every browser post), or it
    is a small form without files: an upload view checks the declared size first and
    answers 413 without reading (rooms, tasks, stock), so a multipart body is left to it.
    """
    if hasattr(request, "_post"):
        return True
    if request.content_type == "multipart/form-data":
        return False
    declared = int(request.META.get("CONTENT_LENGTH") or 0)
    return declared <= settings.DATA_UPLOAD_MAX_MEMORY_SIZE


def _first_use(request: HttpRequest, token: str) -> bool:
    """Record `token` as used; False when it already was."""
    used: list[str] = list(request.session.get(USED_KEY, []))
    if token in used:
        return False
    if not cache.add(CACHE_PREFIX + token, 1, timeout=CACHE_SECONDS):
        return False
    request.session[USED_KEY] = [*used, token][-USED_KEPT:]
    return True


def _without_autofill(match: re.Match[bytes]) -> bytes:
    tag = match.group(0)
    if _AUTOCOMPLETE.search(tag):
        return tag
    return tag[:-1] + b' autocomplete="off">'


def _add_token(response: HttpResponse) -> None:
    """Give each form of an HTML page a fresh one-time token, next to its CSRF field."""
    if response.streaming or not response.get("Content-Type", "").startswith("text/html"):
        return
    if hasattr(response, "render") and not getattr(response, "is_rendered", True):
        response.render()
    token = secrets.token_urlsafe(24).encode()
    # Chromium restores what was typed when the person comes back to the page; sent
    # again, it would create a second record. The token does not stop that sending (the
    # page asked again after the back button has a new token), so the fields start empty.
    original = response.content
    body = _POST_FORM.sub(_without_autofill, original)
    field = b'<input type="hidden" name="' + FORM_TOKEN_FIELD.encode() + b'" value="'
    content = _CSRF_FIELD.sub(lambda m: m.group(1) + field + token + b'">', body)
    if content != original:
        response.content = content
        if response.has_header("Content-Length"):
            response.headers["Content-Length"] = str(len(content))
