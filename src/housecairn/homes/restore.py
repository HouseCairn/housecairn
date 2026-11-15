"""Restoring the archive of a home as a new home (docs/adr/0048).

The archive is read as hostile until proven otherwise, in this order, and nothing is
written before every check has passed:

1. the zip itself (`read_archive`): only the names the format allows, no link, no
   encrypted entry, no duplicate, a cap on the number of entries and on the size once
   unpacked, read from the headers before anything is unpacked, and every entry read
   back no larger than its header says;
2. the manifest: the format and a version this instance knows, and the SHA-256 digest
   and size of every other entry, which must all be listed and all match;
3. each object: every field parsed by its type and checked by the validators of its
   model, every reference to an object that the archive itself holds (never to a row of
   another home), and no identifier already used on this instance;
4. each file: decoded again, its type the one the row says, as at upload.

Then the home, its rows and its files are written in one transaction; a file already
written is removed if anything fails. Identifiers are kept. People are matched by e-mail
address on existing accounts, never created: from the web, only the person restoring is
matched; from `manage.py restore_home`, run by the administrator of the instance, every
person of the archive with an account (docs/adr/0048).

A restore *as a copy* (docs/adr/0069) goes through the same checks, then gives every
object of the archive a new identifier, the links between them following, so that it can
sit next to the home it was made from. Only the person restoring is matched, and is its
only member; when that home is on this instance, only one of its owners may copy it.
"""

import contextlib
import datetime
import hashlib
import io
import json
import re
import stat
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import IO, Any

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from housecairn.core.text import without_control_characters
from housecairn.homes import exports, quotas, services
from housecairn.homes.exports import FileSpec, Section
from housecairn.homes.models import Home, Membership, Role
from housecairn.sync import clock
from housecairn.sync import rows as sync_rows

SUPPORTED_VERSIONS = frozenset({2, 3, 4})
DOCUMENT_MAX_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 200_000
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# Matched with `fullmatch`: `$` alone would accept a name ending with a newline.
_NAME = re.compile(
    rf"(?:{re.escape(exports.MANIFEST)}|{re.escape(exports.README_NAME)}"
    rf"|{exports.DATA_FOLDER}/[a-z_]{{1,40}}\.json"
    rf"|{exports.FILES_FOLDER}/[a-z_]{{1,40}}/{_UUID}(?:\.[a-z0-9]{{1,8}})?)"
)


class ArchiveRefusedError(ValidationError):
    """The archive is not restored, and nothing was written; the message says why."""


class CopyNotAllowedError(PermissionDenied):
    """The home of the archive is on this instance and the person is not one of its owners."""

    def __init__(self) -> None:
        self.message = _(
            "The home of this archive is on this instance: only one of its owners may copy it."
        )
        super().__init__(self.message)


def _refuse(message: str, **params: Any) -> ArchiveRefusedError:
    return ArchiveRefusedError(message % params if params else message)


def _file_limit() -> int:
    from housecairn.rooms import conf

    return conf.photo_max_bytes()


def _document_limit() -> int:
    """The largest data file of an archive; smaller on a demonstration instance (ADR 0071)."""
    from housecairn.demo import mode

    return mode.DOCUMENT_MAX_BYTES if mode.is_on() else DOCUMENT_MAX_BYTES


def max_unpacked_bytes() -> int:
    """What an archive may hold once unpacked: the file quota of a home, plus the data."""
    return int(quotas.limits().bytes) + _document_limit()


# --- 1 and 2: the zip and its manifest --------------------------------------------------


@dataclass
class Archive:
    manifest: dict[str, Any]
    documents: dict[str, dict[str, Any]]
    zipped: zipfile.ZipFile

    def read_file(self, name: str) -> bytes:
        """An entry already checked against the manifest, read again and checked again."""
        expected = self.manifest["entries"][name]
        data = _read_entry(self.zipped, self.zipped.getinfo(name))
        if hashlib.sha256(data).hexdigest() != expected["sha256"]:
            raise _refuse(_("The archive was changed after it was made: %(name)s."), name=name)
        return data


def _read_entry(zipped: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    limit = info.file_size
    try:
        with zipped.open(info) as handle:
            data = handle.read(limit + 1)
    except (zipfile.BadZipFile, EOFError, OSError, NotImplementedError):
        # A wrong CRC, a size that lies, a method zipfile does not know: changed or broken.
        raise _refuse(
            _("The archive was changed after it was made: %(name)s."), name=info.filename
        ) from None
    if len(data) > limit:  # pragma: no cover - zipfile stops at the declared size itself
        raise _refuse(
            _("An entry of the archive is larger than it says: %(name)s."), name=info.filename
        )
    return data


def _check_entries(zipped: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = zipped.infolist()
    if len(infos) > MAX_ENTRIES:
        raise _refuse(_("The archive holds too many files."))
    seen: set[str] = set()
    total = 0
    for info in infos:
        name = info.filename
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise _refuse(_("The archive holds a symbolic link: %(name)s."), name=name)
        if info.flag_bits & 0x1:
            raise _refuse(_("The archive holds an encrypted file: %(name)s."), name=name)
        if not _NAME.fullmatch(name) or "\\" in name or "\x00" in name:
            raise _refuse(
                _("The archive holds a file outside of what the format allows: %(name)s."),
                name=name[:120],
            )
        if name.startswith(f"{exports.FILES_FOLDER}/") and info.file_size > _file_limit():
            raise _refuse(
                _("A file of the archive is larger than this instance accepts: %(name)s."),
                name=name,
            )
        if name in seen:
            raise _refuse(_("The archive holds the same file twice: %(name)s."), name=name)
        seen.add(name)
        total += info.file_size
    if total > max_unpacked_bytes():
        raise _refuse(_("The archive is too large once unpacked for this instance."))
    return infos


# What `json.loads` raises on a hostile document: bytes that are not UTF-8, text that is
# not JSON, nesting deeper than the stack allows (RecursionError), an integer longer than
# Python converts (ValueError, more than 4300 digits by default).
_NOT_JSON = (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError)


def _json(zipped: zipfile.ZipFile, info: zipfile.ZipInfo) -> Any:
    if info.file_size > _document_limit():
        raise _refuse(_("A data file of the archive is too large: %(name)s."), name=info.filename)
    try:
        return json.loads(_read_entry(zipped, info).decode("utf-8"))
    except _NOT_JSON:
        raise _refuse(
            _("A data file of the archive is not valid JSON: %(name)s."), name=info.filename
        ) from None


def read_archive(source: IO[bytes]) -> Archive:
    """Open and check the zip and its manifest; ArchiveRefusedError when anything is wrong."""
    try:
        zipped = zipfile.ZipFile(source)
        infos = _check_entries(zipped)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError):
        raise _refuse(_("This file is not a HouseCairn archive (not a valid zip).")) from None
    by_name = {info.filename: info for info in infos}
    if exports.MANIFEST not in by_name:
        raise _refuse(_("This file is not a HouseCairn archive: it has no manifest."))
    try:
        manifest = _json(zipped, by_name[exports.MANIFEST])
    except zipfile.BadZipFile:
        raise _refuse(_("This file is not a HouseCairn archive (not a valid zip).")) from None
    if not isinstance(manifest, dict) or manifest.get("format") != exports.FORMAT:
        raise _refuse(_("This file is not a HouseCairn archive: its manifest is not one."))
    version = manifest.get("version")
    if version not in SUPPORTED_VERSIONS:
        raise _refuse(
            _(
                "This archive is in version %(version)s of the format, which this instance "
                "cannot restore (it restores version %(supported)s)."
            ),
            version=str(version)[:20],
            supported=", ".join(str(v) for v in sorted(SUPPORTED_VERSIONS)),
        )
    entries = manifest.get("entries")
    apps = manifest.get("apps")
    if not isinstance(entries, dict) or not isinstance(apps, list):
        raise _refuse(_("The manifest of the archive is incomplete."))
    listed = set(entries)
    present = set(by_name) - {exports.MANIFEST}
    if listed != present:
        raise _refuse(
            _("The archive was changed after it was made: its files are not those of its manifest.")
        )
    for name, entry in entries.items():
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get("sha256"), str)
            and entry.get("size") == by_name[name].file_size
        ):
            raise _refuse(_("The archive was changed after it was made: %(name)s."), name=name)
    known = set(exports.SECTIONS)
    documents: dict[str, dict[str, Any]] = {}
    for app in apps:
        if not isinstance(app, str) or app not in known:
            raise _refuse(
                _(
                    "The archive holds data this instance does not know (%(app)s): "
                    "update HouseCairn first."
                ),
                app=str(app)[:40],
            )
        name = f"{exports.DATA_FOLDER}/{app}.json"
        if name not in by_name:
            raise _refuse(_("The manifest of the archive is incomplete."))
        try:
            raw = _read_entry(zipped, by_name[name])
        except zipfile.BadZipFile:
            raise _refuse(
                _("The archive was changed after it was made: %(name)s."), name=name
            ) from None
        if hashlib.sha256(raw).hexdigest() != entries[name]["sha256"]:
            raise _refuse(_("The archive was changed after it was made: %(name)s."), name=name)
        try:
            document = json.loads(raw.decode("utf-8"))
        except _NOT_JSON:
            raise _refuse(
                _("A data file of the archive is not valid JSON: %(name)s."), name=name
            ) from None
        if not isinstance(document, dict) or document.get("app") != app:
            raise _refuse(_("A data file of the archive is not valid: %(name)s."), name=name)
        documents[app] = document
    if "homes" not in documents:
        raise _refuse(_("The manifest of the archive is incomplete."))
    return Archive(manifest=manifest, documents=documents, zipped=zipped)


# --- 3: the objects ---------------------------------------------------------------------


def _bad(section: str, index: int, name: str) -> ArchiveRefusedError:
    return _refuse(
        _("The archive holds a value that is not valid: %(section)s, object %(index)s, %(field)s."),
        section=section,
        index=str(index + 1),
        field=name,
    )


def _ahead_of_the_clock(value: datetime.datetime) -> bool:
    """Whether an `updated_at` is more than a day later than the wall clock.

    Such a date would hold the clock of the change log of the home a day or more ahead, since
    a field with no clock reads as the `updated_at` of its row (docs/adr/0077).
    """
    return value > timezone.now() + datetime.timedelta(milliseconds=clock.DRIFT_LIMIT_MS)


def _parse(f: models.Field[Any, Any], raw: Any) -> Any:
    """The Python value of a field from its JSON value; ValueError when it is not one."""
    if raw is None:
        if f.null:
            return None
        raise ValueError
    if isinstance(f, models.ForeignKey | models.UUIDField):
        if not isinstance(raw, str):
            raise ValueError
        return uuid.UUID(raw)
    if isinstance(f, models.DateTimeField):
        value = datetime.datetime.fromisoformat(raw)
        if value.tzinfo is None:
            raise ValueError
        return value
    if isinstance(f, models.DateField):
        return datetime.date.fromisoformat(raw)
    if isinstance(f, models.TimeField):
        return datetime.time.fromisoformat(raw)
    if isinstance(f, models.DecimalField):
        if not isinstance(raw, str):
            raise ValueError
        try:
            number = Decimal(raw)
        except InvalidOperation:
            raise ValueError from None
        if not number.is_finite():
            raise ValueError
        return number
    if isinstance(f, models.BooleanField):
        if not isinstance(raw, bool):
            raise ValueError
        return raw
    if isinstance(f, models.IntegerField):
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError
        return raw
    if isinstance(f, models.JSONField):
        return raw
    if isinstance(f, models.CharField | models.TextField):
        if not isinstance(raw, str):
            raise ValueError
        return raw
    raise ValueError  # pragma: no cover - a type no section uses yet


def _validate(f: models.Field[Any, Any], value: Any) -> None:
    if value is None or value == "" or isinstance(f, models.ForeignKey):
        return
    if f.choices:
        allowed = {str(k) for k, _label in f.flatchoices}
        if str(value) not in allowed:
            raise ValidationError("choice")
    f.run_validators(value)


@dataclass
class Prepared:
    section: Section
    instance: models.Model
    m2m: dict[str, list[uuid.UUID]]
    files: dict[str, str]  # field -> archive path
    stamps: dict[str, Any]


def _objects(archive: Archive) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for app, document in archive.documents.items():
        sections = document.get("sections", {})
        if not isinstance(sections, dict):
            raise _refuse(_("A data file of the archive is not valid: %(name)s."), name=app)
        known = {s.name for s in exports.SECTIONS[app]}
        for name, rows in sections.items():
            if name not in known or not isinstance(rows, list):
                raise _refuse(
                    _(
                        "The archive holds data this instance does not know (%(app)s): "
                        "update HouseCairn first."
                    ),
                    app=f"{app}.{name}"[:60],
                )
            if not all(isinstance(row, dict) for row in rows):
                raise _refuse(_("A data file of the archive is not valid: %(name)s."), name=app)
            out[name] = rows
    return out


def _name_key(text: str) -> str:
    """A name without its case and its extra spaces: what makes two names the same."""
    return " ".join(text.split()).casefold()


def _upgrade_v2(objects: dict[str, list[dict[str, Any]]]) -> None:
    """Version 2 named the provider of the contract of an item as text; version 3, as a contractor.

    The rule of the data migration of the application (docs/adr/0058): an item whose provider
    is the name of a contractor of the archive (case and spaces ignored) takes that
    contractor; any other provider becomes a contractor of the archive with the trade
    Other, shared by the items that name it. The archive is rewritten in memory, before
    its objects are checked like those of any other.
    """
    items = objects.get("items", [])
    if not any(isinstance(row, dict) and "contract_provider" in row for row in items):
        return
    contractors = objects.setdefault("contractors", [])
    by_name: dict[str, str] = {}
    for contractor in contractors:
        name = contractor.get("name")
        if isinstance(name, str) and isinstance(contractor.get("id"), str):
            by_name.setdefault(_name_key(name), contractor["id"])
    for row in items:
        provider = row.pop("contract_provider", "")
        text = " ".join(provider.split()) if isinstance(provider, str) else ""
        if not text or row.get("contract_contractor"):
            continue
        key = _name_key(text)
        if key not in by_name:
            made = str(uuid.uuid4())
            contractors.append(
                {
                    "id": made,
                    "name": text[:120],
                    "trade": "other",
                    "trade_other": "Maintenance",
                    "phone": "",
                    "email": "",
                    "address": "",
                    "note": text if len(text) > 120 else "",
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )
            by_name[key] = made
        row["contract_contractor"] = by_name[key]


# Old identifier -> new identifier, per model: what a copy gives each object of the archive.
Fresh = dict[type[models.Model], dict[uuid.UUID, uuid.UUID]]


def _prepare(
    home: Home,
    objects: dict[str, list[dict[str, Any]]],
    people: dict[uuid.UUID, Any],
    archive: Archive,
    *,
    as_copy: bool = False,
) -> list[Prepared]:
    """Every row to write, parsed and checked, its references inside the archive.

    With `as_copy`, every identifier is replaced by a new one, drawn here, and every
    reference and link by the new identifier of the object it names; the checks are made
    on the identifiers of the archive, before any is replaced.
    """
    sections = [s for _app, s in exports.all_sections() if s.restore]
    ids: dict[type[models.Model], set[uuid.UUID]] = {}
    for section in sections:
        seen: set[uuid.UUID] = set()
        for index, row in enumerate(objects.get(section.name, [])):
            try:
                seen.add(uuid.UUID(str(row.get("id"))))
            except ValueError:
                raise _bad(section.name, index, "id") from None
        if len(seen) != len(objects.get(section.name, [])):
            raise _refuse(
                _("The archive holds the same object twice (%(section)s)."), section=section.name
            )
        ids[section.model] = seen
    fresh: Fresh = {}
    if as_copy:
        fresh = {model: {old: uuid.uuid4() for old in seen} for model, seen in ids.items()}
    prepared: list[Prepared] = []
    entries = archive.manifest["entries"]
    for section in sections:
        file_specs = {spec.field: spec for spec in section.files}
        for index, raw_row in enumerate(objects.get(section.name, [])):
            row = dict(raw_row)
            if section.on_restore is not None:
                row = section.on_restore(row, home)
            if as_copy and section.on_copy is not None:
                row = section.on_copy(row, home)
            values: dict[str, Any] = {"home": home}
            files: dict[str, str] = {}
            stamps: dict[str, Any] = {}
            for f in section.fields():
                raw = row.get(f.name)
                if f.name in file_specs:
                    if raw is None:
                        continue
                    if (
                        not isinstance(raw, str)
                        or raw not in entries
                        or not raw.startswith(
                            f"{exports.FILES_FOLDER}/{file_specs[f.name].folder}/"
                        )
                    ):
                        raise _bad(section.name, index, f.name)
                    files[f.name] = raw
                    continue
                try:
                    value = _parse(f, raw)
                    _validate(f, value)
                except (ValueError, TypeError, ValidationError):
                    raise _bad(section.name, index, f.name) from None
                if exports.person_reference(f):
                    person = people.get(value)
                    values[f.attname] = person.pk if person is not None else None
                    if values[f.attname] is None and not f.null:  # pragma: no cover - none yet
                        raise _bad(section.name, index, f.name)
                    continue
                if isinstance(f, models.ForeignKey):
                    target: Any = f.related_model
                    if value is not None and (
                        target is Home or value not in ids.get(target, set())
                    ):
                        raise _refuse(
                            _(
                                "The archive refers to an object it does not hold "
                                "(%(section)s, object %(index)s, %(field)s)."
                            ),
                            section=section.name,
                            index=str(index + 1),
                            field=f.name,
                        )
                    if as_copy and value is not None:
                        value = fresh[target][value]
                    values[f.attname] = value
                    continue
                if as_copy and f.primary_key:
                    value = fresh[section.model][value]
                if isinstance(f, models.DateTimeField) and (f.auto_now or f.auto_now_add):
                    if f.name == "updated_at" and value is not None and _ahead_of_the_clock(value):
                        raise _bad(section.name, index, f.name)
                    stamps[f.attname] = value
                values[f.attname] = value
            m2m: dict[str, list[uuid.UUID]] = {}
            for relation in section.many_to_many():
                raw_list = row.get(relation.name, [])
                if not isinstance(raw_list, list):
                    raise _bad(section.name, index, relation.name)
                try:
                    targets = [uuid.UUID(str(v)) for v in raw_list]
                except ValueError:
                    raise _bad(section.name, index, relation.name) from None
                if not set(targets) <= ids.get(relation.related_model, set()):  # type: ignore[arg-type]
                    raise _refuse(
                        _(
                            "The archive refers to an object it does not hold "
                            "(%(section)s, object %(index)s, %(field)s)."
                        ),
                        section=section.name,
                        index=str(index + 1),
                        field=relation.name,
                    )
                if as_copy:
                    related: Any = relation.related_model
                    targets = [fresh[related][pk] for pk in targets]
                m2m[relation.name] = targets
            prepared.append(Prepared(section, section.model(**values), m2m, files, stamps))
    return prepared


def _chunks(values: list[Any], size: int = 500) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _check_free(home_id: uuid.UUID, prepared: list[Prepared]) -> None:
    taken = Home.all_objects.filter(pk=home_id).exists()
    by_model: dict[type[models.Model], list[Any]] = {}
    for p in prepared:
        by_model.setdefault(p.section.model, []).append(p.instance.pk)
    for model, pks in by_model.items():
        for chunk in _chunks(pks):
            if taken:
                break
            taken = model._base_manager.filter(pk__in=chunk).exists()
    if taken:
        raise _refuse(
            _(
                "This instance already holds the objects of this archive: the home it was made "
                "from is still here, or the archive was restored already. Restore it as a "
                "copy, delete that home first, or restore on another instance."
            )
        )


# --- 4: the files -----------------------------------------------------------------------


@dataclass
class _File:
    data: bytes
    extension: str
    content_type: str
    thumbnail: bytes | None


def _check_file(data: bytes, spec: FileSpec, declared: str, where: str) -> _File:
    from housecairn.rooms import images
    from housecairn.tasks import files as task_files

    try:
        processed = images.process(data)
    except ValidationError:
        if not spec.documents:
            raise _refuse(
                _("A file of the archive is not a valid image: %(name)s."), name=where
            ) from None
        try:
            kind = task_files.detect(data)
        except ValidationError:
            raise _refuse(
                _("A file of the archive is not of an accepted type: %(name)s."), name=where
            ) from None
        found = _File(data, kind.extension, kind.content_type, None)
    else:
        # The file is kept byte for byte: the thumbnail is what is made again.
        found = _File(data, processed.extension, processed.content_type, processed.thumbnail)
    if found.content_type != declared:
        raise _refuse(
            _("A file of the archive is not of the type its object says: %(name)s."), name=where
        )
    return found


# --- Writing ------------------------------------------------------------------------------


@dataclass
class RestoreResult:
    home: Home
    counts: dict[str, int] = field(default_factory=dict)
    matched: list[str] = field(default_factory=list)  # names, as the archive gives them
    unattributed: list[str] = field(default_factory=list)


def _people(
    document: dict[str, Any], actor: Any, match_people: bool
) -> tuple[dict[uuid.UUID, Any], list[str], list[str]]:
    user_model = get_user_model()
    people = document.get("people", [])
    if not isinstance(people, list):
        raise _refuse(_("A data file of the archive is not valid: %(name)s."), name="homes")
    found: dict[uuid.UUID, Any] = {}
    matched: list[str] = []
    unattributed: list[str] = []
    for person in people:
        try:
            key = uuid.UUID(str(person["id"]))
            email = str(person["email"])
            name = str(person.get("name") or "")[:150]
        except (KeyError, TypeError, ValueError):
            raise _refuse(
                _("A data file of the archive is not valid: %(name)s."), name="homes"
            ) from None
        user = None
        if email.strip().casefold() == actor.email.strip().casefold():
            user = actor
        elif match_people:
            user = user_model.objects.filter(email__iexact=email.strip(), is_active=True).first()
        if user is None:
            unattributed.append(name)
        else:
            found[key] = user
            if user != actor:
                matched.append(name)
    return found, matched, unattributed


def _home_values(document: dict[str, Any], version: int) -> tuple[uuid.UUID, dict[str, Any]]:
    raw = document.get("home")
    if not isinstance(raw, dict):
        raise _refuse(_("The manifest of the archive is incomplete."))
    fields = {f.name: f for f in Home._meta.concrete_fields}
    values: dict[str, Any] = {}
    names = ["name", "currency", "hemisphere", "required_provider", "created_at", "updated_at"]
    if version >= 4:
        # Format 4 adds the archiving of the home (docs/adr/0079); older archives have none.
        names.append("archived_at")
    for name in names:
        try:
            values[name] = _parse(fields[name], raw.get(name))
            _validate(fields[name], values[name])
            if name == "updated_at" and _ahead_of_the_clock(values[name]):
                raise ValueError(name)
        except (ValueError, TypeError, ValidationError):
            raise _bad("home", 0, name) from None
    try:
        home_id = uuid.UUID(str(raw.get("id")))
    except ValueError:
        raise _bad("home", 0, "id") from None
    return home_id, values


def _memberships(
    home: Home, document: dict[str, Any], people: dict[uuid.UUID, Any], actor: Any
) -> None:
    roles: dict[Any, str] = {}
    for member in document.get("sections", {}).get("members", []):
        try:
            user = people.get(uuid.UUID(str(member.get("user"))))
        except (ValueError, AttributeError):
            continue
        if user is not None and member.get("role") in Role.values:
            roles[user.pk] = member["role"]
    roles[actor.pk] = Role.OWNER  # whoever restores owns the restored home
    Membership.objects.bulk_create(
        [Membership(home=home, user_id=pk, role=role) for pk, role in roles.items()]
    )


def _write(home: Home, prepared: list[Prepared], archive: Archive, written: list[Any]) -> None:
    """Insert the rows and write the files, each stored file added to `written` as it is.

    Added at once, not returned at the end: a file refused after others were stored leaves
    them in `written`, which the caller removes.
    """
    by_section: dict[str, list[Prepared]] = {}
    for p in prepared:
        by_section.setdefault(p.section.name, []).append(p)
    for name, group in by_section.items():
        section = group[0].section
        specs = {spec.field: spec for spec in section.files}
        for p in group:
            for field_name, path in p.files.items():
                spec = specs[field_name]
                declared = getattr(p.instance, spec.content_type)
                checked = _check_file(archive.read_file(path), spec, declared, path)
                stored = getattr(p.instance, field_name)
                stored.save(checked.extension, ContentFile(checked.data), save=False)
                written.append(stored)
                if spec.thumbnail and checked.thumbnail is not None:
                    thumb = getattr(p.instance, spec.thumbnail)
                    thumb.save("-thumb.jpg", ContentFile(checked.thumbnail), save=False)
                    written.append(thumb)
        section.model._base_manager.bulk_create([p.instance for p in group], batch_size=500)
        stamped = [p for p in group if p.stamps]
        for p in stamped:
            for attname, value in p.stamps.items():
                setattr(p.instance, attname, value)
        if stamped:
            section.model._base_manager.bulk_update(
                [p.instance for p in stamped], list(stamped[0].stamps), batch_size=500
            )
        # The change log of the new home: every field of every row, whatever its application.
        sync_rows.record_created(p.instance for p in group)
        for relation in section.many_to_many():
            through: Any = relation.remote_field.through
            source = relation.m2m_field_name()
            target = relation.m2m_reverse_field_name()
            through._default_manager.bulk_create(
                [
                    through(**{f"{source}_id": p.instance.pk, f"{target}_id": pk})
                    for p in group
                    for pk in p.m2m.get(relation.name, [])
                ],
                batch_size=500,
            )
        if section.on_written is not None:
            section.on_written([p.instance for p in group])
        del name


NAME_MAX = Home._meta.get_field("name").max_length or 120


def copy_name(actor: Any, name: str) -> str:
    """The name a copy is given by default: the name of the home, said to be a copy.

    "Home (copy)", then "Home (copy 2)" and so on when the person already has a home of
    that name; the name of the home is shortened so that the whole fits the field.
    """
    taken = {_name_key(n) for n in services.homes_for(actor).values_list("name", flat=True)}
    number = 1
    while True:
        suffix = _(" (copy)") if number == 1 else _(" (copy %(number)s)") % {"number": number}
        candidate = name[: max(NAME_MAX - len(suffix), 1)].rstrip() + suffix
        if _name_key(candidate) not in taken:
            return candidate
        number += 1


def _copy_name_value(actor: Any, name: str, archived: str) -> str:
    """The name chosen for the copy, on one line, or the default one when it is left empty."""
    chosen = " ".join(without_control_characters(" ".join(str(name).split())).split())
    if not chosen:
        return copy_name(actor, archived)
    if len(chosen) > NAME_MAX:
        raise _refuse(
            _("The name of the copy is too long (at most %(max)s characters)."),
            max=str(NAME_MAX),
        )
    return chosen


def _check_may_copy(actor: Any, archive: Archive) -> None:
    """Only an owner of the home of the archive copies it, when that home is on this instance."""
    original = Home.objects.filter(pk=_manifest_home(archive)).first()
    if original is not None and not services.is_owner(actor, original):
        raise CopyNotAllowedError


def restore_home(
    actor: Any,
    source: IO[bytes],
    *,
    match_people: bool = False,
    as_copy: bool = False,
    name: str = "",
) -> RestoreResult:
    """Restore an archive as a new home owned by `actor`. ArchiveRefusedError when it cannot be.

    `as_copy` makes it a copy, with new identifiers (docs/adr/0069): `name` is its name,
    by default that of `copy_name`; only `actor` is matched and becomes a member, whatever
    `match_people` says. CopyNotAllowedError when the home of the archive is on this
    instance and `actor` is not one of its owners.
    """
    from housecairn.audit import services as audit

    archive = read_archive(source)
    homes_doc = archive.documents["homes"]
    home_id, home_values = _home_values(homes_doc, archive.manifest["version"])
    if home_id != _manifest_home(archive):
        raise _refuse(_("The manifest of the archive is incomplete."))
    if as_copy:
        _check_may_copy(actor, archive)
        match_people = False
        home_id = uuid.uuid4()
        home_values["name"] = _copy_name_value(actor, name, home_values["name"])
        home_values["created_at"] = home_values["updated_at"] = timezone.now()
    people, matched, unattributed = _people(homes_doc, actor, match_people)
    home = Home(id=home_id, **home_values)
    objects = _objects(archive)
    if archive.manifest["version"] == 2:
        _upgrade_v2(objects)
    prepared = _prepare(home, objects, people, archive, as_copy=as_copy)
    _check_free(home_id, prepared)
    size = sum(
        archive.manifest["entries"][path]["size"] for p in prepared for path in p.files.values()
    )
    count = sum(len(p.files) for p in prepared)
    written: list[Any] = []
    try:
        with transaction.atomic():
            sync_rows.save(home, force_insert=True)
            Home.all_objects.filter(pk=home.pk).update(
                created_at=home_values["created_at"], updated_at=home_values["updated_at"]
            )
            quotas.check(home, size=size, new_files=count)
            # A copy has no member of the archive: only whoever makes it (docs/adr/0069).
            _memberships(home, {} if as_copy else homes_doc, people, actor)
            _write(home, prepared, archive, written)
            audit.record(
                "home.copied" if as_copy else "home.restored",
                actor=actor,
                home_id=home.id,
                format=f"v{archive.manifest['version']}",
            )
    except IntegrityError:
        _remove(written)
        raise _refuse(
            _("This archive conflicts with data already on this instance; nothing was restored.")
        ) from None
    except BaseException:
        _remove(written)
        raise
    counts: dict[str, int] = {}
    for p in prepared:
        counts[p.section.name] = counts.get(p.section.name, 0) + 1
    home.refresh_from_db()
    return RestoreResult(home=home, counts=counts, matched=matched, unattributed=unattributed)


def _manifest_home(archive: Archive) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(archive.manifest.get("home")))
    except ValueError:
        return None


def _remove(written: list[Any]) -> None:
    for stored in written:
        with contextlib.suppress(OSError):
            stored.delete(save=False)


def restore_bytes(actor: Any, data: bytes, **options: Any) -> RestoreResult:
    """`restore_home` on an archive held in memory (tests, small uploads)."""
    return restore_home(actor, io.BytesIO(data), **options)
