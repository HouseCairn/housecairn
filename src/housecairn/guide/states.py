"""Which steps of the guide are done: the evaluator of `done_when` (docs/adr/0084).

A step ticks when the state of the data of the example home that `guide.json` names is true:
an entity, a minimum number of rows and filters on fields. The vocabulary is small so that the
web and the mobile app evaluate it the same way (`tests/fixtures/guide_states.json` pins it).
A tick is stored as soon as its state is true, and it stays: the guide never goes backwards.

- `satisfied` and `ticked_after` are pure, over rows as `{entity, fields}`; the vectors run them.
- `rows_of` reads the rows of one home, and `tick` stores the ticks of the steps now true.
"""

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from django.apps import apps
from django.db import models

from housecairn.guide import content
from housecairn.guide.models import StepTick
from housecairn.homes.models import Home

Row = Mapping[str, Any]
HOME = "homes.home"


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def matches(row: Row, where: Mapping[str, Any]) -> bool:
    """Whether the fields of `row` satisfy every condition of `where`.

    A condition is a value (equality), `{"not": v}`, `{"gt": n}` (a number above `n`) or
    `{"set": true|false}` (a field with a value, or without one).
    """
    fields: Mapping[str, Any] = row["fields"]
    for name, wanted in where.items():
        value = fields.get(name)
        if isinstance(wanted, dict):
            ((operator, operand),) = wanted.items()
            if operator == "not":
                ok = value != operand
            elif operator == "gt":
                number, limit = _decimal(value), _decimal(operand)
                ok = number is not None and limit is not None and number > limit
            elif operator == "set":
                ok = (value not in (None, "", [])) is operand
            else:
                raise ValueError(f"Unknown condition: {operator!r}")
        else:
            ok = value == wanted
        if not ok:
            return False
    return True


def satisfied(predicate: Mapping[str, Any], rows: Iterable[Row]) -> bool:
    """The state that ticks a step: enough rows of the entity, each matching `where`."""
    where = predicate.get("where", {})
    found = sum(1 for row in rows if row["entity"] == predicate["entity"] and matches(row, where))
    return found >= int(predicate["min"])


def true_steps(rows: Sequence[Row]) -> list[str]:
    """The ids of the steps that have a state and whose state is true over `rows`."""
    return [
        step["id"]
        for step in content.steps()
        if "done_when" in step and satisfied(step["done_when"], rows)
    ]


def ticked_after(snapshots: Iterable[Sequence[Row]]) -> list[list[str]]:
    """The steps ticked after each snapshot of the rows, a tick staying once it is made."""
    stored: set[str] = set()
    after = []
    for rows in snapshots:
        stored |= set(true_steps(rows))
        after.append([step["id"] for step in content.steps() if step["id"] in stored])
    return after


def _wanted_fields() -> dict[str, set[str]]:
    """For each entity a state names, the fields its conditions read."""
    wanted: dict[str, set[str]] = {}
    for step in content.steps():
        predicate = step.get("done_when")
        if predicate is not None:
            wanted.setdefault(predicate["entity"], set()).update(predicate.get("where", {}))
    return wanted


def _value(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value


def rows_of(home: Home) -> list[dict[str, Any]]:
    """The rows of the home that the states read, as `{entity, fields}` (this home only)."""
    found: list[dict[str, Any]] = []
    for entity, names in _wanted_fields().items():
        if entity == HOME:
            found.append(
                {"entity": entity, "fields": {name: getattr(home, name) for name in names}}
            )
            continue
        model: type[models.Model] = apps.get_model(entity)
        manager: Any = model._default_manager
        for row in manager.filter(home=home).values("pk", *sorted(names)):
            row.pop("pk")
            found.append({"entity": entity, "fields": {k: _value(v) for k, v in row.items()}})
    return found


def ticked(home: Home) -> set[str]:
    """The steps of the home that are ticked."""
    return set(StepTick.objects.filter(home=home).values_list("step_id", flat=True))


def tick(home: Home) -> list[str]:
    """Store a tick for each step whose state is true now and that has none; the ids made.

    Only the example home has ticks. A tick is never removed: a step that was done stays done,
    even if its state goes away afterwards (a room deleted, a task marked done and then undone).
    """
    if not home.is_example:
        return []
    already = ticked(home)
    new = [step_id for step_id in true_steps(rows_of(home)) if step_id not in already]
    StepTick.objects.bulk_create(
        [StepTick(home=home, step_id=step_id) for step_id in new], ignore_conflicts=True
    )
    return new
