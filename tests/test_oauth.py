"""OAuth 2 with PKCE for the mobile app: settings, consent, tokens, rotation, revocation
(docs/adr/0078)."""

from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.db import connection as db
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application, Grant, RefreshToken
from oauth2_provider.settings import oauth2_settings

from housecairn.accounts.models import User
from housecairn.apitokens import connections, services
from housecairn.apitokens.models import (
    AppConnection,
    ConnectionAccessToken,
    ConnectionRefreshToken,
    Scope,
)
from housecairn.apitokens.oauth import CLIENT_ID, REDIRECT_URI
from housecairn.audit.models import AuditEvent
from housecairn.homes.models import Home, Membership, Role
from housecairn.instance import settings_services
from tests.oauth_support import (
    authorize_params,
    code_of,
    connect,
    consent,
    exchange,
    pkce_pair,
    refresh,
)

pytestmark = pytest.mark.django_db

INFO = "/api/v1/sync/info"


def mobile_app(*, on: bool) -> None:
    admin = User.objects.create_user(
        email=f"admin-{on}@example.com", is_staff=True, is_superuser=True
    )
    settings_services.update(admin, {"mobile_app_enabled": on})


def info(token: str | None = None, client: Client | None = None, **extra: object) -> object:
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return (client or Client()).get(INFO, secure=True, **headers, **extra)  # type: ignore[arg-type]


class TestSettings:
    """Each value of the table of docs/adr/0078, read back from the settings."""

    EXPECTED = {
        "PKCE_REQUIRED": True,
        "COMPLIANT_BCP_RFC9700_PKCE_METHOD": True,
        "COMPLIANT_BCP_RFC9700_PKCE_REQUIRED": True,
        "COMPLIANT_BCP_RFC9700_TOKEN_STORAGE": True,
        "ROTATE_REFRESH_TOKEN": True,
        "REFRESH_TOKEN_REUSE_PROTECTION": True,
        "COMPLIANT_BCP_RFC9700_REFRESH_TOKEN": True,
        "REFRESH_TOKEN_GRACE_PERIOD_SECONDS": 0,
        "ALLOWED_REDIRECT_URI_SCHEMES": ["app.housecairn.mobile"],
        "COMPLIANT_BCP_RFC9700_REDIRECT_URI_SCHEME": True,
        "COMPLIANT_BCP_RFC9700_REDIRECT_URI_MATCHING": True,
        "COMPLIANT_BCP_RFC9700_IMPLICIT_GRANT": True,
        "COMPLIANT_BCP_RFC9700_PASSWORD_GRANT": True,
        "COMPLIANT_BCP_RFC9700_ACCESS_TOKEN_TRANSPORT": True,
        "COMPLIANT_BCP_RFC9700_AUTHZ_RESPONSE_ISS": True,
        "AUTHORIZATION_CODE_EXPIRE_SECONDS": 60,
        "ACCESS_TOKEN_EXPIRE_SECONDS": 3600,
        "REFRESH_TOKEN_EXPIRE_SECONDS": 90 * 24 * 3600,
        "REQUEST_APPROVAL_PROMPT": "force",
        "OIDC_ENABLED": False,
    }

    @pytest.mark.parametrize(("name", "value"), sorted(EXPECTED.items()))
    def test_value(self, name: str, value: object) -> None:
        assert settings.OAUTH2_PROVIDER[name] == value
        assert getattr(oauth2_settings, name) == value

    def test_generators_and_backend(self) -> None:
        assert settings.OAUTH2_PROVIDER["ACCESS_TOKEN_GENERATOR"].endswith("oauth.access_token")
        assert settings.OAUTH2_PROVIDER["REFRESH_TOKEN_GENERATOR"].endswith("oauth.refresh_token")
        assert settings.OAUTH2_PROVIDER["OAUTH2_BACKEND_CLASS"].endswith("TransportRuleCore")

    def test_insecure_transport_is_never_set(self) -> None:
        import os

        assert "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ
        for path in [*(settings.REPO_DIR / "src").rglob("*.py"), settings.REPO_DIR / "Dockerfile"]:
            for line in path.read_text().splitlines():
                if "OAUTHLIB_INSECURE_TRANSPORT" in line:
                    assert "environ" not in line, (path, line)
                    assert "ENV " not in line, (path, line)

    def test_deploy_check_has_no_error_of_the_toolkit(self) -> None:
        from django.core.checks import Error, run_checks

        found = [
            m
            for m in run_checks(include_deployment_checks=True)
            if m.id and m.id.startswith("oauth2_provider") and m.level >= Error.level
        ]
        assert found == []

    def test_one_public_client(self) -> None:
        client = Application.objects.get()
        assert client.client_id == CLIENT_ID
        assert client.client_type == Application.CLIENT_PUBLIC
        assert client.authorization_grant_type == Application.GRANT_AUTHORIZATION_CODE
        assert client.redirect_uris == REDIRECT_URI
        assert client.skip_authorization is False

    def test_only_three_endpoints(self) -> None:
        client = Client()
        for path in ("/o/introspect/", "/o/applications/", "/o/authorized-tokens/"):
            assert client.get(path, secure=True).status_code == 404


class TestConsent:
    def test_page_shows_server_homes_and_the_name_as_a_label(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        client.force_login(alice)
        _verifier, challenge = pkce_pair()
        name = "<b>Evil</b>\u202e\u0007" + "x" * 100
        page = client.get(
            reverse("oauth2_provider:authorize"),
            authorize_params(challenge, device_name=name),
            secure=True,
        )
        assert page.status_code == 200
        html = page.content.decode()
        assert "testserver" in html
        assert "Name given by the app" in html
        assert "<b>Evil</b>" not in html
        assert "&lt;b&gt;Evil&lt;/b&gt;" in html
        shown = page.context["device_name"]
        assert len(shown) == 60
        assert "\u202e" not in shown
        assert "\u0007" not in shown
        assert alice_home.name in html
        assert 'name="may_create_homes"' not in html
        assert "not encrypted" not in html

    def test_may_create_homes_only_when_asked(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        client.force_login(alice)
        _verifier, challenge = pkce_pair()
        page = client.get(
            reverse("oauth2_provider:authorize"),
            authorize_params(challenge, scope="sync homes.create"),
            secure=True,
        )
        assert 'name="may_create_homes"' in page.content.decode()

    def test_signed_out_goes_to_sign_in(self, client: Client) -> None:
        _verifier, challenge = pkce_pair()
        page = client.get(
            reverse("oauth2_provider:authorize"), authorize_params(challenge), secure=True
        )
        assert page.status_code == 302
        assert "/accounts/login/" in page["Location"]

    @override_settings(
        EMAIL_HOST="smtp.example.com",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ACCOUNT_EMAIL_NOTIFICATIONS=True,
    )
    def test_connect_creates_the_connection_audit_and_email(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        client.force_login(alice)
        answer, _verifier = consent(client, [alice_home], device_name="Pixel")
        code_of(answer)
        assert answer["Location"].startswith(REDIRECT_URI + "?")
        assert "state=s-123" in answer["Location"]
        assert "iss=" in answer["Location"]
        found = AppConnection.objects.get()
        assert found.user == alice
        assert found.name == "Pixel"
        assert list(found.homes.all()) == [alice_home]
        assert found.may_create_homes is False
        assert AuditEvent.objects.filter(action="appconnection.created").count() == 1
        assert len(mail.outbox) == 1
        assert "phone was connected" in mail.outbox[0].subject

    def test_cancel_returns_access_denied(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        client.force_login(alice)
        answer, _verifier = consent(client, [alice_home], allow=False)
        assert answer.status_code == 302
        assert "error=access_denied" in answer["Location"]
        assert not AppConnection.objects.exists()

    def test_a_home_of_another_person_is_refused(
        self, client: Client, alice: User, alice_home: Home, bob_home: Home
    ) -> None:
        client.force_login(alice)
        answer, _verifier = consent(client, [bob_home])
        assert answer.status_code == 200
        assert not AppConnection.objects.exists()

    def test_pkce_plain_is_refused_before_any_connection(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        client.force_login(alice)
        verifier, _challenge = pkce_pair()
        params = authorize_params(verifier, code_challenge_method="plain")
        page = client.get(reverse("oauth2_provider:authorize"), params, secure=True)
        data = {**params, "homes": [str(alice_home.id)], "allow": "1"}
        answer = client.post(reverse("oauth2_provider:authorize"), data, secure=True)
        assert answer.status_code == 302
        assert "code=" not in answer["Location"]
        assert "error=invalid_request" in answer["Location"]
        assert not AppConnection.objects.exists()
        assert page.status_code in (200, 302, 400)

    def test_pkce_missing_is_refused(self, client: Client, alice: User) -> None:
        client.force_login(alice)
        params = authorize_params("")
        del params["code_challenge"], params["code_challenge_method"]
        page = client.get(reverse("oauth2_provider:authorize"), params, secure=True)
        assert page.status_code == 302
        assert "error=invalid_request" in page["Location"]

    def test_another_redirect_uri_is_refused(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        client.force_login(alice)
        _verifier, challenge = pkce_pair()
        params = authorize_params(challenge, redirect_uri="https://example.com/callback")
        page = client.get(reverse("oauth2_provider:authorize"), params, secure=True)
        assert page.status_code in (200, 400)
        assert "form" not in (page.context or {}) or page.context.get("error")

    def test_the_mobile_app_turned_off(self, client: Client, alice: User) -> None:
        mobile_app(on=False)
        client.force_login(alice)
        _verifier, challenge = pkce_pair()
        page = client.get(
            reverse("oauth2_provider:authorize"), authorize_params(challenge), secure=True
        )
        assert page.status_code == 403
        assert "does not allow the mobile app" in page.content.decode()


class TestTokens:
    def test_prefixes_and_hashed_storage(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        assert phone.access.startswith("hca_")
        assert phone.refresh.startswith("hcr_")
        for table in ("oauth2_provider_accesstoken", "oauth2_provider_refreshtoken"):
            with db.cursor() as cursor:
                cursor.execute(f"SELECT * FROM {table}")  # noqa: S608 - fixed names
                rows = cursor.fetchall()
            assert rows
            for row in rows:
                assert all(phone.access not in str(v) and phone.refresh not in str(v) for v in row)

    def test_code_once_only(self, client: Client, alice: User, alice_home: Home) -> None:
        client.force_login(alice)
        answer, verifier = consent(client, [alice_home])
        code = code_of(answer)
        assert exchange(Client(), code, verifier).status_code == 200
        assert exchange(Client(), code, verifier).status_code == 400

    def test_wrong_verifier(self, client: Client, alice: User, alice_home: Home) -> None:
        client.force_login(alice)
        answer, _verifier = consent(client, [alice_home])
        assert exchange(Client(), code_of(answer), "x" * 50).status_code == 400

    def test_every_token_has_its_connection(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        refresh(Client(), phone.refresh)
        assert set(AccessToken.objects.values_list("pk", flat=True)) == set(
            ConnectionAccessToken.objects.values_list("access_token_id", flat=True)
        )
        assert set(RefreshToken.objects.values_list("pk", flat=True)) == set(
            ConnectionRefreshToken.objects.values_list("refresh_token_id", flat=True)
        )
        assert {link.connection_id for link in ConnectionRefreshToken.objects.all()} == {
            phone.connection.id
        }

    def test_rotation_gives_a_new_refresh_token(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        answer = refresh(Client(), phone.refresh)
        assert answer.status_code == 200
        body = answer.json()
        assert body["refresh_token"] != phone.refresh
        assert body["refresh_token"].startswith("hcr_")
        assert info(body["access_token"]).status_code == 200  # type: ignore[attr-defined]
        # The old access token was replaced.
        assert info(phone.access).status_code == 401  # type: ignore[attr-defined]

    def test_reuse_revokes_the_connection(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        rotated = refresh(Client(), phone.refresh).json()
        again = refresh(Client(), phone.refresh)
        assert again.status_code == 400
        phone.connection.refresh_from_db()
        assert phone.connection.revoked_at is not None
        assert refresh(Client(), rotated["refresh_token"]).status_code == 400
        assert info(rotated["access_token"]).status_code == 401  # type: ignore[attr-defined]
        assert AuditEvent.objects.filter(action="appconnection.revoked").count() == 1

    def test_no_grace_period(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        refresh(Client(), phone.refresh)
        # At once, not after a delay: the second use fails.
        assert refresh(Client(), phone.refresh).status_code == 400

    def test_revoking_a_connection_revokes_every_token(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        connections.revoke_connection(alice, phone.connection)
        assert not AccessToken.objects.exists()
        assert not RefreshToken.objects.filter(revoked__isnull=True).exists()
        assert not Grant.objects.exists()
        assert info(phone.access).status_code == 401  # type: ignore[attr-defined]
        assert refresh(Client(), phone.refresh).status_code == 400

    def test_the_app_disconnects_itself(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        answer = Client().post(
            reverse("oauth2_provider:revoke-token"),
            {"token": phone.refresh, "client_id": CLIENT_ID},
            secure=True,
        )
        assert answer.status_code == 200
        phone.connection.refresh_from_db()
        assert phone.connection.revoked_at is not None
        assert info(phone.access).status_code == 401  # type: ignore[attr-defined]

    def test_mobile_app_off_refuses_tokens_and_keeps_connections(
        self, alice: User, alice_home: Home
    ) -> None:
        phone = connect(alice, [alice_home])
        mobile_app(on=False)
        answer = info(phone.access)
        assert answer.status_code == 403  # type: ignore[attr-defined]
        assert answer.json()["code"] == "mobile_app_disabled"  # type: ignore[attr-defined]
        assert info().json()["oauth"] is None  # type: ignore[attr-defined]
        assert refresh(Client(), phone.refresh).status_code == 403
        phone.connection.refresh_from_db()
        assert phone.connection.revoked_at is None
        mobile_app(on=True)
        assert info(phone.access).status_code == 200  # type: ignore[attr-defined]


class TestNoFallBack:
    """An `hca_` token that is not valid is a 401, never the session of the same browser."""

    @pytest.fixture
    def signed_in(self, alice: User) -> Client:
        client = Client()
        client.force_login(alice)
        return client

    @pytest.mark.parametrize("with_session", [False, True])
    def test_expired(
        self, alice: User, alice_home: Home, signed_in: Client, with_session: bool
    ) -> None:
        phone = connect(alice, [alice_home])
        AccessToken.objects.update(expires=timezone.now() - timedelta(seconds=1))
        client = signed_in if with_session else Client()
        answer = client.get(
            reverse("api-v1:home-list"), secure=True, HTTP_AUTHORIZATION=f"Bearer {phone.access}"
        )
        assert answer.status_code == 401
        assert info(phone.access, client=client).status_code == 401  # type: ignore[attr-defined]

    @pytest.mark.parametrize("with_session", [False, True])
    def test_revoked(
        self, alice: User, alice_home: Home, signed_in: Client, with_session: bool
    ) -> None:
        phone = connect(alice, [alice_home])
        connections.revoke_connection(alice, phone.connection)
        client = signed_in if with_session else Client()
        answer = client.get(
            reverse("api-v1:home-list"), secure=True, HTTP_AUTHORIZATION=f"Bearer {phone.access}"
        )
        assert answer.status_code == 401

    @pytest.mark.parametrize("with_session", [False, True])
    def test_unknown(self, signed_in: Client, with_session: bool) -> None:
        client = signed_in if with_session else Client()
        answer = client.get(
            reverse("api-v1:home-list"), secure=True, HTTP_AUTHORIZATION="Bearer hca_unknown"
        )
        assert answer.status_code == 401

    def test_api_tokens_as_on_main(self, alice: User, alice_home: Home) -> None:
        _token, raw = services.issue_token(
            alice, name="ha", scopes=[Scope.READ], homes=[alice_home]
        )
        client = Client()
        answer = client.get(reverse("api-v1:home-list"), HTTP_AUTHORIZATION=f"Bearer {raw}")
        assert answer.status_code == 200
        answer = client.get(reverse("api-v1:home-list"), HTTP_AUTHORIZATION="Bearer nope")
        assert answer.status_code == 401


class TestInfo:
    def test_without_a_token(self) -> None:
        body = info().json()  # type: ignore[attr-defined]
        assert body == {
            "sync": [1],
            "oauth": {
                "authorize": "https://testserver/o/authorize/",
                "token": "https://testserver/o/token/",
                "revoke": "https://testserver/o/revoke_token/",
                "client_id": CLIENT_ID,
            },
        }

    def test_with_a_token(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home], scope="sync homes.create", may_create_homes=True)
        answer = info(phone.access)
        assert answer["HC-Sync"] == "1"  # type: ignore[index]
        body = answer.json()  # type: ignore[attr-defined]
        assert body["connection"] == {"id": str(phone.connection.id), "may_create_homes": True}
        assert body["server"]


class TestScoping:
    def test_a_connection_reaches_its_homes_only(self, alice: User, alice_home: Home) -> None:
        from rest_framework.test import APIRequestFactory

        from housecairn.homes import services as home_services
        from housecairn.homes.scoping import homes_for_request

        other = home_services.create_home(alice, name="Cabin")
        phone = connect(alice, [alice_home])
        request = APIRequestFactory().get("/")
        token = AccessToken.objects.get()
        token.housecairn_connection = phone.connection
        request.user = alice  # type: ignore[attr-defined]
        request.auth = token  # type: ignore[attr-defined]
        assert list(homes_for_request(request)) == [alice_home]
        assert other not in homes_for_request(request)

    def test_a_member_removed_loses_the_home(
        self, alice: User, bob: User, alice_home: Home
    ) -> None:
        Membership.objects.create(home=alice_home, user=bob, role=Role.MEMBER)
        phone = connect(bob, [alice_home])
        Membership.objects.filter(home=alice_home, user=bob).delete()
        from housecairn.homes.services import homes_for

        assert list(homes_for(bob, restrict_to=[alice_home.id])) == []
        assert phone.connection.homes.count() == 1

    def test_deleting_the_account_deletes_its_connections(
        self, alice: User, alice_home: Home
    ) -> None:
        connect(alice, [alice_home])
        call_command("check")
        AppConnection.objects.get().user.delete()
        assert not AppConnection.objects.exists()


class TestUnits:
    """What the HTTP answers alone cannot tell apart."""

    def _request(self, token: str) -> object:
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory

        from housecairn.apitokens.sync import SyncInfoView

        raw = APIRequestFactory().get(INFO, HTTP_AUTHORIZATION=f"Bearer {token}")
        return Request(raw, parser_context={"view": SyncInfoView()})

    def test_an_expired_hca_token_is_refused_never_passed_on(
        self, alice: User, alice_home: Home
    ) -> None:
        """Never "not mine": the next class must not decide for an `hca_` token."""
        from rest_framework.exceptions import AuthenticationFailed

        from housecairn.apitokens.authentication import ConnectionAuthentication

        phone = connect(alice, [alice_home])
        AccessToken.objects.update(expires=timezone.now() - timedelta(seconds=1))
        with pytest.raises(AuthenticationFailed):
            ConnectionAuthentication().authenticate(self._request(phone.access))  # type: ignore[arg-type]
        with pytest.raises(AuthenticationFailed):
            ConnectionAuthentication().authenticate(self._request("hca_unknown"))  # type: ignore[arg-type]
        assert ConnectionAuthentication().authenticate(self._request("hc_other")) is None  # type: ignore[arg-type]

    def test_a_connection_needs_the_scope_sync(self, alice: User, alice_home: Home) -> None:
        from housecairn.apitokens.authentication import ConnectionAuthentication
        from housecairn.apitokens.permissions import TokenScopePermission
        from housecairn.apitokens.sync import SyncInfoView

        phone = connect(alice, [alice_home], scope="sync homes.create", may_create_homes=True)
        request: Any = self._request(phone.access)
        request.user, request.auth = ConnectionAuthentication().authenticate(request)  # type: ignore[misc]
        assert TokenScopePermission().has_permission(request, SyncInfoView())
        request.auth.scope = "homes.create"
        assert not TokenScopePermission().has_permission(request, SyncInfoView())
