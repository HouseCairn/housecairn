"""What of a room, an item and a photo the sync API sends (docs/adr/0077, 0079)."""

from typing import Any

from housecairn.rooms.models import Photo
from housecairn.sync import rows


def photo_file(photo: Photo) -> dict[str, Any]:
    """The file reference of a photo, as ADR 0077 (need 10) writes it, without the digest.

    The phone's own id names a file it uploaded; a photo made on the web is named by its own
    id. The size, type and dimensions of the stored image go in the reference, not as fields.
    """
    return {
        "image": {
            "file": str(photo.pk),
            "size": photo.size_bytes,
            "type": photo.content_type,
            "width": photo.width,
            "height": photo.height,
        }
    }


def register() -> None:
    rows.register(
        Photo,
        rows.Spec(
            skip=frozenset({"content_type", "size_bytes", "width", "height"}),
            virtual=photo_file,
        ),
    )
