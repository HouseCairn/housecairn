"""The OAuth endpoints of the mobile app at `/o/` (docs/adr/0078).

Only three of django-oauth-toolkit's views are mounted: the consent page (`authorize/`), the
token endpoint and the revocation endpoint. Each applies the transport rule and the instance
setting Mobile app before anything else. No other client can be registered.
"""

from typing import Any
from urllib.parse import urlsplit

from allauth.core import ratelimit
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from oauth2_provider import views as dot_views
from oauth2_provider.exceptions import OAuthToolkitError
from oauth2_provider.models import get_application_model
from oauthlib.oauth2.rfc6749.errors import InvalidRequestError

from housecairn.accounts.models import User
from housecairn.apitokens import connections, transport
from housecairn.apitokens.forms import ConsentForm
from housecairn.apitokens.oauth import SCOPE_HOMES_CREATE
from housecairn.homes import services as home_services
from housecairn.homes import sso


def _refused(request: HttpRequest, *, page: bool) -> HttpResponse | None:
    reason = transport.refusal(request)
    if reason is not None:
        code = "insecure_transport"
    elif not connections.mobile_app_enabled():
        reason, code = _("This instance does not allow the mobile app."), "mobile_app_disabled"
    else:
        return None
    if page:
        return TemplateResponse(
            request, "apitokens/consent_refused.html", {"reason": reason}, status=403
        )
    return JsonResponse({"detail": reason, "code": code}, status=403)


@method_decorator(never_cache, name="dispatch")
class ConsentView(dot_views.AuthorizationView):  # type: ignore[misc]
    """The page of Housecairn where a person connects a phone, never skipped."""

    template_name = "apitokens/consent.html"
    form_class = ConsentForm
    request: HttpRequest

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        refused = _refused(request, page=True)
        if refused is not None:
            return refused
        response: HttpResponse = super().dispatch(request, *args, **kwargs)
        return response

    def _scopes(self) -> list[str]:
        if self.request.method == "POST":
            return str(self.request.POST.get("scope", "")).split()
        scopes = self.oauth2_data.get("scopes", []) if hasattr(self, "oauth2_data") else []
        return list(scopes)

    def get_initial(self) -> dict[str, Any]:
        initial: dict[str, Any] = super().get_initial()
        initial["device_name"] = connections.clean_name(
            str(self.request.GET.get("device_name", ""))
        )
        return initial

    def get_form_kwargs(self) -> dict[str, Any]:
        return {
            **super().get_form_kwargs(),
            "user": self.request.user,
            "signed_in_with": sso.signed_in_providers(self.request),
            "providers": sso.declared_providers(self.request),
            "asks_homes_create": SCOPE_HOMES_CREATE in self._scopes(),
        }

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context: dict[str, Any] = super().get_context_data(**kwargs)
        form = context.get("form")
        name = form["device_name"].value() if form is not None else ""
        context.update(
            device_name=connections.clean_name(str(name or "")),
            server=self.request.get_host(),
            insecure=not self.request.is_secure(),
        )
        return context

    def form_valid(self, form: ConsentForm) -> HttpResponse:
        application = get_application_model().objects.get(client_id=form.cleaned_data["client_id"])
        credentials: dict[str, Any] = {
            "client_id": form.cleaned_data.get("client_id"),
            "redirect_uri": form.cleaned_data.get("redirect_uri"),
            "response_type": form.cleaned_data.get("response_type"),
            "state": form.cleaned_data.get("state"),
            "code_challenge": form.cleaned_data.get("code_challenge"),
            "code_challenge_method": form.cleaned_data.get("code_challenge_method"),
        }
        allow = bool(form.cleaned_data.get("allow"))
        scopes = str(form.cleaned_data.get("scope", "")).split()
        if allow and not (
            credentials["code_challenge"] and credentials["code_challenge_method"] == "S256"
        ):
            # PKCE `S256` only, checked before a connection exists: no connection, no email.
            plain: HttpResponse = self.error_response(
                OAuthToolkitError(
                    error=InvalidRequestError(
                        description='PKCE with "S256" is required.',
                        state=credentials.get("state"),
                    ),
                    redirect_uri=credentials["redirect_uri"],
                ),
                application,
            )
            return plain
        if allow:
            user: User = self.request.user  # type: ignore[assignment]
            may_create = bool(form.cleaned_data.get("may_create_homes"))
            if not may_create:
                scopes = [scope for scope in scopes if scope != SCOPE_HOMES_CREATE]
            try:
                connection = connections.create_connection(
                    self.request,
                    user,
                    name=form.cleaned_data.get("device_name", ""),
                    homes=form.cleaned_data["homes"],
                    may_create_homes=may_create,
                    signed_in_with=sso.signed_in_providers(self.request),
                )
            except ValidationError as exc:
                form.add_error("homes", exc)
                invalid: HttpResponse = self.form_invalid(form)
                return invalid
            except home_services.SsoRequiredError as exc:
                provider = sso.provider_named(exc.provider_id, self.request)
                form.add_error(
                    "homes",
                    _("%(home)s opens only after signing in through %(provider)s.")
                    % {"home": exc.home.name, "provider": provider.name},
                )
                locked: HttpResponse = self.form_invalid(form)
                return locked
            credentials["housecairn_connection"] = connection
        try:
            uri, _headers, _body, _status = self.create_authorization_response(
                request=self.request, scopes=" ".join(scopes), credentials=credentials, allow=allow
            )
        except OAuthToolkitError as error:
            if allow:
                connections.revoke(connection)
            failed: HttpResponse = self.error_response(error, application)
            return failed
        if allow and "code=" not in urlsplit(uri).query:
            # oauthlib answered with an error in the redirect: the connection has no code.
            connections.revoke(connection)
        self.success_url = uri
        redirected: HttpResponse = self.redirect(self.success_url, application)
        return redirected


class TokenView(dot_views.TokenView):  # type: ignore[misc]
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        refused = _refused(request, page=False)
        if refused is not None:
            return refused
        # Guessing at the token endpoint is limited as signing in is (docs/adr/0042).
        if not ratelimit.consume(request, action="login"):
            return JsonResponse(
                {"detail": _("Too many attempts. Try again later."), "code": "rate_limited"},
                status=429,
            )
        response: HttpResponse = super().dispatch(request, *args, **kwargs)
        return response


class RevokeTokenView(dot_views.RevokeTokenView):  # type: ignore[misc]
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        reason = transport.refusal(request)
        if reason is not None:
            return JsonResponse({"detail": reason, "code": "insecure_transport"}, status=403)
        # Revoking stays possible when the mobile app is off: a phone may always let go.
        response: HttpResponse = super().dispatch(request, *args, **kwargs)
        return response


app_name = "oauth2_provider"

urlpatterns = [
    path("authorize/", ConsentView.as_view(), name="authorize"),
    path("token/", TokenView.as_view(), name="token"),
    path("revoke_token/", RevokeTokenView.as_view(), name="revoke-token"),
]
