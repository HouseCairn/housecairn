"""The sync API, reading (lot M4 of docs/adr/0079): homes, changes, snapshot, 410.

The API is exposed to the phone: each test of security names the control it proves, and fails
without it (a home check removed, a scope ignored, a cursor not bounded).
"""

import json
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from oauth2_provider.models import AccessToken

from housecairn.accounts.models import User
from housecairn.apitokens import services as token_services
from housecairn.apitokens.models import Scope
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Membership, Role
from housecairn.rooms import services as room_services
from housecairn.sync import changes, clock, reading
from housecairn.sync.models import Change, HomeHead, RowClocks
from tests.oauth_support import Connected, connect

pytestmark = pytest.mark.django_db

HOMES = "/api/v1/sync/homes/"


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


def changes_url(home_id: Any) -> str:
    return f"/api/v1/sync/homes/{home_id}/changes"


def snapshot_url(home_id: Any) -> str:
    return f"/api/v1/sync/homes/{home_id}/snapshot"


def get(token: str | None, path: str, **params: Any) -> Any:
    headers = {} if token is None else {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    return Client().get(path, params, secure=True, **headers)


def lines_of(answer: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in answer.content.decode().splitlines()]


@pytest.fixture
def phone(alice: User, alice_home: Home) -> Connected:
    return connect(alice, [alice_home])


# ---------- the credential ----------


class TestCredential:
    @pytest.mark.parametrize("route", ["homes", "changes", "snapshot"])
    def test_no_token_is_refused(self, alice_home: Home, route: str) -> None:
        path = {"homes": HOMES, "changes": changes_url(alice_home.pk)}.get(
            route, snapshot_url(alice_home.pk)
        )
        answer = get(None, path)
        assert answer.status_code == 403
        assert answer.json()["code"] == "sync_needs_connection"

    @pytest.mark.parametrize("scope", [Scope.READ, Scope.WRITE, Scope.EVENTS])
    def test_an_api_token_is_refused(self, alice: User, alice_home: Home, scope: Scope) -> None:
        _token, raw = token_services.create_token(
            alice, name="t", scopes=[scope], homes=[alice_home], lifetime_days=30
        )
        for path in (HOMES, changes_url(alice_home.pk), snapshot_url(alice_home.pk)):
            answer = get(raw, path)
            assert answer.status_code == 403, path
            assert answer.json()["code"] == "sync_needs_connection"

    def test_a_session_is_refused(self, alice: User, alice_home: Home) -> None:
        client = Client()
        client.force_login(alice)
        answer = client.get(changes_url(alice_home.pk), secure=True)
        assert answer.status_code == 403
        assert answer.json()["code"] == "sync_needs_connection"

    def test_the_scope_sync_is_required(self, phone: Connected, alice_home: Home) -> None:
        """Mutant: a view that ignores the scope (TokenScopePermission left out) fails here."""
        assert get(phone.access, HOMES).status_code == 200
        AccessToken.objects.update(scope="homes.create")
        for path in (HOMES, changes_url(alice_home.pk), snapshot_url(alice_home.pk)):
            assert get(phone.access, path).status_code == 403, path

    def test_a_revoked_or_expired_token_is_refused(self, phone: Connected) -> None:
        AccessToken.objects.update(expires=timezone.now() - timedelta(seconds=1))
        assert get(phone.access, HOMES).status_code == 401

    def test_plain_http_from_a_public_peer_is_refused(self, phone: Connected) -> None:
        answer = Client().get(
            HOMES, HTTP_AUTHORIZATION=f"Bearer {phone.access}", REMOTE_ADDR="203.0.113.9"
        )
        assert answer.status_code == 403
        assert answer.json()["code"] == "insecure_transport"

    def test_every_answer_says_its_version(self, phone: Connected, alice_home: Home) -> None:
        for path in (HOMES, changes_url(alice_home.pk), snapshot_url(alice_home.pk)):
            assert get(phone.access, path)["HC-Sync"] == "1"


# ---------- which homes ----------


class TestHomes:
    def test_the_homes_of_the_connection_only(
        self, alice: User, alice_home: Home, bob_home: Home
    ) -> None:
        cabin = home_services.create_home(alice, name="Cabin")  # alice's, not given to the phone
        phone = connect(alice, [alice_home])
        body = get(phone.access, HOMES).json()
        assert [home["id"] for home in body["homes"]] == [str(alice_home.pk)]
        assert str(cabin.pk) not in json.dumps(body)
        assert str(bob_home.pk) not in json.dumps(body)
        assert body["gone"] == []

    def test_a_home_entry(self, alice: User, alice_home: Home, bob: User) -> None:
        Membership.objects.create(home=alice_home, user=bob, role=Role.READ_ONLY)
        bob.display_name = "Bob"
        bob.save(update_fields=["display_name"])
        phone = connect(alice, [alice_home])
        (entry,) = get(phone.access, HOMES).json()["homes"]
        assert entry == {
            "id": str(alice_home.pk),
            "name": "Main house",
            "role": "owner",
            "archived_at": None,
            "head": HomeHead.objects.get(home=alice_home).pos,
            "member_id": str(alice.pk),
            "members": [
                {"id": str(alice.pk), "name": "alice", "role": "owner"},
                {"id": str(bob.pk), "name": "Bob", "role": "read_only"},
            ],
        }
        assert "@" not in json.dumps(entry)

    def test_the_member_of_the_connection_in_each_home(self, alice: User, bob: User) -> None:
        main = home_services.create_home(alice, name="Main")
        flat = home_services.create_home(bob, name="Flat")
        Membership.objects.create(home=main, user=bob, role=Role.READ_ONLY)
        Membership.objects.create(home=flat, user=alice, role=Role.MEMBER)
        for user, other in ((alice, bob), (bob, alice)):
            phone = connect(user, [main, flat])
            homes = {h["id"]: h for h in get(phone.access, HOMES).json()["homes"]}
            assert set(homes) == {str(main.pk), str(flat.pk)}
            for entry in homes.values():
                assert entry["member_id"] == str(user.pk) != str(other.pk)
                mine = [m for m in entry["members"] if m["id"] == entry["member_id"]]
                assert len(mine) == 1
                assert mine[0]["role"] == entry["role"]
        read_only = {h["id"]: h for h in get(connect(bob, [main]).access, HOMES).json()["homes"]}[
            str(main.pk)
        ]
        assert read_only["role"] == "read_only"
        assert read_only["member_id"] == str(bob.pk)

    def test_an_archived_home_is_listed_and_read(self, alice: User, alice_home: Home) -> None:
        phone = connect(alice, [alice_home])
        home_services.archive_home(alice, alice_home)
        (entry,) = get(phone.access, HOMES).json()["homes"]
        stored = RowClocks.objects.get(home=alice_home, entity="homes.home").clocks
        assert entry["archived_at"] == stored["archived_at"][0]
        clock.parse_clock(entry["archived_at"])
        assert get(phone.access, changes_url(alice_home.pk)).status_code == 200
        assert get(phone.access, snapshot_url(alice_home.pk)).status_code == 200

    def test_a_member_removed_loses_the_home(
        self, alice: User, bob: User, alice_home: Home
    ) -> None:
        membership = Membership.objects.create(home=alice_home, user=bob, role=Role.MEMBER)
        phone = connect(bob, [alice_home])
        assert get(phone.access, changes_url(alice_home.pk)).status_code == 200
        home_services.remove_member(alice, membership)
        assert get(phone.access, HOMES).json()["homes"] == []
        assert get(phone.access, changes_url(alice_home.pk)).status_code == 404
        assert get(phone.access, snapshot_url(alice_home.pk)).status_code == 404

    def test_a_home_that_requires_a_provider_is_locked(
        self, phone: Connected, alice_home: Home
    ) -> None:
        Home.objects.filter(pk=alice_home.pk).update(required_provider="corporate")
        assert get(phone.access, HOMES).json()["homes"] == []
        answer = get(phone.access, changes_url(alice_home.pk))
        assert answer.status_code == 403
        assert answer.json()["code"] == "sso_required"


class TestOtherHousehold:
    """Mutant: a view that reads the home without `homes_for_request` fails here."""

    @pytest.mark.parametrize("route", [changes_url, snapshot_url])
    def test_a_home_of_another_household_is_not_found(
        self, phone: Connected, bob_home: Home, route: Any
    ) -> None:
        room_services.create_room(bob_home.memberships.get().user, bob_home, name="Bob's room")
        answer = get(phone.access, route(bob_home.pk))
        assert answer.status_code == 404
        assert "Bob" not in answer.content.decode()

    @pytest.mark.parametrize("route", [changes_url, snapshot_url])
    def test_an_unknown_id_is_not_found_alike(self, phone: Connected, route: Any) -> None:
        assert get(phone.access, route(uuid.uuid4())).status_code == 404

    def test_a_cursor_never_reaches_another_home(
        self, alice: User, phone: Connected, alice_home: Home, bob: User, bob_home: Home
    ) -> None:
        for n in range(5):
            room_services.create_room(bob, bob_home, name=f"Bob {n}")
        head = HomeHead.objects.get(home=alice_home).pos
        body = get(phone.access, changes_url(alice_home.pk), after=0, limit=1000).json()
        assert {change["op"]["h"] for change in body["changes"]} == {str(alice_home.pk)}
        assert body["head"] == head
        # A position of the other home, beyond this one's head, is refused.
        bob_head = HomeHead.objects.get(home=bob_home).pos
        assert bob_head > head
        assert get(phone.access, changes_url(alice_home.pk), after=bob_head).status_code == 400


class TestErasedHome:
    def test_an_erased_home_is_gone_for_its_connections(
        self, alice: User, alice_home: Home
    ) -> None:
        phone = connect(alice, [alice_home])
        home_id = alice_home.pk
        home_services.delete_home(alice, alice_home, confirmation=alice_home.name)
        body = get(phone.access, HOMES).json()
        assert body["homes"] == []
        assert [gone["id"] for gone in body["gone"]] == [str(home_id)]
        for path in (changes_url(home_id), snapshot_url(home_id)):
            answer = get(phone.access, path)
            assert answer.status_code == 410, path
            assert answer.json()["code"] == "home_gone"

    def test_the_list_of_gone_homes_costs_one_query_whatever_their_number(
        self, alice: User, alice_home: Home, django_assert_num_queries: Any
    ) -> None:
        def erase_one(n: int) -> None:
            home = home_services.create_home(alice, name=f"Gone {n}")
            phone.connection.homes.add(home)
            home_services.delete_home(alice, home, confirmation=home.name)

        phone = connect(alice, [alice_home])
        erase_one(0)
        with django_assert_num_queries(1):
            assert len(reading.gone_for(phone.connection.pk)) == 1
        for n in range(1, 8):
            erase_one(n)
        with django_assert_num_queries(1):
            assert len(reading.gone_for(phone.connection.pk)) == 8

    def test_the_gone_homes_are_found_by_an_index(self) -> None:
        from django.db import connection

        from housecairn.sync.models import ErasedHomeConnection

        table = ErasedHomeConnection._meta.db_table
        with connection.cursor() as cursor:
            found = connection.introspection.get_constraints(cursor, table)
        indexed = [c["columns"] for c in found.values() if c["index"] or c["unique"]]
        assert ["connection_id"] in indexed

    def test_a_member_removed_before_the_erasure_is_not_told(
        self, alice: User, bob: User, alice_home: Home
    ) -> None:
        shared = home_services.create_home(alice, name="Shared")
        membership = Membership.objects.create(home=shared, user=bob, role=Role.MEMBER)
        bob_phone = connect(bob, [shared])
        alice_phone = connect(alice, [shared])
        home_services.remove_member(alice, membership)  # bob's connection stays bound
        assert get(bob_phone.access, changes_url(shared.pk)).status_code == 404
        home_services.delete_home(alice, shared, confirmation="Shared")
        assert get(bob_phone.access, HOMES).json()["gone"] == []
        assert get(bob_phone.access, changes_url(shared.pk)).status_code == 404
        assert get(bob_phone.access, snapshot_url(shared.pk)).status_code == 404
        assert [g["id"] for g in get(alice_phone.access, HOMES).json()["gone"]] == [str(shared.pk)]
        assert get(alice_phone.access, changes_url(shared.pk)).status_code == 410

    def test_any_other_connection_gets_404(
        self, alice: User, bob: User, alice_home: Home, bob_home: Home
    ) -> None:
        shared = home_services.create_home(alice, name="Shared")
        Membership.objects.create(home=shared, user=bob, role=Role.MEMBER)
        alice_phone = connect(alice, [alice_home])  # not bound to the shared home
        bob_phone = connect(bob, [bob_home])
        home_services.delete_home(alice, shared, confirmation="Shared")
        for phone in (alice_phone, bob_phone):
            assert get(phone.access, HOMES).json()["gone"] == []
            assert get(phone.access, changes_url(shared.pk)).status_code == 404
            assert get(phone.access, snapshot_url(shared.pk)).status_code == 404


# ---------- the feed ----------


def make_rooms(user: User, home: Home, count: int) -> None:
    for n in range(count):
        room_services.create_room(user, home, name=f"Room {n}")


class TestChanges:
    def test_pages_follow_one_another(
        self, alice: User, phone: Connected, alice_home: Home
    ) -> None:
        make_rooms(alice, alice_home, 6)
        head = HomeHead.objects.get(home=alice_home).pos
        seen: list[int] = []
        after, more = 0, True
        while more:
            body = get(phone.access, changes_url(alice_home.pk), after=after, limit=4).json()
            assert len(body["changes"]) <= 4
            assert body["head"] == head
            seen.extend(change["pos"] for change in body["changes"])
            more = body["more"]
            after = seen[-1]
        assert seen == list(range(1, head + 1))

    def test_an_operation_of_the_protocol(
        self, alice: User, phone: Connected, alice_home: Home
    ) -> None:
        room = room_services.create_room(alice, alice_home, name="Hall")
        change = Change.objects.filter(home=alice_home, row_id=room.pk).get()
        body = get(phone.access, changes_url(alice_home.pk), after=change.pos - 1, limit=1).json()
        (entry,) = body["changes"]
        assert entry["pos"] == change.pos
        assert entry["device"] == clock.device_to_b64(changes.server_device())
        assert entry["op"]["t"] == "rooms.room"
        assert entry["op"]["h"] == str(alice_home.pk)
        assert entry["op"]["id"] == str(room.pk)
        assert entry["op"]["k"] == "put"
        assert entry["op"]["c"] == change.clock
        assert entry["op"]["f"]["name"] == "Hall"

    def test_a_deletion_is_in_the_feed(
        self, alice: User, phone: Connected, alice_home: Home
    ) -> None:
        room = room_services.create_room(alice, alice_home, name="Hall")
        room_services.delete_room(alice, room)
        body = get(phone.access, changes_url(alice_home.pk), limit=1000).json()
        deletions = [c["op"] for c in body["changes"] if c["op"]["k"] == "delete"]
        assert [(op["id"], op["f"]) for op in deletions] == [(str(room.pk), {})]

    def test_a_stored_file_name_never_leaves_the_server(
        self, phone: Connected, alice_home: Home
    ) -> None:
        report = uuid.uuid4()
        name = f"{alice_home.pk}/{report}.jpg"
        changes.record_change(alice_home, "reports.report", report, changes.PUT, {"photo": name})
        changes.record_change(
            alice_home, "stock.article", uuid.uuid4(), changes.PUT, {"label": name}
        )
        answer = get(phone.access, changes_url(alice_home.pk), limit=1000)
        assert name not in answer.content.decode()
        files = [c["op"]["f"] for c in answer.json()["changes"] if c["op"]["t"] != "homes.home"]
        assert files[-2]["photo"]["file"] == str(report)
        assert set(files[-2]["photo"]) == {"file", "size", "type", "width", "height"}

    def test_nothing_internal_is_given(
        self, alice: User, phone: Connected, alice_home: Home
    ) -> None:
        room = room_services.create_room(alice, alice_home, name="Hall")
        room_services.delete_room(alice, room)
        body = get(phone.access, changes_url(alice_home.pk), limit=1000).json()
        assert set(body) == {"changes", "head", "more"}
        for change in body["changes"]:
            assert set(change) == {"pos", "device", "op"}
            assert set(change["op"]) == {"t", "h", "id", "k", "c", "f"}
            for name in ("deleted_at", "updated_at", "tombstoned_at", "floor", "visible_from"):
                assert name not in change["op"]["f"]


class TestCursor:
    """Mutant: a cursor or a limit not bounded fails here, with a 500 or too many changes."""

    @pytest.mark.parametrize(
        "after", ["-1", "abc", "1.5", "", " 1", "\uff11", "0x10", "1e3", "9" * 25, str(2**63)]
    )
    def test_a_forged_cursor_is_refused(
        self, phone: Connected, alice_home: Home, after: str
    ) -> None:
        answer = get(phone.access, changes_url(alice_home.pk), after=after)
        assert answer.status_code == 400, after
        assert "detail" in answer.json()

    def test_a_cursor_beyond_the_head_is_refused(self, phone: Connected, alice_home: Home) -> None:
        head = HomeHead.objects.get(home=alice_home).pos
        assert get(phone.access, changes_url(alice_home.pk), after=head).status_code == 200
        assert get(phone.access, changes_url(alice_home.pk), after=head + 1).status_code == 400

    @pytest.mark.parametrize("limit", ["0", "-5", "x"])
    def test_a_wrong_limit_is_refused(self, phone: Connected, alice_home: Home, limit: str) -> None:
        assert get(phone.access, changes_url(alice_home.pk), limit=limit).status_code == 400

    def test_a_page_is_bounded(
        self, alice: User, phone: Connected, alice_home: Home, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(reading, "MAX_LIMIT", 3)
        make_rooms(alice, alice_home, 5)
        body = get(phone.access, changes_url(alice_home.pk), limit=10**6).json()
        assert len(body["changes"]) == 3
        assert body["more"] is True

    def test_the_default_page(self, alice: User, phone: Connected, alice_home: Home) -> None:
        assert reading.DEFAULT_LIMIT == 500
        assert reading.MAX_LIMIT == 1000
        body = get(phone.access, changes_url(alice_home.pk)).json()
        assert body["more"] is False

    def test_an_expired_cursor_answers_410(
        self, alice: User, phone: Connected, alice_home: Home
    ) -> None:
        make_rooms(alice, alice_home, 3)
        HomeHead.objects.filter(home=alice_home).update(floor=2)
        answer = get(phone.access, changes_url(alice_home.pk), after=1)
        assert answer.status_code == 410
        assert answer.json()["code"] == "cursor_expired"
        assert get(phone.access, changes_url(alice_home.pk), after=0).status_code == 410
        body = get(phone.access, changes_url(alice_home.pk), after=2).json()
        assert body["changes"][0]["pos"] == 3

    def test_the_retention_purge_expires_the_cursor(
        self, alice: User, phone: Connected, alice_home: Home
    ) -> None:
        from housecairn.sync import retention

        make_rooms(alice, alice_home, 2)
        retention.purge(timezone.now() + retention.RETENTION + timedelta(days=1))
        answer = get(phone.access, changes_url(alice_home.pk), after=0)
        assert answer.status_code == 410
        assert answer.json()["code"] == "cursor_expired"


class TestRateLimit:
    def test_reads_are_limited_per_connection(
        self, phone: Connected, alice: User, alice_home: Home, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from housecairn.instance import ratelimits

        assert ratelimits.rates()["housecairn_sync_read"] == "600/m/key"
        real = ratelimits.rates

        def rates() -> dict[str, str]:
            return {**real(), "housecairn_sync_read": "2/m/key"}

        monkeypatch.setattr(ratelimits, "rates", rates)
        other = connect(alice, [alice_home])
        assert get(phone.access, HOMES).status_code == 200
        assert get(phone.access, changes_url(alice_home.pk)).status_code == 200
        assert get(phone.access, snapshot_url(alice_home.pk)).status_code == 429
        assert get(other.access, HOMES).status_code == 200  # its own count


class TestSnapshotRateLimit:
    def test_snapshots_are_limited_apart_from_the_reads(
        self, phone: Connected, alice: User, alice_home: Home
    ) -> None:
        from housecairn.instance import ratelimits

        assert ratelimits.rates()["housecairn_sync_snapshot"] == "6/m/key"
        for _ in range(6):
            assert get(phone.access, snapshot_url(alice_home.pk)).status_code == 200
        refused = get(phone.access, snapshot_url(alice_home.pk))
        assert refused.status_code == 429
        # The feed and the list are not slowed by it, and another phone has its own count.
        assert get(phone.access, HOMES).status_code == 200
        assert get(phone.access, changes_url(alice_home.pk)).status_code == 200
        other = connect(alice, [alice_home])
        assert get(other.access, snapshot_url(alice_home.pk)).status_code == 200

    def test_a_refused_snapshot_is_not_built(
        self, phone: Connected, alice_home: Home, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from housecairn.instance import ratelimits

        real = ratelimits.rates
        monkeypatch.setattr(
            ratelimits, "rates", lambda: {**real(), "housecairn_sync_snapshot": "1/m/key"}
        )
        assert get(phone.access, snapshot_url(alice_home.pk)).status_code == 200

        def built(home: Home) -> Any:
            raise AssertionError("a refused snapshot is not built")

        monkeypatch.setattr(reading, "snapshot_lines", built)
        assert get(phone.access, snapshot_url(alice_home.pk)).status_code == 429

    def test_the_info_route_announces_the_limits(self, phone: Connected) -> None:
        answer = Client().get(
            "/api/v1/sync/info", secure=True, HTTP_AUTHORIZATION=f"Bearer {phone.access}"
        )
        assert answer.json()["limits"] == {
            "reads_per_minute": 600,
            "snapshots_per_minute": 6,
            "pushes_per_minute": 60,
            "ops_per_push": 500,
            "push_bytes": 1024 * 1024,
        }


class TestSchema:
    def test_the_three_reads_declare_what_a_client_hostile_or_late_gets(self, alice: User) -> None:
        client = Client()
        client.force_login(alice)
        schema = client.get(
            reverse("api-schema"), HTTP_ACCEPT="application/vnd.oai.openapi+json"
        ).json()
        reads = {
            path: item["get"]["responses"]
            for path, item in schema["paths"].items()
            if path.startswith("/api/v1/sync/homes") and "get" in item
        }
        assert len(reads) == 3, sorted(reads)
        for path, responses in reads.items():
            assert {"401", "403", "429"} <= set(responses), path
            assert all(responses[code]["description"] for code in ("401", "403", "429")), path


# ---------- the snapshot ----------


class TestSnapshot:
    def test_rows_members_then_covers(
        self, alice: User, phone: Connected, alice_home: Home
    ) -> None:
        room = room_services.create_room(alice, alice_home, name="Hall")
        answer = get(phone.access, snapshot_url(alice_home.pk))
        assert answer.status_code == 200
        assert answer["Content-Type"].startswith("application/x-ndjson")
        lines = lines_of(answer)
        assert lines[-1] == {"covers": HomeHead.objects.get(home=alice_home).pos}
        assert lines[-2] == {"members": [{"id": str(alice.pk), "name": "alice", "role": "owner"}]}
        rows = {(line["t"], line["id"]): line for line in lines[:-2]}
        home_line = rows[("homes.home", str(alice_home.pk))]
        assert home_line["h"] == str(alice_home.pk)
        hall = rows[("rooms.room", str(room.pk))]
        stored = RowClocks.objects.get(entity="rooms.room", row_id=room.pk).clocks
        assert hall["f"]["name"] == ["Hall", *stored["name"]]
        assert "deleted_at" not in hall["f"]

    def test_a_tombstone_within_the_retention(
        self, alice: User, phone: Connected, alice_home: Home
    ) -> None:
        room = room_services.create_room(alice, alice_home, name="Hall")
        room_services.delete_room(alice, room)
        lines = lines_of(get(phone.access, snapshot_url(alice_home.pk)))
        (hall,) = [line for line in lines if line.get("id") == str(room.pk)]
        stamp = RowClocks.objects.get(entity="rooms.room", row_id=room.pk).clocks["deleted_at"]
        assert hall["f"]["deleted_at"] == [stamp[0], *stamp]

    def test_a_row_deleted_before_the_log_and_the_retention_is_left_out(
        self, alice: User, phone: Connected, alice_home: Home
    ) -> None:
        from housecairn.rooms.models import Room

        room = room_services.create_room(alice, alice_home, name="Hall")
        RowClocks.objects.filter(row_id=room.pk).delete()
        Room.all_objects.filter(pk=room.pk).update(deleted_at=timezone.now() - timedelta(days=401))
        lines = lines_of(get(phone.access, snapshot_url(alice_home.pk)))
        assert str(room.pk) not in {line.get("id") for line in lines}

    def test_unknown_fields_are_given_back(
        self, alice: User, phone: Connected, alice_home: Home
    ) -> None:
        room = room_services.create_room(alice, alice_home, name="Hall").pk
        device = bytes(range(16))
        changes.record_change(
            alice_home,
            "rooms.room",
            room,
            changes.PUT,
            {"name": "Hall", "glow": "amber"},
            stamp=("ff93a1b2c3d40001", device),
            known={"name"},
        )
        changes_body = get(phone.access, changes_url(alice_home.pk), limit=1000).json()
        assert changes_body["changes"][-1]["op"]["f"] == {"name": "Hall", "glow": "amber"}
        assert changes_body["changes"][-1]["device"] == clock.device_to_b64(device)
        lines = lines_of(get(phone.access, snapshot_url(alice_home.pk)))
        (line,) = [line for line in lines if line.get("id") == str(room)]
        stamp = ["ff93a1b2c3d40001", clock.device_to_b64(device)]
        assert line["f"]["glow"] == ["amber", *stamp]

    def test_links(self, alice: User, phone: Connected, alice_home: Home) -> None:
        from housecairn.projects import services as project_services
        from housecairn.sync import ids

        room = room_services.create_room(alice, alice_home, name="Hall")
        project = project_services.create_project(alice, alice_home, title="Paint", rooms=[room])
        link_id = ids.link(alice_home.pk, "projects.project_rooms", project.pk, room.pk)
        lines = lines_of(get(phone.access, snapshot_url(alice_home.pk)))
        (link,) = [line for line in lines if line.get("t") == "projects.project_rooms"]
        assert link["id"] == str(link_id)
        assert link["f"]["project_id"][0] == str(project.pk)
        assert link["f"]["room_id"][0] == str(room.pk)
        project_services.update_project(alice, project, rooms=[])
        lines = lines_of(get(phone.access, snapshot_url(alice_home.pk)))
        (link,) = [line for line in lines if line.get("t") == "projects.project_rooms"]
        assert link["f"]["deleted_at"][0] == link["f"]["deleted_at"][1]
        assert link["f"]["room_id"][0] == str(room.pk)

    def test_no_row_of_another_home(
        self, alice: User, bob: User, phone: Connected, alice_home: Home, bob_home: Home
    ) -> None:
        room_services.create_room(bob, bob_home, name="Bob's room")
        lines = lines_of(get(phone.access, snapshot_url(alice_home.pk)))
        assert {line["h"] for line in lines if "h" in line} == {str(alice_home.pk)}
        assert "Bob" not in json.dumps(lines)

    def test_every_entity_type_is_a_model_of_the_homes(self) -> None:
        from tests.test_sync_guard import home_models

        assert sorted(reading.ENTITIES) == sorted(m._meta.label_lower for m in home_models())
