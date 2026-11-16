"""The version of the application, from one source: `version` in pyproject.toml.

Where the project is installed as a package (tests, a development environment), its
metadata carries that number. The container image does not install it (docs/adr/0015:
its source is on PYTHONPATH, so no unlocked build backend is downloaded), but it ships
pyproject.toml next to the source: the number is read there.
"""

import tomllib
from functools import cache
from importlib import metadata
from pathlib import Path

# src/housecairn/core/version.py -> the root of the checkout, /app in the image.
PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"

UNKNOWN = "dev"


@cache
def application_version() -> str:
    try:
        return metadata.version("housecairn")
    except metadata.PackageNotFoundError:
        return _from_pyproject(PYPROJECT)


def _from_pyproject(path: Path) -> str:
    try:
        with path.open("rb") as file:
            project = tomllib.load(file).get("project", {})
    except (OSError, tomllib.TOMLDecodeError):
        return UNKNOWN
    version = project.get("version")
    if project.get("name") != "housecairn" or not isinstance(version, str) or not version:
        return UNKNOWN
    return version
