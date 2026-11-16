"""The content of the guide as the application reads it (docs/adr/0084)."""

from pathlib import Path

import pytest

from housecairn.guide import content

ROOT = Path(__file__).resolve().parent.parent


def test_the_copy_the_application_reads_is_the_source_byte_for_byte() -> None:
    """The image has no `design/`: the application reads a copy, kept equal to the source."""
    source = (ROOT / "design" / "guide.json").read_bytes()
    assert content.PATH.read_bytes() == source, (
        "src/housecairn/guide/guide.json differs from design/guide.json: "
        "cp design/guide.json src/housecairn/guide/guide.json"
    )


@pytest.mark.parametrize(
    ("asked", "served"),
    [
        ("fr", "fr"),
        ("fr-CA", "fr"),
        ("fr_FR", "fr"),
        ("en", "en"),
        ("en-gb", "en"),
        ("de", "en"),
        ("", "en"),
        (None, "en"),
    ],
)
def test_the_language_of_the_file_that_serves_a_language(asked: str | None, served: str) -> None:
    assert content.language_in_file(asked) == served


def test_a_text_in_two_languages_reads_as_one() -> None:
    both = {"en": "Kitchen", "fr": "Cuisine"}
    assert content.text(both, "fr") == "Cuisine"
    assert content.text(both, "en") == "Kitchen"
    assert content.text("repeat", "fr") == "repeat"
    assert content.text(3, "fr") == 3
    assert content.text({"a": 1}, "fr") == {"a": 1}


def test_the_example_rows_come_in_the_order_they_depend_on() -> None:
    seen: set[str] = set()
    for row in content.example_rows():
        for value in row["fields"].values():
            for target in value if isinstance(value, list) else [value]:
                if isinstance(target, str) and target.split(".")[0] in {
                    "room",
                    "item",
                    "task",
                    "project",
                    "line",
                    "contractor",
                }:
                    assert target in seen, (row["key"], target)
        seen.add(row["key"])
