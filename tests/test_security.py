"""Security defaults: CSP, no external resource, vendored htmx, deploy checks."""

import atexit
import functools
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from scripts import check_external_urls
from tests.htmlscan import inline_code_problems

REPO = Path(settings.REPO_DIR)
SRC = REPO / "src" / "housecairn"
HTMX = SRC / "static" / "vendor" / "htmx" / "htmx.min.js"
HTMX_SHA256 = "d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717"


def _env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


@functools.cache
def _isolated_src() -> Path:
    """A copy of `src/` whose parent holds no `.env`, for the processes that import the settings.

    `housecairn.settings` reads a `.env` next to `src/` (`cp .env.example .env` in the README
    sets `DEBUG=true`). A test about the defaults must not depend on the file of whoever
    runs it, so these processes import the settings from a copy of the sources.
    """
    root = Path(tempfile.mkdtemp(prefix="housecairn-settings-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    shutil.copytree(REPO / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
    return root / "src"


def _run_python(code: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "PYTEST"))}
    for key in (
        "SECRET_KEY",
        "SECRET_KEY_FALLBACKS",
        "DEBUG",
        "ALLOWED_HOSTS",
        "DATABASE_URL",
        "SECURE_SSL_REDIRECT",
    ):
        base.pop(key, None)
    base.update(env)
    base["PYTHONPATH"] = str(_isolated_src())
    return subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", code], env=base, capture_output=True, text=True, check=False
    )


@pytest.mark.django_db
def test_csp_header_is_strict(client: Client) -> None:
    policy = client.get(reverse("index"))["Content-Security-Policy"]
    directives = dict(d.strip().split(" ", 1) for d in policy.split(";") if d.strip())
    assert directives["default-src"] == "'none'"
    assert directives["script-src"] == "'self'"
    assert directives["style-src"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["index", "account_login"])
def test_pages_have_no_inline_script_or_style(client: Client, name: str) -> None:
    html = client.get(reverse(name)).text
    assert inline_code_problems(html) == []


def test_no_external_url_in_templates_and_static() -> None:
    # Same check as the CI step, from scripts/check_external_urls.py.
    assert check_external_urls.web_files(SRC)
    assert check_external_urls.offenders(SRC) == []


def test_vendored_htmx_matches_its_recorded_hash() -> None:
    assert hashlib.sha256(HTMX.read_bytes()).hexdigest() == HTMX_SHA256
    readme = (HTMX.parent / "README.md").read_text(encoding="utf-8")
    assert HTMX_SHA256 in readme


def test_secret_key_is_required_without_debug() -> None:
    result = _run_python("import housecairn.settings", {"DEBUG": "false"})
    assert result.returncode != 0
    assert "SECRET_KEY must be set" in result.stderr


def test_settings_processes_do_not_read_the_env_file_of_the_checkout() -> None:
    src = _isolated_src()
    assert src.parent != REPO
    assert not (src.parent / ".env").exists()


def test_the_settings_read_an_env_file_next_to_src() -> None:
    # The control of the test below: where a `.env` is present, DEBUG comes from it.
    src = _isolated_src()
    env_file = src.parent / ".env"
    env_file.write_text("DEBUG=true\n", encoding="utf-8")
    try:
        code = "import housecairn.settings as s; print(s.DEBUG)"
        assert _run_python(code, {}).stdout.split() == ["True"]
    finally:
        env_file.unlink()


def test_a_rotated_key_still_reads_the_secrets_sealed_with_the_former_one() -> None:
    """`SECRET_KEY_FALLBACKS` is read from the environment, comma-separated: a secret sealed
    with the former key is read after the rotation, and sealed again with the new key."""
    from django.test.utils import override_settings

    from housecairn.instance import crypto

    old, new = secrets.token_urlsafe(50), secrets.token_urlsafe(50)
    with override_settings(SECRET_KEY=old, SECRET_KEY_FALLBACKS=[]):
        sealed = crypto.encrypt("smtp-password")
    code = (
        "import os, django; os.environ['DJANGO_SETTINGS_MODULE'] = 'housecairn.settings';"
        "django.setup(); from django.conf import settings; from housecairn.instance import crypto;"
        "print(len(settings.SECRET_KEY_FALLBACKS)); print(crypto.decrypt(os.environ['SEALED']));"
        "fresh = crypto.encrypt('x'); settings.SECRET_KEY_FALLBACKS = [];"
        "crypto._cipher.cache_clear(); print(crypto.decrypt(fresh))"
    )
    rotated = {"SECRET_KEY": new, "SEALED": sealed, "DEBUG": "true"}
    result = _run_python(code, {**rotated, "SECRET_KEY_FALLBACKS": f"another-old-key,{old}"})
    assert result.stdout.split() == ["2", "smtp-password", "x"], result.stderr
    # Without the former key, the secret is unreadable: what the rotation would lose.
    result = _run_python(code, rotated)
    assert result.returncode != 0
    assert "SecretUnreadableError" in result.stderr


@pytest.mark.parametrize(
    ("value", "keys"), [("a, b", ["a", "b"]), (" a ,, b ,", ["a", "b"]), ("", [])]
)
def test_the_former_keys_are_read_without_the_spaces_around_them(
    value: str, keys: list[str]
) -> None:
    """`"<new>, <old>"`, with a space after the comma, keeps the former key as it is."""
    code = "import housecairn.settings as s; print(repr(s.SECRET_KEY_FALLBACKS))"
    result = _run_python(code, {"DEBUG": "true", "SECRET_KEY_FALLBACKS": value})
    assert result.stdout.strip() == repr(keys), result.stderr


def test_debug_is_off_by_default() -> None:
    code = "import housecairn.settings as s; print(s.DEBUG, s.SESSION_COOKIE_SECURE)"
    result = _run_python(code, {"SECRET_KEY": secrets.token_urlsafe(50)})
    assert result.stdout.split() == ["False", "True"], result.stderr


def test_oidc_is_configured_from_environment() -> None:
    code = (
        "import housecairn.settings as s;"
        "app = s.SOCIALACCOUNT_PROVIDERS['openid_connect']['APPS'][0];"
        "print(app['provider_id'], app['settings']['server_url']);"
        "print(' '.join(s.CONTENT_SECURITY_POLICY['DIRECTIVES']['form-action']))"
    )
    env = {
        "DEBUG": "true",
        "OIDC_SERVER_URL": "https://id.example.com/realms/home",
        "OIDC_CLIENT_ID": "housecairn",
        "OIDC_PROVIDER_ID": "sso",
    }
    result = _run_python(code, env)
    assert result.stdout.splitlines() == [
        "sso https://id.example.com/realms/home",
        "'self' https://id.example.com",
    ], result.stderr


def test_no_oidc_by_default() -> None:
    code = "import housecairn.settings as s; print(s.SOCIALACCOUNT_PROVIDERS)"
    assert _run_python(code, {"DEBUG": "true"}).stdout.strip() == "{}"


def test_check_deploy_with_example_production_environment(tmp_path: Path) -> None:
    env = _env_file(REPO / "deploy" / "production.env.example")
    env.update(
        {
            "SECRET_KEY": secrets.token_urlsafe(50),
            "HOUSECAIRN_DATA_DIR": str(tmp_path),
            "DJANGO_SETTINGS_MODULE": "housecairn.settings",
        }
    )
    code = (
        "import sys; from django.core.management import execute_from_command_line;"
        "execute_from_command_line(['manage.py', 'check', '--deploy', '--fail-level', 'WARNING'])"
    )
    result = _run_python(code, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no issues" in result.stdout


class TestRateLimitCache:
    """The cache behind the rate limits can be shared between web processes (docs/adr/0025)."""

    @staticmethod
    def caches(url: str | None, tmp_path: Path) -> str:
        env = {"DEBUG": "true", "HOUSECAIRN_DATA_DIR": str(tmp_path)}
        if url:
            env["HOUSECAIRN_CACHE_URL"] = url
        code = "import housecairn.settings as s; print(s.CACHES['default']['BACKEND'])"
        return _run_python(code, env).stdout.strip()

    def test_the_default_is_per_process(self, tmp_path: Path) -> None:
        assert self.caches(None, tmp_path).endswith("LocMemCache")

    def test_a_database_cache_can_be_chosen(self, tmp_path: Path) -> None:
        assert self.caches("dbcache://housecairn_cache", tmp_path).endswith("DatabaseCache")

    def test_the_table_of_a_database_cache_is_created(self, tmp_path: Path) -> None:
        env = {
            "DEBUG": "true",
            "HOUSECAIRN_DATA_DIR": str(tmp_path),
            "HOUSECAIRN_CACHE_URL": "dbcache://housecairn_cache",
            "DJANGO_SETTINGS_MODULE": "housecairn.settings",
        }
        code = (
            "from django.core.management import execute_from_command_line as run;"
            "from django.core.cache import cache;"
            "run(['manage.py', 'migrate', '--noinput', '-v', '0']);"
            "run(['manage.py', 'createcachetable']);"
            "cache.set('k', 'v', 60); print(cache.get('k'))"
        )
        result = _run_python(code, env)
        assert result.stdout.strip().splitlines()[-1] == "v", result.stderr


PERMISSIONS_POLICY = "camera=(self), microphone=(), geolocation=(), payment=(), usb=()"


@pytest.mark.django_db
class TestPermissionsPolicy:
    """Browser features the application never uses are switched off, on every response."""

    def test_a_page(self, client: Client) -> None:
        assert client.get(reverse("index"))["Permissions-Policy"] == PERMISSIONS_POLICY

    def test_the_api(self, client: Client) -> None:
        response = client.get(reverse("api-v1:home-list"))
        assert response.status_code in (401, 403)
        assert response["Permissions-Policy"] == PERMISSIONS_POLICY

    def test_a_not_found_page(self, client: Client) -> None:
        response = client.get("/no/such/page/")
        assert response.status_code == 404
        assert response["Permissions-Policy"] == PERMISSIONS_POLICY

    def test_a_file_served_by_the_application(self, tmp_path: Path, settings: Any) -> None:
        (tmp_path / "hello.txt").write_text("hello", encoding="utf-8")
        settings.STATIC_ROOT = tmp_path
        # A new client builds the middleware chain again, WhiteNoise included.
        response = Client().get("/static/hello.txt")
        assert response.status_code == 200
        assert response["Permissions-Policy"] == PERMISSIONS_POLICY

    def test_the_application_asks_for_none_of_these_features(self) -> None:
        # No script asks the browser for the location, and only the Scan button of a barcode
        # field asks for the camera (docs/adr/0083), allowed to the application's own pages.
        for path in (SRC / "static" / "js").rglob("*.js"):
            text = path.read_text(encoding="utf-8")
            if path.name != "barcode-scan.js":
                assert "getUserMedia" not in text, path
            assert "geolocation" not in text, path
            assert "audio: true" not in text, path
