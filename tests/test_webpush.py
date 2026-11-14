"""Notifications on the device, by Web Push (docs/adr/0066).

The encryption is checked against the example of RFC 8291 (section 5 and Appendix A), the
VAPID signature with the public key of the instance. Sending goes to a fake push service
(`FakePushService`) that does what a real one does with a message: it checks the signature
and its audience, and it hands the body to the browser, which decrypts it with its own
keys. No network: `webpush.post` is replaced by the fake, except where the guard of
`core.netguard.post` is checked, before any connection.
"""

import datetime
import json
import socket
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from django.core.exceptions import ValidationError
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.apitokens import services as token_services
from housecairn.core import netguard
from housecairn.homes.models import Home, Role
from housecairn.instance import crypto
from housecairn.notifications import alerts, devices, reminders, services, webpush
from housecairn.notifications.models import Device, Preference, PushKey
from housecairn.tasks import agenda
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Occurrence

pytestmark = pytest.mark.django_db

U = webpush.unb64url
# RFC 8291, section 5 and Appendix A.
RFC = {
    "plaintext": U("V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24"),
    "as_public": U(
        "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
    ),
    "as_private": U("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"),
    "ua_public": U(
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
    ),
    "ua_private": U("q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"),
    "salt": U("DGv6ra1nlYgDCS1FRnbzlw"),
    "auth_secret": U("BTBZMqHH6r4Tts7J_aSIgg"),
    "ecdh_secret": U("kyrL1jIIOHEzg3sM2ZWRHDRB62YACZhhSlknJ672kSs"),
    "prk_key": U("Snr3JMxaHVDXHWJn5wdC52WjpCtd2EIEGBykDcZW32k"),
    "ikm": U("S4lYMb_L0FxCeq0WhDx813KgSYqU26kOyzWUdsXYyrg"),
    "prk": U("09_eUZGrsvxChDCGRCdkLiDXrReGOEVeSCdCcPBSJSc"),
    "cek": U("oIhVW04MRdy2XN9CiKLxTg"),
    "nonce": U("4h_95klXJ5E_qnoN"),
    "message": U(
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
        "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
        "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    ),
}
ENDPOINT = "https://fcm.googleapis.com/fcm/send/fixture-not-an-endpoint"
LIST = "api-v1:push-device-list"
REAL_POST = webpush.post


@pytest.fixture(autouse=True)
def _on() -> Iterator[None]:
    with override_settings(
        HOUSECAIRN_NOTIFICATIONS_ENABLED=True,
        HOUSECAIRN_WEBPUSH_ENABLED=True,
        HOUSECAIRN_WEBPUSH_DISCREET=False,
        TIME_ZONE="UTC",
    ):
        yield


class Browser:
    """The keys a browser makes for one subscription."""

    def __init__(self, endpoint: str = ENDPOINT) -> None:
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789abcdef"
        self.endpoint = endpoint

    def subscription(self, name: str = "Phone") -> dict[str, str]:
        return {
            "name": name,
            "endpoint": self.endpoint,
            "p256dh": webpush.b64url(webpush.public_bytes(self.private.public_key())),
            "auth": webpush.b64url(self.auth),
        }


def verify_vapid(authorization: str, endpoint: str) -> dict[str, Any]:
    """What a push service checks: the signature by `k`, the audience, the expiry."""
    assert authorization.startswith("vapid t=")
    token_part, key_part = authorization[len("vapid t=") :].split(", k=")
    head, body, signature = token_part.split(".")
    public = webpush.load_public(U(key_part))
    raw = U(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    public.verify(der, f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
    assert json.loads(U(head)) == {"typ": "JWT", "alg": "ES256"}
    claims: dict[str, Any] = json.loads(U(body))
    assert claims["aud"] == webpush.origin(endpoint)
    return claims


class FakePushService:
    """Stands for Google's, Mozilla's or Apple's: checks, then delivers to the browsers."""

    def __init__(self) -> None:
        self.browsers: dict[str, Browser] = {}
        self.status: dict[str, int] = {}
        self.shown: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []

    def post(self, endpoint: str, *, body: bytes, authorization: str, urgency: str = "") -> int:
        verify_vapid(authorization, endpoint)
        assert U(authorization.split(", k=")[1]) == U(PushKey.objects.get().public_key)
        if self.status.get(endpoint):
            return self.status[endpoint]
        browser = self.browsers[endpoint]
        plain = webpush.decrypt(body, ua_private=browser.private, auth_secret=browser.auth)
        self.shown.append(json.loads(plain))
        return 201


@pytest.fixture
def push(monkeypatch: pytest.MonkeyPatch) -> FakePushService:
    service = FakePushService()
    monkeypatch.setattr(webpush, "post", service.post)
    return service


def add_device(user: User, push: FakePushService, endpoint: str = ENDPOINT) -> Device:
    browser = Browser(endpoint)
    push.browsers[endpoint] = browser
    return devices.subscribe(user, **browser.subscription())


class TestEncryption:
    def test_the_intermediate_values_are_those_of_rfc_8291(self) -> None:
        keys = webpush.derive(
            ua_public=RFC["ua_public"],
            auth_secret=RFC["auth_secret"],
            salt=RFC["salt"],
            as_private=webpush.private_from_bytes(RFC["as_private"]),
        )
        assert keys.ecdh_secret == RFC["ecdh_secret"]
        assert keys.prk_key == RFC["prk_key"]
        assert keys.ikm == RFC["ikm"]
        assert keys.prk == RFC["prk"]
        assert keys.cek == RFC["cek"]
        assert keys.nonce == RFC["nonce"]

    def test_the_message_is_that_of_rfc_8291(self) -> None:
        as_private = webpush.private_from_bytes(RFC["as_private"])
        assert webpush.public_bytes(as_private.public_key()) == RFC["as_public"]
        body = webpush.encrypt(
            RFC["plaintext"],
            ua_public=RFC["ua_public"],
            auth_secret=RFC["auth_secret"],
            salt=RFC["salt"],
            as_private=as_private,
        )
        assert body == RFC["message"]

    def test_the_browser_of_rfc_8291_reads_it(self) -> None:
        plain = webpush.decrypt(
            RFC["message"],
            ua_private=webpush.private_from_bytes(RFC["ua_private"]),
            auth_secret=RFC["auth_secret"],
        )
        assert plain == RFC["plaintext"]

    def test_each_message_has_its_own_salt_and_key(self) -> None:
        browser = Browser()
        ua_public = webpush.public_bytes(browser.private.public_key())
        first = webpush.encrypt(b"x", ua_public=ua_public, auth_secret=browser.auth)
        second = webpush.encrypt(b"x", ua_public=ua_public, auth_secret=browser.auth)
        assert first[:16] != second[:16]
        assert first[21:86] != second[21:86]

    def test_a_payload_too_large_is_refused(self) -> None:
        browser = Browser()
        ua_public = webpush.public_bytes(browser.private.public_key())
        with pytest.raises(ValueError, match="too large"):
            webpush.encrypt(b"x" * 4000, ua_public=ua_public, auth_secret=browser.auth)


class TestVapid:
    def test_the_signature_is_verified_with_the_key_it_names(self) -> None:
        key = webpush.new_key()
        header = webpush.vapid_header(ENDPOINT, key, "https://home.example.com", now=1000)
        claims = verify_vapid(header, ENDPOINT)
        assert claims == {
            "aud": "https://fcm.googleapis.com",
            "exp": 1000 + webpush.JWT_SECONDS,
            "sub": "https://home.example.com",
        }

    def test_a_signature_by_another_key_is_refused(self) -> None:
        header = webpush.vapid_header(ENDPOINT, webpush.new_key(), "mailto:a@example.com")
        other = webpush.b64url(webpush.public_bytes(webpush.new_key().public_key()))
        forged = header.split(", k=")[0] + ", k=" + other
        with pytest.raises(InvalidSignature):
            verify_vapid(forged, ENDPOINT)

    def test_the_key_of_the_instance_is_sealed_and_made_once(self) -> None:
        first = devices.instance_key()
        assert devices.instance_key().public_key == first.public_key
        assert first.private_key.startswith(crypto.PREFIX)
        raw = crypto.decrypt(first.private_key)
        assert raw not in first.private_key
        key = webpush.private_from_bytes(U(raw))
        assert webpush.b64url(webpush.public_bytes(key.public_key())) == first.public_key

    def test_an_unreadable_key_is_replaced_and_its_devices_removed(
        self, alice: User, push: FakePushService
    ) -> None:
        add_device(alice, push)
        old = PushKey.objects.get().public_key
        PushKey.objects.update(private_key=crypto.PREFIX + "not-readable")
        assert devices.public_key() != old
        assert not Device.objects.exists()

    def test_rotation_removes_every_device(self, alice: User, push: FakePushService) -> None:
        from django.core.management import call_command

        add_device(alice, push)
        old = devices.public_key()
        call_command("rotate_push_key")
        assert devices.public_key() != old
        assert not Device.objects.exists()


class TestSubscriptions:
    def test_subscribe_and_unsubscribe(self, alice: User, push: FakePushService) -> None:
        device = add_device(alice, push)
        assert (device.name, device.service) == ("Phone", "Google")
        for sealed in (device.endpoint, device.p256dh, device.auth):
            assert sealed.startswith(crypto.PREFIX)
        assert "fcm.googleapis.com" not in device.endpoint
        devices.revoke(alice, device)
        assert not Device.all_objects.exists()

    def test_the_same_browser_subscribing_again_is_one_device(
        self, alice: User, bob: User, push: FakePushService
    ) -> None:
        add_device(alice, push)
        add_device(alice, push)
        assert Device.objects.filter(user=alice).count() == 1
        # Signed out, someone else signs in on the browser: it is theirs only.
        add_device(bob, push)
        assert list(Device.objects.values_list("user", flat=True)) == [bob.pk]

    @pytest.mark.parametrize(
        "endpoint",
        [
            "http://fcm.googleapis.com/fcm/send/x",
            "https://fcm.googleapis.com:8443/fcm/send/x",
            "https://user:pw@fcm.googleapis.com/fcm/send/x",
            "https://push.example.com/x",
            "https://fcm.googleapis.com.example.com/x",
            "https://192.0.2.10/x",
            "https://[::1]/x",
            "https://172.31.0.2/x",
            "https://notfcm.googleapis.com/x",
            "",
            "https://fcm.googleapis.com/" + "x" * 1100,
        ],
    )
    def test_an_endpoint_outside_the_push_services_is_refused(
        self, alice: User, endpoint: str
    ) -> None:
        with pytest.raises(ValidationError):
            devices.subscribe(alice, **Browser(endpoint).subscription())
        assert not Device.objects.exists()

    @pytest.mark.parametrize(
        ("endpoint", "service"),
        [
            ("https://updates.push.services.mozilla.com/wpush/v2/x", "Mozilla"),
            ("https://web.push.apple.com/x", "Apple"),
            ("https://wns2-par02p.notify.windows.com/w/?token=x", "Microsoft"),
        ],
    )
    def test_the_push_services_of_the_browsers_are_accepted(
        self, alice: User, endpoint: str, service: str
    ) -> None:
        assert devices.subscribe(alice, **Browser(endpoint).subscription()).service == service

    def test_bad_keys_are_refused(self, alice: User) -> None:
        good = Browser().subscription()
        for field, value in (("p256dh", "AAAA"), ("auth", "AAAA"), ("p256dh", "B" * 87)):
            with pytest.raises(ValidationError):
                devices.subscribe(alice, **{**good, field: value})

    def test_off_by_default(self, alice: User) -> None:
        with override_settings(HOUSECAIRN_WEBPUSH_ENABLED=None):
            from housecairn.notifications.conf import SETTINGS

            assert {s.key: s.default for s in SETTINGS}["webpush_enabled"] is False
        with override_settings(HOUSECAIRN_WEBPUSH_ENABLED=False):
            assert not devices.is_enabled()
            with pytest.raises(ValidationError):
                devices.subscribe(alice, **Browser().subscription())
        with override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=False):
            assert not devices.is_enabled()

    def test_someone_else_cannot_change_a_device(
        self, alice: User, bob: User, push: FakePushService, client: Client
    ) -> None:
        device = add_device(alice, push)
        from django.core.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied):
            devices.revoke(bob, device)
        with pytest.raises(PermissionDenied):
            devices.rename(bob, device, name="Mine")
        client.force_login(bob)
        for route in ("notifications:device", "notifications:device-delete"):
            assert client.post(reverse(route, args=[device.id])).status_code == 404
        api = APIClient()
        api.force_authenticate(bob)
        assert api.delete(reverse("api-v1:push-device-detail", args=[device.id])).status_code == 404
        assert Device.objects.filter(pk=device.pk).exists()

    def test_erasing_the_account_erases_its_devices(
        self, alice: User, push: FakePushService
    ) -> None:
        add_device(alice, push)
        alice.delete()
        assert not Device.all_objects.exists()

    def test_the_export_names_devices_without_endpoint_nor_keys(
        self, alice: User, push: FakePushService
    ) -> None:
        device = add_device(alice, push)
        text = json.dumps(services.export_of(alice))
        assert device.name in text
        for secret in (
            "fcm.googleapis.com",
            "fixture-not-an-endpoint",
            *push.browsers[ENDPOINT].subscription().values(),
        ):
            if secret not in {"Phone"}:
                assert secret not in text


class TestSending:
    def test_a_message_reaches_the_browser_encrypted_and_signed(
        self, alice: User, push: FakePushService
    ) -> None:
        device = add_device(alice, push)
        assert devices.send(device, title="T", body="B", path="/h/x/")
        assert push.shown == [{"title": "T", "body": "B", "url": "/h/x/"}]
        device.refresh_from_db()
        assert device.last_sent_at is not None

    @pytest.mark.parametrize("status", [404, 410])
    def test_a_device_gone_is_removed(
        self, alice: User, push: FakePushService, status: int
    ) -> None:
        device = add_device(alice, push)
        push.status[ENDPOINT] = status
        assert not devices.send(device, title="T", body="B", path="/")
        assert not Device.all_objects.exists()

    def test_another_failure_keeps_the_device(self, alice: User, push: FakePushService) -> None:
        device = add_device(alice, push)
        push.status[ENDPOINT] = 500
        assert not devices.send(device, title="T", body="B", path="/")
        assert Device.objects.exists()

    def test_discreet_content_names_no_task_nor_home(
        self, alice: User, push: FakePushService
    ) -> None:
        device = add_device(alice, push)
        with override_settings(HOUSECAIRN_WEBPUSH_DISCREET=True):
            devices.send_all(
                [device],
                kind="reminder",
                title="HouseCairn reminder",
                body="Boiler, Main house",
                path="/t/",
            )
        assert push.shown == [{"title": "HouseCairn", "body": "A task is due.", "url": "/t/"}]

    def test_discreet_sentences_are_in_the_language_of_the_person(
        self,
        alice: User,
        alice_home: Home,
        bob: User,
        add_member: Callable[..., object],
        push: FakePushService,
    ) -> None:
        """The worker speaks English; the person asked for French. A reminder, a summary
        and a report each say their fixed sentence in French."""
        from housecairn.reports import services as report_services

        add_device(alice, push)
        add_member(alice_home, bob, Role.MEMBER)
        Preference.objects.create(user=alice, time_zone="UTC", digest=False)
        agenda.update_preferences(alice, language="fr")
        due = task_services.today() + datetime.timedelta(days=1)
        task_services.create_task(alice, alice_home, title="Change the filter", due_on=due)
        now = datetime.datetime.combine(due, datetime.time(9), tzinfo=datetime.UTC)
        with override_settings(HOUSECAIRN_WEBPUSH_DISCREET=True):
            assert reminders.dispatch(now) == 1
            Preference.objects.filter(user=alice).update(digest=True)
            assert reminders.dispatch(now) == 1
            report = report_services.create_report(bob, alice_home, title="Leak")
            assert alerts.report_created(str(report.id)) == 1
        assert [m["body"] for m in push.shown] == [
            "Une tâche est à faire.",
            "Des tâches sont à faire.",
            "Un problème a été signalé.",
        ]
        assert {m["title"] for m in push.shown} == {"HouseCairn"}

    def test_the_text_of_a_device_that_is_not_discreet_is_the_text_of_the_caller(
        self, alice: User, push: FakePushService
    ) -> None:
        device = add_device(alice, push)
        Preference.objects.create(user=alice, time_zone="UTC")
        agenda.update_preferences(alice, language="fr")
        with override_settings(HOUSECAIRN_WEBPUSH_DISCREET=False):
            devices.send_all(
                [device], kind="reminder", title="Rappel", body="Filtre, Maison", path="/t/"
            )
        assert push.shown == [{"title": "Rappel", "body": "Filtre, Maison", "url": "/t/"}]

    def test_discreet_is_the_default(self) -> None:
        from housecairn.notifications.conf import SETTINGS

        assert {s.key: s.default for s in SETTINGS}["webpush_discreet"] is True

    def test_a_long_text_is_cut_to_what_a_push_service_accepts(self) -> None:
        data = devices._payload("T", "é" * 5000, "/")
        assert len(data) <= webpush.MAX_PAYLOAD
        assert json.loads(data)["url"] == "/"

    def test_reminders_go_to_a_device_without_any_channel(
        self, alice: User, alice_home: Home, push: FakePushService
    ) -> None:
        add_device(alice, push)
        Preference.objects.create(user=alice, time_zone="UTC")
        due = task_services.today() + datetime.timedelta(days=1)
        task = task_services.create_task(alice, alice_home, title="Change the filter", due_on=due)
        occurrence = Occurrence.objects.get(task=task)
        now = datetime.datetime.combine(due, datetime.time(9), tzinfo=datetime.UTC)
        assert reminders.dispatch(now) == 1
        assert reminders.dispatch(now) == 0  # once
        [shown] = push.shown
        assert shown["title"] == "HouseCairn reminder"
        assert shown["body"].startswith("Change the filter, Main house, due ")
        assert "\n" not in shown["body"]
        assert shown["url"] == reverse(
            "tasks:detail", kwargs={"home_id": alice_home.pk, "task_id": occurrence.task_id}
        )

    def test_no_reminder_to_a_device_when_push_is_off(
        self, alice: User, alice_home: Home, push: FakePushService
    ) -> None:
        add_device(alice, push)
        due = task_services.today() + datetime.timedelta(days=1)
        task_services.create_task(alice, alice_home, title="Change the filter", due_on=due)
        now = datetime.datetime.combine(due, datetime.time(9), tzinfo=datetime.UTC)
        with override_settings(HOUSECAIRN_WEBPUSH_ENABLED=False):
            assert reminders.dispatch(now) == 0
        assert push.shown == []

    def test_the_test_goes_through_the_worker(
        self, alice: User, push: FakePushService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = add_device(alice, push)
        queued: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "django_q.tasks.async_task", lambda name, *args: queued.append((name, *args))
        )
        devices.test(alice, device)
        assert queued == [("housecairn.notifications.devices.send_test", str(device.pk))]
        assert push.shown == []
        assert devices.send_test(str(device.pk))
        assert push.shown[0]["url"] == reverse("notifications:index")


class TestGuard:
    @pytest.fixture
    def resolves_to(self, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], list[str]]:
        connected: list[str] = []

        def point(address: str) -> list[str]:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET

            def resolve(host: str, port: int, *args: Any, **kwargs: Any) -> Any:
                return [(family, socket.SOCK_STREAM, 6, "", (address, port))]

            monkeypatch.setattr(netguard, "_resolve", resolve)
            monkeypatch.setattr(
                netguard._Pinned, "connect", lambda self: connected.append(self._address)
            )
            return connected

        return point

    @pytest.mark.parametrize(
        "address",
        [
            "172.16.5.20",
            "100.64.0.1",
            "127.0.0.1",
            "169.254.169.254",
            "fd00::1",
            "::ffff:172.31.0.2",
        ],
    )
    def test_an_endpoint_whose_name_is_private_is_not_called(
        self, resolves_to: Callable[[str], list[str]], address: str
    ) -> None:
        connected = resolves_to(address)
        with pytest.raises(netguard.RefusedError) as refused:
            netguard.post(ENDPOINT, body=b"x", headers={}, seconds=2)
        assert refused.value.reason == "address"
        assert connected == []

    def test_a_device_whose_push_service_resolves_privately_gets_nothing(
        self,
        alice: User,
        push: FakePushService,
        resolves_to: Callable[[str], list[str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        device = add_device(alice, push)
        connected = resolves_to("172.16.1.3")
        monkeypatch.setattr(webpush, "post", REAL_POST)
        assert not devices.send(device, title="T", body="B", path="/")
        assert connected == []
        assert Device.objects.exists()

    @pytest.mark.parametrize(
        ("url", "reason"),
        [
            ("http://fcm.googleapis.com/x", "scheme"),
            ("https://fcm.googleapis.com:8443/x", "port"),
            ("https://u:p@fcm.googleapis.com/x", "credentials"),
        ],
    )
    def test_only_https_on_443(self, url: str, reason: str) -> None:
        with pytest.raises(netguard.RefusedError) as refused:
            netguard.post(url, body=b"x", headers={}, seconds=2)
        assert refused.value.reason == reason


class TestAccess:
    """Every route of `me/` in the API: a token reads at most, it never writes."""

    @pytest.fixture
    def device(self, alice: User, push: FakePushService) -> Device:
        return add_device(alice, push)

    def _client(self, who: str, alice: User, home: Home) -> APIClient:
        client = APIClient()
        if who == "session":
            client.force_login(alice)
        else:
            scopes = {"read": ["read"], "write": ["read", "write"], "events": ["events"]}[who]
            _token, raw = token_services.issue_token(alice, name="t", scopes=scopes, homes=[home])
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        return client

    EXPECTED = {
        # who: (GET list, POST, PATCH, DELETE)
        "session": (200, 201, 200, 204),
        "write": (200, 403, 403, 403),
        "read": (403, 403, 403, 403),
        "events": (403, 403, 403, 403),
    }

    @pytest.mark.parametrize("who", list(EXPECTED))
    def test_the_matrix_of_the_devices(
        self, who: str, alice: User, alice_home: Home, device: Device, push: FakePushService
    ) -> None:
        client = self._client(who, alice, alice_home)
        detail = reverse("api-v1:push-device-detail", args=[device.id])
        other = Browser("https://fcm.googleapis.com/fcm/send/other")
        push.browsers[other.endpoint] = other
        codes = (
            client.get(reverse(LIST)).status_code,
            client.post(reverse(LIST), other.subscription("Laptop"), format="json").status_code,
            client.patch(detail, {"name": "Renamed"}, format="json").status_code,
            client.delete(detail).status_code,
        )
        assert codes == self.EXPECTED[who]
        if who != "session":
            stored = Device.objects.get()
            assert stored.name == "Phone"

    def test_the_list_never_carries_the_endpoint_nor_the_keys(
        self, alice: User, device: Device, push: FakePushService
    ) -> None:
        client = APIClient()
        client.force_login(alice)
        text = client.get(reverse(LIST)).content.decode()
        assert "Phone" in text
        for secret in push.browsers[ENDPOINT].subscription().values():
            if secret != "Phone":
                assert secret not in text
        assert PushKey.objects.get().private_key not in text

    def test_no_token_writes_anywhere_under_me(
        self, alice: User, alice_home: Home, device: Device
    ) -> None:
        from django.urls import URLPattern, URLResolver, get_resolver

        def walk(patterns: Any, prefix: str) -> Iterator[str]:
            for p in patterns:
                if isinstance(p, URLResolver):
                    yield from walk(p.url_patterns, prefix + str(p.pattern))
                elif isinstance(p, URLPattern):
                    yield prefix + str(p.pattern)

        routes = sorted(
            {
                r
                for r in walk(get_resolver().url_patterns, "")
                if r.replace("^", "").startswith("api/v1/me/") and "format" not in r
            }
        )
        assert any("push-devices" in r for r in routes)
        ids = {"token_id": "00000000-0000-0000-0000-000000000000"}
        for who in ("read", "write", "events"):
            client = self._client(who, alice, alice_home)
            for route in routes:
                path = route.replace("(?P<pk>[^/.]+)", str(device.id))
                path = "/" + path.replace("^", "").replace("$", "")
                for name in ("<uuid:feed_id>", "<uuid:token_id>"):
                    path = path.replace(name, ids["token_id"])
                for method in ("post", "patch", "delete"):
                    status = getattr(client, method)(path, {}, format="json").status_code
                    assert status in (403, 405), (who, method, path, status)
        assert Device.objects.filter(name="Phone").exists()


class TestScreens:
    def test_the_screen_offers_the_button_only_when_on(self, client: Client, alice: User) -> None:
        client.force_login(alice)
        page = client.get(reverse("notifications:index")).content.decode()
        assert "Receive on this device" in page
        assert devices.public_key() in page
        assert PushKey.objects.get().private_key not in page
        with override_settings(HOUSECAIRN_WEBPUSH_ENABLED=False):
            page = client.get(reverse("notifications:index")).content.decode()
        assert "Receive on this device" not in page

    def test_rename_and_stop_on_the_web(
        self, client: Client, alice: User, push: FakePushService
    ) -> None:
        device = add_device(alice, push)
        client.force_login(alice)
        page = client.get(reverse("notifications:device", args=[device.id])).content.decode()
        assert "fcm.googleapis.com" not in page
        assert "Google" in page
        client.post(reverse("notifications:device", args=[device.id]), {"name": "Tablet"})
        device.refresh_from_db()
        assert device.name == "Tablet"
        client.post(reverse("notifications:device-delete", args=[device.id]))
        assert not Device.objects.exists()

    def test_the_service_worker_shows_and_opens_under_its_scope(self, client: Client) -> None:
        script = client.get(reverse("service-worker")).content.decode()
        assert 'addEventListener("push"' in script
        assert 'addEventListener("notificationclick"' in script
        assert "url.pathname.startsWith(CONFIG.scope)" in script


class TestEndToEnd:
    def test_subscribe_with_the_session_then_receive_a_reminder(
        self, alice: User, alice_home: Home, push: FakePushService
    ) -> None:
        """What the button does (static/js/webpush.js), then a run of the worker: the fake
        push service checks the signature and the browser reads the message."""
        browser = Browser("https://updates.push.services.mozilla.com/wpush/v2/fixture")
        push.browsers[browser.endpoint] = browser
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(alice)
        page = client.get(reverse("notifications:index"))
        token = page.cookies["csrftoken"].value if "csrftoken" in page.cookies else ""
        refused = client.post(reverse(LIST), browser.subscription(), format="json")
        assert refused.status_code == 403  # no form token, no device
        answer = client.post(
            reverse(LIST), browser.subscription("Laptop"), format="json", HTTP_X_CSRFTOKEN=token
        )
        assert answer.status_code == 201, answer.content
        assert set(answer.json()) == {"id", "name", "service", "created_at", "last_sent_at"}
        due = task_services.today() + datetime.timedelta(days=1)
        task_services.create_task(alice, alice_home, title="Bleed the radiators", due_on=due)
        Preference.objects.create(user=alice, time_zone="UTC")
        with override_settings(HOUSECAIRN_WEBPUSH_DISCREET=True):
            assert (
                reminders.dispatch(
                    datetime.datetime.combine(due, datetime.time(9), tzinfo=datetime.UTC)
                )
                == 1
            )
        assert push.shown[0]["body"] == "A task is due."
        assert "Bleed" not in json.dumps(push.shown)
