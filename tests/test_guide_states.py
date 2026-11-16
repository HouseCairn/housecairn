"""The evaluator of the steps of the guide, on the vectors the mobile app pins too (docs/adr/0084).

`tests/fixtures/guide_states.json` is a list of cases; a case is a sequence of snapshots of the
rows of the example home, each with the steps ticked after it. A tick is stored as soon as its
state is true, and it stays.
"""

import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from housecairn.accounts.models import User
from housecairn.guide import content, states
from housecairn.guide import services as guide_services
from housecairn.guide.models import StepTick
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.rooms import services as room_services
from tests.example_support import make_example

VECTORS = Path(__file__).parent / "fixtures" / "guide_states.json"


def cases() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]
    return found


CASES = pytest.mark.parametrize("case", cases(), ids=lambda case: case["name"])


@CASES
def test_a_step_ticks_the_first_time_its_state_is_true(case: dict[str, Any]) -> None:
    """After each snapshot the steps ticked are those whose state has been true so far."""
    seen: set[str] = set()
    for number, snapshot in enumerate(case["snapshots"]):
        seen |= set(states.true_steps(snapshot["rows"]))
        expected = [step["id"] for step in content.steps() if step["id"] in seen]
        assert snapshot["ticked"] == expected, (case["name"], number)
        # The first time a state is true is the snapshot that ticks it, not an earlier one.
        if number:
            before = set(case["snapshots"][number - 1]["ticked"])
            assert before <= set(snapshot["ticked"]), (case["name"], number)
    got = states.ticked_after([snapshot["rows"] for snapshot in case["snapshots"]])
    assert got == [snapshot["ticked"] for snapshot in case["snapshots"]]


@CASES
def test_a_tick_stays_when_its_state_goes_away(case: dict[str, Any]) -> None:
    """Ticks only grow along a case; where the rows lose a state, the step stays ticked."""
    ticked = [set(snapshot["ticked"]) for snapshot in case["snapshots"]]
    for earlier, later in itertools.pairwise(ticked):
        assert earlier <= later
    for number, snapshot in enumerate(case["snapshots"]):
        now = set(states.true_steps(snapshot["rows"]))
        assert now <= ticked[number]


def test_the_vectors_hold_the_cases_the_rule_is_pinned_by() -> None:
    names = {case["name"] for case in cases()}
    assert "marked done, then planned again: the tick stays" in names
    done_again = next(c for c in cases() if c["name"].startswith("marked done, then planned"))
    second, third = done_again["snapshots"][1], done_again["snapshots"][2]
    assert "recurring-task.3" in second["ticked"]
    assert not any(
        row["entity"] == "tasks.occurrence" and row["fields"]["status"] == "done"
        for row in third["rows"]
    )
    assert "recurring-task.3" in third["ticked"]
    # Every step that has a state is ticked by some case, and no step ticked by hand appears.
    with_state = {step["id"] for step in content.steps() if "done_when" in step}
    manual = {step["id"] for step in content.steps() if step.get("manual")}
    ticked = {step for case in cases() for snap in case["snapshots"] for step in snap["ticked"]}
    assert ticked == with_state
    assert not ticked & manual


def test_a_step_ticked_by_hand_is_never_ticked_by_the_evaluator() -> None:
    manual = {step["id"] for step in content.steps() if step.get("manual")}
    assert manual == {
        "create-home.2",
        "create-home.3",
        "recurring-task.4",
        "report-problem.2",
    }
    everything = [row for case in cases() for snap in case["snapshots"] for row in snap["rows"]]
    assert not set(states.true_steps(everything)) & manual


@pytest.mark.django_db
def test_the_evaluator_reads_the_rows_of_one_home_as_the_vectors_do(alice: User, bob: User) -> None:
    example = make_example(alice)
    # The rows of another home (a real home of the same person) tick nothing of this one.
    other = home_services.create_home(alice, name="Real")
    room_services.create_room(alice, other, name="Cellar")
    rows = states.rows_of(example)
    assert {row["entity"] for row in rows} == {
        "homes.home",
        "rooms.room",
        "rooms.item",
        "tasks.task",
        "tasks.occurrence",
        "reports.report",
        "projects.budgetline",
        "projects.quote",
        "projects.annualenvelope",
    } | {"projects.project"}
    assert len([r for r in rows if r["entity"] == "rooms.room"]) == 2
    assert states.true_steps(rows) == [
        step["id"] for step in content.steps() if "done_when" in step
    ]
    # The rows of the other home are its own: one room ticks 2.1, and nothing else of the example.
    assert states.true_steps(states.rows_of(other)) == ["rooms-equipment.1"]
    # Decimals travel as strings, as in the vectors.
    lines = [r for r in rows if r["entity"] == "projects.budgetline"]
    assert {r["fields"]["paid"] for r in lines} == {"480.00", "0.00"}


@pytest.mark.django_db
def test_a_tick_is_stored_once_and_stays_when_the_rows_go(alice: User) -> None:
    home = guide_services.create_example_home(alice)
    assert states.tick(home) == ["create-home.1"]
    assert states.tick(home) == []  # nothing new, nothing twice
    guide_services.fill_example(alice, home, up_to=3)
    assert "rooms-equipment.2" in states.ticked(home)
    from housecairn.rooms.models import Room

    for room in Room.objects.filter(home=home):
        room_services.delete_room(alice, room)
    assert states.tick(home) == []
    assert "rooms-equipment.2" in states.ticked(home)  # the guide never goes backwards
    assert StepTick.objects.filter(home=home, step_id="rooms-equipment.2").count() == 1


@pytest.mark.django_db
def test_a_real_home_has_no_tick(alice: User, alice_home: Home) -> None:
    room_services.create_room(alice, alice_home, name="Kitchen")
    assert states.tick(alice_home) == []
    assert not StepTick.objects.exists()
