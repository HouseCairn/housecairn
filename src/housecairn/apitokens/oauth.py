"""django-oauth-toolkit, fitted to the connections of the mobile app (docs/adr/0078).

- `access_token` and `refresh_token` make the secrets, with the prefixes `hca_` and `hcr_`;
- `TransportRuleCore` applies the transport rule (`apitokens.transport`) before oauthlib sees
  the request, and gives it the `https` address only for a request the rule accepted;
- `ConnectionValidator` binds every grant and token to exactly one `AppConnection`: the consent
  creates the connection and its grant, the exchange of the code carries it to the tokens,
  every rotation keeps it. A token without a connection, or of a revoked connection, is
  refused; a refresh token presented again after its rotation revokes the connection.
"""

import hashlib
import secrets
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from django.db import transaction
from oauth2_provider.models import AccessToken, Grant, RefreshToken
from oauth2_provider.oauth2_backends import OAuthLibCore
from oauth2_provider.oauth2_validators import OAuth2Validator

from housecairn.apitokens import transport
from housecairn.apitokens.models import (
    AppConnection,
    ConnectionAccessToken,
    ConnectionGrant,
    ConnectionRefreshToken,
)

ACCESS_PREFIX = "hca_"
REFRESH_PREFIX = "hcr_"
CLIENT_ID = "housecairn-mobile"
REDIRECT_URI = "app.housecairn.mobile:/oauth/callback"
SCOPE_SYNC = "sync"
SCOPE_HOMES_CREATE = "homes.create"


def access_token(request: Any, refresh_token: bool = False) -> str:
    return ACCESS_PREFIX + secrets.token_urlsafe(32)


def refresh_token(request: Any, refresh_token: bool = False) -> str:
    return REFRESH_PREFIX + secrets.token_urlsafe(32)


def checksum(raw: str) -> str:
    """The lookup digest django-oauth-toolkit keeps for a token."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TransportRuleCore(OAuthLibCore):  # type: ignore[misc]
    """oauthlib gets an `https` address only when the transport rule accepts the request."""

    def _get_escaped_full_path(self, request: Any) -> str:
        path: str = super()._get_escaped_full_path(request)
        absolute = str(request.build_absolute_uri(path))
        if request.is_secure() or not transport.allowed(request):
            return absolute
        parts = urlsplit(absolute)
        return urlunsplit(("https", *parts[1:]))


def _usable(connection: AppConnection | None) -> bool:
    from housecairn.apitokens import connections

    return (
        connection is not None
        and connection.is_active
        and connection.user.is_active
        and connections.mobile_app_enabled()
    )


class ConnectionValidator(OAuth2Validator):  # type: ignore[misc]
    def _create_authorization_code(self, request: Any, code: Any, expires: Any = None) -> Grant:
        connection = getattr(request, "housecairn_connection", None)
        if not isinstance(connection, AppConnection):
            # Only the consent page of Housecairn makes a code, and it always names the
            # connection it created: anything else gets no code.
            raise ValueError("an authorization code needs its connection")
        grant: Grant = super()._create_authorization_code(request, code, expires)
        ConnectionGrant.objects.create(grant=grant, connection=connection)
        return grant

    def validate_code(
        self, client_id: str, code: str, client: Any, request: Any, *args: Any, **kwargs: Any
    ) -> bool:
        if not super().validate_code(client_id, code, client, request, *args, **kwargs):
            return False
        link = (
            ConnectionGrant.objects.select_related("connection__user")
            .filter(grant__code=code, grant__application=client)
            .first()
        )
        connection = link.connection if link is not None else None
        if not _usable(connection):
            return False
        request.housecairn_connection = connection
        return True

    def validate_refresh_token(
        self, refresh_token: str, client: Any, request: Any, *args: Any, **kwargs: Any
    ) -> bool:
        valid = super().validate_refresh_token(refresh_token, client, request, *args, **kwargs)
        link = (
            ConnectionRefreshToken.objects.select_related("connection__user", "refresh_token")
            .filter(refresh_token__token_checksum=checksum(refresh_token))
            .order_by("-refresh_token__revoked")
            .first()
        )
        if not valid:
            if link is not None and link.refresh_token.revoked is not None:
                # A refresh token used again after its rotation, or after a revocation: one of
                # the two users is not the phone, so the whole connection goes.
                from housecairn.apitokens import connections

                connections.revoke(link.connection, reason="reuse")
            return False
        connection = link.connection if link is not None else None
        if not _usable(connection):
            return False
        request.housecairn_connection = connection
        return True

    def _create_access_token(
        self, expires: Any, request: Any, token: Any, source_refresh_token: Any = None
    ) -> AccessToken:
        connection = getattr(request, "housecairn_connection", None)
        if not isinstance(connection, AppConnection):
            raise ValueError("an access token needs its connection")
        with transaction.atomic():
            created: AccessToken = super()._create_access_token(
                expires, request, token, source_refresh_token
            )
            ConnectionAccessToken.objects.create(access_token=created, connection=connection)
        return created

    def _create_refresh_token(
        self, request: Any, refresh_token_code: str, access_token: Any, previous_refresh_token: Any
    ) -> RefreshToken:
        connection = getattr(request, "housecairn_connection", None)
        if not isinstance(connection, AppConnection):
            raise ValueError("a refresh token needs its connection")
        with transaction.atomic():
            created: RefreshToken = super()._create_refresh_token(
                request, refresh_token_code, access_token, previous_refresh_token
            )
            ConnectionRefreshToken.objects.create(refresh_token=created, connection=connection)
        return created

    def revoke_token(
        self, token: str, token_type_hint: str | None, request: Any, *args: Any, **kwargs: Any
    ) -> None:
        """The app's own "Disconnect": revoking its refresh token ends the connection."""
        link = (
            ConnectionRefreshToken.objects.select_related("connection")
            .filter(
                refresh_token__token_checksum=checksum(token),
                refresh_token__application=request.client,
            )
            .first()
        )
        super().revoke_token(token, token_type_hint, request, *args, **kwargs)
        if link is not None:
            from housecairn.apitokens import connections

            connections.revoke(link.connection, reason="app")
