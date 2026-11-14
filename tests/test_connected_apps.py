"""Connected apps on the access page: list, edit, revoke, and the nightly clean-up of the
toolkit's tokens (docs/adr/0078)."""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django_q.models import Schedule
from oauth2_provider.models import AccessToken, RefreshToken

from housecairn.accounts.models import User
from housecairn.apitokens import tasks
from housecairn.apitokens.models import AppConnection, ConnectionAccessToken
from housecairn.audit.models import AuditEvent
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from tests.oauth_support import connect, consent, refresh

pytestmark = pytest.mark.django_db

LIST = "/me/tokens/"


def _signed_in(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


class TestList:
    def test_lists_the_connection_with_its_homes(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        page = _signed_in(alice).get(LIST)
        assert page.status_code == 200
        text = page.content.decode()
        assert 'id="connected-apps"' in text
        assert "Test phone" in text
        assert alice_home.name in text
        assert reverse("api-tokens:connection-revoke", args=[phone.connection.id]) in text

    def test_the_name_given_by_the_app_is_escaped(self, alice: User, alice_home: Home) -> None:
        client = _signed_in(alice)
        answer, _verifier = consent(client, [alice_home], device_name="<b>Admin</b>")
        assert answer.status_code == 302
        text = client.get(LIST).content.decode()
        assert "<b>Admin</b>" not in text
        assert "&lt;b&gt;Admin&lt;/b&gt;" in text

    def test_a_revoked_connection_is_not_listed(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        client = _signed_in(alice)
        client.post(reverse("api-tokens:connection-revoke", args=[phone.connection.id]))
        assert "Test phone" not in client.get(LIST).content.decode()

    def test_empty(self, alice: User, alice_home: Home) -> None:
        text = _signed_in(alice).get(LIST).content.decode()
        assert "No connected app yet" in text


class TestEdit:
    def test_rename_and_change_homes(self, alice: User, alice_home: Home) -> None:
        second = home_services.create_home(alice, name="Cabin")
        phone = connect(alice, [alice_home])
        url = reverse("api-tokens:connection-edit", args=[phone.connection.id])
        client = _signed_in(alice)
        assert client.get(url).status_code == 200
        answer = client.post(
            url,
            {
                "name": "  Kitchen\u202e tablet  ",
                "homes": [str(second.id)],
                "may_create_homes": "on",
            },
        )
        assert answer.status_code == 302
        phone.connection.refresh_from_db()
        assert phone.connection.name == "Kitchen tablet"
        assert list(phone.connection.homes.all()) == [second]
        assert phone.connection.may_create_homes is True
        assert AuditEvent.objects.filter(action="appconnection.changed").count() == 1

    def test_a_home_is_required(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        url = reverse("api-tokens:connection-edit", args=[phone.connection.id])
        answer = _signed_in(alice).post(url, {"name": "Phone"})
        assert answer.status_code == 200
        phone.connection.refresh_from_db()
        assert list(phone.connection.homes.all()) == [alice_home]

    def test_another_persons_connection_is_not_found(
        self, alice: User, alice_home: Home, bob: User
    ) -> None:
        phone = connect(alice, [alice_home])
        client = _signed_in(bob)
        edit = reverse("api-tokens:connection-edit", args=[phone.connection.id])
        revoke = reverse("api-tokens:connection-revoke", args=[phone.connection.id])
        assert client.get(edit).status_code == 404
        assert client.post(edit, {"name": "x", "homes": [str(alice_home.id)]}).status_code == 404
        assert client.get(revoke).status_code == 404
        assert client.post(revoke).status_code == 404
        phone.connection.refresh_from_db()
        assert phone.connection.revoked_at is None
        assert phone.connection.name == "Test phone"

    def test_signed_out_goes_to_sign_in(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        answer = Client().get(reverse("api-tokens:connection-edit", args=[phone.connection.id]))
        assert answer.status_code == 302
        assert "/accounts/login/" in answer["Location"]


class TestRevoke:
    def test_confirmation_then_revocation(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        url = reverse("api-tokens:connection-revoke", args=[phone.connection.id])
        client = _signed_in(alice)
        page = client.get(url)
        assert page.status_code == 200
        phone.connection.refresh_from_db()
        assert phone.connection.revoked_at is None  # the page alone revokes nothing
        assert client.post(url).status_code == 302
        phone.connection.refresh_from_db()
        assert phone.connection.revoked_at is not None
        assert not ConnectionAccessToken.objects.filter(connection=phone.connection).exists()
        answer = Client().get(
            "/api/v1/sync/info", secure=True, HTTP_AUTHORIZATION=f"Bearer {phone.access}"
        )
        assert answer.status_code == 401


class TestNightlyCleanUp:
    def test_scheduled_once(self) -> None:
        tasks.schedule()
        tasks.schedule()  # once, whatever the number of migrations
        found = Schedule.objects.get(name=tasks.SCHEDULE)
        assert found.func == "housecairn.apitokens.tasks.clear_expired"
        assert found.schedule_type == Schedule.DAILY
        assert Schedule.objects.filter(name=tasks.SCHEDULE).count() == 1

    def test_a_phone_in_use_keeps_its_tokens(self, alice: User, alice_home: Home) -> None:
        connect(alice, [alice_home])
        AccessToken.objects.update(expires=timezone.now() - timedelta(days=1))
        tasks.clear_expired()
        assert AccessToken.objects.count() == 1
        assert RefreshToken.objects.count() == 1

    def test_idle_for_90_days(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        AccessToken.objects.update(expires=timezone.now() - timedelta(days=91))
        # Refused at use, before any clean-up: the expiry is idle-based.
        assert refresh(Client(), phone.refresh).status_code == 400
        tasks.clear_expired()
        assert not AccessToken.objects.exists()
        assert not RefreshToken.objects.exists()
        assert AppConnection.objects.filter(pk=phone.connection.pk, revoked_at=None).exists()

    def test_a_rotated_refresh_token_is_kept_for_reuse_detection(
        self, alice: User, alice_home: Home
    ) -> None:
        phone = connect(alice, [alice_home])
        refresh(Client(), phone.refresh)
        tasks.clear_expired()
        assert refresh(Client(), phone.refresh).status_code == 400
        phone.connection.refresh_from_db()
        assert phone.connection.revoked_at is not None
