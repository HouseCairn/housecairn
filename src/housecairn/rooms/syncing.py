"""What of a room, an item and a photo the sync API sends (docs/adr/0077, 0079)."""

from typing import Any

from housecairn.rooms.models import Photo
from housecairn.sync import files, rows


def photo_file(photo: Photo) -> dict[str, Any]:
    """The file reference of a photo, as ADR 0077 (need 10) writes it.

    A file a phone sent is named by the phone's id, with the digest of what it sent; a photo
    made on the web is named by its own id. The size, type and dimensions of the stored image
    go in the reference, not as fields.
    """
    return {"image": files.reference_of(photo, files.HOLDERS["rooms.photo"])}


def register() -> None:
    rows.register(
        Photo,
        rows.Spec(
            skip=frozenset({"content_type", "size_bytes", "width", "height"}),
            virtual=photo_file,
        ),
    )
