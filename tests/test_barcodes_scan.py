"""The Scan button and the vendored ZXing library (docs/adr/0083)."""

import hashlib
import re
from pathlib import Path

import pytest
from django.test import Client

from housecairn.accounts.models import User
from housecairn.homes.models import Home
from housecairn.rooms.models import Room

pytest_plugins = ("tests.rooms_fixtures",)

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "src/housecairn/static/vendor"
ZXING = VENDOR / "zxing/zxing-library.min.js"
ZXING_SHA256 = "3ede94153fb0c5b67a12d7adff6decd827c2b22714fdc6faecf27a8f20937ea6"
# The first words of each licence text, and its SPDX identifier.
LICENSE_HEADS = {
    "Apache License\n                           Version 2.0": "Apache-2.0",
    "Zero-Clause BSD": "0BSD",
}


def _allowed_licenses() -> set[str]:
    """The licences of the dependency review (docs/adr/0014): the same list for vendored files."""
    workflow = (ROOT / ".github/workflows/dependency-review.yml").read_text(encoding="utf-8")
    lines = workflow.split("allow-licenses: >-\n", 1)[1].splitlines()
    block = []
    for line in lines:
        if not line.strip() or line.strip().startswith("#") or ":" in line:
            break
        block.append(line)
    return {name.strip() for name in " ".join(block).split(",") if name.strip()}


def test_vendored_zxing_matches_its_recorded_hash() -> None:
    assert hashlib.sha256(ZXING.read_bytes()).hexdigest() == ZXING_SHA256
    assert ZXING_SHA256 in (ZXING.parent / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("folder", sorted(p for p in VENDOR.iterdir() if p.is_dir()))
def test_vendored_files_have_an_allowed_license(folder: Path) -> None:
    text = (folder / "LICENSE").read_text(encoding="utf-8").strip()
    found = [spdx for head, spdx in LICENSE_HEADS.items() if text.startswith(head)]
    assert found, f"Unknown licence in {folder.name}: add it to LICENSE_HEADS after an ADR."
    assert found[0] in _allowed_licenses()


def test_the_script_loads_the_library_it_was_given_with_its_integrity() -> None:
    snippet = (ROOT / "src/housecairn/templates/barcodes/_scan.html").read_text(encoding="utf-8")
    readme = (ZXING.parent / "README.md").read_text(encoding="utf-8")
    integrity = re.search(r"sha384-[A-Za-z0-9+/=]+", snippet)
    assert integrity is not None
    assert integrity.group(0) in readme
    script = (ROOT / "src/housecairn/static/js/barcode-scan.js").read_text(encoding="utf-8")
    assert "vendor/zxing/zxing-library.min.js" in script
    assert "data-zxing-src" not in snippet


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["item", "article"])
def test_the_forms_offer_the_scan_and_keep_the_field_typed_by_hand(
    signed_in: Client, alice: User, alice_home: Home, owner_room: Room, page: str
) -> None:
    url = (
        f"/h/{alice_home.pk}/rooms/{owner_room.pk}/items/new/"
        if page == "item"
        else f"/h/{alice_home.pk}/stock/new/"
    )
    response = signed_in.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'name="ean"' in html
    assert "data-barcode-field" in html
    assert 'inputmode="numeric"' in html
    assert "js/barcode-scan.js" in html
