"""The interface: brand, shell, screens, sign-in, token contrast (docs/adr/0017, 0018)."""

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, override_settings
from django.urls import resolve, reverse
from django.utils import translation

from housecairn.core import design
from housecairn.core.context_processors import nav_section
from housecairn.homes.models import Home
from tests.htmlscan import inline_code_problems, script_tags

SRC = Path(settings.BASE_DIR)
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
CONSOLE_BACKEND = "django.core.mail.backends.console.EmailBackend"
LOGO = "img/brand/housecairn-logo.svg"
# Layers of the interface's styles, imported by static/css/app.css in this order.
STYLE_LAYERS = (
    "base.css",
    "shell.css",
    "components.css",
    "rooms.css",
    "tasks.css",
    "stock.css",
    "calendar.css",
    "labels.css",
    "reports.css",
    "guide.css",
    "forms.css",
    "chip-select.css",
    "utilities.css",
)


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    """A request in another language leaves it active on the thread: start each test in English."""
    with translation.override("en"):
        yield


def _page(client: Client, name: str, **kwargs: object) -> HttpResponse:
    return client.get(reverse(name, kwargs=kwargs or None))


class TestBrand:
    def test_no_template_spells_the_name_in_lower_case_camel(self) -> None:
        """The name is HouseCairn; identifiers (housecairn) stay lower case."""
        offenders = [
            str(path.relative_to(SRC))
            for path in SRC.glob("templates/**/*.html")
            if "Housecairn" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_settings_use_the_brand_name(self) -> None:
        assert settings.MFA_TOTP_ISSUER == "HouseCairn"
        assert settings.SPECTACULAR_SETTINGS["TITLE"] == "HouseCairn API"

    def test_brand_files_are_in_the_static_directory(self) -> None:
        for name in (
            "img/brand/housecairn-logo.svg",
            "img/brand/housecairn-icon.svg",
            "img/brand/favicon.svg",
            "img/brand/favicon-16.png",
            "img/brand/favicon-32.png",
            "img/brand/apple-touch-icon.png",
            "img/brand/icon-192.png",
            "img/brand/icon-512.png",
        ):
            assert (SRC / "static" / name).is_file(), name

    def test_fonts_are_self_hosted_with_their_licence(self) -> None:
        fonts = SRC / "static" / "fonts"
        for family in ("Manrope", "JetBrainsMono"):
            assert next(fonts.glob(f"{family}-*.woff2")).stat().st_size > 10_000
            assert "SIL OPEN FONT LICENSE" in (fonts / f"{family}-OFL.txt").read_text().upper()
        css = (SRC / "static" / "css" / "fonts.css").read_text(encoding="utf-8")
        assert css.count("@font-face") == 2
        assert "http" not in css

    def test_fonts_match_their_recorded_hash(self) -> None:
        fonts = SRC / "static" / "fonts"
        readme = (fonts / "README.md").read_text(encoding="utf-8")
        for path in fonts.glob("*.woff2"):
            assert hashlib.sha256(path.read_bytes()).hexdigest() in readme, path.name

    def test_manifest_lists_the_icons(self, client: Client) -> None:
        response = _page(client, "manifest")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/manifest+json"
        data = json.loads(response.content)
        assert data["name"] == "HouseCairn"
        assert {icon["sizes"] for icon in data["icons"]} == {"192x192", "512x512"}
        assert all(icon["src"].startswith("/static/") for icon in data["icons"])

    def test_manifest_is_translated(self, client: Client) -> None:
        response = client.get(reverse("manifest"), headers={"Accept-Language": "fr"})
        data = json.loads(response.content)
        assert data["lang"] == "fr"
        assert "dans la maison" in data["description"]

    def test_manifest_varies_on_the_language_header(self, client: Client) -> None:
        """A shared cache must keep one manifest per language."""
        for language in ("en", "fr"):
            response = client.get(reverse("manifest"), headers={"Accept-Language": language})
            assert response["Content-Language"] == language
            assert "Accept-Language" in response["Vary"]


class TestScreens:
    """Every screen answers 200, carries the logo and the name, and respects the CSP."""

    SIGNED_OUT = (
        "index",
        "account_login",
        "about",
        "account_signup",
    )
    # These two send mail: they exist only on an instance with a mail server.
    NEEDS_EMAIL = ("account_reset_password", "account_reset_password_done")
    SIGNED_IN = (
        "index",
        "homes:list",
        "more",
        "more-security",
        "about",
        "mfa_index",
        "mfa_list_webauthn",
        "account_change_password",
        "account_delete",
        "account_logout",
        "account_reauthenticate",
        "socialaccount_connections",
    )

    @pytest.mark.django_db
    @pytest.mark.parametrize("name", SIGNED_OUT)
    def test_signed_out(self, client: Client, name: str) -> None:
        response = _page(client, name)
        assert response.status_code == 200, name
        self._check(response.text)
        assert 'class="nav"' not in response.text

    @pytest.mark.django_db
    @pytest.mark.parametrize("name", NEEDS_EMAIL)
    @override_settings(EMAIL_HOST="smtp.example.com", EMAIL_BACKEND=SMTP_BACKEND)
    def test_signed_out_with_email(self, client: Client, name: str) -> None:
        response = _page(client, name)
        assert response.status_code == 200, name
        self._check(response.text)

    @pytest.mark.django_db
    @pytest.mark.parametrize("name", SIGNED_IN)
    def test_signed_in(self, client: Client, alice: object, name: str) -> None:
        client.force_login(alice)  # type: ignore[arg-type]
        response = _page(client, name)
        assert response.status_code == 200, name
        self._check(response.text)
        assert 'class="nav"' in response.text

    @pytest.mark.django_db
    @override_settings(EMAIL_HOST="smtp.example.com", EMAIL_BACKEND=SMTP_BACKEND)
    def test_signed_in_with_email(self, client: Client, alice: object) -> None:
        client.force_login(alice)  # type: ignore[arg-type]
        for name in ("account_email", "more-security"):
            response = _page(client, name)
            assert response.status_code == 200, name
            self._check(response.text)
        assert reverse("account_email") in _page(client, "more-security").text

    @pytest.mark.django_db
    def test_more_hides_the_email_page_without_email(self, client: Client, alice: object) -> None:
        client.force_login(alice)  # type: ignore[arg-type]
        assert reverse("account_email") not in _page(client, "more").text
        assert client.get(reverse("account_email")).status_code == 404

    @pytest.mark.django_db
    def test_instance_and_audit_screens(
        self, client: Client, alice: object, alice_home: Home
    ) -> None:
        from housecairn.accounts.models import User

        admin = User.objects.create_superuser(email="admin@example.com", password="x" * 12)
        client.force_login(admin)
        for name in ("instance:overview", "instance:audit"):
            response = _page(client, name)
            assert response.status_code == 200, name
            self._check(response.text)
        assert reverse("instance:overview") in _page(client, "more").text
        client.force_login(alice)  # type: ignore[arg-type]
        assert reverse("instance:overview") not in _page(client, "more").text
        for name in ("homes:audit", "homes:delete"):
            response = _page(client, name, home_id=alice_home.id)
            assert response.status_code == 200, name
            self._check(response.text)

    @pytest.mark.django_db
    def test_home_detail(self, client: Client, alice: object, alice_home: Home) -> None:
        client.force_login(alice)  # type: ignore[arg-type]
        response = _page(client, "homes:detail", home_id=alice_home.id)
        assert response.status_code == 200
        assert "Main house" in response.text
        self._check(response.text)

    @staticmethod
    def _check(html: str) -> None:
        assert LOGO in html
        assert "HouseCairn" in html
        assert "Housecairn" not in html
        assert inline_code_problems(html) == []
        assert 'href="/static/css/fonts.css"' in html
        assert 'rel="manifest"' in html
        assert 'rel="apple-touch-icon"' in html

    @pytest.mark.django_db
    def test_more_requires_sign_in(self, client: Client) -> None:
        response = _page(client, "more")
        assert response.status_code == 302
        assert reverse("account_login") in response["Location"]

    @pytest.mark.django_db
    def test_more_lists_the_destinations(self, client: Client, alice: object) -> None:
        client.force_login(alice)  # type: ignore[arg-type]
        html = _page(client, "more").text
        for name in ("homes:list", "more-security", "api-docs", "about", "account_logout"):
            assert reverse(name) in html

    @pytest.mark.django_db
    def test_about_carries_the_attribution_and_the_ai_declaration(self, client: Client) -> None:
        html = _page(client, "about").text
        assert "HouseCairn, by Robvred" in html
        assert "AGPL-3.0" in html
        assert "AI agent" in html
        assert settings.HOUSECAIRN_SOURCE_URL in html

    @pytest.mark.django_db
    def test_current_navigation_entry_is_marked(self, client: Client, alice: object) -> None:
        client.force_login(alice)  # type: ignore[arg-type]
        for name, label in (("index", "Today"), ("homes:list", "More"), ("more", "More")):
            html = _page(client, name).text
            bar = html.split('<div class="nav-drawer">')[0]
            assert bar.count('aria-current="page"') == 1, name
            current = bar.split('aria-current="page"')[1].split("</a>")[0]
            assert label in current, name

    def test_navigation_section_of_each_kind_of_page(self) -> None:
        def section(path: str) -> str:
            request = RequestFactory().get(path)
            request.resolver_match = resolve(path)
            return nav_section(request)

        assert section("/") == "today"
        assert section(reverse("homes:list")) == "more"
        assert section(reverse("more")) == "more"
        assert section(reverse("mfa_index")) == "more"
        assert nav_section(RequestFactory().get("/nope")) == ""

    @pytest.mark.django_db
    def test_french(self, client: Client, alice: object) -> None:
        client.force_login(alice)  # type: ignore[arg-type]
        html = client.get(reverse("more-security"), headers={"Accept-Language": "fr"}).text
        assert "Sécurité du compte" in html
        more = client.get(reverse("more"), headers={"Accept-Language": "fr"}).text
        assert "Se déconnecter" in more


class TestErrorPages:
    @pytest.mark.django_db
    def test_not_found(self, client: Client) -> None:
        response = client.get("/no/such/page/")
        assert response.status_code == 404
        assert LOGO in response.text
        assert "Page not found" in response.text
        assert inline_code_problems(response.text) == []

    @pytest.mark.parametrize(
        ("name", "text"),
        [
            ("400.html", "Bad request"),
            ("403.html", "Access denied"),
            ("403_csrf.html", "Session expired"),
            ("404.html", "Page not found"),
        ],
    )
    def test_templates_render_with_the_brand(self, name: str, text: str) -> None:
        html = render_to_string(name)
        assert text in html
        assert LOGO in html
        assert inline_code_problems(html) == []

    def test_server_error_page_is_standalone(self) -> None:
        html = render_to_string("500.html")
        assert "Something went wrong" in html
        assert LOGO in html
        assert inline_code_problems(html) == []
        # Shown when the application itself failed: no session, no form, no database.
        assert "csrf" not in html.lower()
        assert "<form" not in html

    def test_server_error_page_in_french(self) -> None:
        with translation.override("fr"):
            assert "Une erreur est survenue" in render_to_string("500.html")

    @pytest.mark.django_db
    def test_csrf_failure_page(self) -> None:
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("account_login"), {"login": "a@example.com"})
        assert response.status_code == 403
        assert "Session expired" in response.text
        assert LOGO in response.text


class TestSignInPage:
    @pytest.fixture
    def html(self, client: Client) -> str:
        return _page(client, "account_login").text

    @pytest.mark.django_db
    def test_no_passkey_button(self, html: str) -> None:
        assert 'id="passkey_login"' not in html
        assert "Sign in with a passkey" not in html
        assert "Se connecter avec une clé d" not in html

    @pytest.mark.django_db
    def test_address_field_offers_the_browsers_passkeys(self, html: str) -> None:
        field = re.search(r"<input[^>]*name=\"login\"[^>]*>", html)
        assert field is not None
        assert 'autocomplete="username webauthn"' in field.group(0)

    @pytest.mark.django_db
    def test_passkey_script_is_local_and_the_form_posts_to_allauth(self, html: str) -> None:
        sources = [tag.get("src") for tag in script_tags(html)]
        assert "/static/js/passkey-login.js" in sources
        assert "/static/mfa/js/webauthn-json.js" in sources
        assert all(src.startswith("/static/") for src in sources if src)
        assert f'action="{reverse("mfa_login_webauthn")}"' in html
        assert 'id="mfa_credential"' in html
        assert inline_code_problems(html) == []

    @pytest.mark.django_db
    @override_settings(MFA_PASSKEY_LOGIN_ENABLED=False)
    def test_no_passkey_machinery_when_disabled(self, client: Client) -> None:
        html = _page(client, "account_login").text
        assert "passkey-login.js" not in html
        assert "mfa_login" not in html
        assert "webauthn" not in html.split('name="login"')[1].split(">")[0]

    @pytest.mark.django_db
    def test_no_separator_and_no_provider_list_without_provider(self, html: str) -> None:
        assert "<hr" not in html
        assert "third-party" not in html.lower()
        assert "sso-list" not in html

    FORGOT = "Forgot your password?"
    SMTP = {"EMAIL_HOST": "smtp.example.com", "EMAIL_BACKEND": SMTP_BACKEND}

    @pytest.mark.django_db
    def test_no_forgot_password_link_without_email(self, html: str) -> None:
        assert self.FORGOT not in html
        assert reverse("account_reset_password") not in html

    @pytest.mark.django_db
    def test_no_forgot_password_link_with_console_backend(self, client: Client) -> None:
        with override_settings(EMAIL_HOST="smtp.example.com", EMAIL_BACKEND=CONSOLE_BACKEND):
            html = _page(client, "account_login").text
        assert self.FORGOT not in html

    @pytest.mark.django_db
    def test_no_forgot_password_link_without_host(self, client: Client) -> None:
        with override_settings(EMAIL_HOST="", EMAIL_BACKEND=SMTP_BACKEND):
            html = _page(client, "account_login").text
        assert self.FORGOT not in html

    @pytest.mark.django_db
    def test_forgot_password_link_with_email(self, client: Client) -> None:
        with override_settings(**self.SMTP):
            html = _page(client, "account_login").text
        assert self.FORGOT in html
        assert f'href="{reverse("account_reset_password")}"' in html

    @pytest.mark.django_db
    def test_reset_page_is_not_found_without_email(self, client: Client) -> None:
        assert _page(client, "account_reset_password").status_code == 404
        assert _page(client, "account_reset_password_done").status_code == 404
        assert (
            client.post(reverse("account_reset_password"), {"email": "a@example.com"}).status_code
            == 404
        )

    @pytest.mark.django_db
    def test_reset_page_works_with_email(self, client: Client) -> None:
        with override_settings(**self.SMTP):
            assert _page(client, "account_reset_password").status_code == 200
            assert _page(client, "account_reset_password_done").status_code == 200

    @pytest.mark.django_db
    def test_no_signup_link_when_closed(self, html: str) -> None:
        assert reverse("account_signup") not in html

    @pytest.mark.django_db
    @override_settings(HOUSECAIRN_SIGNUP_OPEN=True)
    def test_signup_link_when_open(self, client: Client) -> None:
        assert reverse("account_signup") in _page(client, "account_login").text

    PROVIDERS = {
        "openid_connect": {
            "APPS": [
                {
                    "provider_id": provider_id,
                    "name": name,
                    "client_id": "housecairn",
                    "secret": "not-a-secret",
                    "settings": {"server_url": "https://id.example.com"},
                }
                for provider_id, name in (("first", "Company SSO"), ("second", "Team SSO"))
            ]
        }
    }
    BUTTON = (
        r'<form method="post" action="([^"]+)">\s*'
        r'<input type="hidden" name="csrfmiddlewaretoken" value="[^"]+">\s*'
        r'<button type="submit" class="button button-secondary">([^<]+)</button>'
    )

    @pytest.mark.django_db
    def test_one_button_per_provider_labelled_continue_with_its_name(self, client: Client) -> None:
        with override_settings(SOCIALACCOUNT_PROVIDERS=self.PROVIDERS):
            html = _page(client, "account_login").text
        buttons = re.findall(self.BUTTON, html)
        assert sorted(label for _href, label in buttons) == [
            "Continue with Company SSO",
            "Continue with Team SSO",
        ]
        assert all("/accounts/oidc/" in href for href, _label in buttons)
        assert "<hr" not in html
        assert "third-party" not in html.lower()
        # The buttons come after the sign-in form, not before.
        assert html.index("</form>") < html.index("Continue with Company SSO")

    @pytest.mark.django_db
    def test_provider_button_is_translated(self, client: Client) -> None:
        with override_settings(SOCIALACCOUNT_PROVIDERS=self.PROVIDERS):
            html = client.get(reverse("account_login"), headers={"Accept-Language": "fr"}).text
        labels = sorted(label for _href, label in re.findall(self.BUTTON, html))
        assert labels == ["Continuer avec Company SSO", "Continuer avec Team SSO"]


class TestTokens:
    """WCAG 2.2 AA on the design tokens: text 4.5:1, interface components 3:1."""

    TEXT = (
        ("text", "background"),
        ("text", "surface"),
        ("text", "surface-muted"),
        ("text", "sand-soft"),
        ("text", "sage-soft"),
        ("text", "success-soft"),
        ("text", "info-soft"),
        ("text", "warning-soft"),
        ("text", "danger-soft"),
        ("text-muted", "background"),
        ("text-muted", "surface"),
        ("text-muted", "surface-muted"),
        ("text-muted", "sand-soft"),
        ("text-muted", "sage-soft"),
        ("text-inverse", "primary"),
        ("text-inverse", "primary-hover"),
        ("text-inverse", "danger"),
        ("primary", "background"),
        ("primary", "surface"),
        ("primary", "sage-soft"),
        ("primary", "sand-soft"),
        ("primary-hover", "background"),
        ("text-on-sand", "sand"),
        ("text-sand", "sand-soft"),
        ("text-sand", "background"),
        ("text-sand", "surface"),
        ("success", "success-soft"),
        ("success", "surface"),
        ("warning", "warning-soft"),
        ("danger", "danger-soft"),
        ("danger", "surface"),
        ("danger", "background"),
        ("info", "info-soft"),
        ("info", "surface"),
        ("sage-soft", "slate-deep"),
    )
    COMPONENTS = (
        ("border-strong", "surface"),
        ("border-strong", "background"),
        ("focus", "surface"),
        ("focus", "background"),
        ("focus", "sand-soft"),
    )

    @staticmethod
    def _luminance(colour: str) -> float:
        channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    @classmethod
    def ratio(cls, foreground: str, background: str) -> float:
        colours = design.load_tokens()["color"]
        lights = sorted(
            (cls._luminance(colours[foreground]), cls._luminance(colours[background])),
            reverse=True,
        )
        return (lights[0] + 0.05) / (lights[1] + 0.05)

    @pytest.mark.parametrize(("foreground", "background"), TEXT)
    def test_text_pairs_reach_4_5(self, foreground: str, background: str) -> None:
        assert self.ratio(foreground, background) >= 4.5, (foreground, background)

    @pytest.mark.parametrize(("foreground", "background"), COMPONENTS)
    def test_component_pairs_reach_3(self, foreground: str, background: str) -> None:
        assert self.ratio(foreground, background) >= 3.0, (foreground, background)

    def test_every_token_colour_is_a_hex_value_or_an_overlay(self) -> None:
        for name, value in design.load_tokens()["color"].items():
            assert re.fullmatch(r"#[0-9A-F]{6}", value) or name == "overlay", name

    def test_app_css_imports_the_layers_in_order(self) -> None:
        css = (SRC / "static" / "css" / "app.css").read_text(encoding="utf-8")
        rules = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL).split()
        # Nothing but local imports, so that the order of the cascade is the
        # order of the layers.
        assert " ".join(rules) == " ".join(f'@import url("{layer}");' for layer in STYLE_LAYERS)
        for layer in STYLE_LAYERS:
            assert "@import" not in (SRC / "static" / "css" / layer).read_text(encoding="utf-8")

    def test_the_css_only_uses_declared_variables(self) -> None:
        declared = set(re.findall(r"(--hc-[a-z0-9-]+):", design.expected_css()))
        used = {
            variable
            for css in (*STYLE_LAYERS, "fonts.css")
            for variable in re.findall(
                r"var\((--hc-[a-z0-9-]+)\)",
                (SRC / "static" / "css" / css).read_text(encoding="utf-8"),
            )
        }
        assert sorted(used - declared) == []

    def test_touch_targets_are_44_pixels(self) -> None:
        layout = design.load_tokens()["layout"]
        assert float(layout["touch-target"].removesuffix("rem")) * 16 >= 44
