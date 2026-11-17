"""The shortcut to the iOS beta (TestFlight): one address, in More and About."""

import re
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.core import about_links, more

pytest_plugins = ("tests.rooms_fixtures",)

ROOT = Path(about_links.__file__).resolve().parents[3]
LABELS = {"en": "iOS app (TestFlight beta)", "fr": "Application iOS (bêta TestFlight)"}


def anchors(html: str) -> list[str]:
    """The opening tags of the links that lead to the beta."""
    return re.findall(r"<a\b[^>]*" + re.escape(about_links.TESTFLIGHT_URL) + r"[^>]*>", html)


def test_the_address_is_a_public_https_testflight_invitation() -> None:
    assert re.fullmatch(r"https://testflight\.apple\.com/join/\w+", about_links.TESTFLIGHT_URL)


def test_no_other_file_of_the_code_repeats_the_address() -> None:
    needle = "testflight.apple.com/join/"
    allowed = {ROOT / "src/housecairn/core/about_links.py", Path(__file__).resolve()}
    found = []
    for folder in ("src", "tests", "scripts", "deploy"):
        for path in (ROOT / folder).rglob("*"):
            if (
                path.is_file()
                and path.suffix in {".py", ".html", ".js", ".json", ".yaml", ".yml", ".sh", ".po"}
                and path not in allowed
                and needle in path.read_text(errors="ignore")
            ):
                found.append(str(path.relative_to(ROOT)))
    assert found == []


@pytest.mark.django_db
@pytest.mark.parametrize("language", ["en", "fr"])
def test_more_leads_to_the_beta_in_a_new_tab_without_a_referrer(
    signed_in: Client, language: str
) -> None:
    html = signed_in.get(reverse("more"), headers={"accept-language": language}).content.decode()
    tags = anchors(html)
    assert len(tags) == 2  # the column of a computer and the page of a phone
    for tag in tags:
        assert 'target="_blank"' in tag
        assert 'rel="noopener noreferrer"' in tag
    assert LABELS[language] in html


@pytest.mark.django_db
@pytest.mark.parametrize("language", ["en", "fr"])
def test_about_leads_to_the_beta_in_a_new_tab_without_a_referrer(
    client: Client, language: str
) -> None:
    html = client.get(reverse("about"), headers={"accept-language": language}).content.decode()
    tags = anchors(html)
    assert len(tags) == 1
    assert 'target="_blank"' in tags[0]
    assert 'rel="noopener noreferrer"' in tags[0]
    assert LABELS[language] in html


@pytest.mark.django_db
def test_the_beta_is_a_help_entry_with_its_icon_and_the_other_entries_stay_in_the_tab(
    signed_in: Client, alice_home: object, alice: User
) -> None:
    entries = [e for g in more.groups(alice) if g.key == more.HELP for e in g.entries]
    beta = [e for e in entries if e.url == about_links.TESTFLIGHT_URL]
    assert [e.icon for e in beta] == ["phone-download"]
    assert [e.external for e in entries] == [False, False, False, True, False]


@pytest.mark.django_db
def test_the_page_stays_private_and_the_policy_is_not_widened(client: Client) -> None:
    from django.conf import settings

    policy = str(settings.CONTENT_SECURITY_POLICY)
    assert "apple.com" not in policy
    response = client.get(reverse("account_login"))
    assert "testflight" not in response.content.decode()
    assert "apple.com" not in response.headers.get("Content-Security-Policy", "")
