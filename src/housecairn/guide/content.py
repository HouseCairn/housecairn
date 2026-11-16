"""The content of the interactive guide: `guide.json`, read once (docs/adr/0084).

`design/guide.json` is the source, shared with the mobile app. The image carries no `design/`
directory, so the application reads the copy kept next to this file, which
`tests/test_guide_content.py` keeps byte for byte equal to the source.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings

PATH = Path(__file__).with_name("guide.json")
FALLBACK = "en"


@lru_cache(maxsize=1)
def document() -> dict[str, Any]:
    """The whole file."""
    loaded: dict[str, Any] = json.loads(PATH.read_text(encoding="utf-8"))
    return loaded


def language_in_file(code: str | None) -> str:
    """The language of the file that serves `code` (`fr-ca` reads `fr`), English by default."""
    base = (code or "").lower().replace("_", "-").split("-")[0]
    shipped = {code.split("-")[0] for code, _name in settings.LANGUAGES}
    return base if base in shipped and base in _languages() else FALLBACK


def _languages() -> set[str]:
    return set(document()["example"]["home"]["name"])


def text(value: Any, language: str) -> Any:
    """A value of the file in `language`: a text in two languages reads as one, any other as is."""
    if isinstance(value, dict) and set(value) == _languages():
        return value.get(language) or value[FALLBACK]
    return value


def scenarios() -> list[dict[str, Any]]:
    scenario_list: list[dict[str, Any]] = document()["scenarios"]
    return scenario_list


def steps() -> list[dict[str, Any]]:
    """Every step of every scenario, in order."""
    return [step for scenario in scenarios() for step in scenario["steps"]]


def example_home() -> dict[str, Any]:
    home: dict[str, Any] = document()["example"]["home"]
    return home


def example_rows() -> list[dict[str, Any]]:
    """The rows of the example home but the home itself, each `{key, entity, stage, fields}`."""
    rows: list[dict[str, Any]] = document()["example"]["rows"]
    return rows
