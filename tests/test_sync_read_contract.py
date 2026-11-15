"""The answers of the sync API, reading, against the examples of the protocol, field by field.

`tests/fixtures/sync_read_examples.json` holds examples taken from the mobile protocol
(housecairn-mobile `docs/protocol.md`) and from the table of routes of ADR 0077. Each real
answer has the keys of its example, with values of the same kind (an HLC, a UUID, a device id,
a date and time, a number, a text, null), plus the keys the file lists as additions, and no
other.
"""

import datetime
import json
import pathlib
import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client

from housecairn.accounts.models import User
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.rooms import services as room_services
from housecairn.sync import clock
from housecairn.tasks import services as task_services
from tests.oauth_support import connect

pytestmark = pytest.mark.django_db

EXAMPLES = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "sync_read_examples.json").read_text("utf-8")
)
_HLC = re.compile(r"[0-9a-f]{16}")


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


def kind(value: Any) -> str:
    """What a value is, as the protocol tells values apart."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if _HLC.fullmatch(value):
        return "hlc"
    try:
        clock.device_from_b64(value)
        return "device"
    except ValueError:
        pass
    try:
        if str(uuid.UUID(value)) == value:
            return "uuid"
    except ValueError:
        pass
    try:
        datetime.datetime.fromisoformat(value)
        return "datetime"
    except ValueError:
        return "text"


def same_shape(
    real: Any, example: Any, where: str, additions: frozenset[str] = frozenset()
) -> None:
    """`real` has the keys of `example` (and `additions`), each value of the same kind."""
    if isinstance(example, dict):
        assert isinstance(real, dict), where
        extra = set(real) - set(example) - additions
        assert not extra, (where, extra)
        for key, value in example.items():
            assert key in real, (where, key)
            same_shape(real[key], value, f"{where}.{key}", additions)
        return
    if isinstance(example, list):
        assert isinstance(real, list), where
        assert real, f"{where}: an empty list proves nothing"
        for item in real:
            same_shape(item, example[0], f"{where}[]", additions)
        return
    assert kind(real) == kind(example), (where, real, example)


def fields_match(real: dict[str, Any], example: dict[str, Any], where: str) -> None:
    """Every field of the example is a field of the row, with a value of the same kind."""
    for name, value in example.items():
        assert name in real, (where, name)
        assert kind(real[name]) == kind(value), (where, name, real[name], value)


@pytest.fixture
def scene(alice: User, alice_home: Home) -> dict[str, Any]:
    room = room_services.create_room(alice, alice_home, name="Hall")
    task = task_services.create_task(
        alice, alice_home, title="Change the smoke alarm battery", room=room
    )
    shared = home_services.create_home(alice, name="Shared")
    phone = connect(alice, [alice_home, shared])
    home_services.delete_home(alice, shared, confirmation="Shared")
    return {"phone": phone, "room": room, "task": task, "home": alice_home}


def get(scene: dict[str, Any], path: str, **params: Any) -> Any:
    return Client().get(
        path, params, secure=True, HTTP_AUTHORIZATION=f"Bearer {scene['phone'].access}"
    )


def test_the_homes(scene: dict[str, Any]) -> None:
    case = EXAMPLES["homes"]
    body = get(scene, "/api/v1/sync/homes/").json()
    same_shape(body, case["example"], "", frozenset({"members"}))
    (home,) = body["homes"]
    assert set(home) == {*case["example"]["homes"][0], "members"}
    for member in home["members"]:
        assert set(member) == {"id", "name", "role"}
        assert kind(member["id"]) == "uuid"


def test_the_feed_and_its_operations(scene: dict[str, Any]) -> None:
    case = EXAMPLES["changes"]
    home = scene["home"]
    body = get(scene, f"/api/v1/sync/homes/{home.pk}/changes", limit=1000).json()
    same_shape(
        {**body, "changes": [{k: v for k, v in c.items() if k != "op"} for c in body["changes"]]},
        {**case["example"], "changes": [{"pos": 1}]},
        "",
        frozenset({"device"}),
    )
    for change in body["changes"]:
        assert set(change) == {"pos", "op", "device"}
        assert kind(change["device"]) == "device"
        op = change["op"]
        assert set(op) == set(EXAMPLES["operation"]["example"])
        for key in ("t", "h", "id", "k", "c"):
            assert kind(op[key]) == kind(EXAMPLES["operation"]["example"][key]), key
        assert op["k"] in ("put", "delete", "restore")
    (task_op,) = [
        c["op"]
        for c in body["changes"]
        if c["op"]["t"] == "tasks.task" and c["op"]["id"] == str(scene["task"].pk)
    ]
    fields_match(task_op["f"], EXAMPLES["operation"]["example"]["f"], "tasks.task")
    assert task_op["f"]["room_id"] == str(scene["room"].pk)


def test_the_snapshot(scene: dict[str, Any]) -> None:
    home = scene["home"]
    answer = get(scene, f"/api/v1/sync/homes/{home.pk}/snapshot")
    lines = [json.loads(line) for line in answer.content.decode().splitlines()]
    *rows, members, end = lines
    same_shape(end, EXAMPLES["snapshot_end"]["example"], "")
    assert set(members) == {"members"}
    row_example = EXAMPLES["snapshot_row"]["example"]
    for row in rows:
        assert set(row) == set(row_example), row
        for key in ("t", "h", "id"):
            assert kind(row[key]) == kind(row_example[key]), key
        for name, triple in row["f"].items():
            assert isinstance(triple, list), (row["t"], name)
            assert len(triple) == 3, (row["t"], name)
            assert kind(triple[1]) == "hlc", (row["t"], name)
            assert kind(triple[2]) == "device", (row["t"], name)
    (hall,) = [row for row in rows if row["id"] == str(scene["room"].pk)]
    assert kind(hall["f"]["name"]) == "array"
    for position, value in enumerate(row_example["f"]["name"][1:], start=1):
        assert kind(hall["f"]["name"][position]) == kind(value)


def test_the_errors(scene: dict[str, Any]) -> None:
    home = scene["home"]
    from housecairn.sync.models import HomeHead

    HomeHead.objects.filter(home=home).update(floor=1)
    expired = get(scene, f"/api/v1/sync/homes/{home.pk}/changes", after=0).json()
    gone_id = get(scene, "/api/v1/sync/homes/").json()["gone"][0]["id"]
    gone = get(scene, f"/api/v1/sync/homes/{gone_id}/changes").json()
    for real, example in zip((expired, gone), EXAMPLES["error"]["examples"], strict=True):
        assert set(real) == set(example)
        assert real["code"] == example["code"]
