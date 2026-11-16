"""The guard of the change log: a write of a covered model that records no change fails.

ADR 0077 asks that every write path of a row of a home (a save, a queryset update, a soft
deletion, a hard deletion) adds a change to the feed, and that a test fails when one does not.
The lots M3b to M3e cover the apps one after the other (docs/adr/0079):

- add the app to `COVERED_APPS`, one line;
- add the writers of the app, one per model and kind, to a file of its own,
  `tests/sync_scenarios_<lot>.py`, that defines `SCENARIOS` and `NOT_APPLICABLE`; this module
  loads every such file, so that two lots never edit the same lines. A writer calls the code
  the application runs for that write and nothing else. A scenario that writes the table
  without adding a change fails; a covered model with a missing scenario fails; a kind that
  makes no sense for a model (the ledger of stock movements is never edited) goes to
  `NOT_APPLICABLE` with its reason.

The tests below prove that the guard fails the moment an app is listed without being covered.
"""

import importlib
import pathlib
import re
import sys
import uuid
from collections.abc import Callable, Iterable, Mapping

import pytest
from django.apps import apps
from django.db import connection, models, transaction

from housecairn.accounts.models import User
from housecairn.guide import services as guide_services
from housecairn.homes.models import Home
from housecairn.sync import changes
from housecairn.sync.models import Change, HomeHead, RowClocks
from tests.test_export_registry import cascades_from_home, project_models

pytestmark = pytest.mark.django_db

# The apps whose every write path records its change. One line per lot (M3b to M3e).
COVERED_APPS: tuple[str, ...] = (
    "homes",
    "rooms",
    "projects",
    "tasks",
    "stock",
    "reports",
)

# The apps whose rows are synced and the lot that covers each; none may be missing, so that a
# new app of rows of a home cannot slip by. Remove an app from here when it joins COVERED_APPS.
WAITING_FOR_ITS_LOT: dict[str, str] = {}

# Tables that hang off a home and are not rows of the protocol: said once, with the reason.
NOT_SYNCED = {
    "apitokens.ApiToken_homes": "which homes a token reaches",
    "apitokens.AppConnection_homes": "which homes a connected app reaches",
    "calendar.Feed": "the secret address of a calendar feed of the web",
    "events.Event": "the instance log of events",
    "guide.StepTick": "the steps of the guide ticked on the example home, which never leaves it",
    "homes.ExportJob": "an archive made for a download",
    "homes.ImportPreview": "a CSV file waiting for its confirmation",
    "homes.Invitation": "an invitation to a home, of the web",
    "homes.Membership": "the members of a home are its accounts, read only on the phone",
    "labels.Label": "a QR label of the web",
    "notifications.Delivery": "that a reminder was sent",
    "sync.Change": "the log itself",
    "sync.HomeHead": "the log itself",
    "sync.RowClocks": "the log itself",
    "sync.SyncFile": "the bytes of a file a phone uploaded; the row that refers to it syncs",
}

KINDS = ("save", "queryset_update", "soft_delete", "hard_delete")
LINK_KINDS = ("save", "hard_delete")

Scenario = Callable[[Home], None]
# (model label, kind) -> the write, and (model label, kind) -> why the kind does not apply to the
# model. Filled by the files `tests/sync_scenarios_*.py` of the lots M3b to M3e.
SCENARIOS: dict[tuple[str, str], Scenario] = {}
NOT_APPLICABLE: dict[tuple[str, str], str] = {}
for _file in sorted(pathlib.Path(__file__).parent.glob("sync_scenarios_*.py")):
    _scenarios = importlib.import_module(f"tests.{_file.stem}")
    SCENARIOS.update(_scenarios.SCENARIOS)
    NOT_APPLICABLE.update(_scenarios.NOT_APPLICABLE)


def home_models() -> list[type[models.Model]]:
    """The models of the rows of a home that the sync API carries."""
    return sorted(
        (
            model
            for model in project_models()
            if (model is Home or cascades_from_home(model)) and model._meta.label not in NOT_SYNCED
        ),
        key=lambda model: model._meta.label,
    )


def entity_of(model: type[models.Model]) -> str:
    """The entity type of the protocol: `<app>.<model>` in lower case."""
    return model._meta.label_lower


def kinds_of(model: type[models.Model]) -> tuple[str, ...]:
    return LINK_KINDS if model._meta.auto_created else KINDS


class _WriteSpy:
    """Notes whether a statement wrote a table (INSERT, UPDATE or DELETE)."""

    def __init__(self, table: str) -> None:
        self.pattern = re.compile(
            rf'\s*(?:INSERT (?:OR IGNORE )?INTO|UPDATE|DELETE FROM) "{re.escape(table)}"'
        )
        self.wrote = False

    def __call__(self, execute: Callable[..., object], sql: str, *args: object) -> object:
        if self.pattern.match(sql):
            self.wrote = True
        return execute(sql, *args)


def guard_problems(
    home: Home,
    covered_apps: Iterable[str],
    scenarios: Mapping[tuple[str, str], Scenario],
    not_applicable: Mapping[tuple[str, str], str],
    *,
    only: Iterable[type[models.Model]] | None = None,
) -> list[str]:
    """What is wrong with the coverage: a missing scenario, or a write with no change."""
    covered = set(covered_apps)
    problems = []
    for model in only if only is not None else home_models():
        label = model._meta.label
        if model._meta.app_label not in covered:
            continue
        for kind in kinds_of(model):
            if (label, kind) in not_applicable:
                continue
            scenario = scenarios.get((label, kind))
            if scenario is None:
                problems.append(f"{label} {kind}: no scenario")
                continue
            last = Change.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
            spy = _WriteSpy(model._meta.db_table)
            with transaction.atomic(), connection.execute_wrapper(spy):
                scenario(home)
            if not spy.wrote:
                problems.append(f"{label} {kind}: the scenario writes nothing to the table")
            elif not Change.objects.filter(
                pk__gt=last, home=home, entity=entity_of(model)
            ).exists():
                problems.append(f"{label} {kind}: the write records no change")
    return problems


# The guard on what the application covers today


def test_every_model_of_a_home_is_covered_or_waits_for_its_lot() -> None:
    """A table of rows of a home in an app that is neither covered nor planned fails here."""
    listed = set(COVERED_APPS) | set(WAITING_FOR_ITS_LOT)
    unplanned = sorted(
        model._meta.label for model in home_models() if model._meta.app_label not in listed
    )
    assert unplanned == [], (
        "These tables hang off a home and no lot covers them: add their app to "
        "COVERED_APPS (with its scenarios) or to WAITING_FOR_ITS_LOT, or list the table "
        "in NOT_SYNCED with the reason."
    )


def test_an_app_is_either_covered_or_waiting_never_both() -> None:
    assert set(COVERED_APPS).isdisjoint(WAITING_FOR_ITS_LOT)
    assert set(COVERED_APPS) | set(WAITING_FOR_ITS_LOT) <= {
        config.label for config in apps.get_app_configs()
    }


def test_the_lists_name_existing_tables_and_the_log_is_not_one_of_the_homes() -> None:
    labels = {model._meta.label for model in project_models()}
    assert set(NOT_SYNCED) <= labels
    assert not [m for m in home_models() if m._meta.app_label == "sync"]


def test_the_models_of_the_homes_are_listed() -> None:
    labels = {model._meta.label for model in home_models()}
    assert {"homes.Home", "rooms.Room", "tasks.Occurrence", "stock.Movement"} <= labels
    assert {"projects.Project_rooms", "stock.Article_rooms"} <= labels  # link tables too
    assert "homes.Membership" not in labels


def test_a_write_of_a_covered_model_records_a_change(alice_home: Home) -> None:
    """A write path of a covered model that skips `record_change` fails here."""
    problems = guard_problems(alice_home, COVERED_APPS, SCENARIOS, NOT_APPLICABLE)
    assert problems == [], "\n".join(problems)


def test_a_write_of_an_example_home_records_no_change(alice: User) -> None:
    """The same scenarios on an example home (docs/adr/0084): every one writes, none records.

    The rule lives in `changes.record_change`, which every recorder of a change goes through
    (`sync.rows`, `projects.changelog`, and in the sync API `pushing`, `repairs`, `retention`),
    so the writers of every covered app stay silent on an example home.
    """
    example = guide_services.create_example_home(alice)
    problems = []
    for model in home_models():
        label = model._meta.label
        if model._meta.app_label not in COVERED_APPS:
            continue
        for kind in kinds_of(model):
            scenario = SCENARIOS.get((label, kind))
            if (label, kind) in NOT_APPLICABLE or scenario is None:
                continue
            spy = _WriteSpy(model._meta.db_table)
            with transaction.atomic(), connection.execute_wrapper(spy):
                scenario(example)
            if not spy.wrote:
                problems.append(f"{label} {kind}: the scenario writes nothing to the table")
    assert problems == [], "\n".join(problems)
    assert not Change.objects.filter(home=example).exists()
    assert not HomeHead.objects.filter(home=example).exists()
    assert not RowClocks.objects.filter(home=example).exists()


# The guard itself: it fails when an app is listed without being covered


def test_an_app_listed_without_scenarios_fails_the_guard(alice_home: Home) -> None:
    problems = guard_problems(alice_home, ("rooms",), {}, {})
    assert "rooms.Room save: no scenario" in problems
    assert "rooms.Item hard_delete: no scenario" in problems
    assert not [p for p in problems if not p.startswith("rooms.")]


def test_adding_an_app_to_covered_apps_fails_the_guard_test(
    alice_home: Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test above, run as it is, fails once an app is listed that is not covered."""
    monkeypatch.setattr(sys.modules[__name__], "COVERED_APPS", ("rooms",))
    monkeypatch.setattr(sys.modules[__name__], "SCENARIOS", {})
    with pytest.raises(AssertionError, match=r"rooms\.Room save: no scenario"):
        test_a_write_of_a_covered_model_records_a_change(alice_home)


def test_a_write_that_records_no_change_fails_the_guard(alice_home: Home) -> None:
    from housecairn.rooms.models import Room

    uncovered = {("rooms.Room", "save"): lambda home: Room.objects.create(home=home, name="Hall")}
    problems = guard_problems(
        alice_home, ("rooms",), uncovered, _all_but({("rooms.Room", "save")}), only=[Room]
    )
    assert problems == ["rooms.Room save: the write records no change"]


def test_a_write_that_records_its_change_passes_the_guard(alice_home: Home) -> None:
    from housecairn.rooms.models import Room

    def covered(home: Home) -> None:
        room = Room.objects.create(home=home, name="Hall")
        changes.record_change(home, "rooms.room", room.pk, changes.PUT, {"name": "Hall"})

    problems = guard_problems(
        alice_home,
        ("rooms",),
        {("rooms.Room", "save"): covered},
        _all_but({("rooms.Room", "save")}),
        only=[Room],
    )
    assert problems == []


def test_a_change_of_another_entity_does_not_cover_the_write(alice_home: Home) -> None:
    from housecairn.rooms.models import Room

    def elsewhere(home: Home) -> None:
        Room.objects.create(home=home, name="Hall")
        changes.record_change(home, "rooms.item", uuid.uuid4(), changes.PUT, {"name": "x"})

    problems = guard_problems(
        alice_home,
        ("rooms",),
        {("rooms.Room", "save"): elsewhere},
        _all_but({("rooms.Room", "save")}),
        only=[Room],
    )
    assert problems == ["rooms.Room save: the write records no change"]


def test_a_change_of_another_home_does_not_cover_the_write(alice_home: Home) -> None:
    from housecairn.rooms.models import Room

    other = Home.objects.create(name="Other")

    def wrong_home(home: Home) -> None:
        Room.objects.create(home=home, name="Hall")
        changes.record_change(other, "rooms.room", uuid.uuid4(), changes.PUT, {"name": "x"})

    problems = guard_problems(
        alice_home,
        ("rooms",),
        {("rooms.Room", "save"): wrong_home},
        _all_but({("rooms.Room", "save")}),
        only=[Room],
    )
    assert problems == ["rooms.Room save: the write records no change"]


def test_a_scenario_that_writes_nothing_fails_the_guard(alice_home: Home) -> None:
    from housecairn.rooms.models import Room

    problems = guard_problems(
        alice_home,
        ("rooms",),
        {("rooms.Room", "save"): lambda home: None},
        _all_but({("rooms.Room", "save")}),
        only=[Room],
    )
    assert problems == ["rooms.Room save: the scenario writes nothing to the table"]


def test_a_kind_that_does_not_apply_is_skipped_with_its_reason(alice_home: Home) -> None:
    from housecairn.rooms.models import Room

    reasons = {(Room._meta.label, kind): "test" for kind in KINDS}
    assert guard_problems(alice_home, ("rooms",), {}, reasons, only=[Room]) == []


def _all_but(keep: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """Every kind of the room declared not applicable but the ones under test."""
    return {("rooms.Room", kind): "test" for kind in KINDS if ("rooms.Room", kind) not in keep}
