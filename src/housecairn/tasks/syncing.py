"""What of an attachment the sync API sends (docs/adr/0077, 0079)."""

from typing import Any

from housecairn.sync import files, rows
from housecairn.tasks.models import Attachment


def attachment_file(attachment: Attachment) -> dict[str, Any]:
    """The file reference of an attachment, as ADR 0077 (need 10) writes it.

    As for a photo, a file a phone sent is named by the phone's id, an attachment made on the
    web by its own id, and the size, type and dimensions of the stored file go in the
    reference, not as fields.
    """
    return {"file": files.reference_of(attachment, files.HOLDERS["tasks.attachment"])}


def register() -> None:
    rows.register(
        Attachment,
        rows.Spec(
            skip=frozenset({"content_type", "size_bytes", "width", "height"}),
            virtual=attachment_file,
        ),
    )
