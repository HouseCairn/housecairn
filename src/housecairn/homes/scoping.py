"""Shared mechanism that limits web and API views to the user's homes."""

from typing import Any
from uuid import UUID

from allauth.core.internal import ratelimit
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import models
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.utils import translation
from django.utils.translation import gettext as _
from rest_framework import exceptions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import exception_handler

from housecairn.accounts import services as account_services
from housecairn.apitokens.credentials import Credential, connection_of, credential_of
from housecairn.homes import emails, services, sso
from housecairn.homes.models import Home, Invitation
from housecairn.instance.config import get_setting


def homes_for_request(request: HttpRequest | Request) -> models.QuerySet[Home]:
    """Homes the request's user belongs to, narrowed by the API token if any.

    Includes homes that require a sign-in provider this session did not use:
    lists show them, locked. Anything that reads or changes a home goes
    through `HomeScopedMixin`, which only opens the others.
    """
    restrict_to: list[UUID] | None = None
    credential = credential_of(request)
    if credential is Credential.API_TOKEN:
        restrict_to = list(request.auth.homes.values_list("id", flat=True))  # type: ignore[union-attr]
    elif credential is Credential.CONNECTION:
        connection = connection_of(request)
        assert connection is not None  # noqa: S101 - a connection credential has one
        restrict_to = list(connection.homes.values_list("id", flat=True))
    return services.homes_for(request.user, restrict_to=restrict_to)


def open_homes_for_request(request: HttpRequest | Request) -> models.QuerySet[Home]:
    """Homes this request may read and change (docs/adr/0021)."""
    return services.open_homes(homes_for_request(request), sso.signed_in_providers(request))


class HomeScopedMixin:
    """For web and API views alike: `get_homes()`, and `get_home()` from the URL.

    A home the user cannot see answers 404, never 403, so its existence does
    not leak. A home of the user that requires a sign-in provider this session
    did not use raises `SsoRequiredError` (403, with the provider to use).
    """

    request: Any
    kwargs: dict[str, Any]
    home_url_kwarg = "home_id"

    def get_homes(self) -> models.QuerySet[Home]:
        return open_homes_for_request(self.request)

    def get_home(self, *, allow_locked: bool = False) -> Home:
        """The home from the URL; `allow_locked` skips the sign-in requirement.

        Only for what a member must always be able to do: leave the home, and
        lift a requirement whose provider the instance no longer offers.
        """
        home = get_object_or_404(
            homes_for_request(self.request), id=self.kwargs[self.home_url_kwarg]
        )
        if not allow_locked:
            services.check_open(home, sso.signed_in_providers(self.request))
        return home


class WebHomeScopedMixin(LoginRequiredMixin, HomeScopedMixin):
    """Web views: sign-in required, then the same scoping as the API.

    A home the person may open, read from the address, becomes the current home
    (`homes.current`): the sections then open on the home being looked at.
    """

    def get_home(self, *, allow_locked: bool = False) -> Home:
        home = super().get_home(allow_locked=allow_locked)
        if not allow_locked:
            from housecairn.homes import current

            current.remember(self.request, home)
        return home

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        try:
            response: HttpResponseBase = super().dispatch(request, *args, **kwargs)
            return response
        except services.SsoRequiredError as exc:
            return sso_required_response(request, exc)


def sso_required_response(request: HttpRequest, exc: services.SsoRequiredError) -> HttpResponse:
    provider = sso.provider_named(exc.provider_id, request)
    declared = any(p.id == exc.provider_id for p in sso.declared_providers(request))
    response = TemplateResponse(
        request,
        "homes/sso_required.html",
        {
            "home": exc.home,
            "provider": provider,
            "provider_declared": declared,
            "is_owner": services.is_owner(request.user, exc.home),
        },
        status=403,
    )
    return response.render()


class SsoRequiredApiError(exceptions.PermissionDenied):
    default_code = "sso_required"


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """DRF exception handler: `SsoRequiredError` becomes a 403 naming the provider."""
    from housecairn.demo import mode

    if isinstance(exc, mode.DemoRefusedError):
        # What the demonstration mode refuses (docs/adr/0071), with its reason.
        return Response({"detail": exc.message, "code": "demo"}, status=status.HTTP_403_FORBIDDEN)
    code = getattr(exc, "housecairn_code", None)
    if isinstance(exc, exceptions.PermissionDenied) and isinstance(code, str):
        # The refusals of the sync API's boundary and transport rule (docs/adr/0078).
        return Response({"detail": exc.detail, "code": code}, status=exc.status_code)
    if isinstance(exc, services.SsoRequiredError):
        return Response(
            {
                "detail": _("This home requires signing in through %(provider)s.")
                % {"provider": sso.provider_named(exc.provider_id).name},
                "code": "sso_required",
                "provider": exc.provider_id,
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, services.DeliveryFailedError):
        return Response(
            {
                "detail": _(
                    "The invitation is saved, but the email could not be sent. Send it again later."
                ),
                "code": "delivery_failed",
                "id": str(exc.invitation.pk),
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return exception_handler(exc, context)


# ---------- invitations: delivery and rate limits ----------


def site_url(request: HttpRequest | Request) -> str:
    """Scheme and host for links sent away from the site (docs/adr/0020)."""
    configured: str = get_setting("site_url")
    if configured:
        return configured
    return f"{request.scheme}://{request.get_host()}"


def delivery_for(request: HttpRequest | Request) -> services.Delivery:
    """Send invitations by email when the instance can; otherwise hand the link back."""
    if not account_services.email_is_configured():
        return services.Delivery()
    base = site_url(request)
    language = translation.get_language()

    def send(invitation: Invitation, raw: str) -> None:
        emails.send_invitation(invitation, emails.invitation_link(base, raw), language=language)

    return services.Delivery(send=send)


def invitation_link(request: HttpRequest | Request, raw: str) -> str:
    return emails.invitation_link(site_url(request), raw)


def allow(request: HttpRequest | Request, action: str, *, key: str | None = None) -> bool:
    """Consume one use of a rate limit of `ACCOUNT_RATE_LIMITS` (django-allauth).

    The client address is REMOTE_ADDR, set by ForwardedHeadersMiddleware from a
    trusted proxy only (docs/adr/0012). GET requests count too: opening an
    invitation link is what a guesser would repeat. The public helper of
    django-allauth skips GET, hence its internal function, pinned by the lock
    file and covered by tests/test_invitations.py.
    """
    django_request = request._request if isinstance(request, Request) else request
    user = request.user if request.user.is_authenticated else None
    usage = ratelimit.consume(
        django_request, app="allauth.account", action=action, user=user, key=key, limit_get=True
    )
    return usage is not None
