"""A connection opens the sync API only, and only a connection opens it (docs/adr/0078).

Every route of the API is listed from the URL resolver, with every method its view allows: an
access token of a connection gets 403 `connection_outside_sync` on each route outside
`/api/v1/sync/` (the code proves the refusal came before the view, so a 404 or a 400 is not a
pass), and an API token of each scope or a session gets 403 `sync_needs_connection` on each
route of `/api/v1/sync/`. Forgetting a route refuses the connection, never lets it through.
"""

import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import yaml
from django.core.cache import cache
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, resolve
from oauth2_provider.models import AccessToken
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from housecairn.accounts.models import User
from housecairn.apitokens import services
from housecairn.apitokens.credentials import Credential, UnknownCredentialError, credential_of
from housecairn.apitokens.models import ApiToken, Scope
from housecairn.apitokens.permissions import CredentialFitsView
from housecairn.homes.models import Home
from tests.oauth_support import connect


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    """The token endpoint counts as a sign-in (docs/adr/0042): each test starts from zero."""
    cache.clear()
    yield
    cache.clear()


pytestmark = pytest.mark.django_db

METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


def _segment(entry: Any) -> str:
    text = str(entry.pattern)
    text = text.removeprefix("^")
    return text.removesuffix("\\Z").removesuffix("$")


def _walk(patterns: list[Any], prefix: str = "") -> Iterator[tuple[str, Any]]:
    for entry in patterns:
        if isinstance(entry, URLResolver):
            yield from _walk(entry.url_patterns, prefix + _segment(entry))
        elif isinstance(entry, URLPattern):
            yield prefix + _segment(entry), entry.callback


def _value(name: str, regex: str, home: Home) -> str:
    """A value of a URL argument that its pattern accepts: the test home for `home_id`."""
    if name == "home_id":
        return str(home.id)
    for candidate in (str(uuid.uuid4()), "rooms", "x", "1", "json"):
        if re.fullmatch(regex, candidate):
            return candidate
    raise AssertionError(f"no value for {name}: {regex}")


def _concrete(route: str, home: Home) -> str:
    """A path for a route pattern, with the test home and made-up ids for its arguments."""
    converters = {
        "uuid": "[0-9a-f-]{36}",
        "slug": "[-a-zA-Z0-9_]+",
        "str": "[^/]+",
        "int": "[0-9]+",
    }
    path = route
    path = re.sub(
        r"<(uuid|slug|str|int):([a-z_]+)>",
        lambda m: _value(m.group(2), converters[m.group(1)], home),
        path,
    )
    path = re.sub(
        r"\(\?P<([a-z_]+)>((?:[^()]|\([^()]*\))*)\)",
        lambda m: _value(m.group(1), m.group(2), home),
        path,
    )
    return "/" + path.replace("\\.", ".")


def api_routes(home: Home) -> list[tuple[str, str, Any]]:
    """(path, method, view class) of every route of `/api/v1/` and `/api/schema/`."""
    found: list[tuple[str, str, Any]] = []
    for route, callback in _walk(get_resolver().url_patterns):
        if not (route.startswith("api/v1/") or route.startswith("api/schema")):
            continue
        view = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        if view is None:
            continue
        actions = getattr(callback, "actions", None)
        if actions:
            methods = [m for m in actions if m in METHODS]
        else:
            methods = [m for m in METHODS if hasattr(view, m)]
        allowed = getattr(view, "http_method_names", METHODS)
        path = _concrete(route, home)
        match = resolve(path)
        assert match.func is callback, (route, path)
        found.extend((path, m, view) for m in methods if m in allowed)
    return found


def is_sync(path: str) -> bool:
    return path.startswith("/api/v1/sync/")


def body_of(answer: Any) -> Any:
    """The body of an error, JSON or the YAML of the schema's own renderer."""
    return yaml.safe_load(answer.content)


def call(client: Client, method: str, path: str, **headers: Any) -> Any:
    kwargs: dict[str, Any] = {"secure": True, **headers}
    if method in ("post", "put", "patch"):
        kwargs.update(data="{}", content_type="application/json")
    return getattr(client, method)(path, **kwargs)


@pytest.fixture
def routes(alice_home: Home) -> list[tuple[str, str, Any]]:
    found = api_routes(alice_home)
    assert len(found) > 100
    assert any(is_sync(path) for path, _m, _v in found)
    return found


def test_every_sync_route_declares_it(routes: list[tuple[str, str, Any]]) -> None:
    for path, _method, view in routes:
        assert getattr(view, "sync_api", False) == is_sync(path), path


def test_every_view_carries_the_permission(routes: list[tuple[str, str, Any]]) -> None:
    factory = APIRequestFactory()
    for path, method, view in routes:
        if not issubclass(view, APIView):
            continue
        instance = view()
        instance.request = factory.generic(method.upper(), path)
        instance.action = None
        instance.kwargs = {}
        instance.format_kwarg = None
        assert any(isinstance(p, CredentialFitsView) for p in instance.get_permissions()), (
            path,
            method,
        )


def test_a_connection_is_refused_outside_sync(
    alice: User, alice_home: Home, routes: list[tuple[str, str, Any]]
) -> None:
    phone = connect(alice, [alice_home], scope="sync homes.create", may_create_homes=True)
    client = Client()
    checked = 0
    for path, method, _view in routes:
        if is_sync(path):
            continue
        answer = call(client, method, path, HTTP_AUTHORIZATION=f"Bearer {phone.access}")
        assert answer.status_code == 403, (method, path, answer.status_code)
        if method != "head":
            assert body_of(answer)["code"] == "connection_outside_sync", (method, path)
        checked += 1
    assert checked > 100


@pytest.mark.parametrize("scope", [Scope.READ, Scope.WRITE, Scope.EVENTS])
def test_an_api_token_is_refused_on_sync(
    alice: User, alice_home: Home, routes: list[tuple[str, str, Any]], scope: Scope
) -> None:
    _token, raw = services.issue_token(alice, name="t", scopes=[scope], homes=[alice_home])
    client = Client()
    for path, method, _view in routes:
        if not is_sync(path) or method == "options":
            continue
        answer = call(client, method, path, HTTP_AUTHORIZATION=f"Bearer {raw}")
        assert answer.status_code == 403, (method, path)
        if method != "head":
            assert body_of(answer)["code"] == "sync_needs_connection", (method, path)


def test_a_session_is_refused_on_sync(alice: User, routes: list[tuple[str, str, Any]]) -> None:
    client = Client()
    client.force_login(alice)
    for path, method, _view in routes:
        if not is_sync(path) or method == "options":
            continue
        answer = call(client, method, path)
        assert answer.status_code == 403, (method, path)
        if method != "head":
            assert body_of(answer)["code"] == "sync_needs_connection", (method, path)


def test_info_without_a_credential_is_open() -> None:
    assert Client().get("/api/v1/sync/info", secure=True).status_code == 200


class TestCredentialOf:
    def _request(self, auth: object) -> Any:
        request = APIRequestFactory().get("/")
        request.auth = auth  # type: ignore[attr-defined]
        return request

    def test_session(self) -> None:
        assert credential_of(self._request(None)) is Credential.SESSION

    def test_api_token(self, alice: User, alice_home: Home) -> None:
        token, _raw = services.issue_token(alice, name="t", scopes=["read"], homes=[alice_home])
        assert credential_of(self._request(token)) is Credential.API_TOKEN

    def test_connection(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        token = AccessToken.objects.get()
        token.housecairn_connection = phone.connection
        assert credential_of(self._request(token)) is Credential.CONNECTION

    @pytest.mark.parametrize("value", ["hc_x", object(), 1, ApiToken])
    def test_anything_else_raises(self, value: object) -> None:
        with pytest.raises(UnknownCredentialError):
            credential_of(self._request(value))

    def test_an_access_token_without_its_connection_raises(
        self, alice: User, alice_home: Home
    ) -> None:
        connect(alice, [alice_home])
        with pytest.raises(UnknownCredentialError):
            credential_of(self._request(AccessToken.objects.get()))


# ---------- each lock alone (docs/adr/0078): any one of them refuses the connection ----------


def _without_lock_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("housecairn.apitokens.authentication.check_view", lambda view: None)


def _without_lock_two(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CredentialFitsView, "has_permission", lambda self, request, view: True)


@pytest.mark.parametrize("remove", [_without_lock_one, _without_lock_two], ids=["lock-2", "lock-1"])
def test_one_lock_alone_refuses_every_route(
    alice: User,
    alice_home: Home,
    routes: list[tuple[str, str, Any]],
    remove: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the other lock taken out, each of the first two still refuses every route."""
    phone = connect(alice, [alice_home], scope="sync homes.create", may_create_homes=True)
    remove(monkeypatch)
    client = Client()
    for path, method, _view in routes:
        if is_sync(path):
            continue
        answer = call(client, method, path, HTTP_AUTHORIZATION=f"Bearer {phone.access}")
        assert answer.status_code == 403, (method, path, answer.status_code)
        if method != "head":
            assert body_of(answer)["code"] == "connection_outside_sync", (method, path)


def test_lock_three_alone_never_reads_a_connection_as_a_person(
    alice: User, alice_home: Home, bob: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With both other locks out, the tests of a person still refuse what only a person does."""
    alice.is_staff = alice.is_superuser = True
    alice.save(update_fields=["is_staff", "is_superuser"])
    from housecairn.homes import services as home_services
    from housecairn.homes.models import Membership, Role

    shared = home_services.create_home(alice, name="Shared")
    member = Membership.objects.create(home=shared, user=bob, role=Role.MEMBER)
    phone = connect(alice, [alice_home, shared], scope="sync homes.create", may_create_homes=True)
    _without_lock_one(monkeypatch)
    _without_lock_two(monkeypatch)
    client = Client()
    bearer = {"HTTP_AUTHORIZATION": f"Bearer {phone.access}"}
    home = f"/api/v1/homes/{alice_home.id}/"
    refused = [
        ("post", "/api/v1/homes/", {"name": "New"}),
        ("post", "/api/v1/homes/restore/", {}),
        ("post", f"{home}delete/", {}),
        ("post", f"{home}unlock/", {}),
        ("post", f"{home}archive/", {}),
        ("post", f"{home}leave/", {}),
        ("get", f"{home}audit/", None),
        ("post", f"{home}export/", {}),
        ("get", "/api/v1/me/tokens/", None),
        ("post", "/api/v1/me/tokens/", {"name": "t", "scopes": ["write"]}),
        ("post", "/api/v1/account/delete/", {}),
        ("get", "/api/v1/instance/settings/", None),
        ("patch", f"/api/v1/homes/{shared.id}/members/{member.id}/", {"role": Role.OWNER}),
        ("post", f"/api/v1/homes/{shared.id}/members/{member.id}/make-owner/", {}),
    ]
    for method, path, data in refused:
        kwargs: dict[str, Any] = {"secure": True, **bearer}
        if data is not None:
            kwargs.update(data=data, content_type="application/json")
        answer = getattr(client, method)(path, **kwargs)
        assert answer.status_code in (403, 404), (method, path, answer.status_code)
    assert Home.objects.filter(name="New").count() == 0
    assert not ApiToken.objects.exists()
    alice_home.refresh_from_db()
    assert alice_home.deleted_at is None
    assert alice_home.archived_at is None
    member.refresh_from_db()
    assert member.role == Role.MEMBER
