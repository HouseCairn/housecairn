"""Private storage of the files a phone uploaded, before a row refers to them (docs/adr/0077).

Files live under `MEDIA_ROOT/sync/<home id>/`, which no URL serves: they leave only through
`GET /api/v1/sync/homes/{h}/files/{file_id}`, after the checks of the sync API.
"""

from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from django.utils.functional import cached_property

FOLDER = "sync"


@deconstructible
class SyncFileStorage(FileSystemStorage):
    """`FileSystemStorage` on the current `MEDIA_ROOT`, readable by the service user only."""

    def __init__(self) -> None:
        super().__init__(file_permissions_mode=0o640, directory_permissions_mode=0o750)

    @cached_property
    def base_location(self) -> str:
        return str(Path(settings.MEDIA_ROOT) / FOLDER)

    @cached_property
    def location(self) -> str:
        return str(Path(self.base_location).resolve())

    @property
    def base_url(self) -> str | None:  # type: ignore[override]
        return None


sync_storage = SyncFileStorage()
