"""The About screen shows the version of the application: `version` in pyproject.toml."""

import tomllib
from collections.abc import Iterator
from importlib import metadata
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from housecairn.core import version

ROOT = Path(__file__).parent.parent
EXPECTED = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


@pytest.fixture(autouse=True)
def fresh_version() -> Iterator[None]:
    version.application_version.cache_clear()
    yield
    version.application_version.cache_clear()


@pytest.fixture
def not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The container image: the source is on PYTHONPATH, the package is not installed."""

    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(version.metadata, "version", missing)


def about(client: Client, language: str) -> str:
    response = client.get(reverse("about"), headers={"accept-language": language})
    assert response.status_code == 200
    return response.text


@pytest.mark.django_db
def test_the_version_of_pyproject_is_shown(client: Client) -> None:
    assert f'<p class="about-version">Version {EXPECTED}</p>' in about(client, "en")


@pytest.mark.django_db
def test_the_label_is_translated(client: Client) -> None:
    assert f'<p class="about-version">Version {EXPECTED}</p>' in about(client, "fr")


@pytest.mark.django_db
@pytest.mark.usefixtures("not_installed")
def test_without_the_package_the_number_comes_from_pyproject(client: Client) -> None:
    html = about(client, "en")
    assert f'<p class="about-version">Version {EXPECTED}</p>' in html
    assert "Version dev" not in html


@pytest.mark.usefixtures("not_installed")
def test_pyproject_is_the_file_the_image_ships() -> None:
    # The Dockerfile copies pyproject.toml to /app, the parent of src/.
    assert ROOT.resolve() / "pyproject.toml" == version.PYPROJECT
    assert version.application_version() == EXPECTED


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not toml [",
        '[project]\nname = "other"\nversion = "1.0"\n',
        '[project]\nname = "housecairn"\n',
        '[project]\nname = "housecairn"\nversion = ""\n',
    ],
)
def test_an_unreadable_pyproject_gives_dev(tmp_path: Path, content: str) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(content)
    assert version._from_pyproject(path) == "dev"


def test_a_missing_pyproject_gives_dev(tmp_path: Path) -> None:
    assert version._from_pyproject(tmp_path / "pyproject.toml") == "dev"


def test_the_number_is_not_written_in_the_template() -> None:
    template = ROOT / "src/housecairn/templates/core/about.html"
    assert EXPECTED not in template.read_text()
