"""The mobile app's sign-in, played by the test client: consent, then the token endpoint."""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from django.test import Client
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.apitokens.models import AppConnection
from housecairn.apitokens.oauth import CLIENT_ID, REDIRECT_URI
from housecairn.homes.models import Home


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


def authorize_params(challenge: str, *, scope: str = "sync", **extra: str) -> dict[str, str]:
    return {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "state": "s-123",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": scope,
        **extra,
    }


def consent(
    client: Client,
    homes: list[Home],
    *,
    scope: str = "sync",
    device_name: str = "Test phone",
    allow: bool = True,
    may_create_homes: bool = False,
    secure: bool = True,
    **request: Any,
) -> tuple[Any, str]:
    """Open the consent page, post it, and return the answer and the PKCE verifier."""
    verifier, challenge = pkce_pair()
    params = authorize_params(challenge, scope=scope, device_name=device_name)
    page = client.get(reverse("oauth2_provider:authorize"), params, secure=secure, **request)
    assert page.status_code == 200, page.content
    data: dict[str, Any] = {
        key: value for key, value in params.items() if key not in ("device_name",)
    }
    data["device_name"] = page.context["form"]["device_name"].value()
    data["homes"] = [str(home.id) for home in homes]
    if allow:
        data["allow"] = "1"
    if may_create_homes:
        data["may_create_homes"] = "on"
    answer = client.post(reverse("oauth2_provider:authorize"), data, secure=secure, **request)
    return answer, verifier


def code_of(answer: Any) -> str:
    assert answer.status_code == 302, answer.content
    query = parse_qs(urlsplit(answer["Location"]).query)
    return query["code"][0]


def exchange(
    client: Client, code: str, verifier: str, *, secure: bool = True, **request: Any
) -> Any:
    return client.post(
        reverse("oauth2_provider:token"),
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        },
        secure=secure,
        **request,
    )


def refresh(client: Client, refresh_token: str, *, secure: bool = True) -> Any:
    return client.post(
        reverse("oauth2_provider:token"),
        {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLIENT_ID},
        secure=secure,
    )


@dataclass
class Connected:
    connection: AppConnection
    access: str
    refresh: str


def connect(
    user: User, homes: list[Home], *, scope: str = "sync", may_create_homes: bool = False
) -> Connected:
    """A phone connected for `user`, through the real pages, with a fresh client."""
    client = Client()
    client.force_login(user)
    answer, verifier = consent(client, homes, scope=scope, may_create_homes=may_create_homes)
    tokens = exchange(Client(), code_of(answer), verifier)
    assert tokens.status_code == 200, tokens.content
    body = tokens.json()
    connection = AppConnection.objects.filter(user=user).order_by("-created_at").first()
    assert connection is not None
    return Connected(connection, body["access_token"], body["refresh_token"])
