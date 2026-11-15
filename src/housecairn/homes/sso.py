"""Single sign-on providers, as far as homes are concerned (docs/adr/0021).

Providers are declared by the instance administrator only (the `OIDC_*`
variables, read by django-allauth). A home can require one of them; whether a
request satisfies that is read from the authentication records django-allauth
keeps in the session, never from anything the client sends.
"""

from dataclasses import dataclass

from allauth.account.authentication import get_authentication_records
from allauth.socialaccount.adapter import get_adapter
from django.http import HttpRequest
from rest_framework.request import Request

from housecairn.apitokens.credentials import (
    Credential,
    api_token_of,
    connection_of,
    credential_of,
)


@dataclass(frozen=True)
class Provider:
    id: str
    name: str


def declared_providers(request: HttpRequest | Request | None = None) -> list[Provider]:
    """The providers this instance offers, in declaration order."""
    apps = get_adapter().list_apps(request)
    return [Provider(id=app.provider_id or app.provider, name=app.name) for app in apps]


def provider_named(provider_id: str, request: HttpRequest | Request | None = None) -> Provider:
    """The declared provider with this identifier; a placeholder if it is gone."""
    for provider in declared_providers(request):
        if provider.id == provider_id:
            return provider
    return Provider(id=provider_id, name=provider_id)


def signed_in_providers(request: HttpRequest | Request) -> frozenset[str]:
    """Providers through which this session signed in.

    A request authenticated by an API token has at most the provider recorded on
    the token: the one the session that created it had signed in through, when a
    home of the token required it (docs/adr/0057). A token made without one has
    none, and opens no home that requires a provider.
    """
    credential = credential_of(request)
    if credential is not Credential.SESSION:
        # An API token or a connection: the provider recorded when it was made.
        token = api_token_of(request) or connection_of(request)
        assert token is not None  # noqa: S101 - not a session, so one of the two
        return frozenset({token.signed_in_provider}) if token.signed_in_provider else frozenset()
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return frozenset()
    session_request = request._request if isinstance(request, Request) else request
    records = get_authentication_records(session_request, user)
    return frozenset(
        record["provider"]
        for record in records
        if isinstance(record, dict)
        and record.get("method") == "socialaccount"
        and isinstance(record.get("provider"), str)
    )
