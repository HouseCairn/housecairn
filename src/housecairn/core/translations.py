"""Translation checks: every source string has a non-fuzzy translation.

Each app has its own catalog, `<app>/locale/<language>/LC_MESSAGES/django.po`
(docs/adr/0013), and `makemessages` sends each string to the catalog of its app.
"""

import ast
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.core.management import call_command


@dataclass
class Entry:
    msgid: str = ""
    msgctxt: str | None = None
    msgid_plural: str | None = None
    msgstrs: list[str] = field(default_factory=list)
    fuzzy: bool = False

    @property
    def key(self) -> tuple[str | None, str]:
        return (self.msgctxt, self.msgid)


def parse_po(text: str) -> list[Entry]:
    """Parse the entries of a .po file (enough for checks, not a full parser)."""
    entries: list[Entry] = []
    current = Entry()
    target: str | None = None
    started = False

    def flush() -> None:
        nonlocal current, started
        if started:
            entries.append(current)
        current, started = Entry(), False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            target = None
            continue
        if line.startswith("#~"):
            continue  # obsolete entry
        if line.startswith("#,"):
            current.fuzzy = current.fuzzy or "fuzzy" in line
            continue
        if line.startswith("#"):
            continue
        keyword, _, rest = line.partition(" ")
        if line.startswith('"'):
            value = ast.literal_eval(line)
        else:
            value = ast.literal_eval(rest)
            target = keyword
            started = True
            if keyword.startswith("msgstr"):
                current.msgstrs.append("")
        if target == "msgctxt":
            current.msgctxt = (current.msgctxt or "") + value
        elif target == "msgid":
            current.msgid += value
        elif target == "msgid_plural":
            current.msgid_plural = (current.msgid_plural or "") + value
        elif target and target.startswith("msgstr"):
            current.msgstrs[-1] += value
    flush()
    return entries


def languages() -> list[str]:
    """The translated languages: every language but the source one."""
    return [code for code, _name in settings.LANGUAGES if code != settings.LANGUAGE_CODE]


def catalog_path(locale_dir: str | os.PathLike[str], language: str) -> Path:
    return Path(locale_dir) / language / "LC_MESSAGES" / "django.po"


def po_files() -> Iterator[tuple[str, Path]]:
    for locale_dir in settings.LOCALE_PATHS:
        for language in languages():
            yield language, catalog_path(locale_dir, language)


def app_catalogs(language: str) -> list[Path]:
    """The catalogs of the apps in a language, as found on disk."""
    return sorted(Path(settings.BASE_DIR).glob(f"*/locale/{language}/LC_MESSAGES/django.po"))


def app_of(path: Path) -> str:
    """The app of a catalog: `<app>/locale/<language>/LC_MESSAGES/django.po`."""
    return path.parents[3].name


def untranslated(path: Path) -> list[str]:
    """Problems in one catalog: missing, empty or fuzzy translations."""
    if not path.exists():
        return [f"{path.name}: catalog is missing"]
    problems = []
    for entry in parse_po(path.read_text(encoding="utf-8")):
        if entry.msgid == "":
            continue  # header
        if entry.fuzzy:
            problems.append(f"fuzzy: {entry.msgid!r}")
        elif not entry.msgstrs or any(s == "" for s in entry.msgstrs):
            problems.append(f"untranslated: {entry.msgid!r}")
    return problems


def docs_catalogs() -> list[Path]:
    """The catalogs of the documentation (`docs/locale/<language>/LC_MESSAGES/**/*.po`)."""
    return sorted((Path(settings.BASE_DIR).parents[1] / "docs" / "locale").rglob("*.po"))


def fuzzy_in_docs() -> list[str]:
    """Fuzzy entries (headers aside) of the documentation catalogs.

    Sphinx drops a fuzzy translation without a warning: the page, release notes
    included, then shows the English text.
    """
    root = Path(settings.BASE_DIR).parents[1] / "docs" / "locale"
    return [
        f"{path.relative_to(root) if path.is_relative_to(root) else path}: "
        f"fuzzy: {entry.msgid[:60]!r}"
        for path in docs_catalogs()
        for entry in parse_po(path.read_text(encoding="utf-8"))
        if entry.fuzzy and entry.msgid
    ]


def inconsistent(language: str) -> list[str]:
    """Strings translated differently in two catalogs: only one of them would be shown."""
    seen: dict[tuple[str | None, str], tuple[list[str], Path]] = {}
    problems = []
    for path in app_catalogs(language):
        for entry in parse_po(path.read_text(encoding="utf-8")):
            if not entry.msgid:
                continue
            first = seen.setdefault(entry.key, (entry.msgstrs, path))
            if first[0] != entry.msgstrs:
                apps = f"{app_of(first[1])} and {app_of(path)}"
                problems.append(f"translated differently in {apps}: {entry.msgid!r}")
    return problems


@contextmanager
def _restored(*paths: Path) -> Iterator[None]:
    """Put the files back as they were, and remove the catalogs created meanwhile."""
    backups = {path: path.read_bytes() if path.exists() else None for path in paths}
    base = Path(settings.BASE_DIR)
    dirs = set(base.glob("*/locale"))
    catalogs = set(base.glob("*/locale/*/LC_MESSAGES/django.po"))
    try:
        yield
    finally:
        for path, backup in backups.items():
            if backup is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(backup)
        for created in set(base.glob("*/locale/*/LC_MESSAGES/django.po")) - catalogs:
            if created not in backups:
                created.unlink()
        for created in set(base.glob("*/locale")) - dirs:
            shutil.rmtree(created)


def run_makemessages(language: str, **options: object) -> None:
    """The project's `makemessages`, run from the apps' directory as documented."""
    cwd = Path.cwd()
    os.chdir(settings.BASE_DIR)
    try:
        call_command(
            "makemessages",
            locale=[language],
            domain="django",
            ignore_patterns=["static/*"],
            verbosity=0,
            **options,
        )
    finally:
        os.chdir(cwd)


def missing_from_catalogs(language: str) -> list[str]:
    """Source strings absent from the committed catalog of their app (needs GNU gettext)."""
    if shutil.which("xgettext") is None:
        raise RuntimeError("GNU gettext (xgettext) is required to scan the sources.")
    committed = {
        path: {e.key for e in parse_po(path.read_text(encoding="utf-8"))}
        for path in app_catalogs(language)
    }
    with _restored(*committed):
        run_makemessages(language)
        scanned = {
            path: {e.key for e in parse_po(path.read_text(encoding="utf-8")) if e.msgid}
            for path in app_catalogs(language)
        }
    return [
        f"{msgid!r} (catalog of {app_of(path)})"
        for path, keys in scanned.items()
        for _ctx, msgid in sorted(keys - committed.get(path, set()), key=repr)
    ]
