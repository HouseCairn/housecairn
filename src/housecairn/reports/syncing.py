"""What of a report the sync API sends (docs/adr/0077, 0079).

The columns of the model, `created_at` included; the photo by the name of the stored file
until the lot of the files (M6) maps it to the reference of ADR 0077. Not sent: the columns
derived from the photo (its thumbnail, type and size).
"""

from housecairn.reports.models import Report
from housecairn.sync import rows


def register() -> None:
    rows.register(
        Report,
        rows.Spec(
            skip=frozenset({"thumbnail", "photo_content_type", "photo_size_bytes"}),
            send=frozenset({"created_at"}),
            file_names=frozenset({"photo"}),
        ),
    )
