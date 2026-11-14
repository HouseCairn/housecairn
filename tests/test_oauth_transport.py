"""HTTPS, and plain HTTP only between local addresses (docs/adr/0078)."""

import pytest
from django.test import Client, RequestFactory, override_settings

from housecairn.accounts.models import User
from housecairn.apitokens import transport
from housecairn.core.proxy import ForwardedHeadersMiddleware
from housecairn.homes.models import Home
from tests.oauth_support import code_of, consent, exchange


def through_proxy(request: object) -> object:
    """The request as ForwardedHeadersMiddleware leaves it."""
    seen: list[object] = []

    def view(r: object) -> object:
        seen.append(r)
        return None

    ForwardedHeadersMiddleware(view)(request)  # type: ignore[arg-type]
    return seen[0]


def make(peer: str, host: str, **meta: str) -> object:
    request = RequestFactory().get("/o/token/", REMOTE_ADDR=peer, HTTP_HOST=host, **meta)
    return through_proxy(request)


@pytest.fixture(autouse=True)
def _any_host(settings: object) -> None:
    settings.ALLOWED_HOSTS = ["*"]  # type: ignore[attr-defined]


class TestRule:
    @pytest.mark.parametrize(
        ("peer", "host"),
        [
            ("172.16.1.30", "172.16.1.20:8000"),
            ("172.31.0.5", "172.31.0.2"),
            ("127.0.0.1", "127.0.0.1:8000"),
            ("100.64.1.2", "100.100.1.1:8000"),  # shared address space: a household VPN
            ("fd00::5", "[fd00::1]:8000"),
            ("fe80::1", "[fe80::2]"),
            ("::ffff:172.16.1.30", "172.16.1.20"),  # judged by the IPv4 address it carries
        ],
    )
    def test_local_peer_and_local_literal_pass(self, peer: str, host: str) -> None:
        assert transport.refusal(make(peer, host)) is None  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("peer", "host"),
        [
            ("9.9.9.9", "172.16.1.20"),  # a public peer with a private Host
            ("8.8.8.8", "172.16.1.20"),
            ("172.16.1.30", "house.local"),  # a name, multicast DNS included
            ("172.16.1.30", "house.home.arpa"),
            ("172.16.1.30", "example.com"),
            ("172.16.1.30", "8.8.8.8"),  # a public literal
            ("2001:4860:4860::8888", "[fd00::1]"),
        ],
    )
    def test_each_condition_alone_fails(self, peer: str, host: str) -> None:
        assert transport.refusal(make(peer, host)) is not None  # type: ignore[arg-type]

    def test_forwarded_proto_from_an_untrusted_peer_is_ignored(self) -> None:
        with override_settings(
            SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
            HOUSECAIRN_FORWARDED_ALLOW_IPS=["127.0.0.1"],
        ):
            request = make("8.8.8.8", "example.com", HTTP_X_FORWARDED_PROTO="https")
            assert transport.refusal(request) is not None  # type: ignore[arg-type]

    def test_forwarded_proto_from_a_trusted_proxy_counts(self) -> None:
        with override_settings(
            SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
            HOUSECAIRN_FORWARDED_ALLOW_IPS=["127.0.0.1"],
        ):
            request = make(
                "127.0.0.1",
                "example.com",
                HTTP_X_FORWARDED_PROTO="https",
                HTTP_X_FORWARDED_FOR="8.8.8.8",
            )
            assert transport.refusal(request) is None  # type: ignore[arg-type]

    def test_the_client_a_trusted_proxy_reports_is_the_peer(self) -> None:
        # The proxy is local, the client it reports is not: plain HTTP is refused.
        with override_settings(HOUSECAIRN_FORWARDED_ALLOW_IPS=["172.16.1.1"]):
            request = make("172.16.1.1", "172.16.1.20", HTTP_X_FORWARDED_FOR="8.8.8.8")
            assert transport.refusal(request) is not None  # type: ignore[arg-type]
            request = make("172.16.1.1", "172.16.1.20", HTTP_X_FORWARDED_FOR="172.16.1.30")
            assert transport.refusal(request) is None  # type: ignore[arg-type]

    def test_https_needs_neither(self) -> None:
        request = RequestFactory().get(
            "/", REMOTE_ADDR="8.8.8.8", HTTP_HOST="example.com", secure=True
        )
        assert transport.refusal(through_proxy(request)) is None  # type: ignore[arg-type]


@pytest.mark.django_db
class TestEndpoints:
    def test_plain_http_from_a_name_is_refused_everywhere(self, alice: User) -> None:
        client = Client()
        client.force_login(alice)
        for path in ("/o/authorize/", "/api/v1/sync/info"):
            answer = client.get(path)
            assert answer.status_code == 403, path
        for path in ("/o/token/", "/o/revoke_token/"):
            answer = Client().post(path, {})
            assert answer.status_code == 403
            assert answer.json()["code"] == "insecure_transport"
        assert Client().get("/api/v1/sync/info").json()["code"] == "insecure_transport"

    def test_plain_http_on_the_local_network(self, alice: User, alice_home: Home) -> None:
        local = {"REMOTE_ADDR": "172.16.1.30", "HTTP_HOST": "172.16.1.20:8000"}
        if True:
            client = Client()
            client.force_login(alice)
            answer, verifier = consent(client, [alice_home], secure=False, **local)
            assert answer.status_code == 302
            page = client.get("/api/v1/sync/info", **local)  # type: ignore[arg-type]
            assert page.status_code in (200, 403)
            tokens = exchange(Client(), code_of(answer), verifier, secure=False, **local)
            assert tokens.status_code == 200, tokens.content
            info = Client().get(
                "/api/v1/sync/info",
                HTTP_AUTHORIZATION=f"Bearer {tokens.json()['access_token']}",
                **local,  # type: ignore[arg-type]
            )
            assert info.status_code == 200

    def test_the_consent_page_warns_on_plain_http(self, alice: User, alice_home: Home) -> None:
        local = {"REMOTE_ADDR": "172.16.1.30", "HTTP_HOST": "172.16.1.20"}
        if True:
            client = Client()
            client.force_login(alice)
            from tests.oauth_support import authorize_params, pkce_pair

            _verifier, challenge = pkce_pair()
            page = client.get("/o/authorize/", authorize_params(challenge), **local)  # type: ignore[arg-type]
            assert page.status_code == 200
            assert "not encrypted" in page.content.decode()

    def test_a_public_peer_with_a_private_host_is_refused(self) -> None:
        if True:
            answer = Client().post("/o/token/", {}, REMOTE_ADDR="9.9.9.9", HTTP_HOST="172.16.1.20")
            assert answer.status_code == 403
            assert answer.json()["code"] == "insecure_transport"


class TestOauthlibAddress:
    """oauthlib's own HTTPS check sees `https` only for a request the rule accepted."""

    def _address(self, peer: str, host: str) -> str:
        from housecairn.apitokens.oauth import TransportRuleCore

        return TransportRuleCore()._get_escaped_full_path(make(peer, host))  # type: ignore[no-any-return]

    def test_local_plain_http_is_given_as_https(self) -> None:
        assert self._address("172.16.1.30", "172.16.1.20:8000").startswith("https://")

    @pytest.mark.parametrize(
        ("peer", "host"),
        [("8.8.8.8", "172.16.1.20"), ("172.16.1.30", "house.local")],
    )
    def test_refused_plain_http_stays_http(self, peer: str, host: str) -> None:
        assert self._address(peer, host).startswith("http://")
