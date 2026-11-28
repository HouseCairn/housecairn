"""A real archive of every release restores on an empty instance (docs/administration/data.md).

The archives of `tests/fixtures/archives/` were each made by the export of their own tag,
on the demo home (`scripts/archive_fixtures.py`), not by `main` with its manifest
rewritten: they lack what their release did not have yet. Each is restored on a database
that holds nothing but the account restoring it, and the rows of every section are
counted; the fields its release lacked hold what the migration that added them gives.
"""

import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from housecairn.accounts.models import User
from housecairn.homes import archive_versions, exports, restore
from housecairn.homes.management.commands.export_schema import PUBLISHED
from housecairn.homes.models import Home
from housecairn.rooms.models import Item
from housecairn.stock.models import Article
from housecairn.tasks.models import Contractor, MaterialDraw, Occurrence
from tests.test_restore_security import entries, rebuild

pytest_plugins = ("tests.rooms_fixtures",)
pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[1]
ARCHIVES = ROOT / "tests" / "fixtures" / "archives"

# The rows of the demo home of each release, with what `scripts/archive_fixtures.py` adds.
_BEFORE_4 = {
    "rooms": 6,
    "items": 1,
    "tasks": 6,
    "occurrences": 7,
    "stock_items": 1,
    "projects": 2,
    "project_budget_lines": 2,
}
_FROM_4 = {
    "rooms": 6,
    "items": 1,
    "contractors": 2,
    "tasks": 8,
    "task_materials": 1,
    "occurrences": 10,
    "stock_draws": 1,
    "stock_items": 1,
    "stock_movements": 2,
    "projects": 2,
    "project_budget_lines": 4,
    "project_quotes": 2,
    "project_links": 2,
    "project_tasks": 2,
    "project_envelopes": 1,
}
# Tag -> version of the format, rows by section once restored.
RELEASES: dict[str, tuple[int, dict[str, int]]] = {
    # Format 2: the provider of the contract of the item, a text, becomes a contractor.
    "v0.1.0": (2, {**_BEFORE_4, "contractors": 1}),
    "v0.2.0": (3, _BEFORE_4),
    "v0.3.0": (3, _BEFORE_4),
    "v0.3.1": (3, _BEFORE_4),
    "v0.3.2": (3, _BEFORE_4),
    "v0.4.0": (4, _FROM_4),
    "v0.4.1": (4, _FROM_4),
    "v0.5.0": (4, _FROM_4),
    "v0.5.1": (4, _FROM_4),
    "v0.5.2": (4, _FROM_4),
}


def _archive(tag: str) -> bytes:
    return (ARCHIVES / f"{tag}.zip").read_bytes()


def _manifest(data: bytes) -> dict[str, Any]:
    return json.loads(zipfile.ZipFile(io.BytesIO(data)).read("manifest.json"))


@pytest.fixture
def owner(make_user: Any) -> User:
    # The address of the demo account of every archive: the restore matches it.
    return make_user("owner@example.com")


def test_every_published_release_has_its_archive() -> None:
    from scripts import archive_fixtures

    assert set(RELEASES) == set(archive_fixtures.TAGS)
    assert sorted(p.stem for p in ARCHIVES.glob("*.zip")) == sorted(RELEASES)


@pytest.mark.parametrize("tag", sorted(RELEASES))
def test_the_archive_of_a_release_restores_on_an_empty_instance(
    tag: str, owner: User, media_root: Path
) -> None:
    version, expected = RELEASES[tag]
    data = _archive(tag)
    manifest = _manifest(data)
    # Made by that release itself, not by main with its manifest rewritten.
    assert (manifest["version"], manifest["application_version"]) == (version, tag[1:])
    assert not Home.all_objects.exists()

    result = restore.restore_bytes(owner, data)

    home = result.home
    assert home.name == "Example house"
    assert home.archived_at is None
    counted = {}
    for _app, section in exports.all_sections():
        if section.restore:
            rows = section.model._base_manager.filter(home=home).count()
            if rows:
                counted[section.name] = rows
    assert counted == expected
    assert result.counts == expected
    assert list(home.memberships.values_list("user__email", "role")) == [
        ("owner@example.com", "owner")
    ]


@pytest.mark.parametrize("tag", sorted(RELEASES))
def test_the_fields_a_release_lacked_hold_the_value_of_their_migration(
    tag: str, owner: User, media_root: Path
) -> None:
    version, _expected = RELEASES[tag]
    home = restore.restore_bytes(owner, _archive(tag)).home

    item = Item.objects.get(home=home)
    assert item.ean == ""
    assert item.warranty_duration_unit in {"year", "month"}
    assert isinstance(Article.objects.get(home=home).label_attribution, dict)
    if version == 2:
        assert item.name == "Boiler"
        assert item.contract_contractor is not None
        assert item.contract_contractor.name == "Example Heating Services"
        assert Contractor.objects.get(home=home).trade == "other"
        assert Article.objects.get(home=home).unit_price is None
    # The done occurrence of a task with a plan keeps that plan (tasks 0014).
    done = Occurrence.objects.get(home=home, status="done", task__title="Boiler yearly service")
    assert (done.planned_labour, done.planned_material) == (Decimal("80.00"), Decimal("25.00"))
    assert not Occurrence.objects.filter(home=home, status="planned").exclude(planned_labour=None)
    if version == 4:
        # A draw of before gets the unit price of its stock item (tasks 0013) ...
        draw = MaterialDraw.objects.get(home=home)
        assert draw.unit_price == draw.article.unit_price == Decimal("7.90")
        # ... and a task whose materials the stock prices keeps its labour, not its budget.
        priced = Occurrence.objects.get(home=home, status="done", task=draw.occurrence.task)
        assert (priced.planned_labour, priced.planned_material) == (Decimal("15.00"), None)


@pytest.mark.parametrize("tag", ["v0.4.0", "v0.4.1", "v0.5.0", "v0.5.1", "v0.5.2"])
def test_an_archive_of_format_4_of_before_validates_against_the_published_schema(
    tag: str,
) -> None:
    published = json.loads((ROOT / PUBLISHED).read_text(encoding="utf-8"))
    archive = zipfile.ZipFile(io.BytesIO(_archive(tag)))
    manifest = json.loads(archive.read("manifest.json"))
    jsonschema.validate(manifest, {**published["$defs"]["manifest"], "$defs": published["$defs"]})
    for app in manifest["apps"]:
        document = json.loads(archive.read(f"data/{app}.json"))
        jsonschema.validate(document, published["$defs"]["documents"][app])


def _without(tag: str, section: str, field: str) -> bytes:
    files = entries(_archive(tag))
    app = next(a for a, kept in exports.SECTIONS.items() if section in {s.name for s in kept})
    document = json.loads(files[f"data/{app}.json"])
    for row in document["sections"][section]:
        row.pop(field)
    files[f"data/{app}.json"] = json.dumps(document).encode()
    return rebuild(files)


@pytest.mark.parametrize(
    ("section", "field"),
    [("items", "name"), ("tasks", "title"), ("items", "contract_contractor"), ("rooms", "kind")],
)
def test_a_field_its_version_has_is_refused_when_missing(
    section: str, field: str, owner: User, media_root: Path
) -> None:
    assert field not in archive_versions.optional(exports.FORMAT_VERSION, section)
    with pytest.raises(restore.ArchiveRefusedError, match=rf"{section}, object 1, {field}\."):
        restore.restore_bytes(owner, _without("v0.5.2", section, field))
    assert not Home.all_objects.exists()


def test_a_many_to_many_link_its_version_has_is_refused_when_missing(
    owner: User, media_root: Path
) -> None:
    with pytest.raises(restore.ArchiveRefusedError, match=r"stock_items, object 1, rooms\."):
        restore.restore_bytes(owner, _without("v0.5.2", "stock_items", "rooms"))


def test_a_field_added_in_the_current_version_may_be_missing(owner: User, media_root: Path) -> None:
    assert "ean" in archive_versions.optional(exports.FORMAT_VERSION, "items")
    home = restore.restore_bytes(owner, _without("v0.5.2", "items", "ean")).home
    assert Item.objects.get(home=home).ean == ""


def test_every_version_lacks_at_least_what_a_later_one_lacks() -> None:
    for version in (2, 3):
        for section, fields in archive_versions.ADDED[version + 1].items():
            assert set(fields) <= archive_versions.optional(version, section)
    assert set(archive_versions.ADDED) == restore.SUPPORTED_VERSIONS


def _hostile(tag: str, section: str, field: str, value: Any) -> bytes:
    """The archive of `tag` with `field` of the first row of `section` set to `value`."""
    files = entries(_archive(tag))
    app = next(a for a, kept in exports.SECTIONS.items() if section in {s.name for s in kept})
    document = json.loads(files[f"data/{app}.json"])
    document["sections"][section][0][field] = value
    files[f"data/{app}.json"] = json.dumps(document).encode()
    return rebuild(files)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        # Read by the fills of a version 4 archive of 0.5.2, whose draws lack their unit
        # price and whose occurrences lack their plan: an identifier that is not a text is
        # refused like any value that is not valid, never a crash of the restore.
        ("stock_items", "id", [1]),
        ("stock_draws", "article", {"a": 1}),
        ("occurrences", "task", [1]),
        ("project_tasks", "task", [1]),
        ("tasks", "id", {"x": 1}),
        ("task_materials", "task", [1]),
        ("task_materials", "article", {"a": 1}),
    ],
)
def test_an_identifier_that_is_not_a_text_is_refused_where_fields_are_filled(
    section: str, field: str, value: Any, owner: User, media_root: Path
) -> None:
    with pytest.raises(restore.ArchiveRefusedError):
        restore.restore_bytes(owner, _hostile("v0.5.2", section, field, value))
    assert not Home.all_objects.exists()
