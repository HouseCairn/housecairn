"""What of an attachment the sync API sends (docs/adr/0077, 0079)."""

from typing import Any

from housecairn.sync import rows
from housecairn.tasks.models import Attachment


def attachment_file(attachment: Attachment) -> dict[str, Any]:
    """The file reference of an attachment, as ADR 0077 (need 10) writes it, without the digest.

    As for a photo, an attachment made on the web is named by its own id, and the size, type and
    dimensions of the stored file go in the reference, not as fields.
    """
    return {
        "file": {
            "file": str(attachment.pk),
            "size": attachment.size_bytes,
            "type": attachment.content_type,
            "width": attachment.width,
            "height": attachment.height,
        }
    }


def register() -> None:
    rows.register(
        Attachment,
        rows.Spec(
            skip=frozenset({"content_type", "size_bytes", "width", "height"}),
            virtual=attachment_file,
        ),
    )
