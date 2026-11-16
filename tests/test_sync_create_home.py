"""The sync API, writing homes (lot M5c of docs/adr/0079): `POST /api/v1/sync/homes/`.

The API is exposed to the phone: each test of security names the control it proves, and fails
without it (a right not checked, an id taken over, a conflict that tells where the id is).
"""

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from oauth2_provider.models import AccessToken

from housecairn.accounts.models import User
from housecairn.apitokens import services as token_services
from housecairn.apitokens.models import AppConnection, Scope
from housecairn.audit.models import AuditEvent
from housecairn.homes import services as home_services
from housecairn.homes.forms import HomeForm
from housecairn.homes.models import Home, Membership, Role
from housecairn.rooms import services as room_services
from housecairn.sync import creating
from housecairn.sync.models import Change
from tests.oauth_support import Connected, connect

pytestmark = pytest.mark.django_db

HOMES = "/api/v1/sync/homes/"
CREATE = "sync homes.create"


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


def bearer(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def post(token: str | None, body: Any, **extra: Any) -> Any:
    headers = {} if token is None else bearer(token)
    data = body if isinstance(body, (str, bytes)) else json.dumps(body)
    return Client().post(
        HOMES, data, content_type="application/json", secure=True, **{**headers, **extra}
    )


def new_home(**fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "name": "Cabin",
        "currency": "EUR",
        "hemisphere": "north",
    }
    body.update(fields)
    return body


@pytest.fixture
def phone(alice: User, alice_home: Home) -> Connected:
    """A phone that may create homes: the scope asked for, the right given at consent."""
    return connect(alice, [alice_home], scope=CREATE, may_create_homes=True)


# ---------- creating ----------


class TestCreate:
    def test_a_home_is_created_with_the_phones_id(self, alice: User, phone: Connected) -> None:
        body = new_home(name="Cabin", currency="CHF", hemisphere="south")
        answer = post(phone.access, body)
        assert answer.status_code == 201, answer.content
        assert answer["HC-Sync"] == "1"
        home = Home.objects.get(pk=body["id"])
        assert (home.name, home.currency, home.hemisphere) == ("Cabin", "CHF", "south")
        membership = Membership.objects.get(home=home)
        assert (membership.user, membership.role) == (alice, Role.OWNER)
        assert AuditEvent.objects.filter(action="home.created", home_id=home.pk).exists()

    def test_the_answer_is_the_home_as_the_list_gives_it(
        self, alice: User, phone: Connected
    ) -> None:
        body = new_home()
        created = post(phone.access, body).json()
        listed = Client().get(HOMES, secure=True, **bearer(phone.access)).json()["homes"]
        assert created in listed
        assert created["id"] == body["id"]
        assert created["role"] == "owner"
        assert created["member_id"] == str(alice.pk)
        assert [(m["id"], m["role"]) for m in created["members"]] == [(str(alice.pk), "owner")]

    def test_the_home_is_bound_to_the_connection(self, phone: Connected) -> None:
        """Mutant: a creation that forgets the binding leaves a home the phone cannot read."""
        body = new_home()
        post(phone.access, body)
        connection = AppConnection.objects.get(pk=phone.connection.pk)
        assert body["id"] in {str(pk) for pk in connection.homes.values_list("pk", flat=True)}

    def test_changes_and_snapshot_give_the_new_home(self, phone: Connected) -> None:
        body = new_home(name="Cabin")
        head = post(phone.access, body).json()["head"]
        assert head >= 1
        feed = Client().get(
            f"{HOMES}{body['id']}/changes", {"after": 0}, secure=True, **bearer(phone.access)
        )
        assert feed.status_code == 200
        ops = [change["op"] for change in feed.json()["changes"]]
        assert any(
            op["t"] == "homes.home" and op["id"] == body["id"] and op["f"].get("name") == "Cabin"
            for op in ops
        ), ops
        snapshot = Client().get(
            f"{HOMES}{body['id']}/snapshot", secure=True, **bearer(phone.access)
        )
        assert snapshot.status_code == 200
        rows = [json.loads(line) for line in snapshot.content.decode().splitlines()]
        home_row = next(row for row in rows if row.get("t") == "homes.home")
        assert home_row["id"] == body["id"]
        assert home_row["f"]["name"][0] == "Cabin"
        assert rows[-1] == {"covers": head}

    def test_the_hemisphere_left_out_is_the_north(self, phone: Connected) -> None:
        body = new_home()
        del body["hemisphere"]
        assert post(phone.access, body).status_code == 201
        assert Home.objects.get(pk=body["id"]).hemisphere == "north"

    def test_the_name_is_trimmed_as_on_the_web(self, phone: Connected) -> None:
        body = new_home(name="  Cabin  ")
        assert post(phone.access, body).json()["name"] == "Cabin"
        assert post(phone.access, body).status_code == 200  # sent again: the same home


# ---------- sent again, and conflicts ----------


class TestRepeatAndConflict:
    def test_the_same_home_sent_again_is_200_and_made_once(self, phone: Connected) -> None:
        body = new_home()
        first = post(phone.access, body)
        changes = Change.objects.filter(home_id=body["id"]).count()
        again = post(phone.access, body)
        assert (first.status_code, again.status_code) == (201, 200)
        assert again.json() == first.json()
        assert Home.objects.filter(pk=body["id"]).count() == 1
        assert Membership.objects.filter(home_id=body["id"]).count() == 1
        assert Change.objects.filter(home_id=body["id"]).count() == changes

    @pytest.mark.parametrize(
        "other", [{"name": "Barn"}, {"currency": "USD"}, {"hemisphere": "south"}]
    )
    def test_the_same_id_with_other_fields_is_409(
        self, phone: Connected, other: dict[str, str]
    ) -> None:
        body = new_home()
        post(phone.access, body)
        answer = post(phone.access, {**body, **other})
        assert answer.status_code == 409
        assert answer.json()["code"] == "id_taken"
        home = Home.objects.get(pk=body["id"])
        assert (home.name, home.currency, home.hemisphere) == ("Cabin", "EUR", "north")

    def test_the_id_of_another_accounts_home_is_409_and_tells_nothing(
        self, phone: Connected, bob_home: Home
    ) -> None:
        """Mutant: a lookup across every home would answer 200 with Bob's home, or bind it.

        The answer is the same, byte for byte, as for the phone's own home sent with other
        fields: the phone cannot tell that the id is held by someone else.
        """
        own = new_home()
        post(phone.access, own)
        mine = post(phone.access, {**own, "name": "Barn"})
        theirs = post(phone.access, new_home(id=str(bob_home.pk), name="Flat"))
        assert theirs.status_code == mine.status_code == 409
        assert theirs.content == mine.content
        assert "Flat" not in theirs.content.decode()
        connection = AppConnection.objects.get(pk=phone.connection.pk)
        assert not connection.homes.filter(pk=bob_home.pk).exists()
        assert not Membership.objects.filter(home=bob_home, user=phone.connection.user).exists()

    def test_the_id_of_a_home_of_the_account_not_given_to_the_phone_is_409(
        self, alice: User, phone: Connected
    ) -> None:
        """A home of the account that consent did not give the phone is not bound by replaying."""
        cabin = home_services.create_home(alice, name="Cabin")
        answer = post(phone.access, new_home(id=str(cabin.pk), name="Cabin"))
        assert answer.status_code == 409
        assert answer.json()["code"] == "id_taken"
        assert not AppConnection.objects.get(pk=phone.connection.pk).homes.filter(pk=cabin.pk)

    def test_the_id_of_a_deleted_or_erased_home_or_of_a_row_is_409(
        self, alice: User, bob: User, phone: Connected, alice_home: Home
    ) -> None:
        deleted = home_services.create_home(bob, name="Old")
        Home.objects.filter(pk=deleted.pk).update(deleted_at=deleted.created_at)
        erased = home_services.create_home(bob, name="Gone")
        home_services.erase_home(erased)
        room = room_services.create_room(alice, alice_home, name="Hall")
        for taken in (deleted.pk, erased.pk, room.pk):
            answer = post(phone.access, new_home(id=str(taken)))
            assert answer.status_code == 409, taken
            assert answer.json()["code"] == "id_taken"
        assert not Home.all_objects.filter(pk=room.pk).exists()
        assert not Home.all_objects.filter(pk=erased.pk).exists()

    @pytest.mark.parametrize("deleted", [False, True], ids=["live", "deleted"])
    def test_an_id_taken_meanwhile_is_409(
        self, phone: Connected, bob_home: Home, deleted: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Between the check and the write, another connection took the id: still a 409."""
        if deleted:
            Home.objects.filter(pk=bob_home.pk).update(deleted_at=bob_home.created_at)
        monkeypatch.setattr(creating, "_held", lambda home_id: False)
        answer = post(phone.access, new_home(id=str(bob_home.pk)))
        assert answer.status_code == 409
        assert answer.json()["code"] == "id_taken"
        assert Membership.objects.filter(home_id=bob_home.pk).count() == 1


# ---------- the right to create homes, and the credential ----------


class TestRight:
    def test_without_the_scope_homes_create_is_403(self, phone: Connected) -> None:
        """Mutant: a check of `may_create_homes` alone lets this token through."""
        AccessToken.objects.update(scope="sync")
        body = new_home()
        answer = post(phone.access, body)
        assert answer.status_code == 403
        assert answer.json()["code"] == "cannot_create_homes"
        assert not Home.all_objects.filter(pk=body["id"]).exists()

    def test_without_the_right_given_at_consent_is_403(self, phone: Connected) -> None:
        """Mutant: a check of the scope alone lets a right taken back on the web through."""
        AppConnection.objects.filter(pk=phone.connection.pk).update(may_create_homes=False)
        body = new_home()
        answer = post(phone.access, body)
        assert answer.status_code == 403
        assert answer.json()["code"] == "cannot_create_homes"
        assert not Home.all_objects.filter(pk=body["id"]).exists()

    def test_a_connection_refused_the_right_at_consent_is_403(
        self, alice: User, alice_home: Home
    ) -> None:
        refused = connect(alice, [alice_home], scope=CREATE, may_create_homes=False)
        answer = post(refused.access, new_home())
        assert answer.status_code == 403
        assert answer.json()["code"] == "cannot_create_homes"

    @pytest.mark.parametrize("scope", [Scope.READ, Scope.WRITE, Scope.EVENTS])
    def test_an_api_token_is_refused(self, alice: User, alice_home: Home, scope: Scope) -> None:
        _token, raw = token_services.create_token(
            alice, name="t", scopes=[scope], homes=[alice_home], lifetime_days=30
        )
        body = new_home()
        answer = post(raw, body)
        assert answer.status_code == 403
        assert answer.json()["code"] == "sync_needs_connection"
        assert not Home.all_objects.filter(pk=body["id"]).exists()

    def test_a_session_is_refused(self, alice: User) -> None:
        client = Client()
        client.force_login(alice)
        body = new_home()
        answer = client.post(HOMES, json.dumps(body), content_type="application/json", secure=True)
        assert answer.status_code == 403
        assert answer.json()["code"] == "sync_needs_connection"
        assert not Home.all_objects.filter(pk=body["id"]).exists()

    def test_plain_http_from_a_public_peer_is_refused(self, phone: Connected) -> None:
        body = new_home()
        answer = Client().post(
            HOMES,
            json.dumps(body),
            content_type="application/json",
            REMOTE_ADDR="203.0.113.9",
            **bearer(phone.access),
        )
        assert answer.status_code == 403
        assert answer.json()["code"] == "insecure_transport"
        assert not Home.all_objects.filter(pk=body["id"]).exists()


# ---------- the body, validated as the web validates a home ----------

WEB_CASES = [
    {"name": "Cabin", "currency": "EUR", "hemisphere": "north"},
    {"name": "Cabin", "currency": "EUR", "hemisphere": ""},
    {"name": "Cabin", "currency": "eur", "hemisphere": "north"},
    {"name": "Cabin", "currency": "EURO", "hemisphere": "north"},
    {"name": "Cabin", "currency": "", "hemisphere": "north"},
    {"name": "", "currency": "EUR", "hemisphere": "north"},
    {"name": "   ", "currency": "EUR", "hemisphere": "north"},
    {"name": "x" * 120, "currency": "EUR", "hemisphere": "north"},
    {"name": "x" * 121, "currency": "EUR", "hemisphere": "north"},
    {"name": "Cabin", "currency": "EUR", "hemisphere": "east"},
]


class TestBody:
    @pytest.mark.parametrize("fields", WEB_CASES)
    def test_the_web_and_the_phone_accept_the_same_homes(
        self, phone: Connected, fields: dict[str, str]
    ) -> None:
        """The web form of a home and the sync API agree on every case, and on the field."""
        web = HomeForm(data=fields)
        answer = post(phone.access, {"id": str(uuid.uuid4()), **fields})
        if web.is_valid():
            assert answer.status_code == 201, answer.content
        else:
            assert answer.status_code == 400, answer.content
            assert answer.json()["code"] == "malformed"
            assert set(answer.json()["fields"]) == set(web.errors)

    @pytest.mark.parametrize(
        "body",
        [
            {"name": "Cabin", "currency": "EUR"},
            {"id": "not-a-uuid", "name": "Cabin", "currency": "EUR"},
            {"id": "5b7e8a1c2d3f4a5b9c6d7e8f9a0b1c2d", "name": "Cabin", "currency": "EUR"},
            {"id": "x" * 36, "name": "Cabin", "currency": "EUR"},
            {"id": "5b7e8a1c2d3f4a5b9c6d7e8f9a0b1c2d----", "name": "Cabin", "currency": "EUR"},
            {"id": 1, "name": "Cabin", "currency": "EUR"},
            {"id": str(uuid.uuid4()), "name": 7, "currency": "EUR"},
            {"id": str(uuid.uuid4()), "name": "Cabin"},
            {"id": str(uuid.uuid4()), "name": "Cabin", "currency": "EUR", "hemisphere": 1},
            [],
            "not json",
        ],
    )
    def test_a_body_that_is_not_a_home_is_400(self, phone: Connected, body: Any) -> None:
        answer = post(phone.access, body)
        assert answer.status_code == 400
        assert answer.json()["code"] == "malformed"
        assert Home.all_objects.count() == 1  # alice's home only

    def test_another_content_type_is_415(self, phone: Connected) -> None:
        answer = Client().post(HOMES, new_home(), secure=True, **bearer(phone.access))
        assert answer.status_code == 415

    def test_a_body_too_large_is_400(self, phone: Connected) -> None:
        answer = post(phone.access, new_home(notes="x" * 5000))
        assert answer.status_code == 400
        assert answer.json()["code"] == "malformed"
