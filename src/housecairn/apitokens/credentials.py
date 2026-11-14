"""What a request of the API was authenticated by (docs/adr/0078).

Three credentials reach a view of the API, and only three: a session (`request.auth` is
`None`), an API token (`ApiToken`) and an access token of a connected app (the access token
model of django-oauth-toolkit). A person is "a session", never "not an API token": anything
else raises, so that a fourth kind added later fails loudly instead of being read as a person.
"""

from enum import StrEnum
from typing import Any

from django.http import HttpRequest
from oauth2_provider.models import AbstractAccessToken
from rest_framework.request import Request

from housecairn.apitokens.models import ApiToken, AppConnection


class Credential(StrEnum):
    SESSION = "session"
    API_TOKEN = "api_token"  # noqa: S105 - a kind, not a password
    CONNECTION = "connection"


class UnknownCredentialError(TypeError):
    """`request.auth` holds a value none of the three credentials gives."""


def credential_of(request: HttpRequest | Request) -> Credential:
    auth: Any = getattr(request, "auth", None)
    if auth is None:
        return Credential.SESSION
    if isinstance(auth, ApiToken):
        return Credential.API_TOKEN
    if isinstance(auth, AbstractAccessToken) and isinstance(
        getattr(auth, "housecairn_connection", None), AppConnection
    ):
        return Credential.CONNECTION
    raise UnknownCredentialError(type(auth).__name__)


def is_session(request: HttpRequest | Request) -> bool:
    return credential_of(request) is Credential.SESSION


def api_token_of(request: HttpRequest | Request) -> ApiToken | None:
    """The API token of the request, or None for a session or a connection."""
    if credential_of(request) is Credential.API_TOKEN:
        token: ApiToken = request.auth  # type: ignore[union-attr,assignment]
        return token
    return None


def connection_of(request: HttpRequest | Request) -> AppConnection | None:
    """The connection of the request, or None for a session or an API token."""
    if credential_of(request) is Credential.CONNECTION:
        connection: AppConnection = request.auth.housecairn_connection  # type: ignore[union-attr]
        return connection
    return None
