"""A hostile archive restores nothing, and says why (docs/adr/0048).

Each case starts from a real archive and changes one thing: a name that leaves the
archive, a symbolic link, a zip bomb, a byte changed after export, an unknown version, a
reference to an object of another home, an object already on the instance, a file whose
type is not the one its row says. Each must be refused before anything is written.
"""

import hashlib
import io
import json
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.apitokens import services as token_services
from housecairn.events.models import Event
from housecairn.homes import restore, services
from housecairn.homes.models import Home, Role
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Room
from tests.data_support import archive_bytes, fill_home
from tests.rooms_support import image_bytes, upload

pytest_plugins = ("tests.rooms_fixtures",)
pytestmark = pytest.mark.django_db


def entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def rebuild(
    files: dict[str, bytes],
    *,
    rehash: bool = True,
    extra: Callable[[zipfile.ZipFile], None] | None = None,
) -> bytes:
    """A zip of `files`; with `rehash`, a manifest that agrees with them (a careful forger)."""
    files = dict(files)
    if rehash:
        manifest = json.loads(files["manifest.json"])
        manifest["entries"] = {
            name: {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
            for name, body in files.items()
            if name != "manifest.json"
        }
        files["manifest.json"] = json.dumps(manifest).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
        if extra:
            extra(archive)
    return out.getvalue()


def edit(
    files: dict[str, bytes], app: str, change: Callable[[dict[str, Any]], None]
) -> dict[str, bytes]:
    files = dict(files)
    document = json.loads(files[f"data/{app}.json"])
    change(document)
    files[f"data/{app}.json"] = json.dumps(document).encode()
    return files


@pytest.fixture
def exported(
    media_root: Path,
    alice: User,
    alice_home: Home,
    make_user: Callable[..., User],
    add_member: Callable[..., Any],
) -> bytes:
    member = make_user("member@example.com")
    add_member(alice_home, member, Role.MEMBER)
    fill_home(alice, member, alice_home)
    data = archive_bytes(alice_home)
    services.erase_home(alice_home)
    return data


def refused(actor: User, data: bytes, match: str) -> None:
    homes = Home.all_objects.count()
    rooms = Room.all_objects.count()
    with pytest.raises(restore.ArchiveRefusedError, match=match):
        restore.restore_bytes(actor, data)
    assert Home.all_objects.count() == homes
    assert Room.all_objects.count() == rooms


def test_the_untouched_archive_restores(exported: bytes, alice: User) -> None:
    """The baseline of the cases below: without the change, the archive is accepted."""
    assert restore.restore_bytes(alice, rebuild(entries(exported))).home


@pytest.mark.parametrize(
    "name",
    [
        "../evil.txt",
        "/etc/passwd",
        "files/photos/../../../evil",
        "files\\photos\\x.jpg",
        "data/../manifest.json",
        "other.txt",
        # A trailing newline: `$` would let both through, the whole name must match.
        "data/rooms.json\n",
        "files/photos/00000000-0000-0000-0000-000000000000.jpg\n",
    ],
)
def test_a_name_outside_the_format_is_refused(exported: bytes, alice: User, name: str) -> None:
    files = entries(exported)
    files[name] = b"x"
    refused(alice, rebuild(files), "outside of what the format allows")


def test_a_symbolic_link_is_refused(exported: bytes, alice: User) -> None:
    def link(archive: zipfile.ZipFile) -> None:
        info = zipfile.ZipInfo("files/photos/00000000-0000-0000-0000-000000000000.jpg")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")

    refused(alice, rebuild(entries(exported), extra=link), "symbolic link")


def test_an_encrypted_entry_is_refused(exported: bytes, alice: User) -> None:
    """zipfile writes no encrypted entry: bit 0 of the flags is set as an encrypting tool would.

    Without the check, zipfile would ask for a password (RuntimeError) on reading it.
    """
    data = bytearray(rebuild(entries(exported)))
    name = b"data/rooms.json"
    # Local header: flags at offset 6, name at 30; central directory: flags at 8, name at 46.
    for signature, flags_at, name_at in ((b"PK\x03\x04", 6, 30), (b"PK\x01\x02", 8, 46)):
        start = data.find(signature)
        while start != -1:
            if data[start + name_at : start + name_at + len(name)] == name:
                data[start + flags_at] |= 0x1
            start = data.find(signature, start + 4)
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
        assert archive.getinfo("data/rooms.json").flag_bits & 0x1
    refused(alice, bytes(data), "encrypted file")


def test_a_zip_bomb_is_refused_before_it_is_unpacked(
    exported: bytes, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cap is the file quota of a home, gigabytes: lowered here to keep the test small.
    monkeypatch.setattr(restore, "max_unpacked_bytes", lambda: 4 * 1024 * 1024)
    files = entries(exported)
    # Highly compressible: kilobytes in the zip, more than the instance allows once unpacked.
    files["data/rooms.json"] = b" " * (8 * 1024 * 1024)
    data = rebuild(files, rehash=False)
    assert len(data) < 100_000
    refused(alice, data, "too large once unpacked")


def test_the_bomb_check_reads_the_sizes_and_not_the_content(
    exported: bytes, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused before a single entry is unpacked: no read of the content at all."""
    monkeypatch.setattr(restore, "max_unpacked_bytes", lambda: 1024)
    reads: list[str] = []
    original = restore._read_entry
    monkeypatch.setattr(
        restore, "_read_entry", lambda z, i: reads.append(i.filename) or original(z, i)
    )
    refused(alice, exported, "too large once unpacked")
    assert reads == []


def test_a_file_larger_than_the_instance_accepts_is_refused(exported: bytes, alice: User) -> None:
    files = entries(exported)
    big = next(n for n in files if n.startswith("files/photos/"))
    files[big] = b"\0" * (restore._file_limit() + 1)
    refused(alice, rebuild(files), "larger than this instance accepts")


def test_a_byte_changed_after_the_export_is_refused(exported: bytes, alice: User) -> None:
    files = entries(exported)
    files["data/tasks.json"] = files["data/tasks.json"].replace(
        b"Service the boiler", b"Service the bOiler"
    )
    refused(alice, rebuild(files, rehash=False), "changed after it was made")


def test_a_changed_file_is_refused(exported: bytes, alice: User) -> None:
    files = entries(exported)
    photo = next(n for n in files if n.startswith("files/photos/"))
    files[photo] = files[photo][:-1] + b"\x00"
    refused(alice, rebuild(files, rehash=False), "changed after it was made")


def test_a_file_missing_from_the_manifest_is_refused(exported: bytes, alice: User) -> None:
    files = entries(exported)
    manifest = json.loads(files["manifest.json"])
    manifest["entries"]["files/photos/00000000-0000-0000-0000-000000000000.jpg"] = {
        "sha256": "0" * 64,
        "size": 1,
    }
    files["manifest.json"] = json.dumps(manifest).encode()
    refused(alice, rebuild(files, rehash=False), "not those of its manifest")


@pytest.mark.parametrize("version", [1, 5, "2", None])
def test_an_unknown_version_is_refused(exported: bytes, alice: User, version: Any) -> None:
    files = entries(exported)
    manifest = json.loads(files["manifest.json"])
    manifest["version"] = version
    files["manifest.json"] = json.dumps(manifest).encode()
    refused(alice, rebuild(files, rehash=False), "cannot restore")


def test_a_file_that_is_not_a_zip_is_refused(alice: User) -> None:
    refused(alice, b"PK\x03\x04 not really", "not a valid zip")
    refused(alice, b"", "not a valid zip")


TRAPPED_JSON = {
    # Deeper than the stack: json.loads raises RecursionError, not JSONDecodeError.
    "deep": b"[" * 200_000,
    # Longer than Python converts to an int (4300 digits): json.loads raises ValueError.
    "huge integer": b'{"app": "rooms", "n": ' + b"9" * 5000 + b"}",
}


@pytest.mark.parametrize("name", ["manifest.json", "data/rooms.json"])
@pytest.mark.parametrize("trap", sorted(TRAPPED_JSON))
def test_a_trapped_json_document_is_refused(
    exported: bytes, alice: User, name: str, trap: str
) -> None:
    files = entries(exported)
    files[name] = TRAPPED_JSON[trap]
    # The data file keeps a manifest that agrees with it: only the JSON check stops it.
    refused(alice, rebuild(files, rehash=name != "manifest.json"), "not valid JSON")


def test_an_unknown_part_is_refused(exported: bytes, alice: User) -> None:
    files = entries(exported)
    manifest = json.loads(files["manifest.json"])
    manifest["apps"].append("spaceships")
    files["manifest.json"] = json.dumps(manifest).encode()
    files["data/spaceships.json"] = json.dumps({"app": "spaceships", "sections": {}}).encode()
    refused(alice, rebuild(files), "does not know")


def test_a_reference_to_an_object_of_another_home_is_refused(
    exported: bytes, alice: User, bob: User, bob_home: Home
) -> None:
    """The careful forger rehashes the manifest: the reference check is what stops it."""
    theirs = room_services.create_room(bob, bob_home, name="Bob's office")

    def point_at_bob(document: dict[str, Any]) -> None:
        document["sections"]["items"][0]["room"] = str(theirs.id)

    refused(alice, rebuild(edit(entries(exported), "rooms", point_at_bob)), "does not hold")
    assert Room.objects.get(pk=theirs.pk).home == bob_home


def test_a_many_to_many_link_to_another_home_is_refused(
    exported: bytes, alice: User, bob: User, bob_home: Home
) -> None:
    theirs = room_services.create_room(bob, bob_home, name="Bob's office")

    def point_at_bob(document: dict[str, Any]) -> None:
        document["sections"]["stock_items"][0]["rooms"] = [str(theirs.id)]

    refused(alice, rebuild(edit(entries(exported), "stock", point_at_bob)), "does not hold")


def test_an_object_already_on_the_instance_is_refused(
    exported: bytes, alice: User, bob: User, bob_home: Home
) -> None:
    theirs = Event.objects.create(home=bob_home, kind="bobs", payload={})

    def take_bobs_id(document: dict[str, Any]) -> None:
        document["sections"]["events"][0]["id"] = str(theirs.id)

    files = edit(entries(exported), "events", take_bobs_id)
    refused(alice, rebuild(files), "already holds")
    assert Event.objects.get(pk=theirs.pk).home == bob_home


def test_restoring_twice_is_refused_and_explained(exported: bytes, alice: User) -> None:
    restore.restore_bytes(alice, exported)
    refused(alice, exported, "already holds the objects of this archive")


@pytest.mark.parametrize(
    ("app", "section", "field", "value"),
    [
        ("tasks", "tasks", "priority", "urgent!"),
        ("tasks", "tasks", "labour_budget", "NaN"),
        ("tasks", "tasks", "labour_budget", 12.5),
        ("tasks", "tasks", "repeat_interval", "1"),
        ("rooms", "items", "installed_on", "yesterday"),
        ("rooms", "rooms", "name", "x" * 500),
        ("rooms", "items", "contract_url", "javascript:alert(1)"),
        ("rooms", "rooms", "created_at", "2026-01-01T00:00:00"),
    ],
)
def test_a_value_its_field_does_not_accept_is_refused(
    exported: bytes, alice: User, app: str, section: str, field: str, value: Any
) -> None:
    def change(document: dict[str, Any]) -> None:
        document["sections"][section][0][field] = value

    refused(alice, rebuild(edit(entries(exported), app, change)), "not valid")


def test_a_file_whose_type_is_not_the_one_of_its_row_is_refused(
    exported: bytes, alice: User
) -> None:
    files = entries(exported)
    photo = next(n for n in files if n.startswith("files/photos/") and n.endswith(".jpg"))
    files[photo] = image_bytes("PNG")
    refused(alice, rebuild(files), "not of the type its object says")


def test_a_page_disguised_as_a_photo_is_refused(exported: bytes, alice: User) -> None:
    files = entries(exported)
    photo = next(n for n in files if n.startswith("files/photos/"))
    files[photo] = b"<html><script>alert(1)</script></html>"
    refused(alice, rebuild(files), "not a valid image")


def test_a_person_of_the_archive_never_gets_an_account(exported: bytes, alice: User) -> None:
    accounts = User.objects.count()
    result = restore.restore_bytes(alice, exported, match_people=True)
    assert User.objects.count() == accounts
    assert result.matched == ["member"]


class TestRoutes:
    def test_a_signed_in_person_restores_from_the_web_without_changing_the_current_home(
        self, client: Client, exported: bytes, alice: User, bob_home: Home, bob: User
    ) -> None:
        client.force_login(bob)
        session = client.session
        session["current_home"] = str(bob_home.id)
        session.save()
        response = client.post(
            reverse("homes:restore"), {"archive": upload(exported, "home.zip", "application/zip")}
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("homes:list")
        restored = Home.objects.exclude(pk=bob_home.pk).get()
        assert services.is_owner(bob, restored)
        assert client.session.get("current_home") == str(bob_home.id)

    def test_a_refused_archive_shows_the_reason_on_the_form(
        self, client: Client, alice: User
    ) -> None:
        client.force_login(alice)
        response = client.post(
            reverse("homes:restore"), {"archive": upload(b"nope", "home.zip", "application/zip")}
        )
        assert response.status_code == 200
        assert "not a valid zip" in response.content.decode()
        assert not Home.objects.exists() or Home.objects.count() == 1

    @pytest.mark.parametrize("trap", sorted(TRAPPED_JSON))
    def test_a_trapped_json_document_is_refused_on_the_form_and_not_an_error(
        self, client: Client, exported: bytes, alice: User, trap: str
    ) -> None:
        files = entries(exported)
        files["data/rooms.json"] = TRAPPED_JSON[trap]
        homes = Home.all_objects.count()
        client.force_login(alice)
        response = client.post(
            reverse("homes:restore"),
            {"archive": upload(rebuild(files), "home.zip", "application/zip")},
        )
        assert response.status_code == 200
        assert "not valid JSON" in response.content.decode()
        assert Home.all_objects.count() == homes

    def test_the_api_restores_for_a_signed_in_person_and_never_for_a_token(
        self,
        client: Client,
        api_client: APIClient,
        exported: bytes,
        alice: User,
        bob: User,
        bob_home: Home,
    ) -> None:
        url = reverse("api-v1:home-restore")
        _token, raw = token_services.issue_token(
            bob, name="all", scopes=["read", "write"], homes=[bob_home]
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        response = api_client.post(
            url, {"archive": upload(exported, "home.zip", "application/zip")}, format="multipart"
        )
        assert response.status_code == 403
        assert list(Home.objects.all()) == [bob_home]  # nothing restored
        client.force_login(bob)
        response = client.post(url, {"archive": upload(exported, "home.zip", "application/zip")})
        assert response.status_code == 201
        assert response.json()["role"] == "owner"

    def test_an_anonymous_visitor_cannot_restore(self, client: Client, exported: bytes) -> None:
        response = client.post(
            reverse("homes:restore"), {"archive": upload(exported, "home.zip", "application/zip")}
        )
        assert response.status_code == 302
        assert "login" in response["Location"]


def test_the_restore_page_comes_from_the_list_of_homes_and_leads_back_to_it(
    client: Client, alice: User, alice_home: Home
) -> None:
    client.force_login(alice)
    listing = client.get(reverse("homes:list")).content.decode()
    assert reverse("homes:restore") in listing
    page = client.get(reverse("homes:restore")).content.decode()
    assert f'href="{reverse("homes:list")}"' in page
    assert 'enctype="multipart/form-data"' in page
