"""The instance administrator runs the instance and reads no home (docs/adr/0022).

Proved on the three surfaces: the web screens, the API (session and token), and
Django's administration site, which no longer lists homes, their members,
invitations or events.
"""

from collections.abc import Callable

import pytest
from django.contrib import admin
from django.contrib.auth.hashers import check_password
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from housecairn.accounts.models import InstanceSetup, User
from housecairn.apitokens import services as token_services
from housecairn.apitokens.models import ApiToken
from housecairn.audit.models import AuditEvent
from housecairn.events.models import Event
from housecairn.homes import services
from housecairn.homes.models import Home, HomeOwnedModel, Invitation, Membership

pytestmark = pytest.mark.django_db

SECRET_NAME = "Villa Secrète des Lilas"


@pytest.fixture
def admin_user(make_user: Callable[..., User]) -> User:
    return make_user("admin@example.com", is_staff=True, is_superuser=True)


@pytest.fixture
def secret_home(bob: User) -> Home:
    """A home the administrator does not belong to, with members, an invitation and an event."""
    home = services.create_home(bob, name=SECRET_NAME)
    Invitation.objects.create(
        home=home,
        email="pending-guest@example.com",
        role="member",
        digest="a" * 64,
        expires_at="2999-01-01T00:00:00Z",
        sent_at="2026-01-01T00:00:00Z",
    )
    Event.objects.create(home=home, kind="battery_low", payload={"room": "kitchen"})
    return home


class TestAdminSite:
    def test_no_model_of_a_home_is_registered(self) -> None:
        registered = set(admin.site._registry)
        assert not {Home, Membership, Invitation, Event} & registered
        assert not [m for m in registered if issubclass(m, HomeOwnedModel)]

    def test_the_index_and_every_page_say_nothing_of_a_home(
        self, client: Client, admin_user: User, secret_home: Home
    ) -> None:
        client.force_login(admin_user)
        pages = [reverse("admin:index"), reverse("admin:accounts_user_changelist")]
        for model in admin.site._registry:
            pages.append(
                reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
            )
        for url in pages:
            body = client.get(url).text
            for secret in (SECRET_NAME, "pending-guest@example.com", "battery_low"):
                assert secret not in body, url

    @pytest.mark.parametrize("model", ["home", "membership", "invitation"])
    def test_the_old_pages_are_gone(self, client: Client, admin_user: User, model: str) -> None:
        client.force_login(admin_user)
        for kind in ("changelist", "add"):
            with pytest.raises(Exception, match="not a valid view function"):
                reverse(f"admin:homes_{model}_{kind}")

    def test_a_token_page_does_not_name_its_homes(
        self, client: Client, admin_user: User, bob: User, secret_home: Home
    ) -> None:
        token, _raw = token_services.issue_token(
            bob, name="ha", scopes=["events"], homes=[secret_home]
        )
        client.force_login(admin_user)
        page = client.get(reverse("admin:apitokens_apitoken_change", args=[token.id]))
        assert page.status_code == 200
        assert SECRET_NAME not in page.text
        listing = client.get(reverse("admin:apitokens_apitoken_changelist"))
        assert SECRET_NAME not in listing.text
        # Deletion would list what hangs off the token: revoking is the way.
        assert (
            client.get(reverse("admin:apitokens_apitoken_delete", args=[token.id])).status_code
            == 403
        )

    def test_the_deletion_page_of_an_account_lists_no_membership(
        self, client: Client, admin_user: User, bob: User, secret_home: Home
    ) -> None:
        client.force_login(admin_user)
        page = client.get(reverse("admin:accounts_user_delete", args=[bob.pk]))
        assert page.status_code == 200
        assert SECRET_NAME not in page.text
        assert "bob@example.com" in page.text


class TestAccountsAreNotTakenOver:
    """The administrator cannot take an account: no password, no address, no
    credential, no identity, no session to set or read (docs/adr/0022)."""

    def test_the_password_page_is_gone(self, client: Client, admin_user: User, bob: User) -> None:
        # Django's own address (`<id>/password/`) used to show a form that sets any
        # account's password. Now nothing answers it with a form; the unknown path
        # leads back to the index, and the password stays what it was.
        client.force_login(admin_user)
        before = User.objects.get(pk=bob.pk).password
        for url in (
            f"/admin/accounts/user/{bob.pk}/password/",
            f"/admin/accounts/user/{bob.pk}/password",
        ):
            page = client.get(url, follow=True)
            assert 'name="password1"' not in page.text, url
            posted = client.post(
                url,
                {"password1": "Hacked-Pass-123", "password2": "Hacked-Pass-123"},
                follow=True,
            )
            assert 'name="password1"' not in posted.text, url
        assert User.objects.get(pk=bob.pk).password == before
        assert not check_password("Hacked-Pass-123", User.objects.get(pk=bob.pk).password)
        with pytest.raises(Exception, match="not a valid view function"):
            reverse("admin:accounts_user_password_change", args=[bob.pk])

    def test_the_form_of_an_account_offers_no_password_and_no_link_to_one(
        self, client: Client, admin_user: User, bob: User
    ) -> None:
        client.force_login(admin_user)
        body = client.get(reverse("admin:accounts_user_change", args=[bob.pk])).text
        assert 'name="password' not in body
        assert f"{bob.pk}/password" not in body

    def test_a_post_changes_neither_the_address_nor_the_password(
        self, client: Client, admin_user: User, bob: User
    ) -> None:
        client.force_login(admin_user)
        before = User.objects.get(pk=bob.pk)
        response = client.post(
            reverse("admin:accounts_user_change", args=[bob.pk]),
            {
                "email": "taken@example.com",
                "password": "pbkdf2_sha256$1$x$y",
                "first_name": "Renamed",
                "display_name": "Renamed",
                "is_active": "on",
                "date_joined_0": "2026-01-01",
                "date_joined_1": "00:00:00",
            },
        )
        assert response.status_code in (200, 302)
        after = User.objects.get(pk=bob.pk)
        assert after.email == before.email
        assert after.password == before.password
        assert after.first_name == before.first_name
        assert after.display_name == before.display_name
        assert not check_password("Hacked-Pass-123", after.password)

    def test_no_account_is_added_by_the_site(self, client: Client, admin_user: User) -> None:
        client.force_login(admin_user)
        url = reverse("admin:accounts_user_add")
        assert client.get(url).status_code == 403
        response = client.post(
            url,
            {
                "email": "new@example.com",
                "password1": "Hacked-Pass-123",
                "password2": "Hacked-Pass-123",
            },
        )
        assert response.status_code == 403
        assert not User.objects.filter(email="new@example.com").exists()

    def test_the_administrator_still_switches_an_account_and_it_is_audited(
        self, client: Client, admin_user: User, bob: User
    ) -> None:
        client.force_login(admin_user)
        url = reverse("admin:accounts_user_change", args=[bob.pk])
        assert client.post(url, {"is_active": ""}).status_code == 302
        bob.refresh_from_db()
        assert bob.is_active is False
        event = AuditEvent.objects.get(action="admin.user_changed")
        assert event.actor == admin_user
        assert event.target_user == bob
        assert event.detail == {"fields": ["is_active"]}

    def test_the_administrator_role_is_granted_and_withdrawn_with_an_audit_line(
        self, client: Client, admin_user: User, bob: User
    ) -> None:
        client.force_login(admin_user)
        url = reverse("admin:accounts_user_change", args=[bob.pk])
        client.post(url, {"is_active": "on", "is_staff": "on", "is_superuser": "on"})
        bob.refresh_from_db()
        assert bob.is_staff
        assert bob.is_superuser
        client.post(url, {"is_active": "on"})
        bob.refresh_from_db()
        assert not bob.is_staff
        assert not bob.is_superuser
        details = [e.detail for e in AuditEvent.objects.filter(action="admin.user_changed")]
        assert details == [
            {"fields": ["is_staff", "is_superuser"]},
            {"fields": ["is_staff", "is_superuser"]},
        ]

    def test_an_unchanged_form_writes_no_audit_line(
        self, client: Client, admin_user: User, bob: User
    ) -> None:
        client.force_login(admin_user)
        client.post(reverse("admin:accounts_user_change", args=[bob.pk]), {"is_active": "on"})
        assert not AuditEvent.objects.filter(action="admin.user_changed").exists()

    def test_the_only_models_on_the_site_are_the_three_that_hold_no_credential(self) -> None:
        # A model added later (by a new app or an update of a dependency) fails here
        # until someone decides it takes no account and holds no secret.
        assert {m._meta.label for m in admin.site._registry} == {
            "accounts.User",
            "accounts.InstanceSetup",
            "apitokens.ApiToken",
            # The public client of the mobile app, read only: no secret (docs/adr/0078).
            "oauth2_provider.Application",
        }

    @pytest.mark.parametrize(
        "label",
        [
            "account.EmailAddress",
            "mfa.Authenticator",
            "socialaccount.SocialAccount",
            "socialaccount.SocialApp",
            "socialaccount.SocialToken",
            "django_q.Schedule",
            "django_q.Success",
            "django_q.Failure",
            "django_q.OrmQ",
            "auth.Group",
            "sessions.Session",
        ],
    )
    def test_nothing_that_gives_an_account_or_a_secret_is_on_the_site(
        self, client: Client, admin_user: User, label: str
    ) -> None:
        from django.apps import apps

        model = apps.get_model(label)
        assert not admin.site.is_registered(model)
        client.force_login(admin_user)
        base = f"admin:{model._meta.app_label}_{model._meta.model_name}"
        for kind in ("changelist", "add"):
            with pytest.raises(Exception, match="not a valid view function"):
                reverse(f"{base}_{kind}")
        assert (
            client.get(f"/admin/{model._meta.app_label}/{model._meta.model_name}/").status_code
            == 404
        )

    def test_a_token_and_the_setup_record_stay_unwritable(
        self, client: Client, admin_user: User, bob: User, secret_home: Home
    ) -> None:
        token, raw = token_services.issue_token(
            bob, name="ha", scopes=["events"], homes=[secret_home]
        )
        client.force_login(admin_user)
        page = client.get(reverse("admin:apitokens_apitoken_change", args=[token.id]))
        assert raw not in page.text
        assert token.digest not in page.text
        client.post(reverse("admin:apitokens_apitoken_change", args=[token.id]), {"name": "x"})
        assert ApiToken.objects.get(pk=token.pk).name == "ha"
        assert client.get(reverse("admin:apitokens_apitoken_add")).status_code == 403
        InstanceSetup.objects.all().delete()
        assert client.get(reverse("admin:accounts_instancesetup_add")).status_code == 403

    def test_an_administrator_still_opens_no_home_through_the_site(
        self, client: Client, admin_user: User, secret_home: Home
    ) -> None:
        client.force_login(admin_user)
        for model in ("home", "membership", "invitation", "event"):
            assert client.get(f"/admin/homes/{model}/").status_code == 404
            assert client.get(f"/admin/homes/{model}/{secret_home.pk}/change/").status_code == 404
        assert client.get(f"/admin/events/event/{secret_home.pk}/change/").status_code == 404


class TestWebAndApi:
    def web_urls(self, home: Home) -> list[str]:
        owner = Membership.objects.get(home=home)
        kwargs = {"home_id": home.id}
        return [
            reverse("homes:detail", kwargs=kwargs),
            reverse("homes:household", kwargs=kwargs),
            reverse("homes:audit", kwargs=kwargs),
            reverse("homes:delete", kwargs=kwargs),
            reverse("homes:member", kwargs={**kwargs, "member_id": owner.id}),
        ]

    def api_urls(self, home: Home) -> list[str]:
        kwargs = {"home_id": home.id}
        return [
            reverse("api-v1:home-detail", kwargs=kwargs),
            reverse("api-v1:home-member-list", kwargs=kwargs),
            reverse("api-v1:home-invitation-list", kwargs=kwargs),
            reverse("api-v1:home-audit", kwargs=kwargs),
        ]

    def test_the_administrator_gets_404_on_every_page_of_a_home(
        self, client: Client, admin_user: User, secret_home: Home
    ) -> None:
        client.force_login(admin_user)
        for url in self.web_urls(secret_home) + self.api_urls(secret_home):
            response = client.get(url)
            assert response.status_code == 404, url
            assert SECRET_NAME not in response.text
        for name in ("homes:export", "homes:unlock"):
            url = reverse(name, kwargs={"home_id": secret_home.id})
            assert client.post(url).status_code == 404, url
        for name in ("home-export", "home-unlock"):
            url = reverse(f"api-v1:{name}", kwargs={"home_id": secret_home.id})
            assert client.post(url).status_code == 404, url
        assert SECRET_NAME not in client.get(reverse("homes:list")).text
        listed = client.get(reverse("api-v1:home-list")).json()["results"]
        assert listed == []

    def test_the_administrator_cannot_delete_or_export_a_home(
        self, client: Client, admin_user: User, secret_home: Home
    ) -> None:
        client.force_login(admin_user)
        url = reverse("homes:delete", kwargs={"home_id": secret_home.id})
        assert client.post(url, {"confirmation": SECRET_NAME}).status_code == 404
        api = reverse("api-v1:home-delete-home", kwargs={"home_id": secret_home.id})
        assert (
            client.post(
                api, {"confirmation": SECRET_NAME}, content_type="application/json"
            ).status_code
            == 404
        )
        assert Home.objects.filter(pk=secret_home.pk).exists()

    def test_an_administrator_token_sees_no_home_either(
        self, api_client: APIClient, admin_user: User, secret_home: Home
    ) -> None:
        own = services.create_home(admin_user, name="Admin's own flat")
        _token, raw = token_services.issue_token(
            admin_user, name="all", scopes=["read", "write"], homes=[own]
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        for url in self.api_urls(secret_home):
            assert api_client.get(url).status_code == 404, url
        assert [h["id"] for h in api_client.get(reverse("api-v1:home-list")).json()["results"]] == [
            str(own.id)
        ]

    def test_a_member_of_another_home_and_a_token_see_nothing_either(
        self,
        client: Client,
        api_client: APIClient,
        alice: User,
        alice_home: Home,
        secret_home: Home,
    ) -> None:
        client.force_login(alice)
        for url in self.web_urls(secret_home) + self.api_urls(secret_home):
            assert client.get(url).status_code == 404, url
        _token, raw = token_services.issue_token(
            alice, name="all", scopes=["read", "write"], homes=[alice_home]
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        for url in self.api_urls(secret_home):
            assert api_client.get(url).status_code == 404, url

    def test_being_administrator_adds_no_membership(
        self, admin_user: User, secret_home: Home
    ) -> None:
        assert services.homes_for(admin_user).count() == 0
        assert services.role_of(admin_user, secret_home) is None


class TestInstanceScreensAreForAdministrators:
    URLS = ["instance:overview", "instance:audit"]

    @pytest.mark.parametrize("name", URLS)
    def test_a_member_gets_404_and_an_anonymous_visitor_the_sign_in(
        self, client: Client, alice: User, name: str
    ) -> None:
        assert client.get(reverse(name)).status_code == 302
        client.force_login(alice)
        assert client.get(reverse(name)).status_code == 404

    def test_a_staff_member_who_is_not_superuser_is_not_an_administrator(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        client.force_login(make_user("staff@example.com", is_staff=True))
        assert client.get(reverse("instance:overview")).status_code == 404

    def test_the_api_refuses_everyone_else_and_tokens(
        self,
        client: Client,
        api_client: APIClient,
        alice: User,
        alice_home: Home,
        admin_user: User,
    ) -> None:
        urls = [
            reverse("api-v1:instance-invitation-list"),
            reverse("api-v1:instance-audit-list"),
        ]
        client.force_login(alice)
        for url in urls:
            assert client.get(url).status_code == 403
        assert client.post(reverse("api-v1:instance-unlock")).status_code == 403
        own = services.create_home(admin_user, name="Own")
        _token, raw = token_services.issue_token(
            admin_user, name="all", scopes=["read", "write"], homes=[own]
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        for url in urls:
            assert api_client.get(url).status_code == 403
        assert api_client.post(urls[0], {"email": "x@example.com"}).status_code == 403
        assert api_client.post(reverse("api-v1:instance-unlock")).status_code == 403
