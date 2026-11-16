"""The files of the sync API: uploaded under the phone's id, referred to by rows (lot M6).

docs/adr/0077, need 10, and the mobile protocol, section 2.10. A phone uploads a file
(`PUT /api/v1/sync/homes/{h}/files/{file_id}`) before the push whose operation refers to it
with a **file reference** `{"file": uuid, "sha256": hex, "size", "type", "width", "height"}`.

- The bytes are checked and stored as the web stores them: an image is decoded and written
  again (`rooms.images.process`, EXIF and everything else of the file dropped), a document must
  be on the allow-list of the attachments (`tasks.files.detect`). The server keeps its own copy;
  the phone keeps its own.
- The file waits under `MEDIA_ROOT/sync/<home>/<file_id>` until a row refers to it. A row of a
  type that holds a file (`HOLDERS`) then takes it, moved into the folder of its app, still
  named by the phone's id: `<home>/<file_id><extension>`. The file id of a row is read from the
  name of its stored file, so a file made on the web is named by the id of its row, as the feed
  has always given it.
- A row may refer to a file the server does not have yet: its file column holds
  `<home>/<file_id>` without an extension and no file is on the disk. The upload that comes
  later gives the file to the row. Until then the web shows no picture and `GET` is a 404.
- Attaching a file to a row writes the columns of the stored file (type, size, dimensions,
  thumbnail) without a change of the feed: the reference of the row, which is what the feed
  carries, does not change.
- A file no row ever referred to is removed by the nightly purge after 7 days (`purge`).
"""

import contextlib
import datetime
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from pathlib import PurePosixPath
from typing import Any

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from housecairn.homes import quotas
from housecairn.homes.models import Home
from housecairn.sync.models import SyncFile
from housecairn.sync.storage import sync_storage

# A file no row ever referred to is removed after this (docs/adr/0077, need 10).
ORPHAN_AFTER = datetime.timedelta(days=7)
THUMBNAIL_SUFFIX = "-thumb.jpg"
SHA256 = re.compile(r"[0-9a-f]{64}")
TYPE_MAX_LENGTH = 100


@dataclass(frozen=True)
class Holder:
    """A type of row that holds a file: the field of the protocol, which is its file column."""

    entity: str
    field: str
    # A photo of a room or an item takes an image only; an attachment, a document too.
    images_only: bool
    # Whether the model keeps `is_image` (an attachment, a project file).
    has_is_image: bool


HOLDERS: dict[str, Holder] = {
    "rooms.photo": Holder("rooms.photo", "image", images_only=True, has_is_image=False),
    "tasks.attachment": Holder("tasks.attachment", "file", images_only=False, has_is_image=True),
    "projects.projectfile": Holder(
        "projects.projectfile", "file", images_only=False, has_is_image=True
    ),
}


def model_of(holder: Holder) -> type[models.Model]:
    from django.apps import apps

    return apps.get_model(holder.entity)


def holder_of(model: type[models.Model]) -> Holder | None:
    return HOLDERS.get(model._meta.label_lower)


def _storage(model: type[models.Model], holder: Holder) -> Storage:
    field: Any = model._meta.get_field(holder.field)
    return field.storage  # type: ignore[no-any-return]


# ---------- the reference of an operation ----------


@dataclass(frozen=True)
class Reference:
    """A file reference as a phone writes it in the server mode (protocol section 2.10)."""

    file: uuid.UUID
    sha256: str
    size: int
    type: str
    width: int | None
    height: int | None


class BadReferenceError(ValueError):
    """A value that is not a file reference: the push is `400` `malformed`."""


def _whole(value: Any, name: str, *, required: bool) -> int | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 1 << 31:
        raise BadReferenceError(_("%(name)s of a file is a whole number.") % {"name": name})
    whole: int = value
    return whole


def parse_reference(value: Any) -> Reference:
    """The reference of a file field of an operation; BadReferenceError when it is not one."""
    if not isinstance(value, dict):
        raise BadReferenceError(_("A file is a reference: file, sha256, size and type."))
    text = value.get("file")
    try:
        file_id = uuid.UUID(text) if isinstance(text, str) and len(text) == 36 else None
    except ValueError:
        file_id = None
    if file_id is None or str(file_id) != text:
        raise BadReferenceError(_("file is a UUID in its canonical text form."))
    digest = value.get("sha256")
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        raise BadReferenceError(_("sha256 is 64 lower-case hexadecimal digits."))
    kind = value.get("type")
    if not isinstance(kind, str) or not kind or len(kind) > TYPE_MAX_LENGTH:
        raise BadReferenceError(_("type of a file is a media type."))
    size = _whole(value.get("size"), "size", required=True)
    return Reference(
        file=file_id,
        sha256=digest,
        size=size or 0,
        type=kind,
        width=_whole(value.get("width"), "width", required=False),
        height=_whole(value.get("height"), "height", required=False),
    )


def check_digest(home_id: uuid.UUID, reference: Reference) -> None:
    """BadReferenceError when the file of the home under this id holds other bytes.

    The phone and the server never disagree silently on a file: a reference whose `sha256` is
    not the digest of the file uploaded under its id is refused (docs/adr/0077, need 10). A
    file of another home is not looked at: it is not this home's file.
    """
    digest = (
        SyncFile.objects.filter(pk=reference.file, home_id=home_id)
        .values_list("sha256", flat=True)
        .first()
    )
    if digest is not None and digest != reference.sha256:
        raise BadReferenceError(_("sha256 is not the digest of the file uploaded under this id."))


# ---------- the file id of a row ----------


def pending_name(home_id: uuid.UUID, file_id: uuid.UUID) -> str:
    """The file column of a row whose file has not arrived: no extension, nothing on disk."""
    return f"{home_id}/{file_id}"


def file_id_of(name: str, fallback: Any) -> str:
    """The id of the file a stored name holds (`<home>/<file id><extension>`), else `fallback`."""
    stem = PurePosixPath(name or "").name[:36]
    try:
        return str(uuid.UUID(stem)) if len(stem) == 36 else str(fallback)
    except ValueError:
        return str(fallback)


def reference_of(instance: Any, holder: Holder) -> dict[str, Any]:
    """The file reference of a row, as the feed gives it (docs/adr/0077, need 10).

    Named by the phone's id for a file a phone sent, by the id of the row for one made on the
    web; with the digest of what the phone sent, when it sent the file. The size, type and
    dimensions are those of the file the server keeps (or of the reference, while it waits).
    """
    name = getattr(instance, holder.field).name or ""
    file_id = file_id_of(name, instance.pk)
    reference: dict[str, Any] = {
        "file": file_id,
        "size": instance.size_bytes,
        "type": instance.content_type,
        "width": instance.width,
        "height": instance.height,
    }
    digest = SyncFile.objects.filter(pk=file_id).values_list("sha256", flat=True).first()
    if digest is not None:
        reference["sha256"] = digest
    return reference


# ---------- writing the reference of a pushed row ----------


def columns_for(
    model: type[models.Model], home_id: uuid.UUID, reference: Reference
) -> tuple[dict[str, Any], Callable[[Any], None] | None]:
    """The columns a row writes for the reference that won its file field.

    The file of the phone, when the server has it and it fits the row (an image for a photo),
    with what the server keeps of it; else the reference itself, waiting for its file. The
    second value, when not None, moves the file to the row once the row is written.
    """
    holder = holder_of(model)
    if holder is None:  # pragma: no cover - the push calls it for the holders only
        raise ValueError(f"{model._meta.label} holds no file.")
    found = SyncFile.objects.filter(pk=reference.file, home_id=home_id).first()
    if found is not None and (found.is_image or not holder.images_only):
        columns = _stored_columns(holder, found, home_id)
        return columns, lambda row: _take(row, holder, found)
    columns = {
        holder.field: pending_name(home_id, reference.file),
        "thumbnail": "",
        "content_type": reference.type[: model._meta.get_field("content_type").max_length],  # type: ignore[union-attr]
        "size_bytes": reference.size,
        "width": reference.width if reference.width is not None or not holder.images_only else 0,
        "height": (
            reference.height if reference.height is not None or not holder.images_only else 0
        ),
    }
    if holder.has_is_image:
        columns["is_image"] = reference.type.startswith("image/")
    return columns, None


def _extension(found: SyncFile) -> str:
    return PurePosixPath(found.stored).suffix if found.stored else ""


def _stored_columns(holder: Holder, found: SyncFile, home_id: uuid.UUID) -> dict[str, Any]:
    extension = _extension(found) or _held_extension(found)
    columns: dict[str, Any] = {
        holder.field: f"{home_id}/{found.pk}{extension}",
        "thumbnail": f"{home_id}/{found.pk}{THUMBNAIL_SUFFIX}" if found.is_image else "",
        "content_type": found.content_type,
        "size_bytes": found.size_bytes,
        "width": found.width if found.width is not None or not holder.images_only else 0,
        "height": found.height if found.height is not None or not holder.images_only else 0,
    }
    if holder.has_is_image:
        columns["is_image"] = found.is_image
    return columns


def _held_extension(found: SyncFile) -> str:
    """The extension of a file a row already took (a second row that refers to it)."""
    located = locate(found.home_id, found.pk)
    return PurePosixPath(located.name).suffix if located is not None else ""


def _take(row: Any, holder: Holder, found: SyncFile) -> None:
    """Give the row the file: moved from the sync folder, or copied from the row that has it.

    The copy is made in the transaction of the write, and the file of the sync folder removed
    once it commits. Should the transaction roll back, the copy stays under the name the row
    would take, and the next write that refers to the file finds it there and keeps it.

    Two rows never share a stored file: deleting one of them, or giving it another file, then
    removes its own copy only. A second row that refers to a file another row holds gets a
    copy named after both (`<home>/<file id>-<row>`), still read as the file id.
    """
    model = type(row)
    target = _storage(model, holder)
    name = getattr(row, holder.field).name
    shared = _named_by_another_row(model, holder, row.pk, name)
    if target.exists(name) and not shared:
        _mark_referenced(found)
        return
    if shared:
        name, thumbnail = _own_names(row, holder, name)
        type(row)._base_manager.filter(pk=row.pk).update(
            **{holder.field: name, "thumbnail": thumbnail}
        )
        row.refresh_from_db()
    source = locate(found.home_id, found.pk, exclude=(model, row.pk))
    if source is None:  # the bytes are gone (removed by hand): the row keeps the reference
        return
    moving = source.storage is sync_storage
    if not moving:
        with quotas.room_for(row.home, size=found.size_bytes):
            _copy(source, target, name, row, holder)
    else:
        _copy(source, target, name, row, holder)
    if moving:
        stored, thumb = found.stored, found.thumbnail
        transaction.on_commit(lambda: _delete(sync_storage, [stored, thumb]))
        SyncFile.objects.filter(pk=found.pk).update(stored="", thumbnail="")
    _mark_referenced(found)


def _named_by_another_row(model: type[models.Model], holder: Holder, pk: Any, name: str) -> bool:
    if not name:
        return False
    return model._base_manager.filter(**{holder.field: name}).exclude(pk=pk).exists()


def _own_names(row: Any, holder: Holder, name: str) -> tuple[str, str]:
    """The names of a copy of a file for `row`: the file id, then a part of the row's id."""
    path = PurePosixPath(name)
    stem = f"{path.parent}/{path.name[:36]}-{str(row.pk)[:8]}"
    thumbnail = f"{stem}{THUMBNAIL_SUFFIX}" if row.thumbnail.name else ""
    return f"{stem}{path.suffix}", thumbnail


def _copy(source: "Located", target: Storage, name: str, row: Any, holder: Holder) -> None:
    with source.storage.open(source.name, "rb") as handle:
        saved = target.save(name, handle)
    thumbnail = row.thumbnail.name
    if source.thumbnail and thumbnail:
        with source.storage.open(source.thumbnail, "rb") as handle:
            saved_thumb = target.save(thumbnail, handle)
    else:
        saved_thumb = thumbnail
    if saved != name or saved_thumb != thumbnail:  # the storage chose another name
        type(row)._base_manager.filter(pk=row.pk).update(
            **{holder.field: saved, "thumbnail": saved_thumb}
        )


def _mark_referenced(found: SyncFile) -> None:
    if found.referenced_at is None:
        SyncFile.objects.filter(pk=found.pk, referenced_at__isnull=True).update(
            referenced_at=timezone.now()
        )


def _delete(storage: Storage, names: Iterable[str]) -> None:
    for name in names:
        if name:
            with contextlib.suppress(OSError):
                storage.delete(name)


def replaced(model: type[models.Model], storage: Storage, names: Iterable[str]) -> None:
    """The stored files of a row whose reference changed: removed once the write commits.

    A name another row still holds (a leftover of a copy) is kept.
    """
    holder = holder_of(model)
    kept = [name for name in names if name]
    if not kept or holder is None:
        return

    def remove() -> None:
        held = set(
            model._base_manager.filter(**{f"{holder.field}__in": kept}).values_list(
                holder.field, flat=True
            )
        ) | set(model._base_manager.filter(thumbnail__in=kept).values_list("thumbnail", flat=True))
        _delete(storage, [name for name in kept if name not in held])

    transaction.on_commit(remove)


# ---------- where a file is ----------


@dataclass(frozen=True)
class Located:
    storage: Storage
    name: str
    thumbnail: str
    content_type: str
    is_image: bool
    file_id: str


def _rows_naming(
    home_id: uuid.UUID, file_id: uuid.UUID
) -> Iterable[tuple[type[models.Model], Holder, Any]]:
    prefix = pending_name(home_id, file_id)
    for holder in HOLDERS.values():
        model = model_of(holder)
        rows = model._base_manager.filter(
            home_id=home_id, **{f"{holder.field}__startswith": prefix}
        ).order_by("created_at", "pk")
        for row in rows:
            yield model, holder, row


def locate(
    home_id: uuid.UUID,
    file_id: uuid.UUID,
    *,
    exclude: tuple[type[models.Model], Any] | None = None,
) -> Located | None:
    """The stored file of the id `file_id` in a home: held by a row, or waiting for one."""
    for model, holder, row in _rows_naming(home_id, file_id):
        if exclude is not None and (model, row.pk) == exclude:
            continue
        name = getattr(row, holder.field).name
        storage = _storage(model, holder)
        if name == pending_name(home_id, file_id) or not storage.exists(name):
            continue
        is_image = bool(getattr(row, "is_image", True))
        return Located(
            storage,
            name,
            row.thumbnail.name or "",
            row.content_type,
            is_image,
            str(file_id),
        )
    found = SyncFile.objects.filter(pk=file_id, home_id=home_id).exclude(stored="").first()
    if found is None or not sync_storage.exists(found.stored):
        return None
    return Located(
        sync_storage,
        found.stored,
        found.thumbnail,
        found.content_type,
        found.is_image,
        str(file_id),
    )


def held_by_a_row(home_id: uuid.UUID, file_id: uuid.UUID) -> bool:
    """Whether a row of the home names the file id, its file stored or waiting."""
    return any(True for _ in _rows_naming(home_id, file_id))


# ---------- receiving an upload ----------


class ConflictError(Exception):
    """The id holds other bytes, or is held by something else: `409`."""


class UnsupportedError(Exception):
    """Not a file the web accepts: `415`."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class QuotaError(Exception):
    """The home has no room left for the file: `413` `quota_exceeded`."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Processed:
    data: bytes
    thumbnail: bytes | None
    extension: str
    content_type: str
    is_image: bool
    width: int | None
    height: int | None


def process(data: bytes) -> Processed:
    """Check the bytes as the web checks an attachment; UnsupportedError when it refuses them."""
    from housecairn.rooms import images
    from housecairn.tasks import files as task_files
    from housecairn.tasks.services import _looks_like_image

    try:
        image = images.process(data)
    except ValidationError as image_error:
        if _looks_like_image(data):
            raise UnsupportedError(" ".join(image_error.messages)) from None
        try:
            kind = task_files.detect(data)
        except ValidationError as error:
            raise UnsupportedError(" ".join(error.messages)) from None
        return Processed(data, None, kind.extension, kind.content_type, False, None, None)
    return Processed(
        image.data,
        image.thumbnail,
        image.extension,
        image.content_type,
        True,
        image.width,
        image.height,
    )


def receive(home: Home, file_id: uuid.UUID, data: bytes, digest: str) -> bool:
    """Keep the file a phone uploaded; True when it is new, False when it was there already.

    ConflictError when the id holds other bytes or is the id of a file of the web,
    UnsupportedError when the web would refuse the file, QuotaError when the home is full.
    """
    with transaction.atomic():
        # The lock of the push (`changes.record_change`): a push that refers to the file and
        # this upload do not miss each other.
        Home.all_objects.select_for_update().only("pk").get(pk=home.pk)
        found = SyncFile.objects.select_for_update().filter(pk=file_id).first()
        if found is not None:
            if found.home_id != home.pk or found.sha256 != digest:
                raise ConflictError
            return False
        if locate(home.pk, file_id) is not None:
            raise ConflictError  # a file of the web, named by the id of its row
        processed = process(data)
        size = len(processed.data) + len(processed.thumbnail or b"")
        try:
            with quotas.room_for(home, size=size):
                made = _store(home, file_id, digest, processed)
        except ValidationError as error:
            raise QuotaError(" ".join(error.messages)) from None
        _give_to_waiting_rows(home, made)
    return True


def _store(home: Home, file_id: uuid.UUID, digest: str, processed: Processed) -> SyncFile:
    name = sync_storage.save(
        f"{home.pk}/{file_id}{processed.extension}", ContentFile(processed.data)
    )
    thumbnail = ""
    if processed.thumbnail is not None:
        thumbnail = sync_storage.save(
            f"{home.pk}/{file_id}{THUMBNAIL_SUFFIX}", ContentFile(processed.thumbnail)
        )
    written = [name, thumbnail]
    try:
        return SyncFile.objects.create(
            id=file_id,
            home=home,
            sha256=digest,
            stored=name,
            thumbnail=thumbnail,
            content_type=processed.content_type,
            is_image=processed.is_image,
            size_bytes=len(processed.data),
            width=processed.width,
            height=processed.height,
        )
    except Exception:
        _delete(sync_storage, written)
        raise


def _give_to_waiting_rows(home: Home, found: SyncFile) -> None:
    """The rows that referred to the file before it arrived take it now."""
    pending = pending_name(home.pk, found.pk)
    for holder in HOLDERS.values():
        if holder.images_only and not found.is_image:
            continue
        model = model_of(holder)
        for row in model._base_manager.filter(home_id=home.pk, **{holder.field: pending}):
            columns = _stored_columns(holder, found, home.pk)
            model._base_manager.filter(pk=row.pk).update(**columns)
            row.refresh_from_db()
            _take(row, holder, found)
            found.refresh_from_db()


# ---------- the nightly purge ----------


def purge(now: datetime.datetime | None = None) -> int:
    """Remove the files no row ever referred to, 7 days after their upload; how many.

    The rows of the files whose row is gone for good (purged by the retention) go too: they
    only kept the digest.
    """
    limit = (now or timezone.now()) - ORPHAN_AFTER
    removed = 0
    for found in SyncFile.objects.filter(referenced_at__isnull=True, created_at__lt=limit):
        with transaction.atomic():
            if held_by_a_row(found.home_id, found.pk):
                _mark_referenced(found)
                continue
            found.delete()
            transaction.on_commit(partial(_delete, sync_storage, [found.stored, found.thumbnail]))
            removed += 1
    for found in SyncFile.objects.filter(referenced_at__isnull=False, stored=""):
        if not held_by_a_row(found.home_id, found.pk):
            found.delete()
    return removed
