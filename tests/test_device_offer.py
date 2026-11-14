"""The offer to receive the reminders on this device, on Today (docs/adr/0072).

The server renders the offer only when notifications and Web Push are on, a person is
signed in and the instance is not a demonstration. What depends on the browser (support,
permission, a device already added, a postponed offer, the name of the device) is decided
by static/js/webpush.js and checked in a browser by tests/e2e/device_offer_checks.py.
"""

import hashlib
from collections.abc import Iterator

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import Client, RequestFactory
from django.test.utils import override_settings
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.homes.models import Home
from housecairn.notifications import devices
from tests.htmlscan import inline_code_problems
from tests.test_webpush import ENDPOINT, FakePushService, add_device, push  # noqa: F401

pytestmark = pytest.mark.django_db

MARK = "data-webpush-offer"


@pytest.fixture(autouse=True)
def _on() -> Iterator[None]:
    with override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=True, HOUSECAIRN_WEBPUSH_ENABLED=True):
        yield


def today(client: Client, user: User, home: Home) -> str:
    client.force_login(user)
    return client.get(reverse("tasks:today", args=[home.id])).content.decode()


class TestWhoIsOffered:
    def test_a_person_signed_in_is_offered_the_device(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        page = today(client, alice, alice_home)
        assert MARK in page
        assert "Receive the reminders on this device?" in page
        assert f'data-key="{devices.public_key()}"' in page
        assert f'data-person="{alice.pk}"' in page
        assert reverse("api-v1:push-device-list") in page
        assert "js/webpush.js" in page

    def test_the_offer_is_hidden_until_the_script_has_looked_at_the_browser(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        page = today(client, alice, alice_home)
        start = page.index(MARK)
        assert (
            " hidden>" in page[start : page.index(">", page.index("data-name-fallback", start)) + 1]
        )

    def test_all_homes_together_make_the_offer_too(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        from housecairn.homes import services as home_services

        home_services.create_home(alice, name="Seaside flat")
        client.force_login(alice)
        client.post(reverse("homes:current"), {"home": "all"})
        page = client.get(reverse("index")).content.decode()
        assert "digest" in page  # the page of all the homes, not Today of one
        assert MARK in page

    def test_notifications_off_no_offer(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        with override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=False):
            assert MARK not in today(client, alice, alice_home)

    def test_web_push_off_no_offer(self, client: Client, alice: User, alice_home: Home) -> None:
        with override_settings(HOUSECAIRN_WEBPUSH_ENABLED=False):
            assert MARK not in today(client, alice, alice_home)

    def test_a_demonstration_instance_makes_no_offer(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        settings.HOUSECAIRN_DEMO = True
        try:
            assert MARK not in today(client, alice, alice_home)
        finally:
            settings.HOUSECAIRN_DEMO = False

    def test_nobody_signed_in_no_offer(self) -> None:
        request = RequestFactory().get("/")
        for user in (AnonymousUser(), None):
            request.user = user  # type: ignore[assignment]
            html = Template("{% load notifications_extras %}{% device_banner %}").render(
                Context({"request": request})
            )
            assert MARK not in html
        assert (
            Template("{% load notifications_extras %}{% device_banner %}")
            .render(Context({}))
            .strip()
            == ""
        )

    def test_no_key_is_made_for_an_instance_that_does_not_send(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        from housecairn.notifications.models import PushKey

        with override_settings(HOUSECAIRN_WEBPUSH_ENABLED=False):
            today(client, alice, alice_home)
        assert not PushKey.objects.exists()


class TestKnownDevices:
    def test_the_hashes_of_the_devices_of_the_person_are_given_to_the_script(
        self,
        client: Client,
        alice: User,
        alice_home: Home,
        push: FakePushService,  # noqa: F811
    ) -> None:
        add_device(alice, push)
        page = today(client, alice, alice_home)
        digest = hashlib.sha256(ENDPOINT.encode()).hexdigest()
        assert f'data-known="{digest}"' in page
        assert "fcm.googleapis.com" not in page  # the endpoint itself is never in a page

    def test_the_devices_of_someone_else_are_not(
        self,
        client: Client,
        alice: User,
        bob: User,
        alice_home: Home,
        push: FakePushService,  # noqa: F811
    ) -> None:
        add_device(bob, push)
        assert 'data-known=""' in today(client, alice, alice_home)

    def test_the_offer_for_the_script_lists_only_hashes(
        self,
        alice: User,
        push: FakePushService,  # noqa: F811
    ) -> None:
        add_device(alice, push)
        offer = devices.offer_for(alice)
        assert offer is not None
        assert set(offer) == {"key", "person", "known"}
        assert offer["known"] == hashlib.sha256(ENDPOINT.encode()).hexdigest()


class TestWhatThePageSays:
    def test_the_words_of_the_script_come_from_the_page_in_the_language_of_the_person(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        page = today(client, alice, alice_home)
        assert 'data-name-pattern="{browser} on {system}"' in page
        assert 'data-name-fallback="This device"' in page
        for hook in (
            "data-offer-enable",
            "data-offer-later",
            "data-offer-never",
            "data-offer-install",
            "data-offer-done",
            "data-offer-denied",
            "data-offer-failed",
        ):
            assert hook in page

    def test_the_offer_is_in_french_for_a_french_person(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        client.force_login(alice)
        page = client.get(
            reverse("tasks:today", args=[alice_home.id]), HTTP_ACCEPT_LANGUAGE="fr"
        ).content.decode()
        assert "Recevoir les rappels sur cet appareil ?" in page
        assert 'data-name-pattern="{browser} sur {system}"' in page

    def test_no_inline_script_nor_style(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        assert inline_code_problems(today(client, alice, alice_home)) == []

    def test_the_notifications_screen_says_how_to_unblock(
        self, client: Client, alice: User
    ) -> None:
        client.force_login(alice)
        page = client.get(reverse("notifications:index")).content.decode()
        assert "data-webpush-blocked" in page
        assert "allow notifications for this site in the browser settings" in page
