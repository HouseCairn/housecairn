"""Invitations to the instance: an account without a home (docs/adr/0022)."""

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.audit.models import AuditEvent
from housecairn.instance import services
from housecairn.instance.models import InstanceInvitation

pytestmark = pytest.mark.django_db

SENT_AT = datetime(2026, 11, 9, 22, 22, 59, 900000, tzinfo=UTC)
PASSWORD = "a-long-and-unusual-passphrase"
EMAIL = {
    "EMAIL_HOST": "smtp.example.com",
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
}


@pytest.fixture(autouse=True)
def _email() -> Iterator[None]:
    cache.clear()
    with override_settings(**EMAIL):
        yield
    cache.clear()


@pytest.fixture
def admin_user(make_user: Callable[..., User]) -> User:
    return make_user("admin@example.com", is_staff=True, is_superuser=True)


def link(raw: str) -> str:
    return reverse("instance-invitation", kwargs={"token": raw})


def sent(admin: User, email: str = "new@example.com") -> str:
    """Invite without a mail server in the way, and return the secret of the link."""
    box: list[str] = []
    services.invite(admin, email=email, send=lambda _invitation, raw: box.append(raw))
    return box[0]


def _secret(text: str) -> str:
    match = re.search(r"/join/(hcn_[\w-]+)/", text)
    assert match, text
    return match.group(1)


class TestRules:
    def test_only_an_administrator_invites(self, alice: User) -> None:
        with pytest.raises(PermissionDenied):
            services.invite(alice, email="x@example.com", send=lambda i, r: None)

    def test_only_the_digest_is_stored_and_the_link_works_once(self, admin_user: User) -> None:
        raw = sent(admin_user)
        invitation = InstanceInvitation.objects.get()
        assert raw.startswith("hcn_")
        assert raw not in invitation.digest
        client = Client()
        response = client.post(link(raw), {"password1": PASSWORD, "password2": PASSWORD})
        assert response.status_code == 302
        assert response["Location"] == reverse("homes:create")
        user = User.objects.get(email="new@example.com")
        assert not user.is_staff
        assert EmailAddress.objects.get(user=user).verified
        assert services.find(raw) is None
        assert Client().get(link(raw)).status_code == 404

    def test_dead_links_all_look_the_same(self, admin_user: User, client: Client) -> None:
        expired = sent(admin_user, "a@example.com")
        InstanceInvitation.objects.filter(email="a@example.com").update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        revoked = sent(admin_user, "b@example.com")
        services.revoke(admin_user, InstanceInvitation.objects.get(email="b@example.com"))
        replaced = sent(admin_user, "c@example.com")
        services.resend(
            admin_user,
            InstanceInvitation.objects.get(email="c@example.com"),
            send=lambda i, r: None,
        )
        pages = set()
        for raw in (expired, revoked, replaced, "hcn_" + "x" * 43, "hci_wrongprefix", "nope"):
            response = client.get(link(raw))
            assert response.status_code == 404
            assert "does not work" in response.text
            # The form token is masked anew on each page (the language form has one).
            text = re.sub(r'name="csrfmiddlewaretoken" value="[^"]*"', "", response.text)
            pages.add(re.sub(r'"X-CSRFToken": "[^"]*"', "", text))
            assert client.post(link(raw), {"password1": PASSWORD}).status_code == 404
        assert len(pages) == 1
        assert not User.objects.filter(email__in=["a@example.com", "b@example.com"]).exists()

    def test_one_open_invitation_per_address_in_python_and_in_the_database(
        self, admin_user: User
    ) -> None:
        sent(admin_user)
        with pytest.raises(ValidationError):
            services.invite(admin_user, email="NEW@example.com", send=lambda i, r: None)
        first = InstanceInvitation.objects.get()
        duplicate = InstanceInvitation(
            email="New@Example.com",
            digest="f" * 64,
            expires_at=first.expires_at,
            sent_at=first.sent_at,
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_a_lapsed_invitation_can_be_replaced(self, admin_user: User) -> None:
        old = sent(admin_user)
        InstanceInvitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        fresh = sent(admin_user)
        assert services.find(old) is None
        assert services.find(fresh) is not None

    def test_another_address_cannot_use_a_link(
        self, admin_user: User, bob: User, client: Client
    ) -> None:
        raw = sent(admin_user)
        client.force_login(bob)
        response = client.post(link(raw))
        assert "another address" in response.text
        assert services.find(raw) is not None
        with pytest.raises(PermissionDenied):
            services.accept(bob, raw)

    def test_an_existing_account_accepts_after_signing_in(
        self, admin_user: User, bob: User, client: Client
    ) -> None:
        raw = sent(admin_user, "bob@example.com")
        attempt = client.post(link(raw), {"password1": PASSWORD, "password2": PASSWORD})
        assert "sign in to accept" in attempt.text
        assert User.objects.filter(email="bob@example.com").count() == 1
        client.force_login(bob)
        assert "Accept the invitation" in client.get(link(raw)).text
        assert client.post(link(raw)).status_code == 302
        assert services.find(raw) is None

    def test_a_weak_password_creates_nothing(self, admin_user: User, client: Client) -> None:
        raw = sent(admin_user)
        response = client.post(link(raw), {"password1": "short", "password2": "short"})
        assert response.status_code == 200
        assert not User.objects.filter(email="new@example.com").exists()
        assert services.find(raw) is not None

    @override_settings(ACCOUNT_RATE_LIMITS={"housecairn_invitation": "3/m/ip"})
    def test_opening_links_is_rate_limited_per_client_not_per_header(self) -> None:
        client = Client(REMOTE_ADDR="203.0.113.5")
        codes = [
            client.get(link(f"hcn_guess{i}"), headers={"X-Forwarded-For": f"198.51.100.{i}"})
            for i in range(5)
        ]
        assert [r.status_code for r in codes] == [404, 404, 404, 429, 429]

    def test_the_invitation_answer_does_not_depend_on_an_account_existing(
        self, admin_user: User, client: Client, bob: User
    ) -> None:
        client.force_login(admin_user)
        url = reverse("instance:invite")
        # The message states when the link expires, to the minute. Freeze the clock so a
        # minute change between the two sends cannot make the bodies differ.
        with mock.patch("django.utils.timezone.now", return_value=SENT_AT):
            known = client.post(url, {"email": "bob@example.com"}, follow=True)
            unknown = client.post(url, {"email": "nobody@example.com"}, follow=True)

        def message(text: str) -> str:
            return re.findall(r'<li class="message[^"]*">([^<]*)</li>', text)[0]

        assert message(known.text) == message(unknown.text) == "Invitation sent."
        assert [m.subject for m in mail.outbox] == [mail.outbox[0].subject] * 2
        bodies = [re.sub(r"hcn_[\w-]+", "T", m.body) for m in mail.outbox]
        assert bodies[0] == bodies[1]


class TestScreens:
    def test_invite_resend_and_revoke_by_the_administrator(
        self, client: Client, admin_user: User
    ) -> None:
        client.force_login(admin_user)
        response = client.post(
            reverse("instance:invite"), {"email": "new@example.com"}, follow=True
        )
        assert "Invitation sent." in response.text
        message = mail.outbox[0]
        assert message.to == ["new@example.com"]
        assert message.subject == "HouseCairn: you are invited to create an account"
        html, mimetype = message.alternatives[0]
        assert mimetype == "text/html"
        raw = _secret(message.body)
        assert raw == _secret(str(html))
        assert "http://testserver/join/" in message.body
        invitation = InstanceInvitation.objects.get()
        kwargs = {"invitation_id": invitation.id}
        client.post(reverse("instance:invitation-resend", kwargs=kwargs))
        assert services.find(raw) is None
        assert services.find(_secret(mail.outbox[1].body)) is not None
        client.post(reverse("instance:invitation-revoke", kwargs=kwargs))
        assert services.find(_secret(mail.outbox[1].body)) is None

    @override_settings(EMAIL_HOST="")
    def test_without_email_the_form_is_hidden_and_the_action_refused(
        self, client: Client, admin_user: User
    ) -> None:
        client.force_login(admin_user)
        page = client.get(reverse("instance:overview"))
        assert reverse("instance:invite") not in page.text
        assert "cannot send email" in page.text
        response = client.post(reverse("instance:invite"), {"email": "x@example.com"}, follow=True)
        assert "cannot send email" in response.text
        assert not InstanceInvitation.objects.exists()

    @override_settings(ACCOUNT_RATE_LIMITS={"housecairn_instance_invite": "2/m/user"})
    def test_inviting_is_rate_limited(self, client: Client, admin_user: User) -> None:
        client.force_login(admin_user)
        for i in range(3):
            response = client.post(
                reverse("instance:invite"), {"email": f"p{i}@example.com"}, follow=True
            )
        assert "Too many invitations" in response.text
        assert InstanceInvitation.objects.count() == 2

    def test_a_failing_mail_server_is_reported_not_a_500(
        self, client: Client, admin_user: User
    ) -> None:
        from unittest import mock

        client.force_login(admin_user)
        with mock.patch(
            "housecairn.instance.emails.EmailMultiAlternatives.send",
            side_effect=ConnectionRefusedError("smtp down"),
        ):
            response = client.post(
                reverse("instance:invite"), {"email": "new@example.com"}, follow=True
            )
        assert response.status_code == 200
        assert "could not be sent" in response.text
        assert InstanceInvitation.objects.get().is_pending

    def test_the_overview_lists_pending_invitations_and_counts(
        self, client: Client, admin_user: User
    ) -> None:
        sent(admin_user, "pending@example.com")
        client.force_login(admin_user)
        page = client.get(reverse("instance:overview"))
        assert "pending@example.com" in page.text
        assert "Accounts" in page.text


class TestApi:
    def test_invite_list_resend_revoke(self, api_client: APIClient, admin_user: User) -> None:
        api_client.force_authenticate(admin_user)
        url = reverse("api-v1:instance-invitation-list")
        created = api_client.post(url, {"email": "new@example.com"})
        assert created.status_code == 201
        assert "link" not in created.json()
        assert "hcn_" not in created.text
        listed = api_client.get(url).json()["results"]
        assert [i["email"] for i in listed] == ["new@example.com"]
        old = _secret(mail.outbox[0].body)
        invitation_id = listed[0]["id"]
        resent = api_client.post(
            reverse("api-v1:instance-invitation-resend", kwargs={"invitation_id": invitation_id})
        )
        assert resent.status_code == 200
        assert services.find(old) is None
        deleted = api_client.delete(
            reverse("api-v1:instance-invitation-detail", kwargs={"invitation_id": invitation_id})
        )
        assert deleted.status_code == 204
        assert api_client.get(url).json()["results"] == []

    @override_settings(EMAIL_HOST="")
    def test_without_email_the_api_says_so(self, api_client: APIClient, admin_user: User) -> None:
        api_client.force_authenticate(admin_user)
        response = api_client.post(
            reverse("api-v1:instance-invitation-list"), {"email": "x@example.com"}
        )
        assert response.status_code == 400

    def test_the_audit_log_of_the_instance(self, api_client: APIClient, admin_user: User) -> None:
        sent(admin_user)
        api_client.force_authenticate(admin_user)
        body = api_client.get(reverse("api-v1:instance-audit-list")).json()
        assert [e["action"] for e in body["results"]] == ["instance_invitation.sent"]
        assert body["results"][0]["actor_email"] == "admin@example.com"
        assert AuditEvent.objects.count() == 1


class TestOidcInvitedAddress:
    def test_an_identity_for_an_invited_address_uses_the_invitation_up(
        self, admin_user: User, make_user: Callable[..., User]
    ) -> None:
        sent(admin_user, "fresh@example.com")
        user = make_user("fresh@example.com")
        services.accept_for_identity(user)
        assert not services.pending_invitations().exists()
        assert InstanceInvitation.objects.get().accepted_by == user


@pytest.mark.django_db
class TestTwoSubmissionsOfOneLink:
    def test_the_second_one_gets_the_account_exists_page_and_not_a_500(
        self, admin_user: User, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The other submission wins between the check and the insertion: the unique
        # address then refuses the second one, which is told so.
        raw = sent(admin_user)
        original = User.objects.create_user

        def winner_first(*args: object, **kwargs: object) -> User:
            original(email="new@example.com", password="Another-Pass-123")
            return original(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(User.objects, "create_user", winner_first)
        response = client.post(
            link(raw), {"password1": "Correct-Horse-9", "password2": "Correct-Horse-9"}
        )
        assert response.status_code == 200
        assert "already" in response.text

    def test_the_service_says_so_in_a_validation_error(
        self, admin_user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw = sent(admin_user)
        original = User.objects.create_user

        def winner_first(*args: object, **kwargs: object) -> User:
            original(email="new@example.com", password="Another-Pass-123")
            return original(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(User.objects, "create_user", winner_first)
        with pytest.raises(ValidationError, match="already uses this address"):
            services.create_account(raw, password="Correct-Horse-9")
        assert services.find(raw) is not None
