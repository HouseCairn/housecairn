"""The glossary of design/glossary.json agrees with the catalogs and with design/patterns.md."""

import json
from pathlib import Path
from typing import Any

import pytest

from housecairn.core.translations import parse_po

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY = ROOT / "design" / "glossary.json"
PATTERNS = ROOT / "design" / "patterns.md"
APPS = ROOT / "src" / "housecairn"


def terms() -> list[dict[str, Any]]:
    document = json.loads(GLOSSARY.read_text(encoding="utf-8"))
    result: list[dict[str, Any]] = document["terms"]
    return result


def french(app: str) -> dict[tuple[str | None, str], str]:
    path = APPS / app / "locale" / "fr" / "LC_MESSAGES" / "django.po"
    entries = parse_po(path.read_text(encoding="utf-8"))
    return {e.key: e.msgstrs[0] for e in entries if e.msgid and e.msgstrs}


def test_ids_are_unique_and_every_term_is_complete() -> None:
    ids = [term["id"] for term in terms()]
    assert len(ids) == len(set(ids))
    for term in terms():
        for field in ("en", "fr", "concept", "web"):
            assert term[field], f"{term['id']}: {field} is empty"


@pytest.mark.parametrize("term", terms(), ids=lambda term: term["id"])
def test_the_web_catalog_says_the_term(term: dict[str, Any]) -> None:
    """Each place of `web` has the English msgid and the French of the glossary.

    A term with `web_fault` is a known mistake of the catalog: the test expects it, and
    fails once the catalog is fixed, so that `web_fault` is removed with the fix.
    """
    for place in term["web"]:
        assert place["msgid"] == term["en"], term["id"]
        catalog = french(place["app"])
        key = (place.get("msgctxt"), place["msgid"])
        assert key in catalog, f"{term['id']}: {key} is not in the catalog of {place['app']}"
        assert catalog[key] == term.get("web_fault", term["fr"]), term["id"]
    if "web_fault" in term:
        assert term["web_fault"] != term["fr"], term["id"]


def test_patterns_md_shows_every_term() -> None:
    """The table of design/patterns.md is the glossary: one row per term, same words."""
    rows = {
        cells[0].strip("` "): cells
        for line in PATTERNS.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `")
        for cells in [[cell.strip() for cell in line.strip("|").split("|")]]
    }
    for term in terms():
        assert term["id"] in rows, term["id"]
        cells = rows[term["id"]]
        assert cells[1] == term["en"], term["id"]
        assert cells[2].startswith(term["fr"]), term["id"]
    assert set(rows) == {term["id"] for term in terms()}
