"""Invitations: one-time links, expiry, revocation, no account enumeration (docs/adr/0020)."""

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from unittest import mock
from urllib.parse import quote

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.apitokens import services as token_services
from housecairn.homes import services
from housecairn.homes.models import Home, Invitation, Membership, Role
from housecairn.homes.services import INVITATION_PREFIX

pytestmark = pytest.mark.django_db

SENT_AT = datetime(2026, 11, 9, 22, 22, 59, 900000, tzinfo=UTC)

# Fixtures are created once per test and shared by its examples, on purpose.
shared_fixtures = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])

PASSWORD = "a-long-and-unusual-passphrase"
NO_DELIVERY = services.Delivery()
EMAIL = {
    "EMAIL_HOST": "smtp.example.com",
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
}


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


def invite(owner: User, home: Home, email: str = "new@example.com", role: str = "member") -> str:
    _invitation, raw = services.invite(owner, home, email=email, role=role, delivery=NO_DELIVERY)
    assert raw is not None
    return raw


def verified(user: User, email: str | None = None) -> User:
    """The account with its address verified, as after a confirmation link."""
    EmailAddress.objects.update_or_create(
        user=user, email=email or user.email, defaults={"verified": True, "primary": True}
    )
    return user


def link(raw: str) -> str:
    return reverse("invitation", kwargs={"token": raw})


def link_in(text: str) -> str:
    match = re.search(r"https?://[^\s\"<]+/invitations/(hci_[\w-]+)/", text)
    assert match, text
    return match.group(1)


class TestRules:
    def test_only_the_digest_is_stored(self, alice: User, alice_home: Home) -> None:
        raw = invite(alice, alice_home)
        invitation = Invitation.objects.get()
        assert raw.startswith("hci_")
        assert raw not in invitation.digest
        assert invitation.digest == services.hash_invitation(raw)

    def test_only_owners_invite(
        self, alice_home: Home, bob: User, add_member: Callable[..., Membership]
    ) -> None:
        add_member(alice_home, bob, Role.MEMBER)
        with pytest.raises(PermissionDenied):
            invite(bob, alice_home)

    def test_accept_once(
        self, alice: User, alice_home: Home, make_user: Callable[..., User]
    ) -> None:
        raw = invite(alice, alice_home, role="read_only")
        new = verified(make_user("New@example.com"))
        membership = services.accept_invitation(new, raw)
        assert membership.role == Role.READ_ONLY
        assert services.find_invitation(raw) is None
        with pytest.raises(Invitation.DoesNotExist):
            services.accept_invitation(new, raw)

    def test_another_address_cannot_use_the_link(
        self, alice: User, bob: User, alice_home: Home
    ) -> None:
        raw = invite(alice, alice_home)
        with pytest.raises(PermissionDenied):
            services.accept_invitation(bob, raw)
        assert services.role_of(bob, alice_home) is None
        assert services.find_invitation(raw) is not None

    def test_expired_revoked_and_replaced_links_open_nothing(
        self, alice: User, alice_home: Home
    ) -> None:
        expired = invite(alice, alice_home, "a@example.com")
        Invitation.objects.filter(email="a@example.com").update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        revoked = invite(alice, alice_home, "b@example.com")
        services.revoke_invitation(alice, Invitation.objects.get(email="b@example.com"))
        replaced = invite(alice, alice_home, "c@example.com")
        _inv, fresh = services.resend_invitation(
            alice, Invitation.objects.get(email="c@example.com"), delivery=NO_DELIVERY
        )
        assert fresh is not None
        for raw in (expired, revoked, replaced, "hci_unknown", "not-even-a-token", ""):
            assert services.find_invitation(raw) is None
        assert services.find_invitation(fresh) is not None

    def test_an_existing_member_keeps_their_role(
        self, alice: User, bob: User, alice_home: Home, add_member: Callable[..., Membership]
    ) -> None:
        add_member(alice_home, bob, Role.OWNER)
        with pytest.raises(ValidationError):
            invite(alice, alice_home, "bob@example.com")

    def test_one_pending_invitation_per_address(self, alice: User, alice_home: Home) -> None:
        invite(alice, alice_home)
        with pytest.raises(ValidationError):
            invite(alice, alice_home, "NEW@example.com")

    def test_invitations_of_another_home_are_out_of_reach(
        self, alice: User, bob: User, alice_home: Home, bob_home: Home
    ) -> None:
        invite(bob, bob_home)
        theirs = Invitation.objects.get(home=bob_home)
        with pytest.raises(PermissionDenied):
            services.revoke_invitation(alice, theirs)
        with pytest.raises(PermissionDenied):
            services.resend_invitation(alice, theirs, delivery=NO_DELIVERY)


def invite_by_email(
    owner: User, home: Home, email: str = "new@example.com", role: str = "member"
) -> str:
    """An invitation whose link went to the address by email (and nowhere else)."""
    sent: list[str] = []
    delivery = services.Delivery(send=lambda _invitation, raw: sent.append(raw))
    services.invite(owner, home, email=email, role=role, delivery=delivery)
    return sent[0]


class TestWebAcceptance:
    def test_new_person_creates_an_account_and_joins(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        raw = invite_by_email(alice, alice_home, role="read_only")
        page = client.get(link(raw))
        assert page.status_code == 200
        assert "Join Main house" in page.text
        assert "new@example.com" in page.text
        response = client.post(link(raw), {"password1": PASSWORD, "password2": PASSWORD})
        assert response.status_code == 302
        user = User.objects.get(email="new@example.com")
        assert services.role_of(user, alice_home) == Role.READ_ONLY
        assert not user.is_staff
        # The link went to the mailbox: opening it proves the address.
        assert EmailAddress.objects.get(user=user).verified
        assert client.get(response["Location"]).status_code == 200
        # Used: the same link now opens nothing, even for the new account.
        reused = client.get(link(raw))
        assert reused.status_code == 404
        assert "does not work" in reused.text

    def test_a_handed_over_link_creates_no_account_and_says_nothing_of_accounts(
        self, client: Client, alice: User, alice_home: Home, make_user: Callable[..., User]
    ) -> None:
        """Without email the owner holds the link: it neither takes an address nor probes it."""
        make_user("known@example.com")
        known = invite(alice, alice_home, "known@example.com")
        unknown = invite(alice, alice_home, "nobody@example.com")
        pages = []
        for raw, address in ((known, "known@example.com"), (unknown, "nobody@example.com")):
            visit = client.get(link(raw))
            attempt = client.post(link(raw), {"password1": PASSWORD, "password2": PASSWORD})
            for page in (visit, attempt):
                assert page.status_code == 200
                assert "cannot create an account" in page.text
                assert "Create the account and join" not in page.text
                text = page.text.replace(address, "X").replace(quote(raw, safe=""), "T")
                # The form token is masked anew on each page (the language form has one).
                text = re.sub(r'name="csrfmiddlewaretoken" value="[^"]*"', "CSRF", text)
                pages.append(re.sub(r'"X-CSRFToken": "[^"]*"', "CSRF", text))
        assert len(set(pages)) == 1
        assert not User.objects.filter(email="nobody@example.com").exists()
        assert User.objects.filter(email="known@example.com").count() == 1
        assert services.find_invitation(unknown) is not None

    def test_the_service_refuses_to_create_an_account_from_a_handed_over_link(
        self, alice: User, alice_home: Home
    ) -> None:
        raw = invite(alice, alice_home)
        with pytest.raises(PermissionDenied):
            services.create_account_for_invitation(raw, password=PASSWORD)
        assert not User.objects.filter(email="new@example.com").exists()

    def test_accepting_a_link_sent_by_email_verifies_the_address(
        self, alice: User, alice_home: Home, make_user: Callable[..., User]
    ) -> None:
        raw = invite_by_email(alice, alice_home, "carol@example.com")
        carol = make_user("carol@example.com")
        assert not EmailAddress.objects.filter(user=carol, verified=True).exists()
        services.accept_invitation(carol, raw)
        assert EmailAddress.objects.get(user=carol).verified

    def test_accepting_a_handed_over_link_does_not_verify_the_address(
        self, alice: User, alice_home: Home, make_user: Callable[..., User]
    ) -> None:
        raw = invite(alice, alice_home, "carol@example.com")
        carol = make_user("carol@example.com")
        with pytest.raises(PermissionDenied, match="Verify this address"):
            services.accept_invitation(carol, raw)
        assert not EmailAddress.objects.filter(user=carol, verified=True).exists()

    def test_weak_password_is_refused(self, client: Client, alice: User, alice_home: Home) -> None:
        raw = invite_by_email(alice, alice_home)
        response = client.post(link(raw), {"password1": "short", "password2": "short"})
        assert response.status_code == 200
        assert not User.objects.filter(email="new@example.com").exists()

    def test_existing_account_signs_in_then_joins(
        self, client: Client, alice: User, bob: User, alice_home: Home
    ) -> None:
        raw = invite_by_email(alice, alice_home, "bob@example.com")
        attempt = client.post(link(raw), {"password1": PASSWORD, "password2": PASSWORD})
        assert "sign in to accept" in attempt.text
        assert User.objects.filter(email="bob@example.com").count() == 1
        client.force_login(bob)
        assert "Join the home" in client.get(link(raw)).text
        client.post(link(raw))
        assert services.role_of(bob, alice_home) == Role.MEMBER

    def test_signed_in_with_another_address(
        self, client: Client, alice: User, bob: User, alice_home: Home
    ) -> None:
        raw = invite(alice, alice_home)
        client.force_login(bob)
        response = client.post(link(raw))
        assert "another address" in response.text
        assert services.role_of(bob, alice_home) is None

    @pytest.mark.parametrize("case", ["expired", "revoked", "unknown", "accepted"])
    def test_dead_links_all_look_the_same(
        self,
        client: Client,
        alice: User,
        alice_home: Home,
        make_user: Callable[..., User],
        case: str,
    ) -> None:
        raw = invite(alice, alice_home)
        invitation = Invitation.objects.get()
        if case == "expired":
            invitation.expires_at = timezone.now() - timedelta(minutes=1)
            invitation.save()
        elif case == "revoked":
            services.revoke_invitation(alice, invitation)
        elif case == "unknown":
            raw = "hci_" + "x" * 43
        else:
            services.accept_invitation(verified(make_user("new@example.com")), raw)
        response = client.get(link(raw))
        assert response.status_code == 404
        assert "does not work" in response.text
        assert "Main house" not in response.text
        post = client.post(link(raw), {"password1": PASSWORD, "password2": PASSWORD})
        assert post.status_code == 404

    @override_settings(ACCOUNT_RATE_LIMITS={"housecairn_invitation": "3/m/ip"})
    def test_opening_links_is_rate_limited(self, client: Client) -> None:
        codes = [client.get(link(f"hci_guess{i}")).status_code for i in range(5)]
        assert codes == [404, 404, 404, 429, 429]

    @override_settings(ACCOUNT_RATE_LIMITS={"housecairn_invitation": "3/m/ip"})
    def test_a_forged_forwarding_header_does_not_dodge_the_limit(self) -> None:
        # The peer is not a trusted proxy: the header is dropped, not believed.
        client = Client(REMOTE_ADDR="203.0.113.5")
        codes = [
            client.get(link(f"hci_guess{i}"), headers={"X-Forwarded-For": f"198.51.100.{i}"})
            for i in range(5)
        ]
        assert [r.status_code for r in codes] == [404, 404, 404, 429, 429]

    @override_settings(ACCOUNT_RATE_LIMITS={"housecairn_invitation": "3/m/ip"})
    def test_entries_the_client_wrote_do_not_dodge_the_limit_behind_a_proxy(self) -> None:
        # From the trusted proxy (the loopback by default), only the entry the
        # proxy appended counts: the client wrote everything to its left.
        client = Client(REMOTE_ADDR="127.0.0.1")
        codes = [
            client.get(
                link(f"hci_guess{i}"),
                headers={"X-Forwarded-For": f"198.51.100.{i}, 203.0.113.9"},
            ).status_code
            for i in range(5)
        ]
        assert codes == [404, 404, 404, 429, 429]


class TestWebOwner:
    def test_without_email_the_link_is_shown_once(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        client.force_login(alice)
        household = reverse("homes:household", kwargs={"home_id": alice_home.id})
        response = client.post(
            reverse("homes:invite", kwargs={"home_id": alice_home.id}),
            {"email": "new@example.com", "role": "member"},
            follow=True,
        )
        raw = link_in(response.text)
        assert services.find_invitation(raw) is not None
        assert "http://testserver/invitations/" in response.text
        assert "hci_" not in client.get(household).text
        assert len(mail.outbox) == 0

    @override_settings(**EMAIL)
    def test_with_email_the_link_goes_to_the_address_only(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        client.force_login(alice)
        response = client.post(
            reverse("homes:invite", kwargs={"home_id": alice_home.id}),
            {"email": "new@example.com", "role": "member"},
            follow=True,
        )
        assert "hci_" not in response.text
        assert "Invitation sent" in response.text
        message = mail.outbox[0]
        assert message.to == ["new@example.com"]
        assert message.subject == "HouseCairn: you are invited to Main house"
        html, mimetype = message.alternatives[0]
        assert mimetype == "text/html"
        raw = link_in(message.body)
        assert raw == link_in(str(html))
        client.logout()
        client.post(link(raw), {"password1": PASSWORD, "password2": PASSWORD})
        user = User.objects.get(email="new@example.com")
        assert EmailAddress.objects.get(user=user).verified

    @override_settings(**EMAIL, HOUSECAIRN_SITE_URL="https://home.example.com")
    def test_links_use_the_configured_address_not_the_host_header(
        self, alice: User, alice_home: Home
    ) -> None:
        client = Client(HTTP_HOST="testserver")
        client.force_login(alice)
        client.post(
            reverse("homes:invite", kwargs={"home_id": alice_home.id}),
            {"email": "new@example.com", "role": "member"},
        )
        assert "https://home.example.com/invitations/hci_" in mail.outbox[0].body

    @override_settings(**EMAIL)
    def test_email_in_french_and_one_line_subject(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        services.update_home(alice, alice_home, name="Line\nBreak")
        client.force_login(alice)
        client.post(
            reverse("homes:invite", kwargs={"home_id": alice_home.id}),
            {"email": "new@example.com", "role": "read_only"},
            headers={"Accept-Language": "fr"},
        )
        message = mail.outbox[0]
        assert "\n" not in message.subject
        assert message.subject == "HouseCairn : vous êtes invité à Line Break"
        assert "lecture seule" in message.body.lower()

    @override_settings(**EMAIL)
    def test_no_enumeration(
        self, client: Client, alice: User, alice_home: Home, make_user: Callable[..., User]
    ) -> None:
        make_user("known@example.com")
        client.force_login(alice)
        url = reverse("homes:invite", kwargs={"home_id": alice_home.id})
        # The message states when the link expires, to the minute. Freeze the clock so a
        # minute change between the two sends cannot make the bodies differ.
        with mock.patch("django.utils.timezone.now", return_value=SENT_AT):
            known = client.post(url, {"email": "known@example.com", "role": "member"}, follow=True)
            unknown = client.post(
                url, {"email": "nobody@example.com", "role": "member"}, follow=True
            )

        def visible(text: str) -> str:
            return text.replace("known@example.com", "X").replace("nobody@example.com", "X")

        def message(text: str) -> str:
            return re.findall(r'<li class="message[^"]*">([^<]*)</li>', text)[0]

        assert known.status_code == unknown.status_code == 200
        assert message(known.text) == message(unknown.text) == "Invitation sent."
        assert visible(known.text).count("Send again") == 1
        assert [m.subject for m in mail.outbox] == [mail.outbox[0].subject] * 2
        assert mail.outbox[0].body.replace(link_in(mail.outbox[0].body), "") == mail.outbox[
            1
        ].body.replace(link_in(mail.outbox[1].body), "")

    def test_revoke_and_resend(self, client: Client, alice: User, alice_home: Home) -> None:
        raw = invite(alice, alice_home)
        invitation = Invitation.objects.get()
        client.force_login(alice)
        kwargs = {"home_id": alice_home.id, "invitation_id": invitation.id}
        response = client.post(reverse("homes:invitation-resend", kwargs=kwargs), follow=True)
        fresh = link_in(response.text)
        assert services.find_invitation(raw) is None
        assert services.find_invitation(fresh) is not None
        client.post(reverse("homes:invitation-revoke", kwargs=kwargs))
        assert services.find_invitation(fresh) is None

    def test_another_homes_invitation_is_404(
        self, client: Client, alice: User, bob: User, alice_home: Home, bob_home: Home
    ) -> None:
        raw = invite(bob, bob_home)
        theirs = Invitation.objects.get(home=bob_home)
        client.force_login(alice)
        for home in (alice_home, bob_home):
            kwargs = {"home_id": home.id, "invitation_id": theirs.id}
            assert client.post(reverse("homes:invitation-revoke", kwargs=kwargs)).status_code == 404
            assert client.post(reverse("homes:invitation-resend", kwargs=kwargs)).status_code == 404
        assert services.find_invitation(raw) is not None

    def test_member_cannot_invite(
        self, client: Client, bob: User, alice_home: Home, add_member: Callable[..., Membership]
    ) -> None:
        add_member(alice_home, bob, Role.MEMBER)
        client.force_login(bob)
        response = client.post(
            reverse("homes:invite", kwargs={"home_id": alice_home.id}),
            {"email": "new@example.com", "role": "owner"},
        )
        assert response.status_code == 403
        assert not Invitation.objects.exists()

    @override_settings(ACCOUNT_RATE_LIMITS={"housecairn_invite": "2/m/user"})
    def test_inviting_is_rate_limited(self, client: Client, alice: User, alice_home: Home) -> None:
        client.force_login(alice)
        url = reverse("homes:invite", kwargs={"home_id": alice_home.id})
        for i in range(3):
            response = client.post(
                url, {"email": f"p{i}@example.com", "role": "member"}, follow=True
            )
        assert "Too many invitations" in response.text
        assert Invitation.objects.count() == 2


class TestApi:
    def test_invite_accept_and_dead_link(
        self, api_client: APIClient, alice: User, alice_home: Home, make_user: Callable[..., User]
    ) -> None:
        api_client.force_authenticate(alice)
        url = reverse("api-v1:home-invitation-list", kwargs={"home_id": alice_home.id})
        created = api_client.post(url, {"email": "new@example.com", "role": "read_only"})
        assert created.status_code == 201
        body = created.json()
        raw = link_in(body["link"])
        listed = api_client.get(url).json()["results"]
        assert [i["email"] for i in listed] == ["new@example.com"]
        assert listed[0]["link"] is None
        api_client.force_authenticate(verified(make_user("new@example.com")))
        accept = reverse("api-v1:invitation-accept")
        response = api_client.post(accept, {"token": raw})
        assert response.status_code == 200
        assert response.json()["role"] == "read_only"
        assert api_client.post(accept, {"token": raw}).status_code == 404
        assert api_client.post(accept, {"token": "hci_nope"}).status_code == 404

    @override_settings(**EMAIL)
    def test_with_email_no_link_in_the_answer(
        self, api_client: APIClient, alice: User, alice_home: Home
    ) -> None:
        api_client.force_authenticate(alice)
        url = reverse("api-v1:home-invitation-list", kwargs={"home_id": alice_home.id})
        response = api_client.post(url, {"email": "new@example.com"})
        assert response.json()["link"] is None
        assert len(mail.outbox) == 1

    def test_accept_with_another_address_is_refused(
        self, api_client: APIClient, alice: User, bob: User, alice_home: Home
    ) -> None:
        raw = invite(alice, alice_home)
        api_client.force_authenticate(bob)
        response = api_client.post(reverse("api-v1:invitation-accept"), {"token": raw})
        assert response.status_code == 403
        assert services.role_of(bob, alice_home) is None

    def test_revoke_and_resend(self, api_client: APIClient, alice: User, alice_home: Home) -> None:
        raw = invite(alice, alice_home)
        invitation = Invitation.objects.get()
        api_client.force_authenticate(alice)
        kwargs = {"home_id": alice_home.id, "invitation_id": invitation.id}
        resent = api_client.post(reverse("api-v1:home-invitation-resend", kwargs=kwargs))
        assert resent.status_code == 200
        assert services.find_invitation(raw) is None
        fresh = link_in(resent.json()["link"])
        assert (
            api_client.delete(reverse("api-v1:home-invitation-detail", kwargs=kwargs)).status_code
            == 204
        )
        assert services.find_invitation(fresh) is None

    def test_non_owners_and_other_homes(
        self,
        api_client: APIClient,
        alice: User,
        bob: User,
        alice_home: Home,
        bob_home: Home,
        add_member: Callable[..., Membership],
    ) -> None:
        add_member(alice_home, bob, Role.MEMBER)
        api_client.force_authenticate(bob)
        url = reverse("api-v1:home-invitation-list", kwargs={"home_id": alice_home.id})
        assert api_client.get(url).status_code == 403
        assert api_client.post(url, {"email": "x@example.com"}).status_code == 403
        api_client.force_authenticate(alice)
        other = reverse("api-v1:home-invitation-list", kwargs={"home_id": bob_home.id})
        assert api_client.get(other).status_code == 404
        assert api_client.post(other, {"email": "x@example.com"}).status_code == 404
        assert not Invitation.objects.exists()

    def test_tokens_cannot_invite_or_accept(
        self, api_client: APIClient, alice: User, alice_home: Home
    ) -> None:
        _token, secret = token_services.issue_token(
            alice, name="all", scopes=["read", "write"], homes=[alice_home]
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
        url = reverse("api-v1:home-invitation-list", kwargs={"home_id": alice_home.id})
        assert api_client.post(url, {"email": "x@example.com"}).status_code == 403
        assert (
            api_client.post(reverse("api-v1:invitation-accept"), {"token": "hci_x"}).status_code
            == 403
        )
        assert not Invitation.objects.exists()


class TestProperties:
    """Property-based tests of invitation links (docs/adr/0019)."""

    @shared_fixtures
    @given(raw=st.text(max_size=300))
    def test_any_link_opens_nothing_and_never_fails(
        self, alice: User, alice_home: Home, raw: str
    ) -> None:
        """No string but the issued secret finds an invitation; none raises."""
        assert services.find_invitation(raw) is None
        assert services.find_invitation(INVITATION_PREFIX + raw) is None

    @shared_fixtures
    @given(suffix=st.text(alphabet=st.characters(codec="ascii"), min_size=1, max_size=5))
    def test_the_issued_secret_and_only_it_matches(
        self, alice: User, alice_home: Home, suffix: str
    ) -> None:
        """Appending anything to a valid secret gives a dead link."""
        Invitation.objects.filter(home=alice_home).delete()
        raw = invite(alice, alice_home)
        assert services.find_invitation(raw) is not None
        assert services.find_invitation(raw + suffix) is None
        assert services.find_invitation(raw[:-1]) is None


@settings(max_examples=25)
@given(token=st.text(max_size=80))
def test_the_invitation_page_answers_404_to_any_link(token: str) -> None:
    """Whatever the link, a dead one is a 404 page, never a server error."""
    if "/" in token or not token or token in {".", ".."}:
        return
    cache.clear()
    response = Client().get(reverse("invitation", kwargs={"token": token}))
    assert response.status_code == 404


class TestMailServerDown:
    """A failing mail server leaves the invitation saved and is neither a 500 nor silent."""

    @pytest.fixture
    def failing_send(self) -> Iterator[None]:
        from unittest import mock

        with (
            override_settings(**EMAIL),
            mock.patch(
                "housecairn.homes.emails.EmailMultiAlternatives.send",
                side_effect=ConnectionRefusedError("smtp down"),
            ),
        ):
            yield

    @pytest.mark.usefixtures("failing_send")
    def test_web_invite_and_resend(self, client: Client, alice: User, alice_home: Home) -> None:
        client.force_login(alice)
        url = reverse("homes:invite", kwargs={"home_id": alice_home.id})
        response = client.post(url, {"email": "new@example.com", "role": "member"}, follow=True)
        assert response.status_code == 200
        assert "could not be sent" in response.text
        assert "smtp down" not in response.text
        invitation = Invitation.objects.get()
        assert invitation.is_pending
        again = client.post(
            reverse(
                "homes:invitation-resend",
                kwargs={"home_id": alice_home.id, "invitation_id": invitation.id},
            ),
            follow=True,
        )
        assert "could not be sent" in again.text

    @pytest.mark.usefixtures("failing_send")
    def test_api_answers_503_with_the_invitation_id(
        self, api_client: APIClient, alice: User, alice_home: Home
    ) -> None:
        api_client.force_authenticate(alice)
        url = reverse("api-v1:home-invitation-list", kwargs={"home_id": alice_home.id})
        response = api_client.post(url, {"email": "new@example.com"})
        assert response.status_code == 503
        body = response.json()
        assert body["code"] == "delivery_failed"
        assert body["id"] == str(Invitation.objects.get().id)


class TestOnePendingInvitation:
    """One open invitation per home and address, enforced by the database too."""

    def test_the_database_refuses_a_second_open_invitation(
        self, alice: User, alice_home: Home
    ) -> None:
        from django.db import IntegrityError, transaction

        invite(alice, alice_home, "new@example.com")
        first = Invitation.objects.get()
        duplicate = Invitation(
            home=alice_home,
            email="NEW@example.com",
            role="member",
            digest="d" * 64,
            expires_at=first.expires_at,
            sent_at=first.sent_at,
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_a_lapsed_invitation_does_not_block_a_new_one(
        self, alice: User, alice_home: Home
    ) -> None:
        old_link = invite(alice, alice_home, "new@example.com")
        Invitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        fresh_link = invite(alice, alice_home, "New@Example.com")
        assert services.find_invitation(old_link) is None
        assert services.find_invitation(fresh_link) is not None
        assert Invitation.objects.filter(revoked_at__isnull=True).count() == 1

    def test_a_revoked_or_accepted_one_does_not_block_a_new_one(
        self, alice: User, alice_home: Home
    ) -> None:
        invite(alice, alice_home, "new@example.com")
        services.revoke_invitation(alice, Invitation.objects.get())
        assert services.find_invitation(invite(alice, alice_home, "new@example.com")) is not None


class TestVerifiedAddress:
    """A link handed over by hand needs an address the account verified (docs/adr/0020)."""

    @override_settings(**EMAIL)
    def test_email_change_then_accept(
        self, client: Client, alice: User, bob: User, alice_home: Home
    ) -> None:
        # The audit's scenario: the link is handed over, someone else declares the address.
        raw = invite(alice, alice_home, "ivy@example.com")
        client.force_login(bob)
        client.post(reverse("account_email"), {"action_add": "", "email": "ivy@example.com"})
        assert EmailAddress.objects.filter(
            user=bob, email="ivy@example.com", verified=False
        ).exists()
        refused = client.post(
            reverse("account_email"),
            {"action_primary": "", "email": "ivy@example.com"},
            follow=True,
        )
        assert "must be verified" in refused.text
        bob.refresh_from_db()
        assert bob.email == "bob@example.com"
        assert not EmailAddress.objects.get(user=bob, email="ivy@example.com").primary
        client.post(link(raw))
        assert services.role_of(bob, alice_home) is None
        assert services.find_invitation(raw) is not None

    @override_settings(**EMAIL)
    def test_the_page_says_to_verify_the_address_then_the_link_works(
        self, client: Client, alice: User, alice_home: Home, make_user: Callable[..., User]
    ) -> None:
        raw = invite(alice, alice_home, "Ivy@Example.com")
        ivy = make_user("ivy@example.com")
        client.force_login(ivy)
        page = client.get(link(raw))
        assert "Join the home" not in page.text
        assert "Verify this address in your account first" in page.text
        assert f'href="{reverse("account_email")}"' in page.text
        post = client.post(link(raw))
        assert "Verify this address in your account first" in post.text
        assert services.role_of(ivy, alice_home) is None
        assert not EmailAddress.objects.filter(user=ivy, verified=True).exists()
        # Verified (case ignored), the same link works.
        verified(ivy)
        assert "Join the home" in client.get(link(raw)).text
        client.post(link(raw))
        assert services.role_of(ivy, alice_home) == Role.MEMBER

    def test_without_email_the_page_says_the_address_cannot_be_verified_here(
        self, client: Client, alice: User, alice_home: Home, make_user: Callable[..., User]
    ) -> None:
        raw = invite(alice, alice_home, "ivy@example.com")
        ivy = make_user("ivy@example.com")
        client.force_login(ivy)
        page = client.post(link(raw))
        assert "Join the home" not in page.text
        assert "cannot send email to verify it" in page.text
        assert reverse("account_email") not in page.text
        assert services.role_of(ivy, alice_home) is None

    def test_an_address_verified_by_another_account_does_not_count(
        self, alice: User, alice_home: Home, make_user: Callable[..., User]
    ) -> None:
        raw = invite(alice, alice_home, "ivy@example.com")
        ivy = make_user("ivy@example.com")
        other = make_user("other@example.com")
        EmailAddress.objects.create(user=other, email="ivy@example.com", verified=True)
        with pytest.raises(PermissionDenied, match="Verify this address"):
            services.accept_invitation(ivy, raw)
        assert services.role_of(ivy, alice_home) is None
        assert services.role_of(other, alice_home) is None

    def test_the_api_says_to_verify_the_address(
        self, api_client: APIClient, alice: User, alice_home: Home, make_user: Callable[..., User]
    ) -> None:
        raw = invite(alice, alice_home, "ivy@example.com")
        ivy = make_user("ivy@example.com")
        api_client.force_authenticate(ivy)
        response = api_client.post(reverse("api-v1:invitation-accept"), {"token": raw})
        assert response.status_code == 403
        assert "Verify this address in your account first" in response.json()["detail"]
        assert services.role_of(ivy, alice_home) is None
        assert services.find_invitation(raw) is not None
