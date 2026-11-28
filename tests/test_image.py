"""Container image files: tags script, Dockerfile and compose rules (docs/adr/0015).

The image itself is built and started by the `Container image` job of
`.github/workflows/image.yml`; these tests keep its source files honest.
"""

import re
import shlex
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse

from housecairn.accounts.models import User
from scripts import image_tags

REPO = Path(__file__).resolve().parents[1]
SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize(
    ("ref", "version", "tags"),
    [
        ("refs/heads/main", "edge-0123456", ["edge", "sha-0123456"]),
        ("refs/tags/v1.2.3", "1.2.3", ["1.2.3", "1.2", "latest", "sha-0123456"]),
        ("refs/tags/v0.10.0", "0.10.0", ["0.10.0", "0.10", "latest", "sha-0123456"]),
        ("refs/tags/v1.0.0-rc.1", "1.0.0-rc.1", ["1.0.0-rc.1", "sha-0123456"]),
    ],
)
def test_tags(ref: str, version: str, tags: list[str]) -> None:
    assert image_tags.image_tags(ref, SHA) == (version, tags)


@pytest.mark.parametrize(
    "ref",
    [
        "refs/heads/feature",
        "refs/pull/7/merge",
        "refs/tags/1.2.3",
        "refs/tags/v1.2",
        "refs/tags/v01.2.3",
        "refs/tags/v1.2.3-",
        "refs/tags/v1.2.3;latest",
    ],
)
def test_nothing_published_for_other_refs(ref: str) -> None:
    with pytest.raises(ValueError, match="no image"):
        image_tags.image_tags(ref, SHA)


def test_short_sha_is_refused() -> None:
    with pytest.raises(ValueError, match="full commit SHA"):
        image_tags.image_tags("refs/heads/main", "0123456")


def test_main_prints_github_outputs(capsys: pytest.CaptureFixture[str]) -> None:
    assert image_tags.main(["refs/tags/v2.0.1", SHA]) == 0
    assert capsys.readouterr().out == "version=2.0.1\ntags=2.0.1 2.0 latest sha-0123456\n"


def test_main_errors(capsys: pytest.CaptureFixture[str]) -> None:
    assert image_tags.main(["refs/heads/other", SHA]) == 1
    assert image_tags.main([]) == 2
    assert "usage" in capsys.readouterr().err


def test_every_base_image_is_pinned_by_digest() -> None:
    images = re.findall(r"^FROM\s+(\S+)", (REPO / "Dockerfile").read_text(), re.MULTILINE)
    assert len(images) == 3
    for image in images:
        assert re.fullmatch(r"[a-z0-9./-]+:[\w.-]+@sha256:[0-9a-f]{64}", image), image


def test_final_stage_is_unprivileged_and_healthchecked() -> None:
    dockerfile = (REPO / "Dockerfile").read_text()
    final = dockerfile.rsplit("\nFROM ", 1)[1]
    assert "\nUSER 10001:10001\n" in final
    assert 'CMD ["python", "-m", "housecairn.entrypoint", "healthcheck"]' in final
    assert 'org.opencontainers.image.licenses="AGPL-3.0-only"' in final


def test_base_image_security_upgrades_have_a_floor() -> None:
    # Each Debian package upgraded ahead of the base image must be checked
    # against its fixed version, or a stale mirror would ship it unfixed.
    final = (REPO / "Dockerfile").read_text().rsplit("\nFROM ", 1)[1]
    packages = re.findall(r"--only-upgrade\s+([\w.+-]+(?:\s+[\w.+-]+)*)", final)
    for package in " ".join(packages).split():
        floor = rf"dpkg-query -W -f='\$\{{Version\}}' {re.escape(package)}\)\"\s*\\\n\s*ge '"
        assert re.search(floor, final), package


# Debian packages whose security fixes the final stage requires (docs/adr/0015,
# amendments of 2026-11-08 and 2026-11-28): minimum version, and whether the
# pinned base image lacks the fix, so the step upgrades it. A floor only goes
# up; change it here and in the Dockerfile together.
WATCHED_PACKAGES = {
    "libc6": ("2.41-12+deb13u5", False),
    "libc-bin": ("2.41-12+deb13u5", False),
    "zlib1g": ("1:1.3.dfsg+really1.3.1-1+deb13u1", False),
    "libzstd1": ("1.5.7+dfsg-1+deb13u1", True),
    "login.defs": ("1:4.17.4-2+deb13u1", True),
    "passwd": ("1:4.17.4-2+deb13u1", True),
}


def test_watched_debian_packages_have_their_fixed_version_as_floor() -> None:
    final = (REPO / "Dockerfile").read_text().rsplit("\nFROM ", 1)[1]
    floors = dict(
        re.findall(r"dpkg-query -W -f='\$\{Version\}' ([\w.+-]+)\)\"\s*\\\n\s*ge '([^']+)'", final)
    )
    upgraded = set(
        " ".join(re.findall(r"--only-upgrade\s+([\w.+-]+(?:[ \t]+[\w.+-]+)*)", final)).split()
    )
    assert floors == {name: floor for name, (floor, _) in WATCHED_PACKAGES.items()}
    assert upgraded == {name for name, (_, upgrade) in WATCHED_PACKAGES.items() if upgrade}


def test_both_python_stages_use_the_same_base_image() -> None:
    images = re.findall(r"^FROM\s+(python:\S+)", (REPO / "Dockerfile").read_text(), re.MULTILINE)
    assert len(images) == 2
    assert images[0] == images[1]


def test_published_image_scan_uses_the_same_grype_and_no_default_permissions() -> None:
    workflows = REPO / ".github" / "workflows"
    image = (workflows / "image.yml").read_text()
    scan = (workflows / "image-scan.yml").read_text()
    for name in ("GRYPE_VERSION", "GRYPE_SHA256"):
        pin = re.search(rf'^  {name}: "?([^"\n]+)"?$', image, re.MULTILINE)
        assert pin, name
        assert re.search(rf'^  {name}: "?{re.escape(pin.group(1))}"?$', scan, re.MULTILINE), name
    assert "--only-fixed --fail-on high" in scan
    assert "continue-on-error" not in scan
    assert "\npermissions: {}\n" in scan
    assert "pull_request" not in scan


def test_base_image_is_not_delayed_by_the_dependabot_cooldown() -> None:
    config = (REPO / ".github" / "dependabot.yml").read_text()
    docker = config.split("package-ecosystem: docker\n", 1)[1].split("  - package-ecosystem:", 1)[0]
    assert "interval: daily" in docker
    assert re.search(r"exclude:\s*\n\s*- python\b", docker)


def test_build_context_lets_in_only_what_the_image_needs() -> None:
    lines = [
        line
        for line in (REPO / ".dockerignore").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert lines[0] == "*"
    allowed = {line[1:].rstrip("/") for line in lines if line.startswith("!")}
    assert allowed == {
        "pyproject.toml",
        "uv.lock",
        "LICENSE",
        "NOTICE",
        "manage.py",
        "design",
        "src",
    }


def test_compose_example_is_hardened() -> None:
    compose = (REPO / "compose.yaml").read_text()
    for setting in (
        "read_only: true",
        "cap_drop: [ALL]",
        'security_opt: ["no-new-privileges:true"]',
        "- /tmp:rw,noexec,nosuid",
        "- housecairn-data:/data",
    ):
        assert setting in compose, setting


# What the image leaves out (Redoc, the browsable API's assets, the `.po`
# sources) and what it must keep: Swagger UI for /api/docs/, the compiled French
# catalog.


def _build_step() -> str:
    dockerfile = (REPO / "Dockerfile").read_text()
    build = dockerfile.split(" AS build\n", 1)[1].split("\nFROM ", 1)[0]
    return next(block for block in build.split("\nRUN ") if "collectstatic" in block)


def _collectstatic_ignores() -> list[str]:
    command = _build_step().split("collectstatic", 1)[1].split("&&", 1)[0]
    words = shlex.split(command.replace("\\\n", " "))
    return [words[i + 1] for i, word in enumerate(words) if word == "--ignore"]


def test_build_leaves_out_what_the_application_never_serves() -> None:
    step = _build_step()
    assert _collectstatic_ignores() == ["drf_spectacular_sidecar/redoc/*", "rest_framework"]
    assert '"$site/drf_spectacular_sidecar/static/drf_spectacular_sidecar/redoc"' in step
    assert '"$site/rest_framework/static"' in step
    # The sources go only after the catalogs are compiled, and the build fails
    # if the application's French catalog is not there.
    assert step.index("compilemessages") < step.index("-name '*.po' -delete")
    assert step.index("-name '*.po' -delete") < step.index(
        "test -f src/housecairn/core/locale/fr/LC_MESSAGES/django.mo"
    )
    # Nothing is removed from the collected files themselves.
    assert "/app/static/" not in step.split("collectstatic", 1)[1]


@pytest.mark.django_db
def test_static_files_of_the_image_still_serve_the_api_docs(
    tmp_path: Path, client: Client, alice: User
) -> None:
    root = tmp_path / "static"
    with override_settings(STATIC_ROOT=root):
        call_command(
            "collectstatic",
            interactive=False,
            verbosity=0,
            ignore_patterns=_collectstatic_ignores(),
        )
        client.force_login(alice)
        page = client.get(reverse("api-docs"))
    assert page.status_code == 200
    urls = re.findall(r'(?:href|src)="/static/([^"?]+)"', page.content.decode())
    assert any(url.startswith("drf_spectacular_sidecar/swagger-ui-dist/") for url in urls)
    missing = [url for url in urls if not (root / url).is_file()]
    assert missing == []
    assert (root / "css" / "app.css").is_file()
    assert not (root / "drf_spectacular_sidecar" / "redoc").exists()
    assert not (root / "rest_framework").exists()
