"""The sync API, files (lot M6 of docs/adr/0079): `PUT` and `GET .../files/{file_id}`.

The API is exposed to the phone: each test of security names the control it proves, and fails
without it (a home check removed, a read-only member let in, bytes taken over under an id, a
file read in full before its size is refused). Every test fails on the code before the lot:
the route did not exist, and a pushed file reference was kept in `extra`.
"""

import datetime
import hashlib
import io
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.core.cache import cache
from django.core.handlers.wsgi import WSGIRequest
from django.test import Client, TestCase
from django.utils import timezone
from PIL import Image

from housecairn.accounts.models import User
from housecairn.homes import quotas
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Role
from housecairn.projects import services as project_services
from housecairn.projects.models import ProjectFile
from housecairn.rooms import conf
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Photo, Room
from housecairn.sync import clock, files, reading
from housecairn.sync.models import Change, SyncFile
from housecairn.sync.storage import sync_storage
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Attachment
from tests.oauth_support import Connected, connect
from tests.rooms_support import image_bytes

pytestmark = pytest.mark.django_db
pytest_plugins = ("tests.rooms_fixtures",)

DEVICE = clock.device_to_b64(bytes(range(16)))
PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _media(media_root: Path) -> Path:
    return media_root


@pytest.fixture
def phone(alice: User, alice_home: Home) -> Connected:
    return connect(alice, [alice_home])


@pytest.fixture
def room(alice: User, alice_home: Home) -> Room:
    return room_services.create_room(alice, alice_home, name="Kitchen", kind="kitchen")


def file_url(home_id: Any, file_id: Any) -> str:
    return f"/api/v1/sync/homes/{home_id}/files/{file_id}"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(
    token: str,
    home_id: Any,
    file_id: Any,
    data: bytes,
    *,
    digest: str | None = None,
    content_type: str = "application/octet-stream",
    **extra: Any,
) -> Any:
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}", "HTTP_HC_SHA256": digest or sha(data)}
    if digest == "":
        del headers["HTTP_HC_SHA256"]
    return Client().put(
        file_url(home_id, file_id),
        data,
        content_type=content_type,
        secure=True,
        **{**headers, **extra},
    )


def get(token: str, home_id: Any, file_id: Any, **query: str) -> Any:
    return Client().get(
        file_url(home_id, file_id), query, secure=True, HTTP_AUTHORIZATION=f"Bearer {token}"
    )


def body_of(answer: Any) -> bytes:
    return b"".join(answer.streaming_content)


def stamp(offset_ms: int = 0) -> str:
    return clock.format_clock(clock.make_clock(clock.wall_ms() + offset_ms, 0))


def push(token: str, home_id: Any, ops: list[dict[str, Any]], number: int = 1) -> Any:
    raw = json.dumps({"push": number, "sv": 1, "device": DEVICE, "ops": ops})
    return Client().post(
        f"/api/v1/sync/homes/{home_id}/push",
        raw,
        content_type="application/json",
        secure=True,
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def reference(file_id: Any, data: bytes, **kw: Any) -> dict[str, Any]:
    found: dict[str, Any] = {
        "file": str(file_id),
        "sha256": sha(data),
        "size": len(data),
        "type": "image/jpeg",
        "width": 64,
        "height": 48,
    }
    found.update(kw)
    return found


def photo_op(home: Home, room: Room, row_id: Any, image: Any, **fields: Any) -> dict[str, Any]:
    return {
        "t": "rooms.photo",
        "h": str(home.pk),
        "id": str(row_id),
        "k": "put",
        "c": fields.pop("c", stamp()),
        "f": {"room_id": str(room.pk), "caption": "Window", "image": image, **fields},
    }


def results(answer: Any) -> list[dict[str, Any]]:
    assert answer.status_code == 200, answer.content
    found: list[dict[str, Any]] = answer.json()["results"]
    return found


# ---------- uploading ----------


class TestUpload:
    def test_an_image_is_kept_under_the_phones_id_without_its_metadata(
        self, phone: Connected, alice_home: Home, media_root: Path
    ) -> None:
        data = image_bytes(gps=True, comment=True)
        file_id = uuid.uuid4()
        answer = put(phone.access, alice_home.pk, file_id, data)
        assert answer.status_code == 201, answer.content
        assert answer["HC-Sync"] == "1"
        assert answer.json() == {"file": str(file_id), "sha256": sha(data)}
        kept = SyncFile.objects.get(pk=file_id)
        assert (kept.home_id, kept.sha256, kept.is_image) == (alice_home.pk, sha(data), True)
        assert kept.stored == f"{alice_home.pk}/{file_id}.jpg"
        on_disk = media_root / "sync" / str(alice_home.pk) / f"{file_id}.jpg"
        stored = on_disk.read_bytes()
        assert b"secret comment" not in stored
        assert not Image.open(io.BytesIO(stored)).getexif()  # written again, EXIF dropped
        assert (media_root / "sync" / str(alice_home.pk) / f"{file_id}-thumb.jpg").exists()

    def test_the_same_bytes_again_answer_200(self, phone: Connected, alice_home: Home) -> None:
        data, file_id = image_bytes(), uuid.uuid4()
        assert put(phone.access, alice_home.pk, file_id, data).status_code == 201
        again = put(phone.access, alice_home.pk, file_id, data)
        assert again.status_code == 200
        assert SyncFile.objects.count() == 1

    def test_other_bytes_under_the_same_id_are_a_conflict(
        self, phone: Connected, alice_home: Home
    ) -> None:
        """Control: an id names one file; a second upload cannot replace the first."""
        file_id = uuid.uuid4()
        first = image_bytes()
        assert put(phone.access, alice_home.pk, file_id, first).status_code == 201
        answer = put(phone.access, alice_home.pk, file_id, image_bytes(size=(32, 32)))
        assert answer.status_code == 409
        assert answer.json()["code"] == "file_conflict"
        assert SyncFile.objects.get(pk=file_id).sha256 == sha(first)

    def test_an_id_held_in_another_home_is_a_conflict_that_says_not_where(
        self, alice: User, bob: User, alice_home: Home, bob_home: Home
    ) -> None:
        """Control: a file of another home is neither replaced nor revealed."""
        data, file_id = image_bytes(), uuid.uuid4()
        bobs = connect(bob, [bob_home])
        assert put(bobs.access, bob_home.pk, file_id, data).status_code == 201
        answer = put(connect(alice, [alice_home]).access, alice_home.pk, file_id, data)
        assert answer.status_code == 409
        assert answer.json()["code"] == "file_conflict"
        assert SyncFile.objects.get(pk=file_id).home_id == bob_home.pk

    def test_the_id_of_a_photo_of_the_web_is_a_conflict(
        self, alice: User, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        from tests.rooms_support import upload

        photo = room_services.add_photo(alice, room=room, upload=upload(image_bytes()))
        answer = put(phone.access, alice_home.pk, photo.pk, image_bytes(size=(20, 20)))
        assert answer.status_code == 409
        assert not SyncFile.objects.exists()

    @pytest.mark.parametrize("digest", ["", "ab" * 32, "AB" * 32, "xyz"])
    def test_a_digest_missing_or_wrong_is_malformed(
        self, phone: Connected, alice_home: Home, digest: str
    ) -> None:
        """Control: the bytes kept are those the phone named."""
        answer = put(phone.access, alice_home.pk, uuid.uuid4(), image_bytes(), digest=digest)
        assert answer.status_code == 400
        assert answer.json()["code"] == "malformed"
        assert not SyncFile.objects.exists()

    def test_a_document_of_the_web_is_kept_as_it_is(
        self, phone: Connected, alice_home: Home
    ) -> None:
        file_id = uuid.uuid4()
        assert put(phone.access, alice_home.pk, file_id, PDF).status_code == 201
        kept = SyncFile.objects.get(pk=file_id)
        assert (kept.content_type, kept.is_image, kept.thumbnail) == ("application/pdf", False, "")
        assert sync_storage.open(kept.stored).read() == PDF

    @pytest.mark.parametrize(
        "data",
        [
            b"MZ\x90\x00\x03\x00\x00\x00" + bytes(range(256)),
            b"\x00\x00\x00\x18ftypheic" + b"\0" * 32,
        ],
    )
    def test_a_file_the_web_refuses_is_refused(
        self, phone: Connected, alice_home: Home, data: bytes
    ) -> None:
        """Control: the types are those of the web, decided by the content."""
        answer = put(phone.access, alice_home.pk, uuid.uuid4(), data, content_type="image/jpeg")
        assert answer.status_code == 415
        assert answer.json()["code"] == "unsupported_type"
        assert not SyncFile.objects.exists()

    def test_a_form_body_is_refused(self, phone: Connected, alice_home: Home) -> None:
        answer = put(
            phone.access,
            alice_home.pk,
            uuid.uuid4(),
            image_bytes(),
            content_type="multipart/form-data; boundary=x",
        )
        assert answer.status_code == 415

    def test_an_empty_file_is_malformed(self, phone: Connected, alice_home: Home) -> None:
        answer = put(phone.access, alice_home.pk, uuid.uuid4(), b"")
        assert answer.status_code == 400
        assert answer.json()["code"] == "malformed"


class TestSize:
    def test_a_file_announced_above_the_limit_is_refused_before_it_is_read(
        self, phone: Connected, alice_home: Home, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control: the length is checked first; the body is never read."""
        monkeypatch.setattr(conf, "photo_max_bytes", lambda: 20 * 1024)

        def read(self: WSGIRequest, *args: Any) -> bytes:
            raise AssertionError("the body of a file too large is not read")

        monkeypatch.setattr(WSGIRequest, "read", read)
        answer = put(phone.access, alice_home.pk, uuid.uuid4(), b"x" * (20 * 1024 + 1))
        assert answer.status_code == 413
        assert answer.json()["code"] == "too_large"

    def test_a_file_at_the_limit_is_kept_and_one_byte_more_is_not(
        self, phone: Connected, alice_home: Home, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        limit = 20 * 1024
        monkeypatch.setattr(conf, "photo_max_bytes", lambda: limit)
        assert put(phone.access, alice_home.pk, uuid.uuid4(), b"x" * limit).status_code == 201
        answer = put(phone.access, alice_home.pk, uuid.uuid4(), b"x" * (limit + 1))
        assert answer.status_code == 413
        assert answer.json()["code"] == "too_large"

    def test_info_announces_the_limit(
        self, phone: Connected, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(conf, "photo_max_bytes", lambda: 123456)
        answer = Client().get(
            "/api/v1/sync/info", secure=True, HTTP_AUTHORIZATION=f"Bearer {phone.access}"
        )
        assert answer.json()["limits"]["file_max_bytes"] == 123456

    def test_the_quota_of_the_home_is_kept(
        self, phone: Connected, alice_home: Home, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control: an upload counts in the quota of the home, as on the web."""
        monkeypatch.setattr(quotas, "limits", lambda: quotas.Limits(bytes=10**9, files=1))
        assert put(phone.access, alice_home.pk, uuid.uuid4(), image_bytes()).status_code == 201
        answer = put(phone.access, alice_home.pk, uuid.uuid4(), PDF)
        assert answer.status_code == 413
        assert answer.json()["code"] == "quota_exceeded"
        assert SyncFile.objects.count() == 1
        assert quotas.usage(alice_home.pk).files == 1  # the sync folder is one of the home's


# ---------- who may ----------


class TestWho:
    def test_a_read_only_member_sends_no_file(
        self, alice: User, bob: User, alice_home: Home, add_member: Any
    ) -> None:
        """Control: the rights of the account in the home."""
        add_member(alice_home, bob, Role.READ_ONLY)
        reader = connect(bob, [alice_home])
        answer = put(reader.access, alice_home.pk, uuid.uuid4(), image_bytes())
        assert answer.status_code == 403
        assert answer.json()["code"] == "forbidden"
        assert not SyncFile.objects.exists()

    def test_a_read_only_member_reads_the_files(
        self, alice: User, bob: User, alice_home: Home, add_member: Any, phone: Connected
    ) -> None:
        file_id = uuid.uuid4()
        assert put(phone.access, alice_home.pk, file_id, image_bytes()).status_code == 201
        add_member(alice_home, bob, Role.READ_ONLY)
        assert get(connect(bob, [alice_home]).access, alice_home.pk, file_id).status_code == 200

    def test_a_home_not_bound_to_the_connection_is_404_both_ways(
        self, alice: User, bob: User, alice_home: Home, bob_home: Home, add_member: Any
    ) -> None:
        """Control: home scoping by the homes of the connection, never 403."""
        file_id = uuid.uuid4()
        bobs = connect(bob, [bob_home])
        assert put(bobs.access, bob_home.pk, file_id, image_bytes()).status_code == 201
        add_member(bob_home, alice, Role.OWNER)  # a member, but the phone was not given it
        phone = connect(alice, [alice_home])
        assert get(phone.access, bob_home.pk, file_id).status_code == 404
        assert put(phone.access, bob_home.pk, uuid.uuid4(), image_bytes()).status_code == 404
        assert get(phone.access, alice_home.pk, file_id).status_code == 404  # not by another home

    def test_an_erased_home_is_410(self, alice: User, phone: Connected, alice_home: Home) -> None:
        home_services.erase_home(alice_home)
        for answer in (
            put(phone.access, alice_home.pk, uuid.uuid4(), image_bytes()),
            get(phone.access, alice_home.pk, uuid.uuid4()),
        ):
            assert answer.status_code == 410
            assert answer.json()["code"] == "home_gone"

    def test_plain_http_from_a_public_peer_is_refused(
        self, phone: Connected, alice_home: Home
    ) -> None:
        """Control: the transport rule of ADR 0078."""
        data = image_bytes()
        answer = Client().put(
            file_url(alice_home.pk, uuid.uuid4()),
            data,
            content_type="application/octet-stream",
            HTTP_AUTHORIZATION=f"Bearer {phone.access}",
            HTTP_HC_SHA256=sha(data),
            REMOTE_ADDR="203.0.113.9",
        )
        assert answer.status_code == 403
        assert answer.json()["code"] == "insecure_transport"
        assert not SyncFile.objects.exists()

    def test_an_api_token_is_refused(self, alice: User, alice_home: Home) -> None:
        from housecairn.apitokens import services as token_services
        from housecairn.apitokens.models import Scope

        _token, raw = token_services.create_token(
            alice, name="t", scopes=[Scope.WRITE], homes=[alice_home], lifetime_days=30
        )
        answer = put(raw, alice_home.pk, uuid.uuid4(), image_bytes())
        assert answer.status_code == 403
        assert answer.json()["code"] == "sync_needs_connection"


# ---------- reading ----------


class TestDownload:
    def test_an_image_is_sent_inline_with_the_headers_of_the_web(
        self, phone: Connected, alice_home: Home
    ) -> None:
        file_id = uuid.uuid4()
        put(phone.access, alice_home.pk, file_id, image_bytes())
        answer = get(phone.access, alice_home.pk, file_id)
        assert answer.status_code == 200
        assert answer["Content-Type"] == "image/jpeg"
        assert answer["Content-Disposition"] == f'inline; filename="{file_id}.jpg"'
        assert answer["X-Content-Type-Options"] == "nosniff"
        assert answer["Content-Security-Policy"] == "default-src 'none'; sandbox"
        assert answer["HC-Sync"] == "1"
        assert Image.open(io.BytesIO(body_of(answer))).format == "JPEG"

    def test_the_thumbnail_of_an_image(self, phone: Connected, alice_home: Home) -> None:
        file_id = uuid.uuid4()
        put(phone.access, alice_home.pk, file_id, image_bytes(size=(800, 600)))
        answer = get(phone.access, alice_home.pk, file_id, size="thumbnail")
        assert answer.status_code == 200
        assert answer["Content-Disposition"] == f'inline; filename="{file_id}.jpg"'
        assert max(Image.open(io.BytesIO(body_of(answer))).size) <= conf.THUMBNAIL_SIDE

    def test_a_document_is_a_download_and_has_no_thumbnail(
        self, phone: Connected, alice_home: Home
    ) -> None:
        file_id = uuid.uuid4()
        put(phone.access, alice_home.pk, file_id, PDF)
        answer = get(phone.access, alice_home.pk, file_id)
        assert answer["Content-Type"] == "application/pdf"
        assert answer["Content-Disposition"] == f'attachment; filename="{file_id}.pdf"'
        assert body_of(answer) == PDF
        assert get(phone.access, alice_home.pk, file_id, size="thumbnail").status_code == 404

    def test_another_size_is_refused(self, phone: Connected, alice_home: Home) -> None:
        assert get(phone.access, alice_home.pk, uuid.uuid4(), size="large").status_code == 400

    def test_a_file_never_uploaded_is_404(self, phone: Connected, alice_home: Home) -> None:
        assert get(phone.access, alice_home.pk, uuid.uuid4()).status_code == 404

    def test_a_photo_of_the_web_is_read_by_the_id_the_feed_gives(
        self, alice: User, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        from tests.rooms_support import upload

        photo = room_services.add_photo(alice, room=room, upload=upload(image_bytes()))
        change = Change.objects.filter(entity="rooms.photo", row_id=photo.pk).get()
        file_id = change.fields["image"]["file"]
        assert file_id == str(photo.pk)
        answer = get(phone.access, alice_home.pk, file_id)
        assert answer.status_code == 200
        assert body_of(answer) == photo.image.open("rb").read()

    def test_an_attachment_of_the_web_too(
        self, alice: User, phone: Connected, alice_home: Home
    ) -> None:
        from tests.rooms_support import upload

        task = task_services.create_task(alice, alice_home, title="Boiler")
        made = task_services.add_attachment(
            alice, task, upload=upload(PDF, "manual.pdf", "application/pdf")
        )
        answer = get(phone.access, alice_home.pk, made.pk)
        assert answer["Content-Type"] == "application/pdf"
        assert body_of(answer) == PDF


# ---------- references in the operations ----------


class TestReferences:
    def test_a_photo_pushed_after_its_file_takes_it_under_the_phones_id(
        self, phone: Connected, alice_home: Home, room: Room, media_root: Path
    ) -> None:
        data, file_id, row_id = image_bytes(), uuid.uuid4(), uuid.uuid4()
        put(phone.access, alice_home.pk, file_id, data)
        with TestCase.captureOnCommitCallbacks(execute=True):
            answer = push(
                phone.access,
                alice_home.pk,
                [photo_op(alice_home, room, row_id, reference(file_id, data))],
            )
        assert results(answer)[0]["status"] == "applied"
        photo = Photo.objects.get(pk=row_id)
        assert photo.image.name == f"{alice_home.pk}/{file_id}.jpg"
        assert photo.thumbnail.name == f"{alice_home.pk}/{file_id}-thumb.jpg"
        assert (photo.content_type, photo.room_id) == ("image/jpeg", room.pk)
        assert (media_root / "photos" / str(alice_home.pk) / f"{file_id}.jpg").exists()
        assert not (media_root / "sync" / str(alice_home.pk) / f"{file_id}.jpg").exists()
        assert SyncFile.objects.get(pk=file_id).referenced_at is not None
        # The web serves it, and so does the sync API, by the phone's id.
        assert get(phone.access, alice_home.pk, file_id).status_code == 200
        # The feed gives back the reference the phone sent.
        change = Change.objects.get(entity="rooms.photo", row_id=row_id)
        assert reading.operation_of(change)["f"]["image"] == reference(file_id, data)

    def test_a_photo_pushed_before_its_file_waits_then_takes_it(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        data, file_id, row_id = image_bytes(), uuid.uuid4(), uuid.uuid4()
        answer = push(
            phone.access,
            alice_home.pk,
            [photo_op(alice_home, room, row_id, reference(file_id, data))],
        )
        assert results(answer)[0]["status"] == "applied"
        photo = Photo.objects.get(pk=row_id)
        assert photo.image.name == f"{alice_home.pk}/{file_id}"  # the file on another phone
        assert get(phone.access, alice_home.pk, file_id).status_code == 404
        head = Change.objects.filter(home_id=alice_home.pk).count()
        with TestCase.captureOnCommitCallbacks(execute=True):
            assert put(phone.access, alice_home.pk, file_id, data).status_code == 201
        photo.refresh_from_db()
        assert photo.image.name == f"{alice_home.pk}/{file_id}.jpg"
        assert photo.thumbnail.name == f"{alice_home.pk}/{file_id}-thumb.jpg"
        assert get(phone.access, alice_home.pk, file_id).status_code == 200
        # Taking the file writes no change: the reference of the row is the same.
        assert Change.objects.filter(home_id=alice_home.pk).count() == head

    def test_a_photo_does_not_take_a_document(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        file_id, row_id = uuid.uuid4(), uuid.uuid4()
        put(phone.access, alice_home.pk, file_id, PDF)
        answer = push(
            phone.access,
            alice_home.pk,
            [photo_op(alice_home, room, row_id, reference(file_id, PDF, type="application/pdf"))],
        )
        assert results(answer)[0]["status"] == "applied"
        assert Photo.objects.get(pk=row_id).image.name == f"{alice_home.pk}/{file_id}"

    def test_an_attachment_of_a_task_takes_a_document(
        self, alice: User, phone: Connected, alice_home: Home
    ) -> None:
        task = task_services.create_task(alice, alice_home, title="Boiler")
        file_id, row_id = uuid.uuid4(), uuid.uuid4()
        put(phone.access, alice_home.pk, file_id, PDF)
        operation = {
            "t": "tasks.attachment",
            "h": str(alice_home.pk),
            "id": str(row_id),
            "k": "put",
            "c": stamp(),
            "f": {
                "task_id": str(task.pk),
                "name": "manual.pdf",
                "file": reference(file_id, PDF, type="application/pdf", width=None, height=None),
            },
        }
        with TestCase.captureOnCommitCallbacks(execute=True):
            assert results(push(phone.access, alice_home.pk, [operation]))[0]["status"] == "applied"
        made = Attachment.objects.get(pk=row_id)
        assert made.file.name == f"{alice_home.pk}/{file_id}.pdf"
        assert (made.content_type, made.is_image, made.size_bytes) == (
            "application/pdf",
            False,
            len(PDF),
        )
        assert made.file.open("rb").read() == PDF

    def test_a_file_of_a_project_takes_an_image(
        self, alice: User, phone: Connected, alice_home: Home
    ) -> None:
        project = project_services.create_project(alice, alice_home, title="Kitchen")
        data, file_id, row_id = image_bytes(), uuid.uuid4(), uuid.uuid4()
        put(phone.access, alice_home.pk, file_id, data)
        operation = {
            "t": "projects.projectfile",
            "h": str(alice_home.pk),
            "id": str(row_id),
            "k": "put",
            "c": stamp(),
            "f": {
                "project_id": str(project.pk),
                "name": "idea.jpg",
                "file": reference(file_id, data),
            },
        }
        with TestCase.captureOnCommitCallbacks(execute=True):
            assert results(push(phone.access, alice_home.pk, [operation]))[0]["status"] == "applied"
        made = ProjectFile.objects.get(pk=row_id)
        assert made.file.name == f"{alice_home.pk}/{file_id}.jpg"
        assert made.is_image

    def test_a_reference_that_is_not_one_is_malformed(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        for bad in ("photo.jpg", {"file": "nope"}, {"file": str(uuid.uuid4())}, None):
            answer = push(
                phone.access, alice_home.pk, [photo_op(alice_home, room, uuid.uuid4(), bad)]
            )
            assert answer.status_code == 400, bad
            assert answer.json()["code"] == "malformed"
            assert answer.json()["index"] == 0
        assert not Photo.objects.exists()

    def test_a_new_photo_needs_its_file(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        operation = photo_op(alice_home, room, uuid.uuid4(), None)
        del operation["f"]["image"]
        answer = push(phone.access, alice_home.pk, [operation])
        assert answer.status_code == 400
        assert answer.json()["code"] == "malformed"

    def test_a_new_file_of_a_photo_replaces_the_old_one(
        self, phone: Connected, alice_home: Home, room: Room, media_root: Path
    ) -> None:
        first, second = image_bytes(), image_bytes(size=(40, 40))
        first_id, second_id, row_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        put(phone.access, alice_home.pk, first_id, first)
        put(phone.access, alice_home.pk, second_id, second)
        with TestCase.captureOnCommitCallbacks(execute=True):
            push(
                phone.access,
                alice_home.pk,
                [photo_op(alice_home, room, row_id, reference(first_id, first))],
            )
        later = photo_op(alice_home, room, row_id, reference(second_id, second), c=stamp(1000))
        with TestCase.captureOnCommitCallbacks(execute=True):
            assert (
                results(push(phone.access, alice_home.pk, [later], number=2))[0]["status"]
                == "applied"
            )
        photo = Photo.objects.get(pk=row_id)
        assert photo.image.name == f"{alice_home.pk}/{second_id}.jpg"
        photos = media_root / "photos" / str(alice_home.pk)
        assert not (photos / f"{first_id}.jpg").exists()
        assert (photos / f"{second_id}.jpg").exists()

    def test_a_reference_never_takes_the_file_of_another_home(
        self,
        alice: User,
        bob: User,
        phone: Connected,
        alice_home: Home,
        bob_home: Home,
        room: Room,
        media_root: Path,
    ) -> None:
        """Control: `columns_for` looks for the file in the home of the row (`home_id`)."""
        data, file_id, row_id = image_bytes(), uuid.uuid4(), uuid.uuid4()
        bobs = connect(bob, [bob_home])
        assert put(bobs.access, bob_home.pk, file_id, data).status_code == 201
        kept = SyncFile.objects.get(pk=file_id)
        with TestCase.captureOnCommitCallbacks(execute=True):
            answer = push(
                phone.access,
                alice_home.pk,
                [photo_op(alice_home, room, row_id, reference(file_id, data))],
            )
        assert results(answer)[0]["status"] == "applied"
        # The file of bob stays his: same home, still in the sync folder, never referred to.
        after = SyncFile.objects.get(pk=file_id)
        assert after.home_id == bob_home.pk
        assert (after.stored, after.thumbnail) == (kept.stored, kept.thumbnail)
        assert after.referenced_at is None
        assert get(phone.access, alice_home.pk, file_id).status_code == 404
        assert get(bobs.access, bob_home.pk, file_id).status_code == 200
        # The photo of alice waits for a file of her own home: nothing taken, nothing on disk.
        assert Photo.objects.get(pk=row_id).image.name == f"{alice_home.pk}/{file_id}"
        assert not (media_root / "photos" / str(alice_home.pk)).exists()
        assert not (media_root / "sync" / str(alice_home.pk)).exists()
        assert (media_root / "sync" / str(bob_home.pk) / Path(kept.stored).name).exists()

    def test_a_reference_with_another_digest_than_the_file_is_malformed(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        """No silent difference between the phone and the server about the bytes of a file."""
        data, other, file_id = image_bytes(), image_bytes(size=(40, 40)), uuid.uuid4()
        put(phone.access, alice_home.pk, file_id, data)
        good = photo_op(alice_home, room, uuid.uuid4(), reference(file_id, data))
        bad = photo_op(alice_home, room, uuid.uuid4(), reference(file_id, data, sha256=sha(other)))
        answer = push(phone.access, alice_home.pk, [good, bad])
        assert answer.status_code == 400
        assert answer.json()["code"] == "malformed"
        assert answer.json()["index"] == 1
        assert not Photo.objects.exists()
        assert SyncFile.objects.get(pk=file_id).referenced_at is None

    def test_a_digest_is_not_compared_with_the_file_of_another_home(
        self, bob: User, phone: Connected, alice_home: Home, bob_home: Home, room: Room
    ) -> None:
        """The file under that id is not this home's: the reference waits, as for any unknown."""
        data, other, file_id = image_bytes(), image_bytes(size=(40, 40)), uuid.uuid4()
        put(connect(bob, [bob_home]).access, bob_home.pk, file_id, data)
        operation = photo_op(alice_home, room, uuid.uuid4(), reference(file_id, other))
        answer = push(phone.access, alice_home.pk, [operation])
        assert results(answer)[0]["status"] == "applied"

    def test_the_feed_gives_the_digest_of_a_file_of_the_phone(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        from housecairn.rooms.syncing import photo_file

        data, file_id, row_id = image_bytes(), uuid.uuid4(), uuid.uuid4()
        put(phone.access, alice_home.pk, file_id, data)
        push(
            phone.access,
            alice_home.pk,
            [photo_op(alice_home, room, row_id, reference(file_id, data))],
        )
        given = photo_file(Photo.objects.get(pk=row_id))["image"]
        assert (given["file"], given["sha256"]) == (str(file_id), sha(data))


# ---------- orphans ----------


class TestOrphans:
    def test_a_file_never_referred_to_is_removed_after_7_days(
        self, alice: User, phone: Connected, alice_home: Home, media_root: Path
    ) -> None:
        old, young, used = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        data = image_bytes()
        for file_id in (old, young, used):
            put(phone.access, alice_home.pk, file_id, data if file_id != used else PDF)
        push(
            phone.access,
            alice_home.pk,
            [
                {
                    "t": "tasks.attachment",
                    "h": str(alice_home.pk),
                    "id": str(uuid.uuid4()),
                    "k": "put",
                    "c": stamp(),
                    "f": {
                        "task_id": str(task_services.create_task(alice, alice_home, title="T").pk),
                        "name": "a.pdf",
                        "file": reference(used, PDF, type="application/pdf"),
                    },
                }
            ],
        )
        eight_days_ago = timezone.now() - datetime.timedelta(days=8)
        SyncFile.objects.filter(pk__in=[old, used]).update(created_at=eight_days_ago)
        with TestCase.captureOnCommitCallbacks(execute=True):
            assert files.purge() == 1
        assert set(SyncFile.objects.values_list("pk", flat=True)) == {young, used}
        assert not (media_root / "sync" / str(alice_home.pk) / f"{old}.jpg").exists()
        assert (media_root / "sync" / str(alice_home.pk) / f"{young}.jpg").exists()

    def test_a_file_of_6_days_is_kept(self, phone: Connected, alice_home: Home) -> None:
        """The delay is 7 days: one day short of it, the file stays."""
        file_id = uuid.uuid4()
        put(phone.access, alice_home.pk, file_id, image_bytes())
        SyncFile.objects.update(created_at=timezone.now() - datetime.timedelta(days=6))
        assert files.purge() == 0
        assert SyncFile.objects.filter(pk=file_id).exists()
        SyncFile.objects.update(created_at=timezone.now() - datetime.timedelta(days=8))
        assert files.purge() == 1

    def test_the_nightly_task_runs_it(self, phone: Connected, alice_home: Home) -> None:
        from housecairn.sync import tasks

        file_id = uuid.uuid4()
        put(phone.access, alice_home.pk, file_id, image_bytes())
        SyncFile.objects.update(created_at=timezone.now() - datetime.timedelta(days=8))
        assert tasks.purge()["files"] == 1
        assert not SyncFile.objects.exists()

    def test_a_row_waiting_for_its_file_keeps_it_when_it_comes_late(
        self, phone: Connected, alice_home: Home, room: Room
    ) -> None:
        """A file referred to before its upload is not an orphan, whatever its age."""
        data, file_id = image_bytes(), uuid.uuid4()
        push(
            phone.access,
            alice_home.pk,
            [photo_op(alice_home, room, uuid.uuid4(), reference(file_id, data))],
        )
        put(phone.access, alice_home.pk, file_id, data)
        SyncFile.objects.update(created_at=timezone.now() - datetime.timedelta(days=8))
        assert files.purge() == 0


class TestTwoRowsOneFile:
    def test_a_second_row_gets_its_own_copy_and_keeps_it(
        self, phone: Connected, alice_home: Home, room: Room, media_root: Path
    ) -> None:
        """Control: replacing the file of one row never removes the file of another."""
        data, other = image_bytes(), image_bytes(size=(30, 30))
        file_id, other_id = uuid.uuid4(), uuid.uuid4()
        first, second = uuid.uuid4(), uuid.uuid4()
        put(phone.access, alice_home.pk, file_id, data)
        put(phone.access, alice_home.pk, other_id, other)
        both = [
            photo_op(alice_home, room, first, reference(file_id, data)),
            photo_op(alice_home, room, second, reference(file_id, data)),
        ]
        with TestCase.captureOnCommitCallbacks(execute=True):
            assert [r["status"] for r in results(push(phone.access, alice_home.pk, both))] == [
                "applied",
                "applied",
            ]
        one, two = Photo.objects.get(pk=first), Photo.objects.get(pk=second)
        assert one.image.name != two.image.name
        assert files.file_id_of(two.image.name, None) == str(file_id)
        later = photo_op(alice_home, room, first, reference(other_id, other), c=stamp(1000))
        with TestCase.captureOnCommitCallbacks(execute=True):
            results(push(phone.access, alice_home.pk, [later], number=2))
        two.refresh_from_db()
        assert two.image.open("rb").read()
        assert (media_root / "photos" / two.thumbnail.name).exists()
        assert get(phone.access, alice_home.pk, file_id).status_code == 200
