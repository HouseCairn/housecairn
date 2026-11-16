"""The interactive guide of design/guide.json: both languages, the glossary, the screens, the
states that tick a step and the example home (docs/adr/0084)."""

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps

from housecairn.homes.management.commands import load_demo_data as demo

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "design" / "guide.json"
GLOSSARY = ROOT / "design" / "glossary.json"
SCREENS = ROOT / "docs" / "screens"

LANGUAGES = ("en", "fr")
SCENARIOS = ["create-home", "rooms-equipment", "recurring-task", "report-problem", "project-budget"]
PLACEHOLDER = re.compile(r"\{\{([a-z0-9-]+)\}\}")
# Fields of the example rows that are dates worked out when the row is made, not columns.
DERIVED = {"due_offset_days", "completed_offset_days", "issued_offset_days", "valid_days"}
# Fields that the lot of the web side adds (ADR 0084): the test reads them as existing.
PLANNED = {("homes.home", "is_example")}
# Screens of docs/screens/ that only read: no form, nothing to write (the budget sets nothing).
READ_ONLY_SCREENS = {
    "budget",
    "budget-projects",
    "budget-rooms",
    "budget-tasks",
    "history",
    "audit",
}
# Words of the guide's sentences: nothing a household would not say.
JARGON = ("api", "uuid", "sync", "webhook", "htmx", "token", "json", "csv", "oauth")


def guide() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(GUIDE.read_text(encoding="utf-8"))
    return document


def steps() -> list[tuple[int, dict[str, Any]]]:
    """Every step with the number (1 to 5) of its scenario."""
    return [
        (number, step)
        for number, scenario in enumerate(guide()["scenarios"], start=1)
        for step in scenario["steps"]
    ]


def glossary_ids() -> set[str]:
    terms = json.loads(GLOSSARY.read_text(encoding="utf-8"))["terms"]
    return {term["id"] for term in terms}


def texts() -> list[tuple[str, dict[str, str]]]:
    """Every text of the guide with its place: titles, summaries and sentences."""
    found = []
    for scenario in guide()["scenarios"]:
        found.append((scenario["id"] + ".title", scenario["title"]))
        found.append((scenario["id"] + ".summary", scenario["summary"]))
        for step in scenario["steps"]:
            found.append((step["id"] + ".title", step["title"]))
            found.append((step["id"] + ".explain", step["explain"]))
    return found


def rows() -> list[dict[str, Any]]:
    """The rows of the example home, its home first, as {key, entity, stage, fields}."""
    example = guide()["example"]
    home = example["home"]
    first = {
        "key": home["key"],
        "entity": "homes.home",
        "stage": home["stage"],
        "fields": {"is_example": True, "name": home["name"], "currency": home["currency"]},
    }
    return [first, *example["rows"]]


def matches(row: dict[str, Any], where: dict[str, Any]) -> bool:
    for name, wanted in where.items():
        value = row["fields"].get(name)
        if isinstance(wanted, dict):
            ((operator, operand),) = wanted.items()
            if operator == "not":
                ok = value != operand
            elif operator == "gt":
                ok = value is not None and Decimal(str(value)) > Decimal(str(operand))
            elif operator == "set":
                ok = bool((value not in (None, "", [])) is operand)
            else:  # pragma: no cover - a mistake of the file, refused by the test of the states
                raise AssertionError(operator)
        else:
            ok = value == wanted
        if not ok:
            return False
    return True


def satisfied(predicate: dict[str, Any], available: list[dict[str, Any]]) -> bool:
    """The state that ticks a step: enough rows of the entity, each matching `where`."""
    found = [
        row
        for row in available
        if row["entity"] == predicate["entity"] and matches(row, predicate.get("where", {}))
    ]
    return len(found) >= int(predicate["min"])


def test_the_five_scenarios_in_order() -> None:
    assert guide()["version"] == 1
    assert [scenario["id"] for scenario in guide()["scenarios"]] == SCENARIOS


def test_step_ids_are_numbered_by_scenario() -> None:
    for scenario in guide()["scenarios"]:
        ids = [step["id"] for step in scenario["steps"]]
        assert ids == [f"{scenario['id']}.{n}" for n in range(1, len(ids) + 1)]
        assert any("done_when" in step for step in scenario["steps"]), scenario["id"]


TEXTS = pytest.mark.parametrize(
    ("place", "text"), texts(), ids=lambda value: value if isinstance(value, str) else ""
)


@TEXTS
def test_every_text_is_in_both_languages(place: str, text: dict[str, str]) -> None:
    assert set(text) == set(LANGUAGES), place
    for language in LANGUAGES:
        assert text[language], place
        assert text[language].strip() == text[language], place
    english, french = (PLACEHOLDER.findall(text[language]) for language in LANGUAGES)
    assert english == french, f"{place}: the two languages cite other terms"
    assert english == sorted(set(english), key=english.index), f"{place}: a term cited twice"
    for term in english:
        assert term in glossary_ids(), f"{place}: {{{{{term}}}}} is not a term of the glossary"


@TEXTS
def test_sentences_are_short_and_plain(place: str, text: dict[str, str]) -> None:
    limit = 60 if place.endswith(".title") else 130
    for language in LANGUAGES:
        written = text[language]
        assert len(written) <= limit, f"{place} ({language}): {len(written)} characters"
        lowered = written.lower()
        for word in JARGON:
            assert not re.search(rf"\b{word}\b", lowered), f"{place} ({language}): {word}"
        assert "\u2014" not in written, place
        assert "\u2013" not in written, place
        assert "{{" not in PLACEHOLDER.sub("", written), place


def test_every_screen_has_its_page() -> None:
    for _, step in steps():
        assert (SCREENS / f"{step['screen']}.md").is_file(), f"{step['id']}: {step['screen']}"


def test_a_read_only_screen_carries_no_state_and_no_pre_fill() -> None:
    """A screen that only reads is never where a row is written (the envelope: projects)."""
    for _, step in steps():
        if step["screen"] in READ_ONLY_SCREENS:
            assert "done_when" not in step, step["id"]
            assert "suggest" not in step, step["id"]


def test_a_step_is_ticked_by_a_state_or_by_hand() -> None:
    for _, step in steps():
        assert ("done_when" in step) != (step.get("manual") is True), step["id"]


def model_fields(entity: str) -> set[str]:
    model = apps.get_model(entity)
    return {field.name for field in model._meta.get_fields()} | {
        name for planned, name in PLANNED if planned == entity
    }


def choices(entity: str, name: str) -> set[str] | None:
    if (entity, name) in PLANNED:
        return None
    field = apps.get_model(entity)._meta.get_field(name)
    values = getattr(field, "choices", None)
    return {str(value) for value, _ in values} if values else None


def predicates() -> list[tuple[str, dict[str, Any]]]:
    return [(step["id"], step["done_when"]) for _, step in steps() if "done_when" in step]


@pytest.mark.parametrize(("step_id", "predicate"), predicates(), ids=[p[0] for p in predicates()])
def test_a_state_names_fields_and_values_of_the_models(
    step_id: str, predicate: dict[str, Any]
) -> None:
    assert set(predicate) <= {"entity", "min", "where"}, step_id
    entity = predicate["entity"]
    assert predicate["min"] >= 1, step_id
    for name, wanted in predicate.get("where", {}).items():
        assert name in model_fields(entity), f"{step_id}: {entity} has no {name}"
        if isinstance(wanted, dict):
            assert len(wanted) == 1, step_id
            assert set(wanted) <= {"not", "gt", "set"}, step_id
            wanted = wanted.get("not", wanted.get("gt", wanted.get("set")))
        allowed = choices(entity, name)
        if allowed is not None and isinstance(wanted, str):
            assert wanted in allowed, f"{step_id}: {name} is never {wanted}"


def test_example_rows_have_known_models_fields_and_choices() -> None:
    seen = set()
    for row in rows():
        assert row["key"] not in seen, row["key"]
        seen.add(row["key"])
        assert 1 <= row["stage"] <= 5, row["key"]
        fields = model_fields(row["entity"])
        for name, value in row["fields"].items():
            assert name in fields or name in DERIVED or name == "year", f"{row['key']}: {name}"
            if name in DERIVED or name == "year":
                continue
            allowed = choices(row["entity"], name)
            if allowed is not None and isinstance(value, str):
                assert value in allowed, f"{row['key']}: {name} = {value}"


def test_example_rows_point_to_rows_of_the_same_or_an_earlier_stage() -> None:
    by_key = {row["key"]: row for row in rows()}
    for row in rows():
        for name, value in row["fields"].items():
            for target in value if isinstance(value, list) else [value]:
                if isinstance(target, str) and target in by_key and name != "kind":
                    assert by_key[target]["stage"] <= row["stage"], f"{row['key']}.{name}"


def test_every_text_of_a_row_is_in_both_languages_and_amounts_are_euros() -> None:
    for row in rows():
        for name, value in row["fields"].items():
            if isinstance(value, dict):
                assert set(value) == set(LANGUAGES), f"{row['key']}.{name}"
                assert all(value[language] for language in LANGUAGES), f"{row['key']}.{name}"
            if name in {"planned", "paid", "amount"}:
                assert re.fullmatch(r"\d+(\.\d{1,2})?", value), f"{row['key']}.{name}"
    assert guide()["example"]["home"]["currency"] == "EUR"


def test_suggestions_name_rows_and_their_fields() -> None:
    by_key = {row["key"]: row for row in rows()}
    for _, step in steps():
        if "suggest" in step:
            assert step["suggest"] in by_key, step["id"]
            for name in step.get("suggest_fields", []):
                assert name in by_key[step["suggest"]]["fields"], f"{step['id']}: {name}"
        else:
            assert "suggest_fields" not in step, step["id"]


def test_the_whole_example_ticks_every_step_that_has_a_state() -> None:
    for _, step in steps():
        if "done_when" in step:
            assert satisfied(step["done_when"], rows()), step["id"]


def test_the_rows_of_earlier_scenarios_never_tick_a_step_of_the_next() -> None:
    """Adding what the earlier scenarios make (the 'catch up' of the guide) ticks nothing ahead."""
    for number, step in steps():
        if "done_when" in step:
            earlier = [row for row in rows() if row["stage"] < number]
            assert not satisfied(step["done_when"], earlier), step["id"]


def test_the_total_of_the_quotes_and_the_lines_adds_up() -> None:
    by_key = {row["key"]: row["fields"] for row in rows()}
    accepted = [r for r in by_key.values() if r.get("status") == "accepted" and "amount" in r]
    for quote in accepted:
        line = by_key[quote["budget_line"]]
        assert Decimal(quote["amount"]) <= Decimal(line["planned"])
    for line in (r for key, r in by_key.items() if key.startswith("line.")):
        assert Decimal(line["paid"]) <= Decimal(line["planned"])


def test_the_example_reuses_the_demo_home_where_it_has_the_same_row() -> None:
    """One value, one place: a row that the demo home also has says the same thing."""
    by_key = {row["key"]: row["fields"] for row in rows()}
    home = guide()["example"]["home"]
    assert home["name"] == demo.HOME_NAME
    assert by_key["room.kitchen"]["name"] == demo.ROOMS["kitchen"][1]
    assert by_key["room.bathroom"]["name"] == demo.ROOMS["bathroom"][1]
    assert by_key["item.machine"]["name"] == demo.MACHINE
    assert by_key["task.descaling"]["title"] == demo.DESCALING
    assert by_key["project.bathroom"]["title"] == demo.BATHROOM
    assert by_key["project.bathroom"]["goal"] == demo.BATHROOM_GOAL
    assert by_key["line.tiles"]["label"] == demo.BATHROOM_LINES[0][0]
    tiles = demo.BATHROOM_LINES[0]
    assert (by_key["line.tiles"]["planned"], by_key["line.tiles"]["paid"]) == tiles[2:]
    assert by_key["line.plumber"]["label"] == demo.BATHROOM_LINES[1][0]
    assert by_key["line.plumber"]["planned"] == demo.BATHROOM_LINES[1][2]
    for key, (names, amount, status, days) in zip(
        ("quote.linden", "quote.bath"), demo.QUOTES, strict=True
    ):
        quote = by_key[key]
        contractor = by_key[quote["contractor"]]
        assert contractor["name"] == names
        assert (quote["amount"], quote["status"], -quote["issued_offset_days"]) == (
            amount,
            status,
            days,
        )
    assert by_key["envelope"]["amount"] == demo.ENVELOPE
