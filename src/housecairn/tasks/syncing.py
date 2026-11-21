"""What of an attachment, a stock draw and an occurrence the sync API sends (docs/adr/0077,
0079, 0090, 0091)."""

from typing import Any

from housecairn.sync import files, rows
from housecairn.tasks.models import Attachment, MaterialDraw, Occurrence


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
    # The unit price a draw was taken at is the server's (docs/adr/0090): a phone that pushes
    # it is refused (`forbidden`), and the server sets it when the draw is made or comes back.
    rows.register(MaterialDraw, rows.Spec(skip=frozenset({"unit_price"})))
    # So is the plan an occurrence keeps when it is marked done (docs/adr/0091): set by the
    # server when it becomes done, on the web or by a push, and emptied when it is not.
    rows.register(Occurrence, rows.Spec(skip=frozenset({"planned_labour", "planned_material"})))
