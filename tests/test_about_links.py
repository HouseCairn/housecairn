"""Links of the About screen: real addresses from one module, stores without a link yet."""

import re
from html.parser import HTMLParser

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import translation

from housecairn.core import about_links

REAL = ("site", "docs", "app", "addon", "integration", "mobile", "ios")
STORES = ("android",)


class Tiles(HTMLParser):
    """The tiles of the Links section: key by label, with href, rel and the text."""

    def __init__(self) -> None:
        super().__init__()
        self.tiles: list[dict[str, str | None]] = []
        self._in_list = False
        self._current: dict[str, str | None] | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "ul" and "about-links" in (a.get("class") or ""):
            self._in_list = True
        elif (
            self._in_list
            and tag in {"a", "span"}
            and "about-link" in (a.get("class") or "").split()
        ):
            self._current = {"tag": tag, "href": a.get("href"), "rel": a.get("rel"), "text": ""}
            self.tiles.append(self._current)
            self._depth = 1
        elif self._current is not None:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "ul":
            self._in_list = False
        elif self._current is not None:
            self._depth -= 1
            if self._depth == 0:
                self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"] = (self._current["text"] or "") + data


def tiles(client: Client, language: str = "en") -> list[dict[str, str | None]]:
    response = client.get(reverse("about"), headers={"accept-language": language})
    assert response.status_code == 200
    parser = Tiles()
    parser.feed(response.text)
    return parser.tiles


def test_every_real_link_is_an_https_address_defined_in_the_module() -> None:
    by_key = {link.key: link for link in about_links.links()}
    assert set(by_key) == {*REAL, *STORES}
    for key in REAL:
        assert by_key[key].url.startswith("https://"), key
        assert by_key[key].available
    assert by_key["site"].url == "https://housecairn.app"
    assert by_key["app"].url.endswith("/HouseCairn/housecairn")
    assert by_key["mobile"].url.endswith("/HouseCairn/housecairn-mobile")


@pytest.mark.django_db
def test_the_seven_real_links_are_present_with_a_safe_rel(client: Client) -> None:
    links = [t for t in tiles(client) if t["href"]]
    assert [t["href"] for t in links] == [
        link.url for link in about_links.links() if link.key in REAL
    ]
    for tile in links:
        assert tile["tag"] == "a"
        assert set((tile["rel"] or "").split()) == {"noopener", "noreferrer"}


@pytest.mark.django_db
def test_the_stores_are_not_links_while_their_address_is_empty(client: Client) -> None:
    by_text = {(t["text"] or "").strip().removesuffix("Coming soon"): t for t in tiles(client)}
    tile = by_text["Android"]
    assert tile["tag"] == "span"
    assert tile["href"] is None
    assert "Coming soon" in (tile["text"] or "")
    assert len([t for t in tiles(client) if t["href"] is None]) == 1


@pytest.mark.django_db
def test_a_store_becomes_a_link_when_its_address_is_set(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(about_links.STORE_URLS, "android", "https://apps.example.com/housecairn")
    by_text = {(t["text"] or "").strip().removesuffix("Coming soon"): t for t in tiles(client)}
    assert by_text["Android"]["tag"] == "a"
    assert by_text["Android"]["href"] == "https://apps.example.com/housecairn"
    assert "Coming soon" not in (by_text["Android"]["text"] or "")
    assert [t for t in tiles(client) if t["href"] is None] == []


@pytest.mark.django_db
@override_settings(HOUSECAIRN_DOCS_URL="https://help.example.com/housecairn")
def test_the_documentation_link_follows_the_setting(client: Client) -> None:
    hrefs = [t["href"] for t in tiles(client)]
    assert "https://help.example.com/housecairn" in hrefs


@pytest.mark.django_db
def test_the_labels_are_translated(client: Client) -> None:
    text = " ".join(t["text"] or "" for t in tiles(client, "fr"))
    for label in ("Site web", "Code de l'application", "Module Home Assistant", "Bientôt"):
        assert label in text.replace("&#x27;", "'")


@pytest.mark.django_db
def test_nothing_is_loaded_from_another_host(client: Client) -> None:
    html = client.get(reverse("about")).text
    loaded = re.findall(
        r"""<(?:img|script|link|iframe|source|use)\b[^>]*?(?:src|href|data)="([^"]+)""", html
    )
    assert [u for u in loaded if re.match(r"(https?:)?//", u)] == []
    assert "<svg" in html
    assert "xlink:href" not in html
    assert "url(http" not in html


def test_icons_are_drawn_with_the_text_colour_not_a_brand_colour() -> None:
    from pathlib import Path

    root = Path(about_links.__file__).resolve().parents[1]
    icons = (root / "templates/components/icon.html").read_text()
    assert 'fill="#' not in icons
    assert 'stroke="#' not in icons
    css = (root / "static/css/components.css").read_text()
    start = css.index(".about-links {")
    end = css.index("}", css.index(".about-link-soon {", start))
    section = css[start:end]
    assert "min-height: var(--hc-layout-touch-target);" in section
    assert "#" not in section
    translation.deactivate()
