"""Where the files of a home are kept, and their erasure (docs/adr/0036).

Every app that stores files for a home keeps them in `MEDIA_ROOT/<folder>/<home id>/`
and declares its folder here, so that deleting the home cannot forget them, the way
`homes.exports` keeps an app from forgetting its data.
"""

import logging
import shutil
from pathlib import Path
from uuid import UUID

from django.conf import settings

logger = logging.getLogger("housecairn.files")

# `exports` holds the archives made in the background (homes.jobs).
MEDIA_FOLDERS: list[str] = ["homes", "exports"]


def register_media_folder(name: str) -> None:
    """Declare a folder of `MEDIA_ROOT` that holds one sub-directory per home."""
    if name not in MEDIA_FOLDERS:
        MEDIA_FOLDERS.append(name)


def home_directories(home_id: UUID, root: Path | None = None) -> list[Path]:
    """The directory of the home in each declared folder (they may not exist).

    `root`: another `MEDIA_ROOT`, for a backup being checked or restored elsewhere.
    """
    root = Path(settings.MEDIA_ROOT) if root is None else root
    return [root / folder / str(home_id) for folder in MEDIA_FOLDERS]


def _erase(directory: Path, folder: Path) -> None:
    # A symbolic link is removed as a link, never followed; anything that does not
    # resolve to a direct child of the folder is left alone.
    if directory.is_symlink():
        directory.unlink()
        return
    if not directory.exists():
        return
    if directory.resolve().parent != folder.resolve():
        raise ValueError("The directory of a home must sit directly in its folder.")
    shutil.rmtree(directory)


def erase_home_files(home_id: UUID) -> None:
    """Remove the files of a home. A failure is logged and never raised.

    Called once the deletion of the rows is committed: the home is gone either way,
    and a disk error must not turn a finished deletion into an error page. The
    log line carries the path, which holds the home identifier and nothing else.
    """
    root = Path(settings.MEDIA_ROOT)
    for folder, directory in zip(MEDIA_FOLDERS, home_directories(home_id), strict=True):
        try:
            _erase(directory, root / folder)
        except (OSError, ValueError):
            logger.error("Could not erase the files of a deleted home: %s", directory)
