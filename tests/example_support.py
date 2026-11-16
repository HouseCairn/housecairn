"""Helpers of the tests of the example home of the guide (docs/adr/0084)."""

from pathlib import Path

from housecairn.accounts.models import User
from housecairn.guide import services as guide_services
from housecairn.homes import files
from housecairn.homes.models import Home

# Every stage of the file: the home, then each scenario's rows.
ALL_STAGES = 6


def make_example(owner: User, *, language: str = "en") -> Home:
    """The example home of `owner` with every row of `guide.json`, made through the services."""
    home = guide_services.create_example_home(owner, language=language)
    guide_services.fill_example(owner, home, up_to=ALL_STAGES, language=language)
    return home


def write_a_file_in_every_folder(home: Home, root: Path) -> list[Path]:
    """One file in the directory of the home of each folder an app declares (`MEDIA_FOLDERS`)."""
    written = []
    for directory in files.home_directories(home.pk, root):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "a-file.bin"
        path.write_bytes(b"x" * 16)
        written.append(path)
    return written
