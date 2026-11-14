"""Complete export of a home: a manifest, one JSON document per app, and the files (docs/adr/0048).

The archive is versioned (`FORMAT_VERSION`); docs/administration/data.md describes it and
`docs/_static/schemas/` publishes its JSON Schema. Each app that keeps data of a home
registers its tables here as `Section`s, at start-up (`AppConfig.ready`): the export reads
every concrete field of each table, so a field added later is exported without a change
here, and `homes.restore` puts the same rows back. `tests/test_export_registry.py` fails on
a table that is in no section and on a reference that no section can resolve.

Identifiers are the UUIDs of the rows, never an internal number; a person is named by the
identifier of the `people` part of `data/homes.json`, which alone holds addresses.
Amounts are text with a point (`"12.50"`), in the currency of the home (`home.currency`).
"""

import datetime
import hashlib
import json
import tempfile
import uuid
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from importlib import metadata
from pathlib import Path
from typing import IO, Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousFileOperation
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from housecairn.accounts.names import person_name
from housecairn.homes import services
from housecairn.homes.models import Home, Invitation, Membership

FORMAT = "housecairn-export"
FORMAT_VERSION = 4
MANIFEST = "manifest.json"
README_NAME = "README.txt"
DATA_FOLDER = "data"
FILES_FOLDER = "files"

# Fields never written, whatever the table: the home is the archive, and only rows that
# are not deleted are exported.
IMPLICIT_FIELDS = frozenset({"home", "deleted_at"})


@dataclass(frozen=True)
class FileSpec:
    """A file field of a section whose file goes into `files/<folder>/`.

    `thumbnail` is a field made again from the file at restore, never exported;
    `content_type` names the field that holds the type found at upload, checked against
    the content at restore; `documents` also accepts the documents of `tasks.files`.
    """

    field: str
    folder: str
    content_type: str = "content_type"
    thumbnail: str | None = None
    documents: bool = False


Rows = Callable[[Home], "models.QuerySet[Any]"]


@dataclass(frozen=True)
class Section:
    """A table of a home in the archive: one list of objects in `data/<app>.json`.

    `rows` gives the rows of the home (by default the live rows with `home=home`);
    `exclude` lists fields kept out (secrets, links to what is not exported);
    `restore` is False for a part kept for the owners' information only, with
    `why_not_restored` saying why. `on_export` and `on_restore` adjust one object;
    `on_restore` also gets the home being restored (its hemisphere, for instance).
    `on_copy` adjusts an object restored as a copy, after `on_restore`: a value that must
    be unique on the instance, besides the identifier, is drawn again there (docs/adr/0069).
    """

    name: str
    model: type[models.Model]
    rows: Rows | None = None
    exclude: tuple[str, ...] = ()
    files: tuple[FileSpec, ...] = ()
    restore: bool = True
    why_not_restored: str = ""
    on_export: Callable[[models.Model, dict[str, Any]], dict[str, Any]] | None = None
    on_restore: Callable[[dict[str, Any], Home], dict[str, Any]] | None = None
    on_copy: Callable[[dict[str, Any], Home], dict[str, Any]] | None = None
    # Fields that `on_export` may write as null although the column is not (the schema says so).
    nulls: tuple[str, ...] = ()
    order_by: tuple[str, ...] = ("created_at", "id")

    def queryset(self, home: Home) -> "models.QuerySet[Any]":
        rows = self.rows(home) if self.rows else self.model._default_manager.filter(home=home)
        relations = [m.name for m in self.many_to_many()]
        if relations:
            # One query per relation for all the rows, whatever their number.
            rows = rows.prefetch_related(*relations)
        return rows.order_by(*self.order_by)

    def file_fields(self) -> frozenset[str]:
        names = {spec.field for spec in self.files}
        names |= {spec.thumbnail for spec in self.files if spec.thumbnail}
        return frozenset(names)

    def fields(self) -> list[models.Field[Any, Any]]:
        """The concrete fields written, in the order of the model."""
        skip = IMPLICIT_FIELDS | set(self.exclude) | {s.thumbnail for s in self.files}
        return [f for f in self.model._meta.concrete_fields if f.name not in skip]

    def many_to_many(self) -> list[models.ManyToManyField[Any, Any]]:
        return [
            f
            for f in self.model._meta.many_to_many
            if f.name not in self.exclude and isinstance(f, models.ManyToManyField)
        ]


# App label -> its sections, in the order the app registered them.
SECTIONS: dict[str, list[Section]] = {}


def register(app: str, *sections: Section) -> None:
    """Add sections to `data/<app>.json`. Called from the app's `ready()`."""
    known = {s.name for sections_of in SECTIONS.values() for s in sections_of}
    for section in sections:
        if section.name in known:
            raise ValueError(f"Two sections of the export are named {section.name!r}.")
        known.add(section.name)
    SECTIONS.setdefault(app, []).extend(sections)


def all_sections() -> Iterator[tuple[str, Section]]:
    for app, sections in SECTIONS.items():
        for section in sections:
            yield app, section


def section_of_model(model: type[models.Model]) -> Section | None:
    for _app, section in all_sections():
        if section.model is model:
            return section
    return None


def person_reference(f: models.Field[Any, Any]) -> bool:
    """True for a key to an account: written as the identifier of a person of `people`."""
    return f.is_relation and f.related_model is get_user_model()


def _scalar(value: Any) -> Any:
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


# --- What is in the archive ------------------------------------------------------------


@dataclass
class ExportedFile:
    """A file of the home: its path inside the archive and where it is stored."""

    name: str
    source: Path


def stored_path(instance: models.Model, spec: FileSpec) -> Path | None:
    """The file on disk, if it is there and inside the folder of its own home."""
    stored = getattr(instance, spec.field)
    if not stored:
        return None
    storage = stored.storage
    try:
        path = Path(storage.path(stored.name)).resolve()
    except (SuspiciousFileOperation, ValueError, OSError, NotImplementedError):
        return None
    folder = (Path(storage.location) / str(instance.home_id)).resolve()  # type: ignore[attr-defined]
    if path.parent != folder or not path.is_file():
        return None
    return path


def _archive_name(instance: models.Model, spec: FileSpec, source: Path) -> str:
    return f"{FILES_FOLDER}/{spec.folder}/{instance.pk}{source.suffix.lower()}"


@dataclass
class Collected:
    """The rows of a home, closed under their references, and the files they name."""

    rows: dict[str, list[models.Model]] = field(default_factory=dict)
    ids: dict[type[models.Model], set[Any]] = field(default_factory=dict)
    files: list[ExportedFile] = field(default_factory=list)


def _required_targets(section: Section) -> list[models.ForeignKey[Any, Any]]:
    return [
        f
        for f in section.fields()
        if isinstance(f, models.ForeignKey)
        and not f.null
        and f.related_model is not Home
        and not person_reference(f)
    ]


def collect(home: Home) -> Collected:
    """Every exported row of the home.

    A row whose required reference points to a row that is not exported (deleted, say)
    is left out, and so on until nothing changes, so that the archive never names an
    object it does not hold; a reference that may be empty is written `null` instead.
    """
    result = Collected()
    for _app, section in all_sections():
        result.rows[section.name] = list(section.queryset(home))
    sections = [s for _a, s in all_sections()]
    changed = True
    while changed:
        changed = False
        result.ids = {s.model: {row.pk for row in result.rows[s.name]} for s in sections}
        for section in sections:
            required = _required_targets(section)
            if not required:
                continue
            kept = [
                row
                for row in result.rows[section.name]
                if all(
                    getattr(row, f.attname) in result.ids.get(f.related_model, set())  # type: ignore[arg-type]
                    for f in required
                )
            ]
            if len(kept) != len(result.rows[section.name]):
                result.rows[section.name] = kept
                changed = True
    return result


def _people_of(rows: Iterable[models.Model], section: Section, people: set[Any]) -> None:
    references = [f.attname for f in section.fields() if person_reference(f)]
    for row in rows:
        for name in references:
            pk = getattr(row, name)
            if pk is not None:
                people.add(pk)


def serialize(section: Section, row: models.Model, collected: Collected) -> dict[str, Any]:
    """One object of a section, from every concrete field of its row."""
    data: dict[str, Any] = {}
    file_specs = {spec.field: spec for spec in section.files}
    for f in section.fields():
        if f.name in file_specs:
            spec = file_specs[f.name]
            source = stored_path(row, spec)
            if source is None:
                data[f.name] = None
            else:
                name = _archive_name(row, spec, source)
                collected.files.append(ExportedFile(name=name, source=source))
                data[f.name] = name
            continue
        if isinstance(f, models.FileField):  # pragma: no cover - the registry test forbids it
            raise ValueError(f"{section.name}.{f.name}: a file field needs a FileSpec.")
        raw = getattr(row, f.attname)
        if f.is_relation and raw is not None and not person_reference(f):
            target = f.related_model
            if target is not Home and raw not in collected.ids.get(target, set()):  # type: ignore[arg-type]
                raw = None  # an optional link to a row that is not exported
        data[f.name] = _scalar(raw)
    for m2m in section.many_to_many():
        exported = collected.ids.get(m2m.related_model, set())  # type: ignore[arg-type]
        data[m2m.name] = sorted(
            str(target.pk) for target in getattr(row, m2m.name).all() if target.pk in exported
        )
    if section.on_export is not None:
        data = section.on_export(row, data)
    return data


def _home(home: Home) -> dict[str, Any]:
    return {
        "id": str(home.id),
        "name": home.name,
        "currency": home.currency,
        "hemisphere": home.hemisphere,
        "required_provider": home.required_provider,
        "archived_at": home.archived_at.isoformat() if home.archived_at else None,
        "created_at": home.created_at.isoformat(),
        "updated_at": home.updated_at.isoformat(),
    }


def documents(home: Home) -> tuple[dict[str, dict[str, Any]], list[ExportedFile]]:
    """The JSON document of each app (`data/<app>.json`) and the files they name."""
    collected = collect(home)
    out: dict[str, dict[str, Any]] = {}
    people: set[Any] = set()
    for app, sections in SECTIONS.items():
        body: dict[str, Any] = {}
        for section in sections:
            rows = collected.rows[section.name]
            _people_of(rows, section, people)
            body[section.name] = [serialize(section, row, collected) for row in rows]
        out[app] = {"app": app, "sections": body}
    homes_doc = out.setdefault("homes", {"app": "homes", "sections": {}})
    homes_doc["home"] = _home(home)
    users = get_user_model().objects.in_bulk(people)
    homes_doc["people"] = [
        {"id": str(user.pk), "email": user.email, "name": person_name(user)}
        for user in sorted(users.values(), key=lambda u: str(u.pk))
    ]
    return out, collected.files


def _app_version() -> str:
    try:
        return metadata.version("housecairn")
    except metadata.PackageNotFoundError:  # pragma: no cover - run from a bare checkout
        return "dev"


README = """HouseCairn export of one home
==============================

manifest.json   format "{format}", version {version}: what the archive holds, with the
                SHA-256 digest and the size of every other file.
data/<app>.json the data of the home, one JSON document per part of the application.
files/          the files of the home, at the paths the documents give.

The format is described in the documentation: Administration > Export and deletion.
This archive holds the home it was made for and nothing of any other home. A HouseCairn
instance restores it as a new home: Homes > Restore a home.
"""


class _Hashing:
    """A writable stream that counts and hashes what goes through it."""

    def __init__(self, target: IO[bytes]) -> None:
        self.target = target
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> None:
        self.digest.update(data)
        self.size += len(data)
        self.target.write(data)


def _write(archive: zipfile.ZipFile, name: str, data: bytes, entries: dict[str, Any]) -> None:
    archive.writestr(name, data)
    entries[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _write_file(archive: zipfile.ZipFile, name: str, source: Path, entries: dict[str, Any]) -> None:
    if name.startswith(("/", "..")) or ".." in Path(name).parts:
        raise ValueError("An exported file may not leave the archive.")
    with source.open("rb") as handle, archive.open(name, "w", force_zip64=True) as out:
        hashing = _Hashing(out)
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hashing.write(chunk)
    entries[name] = {"sha256": hashing.digest.hexdigest(), "size": hashing.size}


def write_archive(home: Home, target: IO[bytes]) -> dict[str, Any]:
    """Write the archive of `home` into `target`; return its manifest."""
    docs, files = documents(home)
    entries: dict[str, Any] = {}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        _write(
            archive,
            README_NAME,
            README.format(format=FORMAT, version=FORMAT_VERSION).encode(),
            entries,
        )
        for app, doc in docs.items():
            raw = json.dumps(doc, indent=2, ensure_ascii=False).encode()
            _write(archive, f"{DATA_FOLDER}/{app}.json", raw, entries)
        for f in files:
            _write_file(archive, f.name, f.source, entries)
        manifest = {
            "format": FORMAT,
            "version": FORMAT_VERSION,
            "application_version": _app_version(),
            "exported_at": timezone.now().isoformat(),
            "home": str(home.id),
            "apps": list(docs),
            "entries": entries,
        }
        archive.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def archive_name(home: Home) -> str:
    stamp = timezone.now().strftime("%Y%m%d")
    return f"housecairn-{slugify(home.name) or 'home'}-{stamp}.zip"


def build_archive(home: Home) -> tuple[IO[bytes], str]:
    """The archive of a home, as a file object positioned at its start, and a file name."""
    spool = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)  # noqa: SIM115
    write_archive(home, spool)
    spool.seek(0)
    return spool, archive_name(home)


def estimated_size(home: Home) -> int:
    """The bytes of the files of the home: what decides a background export."""
    from housecairn.homes import quotas

    return quotas.usage(home.id).bytes


def media_directory(home: Home) -> Path:
    """Where the files of a home are kept (used by the lots that store files)."""
    return Path(settings.MEDIA_ROOT) / "homes" / str(home.id)  # see homes.files for the others


def check_owner(actor: Any, home: Home) -> None:
    from django.core.exceptions import PermissionDenied

    if not services.is_owner(actor, home):
        raise PermissionDenied


def export_home(actor: Any, home: Home) -> tuple[IO[bytes], str]:
    """The archive of the home for one of its owners, recorded in the audit log."""
    from housecairn.audit import services as audit

    check_owner(actor, home)
    archive, name = build_archive(home)
    audit.record("home.exported", actor=actor, home_id=home.id, format=f"v{FORMAT_VERSION}")
    return archive, name


# --- The sections of this app ----------------------------------------------------------


def _members(home: Home) -> "models.QuerySet[Membership]":
    return services.members(home)


def _invitations(home: Home) -> "models.QuerySet[Invitation]":
    return services.pending_invitations(home)


register(
    "homes",
    Section(
        "members",
        Membership,
        rows=_members,
        restore=False,
        why_not_restored="memberships are made again from the people the restore matches",
    ),
    Section(
        "pending_invitations",
        Invitation,
        rows=_invitations,
        # The digest is the secret of the link; who invited and who accepted are accounts.
        exclude=("digest", "invited_by", "accepted_by", "accepted_at", "revoked_at"),
        restore=False,
        why_not_restored="an invitation is a link sent by e-mail, which only its secret opens",
    ),
)
