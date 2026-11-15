"""The sync API, writing (lot M5a of docs/adr/0079): `POST /api/v1/sync/homes/{h}/push`.

The API is exposed to the phone: each test of security names the control it proves, and fails
without it (a home check removed, a scope ignored, a push applied twice, a device not fixed).
"""

import json
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone
from oauth2_provider.models import AccessToken

from housecairn.accounts.models import User
from housecairn.apitokens import connections as connection_services
from housecairn.apitokens import services as token_services
from housecairn.apitokens.models import AppConnection, Scope
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Membership, Role
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Room
from housecairn.stock import services as stock_services
from housecairn.sync import changes, clock, pushing, reading, retention
from housecairn.sync.models import Change, Push, RowClocks
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Occurrence, Task
from tests.oauth_support import Connected, connect

pytestmark = pytest.mark.django_db

DEVICE = clock.device_to_b64(bytes(range(16)))
OTHER_DEVICE = clock.device_to_b64(bytes(range(16, 32)))


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


def push_url(home_id: Any) -> str:
    return f"/api/v1/sync/homes/{home_id}/push"


def stamp(offset_ms: int = 0, counter: int = 0) -> str:
    """A clock near the wall clock, `offset_ms` after it."""
    return clock.format_clock(clock.make_clock(clock.wall_ms() + offset_ms, counter))


def op(
    home: Any, entity: str, row_id: Any, fields: dict[str, Any] | None = None, **kw: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "t": entity,
        "h": str(home),
        "id": str(row_id),
        "k": kw.pop("k", "put"),
        "c": kw.pop("c", stamp()),
    }
    if fields is not None:
        body["f"] = fields
    body.update(kw)
    return body


def send(
    token: str | None,
    home_id: Any,
    ops: list[Any] | None = None,
    *,
    number: int = 1,
    device: str = DEVICE,
    sv: int = 1,
    raw: bytes | None = None,
    content_type: str = "application/json",
    **extra: Any,
) -> Any:
    headers = {} if token is None else {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    if raw is None:
        raw = json.dumps({"push": number, "sv": sv, "device": device, "ops": ops or []}).encode()
    return Client().post(
        push_url(home_id), raw, content_type=content_type, secure=True, **headers, **extra
    )


@pytest.fixture
def phone(alice: User, alice_home: Home) -> Connected:
    return connect(alice, [alice_home])


@pytest.fixture
def room(alice: User, alice_home: Home) -> Room:
    return room_services.create_room(alice, alice_home, name="Kitchen", kind="kitchen")


def result_of(answer: Any, index: int = 0) -> dict[str, Any]:
    assert answer.status_code == 200, answer.content
    found: dict[str, Any] = answer.json()["results"][index]
    return found


# ---------- the credential and the home ----------


class TestCredential:
    def test_no_token_is_refused(self, alice_home: Home) -> None:
        answer = send(None, alice_home.pk)
        assert answer.status_code == 403
        assert answer.json()["code"] == "sync_needs_connection"

    @pytest.mark.parametrize("scope", [Scope.READ, Scope.WRITE, Scope.EVENTS])
    def test_an_api_token_is_refused(self, alice: User, alice_home: Home, scope: Scope) -> None:
        _token, raw = token_services.create_token(
            alice, name="t", scopes=[scope], homes=[alice_home], lifetime_days=30
        )
        answer = send(
            raw, alice_home.pk, [op(alice_home.pk, "homes.home", alice_home.pk, {"name": "X"})]
        )
        assert answer.status_code == 403
        assert answer.json()["code"] == "sync_needs_connection"
        assert Home.objects.get(pk=alice_home.pk).name == "Main house"

    def test_a_session_is_refused(self, alice: User, alice_home: Home) -> None:
        client = Client()
        client.force_login(alice)
        answer = client.post(
            push_url(alice_home.pk), "{}", content_type="application/json", secure=True
        )
        assert answer.status_code == 403
        assert answer.json()["code"] == "sync_needs_connection"

    def test_the_scope_sync_is_required(self, phone: Connected, alice_home: Home) -> None:
        """Mutant: a view that ignores the scope (TokenScopePermission left out) fails here."""
        AccessToken.objects.update(scope="homes.create")
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "homes.home", alice_home.pk, {"name": "X"})],
        )
        assert answer.status_code == 403
        assert Home.objects.get(pk=alice_home.pk).name == "Main house"

    def test_an_expired_token_is_refused(self, phone: Connected, alice_home: Home) -> None:
        AccessToken.objects.update(expires=timezone.now() - timedelta(seconds=1))
        assert send(phone.access, alice_home.pk).status_code == 401

    def test_a_revoked_connection_is_refused(
        self, phone: Connected, alice: User, alice_home: Home
    ) -> None:
        connection_services.revoke(phone.connection, actor=alice)
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "homes.home", alice_home.pk, {"name": "X"})],
        )
        assert answer.status_code == 401
        assert Home.objects.get(pk=alice_home.pk).name == "Main house"

    def test_plain_http_from_a_public_peer_is_refused(
        self, phone: Connected, alice_home: Home
    ) -> None:
        answer = Client().post(
            push_url(alice_home.pk),
            "{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {phone.access}",
            REMOTE_ADDR="203.0.113.9",
        )
        assert answer.status_code == 403
        assert answer.json()["code"] == "insecure_transport"

    def test_the_answer_says_its_version(self, phone: Connected, alice_home: Home) -> None:
        assert send(phone.access, alice_home.pk)["HC-Sync"] == "1"


class TestWhichHome:
    def test_a_home_of_another_household_is_404(self, phone: Connected, bob_home: Home) -> None:
        """Mutant: the home of the route not checked against the connection fails here."""
        answer = send(
            phone.access,
            bob_home.pk,
            [op(bob_home.pk, "homes.home", bob_home.pk, {"name": "Mine"})],
        )
        assert answer.status_code == 404
        assert Home.objects.get(pk=bob_home.pk).name == "Flat"
        assert not Change.objects.filter(
            home=bob_home, entity="homes.home", fields__name="Mine"
        ).exists()

    def test_an_unknown_home_is_404(self, phone: Connected) -> None:
        assert send(phone.access, uuid.uuid4()).status_code == 404

    def test_a_member_removed_is_404(
        self, alice: User, bob: User, bob_home: Home, add_member: Callable[..., Membership]
    ) -> None:
        add_member(bob_home, alice, Role.MEMBER)
        phone = connect(alice, [bob_home])
        Membership.objects.filter(home=bob_home, user=alice).delete()
        answer = send(
            phone.access,
            bob_home.pk,
            [op(bob_home.pk, "homes.home", bob_home.pk, {"name": "Mine"})],
        )
        assert answer.status_code == 404
        assert Home.objects.get(pk=bob_home.pk).name == "Flat"

    def test_an_erased_home_is_410_and_nothing_is_applied(
        self, phone: Connected, alice: User, alice_home: Home
    ) -> None:
        home_id = alice_home.pk
        home_services.erase_home(alice_home)
        answer = send(
            phone.access, home_id, [op(home_id, "rooms.room", uuid.uuid4(), {"name": "Hall"})]
        )
        assert answer.status_code == 410
        assert answer.json()["code"] == "home_gone"
        assert not Room.all_objects.filter(home_id=home_id).exists()
        assert not Push.objects.exists()


# ---------- the body ----------


class TestBody:
    @pytest.mark.parametrize(
        "raw",
        [
            b"{",
            b"[]",
            b"null",
            b'{"push": 1, "sv": 1, "device": "x", "ops": NaN}',
            b"\xff\xfe",
            b"[" * 100_000 + b"]" * 100_000,
            b'{"push": 1' + b"0" * 5000 + b"}",
        ],
    )
    def test_a_body_that_is_not_json_is_400(
        self, phone: Connected, alice_home: Home, raw: bytes
    ) -> None:
        answer = send(phone.access, alice_home.pk, raw=raw)
        assert answer.status_code == 400
        assert answer.json()["code"] == "malformed"

    @pytest.mark.parametrize(
        "body",
        [
            {"sv": 1, "device": DEVICE, "ops": []},
            {"push": 0, "sv": 1, "device": DEVICE, "ops": []},
            {"push": -1, "sv": 1, "device": DEVICE, "ops": []},
            {"push": True, "sv": 1, "device": DEVICE, "ops": []},
            {"push": "1", "sv": 1, "device": DEVICE, "ops": []},
            {"push": 2**64, "sv": 1, "device": DEVICE, "ops": []},
            {"push": 1, "device": DEVICE, "ops": []},
            {"push": 1, "sv": 1, "device": DEVICE, "ops": {}},
            {"push": 1, "sv": 1, "device": "short", "ops": []},
            {"push": 1, "sv": 1, "ops": []},
        ],
    )
    def test_an_envelope_out_of_shape_is_400(
        self, phone: Connected, alice_home: Home, body: dict[str, Any]
    ) -> None:
        answer = send(phone.access, alice_home.pk, raw=json.dumps(body).encode())
        assert answer.status_code == 400
        assert answer.json()["code"] == "malformed"

    def test_a_body_too_large_is_400(self, phone: Connected, alice_home: Home) -> None:
        name = "x" * (pushing.MAX_BODY_BYTES + 1)
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": name})],
        )
        assert answer.status_code == 400
        assert answer.json()["code"] == "too_large"
        assert not Room.all_objects.filter(home=alice_home).exists()

    def test_a_body_larger_than_django_accepts_is_400_not_500(
        self, phone: Connected, alice_home: Home
    ) -> None:
        raw = b'{"pad": "' + b"x" * (3 * 1024 * 1024) + b'"}'
        answer = send(phone.access, alice_home.pk, raw=raw)
        assert answer.status_code == 400
        assert answer.json()["code"] == "too_large"

    def test_too_many_operations_is_400(self, phone: Connected, alice_home: Home) -> None:
        ops = [
            op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "R"})
            for _ in range(pushing.MAX_OPS + 1)
        ]
        answer = send(phone.access, alice_home.pk, ops)
        assert answer.status_code == 400
        assert answer.json()["code"] == "too_large"

    def test_the_limit_itself_is_accepted(self, phone: Connected, alice_home: Home) -> None:
        ops = [
            op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "R"})
            for _ in range(pushing.MAX_OPS)
        ]
        answer = send(phone.access, alice_home.pk, ops)
        assert answer.status_code == 200
        assert Room.objects.filter(home=alice_home).count() == pushing.MAX_OPS

    def test_another_content_type_is_415(self, phone: Connected, alice_home: Home) -> None:
        answer = send(phone.access, alice_home.pk, raw=b"push=1", content_type="text/plain")
        assert answer.status_code == 415

    def test_a_schema_version_below_the_minimum_reads_but_does_not_write(
        self, phone: Connected, alice_home: Home
    ) -> None:
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "R"})],
            sv=0,
        )
        assert answer.status_code == 400
        assert answer.json()["code"] == "sync_version"
        assert not Room.objects.filter(home=alice_home).exists()

    @pytest.mark.parametrize(
        "bad",
        [
            "not an object",
            {"t": "rooms"},
            {"t": "Rooms.Room", "k": "put"},
            {"k": "move"},
            {"c": "0193A1B2C3D40001"},
            {"c": 12},
            {"id": "not-a-uuid"},
            {"id": "{12345678-1234-5678-1234-567812345678}"},
            {"h": None},
            {"f": {}},
            {"f": []},
            {"f": {"deleted_at": None}},
            {"f": {"Name": "x"}},
            {"f": {"name": "nul\x00"}},
            {"f": {"name": 12}},
            {"f": {"position": "3"}},
            {"f": {"position": True}},
            {"f": {"position": -1}},
            {"f": {"position": 2**70}},
            {"f": {"paint_article_id": "x"}},
            {"f": {"name": "x" * 1000}},
            {"k": "delete", "f": {"name": "x"}},
        ],
    )
    def test_a_malformed_operation_is_400_with_its_index_and_nothing_is_applied(
        self, phone: Connected, alice_home: Home, bad: Any
    ) -> None:
        good = op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "First"})
        if isinstance(bad, dict):
            wrong = op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "R"})
            wrong.update(bad)
            wrong = {k: v for k, v in wrong.items() if v is not None or k != "h"}
        else:
            wrong = bad
        answer = send(phone.access, alice_home.pk, [good, wrong])
        assert answer.status_code == 400, answer.content
        assert answer.json()["code"] == "malformed"
        assert answer.json()["index"] == 1
        assert not Room.all_objects.filter(home=alice_home).exists()
        assert not Change.objects.filter(home=alice_home, entity="rooms.room").exists()
        assert not Push.objects.exists()

    @pytest.mark.parametrize(
        "fields",
        [
            {"name": "Half \ud83d"},  # a known text column
            {"name": "R", "moisture_goal": "\udc00 cut"},  # a field the server does not know
            {"name": "R", "moisture_goal": {"\ud800": 1}},  # a key, in a value
            {"name": "R", "moisture_goal": ["ok", ["\ud800"]]},
            {"\ud800": "R"},  # the name of a field
        ],
    )
    def test_a_lone_surrogate_is_400_and_the_feed_stays_readable(
        self, phone: Connected, alice_home: Home, fields: dict[str, Any]
    ) -> None:
        """Mutant: texts not checked for UTF-8: the write fails (500), or `extra` keeps it and
        every later read of the feed and of the snapshot fails (500)."""
        good = op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "First"})
        wrong = op(alice_home.pk, "rooms.room", uuid.uuid4(), fields)
        answer = send(phone.access, alice_home.pk, [good, wrong])
        assert answer.status_code == 400, answer.content
        assert answer.json()["code"] == "malformed"
        assert answer.json()["index"] == 1
        assert not Room.all_objects.filter(home=alice_home).exists()
        assert not Push.objects.exists()
        headers = {"HTTP_AUTHORIZATION": f"Bearer {phone.access}"}
        base = f"/api/v1/sync/homes/{alice_home.pk}"
        assert Client().get(f"{base}/changes", secure=True, **headers).status_code == 200
        assert Client().get(f"{base}/snapshot", secure=True, **headers).status_code == 200

    @pytest.mark.parametrize("depth", [33, 905, 2000, 5000])
    @pytest.mark.parametrize("column", ["moisture_goal", "links"])
    def test_a_value_nested_too_deep_is_400_not_500(
        self, phone: Connected, alice_home: Home, room: Room, depth: int, column: str
    ) -> None:
        """Mutant: a recursive walk of the values fails with RecursionError (500) near 905
        levels; without the bound, a JSON column or `extra` keeps any depth."""
        nested = "[" * depth + "]" * depth
        entity, fields = (
            ("rooms.room", '{"name":"R","moisture_goal":%s}')
            if column == "moisture_goal"
            else ("rooms.item", '{"room_id":"' + str(room.pk) + '","name":"Boiler","links":%s}')
        )
        good = json.dumps(op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "First"}))
        wrong = json.dumps(op(alice_home.pk, entity, uuid.uuid4(), {})).replace(
            '"f": {}', '"f": ' + fields % nested
        )
        raw = ('{"push":1,"sv":1,"device":"%s","ops":[%s,%s]}' % (DEVICE, good, wrong)).encode()
        answer = send(phone.access, alice_home.pk, raw=raw)
        assert answer.status_code == 400, answer.content
        assert answer.json()["code"] == "malformed"
        assert answer.json()["index"] == 1
        assert not Push.objects.exists()

    def test_a_value_nested_to_the_bound_is_kept(
        self, phone: Connected, alice_home: Home
    ) -> None:
        nested: Any = []
        for _level in range(pushing.MAX_DEPTH - 1):
            nested = [nested]
        row_id = uuid.uuid4()
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "rooms.room", row_id, {"name": "R", "moisture_goal": nested})],
        )
        assert result_of(answer)["status"] == "applied"
        clocks = RowClocks.objects.get(entity="rooms.room", row_id=row_id)
        assert clocks.extra["moisture_goal"][0] == nested

    def test_bad_values_of_every_kind_are_400(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        task_id = uuid.uuid4()
        cases = [
            {"title": "T", "labour_budget": 12.5},
            {"title": "T", "labour_budget": "NaN"},
            {"title": "T", "labour_budget": "-sNaN"},
            {"title": "T", "labour_budget": "Infinity"},
            {"title": "T", "labour_budget": "abc"},
            {"title": "T", "labour_budget": "1" * 40},
            {"title": "T", "series_start": "2026-13-01"},
            {"title": "T", "series_start": "20260101"},
            {"title": "T", "repeat_interval": 1.5},
        ]
        for fields in cases:
            answer = send(
                phone.access, alice_home.pk, [op(alice_home.pk, "tasks.task", task_id, fields)]
            )
            assert answer.status_code == 400, fields
        assert not Task.all_objects.filter(pk=task_id).exists()

    def test_a_link_url_must_be_a_web_address(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        item = {"room_id": str(room.pk), "name": "Boiler", "contract_url": "javascript:alert(1)"}
        answer = send(
            phone.access, alice_home.pk, [op(alice_home.pk, "rooms.item", uuid.uuid4(), item)]
        )
        assert answer.status_code == 400

    def test_a_new_row_without_its_required_fields_is_400(
        self, phone: Connected, alice_home: Home
    ) -> None:
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "rooms.item", uuid.uuid4(), {"name": "Boiler"})],
        )
        assert answer.status_code == 400
        assert "room_id" in answer.json()["detail"]

    def test_a_write_that_breaks_a_rule_of_the_database_is_400_not_500(
        self, phone: Connected, alice: User, alice_home: Home
    ) -> None:
        """Two open occurrences of one task: lot M5b repairs it; M5a refuses the push."""
        task = task_services.create_task(alice, alice_home, title="Filter")
        answer = send(
            phone.access,
            alice_home.pk,
            [
                op(
                    alice_home.pk,
                    "tasks.occurrence",
                    uuid.uuid4(),
                    {"task_id": str(task.pk), "status": "planned"},
                )
            ],
        )
        assert answer.status_code == 400
        assert answer.json()["index"] == 0


# ---------- the per-field rule ----------


class TestMerge:
    def test_a_new_row_is_made_with_the_phone_id_and_its_clock(
        self, phone: Connected, alice_home: Home
    ) -> None:
        row_id, made = uuid.uuid4(), stamp()
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "rooms.room", row_id, {"name": "Hall", "position": 2}, c=made)],
        )
        assert result_of(answer) == {"i": 0, "status": "applied"}
        room = Room.objects.get(pk=row_id)
        assert (room.home_id, room.name, room.position) == (alice_home.pk, "Hall", 2)
        change = Change.objects.get(home=alice_home, row_id=row_id)
        assert (change.clock, bytes(change.device)) == (made, clock.device_from_b64(DEVICE))
        assert change.fields == {"name": "Hall", "position": 2}
        assert answer.json()["head"] == change.pos
        feed = reading.changes_after(alice_home.pk, change.pos - 1, 10)
        assert feed.changes[0]["device"] == DEVICE
        assert feed.changes[0]["op"]["c"] == made

    def test_an_older_write_loses_and_the_winner_is_returned(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        """Mutant: a push that writes every field without comparing clocks fails here."""
        later, earlier = stamp(600_000), stamp(300_000)
        first = [
            op(alice_home.pk, "rooms.room", room.pk, {"notes": "Phone A", "position": 1}, c=later)
        ]
        assert result_of(send(phone.access, alice_home.pk, first))["status"] == "applied"
        second = [
            op(
                alice_home.pk,
                "rooms.room",
                room.pk,
                {"notes": "Phone B", "paint_brand": "Acme"},
                c=earlier,
            )
        ]
        result = result_of(send(phone.access, alice_home.pk, second, number=2))
        assert result["status"] == "applied"
        assert result["lost"] == {"notes": ["Phone A", later, DEVICE]}
        assert result["other_version"] == {"notes": "Phone B"}
        room.refresh_from_db()
        assert (room.notes, room.position, room.paint_brand) == ("Phone A", 1, "Acme")

    def test_a_web_write_wins_over_an_older_phone_write(
        self, phone: Connected, alice: User, alice_home: Home, room: Room
    ) -> None:
        room_services.update_room(alice, room, name="Kitchen", notes="Web notes")
        web = RowClocks.objects.get(home=alice_home, entity="rooms.room", row_id=room.pk).clocks
        old = clock.format_clock(clock.make_clock(1_600_000_000_000))
        answer = send(
            phone.access,
            alice_home.pk,
            [
                op(
                    alice_home.pk,
                    "rooms.room",
                    room.pk,
                    {"notes": "Phone notes", "position": 7},
                    c=old,
                )
            ],
        )
        result = result_of(answer)
        assert result["status"] == "ignored"
        assert result["lost"]["notes"] == ["Web notes", *web["notes"]]
        assert result["lost"]["position"][0] == 0  # no clock of its own: its `updated_at`
        assert result["other_version"] == {"notes": "Phone notes"}
        room.refresh_from_db()
        assert (room.notes, room.position) == ("Web notes", 0)

    def test_a_write_where_every_field_loses_is_ignored(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        old = clock.format_clock(clock.make_clock(1_600_000_000_000))
        result = result_of(
            send(
                phone.access,
                alice_home.pk,
                [op(alice_home.pk, "rooms.room", room.pk, {"name": "Old"}, c=old)],
            )
        )
        assert result["status"] == "ignored"
        assert result["lost"]["name"][0] == "Kitchen"
        room.refresh_from_db()
        assert room.name == "Kitchen"

    def test_a_later_phone_write_beats_the_web(
        self, phone: Connected, alice: User, alice_home: Home, room: Room
    ) -> None:
        room_services.update_room(alice, room, name="Kitchen", notes="Web")
        result = result_of(
            send(
                phone.access,
                alice_home.pk,
                [op(alice_home.pk, "rooms.room", room.pk, {"notes": "Phone"}, c=stamp(60_000))],
            )
        )
        assert result == {"i": 0, "status": "applied"}
        room.refresh_from_db()
        assert room.notes == "Phone"

    def test_equal_clocks_are_broken_by_the_device_bytes(
        self, alice: User, alice_home: Home, room: Room
    ) -> None:
        made = stamp(60_000)
        low, high = bytes(16), b"\xff" * 16
        writer = pushing.Pusher(alice, Role.OWNER, high)
        pushing.apply(
            alice_home, writer, [op(alice_home.pk, "rooms.room", room.pk, {"name": "High"}, c=made)]
        )
        loser = pushing.Pusher(alice, Role.OWNER, low)
        answer = pushing.apply(
            alice_home, loser, [op(alice_home.pk, "rooms.room", room.pk, {"name": "Low"}, c=made)]
        )
        assert answer["results"][0]["status"] == "ignored"
        assert answer["results"][0]["lost"] == {"name": ["High", made, clock.device_to_b64(high)]}
        room.refresh_from_db()
        assert room.name == "High"

    def test_unknown_fields_are_kept_in_extra_and_given_back(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        made = stamp(60_000)
        answer = send(
            phone.access,
            alice_home.pk,
            [
                op(
                    alice_home.pk,
                    "rooms.room",
                    room.pk,
                    {"name": "Den", "humidity_goal": {"min": 40}},
                    c=made,
                )
            ],
        )
        assert result_of(answer)["status"] == "applied"
        rc = RowClocks.objects.get(home=alice_home, entity="rooms.room", row_id=room.pk)
        assert rc.extra == {"humidity_goal": [{"min": 40}, made, DEVICE]}
        line = next(
            line for line in reading.snapshot_lines(alice_home) if line.get("id") == str(room.pk)
        )
        assert line["f"]["humidity_goal"] == [{"min": 40}, made, DEVICE]
        change = Change.objects.filter(home=alice_home, row_id=room.pk).order_by("-pos").first()
        assert change is not None
        assert change.fields["humidity_goal"] == {"min": 40}
        older = clock.format_clock(clock.make_clock(1_600_000_000_000))
        lost = result_of(
            send(
                phone.access,
                alice_home.pk,
                [op(alice_home.pk, "rooms.room", room.pk, {"humidity_goal": 1}, c=older)],
                number=2,
            )
        )
        assert lost["lost"] == {"humidity_goal": [{"min": 40}, made, DEVICE]}

    def test_a_delete_then_an_older_put_does_not_bring_the_row_back(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        later, earlier = stamp(60_000), stamp(30_000)
        answer = send(
            phone.access,
            alice_home.pk,
            [
                op(alice_home.pk, "rooms.room", room.pk, k="delete", c=later),
                op(alice_home.pk, "rooms.room", room.pk, {"name": "Edited"}, c=earlier),
            ],
        )
        assert [r["status"] for r in answer.json()["results"]] == ["applied", "applied"]
        stored = Room.all_objects.get(pk=room.pk)
        assert stored.deleted_at is not None
        assert stored.name == "Edited"

    def test_a_restore_with_a_later_clock_brings_it_back_an_older_one_does_not(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        deleted, before, after = stamp(60_000), stamp(30_000), stamp(90_000)
        send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "rooms.room", room.pk, k="delete", c=deleted)],
        )
        older = result_of(
            send(
                phone.access,
                alice_home.pk,
                [op(alice_home.pk, "rooms.room", room.pk, k="restore", c=before)],
                number=2,
            )
        )
        assert older["status"] == "ignored"
        assert older["lost"]["deleted_at"][1:] == [deleted, DEVICE]
        assert Room.all_objects.get(pk=room.pk).deleted_at is not None
        later = result_of(
            send(
                phone.access,
                alice_home.pk,
                [op(alice_home.pk, "rooms.room", room.pk, k="restore", c=after)],
                number=3,
            )
        )
        assert later["status"] == "applied"
        assert Room.all_objects.get(pk=room.pk).deleted_at is None

    def test_a_delete_of_a_row_the_server_never_had_is_ignored(
        self, phone: Connected, alice_home: Home
    ) -> None:
        row_id = uuid.uuid4()
        result = result_of(
            send(phone.access, alice_home.pk, [op(alice_home.pk, "rooms.room", row_id, k="delete")])
        )
        assert result == {"i": 0, "status": "ignored"}
        assert not RowClocks.objects.filter(row_id=row_id).exists()

    def test_values_of_every_kind_are_stored_and_logged_as_the_protocol_writes_them(
        self, phone: Connected, alice: User, alice_home: Home, room: Room
    ) -> None:
        task_id = uuid.uuid4()
        fields = {
            "title": "Gutters",
            "room_id": str(room.pk),
            "labour_budget": "12.5",
            "estimated_minutes": 30,
            "series_start": "2027-03-01",
            "assignee_id": str(alice.pk),
            "notes": "",
            "priority": "later",
        }
        result = result_of(
            send(phone.access, alice_home.pk, [op(alice_home.pk, "tasks.task", task_id, fields)])
        )
        assert result["status"] == "applied"
        task = Task.objects.get(pk=task_id)
        assert str(task.labour_budget) == "12.50"
        assert task.series_start.isoformat() == "2027-03-01"
        assert task.priority == "later"  # kept as is (mobile ADR 0003)
        change = Change.objects.get(home=alice_home, row_id=task_id)
        assert change.fields["labour_budget"] == "12.50"

    def test_an_instant_keeps_the_phone_string(
        self, phone: Connected, alice: User, alice_home: Home
    ) -> None:
        article_id, made = uuid.uuid4(), stamp(0, counter=7)
        result = result_of(
            send(
                phone.access,
                alice_home.pk,
                [
                    op(
                        alice_home.pk,
                        "stock.article",
                        article_id,
                        {"name": "Filter", "created_at": made},
                    )
                ],
            )
        )
        assert result["status"] == "applied"
        from housecairn.stock.models import Article

        article = Article.objects.get(pk=article_id)
        assert clock.clock_of_instant(article.created_at) >> 16 == clock.parse_clock(made) >> 16
        assert Change.objects.get(row_id=article_id).fields["created_at"] == made

    def test_a_file_reference_is_kept_until_the_files_lot(
        self, phone: Connected, alice_home: Home
    ) -> None:
        """M6 maps file references; until then a phone's reference is a field kept in `extra`."""
        from housecairn.stock.models import Article

        article_id = uuid.uuid4()
        reference = {
            "file": str(uuid.uuid4()),
            "sha256": "ab" * 32,
            "size": 10,
            "type": "image/jpeg",
        }
        fields = {"name": "Filter", "label": reference}
        result = result_of(
            send(
                phone.access,
                alice_home.pk,
                [op(alice_home.pk, "stock.article", article_id, fields)],
            )
        )
        assert result["status"] == "applied"
        assert not Article.objects.get(pk=article_id).label  # no stored file, no path written
        change = Change.objects.get(row_id=article_id)
        feed = reading.changes_after(alice_home.pk, change.pos - 1, 1)
        assert feed.changes[0]["op"]["f"]["label"] == reference
        line = next(
            line for line in reading.snapshot_lines(alice_home) if line.get("id") == str(article_id)
        )
        assert line["f"]["label"][0] == reference

    def test_completed_by_is_the_account_of_the_phone(
        self,
        alice: User,
        bob: User,
        alice_home: Home,
        add_member: Callable[..., Membership],
    ) -> None:
        add_member(alice_home, bob, Role.MEMBER)
        task = task_services.create_task(alice, alice_home, title="Filter")
        occurrence = Occurrence.objects.get(task=task)
        phone = connect(bob, [alice_home])
        fields = {"completed_by_id": str(alice.pk), "completion_note": "Done"}
        result = result_of(
            send(
                phone.access,
                alice_home.pk,
                [op(alice_home.pk, "tasks.occurrence", occurrence.pk, fields, c=stamp(60_000))],
            )
        )
        assert result["status"] == "applied"
        occurrence.refresh_from_db()
        assert occurrence.completed_by_id == bob.pk


# ---------- the results and their codes ----------


class TestCodes:
    def test_forbidden_archived_at_by_a_member_who_is_not_an_owner(
        self, alice: User, bob: User, alice_home: Home, add_member: Callable[..., Membership]
    ) -> None:
        """The whole operation is rejected: its other fields are not written either."""
        add_member(alice_home, bob, Role.MEMBER)
        phone = connect(bob, [alice_home])
        made = stamp(60_000)
        answer = send(
            phone.access,
            alice_home.pk,
            [
                op(
                    alice_home.pk,
                    "homes.home",
                    alice_home.pk,
                    {"archived_at": made, "name": "Renamed"},
                    c=made,
                )
            ],
        )
        assert result_of(answer) == {"i": 0, "status": "rejected", "code": "forbidden"}
        home = Home.objects.get(pk=alice_home.pk)
        assert (home.archived_at, home.name) == (None, "Main house")

    def test_an_owner_archives_and_the_phone_string_comes_back(
        self, phone: Connected, alice: User, alice_home: Home
    ) -> None:
        made = stamp(60_000, counter=3)
        result = result_of(
            send(
                phone.access,
                alice_home.pk,
                [op(alice_home.pk, "homes.home", alice_home.pk, {"archived_at": made}, c=made)],
            )
        )
        assert result["status"] == "applied"
        home = Home.objects.get(pk=alice_home.pk)
        assert home.archived_at is not None
        assert clock.clock_of_instant(home.archived_at) == clock.parse_clock(made) >> 16 << 16
        assert reading.home_entry(home, alice)["archived_at"] == made
        assert Change.objects.filter(home=home, entity="homes.home").order_by(
            "-pos"
        ).first().fields == {"archived_at": made}  # type: ignore[union-attr]

    def test_a_member_renames_the_home_as_on_the_web(
        self, alice: User, bob: User, alice_home: Home, add_member: Callable[..., Membership]
    ) -> None:
        add_member(alice_home, bob, Role.MEMBER)
        phone = connect(bob, [alice_home])
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "homes.home", alice_home.pk, {"name": "Renamed"}, c=stamp(60_000))],
        )
        assert result_of(answer)["status"] == "applied"
        assert Home.objects.get(pk=alice_home.pk).name == "Renamed"

    def test_forbidden_for_a_read_only_member(
        self, alice: User, bob: User, alice_home: Home, add_member: Callable[..., Membership]
    ) -> None:
        add_member(alice_home, bob, Role.READ_ONLY)
        phone = connect(bob, [alice_home])
        ops = [
            op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "Hall"}),
            op(alice_home.pk, "homes.home", alice_home.pk, {"name": "Renamed"}, c=stamp(60_000)),
        ]
        answer = send(phone.access, alice_home.pk, ops)
        assert [r["code"] for r in answer.json()["results"]] == ["forbidden", "forbidden"]
        assert not Room.objects.filter(home=alice_home).exists()
        assert Home.objects.get(pk=alice_home.pk).name == "Main house"

    @pytest.mark.parametrize(
        "field", ["required_provider", "visible_from", "id", "home_id", "updated_at", "thumbnail"]
    )
    def test_forbidden_for_a_field_of_the_server(
        self, phone: Connected, alice_home: Home, field: str
    ) -> None:
        entity = (
            "homes.home"
            if field in ("required_provider", "id", "updated_at")
            else "tasks.occurrence"
        )
        entity = "reports.report" if field == "thumbnail" else entity
        entity = "rooms.room" if field == "home_id" else entity
        row_id = alice_home.pk if entity == "homes.home" else uuid.uuid4()
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, entity, row_id, {field: "x"}, c=stamp(60_000))],
        )
        assert result_of(answer)["code"] == "forbidden"
        assert Home.objects.get(pk=alice_home.pk).required_provider == ""

    def test_forbidden_to_delete_a_home(self, phone: Connected, alice_home: Home) -> None:
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "homes.home", alice_home.pk, k="delete", c=stamp(60_000))],
        )
        assert result_of(answer)["code"] == "forbidden"
        assert Home.objects.filter(pk=alice_home.pk).exists()

    @pytest.mark.parametrize(
        "entity", ["family.member", "family.settings", "garden.plant", "sync.change"]
    )
    def test_unknown_type(self, phone: Connected, alice_home: Home, entity: str) -> None:
        answer = send(
            phone.access, alice_home.pk, [op(alice_home.pk, entity, uuid.uuid4(), {"name": "x"})]
        )
        assert result_of(answer) == {"i": 0, "status": "rejected", "code": "unknown_type"}

    def test_wrong_home(self, phone: Connected, alice_home: Home, bob_home: Home) -> None:
        ops = [
            op(bob_home.pk, "rooms.room", uuid.uuid4(), {"name": "Hall"}),
            op(alice_home.pk, "homes.home", bob_home.pk, {"name": "Mine"}),
        ]
        answer = send(phone.access, alice_home.pk, ops)
        assert [r["code"] for r in answer.json()["results"]] == ["wrong_home", "wrong_home"]
        assert not Room.objects.filter(home=bob_home).exists()
        assert Home.objects.get(pk=bob_home.pk).name == "Flat"

    def test_invalid_reference_to_a_row_of_another_home(
        self, phone: Connected, alice_home: Home, bob: User, bob_home: Home
    ) -> None:
        """Mutant: a reference not checked against the home fails here."""
        theirs = room_services.create_room(bob, bob_home, name="Theirs", kind="kitchen")
        item = {"room_id": str(theirs.pk), "name": "Boiler"}
        answer = send(
            phone.access, alice_home.pk, [op(alice_home.pk, "rooms.item", uuid.uuid4(), item)]
        )
        assert result_of(answer) == {"i": 0, "status": "rejected", "code": "invalid_reference"}

    def test_invalid_reference_to_an_unknown_row_and_to_an_account_not_member(
        self, phone: Connected, alice_home: Home, room: Room, bob: User
    ) -> None:
        ops = [
            op(
                alice_home.pk,
                "rooms.item",
                uuid.uuid4(),
                {"room_id": str(uuid.uuid4()), "name": "B"},
            ),
            op(
                alice_home.pk,
                "tasks.task",
                uuid.uuid4(),
                {"title": "T", "assignee_id": str(bob.pk)},
            ),
        ]
        answer = send(phone.access, alice_home.pk, ops)
        assert [r["code"] for r in answer.json()["results"]] == ["invalid_reference"] * 2

    def test_a_reference_to_a_row_made_earlier_in_the_push_is_valid(
        self, phone: Connected, alice_home: Home
    ) -> None:
        room_id = uuid.uuid4()
        ops = [
            op(alice_home.pk, "rooms.room", room_id, {"name": "Hall"}),
            op(
                alice_home.pk,
                "rooms.item",
                uuid.uuid4(),
                {"room_id": str(room_id), "name": "Boiler"},
            ),
        ]
        answer = send(phone.access, alice_home.pk, ops)
        assert [r["status"] for r in answer.json()["results"]] == ["applied", "applied"]

    def test_id_taken_by_a_row_of_another_home(
        self, phone: Connected, alice_home: Home, bob: User, bob_home: Home
    ) -> None:
        """Mutant: rows keyed by home and id instead of id alone fail here."""
        theirs = room_services.create_room(bob, bob_home, name="Theirs", kind="kitchen")
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "rooms.room", theirs.pk, {"name": "Mine"}, c=stamp(60_000))],
        )
        assert result_of(answer) == {"i": 0, "status": "rejected", "code": "id_taken"}
        theirs.refresh_from_db()
        assert (theirs.home_id, theirs.name) == (bob_home.pk, "Theirs")
        delete = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "rooms.room", theirs.pk, k="delete", c=stamp(60_000))],
            number=2,
        )
        assert result_of(delete)["code"] == "id_taken"
        assert Room.objects.filter(pk=theirs.pk).exists()

    def test_id_taken_by_a_row_of_another_table_and_by_another_home_itself(
        self, phone: Connected, alice_home: Home, room: Room, bob_home: Home
    ) -> None:
        ops = [
            op(alice_home.pk, "tasks.task", room.pk, {"title": "Same id"}),
            op(alice_home.pk, "rooms.room", bob_home.pk, {"name": "Home id"}),
            op(alice_home.pk, "rooms.room", alice_home.pk, {"name": "My home id"}),
        ]
        answer = send(phone.access, alice_home.pk, ops)
        assert [r["code"] for r in answer.json()["results"]] == ["id_taken"] * 3

    def test_id_taken_within_one_push(self, phone: Connected, alice_home: Home) -> None:
        row_id = uuid.uuid4()
        ops = [
            op(alice_home.pk, "rooms.room", row_id, {"name": "Hall"}),
            op(alice_home.pk, "tasks.task", row_id, {"title": "Same id"}),
        ]
        answer = send(phone.access, alice_home.pk, ops)
        assert [r.get("code") for r in answer.json()["results"]] == [None, "id_taken"]

    def test_id_taken_does_not_say_where(
        self, phone: Connected, alice_home: Home, bob: User, bob_home: Home
    ) -> None:
        theirs = room_services.create_room(bob, bob_home, name="Theirs", kind="kitchen")
        answer = send(
            phone.access, alice_home.pk, [op(alice_home.pk, "rooms.room", theirs.pk, {"name": "x"})]
        )
        text = answer.content.decode()
        assert str(bob_home.pk) not in text
        assert "Theirs" not in text
        assert "Flat" not in text


# ---------- link tables ----------


class TestLinks:
    def _article(self, alice: User, home: Home) -> Any:
        return stock_services.create_article(alice, home, name="Filter")

    def test_a_link_is_made_removed_and_restored(
        self, phone: Connected, alice: User, alice_home: Home, room: Room
    ) -> None:
        article = self._article(alice, alice_home)
        entity = "stock.article_rooms"
        from housecairn.sync import ids

        link_id = ids.link(alice_home.pk, entity, article.pk, room.pk)
        fields = {"article_id": str(article.pk), "room_id": str(room.pk)}
        made = send(
            phone.access, alice_home.pk, [op(alice_home.pk, entity, link_id, fields, c=stamp(1000))]
        )
        assert result_of(made)["status"] == "applied"
        assert list(article.rooms.all()) == [room]
        removed = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, entity, link_id, k="delete", c=stamp(2000))],
            number=2,
        )
        assert result_of(removed)["status"] == "applied"
        assert list(article.rooms.all()) == []
        again = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, entity, link_id, fields, c=stamp(3000))],
            number=3,
        )
        assert result_of(again)["status"] == "applied"
        assert list(article.rooms.all()) == []  # a put never brings a removed row back
        restored = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, entity, link_id, k="restore", c=stamp(4000))],
            number=4,
        )
        assert result_of(restored)["status"] == "applied"
        assert list(article.rooms.all()) == [room]

    def test_a_link_whose_id_does_not_name_its_rows_is_rejected(
        self, phone: Connected, alice: User, alice_home: Home, room: Room
    ) -> None:
        article = self._article(alice, alice_home)
        fields = {"article_id": str(article.pk), "room_id": str(room.pk)}
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "stock.article_rooms", uuid.uuid4(), fields)],
        )
        assert result_of(answer)["code"] == "invalid_reference"
        assert list(article.rooms.all()) == []

    def test_a_link_to_a_row_of_another_home_is_rejected(
        self, phone: Connected, alice: User, alice_home: Home, bob: User, bob_home: Home
    ) -> None:
        from housecairn.sync import ids

        article = self._article(alice, alice_home)
        theirs = room_services.create_room(bob, bob_home, name="Theirs", kind="kitchen")
        entity = "stock.article_rooms"
        link_id = ids.link(alice_home.pk, entity, article.pk, theirs.pk)
        fields = {"article_id": str(article.pk), "room_id": str(theirs.pk)}
        answer = send(phone.access, alice_home.pk, [op(alice_home.pk, entity, link_id, fields)])
        assert result_of(answer)["code"] == "invalid_reference"


# ---------- a push runs once ----------


class TestPushNumbers:
    def test_a_push_sent_again_gets_the_first_answer_and_is_not_applied_again(
        self, phone: Connected, alice_home: Home
    ) -> None:
        """Mutant: a push number not kept (each push applied) fails here."""
        ops = [op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "Hall"})]
        first = send(phone.access, alice_home.pk, ops, number=5)
        count = Change.objects.filter(home=alice_home).count()
        again = send(phone.access, alice_home.pk, ops, number=5)
        assert again.status_code == 200
        assert again.json() == first.json()
        assert Change.objects.filter(home=alice_home).count() == count

    def test_the_same_number_with_another_body_is_409(
        self, phone: Connected, alice_home: Home
    ) -> None:
        send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "A"})],
            number=5,
        )
        answer = send(
            phone.access,
            alice_home.pk,
            [op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "B"})],
            number=5,
        )
        assert answer.status_code == 409
        assert answer.json()["code"] == "push_conflict"
        assert not Room.objects.filter(name="B").exists()

    def test_numbers_are_per_connection(self, alice: User, alice_home: Home) -> None:
        one, two = connect(alice, [alice_home]), connect(alice, [alice_home])
        send(
            one.access,
            alice_home.pk,
            [op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "A"})],
            number=1,
        )
        answer = send(
            two.access,
            alice_home.pk,
            [op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "B"})],
            number=1,
            device=OTHER_DEVICE,
        )
        assert answer.status_code == 200
        assert Room.objects.filter(name="B").exists()

    def test_a_malformed_push_is_not_kept(self, phone: Connected, alice_home: Home) -> None:
        send(phone.access, alice_home.pk, ["bad"], number=3)
        assert not Push.objects.exists()

    def test_a_number_is_kept_30_days(self, phone: Connected, alice_home: Home) -> None:
        send(phone.access, alice_home.pk, [], number=1)
        kept = Push.objects.get()
        retention.purge(kept.created_at + timedelta(days=30))
        assert Push.objects.exists()
        retention.purge(kept.created_at + timedelta(days=30, seconds=1))
        assert not Push.objects.exists()


# ---------- the device ----------


class TestDevice:
    def test_the_first_push_fixes_the_device_another_is_409(
        self, phone: Connected, alice_home: Home
    ) -> None:
        assert send(phone.access, alice_home.pk, [], number=1).status_code == 200
        assert AppConnection.objects.get(pk=phone.connection.pk).device_id == DEVICE
        answer = send(phone.access, alice_home.pk, [], number=2, device=OTHER_DEVICE)
        assert answer.status_code == 409
        assert answer.json()["code"] == "wrong_device"

    def test_the_device_of_another_connection_is_409(self, alice: User, alice_home: Home) -> None:
        one, two = connect(alice, [alice_home]), connect(alice, [alice_home])
        send(one.access, alice_home.pk, [], number=1)
        answer = send(two.access, alice_home.pk, [], number=1)
        assert answer.status_code == 409
        assert answer.json()["code"] == "wrong_device"

    def test_the_same_phone_signed_in_again_pushes_with_its_device(
        self, phone: Connected, alice: User, alice_home: Home
    ) -> None:
        """Mutant: a revoked connection counted in the check: 409 at every push, for good."""
        row_id = uuid.uuid4()
        first = [op(alice_home.pk, "rooms.room", row_id, {"name": "Before"})]
        assert send(phone.access, alice_home.pk, first, number=1).status_code == 200
        connection_services.revoke(phone.connection, actor=alice)
        again = connect(alice, [alice_home])
        later = [op(alice_home.pk, "rooms.room", row_id, {"name": "After"}, c=stamp(5))]
        answer = send(again.access, alice_home.pk, later, number=1)
        assert result_of(answer)["status"] == "applied"
        assert AppConnection.objects.get(pk=again.connection.pk).device_id == DEVICE
        assert Room.objects.get(pk=row_id).name == "After"
        # The revoked connection cannot push any more, with that device or another.
        assert send(phone.access, alice_home.pk, later, number=2).status_code == 401

    def test_the_server_device_is_refused(self, phone: Connected, alice_home: Home) -> None:
        """A phone that took the server's device id would win every tie as the web."""
        server = clock.device_to_b64(changes.server_device())
        answer = send(phone.access, alice_home.pk, [], device=server)
        assert answer.status_code == 400
        assert AppConnection.objects.get(pk=phone.connection.pk).device_id == ""


# ---------- the rate limit ----------


class TestRateLimit:
    def test_the_push_limit_is_its_own(
        self, phone: Connected, alice_home: Home, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from housecairn.instance import ratelimits

        assert ratelimits.rates()["housecairn_sync_push"] == "60/m/key"
        real = ratelimits.rates

        def tight() -> dict[str, str]:
            return {**real(), "housecairn_sync_push": "2/m/key"}

        monkeypatch.setattr(ratelimits, "rates", tight)
        assert send(phone.access, alice_home.pk, [], number=1).status_code == 200
        assert send(phone.access, alice_home.pk, [], number=2).status_code == 200
        assert send(phone.access, alice_home.pk, [], number=3).status_code == 429
        # The reads keep their own limit.
        reads = Client().get(
            f"/api/v1/sync/homes/{alice_home.pk}/changes",
            secure=True,
            HTTP_AUTHORIZATION=f"Bearer {phone.access}",
        )
        assert reads.status_code == 200

    def test_the_limit_is_per_connection(
        self, alice: User, alice_home: Home, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from housecairn.instance import ratelimits

        real = ratelimits.rates
        monkeypatch.setattr(
            ratelimits, "rates", lambda: {**real(), "housecairn_sync_push": "1/m/key"}
        )
        one, two = connect(alice, [alice_home]), connect(alice, [alice_home])
        assert send(one.access, alice_home.pk, [], number=1).status_code == 200
        assert send(one.access, alice_home.pk, [], number=2).status_code == 429
        assert send(two.access, alice_home.pk, [], number=1, device=OTHER_DEVICE).status_code == 200
