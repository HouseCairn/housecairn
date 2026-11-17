"""A read-only member is given their own reports only, as the site shows them (docs/adr/0086).

The site shows a read-only member the reports they made and no others (`visible_reports`). The
sync API gave their phone every report of the home, with its photo. Each test fails on the code
before this change: the feed, the snapshot and the files gave the reports of the others, and a
change of role left the phone with what it held.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection as db_connection
from django.db.migrations.executor import MigrationExecutor

from housecairn.accounts.models import User
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Membership, Role
from housecairn.reports import services as report_services
from housecairn.reports.models import Report
from housecairn.rooms import services as room_services
from housecairn.sync import rows as changelog
from housecairn.sync.files import pending_name
from housecairn.sync.models import HomeHead
from housecairn.sync.visibility import view_of
from tests.oauth_support import Connected, connect
from tests.rooms_support import image_bytes, upload
from tests.test_sync_files import get as get_file
from tests.test_sync_files import put as put_file
from tests.test_sync_read_api import changes_url, get, lines_of, snapshot_url

pytestmark = pytest.mark.django_db
pytest_plugins = ("tests.rooms_fixtures",)

REPORT = "reports.report"


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _media(media_root: Path) -> Path:
    return media_root


@pytest.fixture
def carol(make_user: Any) -> User:
    return make_user("carol@example.com")


def report(user: User, home: Home, title: str, *, photo: bool = False) -> Report:
    return report_services.create_report(
        user, home, title=title, photo=upload(image_bytes()) if photo else None
    )


def feed(phone: Connected, home: Home, after: int = 0, **params: Any) -> Any:
    return get(phone.access, changes_url(home.pk), after=after, **params)


def report_ids_in_feed(answer: Any) -> set[str]:
    assert answer.status_code == 200, answer.content
    return {c["op"]["id"] for c in answer.json()["changes"] if c["op"]["t"] == REPORT}


def snapshot(phone: Connected, home: Home) -> list[dict[str, Any]]:
    answer = get(phone.access, snapshot_url(home.pk))
    assert answer.status_code == 200, answer.content
    return lines_of(answer)


def report_ids_in(lines: list[dict[str, Any]]) -> set[str]:
    return {line["id"] for line in lines if line.get("t") == REPORT}


def covers(lines: list[dict[str, Any]]) -> int:
    value: int = lines[-1]["covers"]
    return value


@pytest.fixture
def home_with_reports(alice: User, bob: User, alice_home: Home, add_member: Any) -> dict[str, Any]:
    """Alice owns the home, Bob reads it; each made a report, Alice's with a photo."""
    add_member(alice_home, bob, Role.READ_ONLY)
    room = room_services.create_room(alice, alice_home, name="Kitchen", kind="kitchen")
    alices = report(alice, alice_home, "Leak under the sink", photo=True)
    bobs = report(bob, alice_home, "Door sticks", photo=True)
    return {"room": room, "alices": alices, "bobs": bobs}


# ---------- the feed ----------


def test_the_feed_gives_a_reader_their_own_reports_and_every_other_row(
    bob: User, alice_home: Home, home_with_reports: dict[str, Any]
) -> None:
    reader = connect(bob, [alice_home])
    answer = feed(reader, alice_home)
    assert report_ids_in_feed(answer) == {str(home_with_reports["bobs"].pk)}
    rows = {(c["op"]["t"], c["op"]["id"]) for c in answer.json()["changes"]}
    assert ("rooms.room", str(home_with_reports["room"].pk)) in rows
    assert "Leak under the sink" not in answer.content.decode()


def test_a_member_who_writes_still_gets_every_report(
    alice: User, alice_home: Home, home_with_reports: dict[str, Any]
) -> None:
    owner = connect(alice, [alice_home])
    assert report_ids_in_feed(feed(owner, alice_home)) == {
        str(home_with_reports["alices"].pk),
        str(home_with_reports["bobs"].pk),
    }
    lines = snapshot(owner, alice_home)
    assert report_ids_in(lines) == {
        str(home_with_reports["alices"].pk),
        str(home_with_reports["bobs"].pk),
    }


def test_the_pages_hold_only_what_the_reader_reads_and_lose_nothing(
    alice: User, bob: User, alice_home: Home, add_member: Any
) -> None:
    """A page of `limit` changes the reader reads, never an empty page that says `more`."""
    add_member(alice_home, bob, Role.READ_ONLY)
    mine = [report(bob, alice_home, f"Mine {n}") for n in range(3)]
    for n in range(5):
        report(alice, alice_home, f"Not mine {n}")
    last = report(bob, alice_home, "Mine last")
    reader = connect(bob, [alice_home])
    whole = feed(reader, alice_home).json()["changes"]
    after, pages = 0, []
    while True:
        body = feed(reader, alice_home, after=after, limit=1).json()
        pages.append(body)
        if not body["changes"] or not body["more"]:
            break
        after = body["changes"][-1]["pos"]
    assert all(len(page["changes"]) == 1 for page in pages)
    assert pages[-1]["more"] is False
    # Page by page, the same changes as in one page: none lost, none twice.
    assert [page["changes"][0] for page in pages] == whole
    reports = [c["op"]["id"] for c in whole if c["op"]["t"] == REPORT]
    assert reports == [str(r.pk) for r in [*mine, last]]


def test_the_tombstone_of_a_report_of_another_member_is_not_given(
    alice: User, bob: User, alice_home: Home, home_with_reports: dict[str, Any]
) -> None:
    reader = connect(bob, [alice_home])
    head = feed(reader, alice_home).json()["head"]
    changelog.soft_delete(home_with_reports["alices"])
    changelog.soft_delete(home_with_reports["bobs"])
    answer = feed(reader, alice_home, after=head)
    assert [(c["op"]["id"], c["op"]["k"]) for c in answer.json()["changes"]] == [
        (str(home_with_reports["bobs"].pk), "delete")
    ]
    lines = snapshot(reader, alice_home)
    assert report_ids_in(lines) == {str(home_with_reports["bobs"].pk)}


# ---------- the snapshot ----------


def test_the_snapshot_gives_a_reader_their_own_reports_only(
    bob: User, alice_home: Home, home_with_reports: dict[str, Any]
) -> None:
    reader = connect(bob, [alice_home])
    lines = snapshot(reader, alice_home)
    assert report_ids_in(lines) == {str(home_with_reports["bobs"].pk)}
    assert any(line.get("t") == "rooms.room" for line in lines)
    assert "Leak under the sink" not in str(lines)


# ---------- the files ----------


def test_the_photo_of_a_report_of_another_member_answers_as_an_unknown_id(
    bob: User, alice_home: Home, home_with_reports: dict[str, Any]
) -> None:
    reader = connect(bob, [alice_home])
    alices, bobs = home_with_reports["alices"], home_with_reports["bobs"]
    unknown = get_file(reader.access, alice_home.pk, uuid.uuid4())
    for size in ({}, {"size": "thumbnail"}):
        hidden = get_file(reader.access, alice_home.pk, alices.pk, **size)
        assert hidden.status_code == unknown.status_code == 404
        assert hidden.json() == unknown.json()
        assert get_file(reader.access, alice_home.pk, bobs.pk, **size).status_code == 200


def test_a_member_who_writes_reads_the_photo_of_any_report(
    alice: User, alice_home: Home, home_with_reports: dict[str, Any]
) -> None:
    owner = connect(alice, [alice_home])
    for row in (home_with_reports["alices"], home_with_reports["bobs"]):
        assert get_file(owner.access, alice_home.pk, row.pk).status_code == 200


# ---------- a task made from a report the reader may not see ----------


def test_a_task_made_from_a_hidden_report_is_given_with_its_title_and_place_not_the_report(
    alice: User, bob: User, alice_home: Home, home_with_reports: dict[str, Any]
) -> None:
    """The task keeps the title and the place copied at its making, as the site shows them.

    The report (its row, id, photo) is not given, in the feed nor in the snapshot.
    """
    alices, room = home_with_reports["alices"], home_with_reports["room"]
    alices.room = room
    alices.save(update_fields=["room"])
    task = report_services.make_task(alice, alices)
    assert (task.title, task.room_id) == (alices.title, room.pk)
    reader = connect(bob, [alice_home])

    answer = feed(reader, alice_home)
    ops = [c["op"] for c in answer.json()["changes"]]
    tasks = [op for op in ops if op["t"] == "tasks.task"]
    assert [op["id"] for op in tasks] == [str(task.pk)]
    assert tasks[0]["f"]["title"] == "Leak under the sink"
    assert tasks[0]["f"]["room_id"] == str(room.pk)
    lines = snapshot(reader, alice_home)
    in_snapshot = [line for line in lines if line.get("t") == "tasks.task"]
    assert [line["id"] for line in in_snapshot] == [str(task.pk)]

    assert str(alices.pk) not in report_ids_in_feed(answer)
    assert report_ids_in(lines) == {str(home_with_reports["bobs"].pk)}
    # Nowhere in either answer: the id of the report, nor the name of its photo.
    for given in (answer.content.decode(), str(lines)):
        assert str(alices.pk) not in given
        assert alices.photo.name not in given
    assert get_file(reader.access, alice_home.pk, alices.pk).status_code == 404


def test_the_bytes_waiting_under_the_id_of_a_hidden_photo_are_not_given(
    alice: User, bob: User, alice_home: Home, home_with_reports: dict[str, Any]
) -> None:
    """A file uploaded but not yet taken by a row, which a hidden report already names.

    The row says the file is its photo: the reader is told 404 and not given the bytes that wait
    in the sync folder under that id, which the owner is given.
    """
    waiting = uuid.uuid4()
    data = image_bytes()
    owner = connect(alice, [alice_home])
    assert (
        put_file(owner.access, alice_home.pk, waiting, data, content_type="image/png").status_code
        == 201
    )
    Report.objects.filter(pk=home_with_reports["alices"].pk).update(
        photo=pending_name(alice_home.pk, waiting)
    )

    assert get_file(owner.access, alice_home.pk, waiting).status_code == 200
    reader = connect(bob, [alice_home])
    hidden = get_file(reader.access, alice_home.pk, waiting)
    unknown = get_file(reader.access, alice_home.pk, uuid.uuid4())
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json()


# ---------- the site and the sync API read one rule ----------


def test_the_site_and_the_sync_view_give_each_member_the_same_reports(
    alice: User, bob: User, carol: User, alice_home: Home, add_member: Any
) -> None:
    """`visible_reports` (site) and `view_of` (sync) agree for a reader, a member and an owner.

    Each reads `visible_among`: if one stops and the rule changes, they part.
    """
    add_member(alice_home, bob, Role.READ_ONLY)
    add_member(alice_home, carol, Role.MEMBER)
    bobs = [report(bob, alice_home, "Door"), report(bob, alice_home, "Gone door")]
    others = [
        report(alice, alice_home, "Leak"),
        report(alice, alice_home, "Made a task"),
        report(carol, alice_home, "Window"),
        report(carol, alice_home, "Gone window"),
    ]
    report_services.make_task(alice, others[1])
    deleted = {bobs[1].pk, others[3].pk}
    for row in (bobs[1], others[3]):
        changelog.soft_delete(row)
    everyone = {r.pk for r in [*bobs, *others]}

    for user in (bob, carol, alice):
        site = set(report_services.visible_reports(user, alice_home).values_list("pk", flat=True))
        view = view_of(user, alice_home)
        sync = everyone if view.is_whole else view.report_ids()
        assert sync - deleted == site, user.email
        # The phone is given the tombstone of the deleted reports it may see, and no other.
        assert sync & deleted == (deleted if view.is_whole else {bobs[1].pk})
        assert report_ids_in(snapshot(connect(user, [alice_home]), alice_home)) == {
            str(pk) for pk in sync
        }


# ---------- a change of role ----------


def test_a_member_made_read_only_starts_again_without_the_reports_of_the_others(
    alice: User, bob: User, alice_home: Home, add_member: Any
) -> None:
    membership = add_member(alice_home, bob, Role.MEMBER)
    alices = report(alice, alice_home, "Leak")
    bobs = report(bob, alice_home, "Door")
    phone = connect(bob, [alice_home])
    first = feed(phone, alice_home)
    assert report_ids_in_feed(first) == {str(alices.pk), str(bobs.pk)}
    cursor = first.json()["head"]

    home_services.change_role(alice, membership, Role.READ_ONLY)

    expired = feed(phone, alice_home, after=cursor)
    assert (expired.status_code, expired.json()["code"]) == (410, "cursor_expired")
    lines = snapshot(phone, alice_home)
    assert report_ids_in(lines) == {str(bobs.pk)}
    again = feed(phone, alice_home, after=covers(lines))
    assert again.status_code == 200
    assert again.json()["changes"] == []


def test_a_reader_made_member_starts_again_with_every_report(
    alice: User, bob: User, alice_home: Home, add_member: Any
) -> None:
    membership = add_member(alice_home, bob, Role.READ_ONLY)
    alices = report(alice, alice_home, "Leak")
    bobs = report(bob, alice_home, "Door")
    phone = connect(bob, [alice_home])
    first = feed(phone, alice_home)
    assert report_ids_in_feed(first) == {str(bobs.pk)}

    home_services.change_role(alice, membership, Role.MEMBER)

    expired = feed(phone, alice_home, after=first.json()["head"])
    assert (expired.status_code, expired.json()["code"]) == (410, "cursor_expired")
    lines = snapshot(phone, alice_home)
    assert report_ids_in(lines) == {str(alices.pk), str(bobs.pk)}
    assert feed(phone, alice_home, after=covers(lines)).status_code == 200


def test_a_cursor_at_the_head_before_the_change_expires_too(
    alice: User, bob: User, alice_home: Home, add_member: Any
) -> None:
    """The phone read everything as a member: its cursor is the head, and nothing came since."""
    membership = add_member(alice_home, bob, Role.MEMBER)
    report(alice, alice_home, "Leak")
    phone = connect(bob, [alice_home])
    cursor = feed(phone, alice_home).json()["head"]
    home_services.change_role(alice, membership, Role.READ_ONLY)
    assert feed(phone, alice_home, after=cursor).status_code == 410


def test_a_change_of_role_that_keeps_writing_starts_nobody_again(
    alice: User, bob: User, carol: User, alice_home: Home, add_member: Any
) -> None:
    """Member to owner, and the phone of another member, keep their cursor."""
    membership = add_member(alice_home, bob, Role.MEMBER)
    add_member(alice_home, carol, Role.MEMBER)
    report(alice, alice_home, "Leak")
    bobs, carols = connect(bob, [alice_home]), connect(carol, [alice_home])
    cursor = feed(bobs, alice_home).json()["head"]
    home_services.change_role(alice, membership, Role.OWNER)
    assert feed(bobs, alice_home, after=cursor).status_code == 200
    reader = home_services.change_role(alice, membership, Role.READ_ONLY)
    assert reader.role == Role.READ_ONLY
    assert feed(carols, alice_home, after=cursor).status_code == 200


def test_joining_again_starts_the_phone_again(
    alice: User, bob: User, alice_home: Home, add_member: Any
) -> None:
    from allauth.account.models import EmailAddress

    membership = add_member(alice_home, bob, Role.MEMBER)
    report(alice, alice_home, "Leak")
    phone = connect(bob, [alice_home])
    cursor = feed(phone, alice_home).json()["head"]
    home_services.remove_member(alice, membership)
    _invitation, raw = home_services.invite(
        alice, alice_home, email=bob.email, role="read_only", delivery=home_services.Delivery()
    )
    assert raw is not None
    EmailAddress.objects.update_or_create(
        user=bob, email=bob.email, defaults={"verified": True, "primary": True}
    )
    home_services.accept_invitation(bob, raw)
    assert feed(phone, alice_home, after=cursor).status_code == 410


# ---------- other homes, other people ----------


def test_an_instance_administrator_not_a_member_reads_nothing_of_the_home(
    alice_home: Home, home_with_reports: dict[str, Any]
) -> None:
    admin = User.objects.create_superuser(email="admin@example.com", password="x" * 20)
    own = home_services.create_home(admin, name="Admin's")
    phone = connect(admin, [own])
    assert feed(phone, alice_home).status_code == 404
    assert get(phone.access, snapshot_url(alice_home.pk)).status_code == 404
    hidden = get_file(phone.access, alice_home.pk, home_with_reports["alices"].pk)
    assert hidden.status_code == 404


def test_the_view_of_a_reader_is_that_of_each_home(
    alice: User, bob: User, carol: User, alice_home: Home, bob_home: Home, add_member: Any
) -> None:
    """Bob reads Alice's home and owns his own, where Carol reports: he gets all of his."""
    add_member(alice_home, bob, Role.READ_ONLY)
    add_member(bob_home, carol, Role.READ_ONLY)
    alices = report(alice, alice_home, "Leak", photo=True)
    carols = report(carol, bob_home, "Window", photo=True)
    phone = connect(bob, [alice_home, bob_home])
    assert report_ids_in_feed(feed(phone, alice_home)) == set()
    assert report_ids_in_feed(feed(phone, bob_home)) == {str(carols.pk)}
    assert report_ids_in(snapshot(phone, bob_home)) == {str(carols.pk)}
    assert get_file(phone.access, bob_home.pk, carols.pk).status_code == 200
    assert get_file(phone.access, alice_home.pk, alices.pk).status_code == 404
    # The photo of a report of a home asked through another home is not found either.
    assert get_file(phone.access, bob_home.pk, alices.pk).status_code == 404


# ---------- the readers of before ----------


@pytest.mark.django_db(transaction=True)
def test_the_readers_of_before_start_again_once() -> None:
    """The migration: each read-only member's view starts after the head of the home."""
    from housecairn.sync.models import MemberView  # read here: the table is new

    alice = User.objects.create_user(email="alice@example.com", password="x" * 20)
    bob = User.objects.create_user(email="bob@example.com", password="x" * 20)
    home = home_services.create_home(alice, name="Main house")
    Membership.objects.create(home=home, user=bob, role=Role.READ_ONLY)
    report(alice, home, "Leak")
    head = HomeHead.objects.get(home=home).pos
    executor = MigrationExecutor(db_connection)
    try:
        executor.migrate([("sync", "0004_sync_files")])
        executor = MigrationExecutor(db_connection)
        executor.migrate(executor.loader.graph.leaf_nodes("sync"))
        assert HomeHead.objects.get(home=home).pos == head + 1
        assert list(MemberView.objects.values_list("user_id", "since")) == [(bob.pk, head + 1)]
    finally:
        call_command("migrate", verbosity=0)
