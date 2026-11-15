"""DRF authentication by `Authorization: Bearer ...`: connected apps, then API tokens.

`ConnectionAuthentication` comes first and answers every bearer token that starts with
`hca_`: the connection, or 401 when the token is unknown, expired or revoked. It never
answers "not mine" for such a token, so an expired one is a 401, never a fall back to the
session cookie of the same browser. A connection opens the sync API only: on a view without
`sync_api = True` it is refused with 403 `connection_outside_sync`, before the view or its
permissions run (docs/adr/0078). Any other bearer token goes on to `ApiTokenAuthentication`,
unchanged.
"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from oauth2_provider.contrib.rest_framework import OAuth2Authentication
from oauth2_provider.models import AccessToken
from rest_framework import authentication, exceptions
from rest_framework.request import Request

from housecairn.accounts.models import User
from housecairn.apitokens import connections, services
from housecairn.apitokens.models import ApiToken, ConnectionAccessToken
from housecairn.apitokens.oauth import ACCESS_PREFIX, checksum
from housecairn.core.language import activate_for


class CodedPermissionDenied(exceptions.PermissionDenied):
    """A 403 whose body carries a `code` the app words itself (`homes.scoping` writes it)."""

    def __init__(self, detail: Any, code: str) -> None:
        super().__init__(detail, code)
        self.housecairn_code = code


def connection_outside_sync() -> CodedPermissionDenied:
    return CodedPermissionDenied(
        _("An access token of a connected app opens the sync API only."),
        "connection_outside_sync",
    )


def check_view(view: Any) -> None:
    """The first lock: a connection is refused on a view without `sync_api = True`."""
    if not getattr(view, "sync_api", False):
        raise connection_outside_sync()


def _bearer(request: Request) -> str | None:
    header = authentication.get_authorization_header(request).split()
    if len(header) != 2 or header[0].lower() != b"bearer":
        return None
    try:
        return header[1].decode("ascii")
    except UnicodeDecodeError:
        return None


class ConnectionAuthentication(OAuth2Authentication):  # type: ignore[misc]
    keyword = "Bearer"

    def authenticate(self, request: Request) -> tuple[User, Any] | None:
        raw = _bearer(request)
        if raw is None or not raw.startswith(ACCESS_PREFIX):
            return None
        # Looked up from the header alone, by the toolkit's own digest: its `verify_request`
        # would read the body first, so a view's parser could answer before this class does.
        access_token = (
            AccessToken.objects.select_related("user").filter(token_checksum=checksum(raw)).first()
        )
        if access_token is None or access_token.is_expired():
            raise exceptions.AuthenticationFailed(_("Invalid, expired or revoked token."))
        user = access_token.user
        link = (
            ConnectionAccessToken.objects.select_related("connection__user")
            .filter(access_token=access_token)
            .first()
        )
        # No token resolves to no connection: an access token made outside a consent is
        # unknown here.
        if link is None or not link.connection.is_active or user is None or not user.is_active:
            raise exceptions.AuthenticationFailed(_("Invalid, expired or revoked token."))
        if not connections.mobile_app_enabled():
            raise CodedPermissionDenied(
                _("This instance does not allow the mobile app."), "mobile_app_disabled"
            )
        check_view((request.parser_context or {}).get("view"))
        access_token.housecairn_connection = link.connection
        connections.touch(link.connection)
        # The person's own language, as for API tokens (docs/adr/0074).
        activate_for(request._request, user)
        return user, access_token

    def authenticate_header(self, request: Request) -> str:
        return self.keyword


class ApiTokenAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request: Request) -> tuple[User, ApiToken] | None:
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None
        if len(header) != 2:
            raise exceptions.AuthenticationFailed(_("Invalid token header."))
        try:
            raw = header[1].decode("ascii")
        except UnicodeDecodeError as exc:
            raise exceptions.AuthenticationFailed(_("Invalid token header.")) from exc
        token = services.authenticate(raw)
        if token is None:
            raise exceptions.AuthenticationFailed(_("Invalid, expired or revoked token."))
        # The person's own language wins over `Accept-Language`, as on the pages
        # (docs/adr/0074): the answer and its errors are worded in it.
        activate_for(request._request, token.user)
        return token.user, token

    def authenticate_header(self, request: Request) -> str:
        return self.keyword
