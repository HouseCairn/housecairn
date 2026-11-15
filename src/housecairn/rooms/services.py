"""Business rules of rooms, items and photos (docs/adr/0002, 0029, 0030).

Web views and API views call these functions; neither holds rules of its own.
Reading is limited to the user's homes by the views (`housecairn.homes.scoping`);
writing is checked here, by role: owners and members write, read-only reads.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db import models, transaction
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext as _

from housecairn.accounts.models import User
from housecairn.audit import services as audit
from housecairn.core.text import without_control_characters
from housecairn.homes import quotas
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.rooms import conf, images
from housecairn.rooms.models import Item, Photo, PhotoCategory, Room, RoomKind
from housecairn.sync import rows as sync_rows

AnyUser = User | AnonymousUser

ROOM_FIELDS = frozenset({"name", "kind", "notes"})
MEMORY_FIELDS = frozenset(
    {
        "paint_colour",
        "paint_brand",
        "paint_reference",
        "paint_batch",
        "paint_article",
        "floor_covering",
        "wall_covering",
        "memory_notes",
    }
)
ITEM_FIELDS = frozenset(
    {
        "name",
        "kind",
        "brand",
        "model",
        "serial_number",
        "installed_on",
        "warranty_ends_on",
        "contract_contractor",
        "contract_number",
        "contract_url",
        "links",
        "notes",
    }
)
SEARCH_LIMIT = 50


def _require_write(actor: AnyUser, home: Home) -> None:
    if not home_services.can_write(actor, home):
        raise PermissionDenied


def _soft_delete(rows: models.QuerySet[Any]) -> None:
    """Soft-delete the rows, each with its tombstone in the change log (docs/adr/0077)."""
    sync_rows.soft_delete_queryset(rows)


# Free text on one line: control and format characters are dropped. Notes keep their line breaks.
SINGLE_LINE_FIELDS = (ROOM_FIELDS | MEMORY_FIELDS | ITEM_FIELDS | {"caption"}) - {
    "notes",
    "memory_notes",
    "links",
}


def _apply(instance: models.Model, allowed: frozenset[str], changes: dict[str, Any]) -> None:
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"Cannot change: {', '.join(sorted(unknown))}")
    for field, value in changes.items():
        if field in SINGLE_LINE_FIELDS and isinstance(value, str):
            value = without_control_characters(value)
        setattr(instance, field, value)


# ---------- reading ----------


def rooms_of(home: Home) -> models.QuerySet[Room]:
    """Rooms of the home in their order, with `item_count`."""
    rooms: models.QuerySet[Room] = (
        Room.objects.filter(home=home)
        .annotate(item_count=models.Count("items", filter=models.Q(items__deleted_at__isnull=True)))
        .order_by("position", "created_at", "id")
    )
    return rooms


def items_of(home: Home) -> models.QuerySet[Item]:
    return Item.objects.filter(home=home).select_related("room", "contract_contractor")


def brands_of(home: Home) -> list[str]:
    """The brands already entered for the items of the home, once each, alphabetical.

    Compared without regard to case: the first spelling met is the one suggested.
    """
    seen: dict[str, str] = {}
    for brand in Item.objects.filter(home=home).exclude(brand="").values_list("brand", flat=True):
        seen.setdefault(brand.casefold(), brand)
    return sorted(seen.values(), key=str.casefold)


def photos_of(home: Home) -> models.QuerySet[Photo]:
    return Photo.objects.filter(home=home)


@dataclass(frozen=True)
class SearchResult:
    rooms: list[Room]
    items: list[Item]

    def __bool__(self) -> bool:
        return bool(self.rooms or self.items)


def search(home: Home, query: str) -> SearchResult:
    """Rooms and items of the home whose name contains `query` (case-insensitive)."""
    query = query.strip()
    if not query:
        return SearchResult([], [])
    rooms = rooms_of(home).filter(name__icontains=query)[:SEARCH_LIMIT]
    items = items_of(home).filter(name__icontains=query).order_by("name", "id")[:SEARCH_LIMIT]
    return SearchResult(list(rooms), list(items))


# ---------- rooms ----------


def _name_the_room(room: Room) -> None:
    """A room left unnamed, or named by invisible characters, takes the label of its type.

    As the form does (docs/adr/0045); "Other" has no label to offer.
    """
    if room.name.strip():
        return
    if room.kind == RoomKind.OTHER:
        raise ValidationError({"name": _("Give the room a name.")})
    room.name = str(RoomKind(room.kind).label)


def _check_paint(room: Room) -> None:
    """The tin left over is an item of the stock of this very home (docs/adr/0046)."""
    article = room.paint_article
    if article is not None and (article.home_id != room.home_id or article.deleted_at is not None):
        raise ValidationError({"paint_article": _("This stock item is not in this home.")})


@transaction.atomic
def create_room(actor: AnyUser, home: Home, **fields: Any) -> Room:
    _require_write(actor, home)
    room = Room(home=home)
    _apply(room, ROOM_FIELDS | MEMORY_FIELDS, fields)
    _name_the_room(room)
    _check_paint(room)
    last = Room.objects.filter(home=home).aggregate(last=models.Max("position"))["last"]
    room.position = 0 if last is None else last + 1
    room.full_clean()
    sync_rows.save(room)
    audit.record_object("room.created", actor=actor, home_id=home.id, title=room.name)
    return room


@transaction.atomic
def update_room(actor: AnyUser, room: Room, **changes: Any) -> Room:
    _require_write(actor, room.home)
    _apply(room, ROOM_FIELDS | MEMORY_FIELDS, changes)
    _name_the_room(room)
    if "paint_article" in changes:
        _check_paint(room)
    room.full_clean()
    sync_rows.save(room)
    audit.record_object(
        "room.updated", actor=actor, home_id=room.home_id, title=room.name, fields=sorted(changes)
    )
    return room


@transaction.atomic
def delete_room(actor: AnyUser, room: Room) -> None:
    """Soft-delete the room with its items and photos."""
    _require_write(actor, room.home)
    _soft_delete(Photo.objects.filter(models.Q(room=room) | models.Q(item__room=room)))
    _soft_delete(Item.objects.filter(room=room))
    sync_rows.soft_delete(room)
    audit.record_object("room.deleted", actor=actor, home_id=room.home_id, title=room.name)


@transaction.atomic
def reorder_rooms(actor: AnyUser, home: Home, ordered_ids: Sequence[UUID]) -> list[Room]:
    """Put the listed rooms first, in that order; the others follow in their current order."""
    _require_write(actor, home)
    rooms = list(Room.objects.filter(home=home).select_for_update())
    by_id = {room.id: room for room in rooms}
    if len(set(ordered_ids)) != len(ordered_ids) or not set(ordered_ids) <= set(by_id):
        raise ValidationError(_("The list holds a room twice, or a room that does not exist."))
    first = [by_id[pk] for pk in ordered_ids]
    rest = [room for room in rooms if room.id not in set(ordered_ids)]
    ordered = [*first, *sorted(rest, key=lambda r: (r.position, r.created_at, r.id))]
    for position, room in enumerate(ordered):
        if room.position != position:
            room.position = position
            sync_rows.save(room, update_fields=["position", "updated_at"])
    return ordered


def move_room(actor: AnyUser, room: Room, direction: str) -> None:
    """Swap the room with its neighbour, `up` (earlier) or `down` (later)."""
    if direction not in {"up", "down"}:
        raise ValidationError(_("Choose up or down."))
    ids = [r.id for r in Room.objects.filter(home=room.home)]
    index = ids.index(room.id)
    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(ids):
        ids[index], ids[target] = ids[target], ids[index]
    reorder_rooms(actor, room.home, ids)


# ---------- items ----------


def _check_same_home(room: Room, home: Home) -> None:
    if room.home_id != home.id:
        raise ValidationError({"room": _("This room is not in this home.")})


@transaction.atomic
def create_item(actor: AnyUser, room: Room, **fields: Any) -> Item:
    _require_write(actor, room.home)
    item = Item(home=room.home, room=room)
    _apply(item, ITEM_FIELDS, fields)
    item.full_clean()
    sync_rows.save(item)
    audit.record_object("item.created", actor=actor, home_id=item.home_id, title=item.name)
    return item


@transaction.atomic
def update_item(actor: AnyUser, item: Item, *, room: Room | None = None, **changes: Any) -> Item:
    """Change the item; passing another `room` moves it, photos included."""
    _require_write(actor, item.home)
    _apply(item, ITEM_FIELDS, changes)
    if room is not None:
        _check_same_home(room, item.home)
        item.room = room
    item.full_clean()
    sync_rows.save(item)
    audit.record_object(
        "item.updated",
        actor=actor,
        home_id=item.home_id,
        title=item.name,
        fields=sorted([*changes, *(["room"] if room is not None else [])]),
    )
    return item


@transaction.atomic
def delete_item(actor: AnyUser, item: Item) -> None:
    _require_write(actor, item.home)
    _soft_delete(Photo.objects.filter(item=item))
    sync_rows.soft_delete(item)
    audit.record_object("item.deleted", actor=actor, home_id=item.home_id, title=item.name)


# ---------- photos ----------


def _read_upload(upload: UploadedFile) -> bytes:
    limit = conf.photo_max_bytes()
    too_large = ValidationError(
        _("This file is too large: the limit is %(limit)s."),
        params={"limit": filesizeformat(limit)},
    )
    if upload.size is not None and upload.size > limit:
        raise too_large
    data: bytes = upload.read(limit + 1)
    if len(data) > limit:
        raise too_large
    if not data:
        raise ValidationError(_("This file is empty."))
    return data


def add_photo(
    actor: AnyUser,
    *,
    upload: UploadedFile,
    room: Room | None = None,
    item: Item | None = None,
    caption: str = "",
    category: str = PhotoCategory.GENERAL,
) -> Photo:
    """Attach a photo to a room or an item (exactly one of the two).

    The upload is decoded and written again (`images.process`); the name sent with
    it is dropped and the stored names come from identifiers.
    """
    if (room is None) == (item is None):
        raise ValueError("A photo belongs to a room or to an item, not both, not neither.")
    home = (room or item).home  # type: ignore[union-attr]
    _require_write(actor, home)
    existing = Photo.objects.filter(home=home, room=room, item=item).count()
    if existing >= conf.MAX_PHOTOS_PER_TARGET:
        raise ValidationError(
            _("At most %(max)d photos for a room or an item."),
            params={"max": conf.MAX_PHOTOS_PER_TARGET},
        )
    processed = images.process(_read_upload(upload))
    photo = Photo(
        home=home,
        room=room,
        item=item,
        caption=without_control_characters(caption),
        category=category,
        content_type=processed.content_type,
        size_bytes=len(processed.data),
        width=processed.width,
        height=processed.height,
    )
    photo.full_clean(exclude=["image", "thumbnail"])
    with quotas.room_for(home, size=len(processed.data) + len(processed.thumbnail)):
        photo.image.save(processed.extension, ContentFile(processed.data), save=False)
        photo.thumbnail.save("-thumb.jpg", ContentFile(processed.thumbnail), save=False)
        try:
            sync_rows.save(photo)
        except Exception:
            photo.image.delete(save=False)
            photo.thumbnail.delete(save=False)
            raise
    audit.record_object(
        "photo.added",
        actor=actor,
        home_id=home.id,
        title=(room or item).name,  # type: ignore[union-attr]
        kind="room" if room is not None else "item",
    )
    return photo


@transaction.atomic
def update_photo(actor: AnyUser, photo: Photo, **changes: Any) -> Photo:
    _require_write(actor, photo.home)
    _apply(photo, frozenset({"caption", "category"}), changes)
    photo.full_clean(exclude=["image", "thumbnail"])
    sync_rows.save(photo)
    return photo


@transaction.atomic
def delete_photo(actor: AnyUser, photo: Photo) -> None:
    """Soft-delete: the file stays on disk until the purge of deleted records."""
    _require_write(actor, photo.home)
    sync_rows.soft_delete(photo)
    target = photo.room or photo.item
    audit.record_object(
        "photo.deleted",
        actor=actor,
        home_id=photo.home_id,
        title=target.name if target is not None else "",
        kind="room" if photo.room_id else "item",
    )


@transaction.atomic
def clear_contract_contractor(contractor: Any) -> int:
    """Take a contractor off the maintenance contracts of the items that name it.

    The number and the link of each contract stay (docs/adr/0058). Called when a contractor is
    deleted; each item that loses its provider records the change.
    """
    return sync_rows.update_queryset(
        Item.all_objects.filter(contract_contractor=contractor), contract_contractor=None
    )


def photos_for(target: Room | Item) -> models.QuerySet[Photo]:
    return Photo.objects.filter(**{"room" if isinstance(target, Room) else "item": target})
