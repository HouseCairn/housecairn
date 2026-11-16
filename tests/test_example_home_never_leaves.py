"""An example home never leaves its device (docs/adr/0084).

The rule: a row of an example home is never an operation, never in a snapshot, never in an
export and never in a backup of the server. One test per way out; each makes an example home
with every row of `design/guide.json` through the services. The backup has its own file
(`tests/test_example_home_backup.py`), and the write recorded by no change goes through the
scenarios of the guard of the change log (`tests/test_sync_guard.py`).
"""

import datetime
import io
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.apitokens import services as token_services
from housecairn.apitokens.models import Scope
from housecairn.audit.models import AuditEvent
from housecairn.calendar import feeds, ics
from housecairn.calendar.models import Feed
from housecairn.events import services as event_services
from housecairn.guide import services as guide_services
from housecairn.guide import states
from housecairn.guide.models import StepTick
from housecairn.homes import csv_export, exports, jobs
from housecairn.homes import services as home_services
from housecairn.homes.models import ExportJob, Home, Membership, Role
from housecairn.labels import services as label_services
from housecairn.notifications import alerts, reminders, services
from housecairn.notifications.models import Preference
from housecairn.reports import services as report_services
from housecairn.reports.models import Report
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Room
from housecairn.sync import changes, pushing, repairs, retention
from housecairn.sync.models import Change, ErasedHome, HomeHead, RowClocks
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Occurrence
from tests.example_support import make_example, write_a_file_in_every_folder
from tests.oauth_support import Connected, connect
from tests.test_sync_push_api import DEVICE, op, result_of, send
from tests.test_sync_read_api import changes_url, get, snapshot_url

pytestmark = pytest.mark.django_db
pytest_plugins = ("tests.rooms_fixtures",)

HOMES = "/api/v1/sync/homes/"


def nothing_logged(home: Home) -> bool:
    return not (
        Change.objects.filter(home=home).exists()
        or HomeHead.objects.filter(home=home).exists()
        or RowClocks.objects.filter(home=home).exists()
    )


@pytest.fixture
def example(alice: User) -> Home:
    return make_example(alice)


def test_the_example_home_is_whole_and_logs_nothing(alice: User, example: Home) -> None:
    """Every row of the file is there, and the change log of the home is empty."""
    assert example.is_example
    assert Room.objects.filter(home=example).count() == 2
    assert Occurrence.objects.filter(home=example, status="done").count() == 1
    assert Report.objects.filter(home=example, status="resolved").count() == 1
    assert nothing_logged(example)
    assert Change.objects.count() == 0


# ---------- a write records no change: the other callers of record_change ----------


def an_old_tombstone(home: Home) -> Room:
    """A room of the home, soft-deleted, with a tombstone old enough for the retention sweep."""
    room = Room.all_objects.create(
        home=home, name="Gone", deleted_at=datetime.datetime.now(datetime.UTC)
    )
    RowClocks.objects.create(
        home=home,
        entity="rooms.room",
        row_id=room.pk,
        tombstoned_at=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
    )
    return room


def test_a_push_records_nothing_for_the_example_home(alice: User, example: Home) -> None:
    room = Room.objects.filter(home=example).first()
    assert room is not None
    pusher = pushing.Pusher(alice, Role.OWNER, bytes(range(16)))
    stamp = str(changes.clock.format_clock(changes.clock.make_clock(changes.clock.wall_ms(), 0)))
    fresh = uuid.uuid4()
    answer = pushing.apply(
        example,
        pusher,
        [
            op(example.pk, "rooms.room", room.pk, {"name": "Pushed name"}, c=stamp),
            op(example.pk, "rooms.room", fresh, {"name": "Pushed room", "kind": "other"}, c=stamp),
            op(example.pk, "rooms.room", room.pk, k="delete", c=stamp),
        ],
    )
    assert {r["status"] for r in answer["results"]} == {"ignored"}
    assert nothing_logged(example)
    assert Room.objects.get(pk=room.pk).name != "Pushed name"
    assert not Room.all_objects.filter(pk=fresh).exists()


def test_a_repair_records_nothing_for_the_example_home(alice: User, example: Home) -> None:
    room = Room.objects.filter(home=example).first()
    assert room is not None
    session = repairs.Session(example, alice)
    session._repair(room, {"name": "Repaired"})
    assert Room.objects.get(pk=room.pk).name == "Repaired"
    assert nothing_logged(example)


def test_a_retention_sweep_records_nothing_for_the_example_home(alice: User, example: Home) -> None:
    """The sweep purges a soft-deleted room and empties what points at it: no change."""
    gone = an_old_tombstone(example)
    report = Report.objects.filter(home=example).first()
    assert report is not None
    Report.objects.filter(pk=report.pk).update(room=gone)
    purged = retention.purge()
    assert purged.rows >= 1
    assert not Room.all_objects.filter(pk=gone.pk).exists()
    assert Report.objects.get(pk=report.pk).room_id is None
    assert not Change.objects.filter(home=example).exists()
    assert not HomeHead.objects.filter(home=example).exists()


def test_a_real_home_still_records_its_changes(alice: User, alice_home: Home) -> None:
    """The control of the tests above: the same writes on a real home record a change."""
    room = room_services.create_room(alice, alice_home, name="Hall")
    assert Change.objects.filter(home=alice_home, row_id=room.pk).exists()
    assert changes.record_change(
        alice_home, "rooms.room", room.pk, changes.PUT, {"name": "x"}
    ).change


# ---------- no snapshot, no list, closed routes ----------


def test_the_sync_homes_list_leaves_out_the_example_home(
    alice: User, alice_home: Home, example: Home
) -> None:
    phone = connect(alice, [alice_home])
    phone.connection.homes.add(example)  # bound to it, as an older server could have
    body = get(phone.access, HOMES).json()
    assert [home["id"] for home in body["homes"]] == [str(alice_home.pk)]
    assert str(example.pk) not in json.dumps(body)


def test_the_api_homes_list_leaves_out_the_example_home(
    alice: User, alice_home: Home, example: Home, api_client: APIClient
) -> None:
    api_client.force_authenticate(alice)
    listed = api_client.get(reverse("api-v1:home-list")).json()["results"]
    assert [home["id"] for home in listed] == [str(alice_home.pk)]
    for name in ("home-detail", "home-rooms"):
        try:
            url = reverse(f"api-v1:{name}", kwargs={"home_id": example.pk})
        except Exception:  # noqa: S112 - a route this API does not have
            continue
        assert api_client.get(url).status_code == 404, name
    # A token bound to it, written straight in the database, reaches nothing either.
    token, secret = token_services.issue_token(
        alice, name="t", scopes=[Scope.READ], homes=[alice_home]
    )
    token.homes.add(example)
    bearer = APIClient()
    bearer.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
    assert (
        bearer.get(reverse("api-v1:home-detail", kwargs={"home_id": example.pk})).status_code == 404
    )
    seen = [home["id"] for home in bearer.get(reverse("api-v1:home-list")).json()["results"]]
    assert str(example.pk) not in seen


def test_the_four_sync_routes_answer_404_for_the_example_home(
    alice: User, alice_home: Home, example: Home
) -> None:
    phone: Connected = connect(alice, [alice_home])
    phone.connection.homes.add(example)
    assert get(phone.access, changes_url(example.pk)).status_code == 404
    assert get(phone.access, snapshot_url(example.pk)).status_code == 404
    pushed = send(
        phone.access, example.pk, [op(example.pk, "rooms.room", uuid.uuid4(), {"name": "x"})]
    )
    assert pushed.status_code == 404
    file_url = f"/api/v1/sync/homes/{example.pk}/files/{uuid.uuid4()}"
    assert get(phone.access, file_url).status_code == 404
    put = Client().put(
        file_url,
        b"x",
        content_type="application/pdf",
        secure=True,
        HTTP_AUTHORIZATION=f"Bearer {phone.access}",
    )
    assert put.status_code == 404
    # The same routes answer for the real home of the same connection.
    assert get(phone.access, changes_url(alice_home.pk)).status_code == 200
    assert nothing_logged(example)


def test_a_pushed_is_example_is_rejected_as_forbidden(alice: User, alice_home: Home) -> None:
    phone = connect(alice, [alice_home])
    answer = send(
        phone.access,
        alice_home.pk,
        [op(alice_home.pk, "homes.home", alice_home.pk, {"is_example": True})],
    )
    result = result_of(answer)
    assert (result["status"], result["code"]) == ("rejected", "forbidden")
    assert not Home.objects.get(pk=alice_home.pk).is_example
    assert DEVICE  # the device of the push is the one of the helper


def test_the_mark_is_not_a_field_of_the_protocol(alice: User, alice_home: Home) -> None:
    from housecairn.sync import reading

    lines = list(reading.snapshot_lines(alice_home))
    assert "is_example" not in json.dumps(lines)


# ---------- erasing ----------


def test_removing_the_example_home_leaves_no_erased_home(
    alice: User, example: Home, django_capture_on_commit_callbacks: Any, media_root: Path
) -> None:
    """No row of erased home, no new audit entry, none of its audit entries, no file, no tick."""
    report_services.create_report(alice, example, title="The hinge squeaks")
    assert AuditEvent.objects.filter(home_id=example.pk, action="report.created").exists()
    assert StepTick.objects.filter(home=example).exists()
    written = write_a_file_in_every_folder(example, media_root)
    audits_of_others = AuditEvent.objects.exclude(home_id=example.pk).count()
    with django_capture_on_commit_callbacks(execute=True):
        home_services.delete_home(alice, example, confirmation="")
    assert not Home.all_objects.filter(pk=example.pk).exists()
    assert not ErasedHome.objects.exists()
    assert not AuditEvent.objects.filter(home_id=example.pk).exists()
    assert not AuditEvent.objects.filter(action="home.deleted").exists()
    assert AuditEvent.objects.exclude(home_id=example.pk).count() == audits_of_others
    assert not StepTick.objects.exists()
    assert not any(path.exists() for path in written)
    assert not any(path.parent.exists() for path in written)


def test_deleting_the_account_takes_the_example_home_with_it(alice: User, example: Home) -> None:
    from housecairn.accounts import services as account_services

    assert account_services.homes_blocking_erasure(alice) == []
    account_services.delete_account(alice)
    assert not Home.all_objects.filter(pk=example.pk).exists()
    assert not ErasedHome.objects.exists()


def test_a_real_home_still_needs_its_name_and_offers_its_export(
    alice: User, alice_home: Home
) -> None:
    """The waiver is for the example home only: a real home keeps the typed name and the export."""
    with pytest.raises(ValidationError):
        home_services.delete_home(alice, alice_home, confirmation="")
    with pytest.raises(ValidationError):
        home_services.delete_home(alice, alice_home, confirmation="Not its name")
    assert Home.objects.filter(pk=alice_home.pk).exists()
    archive, _name = exports.export_home(alice, alice_home)  # the export is still offered
    assert archive.read(2) == b"PK"
    home_services.delete_home(alice, alice_home, confirmation=alice_home.name)
    assert ErasedHome.objects.filter(pk=alice_home.pk).exists()


def test_only_the_maker_may_remove_the_example_home(alice: User, bob: User, example: Home) -> None:
    with pytest.raises(PermissionDenied):
        home_services.delete_home(bob, example, confirmation="")
    assert Home.objects.filter(pk=example.pk).exists()
    with pytest.raises(ValueError, match="Only the example home"):
        guide_services.remove_example(alice, Home.objects.create(name="Real"))


# ---------- exports ----------


def test_export_and_csv_refuse_the_example_home(alice: User, example: Home, client: Client) -> None:
    for call in (
        lambda: exports.export_home(alice, example),
        lambda: exports.build_archive(example),
        lambda: jobs.start_export(alice, example),
        lambda: exports.write_archive(example, io.BytesIO()),
    ):
        with pytest.raises(exports.ExampleHomeError):
            call()
    assert not ExportJob.all_objects.filter(home=example).exists()
    for entity in csv_export.ENTITIES.values():
        with pytest.raises(exports.ExampleHomeError):
            csv_export.build_csv(example, entity)
    client.force_login(alice)
    assert client.post(reverse("homes:export", kwargs={"home_id": example.pk})).status_code == 403
    csv_url = reverse("homes:export-csv", kwargs={"home_id": example.pk, "entity": "rooms"})
    assert client.get(csv_url).status_code == 403


def test_a_restore_of_a_home_never_makes_an_example_home(alice: User, alice_home: Home) -> None:
    """An archive carries no mark; whatever its document says, the field reads false."""
    import zipfile

    from housecairn.homes import restore

    archive, _name = exports.export_home(alice, alice_home)
    original = zipfile.ZipFile(archive)
    home_document = json.loads(original.read("data/homes.json"))
    home_document["home"]["is_example"] = True
    home_document["home"]["name"] = "Marked"
    assert "is_example" not in json.dumps(json.loads(original.read("data/homes.json"))["home"])
    manifest = json.loads(original.read("manifest.json"))
    import hashlib

    raw = json.dumps(home_document, indent=2, ensure_ascii=False).encode()
    manifest["entries"]["data/homes.json"] = {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
    }
    forged = io.BytesIO()
    with zipfile.ZipFile(forged, "w") as target:
        for entry in original.namelist():
            if entry == "data/homes.json":
                target.writestr(entry, raw)
            elif entry == "manifest.json":
                target.writestr(entry, json.dumps(manifest))
            else:
                target.writestr(entry, original.read(entry))
    forged.seek(0)
    result = restore.restore_home(alice, forged, as_copy=True, name="Restored")
    assert not result.home.is_example
    assert not Home.all_objects.filter(is_example=True).exists()


# ---------- calendar, messages, labels, tokens ----------


def test_the_calendar_feeds_leave_the_example_home_out(
    alice: User, alice_home: Home, example: Home
) -> None:
    task_services.create_task(
        alice,
        alice_home,
        title="Service the boiler",
        due_on=task_services.today(),
        repeat_mode="fixed",
        repeat_unit="year",
        repeat_interval=1,
    )
    feed, _secret = feeds.create_feed(alice)  # the combined feed
    body = ics.render(feed, "https://example.com").decode()
    assert "Service the boiler" in body
    assert "Descaling" not in body
    assert str(example.pk) not in body
    with pytest.raises(PermissionDenied):
        feeds.create_feed(alice, example)
    stray = Feed.objects.create(user=alice, home=example, digest="d" * 64)
    assert stray.home_id == example.pk
    assert feeds.find_feed("a-secret-that-has-no-feed") is None
    assert list(ics._covered(stray)) == []
    assert example.pk not in {home.pk for home in feeds.feed_homes(alice)}


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    box: list[tuple[str, str]] = []

    def record(sealed: str, *, title: str, body: str) -> bool:
        box.append((title, body))
        return True

    monkeypatch.setattr(reminders.outbound, "send", record)
    monkeypatch.setattr(alerts.outbound, "send", record)
    return box


@override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=True, TIME_ZONE="UTC")
def test_no_reminder_push_apprise_webhook_event_or_alert_is_sent_for_it(
    alice: User,
    bob: User,
    alice_home: Home,
    example: Home,
    outbox: list[tuple[str, str]],
    api_client: APIClient,
) -> None:
    url = "tgram://123456789:fixture-not-a-token/987654"
    for person in (alice, bob):
        services.add_channel(person, name="Phone", url=url)
        Preference.objects.update_or_create(
            user=person, defaults={"time_zone": "UTC", "days_before": 0}
        )
    today = task_services.today()
    # The control: a task of a real home sends a reminder, a task of the example home does not.
    task_services.create_task(alice, alice_home, title="Real task", due_on=today)
    task_services.create_task(alice, example, title="Descaling due today", due_on=today)
    noon = datetime.datetime.combine(today, datetime.time(12), tzinfo=datetime.UTC)
    assert reminders.dispatch(noon) == 1
    assert outbox
    assert "Descaling" not in str(outbox)
    outbox.clear()
    assert reminders.dispatch(noon + datetime.timedelta(days=30)) <= 1
    assert "Descaling" not in str(outbox)
    # A report in the example home alerts nobody, even a member who could be told.
    Membership.objects.create(home=example, user=bob, role=Role.MEMBER)
    report = report_services.create_report(alice, example, title="The tap leaks")
    assert alerts.report_created(str(report.pk)) == 0
    control = report_services.create_report(alice, alice_home, title="The door sticks")
    Membership.objects.create(home=alice_home, user=bob, role=Role.MEMBER)
    assert alerts.report_created(str(control.pk)) == 1
    assert not any("The tap leaks" in body for _title, body in outbox)
    # An event of Home Assistant, by the service and by the route.
    with pytest.raises(PermissionDenied):
        event_services.record_event(alice, example, kind="smoke", payload={})
    api_client.force_authenticate(alice)
    answer = api_client.post(
        reverse("api-v1:home-events", kwargs={"home_id": example.pk}),
        {"kind": "smoke", "payload": {}},
        format="json",
    )
    assert answer.status_code == 404


def test_labels_and_api_tokens_cannot_reach_the_example_home(
    alice: User, alice_home: Home, example: Home, client: Client
) -> None:
    room = Room.objects.filter(home=example).first()
    assert room is not None
    with pytest.raises(PermissionDenied):
        label_services.create_label(alice, example, kind="room", target_id=room.pk)
    client.force_login(alice)
    assert client.get(reverse("labels:list", kwargs={"home_id": example.pk})).status_code == 404
    assert client.get(reverse("labels:list", kwargs={"home_id": alice_home.pk})).status_code == 200
    with pytest.raises(PermissionDenied):
        token_services.issue_token(alice, name="x", scopes=[Scope.READ], homes=[example])
    with pytest.raises(PermissionDenied):
        token_services.issue_token(
            alice, name="x", scopes=[Scope.READ], homes=[alice_home, example]
        )
    assert example not in set(token_services.token_homes(alice))
    with pytest.raises(PermissionDenied):
        feeds.create_feed(alice, example)


# ---------- nothing writes the mark ----------


def test_no_form_and_no_api_writes_is_example(
    alice: User, client: Client, api_client: APIClient
) -> None:
    from housecairn.homes import forms as home_forms

    for form_class in (home_forms.HomeForm, home_forms.NewHomeForm):
        assert "is_example" not in form_class().fields
    client.force_login(alice)
    page = client.post(
        reverse("homes:create"),
        {"name": "Forged", "currency": "EUR", "hemisphere": "north", "is_example": "on"},
    )
    assert page.status_code == 302
    assert not Home.objects.get(name="Forged").is_example
    api_client.force_authenticate(alice)
    made = api_client.post(
        reverse("api-v1:home-list"),
        {"name": "Forged by API", "currency": "EUR", "is_example": True},
        format="json",
    )
    assert made.status_code == 201
    assert "is_example" not in made.json()
    assert not Home.objects.get(name="Forged by API").is_example
    detail = reverse("api-v1:home-detail", kwargs={"home_id": made.json()["id"]})
    api_client.patch(detail, {"is_example": True}, format="json")
    assert not Home.objects.get(name="Forged by API").is_example
    # A phone makes a home through the sync API: the mark is not one of its fields.
    phone = connect(
        alice, [Home.objects.get(name="Forged")], scope="sync homes.create", may_create_homes=True
    )
    created = Client().post(
        HOMES,
        json.dumps(
            {"id": str(uuid.uuid4()), "name": "Phone", "currency": "EUR", "is_example": True}
        ),
        content_type="application/json",
        secure=True,
        HTTP_AUTHORIZATION=f"Bearer {phone.access}",
    )
    assert created.status_code in (201, 400)
    assert not Home.all_objects.filter(name="Phone", is_example=True).exists()
    assert not Home.all_objects.filter(is_example=True).exists()


# ---------- one per account, catching up ----------


def test_one_example_home_per_account(alice: User, bob: User, example: Home) -> None:
    with pytest.raises(ValidationError):
        guide_services.create_example_home(alice)
    assert Home.objects.filter(is_example=True, memberships__user=alice).count() == 1
    assert guide_services.example_home_of(alice) == example
    # Another account has its own; deleting the first lets the account make another.
    other = guide_services.create_example_home(bob)
    assert other != example
    home_services.delete_home(alice, example, confirmation="")
    assert guide_services.example_home_of(alice) is None
    assert guide_services.create_example_home(alice).is_example


def test_only_its_maker_is_in_the_example_home(alice: User, example: Home) -> None:
    with pytest.raises(ValidationError):
        home_services.invite(
            alice,
            example,
            email="guest@example.com",
            role=Role.MEMBER,
            delivery=home_services.Delivery(),
        )
    assert list(Membership.objects.filter(home=example).values_list("user", flat=True)) == [
        alice.pk
    ]


@pytest.mark.parametrize("language", ["en", "fr"])
def test_fill_example_ticks_nothing_ahead(alice: User, language: str) -> None:
    """Catching up to scenario n adds the rows of the scenarios before it, and no later one."""
    from housecairn.guide import content

    steps_by_scenario = {
        number: [step["id"] for step in scenario["steps"]]
        for number, scenario in enumerate(content.scenarios(), start=1)
    }
    home = guide_services.create_example_home(alice, language=language)
    done: set[str] = set()
    for up_to in range(2, 6):
        guide_services.fill_example(alice, home, up_to=up_to, language=language)
        ticked = states.ticked(home)
        later = {step for n, ids in steps_by_scenario.items() if n >= up_to for step in ids}
        assert not ticked & later, (up_to, sorted(ticked & later))
        assert ticked >= done
        done = ticked
    # Catching up again adds nothing: every row is found, none is made twice.
    count = Room.objects.filter(home=home).count()
    guide_services.fill_example(alice, home, up_to=5, language=language)
    assert Room.objects.filter(home=home).count() == count == 2
    # The language of the person is that of the rows.
    names = set(Room.objects.filter(home=home).values_list("name", flat=True))
    assert names == ({"Kitchen", "Bathroom"} if language == "en" else {"Cuisine", "Salle de bain"})
    assert nothing_logged(home)


def test_a_person_who_made_the_rooms_themselves_is_not_given_them_twice(
    alice: User,
) -> None:
    home = guide_services.create_example_home(alice, language="en")
    room_services.create_room(alice, home, name="Kitchen", kind="kitchen")
    guide_services.fill_example(alice, home, up_to=3, language="en")
    assert list(Room.objects.filter(home=home).order_by("name").values_list("name", flat=True)) == [
        "Bathroom",
        "Kitchen",
    ]


def test_the_example_home_is_left_out_of_the_lists_of_homes(
    alice: User, alice_home: Home, example: Home
) -> None:
    assert list(home_services.homes_for(alice)) == [alice_home]
    assert set(home_services.homes_for(alice, include_example=True)) == {alice_home, example}
    assert list(home_services.real_homes(Home.objects.all())) == [alice_home]
    assert Home.objects.filter(pk=example.pk).exists()  # it still exists, as a home of its own


def test_a_function_that_records_a_change_gives_nothing_for_the_example_home(
    example: Home,
) -> None:
    done = changes.record_change(example.pk, "rooms.room", uuid.uuid4(), changes.PUT, {"name": "x"})
    assert done.change is None
    assert done.lost == {}
    assert nothing_logged(example)


def test_the_web_pages_of_the_example_home_open_and_the_api_does_not(
    alice: User, example: Home, client: Client, api_client: APIClient
) -> None:
    client.force_login(alice)
    for name in ("homes:detail", "rooms:list", "tasks:today"):
        assert client.get(reverse(name, kwargs={"home_id": example.pk})).status_code == 200, name
    api_client.force_authenticate(alice)
    detail = reverse("api-v1:home-detail", kwargs={"home_id": example.pk})
    assert api_client.get(detail).status_code == 404


def test_all_homes_and_the_budget_and_calendar_of_all_homes_leave_the_example_home_out(
    alice: User, alice_home: Home, example: Home, client: Client, api_client: APIClient
) -> None:
    """Today of all the homes, the budget of all the homes and the calendar list only real ones."""
    second = home_services.create_home(alice, name="Second house")
    task_services.create_task(
        alice, second, title="Clean the gutters", due_on=task_services.today()
    )
    client.force_login(alice)
    session = client.session
    session["housecairn.current_all"] = True
    session.save()
    page = client.get(reverse("index")).content.decode()
    assert "Second house" in page
    assert "Descaling" not in page
    assert "Example house" not in page
    api_client.force_authenticate(alice)
    mine = api_client.get(reverse("api-v1:my-home")).json()
    assert "Descaling" not in json.dumps(mine)
    combined = api_client.get(reverse("api-v1:budget-combined")).json()
    seen = {home["id"] for row in combined for home in row["homes"]}
    assert str(example.pk) not in seen
    calendar = api_client.get(
        reverse("api-v1:calendar"),
        {"from": str(task_services.today()), "to": str(task_services.today())},
    ).json()
    assert "Descaling" not in json.dumps(calendar)
    assert "Clean the gutters" in json.dumps(calendar)
