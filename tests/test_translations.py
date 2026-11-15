import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import Client
from django.urls import reverse
from django.utils import translation

from housecairn.core import translations
from housecairn.core.management.commands import makemessages

SAMPLE = """\
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

#: a.py:1
msgid "Done"
msgstr "Fait"

#, fuzzy
msgid "Postponed"
msgstr "Reporté"

msgctxt "verb"
msgid "Skip"
msgstr ""

msgid "One task"
msgid_plural "%d tasks"
msgstr[0] "Une tâche"
msgstr[1] ""

msgid ""
"Long "
"text"
msgstr ""
"Texte "
"long"

#~ msgid "Old"
#~ msgstr ""
"""


@pytest.fixture
def source_copy(tmp_path: Path, settings: Any) -> Iterator[Path]:
    """A copy of the application's code and catalogs, for the tests that run `makemessages`.

    `makemessages` rewrites the catalogs it works on: on the source tree, two xdist
    workers would rewrite the same `django.po` at once, and a test would edit a tracked
    file. The copy gives each test its own catalogs; the source tree comes out unchanged.
    """
    base = Path(settings.BASE_DIR)
    locale_dirs = [Path(p) for p in settings.LOCALE_PATHS]
    tracked = {path: path.read_bytes() for d in locale_dirs for path in d.rglob("*.po")}
    copy = tmp_path / base.name
    shutil.copytree(base, copy, ignore=shutil.ignore_patterns("__pycache__", "static", "*.mo"))
    settings.BASE_DIR = copy
    settings.LOCALE_PATHS = [copy / d.relative_to(base) for d in locale_dirs]
    yield copy
    assert {path: path.read_bytes() for path in tracked} == tracked
    assert not [path for d in locale_dirs for path in d.rglob("*.pot")]


def test_parser_and_problems(tmp_path: Path) -> None:
    entries = translations.parse_po(SAMPLE)
    assert [e.msgid for e in entries] == ["", "Done", "Postponed", "Skip", "One task", "Long text"]
    assert entries[3].msgctxt == "verb"
    assert entries[5].msgstrs == ["Texte long"]
    path = tmp_path / "django.po"
    path.write_text(SAMPLE, encoding="utf-8")
    assert translations.untranslated(path) == [
        "fuzzy: 'Postponed'",
        "untranslated: 'Skip'",
        "untranslated: 'One task'",
    ]


def test_shipped_documentation_catalogs_have_no_fuzzy_entry() -> None:
    """Sphinx ignores a fuzzy entry silently: a release note would stay in English."""
    assert len(translations.docs_catalogs()) > 50
    assert translations.fuzzy_in_docs() == []


def test_a_fuzzy_documentation_entry_fails_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "note.po"
    path.write_text(
        '#, fuzzy\nmsgid ""\nmsgstr ""\n\n#, fuzzy\nmsgid "A note"\nmsgstr "Une note"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(translations, "docs_catalogs", lambda: [path])
    monkeypatch.setattr(translations, "po_files", lambda: iter([]))
    monkeypatch.setattr(translations, "inconsistent", lambda _language: [])
    with pytest.raises(CommandError, match=r"docs: .*fuzzy: 'A note'") as raised:
        call_command("check_translations")
    assert "''" not in str(raised.value)  # the header is not reported


def test_missing_catalog_is_reported(tmp_path: Path) -> None:
    assert translations.untranslated(tmp_path / "none.po") == ["none.po: catalog is missing"]


def test_shipped_catalogs_are_complete() -> None:
    call_command("check_translations")


def test_command_fails_on_problems(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "django.po"
    path.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(translations, "po_files", lambda: iter([("fr", path)]))
    with pytest.raises(CommandError, match="untranslated: 'Skip'"):
        call_command("check_translations")


@pytest.mark.skipif(shutil.which("xgettext") is None, reason="needs GNU gettext")
def test_every_source_string_is_in_the_catalog(source_copy: Path) -> None:
    assert all(path.is_relative_to(source_copy) for _lang, path in translations.po_files())
    before = {p: p.read_bytes() for _lang, p in translations.po_files()}
    call_command("check_translations", "--sources")
    assert {p: p.read_bytes() for _lang, p in translations.po_files()} == before


def test_sources_scan_needs_gettext(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="gettext"):
        translations.missing_from_catalogs("fr")


def test_catalogs_have_no_dates_or_locations() -> None:
    """Two branches that add a string must merge without a conflict (docs/adr/0013)."""
    catalogs = [path for _lang, path in translations.po_files()]
    catalogs += (Path(settings.BASE_DIR).parents[1] / "docs" / "locale").rglob("*.po")
    assert len(catalogs) > 50  # the application's and the documentation's
    for path in catalogs:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^#:|POT-Creation-Date", text, re.MULTILINE), path


@pytest.mark.skipif(shutil.which("xgettext") is None, reason="needs GNU gettext")
def test_regenerating_the_catalogs_changes_nothing(source_copy: Path) -> None:
    for language in translations.languages():
        catalogs = translations.app_catalogs(language)
        assert catalogs
        assert all(path.is_relative_to(source_copy) for path in catalogs)
        before = {path: path.read_bytes() for path in catalogs}
        with translations._restored(*catalogs):
            translations.run_makemessages(language)
            assert translations.app_catalogs(language) == catalogs
            assert {path: path.read_bytes() for path in catalogs} == before


def test_strings_go_to_the_catalog_of_their_app() -> None:
    base = Path(settings.BASE_DIR)
    assert makemessages.owner(base / "tasks" / "views.py") == "tasks"
    assert makemessages.owner(base / "tasks" / "forms" / "task.py") == "tasks"
    assert makemessages.owner(base / "templates" / "tasks" / "task_detail.html") == "tasks"
    # The shell, the shared templates and the project modules: the catalog of core.
    assert makemessages.owner(base / "templates" / "base.html") == "core"
    assert makemessages.owner(base / "templates" / "shell" / "nav.html") == "core"
    assert makemessages.owner(base / "templates" / "account" / "login.html") == "core"
    assert makemessages.owner(base / "settings.py") == "core"


def test_each_app_has_its_catalog_and_the_single_one_is_gone() -> None:
    """One catalog per app (docs/adr/0013), the shared strings first, in core."""
    base = Path(settings.BASE_DIR)
    assert not (base / "locale").exists()
    assert [Path(p) for p in settings.LOCALE_PATHS] == [
        path.parents[2] for path in sorted(translations.app_catalogs("fr"), key=_core_first)
    ]
    assert translations.app_of(translations.po_files().__next__()[1]) == "core"
    assert len(settings.LOCALE_PATHS) > 10
    assert translations.inconsistent("fr") == []


def _core_first(path: Path) -> tuple[bool, Path]:
    return (translations.app_of(path) != "core", path.parents[2])


def test_a_string_translated_two_ways_is_reported(tmp_path: Path, settings: Any) -> None:
    settings.BASE_DIR = tmp_path
    for app, msgstr in (("rooms", "Pièce"), ("tasks", "Salle"), ("stock", "Pièce")):
        path = translations.catalog_path(tmp_path / app / "locale", "fr")
        path.parent.mkdir(parents=True)
        path.write_text(f'msgid ""\nmsgstr ""\n\nmsgid "Room"\nmsgstr "{msgstr}"\n')
    assert translations.inconsistent("fr") == ["translated differently in rooms and tasks: 'Room'"]


@pytest.mark.skipif(shutil.which("xgettext") is None, reason="needs GNU gettext")
def test_a_new_string_changes_only_the_catalog_of_its_app(source_copy: Path) -> None:
    """Two pull requests that add strings to two apps touch two different catalogs."""
    with (source_copy / "templates" / "rooms" / "room_detail.html").open("a") as template:
        template.write('{% load i18n %}{% translate "A string only rooms has" %}\n')
    with (source_copy / "budget" / "apps.py").open("a") as module:
        module.write("\nfrom django.utils.translation import gettext_lazy\n")
        module.write('NEW = gettext_lazy("A string only budget has")\n')
    catalogs = translations.app_catalogs("fr")
    before = {path: path.read_bytes() for path in catalogs}
    with pytest.raises(CommandError) as raised:
        call_command("check_translations", "--sources")
    assert "'A string only rooms has' (catalog of rooms)" in str(raised.value)
    assert "'A string only budget has' (catalog of budget)" in str(raised.value)
    with translations._restored(*catalogs):
        translations.run_makemessages("fr")
        changed = {translations.app_of(p) for p in catalogs if p.read_bytes() != before[p]}
        assert changed == {"rooms", "budget"}
        rooms = translations.catalog_path(source_copy / "rooms" / "locale", "fr")
        assert 'msgid "A string only rooms has"\nmsgstr ""' in rooms.read_text(encoding="utf-8")


@pytest.mark.django_db
def test_french_is_served(client: Client) -> None:
    """The compiled catalog is there: `compilemessages` ran, in CI as in the image."""
    response = client.get(reverse("account_login"), HTTP_ACCEPT_LANGUAGE="fr")
    assert response.headers["Content-Language"] == "fr"
    assert '<html lang="fr">' in response.text
    assert "Se connecter" in response.text
    with translation.override("fr"):
        assert translation.gettext("Sign in") == "Se connecter"
