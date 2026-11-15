"""The push and the change log, and the merge rules the push shares with the phone (lot M5a).

- The guard of the change log (`tests/test_sync_guard.py`) run with the push as the writer:
  every entity type a phone may push is written by `sync.pushing` through the helpers of the
  change log, and a write that records no change fails it.
- The merge vectors (`tests/fixtures/sync_merge.json`): the cases of the per-field rule of
  mobile ADR 0003, applied in every order, give the same row. The mobile repository has no
  merge vectors yet; this file publishes them, for it to pin as it pins `sync_ids.json`.
- Random histories of three devices over two homes, pushed in random orders and cut in random
  pushes, give the same snapshot (docs/adr/0077, "Consequences").
"""

import itertools
import json
import pathlib
import random
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from housecairn.accounts.models import User
from housecairn.homes.models import Home, Membership, Role
from housecairn.rooms import services as room_services
from housecairn.sync import changes, clock, pushing, reading
from housecairn.sync import rows as sync_rows
from housecairn.sync.models import RowClocks
from tests.data_support import fill_home
from tests.test_sync_guard import COVERED_APPS, guard_problems, home_models

pytestmark = pytest.mark.django_db
pytest_plugins = ("tests.rooms_fixtures",)

VECTORS = pathlib.Path(__file__).parent / "fixtures" / "sync_merge.json"


def later(seconds: int = 3600) -> str:
    return clock.format_clock(clock.make_clock(clock.wall_ms() + seconds * 1000))


# ---------- the guard, with the push as the writer ----------


def _line_of(home: Home, entity: str) -> dict[str, Any] | None:
    """A live row of `entity` in the home, as the snapshot gives it."""
    for line in reading.snapshot_lines(home):
        if line.get("t") == entity and line["f"].get(changes.DELETED_AT, [None])[0] is None:
            return line
    return None


def _put_every_field(home: Home, pusher: pushing.Pusher, line: dict[str, Any]) -> dict[str, Any]:
    """A `put` of every field a phone may write, with the values the row holds, and a later clock.

    Every field wins (the clock is after every stored one), so the push writes the row.
    """
    model = reading.model_of(line["t"])
    if reading.is_link(line["t"]):
        writable: set[str] = set(line["f"]) - {changes.DELETED_AT}
    else:
        writable = set(pushing.columns_of(model).writable)
    fields = {name: entry[0] for name, entry in line["f"].items() if name in writable}
    return {
        "t": line["t"],
        "h": str(home.pk),
        "id": line["id"],
        "k": "put",
        "c": later(),
        "f": fields,
    }


def push_scenarios(
    pusher_of: Callable[[Home], pushing.Pusher],
) -> tuple[dict[tuple[str, str], Callable[[Home], None]], dict[tuple[str, str], str]]:
    scenarios: dict[tuple[str, str], Callable[[Home], None]] = {}
    not_applicable: dict[tuple[str, str], str] = {}
    for model in home_models():
        label, entity = model._meta.label, model._meta.label_lower

        def apply(home: Home, *ops: dict[str, Any]) -> None:
            answer = pushing.apply(home, pusher_of(home), list(ops))
            assert all(r["status"] == "applied" for r in answer["results"]), answer

        if model._meta.auto_created:

            def link_save(home: Home, entity: str = entity) -> None:
                line = _line_of(home, entity)
                assert line is not None, entity
                common = {"t": entity, "h": str(home.pk), "id": line["id"]}
                apply(home, {**common, "k": "delete", "c": later(10)})
                apply(home, {**common, "k": "restore", "c": later(20)})

            def link_delete(home: Home, entity: str = entity) -> None:
                line = _line_of(home, entity)
                assert line is not None, entity
                apply(
                    home,
                    {
                        "t": entity,
                        "h": str(home.pk),
                        "id": line["id"],
                        "k": "delete",
                        "c": later(30),
                    },
                )

            scenarios[(label, "save")] = link_save
            scenarios[(label, "hard_delete")] = link_delete
            continue

        def save(home: Home, entity: str = entity) -> None:
            line = _line_of(home, entity)
            assert line is not None, entity
            apply(home, _put_every_field(home, pusher_of(home), line))

        def soft_delete(home: Home, entity: str = entity) -> None:
            line = _line_of(home, entity)
            assert line is not None, entity
            apply(
                home,
                {"t": entity, "h": str(home.pk), "id": line["id"], "k": "delete", "c": later(40)},
            )

        scenarios[(label, "save")] = save
        if model is Home:
            not_applicable[(label, "soft_delete")] = "a home is erased on the web, not pushed"
        else:
            scenarios[(label, "soft_delete")] = soft_delete
        not_applicable[(label, "queryset_update")] = "a push writes one row per operation"
        not_applicable[(label, "hard_delete")] = "a push deletes softly, with a tombstone"
    return scenarios, not_applicable


@pytest.fixture
def filled(media_root: Path, alice: User, alice_home: Home, make_user: Callable[..., User]) -> Home:
    member = make_user("member@example.com")
    Membership.objects.create(home=alice_home, user=member, role=Role.MEMBER)
    fill_home(alice, member, alice_home)
    return alice_home


def test_every_type_written_by_a_push_records_its_change(filled: Home, alice: User) -> None:
    """The guard of the change log, with `sync.pushing` as the only writer."""
    device = bytes(range(16))
    scenarios, not_applicable = push_scenarios(
        lambda home: pushing.Pusher(alice, Role.OWNER, device)
    )
    problems = guard_problems(filled, COVERED_APPS, scenarios, not_applicable)
    assert problems == [], "\n".join(problems)


def test_the_guard_fails_a_push_that_writes_without_its_change(
    filled: Home, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutant: the push writes the row by a plain `save()`, and records nothing."""

    def unrecorded(
        model: Any, home_id: Any, pk: Any, kind: str, fields: Any, values: Any, **_kw: Any
    ) -> Any:
        stored = model._base_manager.get(pk=pk)
        for name, value in values.items():
            setattr(stored, name, value)
        stored.save()
        return changes.Recorded(changes.Change(), {})

    monkeypatch.setattr(sync_rows, "apply_stamped", unrecorded)
    device = bytes(range(16))
    scenarios, not_applicable = push_scenarios(
        lambda home: pushing.Pusher(alice, Role.OWNER, device)
    )
    rooms = [m for m in home_models() if m._meta.label == "rooms.Room"]
    problems = guard_problems(filled, ("rooms",), scenarios, not_applicable, only=rooms)
    assert "rooms.Room save: the write records no change" in problems


# ---------- the merge vectors ----------


def _vectors() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(VECTORS.read_text(encoding="utf-8"))
    return data


def test_the_vectors_name_their_rules() -> None:
    data = _vectors()
    assert data["version"] == 1
    assert {case["rule"] for case in data["cases"]} >= {
        "later clock wins",
        "fields merge apart",
        "tie broken by device",
        "repeat changes nothing",
        "delete is a field",
        "restore needs a later clock",
        "unknown field kept",
    }


def _substitute(value: Any, names: dict[str, str]) -> Any:
    if isinstance(value, str) and value in names:
        return names[value]
    if isinstance(value, dict):
        return {k: _substitute(v, names) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, names) for v in value]
    return value


def _state(home: Home, row_id: uuid.UUID) -> dict[str, Any]:
    line = next(line for line in reading.snapshot_lines(home) if line.get("id") == str(row_id))
    return {name: entry[0] for name, entry in line["f"].items()}


@pytest.mark.parametrize("case", _vectors()["cases"], ids=lambda case: case["name"])
def test_a_merge_vector_gives_its_row_in_every_order(
    alice: User, alice_home: Home, case: dict[str, Any]
) -> None:
    data = _vectors()
    devices = {name: clock.device_from_b64(text) for name, text in data["devices"].items()}
    room = room_services.create_room(alice, alice_home, name="Start", kind="other")
    names = {"$home": str(alice_home.pk), "$row": str(room.pk)}
    ops = [(entry["device"], _substitute(entry["op"], names)) for entry in case["ops"]]
    seen: list[dict[str, Any]] = []
    for order in itertools.permutations(ops):
        sid = transaction_savepoint()
        for device, operation in order:
            pushing.apply(
                alice_home, pushing.Pusher(alice, Role.OWNER, devices[device]), [operation]
            )
        state = _state(alice_home, room.pk)
        seen.append(state)
        for name, value in case["expect"].items():
            assert state.get(name) == value, (case["name"], order, name)
        transaction_rollback(sid)
    assert all(state == seen[0] for state in seen)


def transaction_savepoint() -> str:
    from django.db import transaction

    return transaction.savepoint()


def transaction_rollback(sid: str) -> None:
    from django.db import transaction

    transaction.savepoint_rollback(sid)


# ---------- random histories ----------


def test_random_histories_in_random_orders_give_the_same_snapshot(
    alice: User, alice_home: Home, bob: User, make_user: Callable[..., User]
) -> None:
    """Three devices, two homes, random puts, deletes and restores, cut in random pushes."""
    from housecairn.homes import services as home_services

    second = home_services.create_home(alice, name="Second house")
    homes = [alice_home, second]
    rooms = {
        home.pk: [
            room_services.create_room(alice, home, name=f"Room {n}", kind="other") for n in range(3)
        ]
        for home in homes
    }
    devices = [bytes([n]) * 16 for n in (3, 7, 11)]
    rng = random.Random(20261115)  # noqa: S311 - a reproducible history, not a secret
    base = clock.wall_ms() + 60_000
    history: list[tuple[int, Home, dict[str, Any]]] = []
    for step in range(60):
        home = rng.choice(homes)
        room = rng.choice(rooms[home.pk])
        device = rng.randrange(len(devices))
        made = clock.format_clock(clock.make_clock(base + rng.randrange(0, 50), step))
        kind = rng.choice(["put", "put", "put", "delete", "restore"])
        operation: dict[str, Any] = {
            "t": "rooms.room",
            "h": str(home.pk),
            "id": str(room.pk),
            "k": kind,
            "c": made,
        }
        if kind == "put":
            field = rng.choice(["name", "notes", "position", "paint_brand", "moisture"])
            value: Any = rng.randrange(100) if field == "position" else f"{field} {step}"
            operation["f"] = {field: value}
        history.append((device, home, operation))

    snapshots = []
    for _attempt in range(4):
        order = history[:]
        rng.shuffle(order)
        sid = transaction_savepoint()
        while order:
            size = rng.randrange(1, 6)
            batch, order = order[:size], order[size:]
            for device, home, operation in batch:
                pushing.apply(home, pushing.Pusher(alice, Role.OWNER, devices[device]), [operation])
        snapshot = []
        for home in homes:
            for line in reading.snapshot_lines(home):
                if line.get("t") == "rooms.room":
                    snapshot.append(
                        (
                            line["id"],
                            sorted(
                                (k, v[0]) for k, v in line["f"].items() if k != changes.DELETED_AT
                            ),
                            line["f"].get(changes.DELETED_AT, [None])[0] is None,
                        )
                    )
        snapshots.append(sorted(snapshot))
        transaction_rollback(sid)
    assert all(snapshot == snapshots[0] for snapshot in snapshots)
    assert RowClocks.objects.filter(entity="rooms.room").count() == 6
