"""The photo of a report and the label of an article as files of the sync API (docs/adr/0085).

Both were sent in the feed as a reference named by the id of their row, but the routes of the
files (lot M6) served and took neither: a phone could not fetch the photo of a report made on
the web, nor send one, and a reference it pushed was kept in `extra`. Each test fails on the
code before this change: `GET` answered 404, and a pushed reference wrote no column.
"""

import io
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from PIL import Image

from housecairn.accounts.models import User
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Role
from housecairn.reports import services as report_services
from housecairn.reports.models import Report
from housecairn.rooms import services as room_services
from housecairn.stock import services as stock_services
from housecairn.stock.models import Article
from housecairn.sync import reading
from housecairn.sync.models import Change, RowClocks, SyncFile
from tests.example_support import make_example
from tests.oauth_support import Connected, connect
from tests.rooms_support import image_bytes, upload
from tests.test_sync_files import PDF, body_of, get, push, put, reference, results, sha, stamp

pytestmark = pytest.mark.django_db
pytest_plugins = ("tests.rooms_fixtures",)

REPORT = "reports.report"
ARTICLE = "stock.article"
# The type of row, its file field and the folder of its app under MEDIA_ROOT.
KINDS = [(REPORT, "photo", "reports"), (ARTICLE, "label", "stock")]


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Any:
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _media(media_root: Path) -> Path:
    return media_root


@pytest.fixture
def phone(alice: User, alice_home: Home) -> Connected:
    return connect(alice, [alice_home])


def web_report(user: User, home: Home, data: bytes | None = None) -> Report:
    return report_services.create_report(
        user, home, title="Leak", photo=upload(data or image_bytes())
    )


def web_article(user: User, home: Home, data: bytes | None = None) -> Article:
    return stock_services.create_article(
        user, home, name="Filter", label=upload(data or image_bytes())
    )


def web_row(entity: str, user: User, home: Home, data: bytes | None = None) -> Any:
    return (web_report if entity == REPORT else web_article)(user, home, data)


def model_of(entity: str) -> Any:
    return Report if entity == REPORT else Article


def row_op(home: Home, entity: str, row_id: Any, file: Any, **fields: Any) -> dict[str, Any]:
    field = "photo" if entity == REPORT else "label"
    base = {"title": "Leak"} if entity == REPORT else {"name": "Filter"}
    return {
        "t": entity,
        "h": str(home.pk),
        "id": str(row_id),
        "k": "put",
        "c": fields.pop("c", stamp()),
        "f": {**base, field: file, **fields},
    }


def pushed(phone: Connected, home: Home, *ops: dict[str, Any], number: int = 1) -> Any:
    with TestCase.captureOnCommitCallbacks(execute=True):
        return push(phone.access, home.pk, list(ops), number=number)


# ---------- reading a file made on the web ----------


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_a_file_of_the_web_is_read_by_the_id_the_feed_gives(
    alice: User, phone: Connected, alice_home: Home, entity: str, field: str, folder: str
) -> None:
    row = web_row(entity, alice, alice_home)
    change = Change.objects.get(entity=entity, row_id=row.pk)
    given = reading.operation_of(change, row)["f"][field]
    assert given["file"] == str(row.pk)  # the form the feed has always given
    assert "sha256" not in given  # made on the web: no digest
    answer = get(phone.access, alice_home.pk, given["file"])
    assert answer.status_code == 200
    assert answer["Content-Type"] == "image/jpeg"
    assert answer["Content-Disposition"] == f'inline; filename="{row.pk}.jpg"'
    assert answer["X-Content-Type-Options"] == "nosniff"
    assert answer["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert body_of(answer) == getattr(row, field).open("rb").read()


def test_the_photo_of_a_report_has_its_thumbnail_and_a_label_none(
    alice: User, phone: Connected, alice_home: Home
) -> None:
    report = web_report(alice, alice_home, image_bytes(size=(800, 600)))
    article = web_article(alice, alice_home)
    thumb = get(phone.access, alice_home.pk, report.pk, size="thumbnail")
    assert thumb.status_code == 200
    assert body_of(thumb) == report.thumbnail.open("rb").read()
    # The model of an article keeps no thumbnail of its label.
    assert get(phone.access, alice_home.pk, article.pk, size="thumbnail").status_code == 404


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_the_id_of_a_file_of_the_web_cannot_be_uploaded_to(
    alice: User, phone: Connected, alice_home: Home, entity: str, field: str, folder: str
) -> None:
    """Control: an upload never takes over the id of a file the web keeps."""
    row = web_row(entity, alice, alice_home)
    answer = put(phone.access, alice_home.pk, row.pk, image_bytes(size=(32, 32)))
    assert answer.status_code == 409
    assert answer.json()["code"] == "file_conflict"
    assert not SyncFile.objects.exists()


# ---------- a phone's file in a pushed row ----------


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_a_row_pushed_after_its_file_takes_it_under_the_phones_id(
    phone: Connected, alice_home: Home, media_root: Path, entity: str, field: str, folder: str
) -> None:
    data, file_id, row_id = image_bytes(), uuid.uuid4(), uuid.uuid4()
    assert put(phone.access, alice_home.pk, file_id, data).status_code == 201
    answer = pushed(phone, alice_home, row_op(alice_home, entity, row_id, reference(file_id, data)))
    assert results(answer)[0]["status"] == "applied"
    row = model_of(entity).objects.get(pk=row_id)
    assert getattr(row, field).name == f"{alice_home.pk}/{file_id}.jpg"
    assert getattr(row, f"{field}_content_type") == "image/jpeg"
    assert (media_root / folder / str(alice_home.pk) / f"{file_id}.jpg").exists()
    assert not list((media_root / "sync" / str(alice_home.pk)).iterdir())  # moved, thumb gone
    assert SyncFile.objects.get(pk=file_id).referenced_at is not None
    assert get(phone.access, alice_home.pk, file_id).status_code == 200
    # The feed gives back the reference the phone sent, its digest included.
    change = Change.objects.get(entity=entity, row_id=row_id)
    assert reading.operation_of(change, row)["f"][field] == reference(file_id, data)
    assert field not in RowClocks.objects.get(row_id=row_id).extra


def test_a_report_keeps_the_columns_of_its_photo(
    phone: Connected, alice_home: Home, media_root: Path
) -> None:
    data, file_id, row_id = image_bytes(size=(800, 600)), uuid.uuid4(), uuid.uuid4()
    put(phone.access, alice_home.pk, file_id, data)
    pushed(phone, alice_home, row_op(alice_home, REPORT, row_id, reference(file_id, data)))
    report = Report.objects.get(pk=row_id)
    assert report.thumbnail.name == f"{alice_home.pk}/{file_id}-thumb.jpg"
    assert report.photo_size_bytes == SyncFile.objects.get(pk=file_id).size_bytes
    assert (media_root / "reports" / str(alice_home.pk) / f"{file_id}-thumb.jpg").exists()
    assert get(phone.access, alice_home.pk, file_id, size="thumbnail").status_code == 200


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_a_row_pushed_before_its_file_waits_then_takes_it(
    phone: Connected, alice_home: Home, entity: str, field: str, folder: str
) -> None:
    data, file_id, row_id = image_bytes(), uuid.uuid4(), uuid.uuid4()
    answer = pushed(phone, alice_home, row_op(alice_home, entity, row_id, reference(file_id, data)))
    assert results(answer)[0]["status"] == "applied"
    row = model_of(entity).objects.get(pk=row_id)
    assert getattr(row, field).name == f"{alice_home.pk}/{file_id}"  # on another phone
    assert get(phone.access, alice_home.pk, file_id).status_code == 404
    head = Change.objects.filter(home_id=alice_home.pk).count()
    with TestCase.captureOnCommitCallbacks(execute=True):
        assert put(phone.access, alice_home.pk, file_id, data).status_code == 201
    row.refresh_from_db()
    assert getattr(row, field).name == f"{alice_home.pk}/{file_id}.jpg"
    assert get(phone.access, alice_home.pk, file_id).status_code == 200
    assert Change.objects.filter(home_id=alice_home.pk).count() == head  # no change written


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_the_file_is_written_again_without_its_metadata(
    phone: Connected, alice_home: Home, media_root: Path, entity: str, field: str, folder: str
) -> None:
    """The rule of the photo of a room: the server keeps its own re-encoded copy."""
    data, file_id, row_id = image_bytes(gps=True, comment=True), uuid.uuid4(), uuid.uuid4()
    put(phone.access, alice_home.pk, file_id, data)
    pushed(phone, alice_home, row_op(alice_home, entity, row_id, reference(file_id, data)))
    kept = (media_root / folder / str(alice_home.pk) / f"{file_id}.jpg").read_bytes()
    assert b"secret comment" not in kept
    assert not Image.open(io.BytesIO(kept)).getexif()
    assert body_of(get(phone.access, alice_home.pk, file_id)) == kept
    # The reference keeps the digest of what the phone sent, not of the copy.
    given = reading.operation_of(Change.objects.get(entity=entity, row_id=row_id))["f"][field]
    assert given["sha256"] == sha(data) != sha(kept)


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_a_row_does_not_take_a_document(
    phone: Connected, alice_home: Home, entity: str, field: str, folder: str
) -> None:
    """Images only, as for the photo of a room: the row waits for an image under that id."""
    file_id, row_id = uuid.uuid4(), uuid.uuid4()
    put(phone.access, alice_home.pk, file_id, PDF)
    ref = reference(file_id, PDF, type="application/pdf", width=None, height=None)
    assert results(pushed(phone, alice_home, row_op(alice_home, entity, row_id, ref)))[0][
        "status"
    ] == ("applied")
    row = model_of(entity).objects.get(pk=row_id)
    assert getattr(row, field).name == f"{alice_home.pk}/{file_id}"
    assert SyncFile.objects.get(pk=file_id).referenced_at is None


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_a_reference_with_another_digest_than_the_file_is_malformed(
    phone: Connected, alice_home: Home, entity: str, field: str, folder: str
) -> None:
    data, other, file_id = image_bytes(), image_bytes(size=(40, 40)), uuid.uuid4()
    put(phone.access, alice_home.pk, file_id, data)
    bad = row_op(alice_home, entity, uuid.uuid4(), reference(file_id, data, sha256=sha(other)))
    answer = push(phone.access, alice_home.pk, [bad])
    assert answer.status_code == 400
    assert (answer.json()["code"], answer.json()["index"]) == ("malformed", 0)
    assert not model_of(entity).objects.exists()


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_a_value_that_is_not_a_reference_is_malformed(
    phone: Connected, alice_home: Home, entity: str, field: str, folder: str
) -> None:
    """Before, it was kept in `extra` whatever it was; a JSON text of the first builds too."""
    for bad in ("photo.jpg", '{"size": 1, "type": "image/jpeg"}', {"file": str(uuid.uuid4())}):
        answer = push(phone.access, alice_home.pk, [row_op(alice_home, entity, uuid.uuid4(), bad)])
        assert answer.status_code == 400, bad
        assert answer.json()["code"] == "malformed"
    assert not model_of(entity).objects.exists()


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_a_row_without_a_file_is_made_without_one(
    phone: Connected, alice_home: Home, entity: str, field: str, folder: str
) -> None:
    """Unlike a photo of a room, a report or an article may have no photo: `null` or absent."""
    with_null, absent = uuid.uuid4(), uuid.uuid4()
    operation = row_op(alice_home, entity, absent, None)
    del operation["f"][field]
    answer = pushed(phone, alice_home, row_op(alice_home, entity, with_null, None), operation)
    assert [r["status"] for r in results(answer)] == ["applied", "applied"]
    for row_id in (with_null, absent):
        assert not getattr(model_of(entity).objects.get(pk=row_id), field)
    # The null is the value of the column, no longer a field kept in `extra`.
    assert field not in RowClocks.objects.get(row_id=with_null).extra
    assert field in RowClocks.objects.get(row_id=with_null).clocks


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_a_new_file_replaces_the_one_of_the_web(
    alice: User,
    phone: Connected,
    alice_home: Home,
    media_root: Path,
    entity: str,
    field: str,
    folder: str,
) -> None:
    row = web_row(entity, alice, alice_home)
    old = media_root / folder / getattr(row, field).name
    assert old.exists()
    data, file_id = image_bytes(size=(40, 40)), uuid.uuid4()
    put(phone.access, alice_home.pk, file_id, data)
    later = row_op(alice_home, entity, row.pk, reference(file_id, data), c=stamp(60_000))
    assert results(pushed(phone, alice_home, later))[0]["status"] == "applied"
    row.refresh_from_db()
    assert getattr(row, field).name == f"{alice_home.pk}/{file_id}.jpg"
    assert not old.exists()
    assert (media_root / folder / str(alice_home.pk) / f"{file_id}.jpg").exists()
    assert get(phone.access, alice_home.pk, row.pk).status_code == 404  # the old id is gone
    assert get(phone.access, alice_home.pk, file_id).status_code == 200


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_null_removes_the_file(
    alice: User,
    phone: Connected,
    alice_home: Home,
    media_root: Path,
    entity: str,
    field: str,
    folder: str,
) -> None:
    row = web_row(entity, alice, alice_home)
    old = media_root / folder / getattr(row, field).name
    later = row_op(alice_home, entity, row.pk, None, c=stamp(60_000))
    assert results(pushed(phone, alice_home, later))[0]["status"] == "applied"
    row.refresh_from_db()
    assert not getattr(row, field)
    assert getattr(row, f"{field}_content_type") == ""
    assert not old.exists()
    assert get(phone.access, alice_home.pk, row.pk).status_code == 404
    if entity == REPORT:
        assert not row.thumbnail
        assert row.photo_size_bytes == 0
        assert not row.has_photo


def test_a_value_kept_in_extra_before_gives_way_to_the_column(
    phone: Connected, alice_home: Home
) -> None:
    """A reference a phone pushed before this change was kept in `extra`; a later write wins."""
    row_id = uuid.uuid4()
    assert results(pushed(phone, alice_home, row_op(alice_home, REPORT, row_id, None)))
    clocks = RowClocks.objects.get(row_id=row_id)
    old_clock = stamp(-60_000)
    clocks.extra["photo"] = [
        '{"size": 1, "type": "image/jpeg"}',
        old_clock,
        clocks.clocks["title"][1],
    ]
    clocks.save()
    data, file_id = image_bytes(), uuid.uuid4()
    put(phone.access, alice_home.pk, file_id, data)
    later = row_op(alice_home, REPORT, row_id, reference(file_id, data), c=stamp(1000))
    assert results(pushed(phone, alice_home, later, number=2))[0]["status"] == "applied"
    clocks.refresh_from_db()
    assert "photo" not in clocks.extra
    line = next(
        line for line in reading.snapshot_lines(alice_home) if line.get("id") == str(row_id)
    )
    assert line["f"]["photo"][0]["file"] == str(file_id)
    assert line["f"]["photo"][0]["sha256"] == sha(data)


def test_a_write_older_than_the_value_kept_in_extra_loses(
    phone: Connected, alice_home: Home
) -> None:
    row_id = uuid.uuid4()
    pushed(phone, alice_home, row_op(alice_home, REPORT, row_id, None))
    clocks = RowClocks.objects.get(row_id=row_id)
    clocks.extra["photo"] = ["kept", stamp(120_000), clocks.clocks["title"][1]]
    clocks.save()
    older = row_op(alice_home, REPORT, row_id, None, c=stamp(60_000))
    del older["f"]["title"]
    assert results(pushed(phone, alice_home, older, number=2))[0]["status"] == "ignored"
    clocks.refresh_from_db()
    assert clocks.extra["photo"][0] == "kept"


# ---------- who reads and writes ----------


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_another_home_never_gives_its_file(
    alice: User,
    bob: User,
    alice_home: Home,
    bob_home: Home,
    add_member: Any,
    entity: str,
    field: str,
    folder: str,
) -> None:
    """Control: home scoping, as for the photo of a room; 404, never 403."""
    theirs = web_row(entity, bob, bob_home)
    assert get(connect(bob, [bob_home]).access, bob_home.pk, theirs.pk).status_code == 200
    add_member(bob_home, alice, Role.OWNER)  # a member, but the phone was not given that home
    phone = connect(alice, [alice_home])
    assert get(phone.access, bob_home.pk, theirs.pk).status_code == 404
    assert get(phone.access, alice_home.pk, theirs.pk).status_code == 404  # not by another home
    assert get(phone.access, alice_home.pk, uuid.uuid4()).status_code == 404  # an id guessed


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_a_reference_never_takes_the_file_of_another_home(
    bob: User,
    phone: Connected,
    alice_home: Home,
    bob_home: Home,
    entity: str,
    field: str,
    folder: str,
) -> None:
    data, file_id, row_id = image_bytes(), uuid.uuid4(), uuid.uuid4()
    bobs = connect(bob, [bob_home])
    assert put(bobs.access, bob_home.pk, file_id, data).status_code == 201
    pushed(phone, alice_home, row_op(alice_home, entity, row_id, reference(file_id, data)))
    row = model_of(entity).objects.get(pk=row_id)
    assert getattr(row, field).name == f"{alice_home.pk}/{file_id}"  # waits, takes nothing
    assert SyncFile.objects.get(pk=file_id).referenced_at is None
    assert get(phone.access, alice_home.pk, file_id).status_code == 404
    assert get(bobs.access, bob_home.pk, file_id).status_code == 200


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_a_read_only_member_reads_the_file_and_sends_none(
    alice: User,
    bob: User,
    alice_home: Home,
    add_member: Any,
    entity: str,
    field: str,
    folder: str,
) -> None:
    """As for the photo of a room: reading is a member's right, writing is not."""
    row = web_row(entity, alice, alice_home)
    add_member(alice_home, bob, Role.READ_ONLY)
    reader = connect(bob, [alice_home])
    assert get(reader.access, alice_home.pk, row.pk).status_code == 200
    answer = put(reader.access, alice_home.pk, uuid.uuid4(), image_bytes())
    assert (answer.status_code, answer.json()["code"]) == (403, "forbidden")


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_a_row_deleted_is_read_as_a_photo_of_a_room_is(
    alice: User, phone: Connected, alice_home: Home, entity: str, field: str, folder: str
) -> None:
    """Deleted, the file stays for a restore, as a photo's does; gone for good, it is 404."""
    room = room_services.create_room(alice, alice_home, name="Kitchen", kind="kitchen")
    photo = room_services.add_photo(alice, room=room, upload=upload(image_bytes()))
    row = web_row(entity, alice, alice_home)
    for target in (photo, row):
        type(target).objects.filter(pk=target.pk).update(deleted_at=photo.created_at)
    assert get(phone.access, alice_home.pk, photo.pk).status_code == 200
    assert get(phone.access, alice_home.pk, row.pk).status_code == 200
    for target in (photo, row):
        type(target)._base_manager.filter(pk=target.pk).delete()
    assert get(phone.access, alice_home.pk, photo.pk).status_code == 404
    assert get(phone.access, alice_home.pk, row.pk).status_code == 404


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_the_example_home_gives_no_file(
    alice: User, alice_home: Home, entity: str, field: str, folder: str
) -> None:
    """docs/adr/0084: the example home never leaves the instance, its photos neither."""
    example = make_example(alice)
    row = web_row(entity, alice, example)
    real = web_row(entity, alice, alice_home)
    phone = connect(alice, [alice_home])
    phone.connection.homes.add(example)  # even bound to the connection by hand
    assert get(phone.access, example.pk, row.pk).status_code == 404
    assert get(phone.access, alice_home.pk, row.pk).status_code == 404
    assert get(phone.access, alice_home.pk, real.pk).status_code == 200  # the same, in a real home


# ---------- the web, the export and the erasure keep the file of a phone ----------


@pytest.mark.parametrize(("entity", "field", "folder"), KINDS)
def test_the_web_shows_exports_and_erases_the_file_of_a_phone(
    alice: User,
    phone: Connected,
    alice_home: Home,
    media_root: Path,
    entity: str,
    field: str,
    folder: str,
) -> None:
    data, file_id, row_id = image_bytes(), uuid.uuid4(), uuid.uuid4()
    put(phone.access, alice_home.pk, file_id, data)
    pushed(phone, alice_home, row_op(alice_home, entity, row_id, reference(file_id, data)))
    kept = media_root / folder / str(alice_home.pk) / f"{file_id}.jpg"
    client = Client()
    client.force_login(alice)
    if entity == REPORT:
        page = reverse("reports:photo", kwargs={"home_id": alice_home.pk, "report_id": row_id})
    else:
        page = reverse("stock:label", kwargs={"home_id": alice_home.pk, "article_id": row_id})
    shown = client.get(page)
    assert shown.status_code == 200
    assert body_of(shown) == kept.read_bytes()
    archive = client.post(reverse("homes:export", kwargs={"home_id": alice_home.pk}))
    assert archive.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(body_of(archive))).namelist()
    assert f"files/{folder}/{row_id}.jpg" in names
    with TestCase.captureOnCommitCallbacks(execute=True):
        home_services.erase_home(alice_home)
    assert not kept.exists()
