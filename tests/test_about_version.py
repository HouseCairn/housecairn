"""The About screen shows the version of the application, read from the installed package."""

from importlib import metadata

import pytest
from django.test import Client
from django.urls import reverse


def about(client: Client, language: str) -> str:
    response = client.get(reverse("about"), headers={"accept-language": language})
    assert response.status_code == 200
    return response.text


@pytest.mark.django_db
def test_the_version_of_the_package_is_shown(client: Client) -> None:
    number = metadata.version("housecairn")
    html = about(client, "en")
    assert f'<span class="about-version-number">Version {number}</span>' in html


@pytest.mark.django_db
def test_the_label_is_translated(client: Client) -> None:
    number = metadata.version("housecairn")
    assert f"Version {number}</span>" in about(client, "fr")


@pytest.mark.django_db
def test_the_number_is_not_written_in_the_template(client: Client) -> None:
    from pathlib import Path

    template = Path(__file__).parent.parent / "src/housecairn/templates/core/about.html"
    assert metadata.version("housecairn") not in template.read_text()
