"""Notification channels: sealed addresses, outgoing calls, isolation, API (docs/adr/0043)."""

import contextvars
import datetime
import http.server
import ipaddress
import json
import logging
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import apprise
import pytest
import requests
from apprise.apprise import any_abandoned_calls_still_running
from apprise.plugins.discord import NotifyDiscord
from apprise.plugins.gotify import NotifyGotify
from apprise.plugins.matrix import NotifyMatrix
from apprise.plugins.telegram import NotifyTelegram
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone, translation
from rest_framework.test import APIClient

from housecairn.accounts import services as account_services
from housecairn.accounts.models import User
from housecairn.apitokens import services as token_services
from housecairn.audit.models import AuditEvent
from housecairn.homes.models import Home
from housecairn.instance import config
from housecairn.notifications import forms, outbound, reminders, services
from housecairn.notifications.models import Channel, Delivery, SendState
from housecairn.tasks import services as task_services
from tests.test_instance_settings import recent

pytestmark = pytest.mark.django_db

TOKEN = "123456789:fixture-not-a-token"
URL = f"tgram://{TOKEN}/987654"
LIST = "api-v1:notification-channel-list"


@pytest.fixture(autouse=True)
def _on() -> Iterator[None]:
    with override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=True):
        yield


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """What reached the Telegram, Gotify and Discord plugins, without any network."""
    calls: list[tuple[str, str, str]] = []

    def fake(self: Any, body: str, title: str = "", **kwargs: Any) -> bool:
        calls.append((type(self).__name__, title, body))
        return True

    for plugin in (NotifyTelegram, NotifyGotify, NotifyDiscord):
        monkeypatch.setattr(plugin, "send", fake)
    return calls


@pytest.fixture
def channel(alice: User) -> Channel:
    return services.add_channel(alice, name="Phone", url=URL)


def spy_refusals(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The addresses the guard refused while sending."""
    refused: list[str] = []
    real = outbound.forbidden_address

    def spy(address: Any) -> bool:
        if real(address):
            refused.append(str(address))
            return True
        return False

    monkeypatch.setattr(outbound, "forbidden_address", spy)
    return refused


def let_loopback_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loopback is refused, rightly: let it through for a test that runs a local server, with
    private networks allowed, as an administrator would."""
    monkeypatch.setattr(outbound, "forbidden_address", lambda address: False)
    monkeypatch.setattr(outbound, "private_networks_allowed", lambda: True)


class Drip:
    """What a dripping test server knows: its addresses and its connections still open."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.open = 0
        self.served = 0
        self.urls: list[str] = []


def self_signed(folder: Path) -> tuple[Path, Path]:
    """A certificate for 127.0.0.1 and its key, written in `folder`."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = folder / "server.pem", folder / "server.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def signed_in(client: Client, user: User) -> Client:
    client.force_login(user)
    return client


class TestAddresses:
    @pytest.mark.parametrize(
        "url",
        [
            "windows://",
            "syslog://",
            "rsyslog://192.0.2.1",
            "mqtt://broker.example.com/topic",
            "dbus://",
            "file:///etc/passwd",
            "https://example.com/hook",
            # Generic webhooks: their URL chooses the method, the headers and the fields.
            "json://hooks.example.com/hook",
            "jsons://hooks.example.com/admin/delete?method=DELETE",
            "xml://hooks.example.com/hook",
            "xmls://hooks.example.com/hook",
            "form://hooks.example.com/hook",
            "forms://hooks.example.com/hook",
            "not an address",
            "",
        ],
    )
    def test_a_scheme_outside_the_list_is_refused(self, alice: User, url: str) -> None:
        with pytest.raises(services.ValidationError) as caught:
            services.add_channel(alice, name="x", url=url)
        assert "url" in caught.value.message_dict
        assert not Channel.objects.exists()

    @pytest.mark.parametrize(
        "url",
        [
            "gotifys://127.0.0.1/token",
            "ntfy://[::1]/topic",
            "ntfy://169.254.169.254/topic",
            "gotify://[::ffff:127.0.0.1]/token",
            "mmost://0.0.0.0/token",
        ],
    )
    def test_an_address_typed_as_is_is_checked(self, alice: User, url: str) -> None:
        with pytest.raises(services.ValidationError, match="does not send to this address"):
            services.add_channel(alice, name="x", url=url)

    @pytest.mark.parametrize(
        "url",
        [
            "discord://12345/abcdef?template=/etc/passwd",
            "mailtos://user:pass@mail.example.com?to=a@example.com&pgppub=/etc/passwd",
        ],
    )
    def test_options_that_read_a_file_are_refused(self, alice: User, url: str) -> None:
        with pytest.raises(services.ValidationError, match="read a file"):
            services.add_channel(alice, name="x", url=url)

    @pytest.mark.parametrize(
        "url",
        [
            "apprises://api.example.com/token?+X-Admin=1",
            "mailtos://user:pass@mail.example.com?to=a@example.com&+Bcc=b@example.com",
            "ntfys://ntfy.example.com/topic?-message=1",
            "discord://12345/abcdef?:content=x",
            "gotifys://gotify.example.com/token?%2BX-Admin=1",
        ],
    )
    def test_options_that_add_headers_or_fields_are_refused(self, alice: User, url: str) -> None:
        with pytest.raises(services.ValidationError, match="headers or fields"):
            services.add_channel(alice, name="x", url=url)

    @pytest.mark.parametrize("scheme", ["json", "jsons", "xml", "xmls", "form", "forms"])
    def test_a_stored_webhook_channel_is_not_sent_to(
        self, monkeypatch: pytest.MonkeyPatch, scheme: str
    ) -> None:
        """A channel saved before its scheme left the list is refused when it is sent to."""
        called: list[str] = []
        monkeypatch.setattr(requests.Session, "request", lambda *a, **k: called.append("x"))
        sealed = outbound.seal(f"{scheme}://hooks.example.com/hook")
        assert outbound.send(sealed, title="t", body="b") is False
        assert called == []

    def test_the_examples_of_the_documentation_are_accepted(self) -> None:
        """Pasted as they are, every example address of the guide, the screen and the form."""
        docs = Path(__file__).resolve().parents[1] / "docs"
        texts = []
        for page in ("guides/notifications", "screens/notifications"):
            texts.append((docs / f"{page}.md").read_text(encoding="utf-8"))
            catalog = (docs / "locale/fr/LC_MESSAGES" / f"{page}.po").read_text(encoding="utf-8")
            # Without the comments, where gettext keeps the entries of removed strings.
            texts.extend(line for line in catalog.splitlines() if not line.startswith("#"))
        help_text = forms.ChannelForm.base_fields["url"].help_text
        for language in ("en", "fr"):
            with translation.override(language):
                texts.append(str(help_text))
                texts.append(str(services.REFUSALS["invalid"]))
        examples = {
            match.rstrip(".")
            for text in texts
            for match in re.findall(r"\b[a-z]+://[^\s`\"]+", text)
            if not match.startswith(("http://", "https://"))
        }
        assert len(examples) >= 10
        for example in sorted(examples):
            try:
                outbound.parse(example)
            except outbound.RefusedError as refused:
                pytest.fail(f"{example}: {refused}")

    @pytest.mark.parametrize("host", ["172.16.5.20", "172.31.0.2", "100.64.0.1", "[fd12::1]"])
    def test_a_private_network_is_refused_unless_allowed(self, alice: User, host: str) -> None:
        """Refused by default, with a sentence that says who can allow it (docs/adr/0059)."""
        with pytest.raises(services.ValidationError, match="private network") as refused:
            services.add_channel(alice, name="ntfy", url=f"ntfy://{host}/house")
        assert "administrator" in str(refused.value)
        assert not Channel.objects.exists()
        with override_settings(HOUSECAIRN_NOTIFICATIONS_PRIVATE_NETWORKS=True):
            added = services.add_channel(alice, name="ntfy", url=f"ntfy://{host}/house")
        assert (added.scheme, added.service) == ("ntfy", "ntfy")

    def test_the_address_is_sealed(self, channel: Channel) -> None:
        assert channel.url.startswith("v1:")
        assert TOKEN not in channel.url
        assert (channel.scheme, channel.service) == ("tgram", "Telegram")

    def test_notifications_off_refuse_a_channel_and_a_test(
        self, alice: User, channel: Channel
    ) -> None:
        with override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=False):
            with pytest.raises(services.ValidationError, match="turned off"):
                services.add_channel(alice, name="x", url=URL)
            with pytest.raises(services.ValidationError, match="turned off"):
                services.start_test(alice, channel)


class TestOutgoingCalls:
    """The guard around Apprise: every address resolved while sending is checked."""

    @pytest.mark.parametrize(
        "address",
        ["127.0.0.1", "::1", "169.254.169.254", "fe80::1", "224.0.0.1", "0.0.0.0"],  # noqa: S104
    )
    def test_a_name_resolving_to_a_refused_address_is_not_called(
        self, monkeypatch: pytest.MonkeyPatch, address: str
    ) -> None:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        monkeypatch.setattr(
            outbound,
            "_resolve",
            lambda *a, **k: [(family, socket.SOCK_STREAM, 6, "", (address, 443))],
        )
        connected: list[Any] = []
        monkeypatch.setattr(socket.socket, "connect", lambda self, a: connected.append(a))
        refused = spy_refusals(monkeypatch)
        sealed = outbound.seal("gotifys://hooks.example.com/token")
        assert outbound.send(sealed, title="t", body="b") is False
        assert connected == []
        assert refused == [address]

    @pytest.mark.parametrize(
        "address", ["::ffff:127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::1", "::ffff:169.254.1.1"]
    )
    def test_an_ipv6_address_that_carries_a_refused_ipv4_is_not_called(
        self, monkeypatch: pytest.MonkeyPatch, address: str
    ) -> None:
        """Mapped, NAT64 and 6to4 forms of loopback and link-local, as resolved when sending."""
        monkeypatch.setattr(
            outbound,
            "_resolve",
            lambda *a, **k: [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0))],
        )
        connected: list[Any] = []
        monkeypatch.setattr(socket.socket, "connect", lambda self, a: connected.append(a))
        refused = spy_refusals(monkeypatch)
        assert (
            outbound.send(outbound.seal("ntfys://ntfy.example.com/t"), title="t", body="b") is False
        )
        assert connected == []
        assert refused == [address]

    @pytest.mark.parametrize(
        "address",
        [
            "169.254.169.254",
            "100.100.100.200",
            "192.0.0.192",
            "fd00:ec2::254",
            "::ffff:100.100.100.200",
            "64:ff9b::c000:c0",
        ],
    )
    def test_cloud_metadata_is_refused_though_private_networks_are_not(
        self, alice: User, monkeypatch: pytest.MonkeyPatch, address: str
    ) -> None:
        literal = f"[{address}]" if ":" in address else address
        with pytest.raises(services.ValidationError, match="does not send to this address"):
            services.add_channel(alice, name="x", url=f"ntfy://{literal}/topic")
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        monkeypatch.setattr(
            outbound,
            "_resolve",
            lambda *a, **k: [(family, socket.SOCK_STREAM, 6, "", (address, 443))],
        )
        connected: list[Any] = []
        monkeypatch.setattr(socket.socket, "connect", lambda self, a: connected.append(a))
        assert (
            outbound.send(outbound.seal("ntfys://ntfy.example.com/t"), title="t", body="b") is False
        )
        assert connected == []
        assert not outbound.refused(ipaddress.ip_address("100.100.100.201"), private_networks=True)
        assert outbound.refused(ipaddress.ip_address("100.100.100.201"))

    @pytest.mark.parametrize("address", ["172.16.1.20", "172.20.1.3", "100.64.0.9", "fd12::20"])
    def test_a_name_resolving_to_a_private_address_is_refused_unless_allowed(
        self, monkeypatch: pytest.MonkeyPatch, address: str
    ) -> None:
        """docs/adr/0059: refused by default, as a refusal of its own; allowed by the setting."""
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        monkeypatch.setattr(
            outbound,
            "_resolve",
            lambda *a, **k: [(family, socket.SOCK_STREAM, 6, "", (address, 443))],
        )
        connected: list[Any] = []
        monkeypatch.setattr(socket.socket, "connect", lambda self, a: connected.append(a[0]))
        sealed = outbound.seal("gotifys://gotify.example.com/token")
        assert outbound.deliver(sealed, title="t", body="b") is outbound.Outcome.PRIVATE
        assert outbound.send(sealed, title="t", body="b") is False
        assert connected == []
        with override_settings(HOUSECAIRN_NOTIFICATIONS_PRIVATE_NETWORKS=True):
            assert outbound.deliver(sealed, title="t", body="b") is outbound.Outcome.FAILED
        assert connected == [address]  # called; the fake connection then fails

    def test_the_test_button_says_a_private_address_is_refused(
        self,
        monkeypatch: pytest.MonkeyPatch,
        alice: User,
        client: Client,
        api_client: APIClient,
    ) -> None:
        channel = services.add_channel(alice, name="Gotify", url="gotifys://gotify.example.com/t")
        monkeypatch.setattr(
            outbound,
            "_resolve",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.16.1.20", 443))],
        )
        assert services.start_test(alice, channel).test_state == SendState.PRIVATE
        signed_in(client, alice)
        page = client.post(reverse("notifications:test", args=[channel.id]), follow=True)
        assert "private network" in page.text
        assert "administrator" in page.text
        api_client.force_authenticate(alice)
        answer = api_client.post(reverse("api-v1:notification-channel-test", args=[channel.id]))
        assert answer.status_code == 202
        assert answer.json()["state"] == "private"
        assert "private network" in json.dumps(answer.json())
        assert "172.16.1.20" not in page.text + json.dumps(answer.json())

    @pytest.fixture
    def listener(self) -> Iterator[socket.socket]:
        """A server on 127.0.0.1; the tests accept what reached it."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen()
        server.settimeout(0.2)
        try:
            yield server
        finally:
            server.close()

    @pytest.mark.parametrize(
        ("family", "address"),
        [
            (socket.AF_INET, ("127.0.0.1", 9)),
            (socket.AF_INET6, ("::1", 9, 0, 0)),
            (socket.AF_INET6, ("::ffff:127.0.0.1", 9, 0, 0)),
            (socket.AF_INET, ("169.254.169.254", 80)),
            (socket.AF_INET6, ("fd00:ec2::254", 80, 0, 0)),
            (socket.AF_INET6, ("::ffff:100.100.100.200", 80, 0, 0)),
            (socket.AF_INET, ("172.16.1.20", 80)),
            (socket.AF_INET6, ("::ffff:172.16.0.1", 80, 0, 0)),
            (socket.AF_INET6, ("fe80::1%1", 80, 0, 1)),
        ],
    )
    def test_a_connection_to_a_refused_address_is_refused_without_resolving(
        self, monkeypatch: pytest.MonkeyPatch, family: int, address: tuple[Any, ...]
    ) -> None:
        """docs/adr/0059: `connect` checks its own address, IPv4, IPv6 and IPv4 in IPv6, so a
        plugin that would skip `getaddrinfo` reaches nothing either."""
        outbound.install()
        resolved: list[Any] = []
        monkeypatch.setattr(outbound, "_resolve", lambda *a, **k: resolved.append(a) or [])
        with outbound.guarded() as guard:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            try:
                with pytest.raises(OSError, match="refused by the instance"):
                    sock.connect(address)
                with pytest.raises(OSError, match="refused by the instance"):
                    sock.connect_ex(address)
            finally:
                sock.close()
        assert resolved == []
        assert guard.blocked is True

    @pytest.mark.parametrize(
        ("family", "address"),
        [
            (socket.AF_INET, ("127.0.0.1", 9)),
            (socket.AF_INET6, ("::ffff:127.0.0.1", 9, 0, 0)),
            (socket.AF_INET, ("100.100.100.200", 80)),
            (socket.AF_INET6, ("::ffff:100.100.100.200", 80, 0, 0)),
            (socket.AF_INET6, ("::ffff:192.0.0.192", 80, 0, 0)),
            (socket.AF_INET6, ("fd00:ec2::254", 80, 0, 0)),
        ],
    )
    def test_loopback_and_metadata_stay_refused_to_connect_with_private_networks_allowed(
        self, family: int, address: tuple[Any, ...]
    ) -> None:
        """The setting lifts the rule of private networks only: the instance itself and the
        metadata services, also carried by an IPv6 address, stay out of reach."""
        outbound.install()
        with outbound.guarded(private_networks=True) as guard:
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                with pytest.raises(OSError, match="refused by the instance"):
                    sock.connect(address)
            finally:
                sock.close()
        assert guard.blocked is True
        assert guard.private is False

    def test_connect_applies_the_rule_of_private_networks(
        self, monkeypatch: pytest.MonkeyPatch, listener: socket.socket
    ) -> None:
        """Loopback let through as for the local servers of these tests, it is judged as a
        private address: refused by default, reached once private networks are allowed."""
        monkeypatch.setattr(outbound, "forbidden_address", lambda address: False)
        target = listener.getsockname()
        outbound.install()
        with (
            outbound.guarded() as guard,
            socket.socket() as sock,
            pytest.raises(OSError, match="refused by the instance"),
        ):
            sock.connect(target)
        assert guard.private is True
        with outbound.guarded(private_networks=True) as guard, socket.socket() as sock:
            sock.connect(target)
        assert guard.blocked is False
        conn, _ = listener.accept()
        conn.close()

    def test_a_name_or_a_local_socket_is_refused_to_connect(self, tmp_path: Path) -> None:
        """A name given to `connect` would be resolved by the C library, out of sight."""
        outbound.install()
        with (
            outbound.guarded(private_networks=True),
            socket.socket() as sock,
            pytest.raises(OSError, match="refused by the instance"),
        ):
            sock.connect(("localhost", 9))
        with (
            outbound.guarded(private_networks=True),
            socket.socket(socket.AF_UNIX) as local,
            pytest.raises(OSError, match="refused by the instance"),
        ):
            local.connect(str(tmp_path / "socket"))

    def test_a_plugin_that_connects_to_a_literal_reaches_nothing(
        self, monkeypatch: pytest.MonkeyPatch, listener: socket.socket
    ) -> None:
        """A plugin that would open its own socket to a loopback address, without resolving
        a name first, is refused by `connect`: the send fails and the server sees nothing."""
        target = listener.getsockname()
        resolved: list[Any] = []
        monkeypatch.setattr(outbound, "_resolve", lambda *a, **k: resolved.append(a) or [])

        def raw(self: Any, body: str, title: str = "", **kwargs: Any) -> bool:
            with socket.socket() as sock:
                sock.settimeout(0.5)
                sock.connect(target)
                sock.sendall(b"POST / HTTP/1.0\r\n\r\n")
            return True

        monkeypatch.setattr(NotifyGotify, "send", raw)
        sealed = outbound.seal("gotifys://gotify.example.com/token")
        with override_settings(HOUSECAIRN_NOTIFICATIONS_PRIVATE_NETWORKS=True):
            assert outbound.send(sealed, title="t", body="b") is False
        assert resolved == []
        with pytest.raises(TimeoutError):
            listener.accept()

    def test_the_guard_only_acts_while_sending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        outbound.install()
        assert socket.getaddrinfo("127.0.0.1", 80)
        with outbound.guarded(), pytest.raises(OSError, match="refused"):
            socket.getaddrinfo("127.0.0.1", 80)

    def test_email_goes_through_the_same_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            outbound,
            "_resolve",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 587))],
        )
        refused = spy_refusals(monkeypatch)
        sealed = outbound.seal("mailtos://user:pass@mail.example.com?to=a@example.com")
        assert outbound.send(sealed, title="t", body="b") is False
        assert refused == ["127.0.0.1"]

    def test_no_redirect_is_followed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hits: list[str] = []

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                hits.append(self.path)
                self.send_response(302)
                port = self.server.server_address[1]
                self.send_header("Location", f"http://127.0.0.1:{port}/elsewhere")
                self.end_headers()

            def do_GET(self) -> None:
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args: Any) -> None:
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        let_loopback_through(monkeypatch)
        try:
            sealed = outbound.seal(f"gotify://127.0.0.1:{server.server_port}/hook/token")
            assert outbound.send(sealed, title="t", body="b") is False
        finally:
            server.shutdown()
        assert hits == ["/hook/message"]
        # Outside the guard, requests follows redirects as before.
        response = requests.Response()
        response.status_code, response.headers["Location"] = 302, "/next"
        assert requests.Session().get_redirect_target(response) == "/next"

    @pytest.fixture
    def slow_server(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
        """A server that answers one byte every tenth of a second: about 20 s in all."""
        stop = threading.Event()

        class Slow(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                answer = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                answer += b"Content-Length: 160\r\n\r\n" + b'{"id": 1}'.ljust(160)
                for byte in answer:
                    if stop.wait(0.1):
                        return
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()

            def log_message(self, *args: Any) -> None:
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        let_loopback_through(monkeypatch)
        monkeypatch.setattr(outbound, "SEND_SECONDS", 1.0, raising=False)
        try:
            yield server.server_port
        finally:
            stop.set()
            server.shutdown()
            server.server_close()

    def test_a_slow_server_holds_a_send_for_the_total_delay_only(self, slow_server: int) -> None:
        sealed = outbound.seal(f"gotify://127.0.0.1:{slow_server}/token")
        started = time.monotonic()
        assert outbound.send(sealed, title="t", body="b") is False
        assert time.monotonic() - started < 3

    def test_the_test_button_and_the_reminders_wait_the_total_delay_only(
        self, slow_server: int, alice: User, alice_home: Home
    ) -> None:
        channel = services.add_channel(
            alice, name="Slow", url=f"gotify://127.0.0.1:{slow_server}/token"
        )
        started = time.monotonic()
        assert services.start_test(alice, channel).test_state == SendState.FAILED
        assert time.monotonic() - started < 3
        task_services.create_task(alice, alice_home, title="Filter", due_on=task_services.today())
        started = time.monotonic()
        assert reminders.dispatch(timezone.now() + datetime.timedelta(days=1)) == 1
        assert time.monotonic() - started < 3
        assert Delivery.objects.get().sent is False

    @pytest.fixture
    def dripping_server(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Drip]:
        """Announces a body of a megabyte and sends one byte of it a second, for ever: each
        wait of the socket is short, so only the end of the send can stop the plugin."""
        drip = Drip()

        class Dripping(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                with drip.lock:
                    drip.open += 1
                    drip.served += 1
                try:
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1000000\r\n\r\n")
                    while not drip.stop.wait(1.0):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                except OSError:
                    pass  # the client is gone
                finally:
                    with drip.lock:
                        drip.open -= 1

            def log_message(self, *args: Any) -> None:
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Dripping)
        server.daemon_threads = True
        certificate, key = self_signed(tmp_path)
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate, key)
        tls = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Dripping)
        tls.daemon_threads = True
        tls.socket = context.wrap_socket(tls.socket, server_side=True)
        for each in (server, tls):
            threading.Thread(target=each.serve_forever, daemon=True).start()
        # The certificate of the test server is the only one trusted (requests reads it).
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(certificate))
        let_loopback_through(monkeypatch)
        monkeypatch.setattr(outbound, "SEND_SECONDS", 1.0, raising=False)
        drip.urls = [
            f"gotify://127.0.0.1:{server.server_port}/token",
            f"gotifys://127.0.0.1:{tls.server_port}/token",
        ]
        try:
            yield drip
        finally:
            drip.stop.set()
            for each in (server, tls):
                each.shutdown()
                each.server_close()

    def test_a_server_that_drips_keeps_no_thread_after_the_send(
        self, dripping_server: Drip
    ) -> None:
        """Three sends at once, in clear and over TLS: each one returns within the total
        delay, and ten seconds after they started, no thread of a plugin still runs and no
        connection is left open."""
        started = time.monotonic()
        urls = [*dripping_server.urls, dripping_server.urls[0]]
        results: list[bool] = []
        sends = [
            threading.Thread(
                target=lambda url=url: results.append(
                    outbound.send(outbound.seal(url), title="t", body="b")
                )
            )
            for url in urls
        ]
        for each in sends:
            each.start()
        for each in sends:
            each.join(5)
        assert results == [False, False, False]
        assert time.monotonic() - started < 3
        assert dripping_server.served == 3  # each one reached the server, TLS included
        while time.monotonic() - started < 10 and (
            dripping_server.open or any_abandoned_calls_still_running()
        ):
            time.sleep(0.1)
        assert dripping_server.open == 0
        assert not any_abandoned_calls_still_running()

    def test_a_send_that_ended_opens_nothing_more(self) -> None:
        """What the thread of a plugin still does after the end of its send is refused: a
        new socket, a new name, a new connection."""
        with outbound.guarded():
            context = contextvars.copy_context()
            sock = context.run(socket.socket)
        with pytest.raises(OSError, match="ended"):
            context.run(socket.socket)
        with pytest.raises(OSError, match="ended"):
            context.run(socket.getaddrinfo, "example.com", 443)
        with pytest.raises(OSError, match="ended"):
            context.run(sock.connect, ("192.0.2.1", 443))
        sock.close()

    def test_matrix_logs_out_inside_the_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The Matrix plugin logs out when it is garbage collected, wherever that happens:
        the send does it while the guard still checks names, then drops the token."""
        guarded_at_logout: list[bool] = []

        def log_out(self: Any) -> bool:
            guarded_at_logout.append(outbound._guard.get() is not None)
            return True

        def logged_in(self: Any, *args: Any, **kwargs: Any) -> bool:
            self.access_token = "session"
            return True

        monkeypatch.setattr(NotifyMatrix, "_logout", log_out)
        monkeypatch.setattr(NotifyMatrix, "send", logged_in)
        plugins: list[Any] = []
        real = outbound._plugin

        def keep(url: str, **kwargs: Any) -> Any:
            plugins.append(real(url, **kwargs))
            return plugins[-1]

        monkeypatch.setattr(outbound, "_plugin", keep)
        sealed = outbound.seal("matrixs://user:pass@matrix.example.com/!room:example.com")
        assert outbound.send(sealed, title="t", body="b") is True
        assert guarded_at_logout == [True]
        assert plugins[0].access_token is None

    def test_certificates_are_verified_whatever_the_address_says(self) -> None:
        plugin = outbound._plugin("gotifys://gotify.example.com/token?verify=no")
        assert plugin.verify_certificate is True

    def test_each_wait_of_a_socket_is_short_whatever_the_address_says(self) -> None:
        plugin = outbound._plugin("gotifys://gotify.example.com/token?cto=120&rto=120")
        assert plugin.socket_connect_timeout == outbound.TIMEOUT_SECONDS == 5
        assert plugin.socket_read_timeout == outbound.TIMEOUT_SECONDS

    def test_a_plugin_of_another_scheme_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If Apprise ever mapped an allowed scheme to another plugin, it is not used."""
        real = apprise.Apprise.instantiate

        def other(url: str, **kwargs: Any) -> Any:
            return real("json://hooks.example.com/hook", **kwargs)

        monkeypatch.setattr(apprise.Apprise, "instantiate", staticmethod(other))
        with pytest.raises(outbound.RefusedError, match="scheme"):
            outbound.parse("ntfys://ntfy.example.com/topic")

    def test_apprise_does_not_retry(self) -> None:
        plugin = outbound._plugin("gotifys://gotify.example.com/token?retry=5&wait=30")
        assert (plugin.retry, plugin.wait) == (0, 0)

    def test_the_asset_has_no_image_address(self) -> None:
        plugin = apprise.Apprise.instantiate("discord://12345/abcdef", asset=outbound._asset())
        assert plugin.image_url(apprise.NotifyType.INFO) is None
        assert plugin.image_url(apprise.NotifyType.INFO, logo=True) is None

    def test_the_image_options_are_off_whatever_the_address_says(self) -> None:
        plugin = outbound._plugin("discord://12345/abcdef?image=yes&footer_logo=yes&avatar=yes")
        assert (plugin.include_image, plugin.footer_logo, plugin.avatar) == (False, False, False)
        for url in (
            "ntfys://ntfy.example.com/t?image=yes",
            "mmosts://chat.example.com/t?image=yes",
        ):
            assert outbound._plugin(url).include_image is False

    @pytest.mark.parametrize(
        "url",
        [
            "ntfys://ntfy.example.com/topic",
            "gotifys://gotify.example.com/token",
            "signals://signal.example.com/+12025550100/+12025550101",
            "matrixs://user:pass@matrix.example.com/!room:example.com",
            "mmosts://chat.example.com/token",
            "apprises://api.example.com/token",
            "tgram://123456789:fixture-not-a-token/987654321?image=yes",
            "discord://12345/abcdef",
            "slack://T1JJ3T3L2/A1BRTD4JD/TIiajkdnlazkcOXrIdevi7FQ/#general",
            "pover://uuuuuuuuuuuuuuuuuuuuuuuuuuuuuu@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ],
    )
    def test_no_remote_image_in_a_message(self, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
        """Apprise links its icons on github.com: the receiving app would load them."""
        payloads: list[str] = []

        def capture(self: Any, method: str, url: str, **kwargs: Any) -> requests.Response:
            payloads.append(
                json.dumps(
                    [kwargs.get("data"), kwargs.get("json"), kwargs.get("files")], default=str
                )
            )
            answer = requests.Response()
            answer.status_code = 200
            answer._content = b'{"ok": true, "id": 1, "access_token": "t", "room_id": "!r:x"}'
            return answer

        monkeypatch.setattr(requests.Session, "request", capture)
        monkeypatch.setattr(outbound, "forbidden_address", lambda address: False)
        outbound.send(outbound.seal(url), title="Fixed", body="Body")
        assert payloads
        for payload in payloads:
            for image in ("githubusercontent", "github.com", ".png", "http"):
                assert image not in payload

    def test_the_title_is_escaped_for_the_format_of_the_channel(
        self, sent: list[tuple[str, str, str]]
    ) -> None:
        body = "<b>Paint</b> *the* [hall](javascript:alert(1)) & co"
        assert outbound.send(outbound.seal(URL), title="Fixed", body=body)
        assert outbound.send(
            outbound.seal("discord://12345/abcdef?format=markdown"), title="F", body=body
        )
        (_, _, html), (_, _, markdown) = sent
        assert "<b>Paint" not in html
        assert "&lt;b&gt;Paint&lt;/b&gt;" in html
        assert "&amp; co" in html
        assert r"\*the\*" in markdown
        assert r"\[hall\]" in markdown


class TestSurfaces:
    def test_no_surface_carries_the_address(
        self,
        client: Client,
        alice: User,
        api_client: APIClient,
        caplog: Any,
        sent: list[tuple[str, str, str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        signed_in(client, alice)
        api_client.force_authenticate(alice)
        with caplog.at_level(logging.DEBUG):
            added = client.post(reverse("notifications:add"), {"name": "Phone", "url": URL})
            assert added.status_code == 302
            refused = client.post(
                reverse("notifications:add"), {"name": "x", "url": URL + "?template=/etc/x"}
            )
            api_refused = api_client.post(
                reverse(LIST), {"name": "x", "url": URL + "?template=/etc/x"}, format="json"
            )
            created = api_client.post(reverse(LIST), {"name": "API", "url": URL}, format="json")
            channel = Channel.objects.first()
            assert channel is not None
            detail = reverse("api-v1:notification-channel-detail", args=[channel.id])
            page = reverse("notifications:channel", kwargs={"channel_id": channel.id})
            tested = client.post(reverse("notifications:test", kwargs={"channel_id": channel.id}))
            api_tested = api_client.post(
                reverse("api-v1:notification-channel-test", args=[channel.id])
            )
            # A plugin that fails with the address in its exception: still nothing leaks.
            monkeypatch.setattr(
                NotifyTelegram, "send", lambda *a, **k: (_ for _ in ()).throw(ValueError(URL))
            )
            failed = api_client.post(reverse("api-v1:notification-channel-test", args=[channel.id]))
            outputs = [
                refused.text,
                api_refused.text,
                created.text,
                client.get(reverse("notifications:index")).text,
                client.get(page).text,
                client.get(tested.url).text,
                api_tested.text,
                failed.text,
                api_client.get(reverse(LIST)).text,
                api_client.get(detail).text,
                client.get(reverse("notifications:export")).text,
                api_client.get(reverse("api-v1:notification-export")).text,
                api_client.get(reverse("api-schema")).text,
            ]
        assert json.loads(api_tested.text)["state"] == "sent"
        assert json.loads(failed.text) == {
            "state": "failed",
            "detail": str(services.TEST_RESULTS[False]),
        }
        outputs.append(json.dumps(list(AuditEvent.objects.values()), default=str))
        outputs.append(caplog.text)
        with connection.cursor() as cursor:
            for table in connection.introspection.table_names():
                cursor.execute(f'SELECT * FROM "{table}"')  # noqa: S608 - names from Django
                outputs.append(repr(cursor.fetchall()))
        for output in outputs:
            assert TOKEN not in output
            assert "987654" not in output

    def test_the_export_lists_channels_without_their_address(
        self, client: Client, alice: User, channel: Channel
    ) -> None:
        exported = json.loads(signed_in(client, alice).get(reverse("notifications:export")).text)
        assert exported["channels"] == [
            {
                "id": str(channel.id),
                "name": "Phone",
                "scheme": "tgram",
                "service": "Telegram",
                "enabled": True,
                "created_at": channel.created_at.isoformat(),
            }
        ]
        assert exported["preferences"]["days_before"] == 1

    def test_erasing_the_account_erases_channels_and_deliveries(
        self, alice: User, bob: User, channel: Channel
    ) -> None:
        Delivery.objects.create(user=alice, kind="digest", key="2026-06-15")
        services.add_channel(bob, name="Bob", url=URL)
        account_services.delete_account(alice)
        assert list(Channel.objects.values_list("name", flat=True)) == ["Bob"]
        assert not Delivery.objects.exists()


class TestIsolation:
    @pytest.fixture
    def admin(self, make_user: Callable[..., User]) -> User:
        return make_user("admin@example.com", is_staff=True, is_superuser=True)

    @pytest.mark.parametrize("who", ["bob", "admin"])
    def test_someone_else_gets_the_404_of_a_missing_channel(
        self, client: Client, api_client: APIClient, channel: Channel, request: Any, who: str
    ) -> None:
        other = request.getfixturevalue(who)
        signed_in(client, other)
        api_client.force_authenticate(other)
        missing = "00000000-0000-0000-0000-000000000000"
        for channel_id in (channel.id, missing):
            for name in ("channel", "test", "delete"):
                url = reverse(f"notifications:{name}", kwargs={"channel_id": channel_id})
                answer = client.get(url) if name == "channel" else client.post(url)
                assert answer.status_code == 404
            detail = reverse("api-v1:notification-channel-detail", args=[channel_id])
            assert api_client.get(detail).status_code == 404
            assert api_client.patch(detail, {"name": "x"}, format="json").status_code == 404
            assert api_client.delete(detail).status_code == 404
            test = reverse("api-v1:notification-channel-test", args=[channel_id])
            assert api_client.post(test).status_code == 404
        assert json.loads(api_client.get(reverse(LIST)).text) == []
        assert "Phone" not in client.get(reverse("notifications:index")).text
        assert Channel.objects.get().name == "Phone"

    def test_the_services_check_the_owner_themselves(
        self, bob: User, channel: Channel, sent: list[Any]
    ) -> None:
        """Not only the views: a service called with someone else's channel refuses."""
        for call in (
            lambda: services.update_channel(bob, channel, name="Mine", enabled=False),
            lambda: services.start_test(bob, channel),
            lambda: services.delete_channel(bob, channel),
        ):
            with pytest.raises(PermissionDenied):
                call()
        channel.refresh_from_db()
        assert (channel.name, channel.enabled) == ("Phone", True)
        assert sent == []

    def test_the_administration_site_has_no_channel(
        self, client: Client, admin: User, channel: Channel
    ) -> None:
        signed_in(client, admin)
        page = client.get(reverse("admin:index")).text
        assert "otification" not in page

    @pytest.mark.parametrize(
        ("scopes", "allowed"), [(["read"], False), (["events"], False), (["read", "write"], True)]
    )
    def test_a_token_needs_the_write_scope(
        self,
        api_client: APIClient,
        alice: User,
        alice_home: Home,
        channel: Channel,
        scopes: list[str],
        allowed: bool,
    ) -> None:
        _token, raw = token_services.issue_token(alice, name="t", scopes=scopes, homes=[alice_home])
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        answers = [
            api_client.get(reverse(LIST)),
            api_client.post(reverse(LIST), {"name": "x", "url": URL}, format="json"),
            api_client.get(reverse("api-v1:notification-preferences")),
            api_client.get(reverse("api-v1:notification-export")),
        ]
        codes = [a.status_code for a in answers]
        # Reading with `write`; adding a channel is the person's, never a token's.
        assert codes == ([200, 403, 200, 200] if allowed else [403] * 4)
        assert not Channel.objects.exclude(pk=channel.pk).exists()

    def test_a_write_token_changes_nothing_of_the_person(
        self,
        api_client: APIClient,
        alice: User,
        alice_home: Home,
        channel: Channel,
        sent: list[Any],
    ) -> None:
        """Channels and reminders belong to the person, not to a token tied to homes."""
        _token, raw = token_services.issue_token(
            alice, name="t", scopes=["read", "write"], homes=[alice_home]
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        detail = reverse("api-v1:notification-channel-detail", args=[channel.id])
        prefs = reverse("api-v1:notification-preferences")
        before = Channel.objects.get().url
        answers = {
            "create": api_client.post(reverse(LIST), {"name": "x", "url": URL}, format="json"),
            "rename": api_client.patch(
                detail, {"name": "Hijacked", "enabled": False}, format="json"
            ),
            "delete": api_client.delete(detail),
            "test": api_client.post(reverse("api-v1:notification-channel-test", args=[channel.id])),
            "preferences": api_client.patch(prefs, {"digest": True}, format="json"),
        }
        assert {k: v.status_code for k, v in answers.items()} == dict.fromkeys(answers, 403)
        stored = Channel.objects.get()
        assert (stored.name, stored.enabled, stored.url) == ("Phone", True, before)
        assert not services.Preference.objects.filter(user=alice, digest=True).exists()
        assert sent == []
        # Reading stays open to a token with `write`.
        assert api_client.get(detail).status_code == 200
        assert api_client.get(prefs).status_code == 200
        # The person signed in still does all of it.
        api_client.credentials()
        api_client.force_authenticate(alice)
        assert api_client.patch(prefs, {"digest": True}, format="json").status_code == 200
        assert (
            api_client.post(
                reverse("api-v1:notification-channel-test", args=[channel.id])
            ).status_code
            == 202
        )


class TestWebAndApi:
    def test_add_rename_disable_and_delete_on_the_web(
        self, client: Client, alice: User, sent: list[tuple[str, str, str]]
    ) -> None:
        signed_in(client, alice)
        assert (
            client.post(reverse("notifications:add"), {"name": "Phone", "url": URL}).status_code
            == 302
        )
        channel = Channel.objects.get()
        page = reverse("notifications:channel", kwargs={"channel_id": channel.id})
        assert client.post(page, {"name": "Old phone"}).status_code == 302
        channel.refresh_from_db()
        assert (channel.name, channel.enabled) == ("Old phone", False)
        tested = client.post(reverse("notifications:test", kwargs={"channel_id": channel.id}))
        assert "The test message was sent." in client.get(tested.url).text
        assert "HouseCairn test" in sent[0][2]  # Telegram puts the title in the body
        client.post(reverse("notifications:delete", kwargs={"channel_id": channel.id}))
        assert not Channel.objects.exists()

    def test_the_form_never_shows_the_address_back(self, client: Client, alice: User) -> None:
        signed_in(client, alice)
        answer = client.post(reverse("notifications:add"), {"name": "", "url": URL})
        assert answer.status_code == 422
        assert TOKEN not in answer.text

    def test_tests_are_limited(self, alice: User, channel: Channel, sent: list[Any]) -> None:
        for _ in range(services.TESTS_PER_MINUTE):
            assert services.start_test(alice, channel).test_state == SendState.SENT
        with pytest.raises(services.ValidationError, match="Too many"):
            services.start_test(alice, channel)

    def test_api_channels_and_preferences(self, api_client: APIClient, alice: User) -> None:
        api_client.force_authenticate(alice)
        created = api_client.post(reverse(LIST), {"name": "Phone", "url": URL}, format="json")
        assert created.status_code == 201
        assert set(created.json()) == {
            "id",
            "name",
            "scheme",
            "service",
            "enabled",
            "test_state",
            "created_at",
        }
        detail = reverse("api-v1:notification-channel-detail", args=[created.json()["id"]])
        assert (
            api_client.patch(detail, {"enabled": False}, format="json").json()["enabled"] is False
        )
        moved = api_client.patch(detail, {"url": "ntfy://x/y", "name": "New"}, format="json")
        assert moved.status_code == 400
        assert set(moved.json()) == {"url"}
        assert "delete the channel" in moved.json()["url"][0]
        assert api_client.patch(detail, {"scheme": "ntfy"}, format="json").status_code == 400
        assert TOKEN in outbound.crypto.decrypt(Channel.objects.get().url)
        assert Channel.objects.get().name == "Phone"
        prefs = reverse("api-v1:notification-preferences")
        changed = api_client.patch(
            prefs,
            {
                "quiet_start": "22:00",
                "quiet_end": "07:00",
                "digest": True,
                "time_zone": "Europe/Paris",
            },
            format="json",
        )
        assert changed.status_code == 200
        assert changed.json()["quiet_end"] == "07:00:00"
        half = api_client.patch(prefs, {"quiet_start": None}, format="json")
        assert half.status_code == 400
        wrong = api_client.patch(prefs, {"time_zone": "Mars/Olympus"}, format="json")
        assert wrong.status_code == 400
        assert api_client.delete(detail).status_code == 204

    def test_preferences_on_the_web(self, client: Client, alice: User) -> None:
        signed_in(client, alice)
        url = reverse("notifications:preferences")
        fields = {"days_before": 2, "send_at": "07:30", "time_zone": ""}
        half = client.post(url, {**fields, "quiet_start": "22:00"})
        assert half.status_code == 422
        assert not services.Preference.objects.exists()
        saved = client.post(url, {**fields, "quiet_start": "22:00", "quiet_end": "07:00"})
        assert saved.status_code == 302
        prefs = services.preferences_of(alice)
        assert (prefs.days_before, str(prefs.quiet_end)) == (2, "07:00:00")

    def test_the_settings_screen_locks_a_variable(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        admin = make_user("admin@example.com", is_staff=True, is_superuser=True)
        recent(client, admin)
        with override_settings(HOUSECAIRN_ENV_LOCKED=frozenset({"notifications_enabled"})):
            assert config.is_locked("notifications_enabled")
            page = client.get(
                reverse("instance:settings", kwargs={"section": "notifications"})
            ).text
        tag = re.search(r'<input[^>]*id="locked-notifications_enabled"[^>]*>', page)
        assert tag is not None
        assert "disabled" in tag.group(0)
        assert "checked" in tag.group(0)
        assert 'name="notifications_interval"' in page
        assert config.get_setting("notifications_enabled") is True
        assert config.get_setting("notifications_interval") == 5

    def test_the_more_page_links_to_notifications(self, client: Client, alice: User) -> None:
        page = signed_in(client, alice).get(reverse("more")).text
        assert re.search(r'href="/me/notifications/"', page)
