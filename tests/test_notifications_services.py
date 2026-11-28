"""The services a channel may use: each one reviewed, each one tested (docs/adr/0064)."""

import re
import smtplib
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests
from apprise.manager_plugins import NotificationManager
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse
from django.utils.html import escape
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.notifications import catalog, outbound, services
from housecairn.notifications.catalog import SERVICES, Service

pytestmark = pytest.mark.django_db

ADR = (
    Path(__file__).resolve().parents[1]
    / "docs/adr"
    / "0064-every-apprise-service-with-a-fixed-format.md"
)

# The first request of a send to the example of each service: method and host. The host of
# a self-hosted service is the one typed in the example; the others are fixed by the plugin.
EXPECTED = {
    "tgram": ("POST", "api.telegram.org"), "discord": ("POST", "discord.com"),
    "slack": ("POST", "hooks.slack.com"), "matrixs": ("GET", "matrix.example.com"),
    "mmosts": ("POST", "chat.example.com"), "rockets": ("POST", "chat.example.com"),
    "zulip": ("POST", "example.zulipchat.com"),
    "workflows": ("POST", "prod-01.westeurope.logic.azure.com"),
    "webex": ("POST", "api.ciscospark.com"), "gchat": ("POST", "chat.googleapis.com"),
    "threema": ("POST", "msgapi.threema.ch"), "whatsapp": ("POST", "graph.facebook.com"),
    "line": ("POST", "api.line.me"), "viber": ("POST", "chatapi.viber.com"),
    "revolt": ("POST", "api.revolt.chat"), "guilded": ("POST", "media.guilded.gg"),
    "fluxer": ("POST", "api.fluxer.app"), "flock": ("POST", "api.flock.com"),
    "ryver": ("POST", "example-org.ryver.com"), "twist": ("POST", "api.twist.com"),
    "cliq": ("POST", "cliq.zoho.com"), "zoom": ("POST", "inbots.zoom.us"),
    "stackfield": ("POST", "www.stackfield.com"), "groupme": ("POST", "api.groupme.com"),
    "dingtalk": ("POST", "oapi.dingtalk.com"), "feishu": ("POST", "open.feishu.cn"),
    "lark": ("POST", "open.larksuite.com"), "wechat": ("GET", "qyapi.weixin.qq.com"),
    "wecombot": ("POST", "qyapi.weixin.qq.com"), "kook": ("POST", "www.kookapp.cn"),
    "qq": ("POST", "qmsg.zendee.cn"), "zalo": ("POST", "bot-api.zaloplatforms.com"),
    "notifico": ("GET", "n.tkte.ch"), "ntfys": ("POST", "ntfy.example.com"),
    "pover": ("POST", "api.pushover.net"), "pbul": ("POST", "api.pushbullet.com"),
    "barks": ("POST", "bark.example.com"), "psafers": ("POST", "www.pushsafer.com"),
    "prowl": ("POST", "api.prowlapp.com"), "simplepush": ("POST", "api.simplepu.sh"),
    "join": ("POST", "joinjoaomgcd.appspot.com"), "pushed": ("POST", "api.pushed.co"),
    "pushy": ("POST", "api.pushy.me"), "pushdeer": ("POST", "api2.pushdeer.com"),
    "pushme": ("POST", "push.i-i.me"), "pushplus": ("POST", "www.pushplus.plus"),
    "schan": ("POST", "sctapi.ftqq.com"), "chanify": ("POST", "api.chanify.net"),
    "wxpusher": ("POST", "wxpusher.zjiecode.com"), "wpush": ("POST", "api.wpush.cn"),
    "spugpush": ("POST", "push.spug.dev"), "push": ("POST", "push.techulus.com"),
    "ifttt": ("POST", "maker.ifttt.com"), "onesignal": ("POST", "api.onesignal.com"),
    "kumulos": ("POST", "messages.kumulos.com"), "notifiarr": ("POST", "notifiarr.com"),
    "pushward": ("POST", "api.pushward.app"), "pingram": ("POST", "api.pingram.io"),
    "signalgrid": ("POST", "api.signalgrid.co"), "lauther": ("POST", "api.lauther.id"),
    "dot": ("POST", "dot.mindreset.tech"), "lfg": ("POST", "api.lfg.gm"),
    "youlmk": ("POST", "youlmk.com"), "trigv": ("POST", "api.trigv.com"),
    "strmlabs": ("POST", "streamlabs.com"), "freemobile": ("POST", "smsapi.free-mobile.fr"),
    "sfr": ("POST", "www.dmc.sfr-sh.fr"), "octopush": ("POST", "api.octopush.com"),
    "seven": ("POST", "gateway.seven.io"), "46elks": ("POST", "api.46elks.com"),
    "serwersms": ("POST", "api2.serwersms.pl"), "smsmanager": ("GET", "http-api.smsmanager.cz"),
    "twilio": ("POST", "api.twilio.com"), "vonage": ("POST", "rest.nexmo.com"),
    "sinch": ("POST", "us.sms.api.sinch.com"), "plivo": ("POST", "api.plivo.com"),
    "telnyx": ("POST", "api.telnyx.com"), "msgbird": ("POST", "rest.messagebird.com"),
    "clicksend": ("POST", "rest.clicksend.com"), "clickatell": ("GET", "platform.clickatell.com"),
    "bulksms": ("POST", "api.bulksms.com"), "bulkvs": ("POST", "portal.bulkvs.com"),
    "burstsms": ("POST", "api.transmitsms.com"), "d7sms": ("POST", "api.d7networks.com"),
    "httpsms": ("POST", "api.httpsms.com"), "atalk": ("POST", "api.africastalking.com"),
    "exotel": ("POST", "api.exotel.com"), "eight00com": ("POST", "api.800.com"),
    "kavenegar": ("POST", "api.kavenegar.com"), "msg91": ("POST", "control.msg91.com"),
    "mobilemessage": ("POST", "api.mobilemessage.com.au"),
    "notifyre": ("POST", "api.notifyre.com"), "ringc": ("POST", "platform.ringcentral.com"),
    "smsc": ("POST", "smsc.ru"), "voipms": ("GET", "voip.ms"),
    "sns": ("POST", "sns.eu-west-1.amazonaws.com"), "dapnet": ("POST", "www.hampager.de"),
    "mailtos": ("SMTP", "mail.example.com"), "brevo": ("POST", "api.brevo.com"),
    "mailgun": ("POST", "api.eu.mailgun.net"), "sparkpost": ("POST", "api.eu.sparkpost.com"),
    "ses": ("POST", "email.eu-west-1.amazonaws.com"),
    "azure": ("POST", "login.microsoftonline.com"), "sendgrid": ("POST", "api.sendgrid.com"),
    "postmark": ("POST", "api.postmarkapp.com"), "resend": ("POST", "api.resend.com"),
    "mailersend": ("POST", "api.mailersend.com"), "smtp2go": ("POST", "api.smtp2go.com"),
    "sendpulse": ("POST", "api.sendpulse.com"), "gotifys": ("POST", "gotify.example.com"),
    "signals": ("POST", "signal.example.com"), "apprises": ("POST", "apprise.example.com"),
    "nclouds": ("POST", "cloud.example.com"), "nctalks": ("POST", "cloud.example.com"),
    "synologys": ("POST", "nas.example.com"), "humhubs": ("POST", "humhub.example.com"),
    "smseagles": ("POST", "smseagle.example.com"),
    "evolutions": ("POST", "evolution.example.com"), "onebots": ("POST", "onebot.example.com"),
    "gorushs": ("POST", "gorush.example.com"), "parseps": ("POST", "parse.example.com"),
    "pjets": ("POST", "pushjet.example.com"), "lametrics": ("POST", "lametric.example.com"),
    "embys": ("POST", "emby.example.com"), "jellyfins": ("POST", "jellyfin.example.com"),
    "kodis": ("POST", "kodi.example.com"), "enigma2s": ("GET", "receiver.example.com"),
    "pagerduty": ("POST", "events.eu.pagerduty.com"), "splunk": ("POST", "alert.victorops.com"),
    "squadcast": ("POST", "api.squadcast.com"), "ilert": ("POST", "api.ilert.com"),
    "incidentio": ("POST", "api.incident.io"), "signl4": ("POST", "connect.signl4.com"),
    "pagertree": ("POST", "api.pagertree.com"), "spike": ("POST", "api.spike.sh"),
    "betterstack": ("POST", "incidents.betterstack.com"),
    "goalerts": ("POST", "goalert.example.com"),
    "kakaotalk": ("POST", "kauth.kakao.com"), "gupshup": ("POST", "api.gupshup.io"),
    "whapi": ("POST", "gate.whapi.cloud"), "cm": ("POST", "gw.messaging.cm.com"),
    "infobip": ("POST", "abcde1.api.infobip.com"), "kaleyra": ("POST", "api.kaleyra.io"),
    "smstunnel": ("POST", "smstunnel.io"), "telesign": ("POST", "rest-ww.telesign.com"),
    "groundmail": ("POST", "api.groundmail.dev"),
    "firehydrant": ("POST", "signals.firehydrant.com"),
    "xmatters": ("POST", "example.xmatters.com"),
}  # fmt: skip


@pytest.fixture(autouse=True)
def _on() -> Iterator[None]:
    with override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=True):
        yield


def _ids(service: Service) -> str:
    return service.example.split("://")[0]


# Services whose request goes to the host typed in the address, listed by hand (email has its
# own test). A new service with a typed host is added here, never found by a filter.
TYPED = [
    "matrixs", "mmosts", "rockets", "ntfys", "barks", "gotifys", "signals", "apprises",
    "nclouds", "nctalks", "synologys", "humhubs", "smseagles", "evolutions", "onebots",
    "gorushs", "parseps", "pjets", "lametrics", "embys", "jellyfins", "kodis", "enigma2s",
    "goalerts", "zulip", "workflows", "fluxer", "notifico", "pushdeer", "betterstack", "infobip",
    "xmatters",
]  # fmt: skip
# The form of the address that names the host, where the example names the provider.
TYPED_FORMS = {
    "fluxer": "fluxers://{host}/1234567890/webhook-token-0123456789",
    "notifico": "notificos://{host}/1234/abcdef0123456789abcdef0123456789",
    "pushdeer": "pushdeers://{host}/abcdef0123456789abcdef0123456789",
    "betterstack": "betterstack://api-token-0123456789@{host}",
}


def _typed_service(name: str) -> Service:
    return next(s for s in SERVICES if _ids(s) == name)


def _typed_url(name: str, host: str) -> str:
    """The address of the service with the host typed in it."""
    if name in TYPED_FORMS:
        return TYPED_FORMS[name].format(host=host)
    return _typed_service(name).example.replace(EXPECTED[name][1], host)


def _all_schemes() -> set[str]:
    manager = NotificationManager()
    manager.load_modules()
    return {s.lower() for s in manager.schemas(include_disabled=True)}


class _Answer:
    """Stands for the service: records the first request of a send, answers 200."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, str]] = []
        calls = self.calls

        def request(session: Any, method: str, url: str, **kwargs: Any) -> requests.Response:
            calls.append((method.upper(), requests.utils.urlparse(url).hostname or ""))
            answer = requests.Response()
            answer.status_code = 200
            answer._content = b'{"ok": true, "id": 1, "access_token": "t", "status": "ok"}'
            return answer

        class SMTP:
            def __init__(self, host: str = "", *args: Any, **kwargs: Any) -> None:
                calls.append(("SMTP", host))
                raise smtplib.SMTPConnectError(421, "test")

        monkeypatch.setattr(requests.Session, "request", request)
        monkeypatch.setattr(smtplib, "SMTP", SMTP)
        monkeypatch.setattr(smtplib, "SMTP_SSL", SMTP)


class TestTheList:
    def test_each_scheme_of_the_list_belongs_to_one_service(self) -> None:
        schemes = [s for service in SERVICES for s in service.schemes]
        assert len(schemes) == len(set(schemes))
        assert outbound.ALLOWED_SCHEMES == catalog.ALLOWED_SCHEMES == set(schemes)
        assert set(EXPECTED) == {_ids(s) for s in SERVICES}

    def test_every_scheme_of_the_list_is_one_of_apprise(self) -> None:
        assert _all_schemes() >= catalog.ALLOWED_SCHEMES

    def test_the_record_reviews_every_plugin_of_apprise(self) -> None:
        """Every scheme of the installed Apprise is in the table of ADR 0064, admitted or
        refused with a reason, and the admitted ones are exactly the list of the code."""
        admitted: set[str] = set()
        refused: dict[str, str] = {}
        for line in ADR.read_text(encoding="utf-8").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != 4 or cells[2] not in ("admitted", "refused"):
                continue
            schemes = set(re.findall(r"`([a-z0-9]+)`", cells[0]))
            if cells[2] == "admitted":
                admitted |= schemes
            else:
                assert cells[3], f"no reason for {cells[0]}"
                refused |= dict.fromkeys(schemes, cells[3])
        assert admitted == catalog.ALLOWED_SCHEMES
        assert not admitted & set(refused)
        assert admitted | set(refused) == _all_schemes()

    @pytest.mark.parametrize("scheme", sorted(_all_schemes() - catalog.ALLOWED_SCHEMES))
    def test_every_other_scheme_of_apprise_is_refused(self, scheme: str) -> None:
        with pytest.raises(outbound.RefusedError, match=r"^scheme$"):
            outbound.parse(f"{scheme}://user:password@host.example.com/abcdef0123456789")

    @pytest.mark.parametrize(
        "scheme",
        ["hassio", "hassios", "json", "jsons", "xml", "xmls", "form", "forms", "fcm", "vapid",
         "opsgenie", "jira", "syslog", "rsyslog", "mqtt", "mqtts", "notica", "pinglet",
         "flowtriq", "mastodon", "bluesky", "github"],
    )  # fmt: skip
    def test_the_services_refused_for_a_reason_stay_out(self, scheme: str) -> None:
        """A Home Assistant token, a generic webhook, a file read on the server, a method or
        a whole path chosen by the address, a public post: never in the list."""
        assert scheme not in catalog.ALLOWED_SCHEMES

    @pytest.mark.parametrize("url", ["kodi://kodi.example.com", "xbmc://kodi.example.com"])
    def test_the_schemes_are_read_on_the_class_of_the_plugin(self, url: str) -> None:
        """The Kodi plugin keeps the version of its protocol in `protocol`, an integer: the
        schemes are read on the class, so its plain schemes are not refused."""
        assert outbound.parse(url).scheme == url.split("://")[0]

    def test_each_service_has_a_family_a_place_and_a_page(self) -> None:
        for service in SERVICES:
            assert service.family in catalog.FAMILIES
            assert service.where in catalog.WHERE
            assert re.fullmatch(r"https://appriseit\.com/services/[a-z0-9_]+/", service.link)
        assert [key for key, _, items in catalog.by_family() if items] == list(catalog.FAMILIES)


@pytest.mark.parametrize("service", SERVICES, ids=_ids)
class TestEachService:
    def test_its_example_is_accepted_and_calls_its_host(
        self, monkeypatch: pytest.MonkeyPatch, service: Service
    ) -> None:
        parsed = outbound.parse(service.example)
        assert parsed.scheme in service.schemes
        answer = _Answer(monkeypatch)
        outbound.deliver(outbound.seal(service.example), title="Fixed", body="Body")
        assert answer.calls, "nothing was sent"
        assert answer.calls[0] == EXPECTED[_ids(service)]

    def test_its_plugin_is_held_to_the_rules(self, service: Service) -> None:
        plugin = outbound._plugin(service.example)
        assert plugin.verify_certificate is True
        assert plugin.socket_connect_timeout == plugin.socket_read_timeout == 5
        assert (plugin.retry, plugin.wait) == (0, 0)
        assert getattr(plugin, "include_image", False) is False

    def test_its_address_is_never_shown(
        self, client: Client, api_client: APIClient, alice: User, service: Service
    ) -> None:
        """No part of the address that the screens did not show before is shown after."""
        client.force_login(alice)
        api_client.force_authenticate(alice)
        examples = [escape(s.example) for s in SERVICES]

        def outputs(channel_id: Any = None) -> list[str]:
            pages = [
                client.get(reverse("notifications:index")).text,
                client.get(reverse("notifications:export")).text,
                api_client.get(reverse("api-v1:notification-channel-list")).text,
            ]
            if channel_id is not None:
                page = reverse("notifications:channel", kwargs={"channel_id": channel_id})
                pages.append(client.get(page).text)
            # Without the list of services, which shows every example on purpose.
            for example in examples:
                pages = [page.replace(example, "") for page in pages]
            return pages

        before = " ".join(outputs()).lower()
        channel = services.add_channel(alice, name="Channel", url=service.example)
        shown = f"{channel.scheme} {channel.service}".lower()
        parts = {
            part
            for part in re.split(r"[/:@?&=#.]", service.example.split("://", 1)[1])
            if len(part) >= 5 and part.lower() not in before and part.lower() not in shown
        }
        assert parts, service.example
        for output in [*outputs(channel.id), repr(channel.__dict__)]:
            for part in parts:
                assert part.lower() not in output.lower()


@pytest.mark.parametrize("name", TYPED)
class TestTypedHosts:
    """The guard of docs/adr/0059 holds for every service whose host is typed."""

    def test_the_request_goes_to_the_host_typed(
        self, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        url = _typed_url(name, "typed.example.org")
        answer = _Answer(monkeypatch)
        outbound.deliver(outbound.seal(url), title="Fixed", body="Body")
        assert answer.calls
        assert answer.calls[0][1] == "typed.example.org"

    def test_a_private_address_typed_as_is_is_refused(self, name: str) -> None:
        url = _typed_url(name, "100.64.20.30")
        with pytest.raises(outbound.RefusedError, match=r"^private$"):
            outbound.parse(url)
        with override_settings(HOUSECAIRN_NOTIFICATIONS_PRIVATE_NETWORKS=True):
            assert outbound.parse(url).scheme in _typed_service(name).schemes

    def test_a_name_that_leads_to_a_private_address_is_not_called(
        self, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        monkeypatch.setattr(
            outbound,
            "_resolve",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.20.30", 443))],
        )
        connected: list[Any] = []
        monkeypatch.setattr(socket.socket, "connect", lambda self, a: connected.append(a))
        sealed = outbound.seal(_typed_url(name, "typed.example.org"))
        assert outbound.deliver(sealed, title="t", body="b") is outbound.Outcome.PRIVATE
        assert connected == []


class TestMailServerOption:
    """`?smtp=` of an email address changes the server that is called: same guard."""

    URL = "mailtos://user:password@mail.example.com/?to=you@example.com&smtp={host}"

    def test_a_private_address_in_the_option_is_not_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connected: list[Any] = []
        monkeypatch.setattr(socket.socket, "connect", lambda self, a: connected.append(a))
        sealed = outbound.seal(self.URL.format(host="100.64.20.30"))
        assert outbound.deliver(sealed, title="t", body="b") is outbound.Outcome.PRIVATE
        assert connected == []

    def test_a_name_in_the_option_that_leads_to_a_private_address_is_not_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            outbound,
            "_resolve",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.20.30", 587))],
        )
        connected: list[Any] = []
        monkeypatch.setattr(socket.socket, "connect", lambda self, a: connected.append(a))
        sealed = outbound.seal(self.URL.format(host="relay.example.org"))
        assert outbound.deliver(sealed, title="t", body="b") is outbound.Outcome.PRIVATE
        assert connected == []
