"""Installable application and offline reading (docs/adr/0065).

The service worker keeps a page only when the server marks it (`core.offline`): these tests
check the marks, the service worker's own headers and the manifest. What the browser does
with them is checked in a real browser (tests/e2e/pwa_checks.py, workflow UI).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.test import Client, override_settings
from django.urls import reverse, set_script_prefix

from housecairn.accounts.models import User
from housecairn.core import offline
from housecairn.homes.models import Home

KEY = re.compile(r"^[0-9a-f]{32}$")
HTML = 'rel="manifest"'


def _key(response: object) -> str | None:
    return response.headers.get(offline.HEADER)  # type: ignore[attr-defined]


@pytest.mark.django_db
class TestPagesKeptOffline:
    def test_today_and_the_task_list_carry_a_key(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        client.force_login(alice)
        for route in ("tasks:today", "tasks:list"):
            response = client.get(reverse(route, kwargs={"home_id": alice_home.pk}))
            assert response.status_code == 200
            assert KEY.match(_key(response) or ""), route
            assert "private" in response["Cache-Control"]
            assert "public" not in response["Cache-Control"]

    def test_the_key_is_bound_to_the_person_the_session_and_the_home(
        self, client: Client, alice: User, alice_home: Home, bob: User, add_member: object
    ) -> None:
        today = reverse("tasks:today", kwargs={"home_id": alice_home.pk})
        client.force_login(alice)
        first = _key(client.get(today))
        assert first == _key(client.get(today))
        client.logout()
        client.force_login(alice)
        assert _key(client.get(today)) != first, "a new session, a new key"
        add_member(alice_home, bob, "member")  # type: ignore[operator]
        other = Client()
        other.force_login(bob)
        assert _key(other.get(today)) not in {first, None, offline.PURGE}
        second = Home.objects.create(name="Second house")
        add_member(second, alice, "owner")  # type: ignore[operator]
        assert _key(client.get(reverse("tasks:today", kwargs={"home_id": second.pk}))) not in {
            _key(client.get(today)),
            None,
        }

    # Every page a signed-in person may open that holds an account, a secret, the
    # administration or an export: none may be kept.
    SENSITIVE = (
        ("more-security", {}),
        ("profile", {}),
        ("account_change_password", {}),
        ("mfa_index", {}),
        ("api-tokens:list", {}),
        ("api-tokens:new", {}),
        ("calendar:feeds", {}),
        ("notifications:index", {}),
        ("display-preferences", {}),
        ("homes:audit", {"home": True}),
        ("homes:household", {"home": True}),
        ("homes:edit", {"home": True}),
        ("tasks:create", {"home": True}),
        ("tasks:history", {"home": True}),
        ("more", {}),
        ("about", {}),
    )

    @pytest.mark.parametrize(("route", "options"), SENSITIVE)
    def test_no_other_page_is_kept(
        self, client: Client, alice: User, alice_home: Home, route: str, options: dict[str, bool]
    ) -> None:
        client.force_login(alice)
        kwargs = {"home_id": alice_home.pk} if options.get("home") else {}
        response = client.get(reverse(route, kwargs=kwargs))
        assert response.status_code in (200, 302), route
        assert _key(response) is None, route

    def test_the_administration_and_the_api_are_not_kept(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        alice.is_staff = alice.is_superuser = True
        alice.save()
        client.force_login(alice)
        for address in (reverse("admin:index"), reverse("api-docs"), "/api/v1/homes/"):
            response = client.get(address)
            assert _key(response) is None, address

    def test_a_home_of_someone_else_is_not_kept(
        self, client: Client, alice: User, bob_home: Home
    ) -> None:
        """The home of another person is a 404 (it does not say it exists): nothing to keep."""
        client.force_login(alice)
        response = client.get(reverse("tasks:today", kwargs={"home_id": bob_home.pk}))
        assert response.status_code == 404
        assert _key(response) is None

    def test_a_403_says_purge(self, alice: User, alice_home: Home) -> None:
        """A signed-in person whose request is refused (here a missing CSRF token)."""
        client = Client(enforce_csrf_checks=True)
        client.force_login(alice)
        response = client.post(reverse("tasks:today", kwargs={"home_id": alice_home.pk}))
        assert response.status_code == 403
        assert _key(response) == offline.PURGE

    def test_a_401_says_purge(self, client: Client, alice: User, alice_home: Home) -> None:
        """The API answers 401 (JSON, not a page) to a bad token, and the pages are dropped."""
        response = client.get("/api/v1/homes/", headers={"Authorization": "Bearer unknown"})
        assert response.status_code == 401
        assert response["Content-Type"].startswith("application/json")
        assert _key(response) == offline.PURGE

    def test_a_no_store_page_is_not_kept(
        self, client: Client, alice: User, alice_home: Home, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from django.http import HttpResponse

        from housecairn.tasks import views

        def no_store(self: object, *args: object, **kwargs: object) -> HttpResponse:
            response = HttpResponse("<p>x</p>", content_type="text/html")
            response["Cache-Control"] = "no-store"
            return response

        monkeypatch.setattr(views.TodayView, "get", no_store)
        client.force_login(alice)
        response = client.get(reverse("tasks:today", kwargs={"home_id": alice_home.pk}))
        assert response.status_code == 200
        assert _key(response) is None

    def test_a_write_is_never_kept(self, client: Client, alice: User, alice_home: Home) -> None:
        client.force_login(alice)
        response = client.post(reverse("tasks:today", kwargs={"home_id": alice_home.pk}))
        assert _key(response) is None

    def test_signed_out_pages_say_purge(self, client: Client) -> None:
        for route in ("index", "account_login"):
            response = client.get(reverse(route))
            assert _key(response) == offline.PURGE, route

    def test_signing_out_says_purge(self, client: Client, alice: User, alice_home: Home) -> None:
        client.force_login(alice)
        response = client.post(reverse("account_logout"), follow=True)
        assert _key(response) == offline.PURGE

    def test_a_json_answer_of_a_signed_out_visitor_does_not_purge(self, client: Client) -> None:
        """The manifest, fetched without the cookie behind some proxies, must not purge."""
        assert _key(client.get(reverse("manifest"))) is None


@pytest.mark.django_db
class TestServiceWorker:
    def test_headers(self, client: Client) -> None:
        response = client.get(reverse("service-worker"))
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/javascript")
        assert response["Cache-Control"] == "no-cache"
        assert response["Service-Worker-Allowed"] == "/"
        assert reverse("service-worker") == "/service-worker.js"

    def test_it_is_valid_javascript(self, client: Client, tmp_path: Path) -> None:
        if shutil.which("node") is None:
            pytest.skip("Node.js is not installed")
        script = tmp_path / "sw.js"
        script.write_bytes(client.get(reverse("service-worker")).content)
        subprocess.run(["node", "--check", str(script)], check=True)  # noqa: S603, S607

    def test_its_configuration(self, client: Client) -> None:
        body = client.get(reverse("service-worker")).content.decode()
        raw = re.search(r'JSON\.parse\("(.*)"\);', body)
        assert raw is not None
        config = json.loads(raw.group(1).encode().decode("unicode_escape"))
        assert config["scope"] == "/"
        assert config["start"] == "/"
        assert config["offline"] == reverse("offline")
        assert config["static"] == "/static/"
        assert config["header"] == offline.HEADER
        assert reverse("offline") in config["precache"]
        assert all(
            entry.startswith("/static/")
            for entry in config["precache"]
            if not entry.startswith("/offline/")
        )
        assert config["offlinePages"] == {"en": "/offline/?lang=en", "fr": "/offline/?lang=fr"}
        assert set(config["offlinePages"].values()) <= set(config["precache"])
        assert re.fullmatch(r"[0-9a-f]{16}", config["version"])

    def test_only_get(self, client: Client) -> None:
        assert client.post(reverse("service-worker")).status_code == 405

    def test_the_csp_allows_only_workers_of_the_application(self, client: Client) -> None:
        policy = client.get(reverse("account_login"))["Content-Security-Policy"]
        assert "worker-src 'self'" in policy

    @override_settings(FORCE_SCRIPT_NAME="/housecairn", HOUSECAIRN_URL_PREFIX="/housecairn")
    def test_under_a_prefix_the_scope_is_the_prefix(self, client: Client) -> None:
        try:
            response = client.get("/housecairn/service-worker.js")
        finally:
            set_script_prefix("/")
        assert response.status_code == 200
        assert response["Service-Worker-Allowed"] == "/housecairn/"
        body = response.content.decode()
        assert "\\u0022scope\\u0022: \\u0022/housecairn/\\u0022" in body


@pytest.mark.django_db
class TestOfflinePage:
    def test_it_holds_nothing_of_the_person(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        client.force_login(alice)
        response = client.get(reverse("offline"))
        assert response.status_code == 200
        html = response.content.decode()
        assert "Offline" in html
        assert alice.email not in html
        assert alice_home.name not in html
        assert "csrf" not in html.lower()
        assert _key(response) is None

    def test_it_is_translated(self, client: Client) -> None:
        """The words of the person's language, as when the service worker was installed."""
        html = client.get(reverse("offline"), headers={"Accept-Language": "fr"}).content.decode()
        assert "Hors ligne" in html


@pytest.mark.django_db
class TestManifest:
    def test_installable(self, client: Client) -> None:
        data = json.loads(client.get(reverse("manifest")).content)
        assert data["id"] == "/"
        assert data["start_url"] == "/"
        assert data["scope"] == "/"
        assert data["display"] == "standalone"
        purposes = {(icon["sizes"], icon["purpose"]) for icon in data["icons"]}
        assert purposes == {
            ("192x192", "any"),
            ("512x512", "any"),
            ("192x192", "maskable"),
            ("512x512", "maskable"),
        }

    def test_maskable_icons_are_opaque(self) -> None:
        from django.conf import settings
        from PIL import Image

        for size in (192, 512):
            path = Path(settings.BASE_DIR) / f"static/img/brand/icon-maskable-{size}.png"
            with Image.open(path) as image:
                assert image.size == (size, size)
                assert image.mode == "RGB", "a maskable icon has no transparent corner"

    @override_settings(FORCE_SCRIPT_NAME="/housecairn", HOUSECAIRN_URL_PREFIX="/housecairn")
    def test_under_a_prefix(self, client: Client) -> None:
        try:
            data = json.loads(client.get("/housecairn/manifest.webmanifest").content)
        finally:
            set_script_prefix("/")
        assert data["id"] == data["start_url"] == data["scope"] == "/housecairn/"


@pytest.mark.django_db
class TestPages:
    def test_the_manifest_is_fetched_with_the_cookie(self, client: Client) -> None:
        html = client.get(reverse("account_login")).content.decode()
        assert re.search(
            r'<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials">',
            html,
        )

    def test_the_worker_and_the_ios_tags(self, client: Client) -> None:
        html = client.get(reverse("account_login")).content.decode()
        assert 'data-worker="/service-worker.js"' in html
        assert 'data-scope="/"' in html
        assert 'data-signed-in="false"' in html
        assert '<meta name="apple-mobile-web-app-capable" content="yes">' in html
        assert 'name="apple-mobile-web-app-title"' in html

    def test_the_banner_is_hidden_and_dated(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        client.force_login(alice)
        html = client.get(
            reverse("tasks:today", kwargs={"home_id": alice_home.pk})
        ).content.decode()
        assert 'data-signed-in="true"' in html
        banner = r'id="offline-banner"[^>]*data-served="network" hidden><span><strong>Offline: data'
        assert re.search(banner, html)
