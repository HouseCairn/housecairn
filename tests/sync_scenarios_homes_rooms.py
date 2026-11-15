"""Scenarios of the guard of the change log for the apps `homes` and `rooms` (lot M3b).

Each scenario calls the code the application runs for that write. A row it needs before the
write is made straight in the database, so that only the code under test records a change.
`tests/test_sync_guard.py` loads every `sync_scenarios_*.py` of this folder.
"""

from collections.abc import Callable

from django.core.files.uploadedfile import SimpleUploadedFile

from housecairn.accounts.models import User
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Membership, Role
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Item, Photo, Room
from tests.rooms_support import image_bytes

Scenario = Callable[[Home], None]


def owner_of(home: Home) -> User:
    return Membership.objects.select_related("user").get(home=home, role=Role.OWNER).user


def room_in(home: Home) -> Room:
    return Room.objects.create(home=home, name="Hall")


def item_in(home: Home) -> Item:
    return Item.objects.create(home=home, room=room_in(home), name="Boiler")


def photo_of(home: Home, *, room: Room | None = None, item: Item | None = None) -> Photo:
    return Photo.objects.create(
        home=home,
        room=room,
        item=item,
        content_type="image/jpeg",
        size_bytes=10,
        width=4,
        height=3,
        image=f"{home.pk}/p.jpg",
        thumbnail=f"{home.pk}/p-thumb.jpg",
    )


def rename_home(home: Home) -> None:
    home_services.update_home(owner_of(home), home, name="Renamed")


def create_room(home: Home) -> None:
    room_services.create_room(owner_of(home), home, name="Hall")


def delete_room(home: Home) -> None:
    room_services.delete_room(owner_of(home), room_in(home))


def delete_room_with_items(home: Home) -> None:
    """The items of the room are soft-deleted by one queryset update."""
    item = item_in(home)
    room_services.delete_room(owner_of(home), item.room)


def create_item(home: Home) -> None:
    room_services.create_item(owner_of(home), room_in(home), name="Boiler")


def delete_item(home: Home) -> None:
    room_services.delete_item(owner_of(home), item_in(home))


def delete_item_with_photos(home: Home) -> None:
    """The photos of the item are soft-deleted by one queryset update."""
    item = item_in(home)
    photo_of(home, item=item)
    room_services.delete_item(owner_of(home), item)


def add_photo(home: Home) -> None:
    room_services.add_photo(
        owner_of(home),
        room=room_in(home),
        upload=SimpleUploadedFile("a.jpg", image_bytes(), content_type="image/jpeg"),
    )


def delete_photo(home: Home) -> None:
    room_services.delete_photo(owner_of(home), photo_of(home, room=room_in(home)))


SCENARIOS: dict[tuple[str, str], Scenario] = {
    ("homes.Home", "save"): rename_home,
    ("rooms.Room", "save"): create_room,
    ("rooms.Room", "soft_delete"): delete_room,
    ("rooms.Item", "save"): create_item,
    ("rooms.Item", "queryset_update"): delete_room_with_items,
    ("rooms.Item", "soft_delete"): delete_item,
    ("rooms.Photo", "save"): add_photo,
    ("rooms.Photo", "queryset_update"): delete_item_with_photos,
    ("rooms.Photo", "soft_delete"): delete_photo,
}

PURGE = (
    "the row leaves the table only through the retention purge, which removes its tombstone "
    "with it (tests/test_sync_retention.py, tests/test_sync_homes_rooms.py)"
)
NOT_APPLICABLE: dict[tuple[str, str], str] = {
    ("homes.Home", "queryset_update"): (
        "the only queryset update of homes writes `required_provider`, a rule of the instance "
        "that the sync API does not carry"
    ),
    ("homes.Home", "soft_delete"): "a home is never soft-deleted: it is erased for good",
    ("homes.Home", "hard_delete"): (
        "erasing a home erases its change log with it and writes its row in `sync_erasedhome`"
    ),
    ("rooms.Room", "queryset_update"): "a room is changed one at a time; no queryset updates rooms",
    ("rooms.Room", "hard_delete"): PURGE,
    ("rooms.Item", "hard_delete"): PURGE,
    ("rooms.Photo", "hard_delete"): PURGE,
}
