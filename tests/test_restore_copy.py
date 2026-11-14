"""Restore as a copy: a new home, new identifiers, next to the original (docs/adr/0069).

The archive of a home holding a row of every table is restored as a copy on the very
instance where that home still is, twice; each model is then listed to show that no row,
no link and no file is shared between the original and a copy, that the original has not
changed, and that a failure half-way leaves nothing behind.
"""

import datetime
import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.audit.models import AuditEvent
from housecairn.homes import exports, restore, services
from housecairn.homes.models import Home, Invitation, Membership, Role
from housecairn.labels.models import Label
from tests.data_support import archive_bytes, export_data, fill_home
from tests.rooms_support import upload
from tests.test_restore_roundtrip import snapshot

pytest_plugins = ("tests.rooms_fixtures",)
pytestmark = pytest.mark.django_db


def restorable() -> list[exports.Section]:
    return [s for _app, s in exports.all_sections() if s.restore]


def rows_of(section: exports.Section, home_id: Any) -> list[models.Model]:
    return list(section.model._base_manager.filter(home_id=home_id))


def references(section: exports.Section, row: models.Model) -> list[tuple[Any, Any]]:
    """Every (model, identifier) a row points to: its keys and its many-to-many links."""
    out: list[tuple[Any, Any]] = []
    for f in section.fields():
        if (
            isinstance(f, models.ForeignKey)
            and not exports.person_reference(f)
            and f.related_model is not Home
        ):
            value = getattr(row, f.attname)
            if value is not None:
                out.append((f.related_model, value))
    for relation in section.many_to_many():
        for pk in getattr(row, relation.name).values_list("pk", flat=True):
            out.append((relation.related_model, pk))
    return out


def content(home_id: Any) -> dict[str, list[tuple[Any, ...]]]:
    """What the rows of a home say, without any identifier: what a copy must give again."""
    out: dict[str, list[tuple[Any, ...]]] = {}
    for section in restorable():
        files = {spec.field for spec in section.files} | {
            spec.thumbnail for spec in section.files if spec.thumbnail
        }
        values = []
        for row in rows_of(section, home_id):
            kept = []
            for f in section.fields():
                if f.primary_key or f.is_relation or f.name in files:
                    continue
                if isinstance(f, models.DateTimeField) and (f.auto_now or f.auto_now_add):
                    continue
                if section.name == "labels" and f.name == "code":
                    continue
                kept.append((f.name, repr(getattr(row, f.attname))))
            for spec in section.files:
                stored = getattr(row, spec.field)
                kept.append((spec.field, Path(stored.path).read_bytes() if stored else None))
            values.append(tuple(kept))
        out[section.name] = sorted(values, key=repr)
    return out


def media_files(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def original(
    media_root: Path,
    alice: User,
    alice_home: Home,
    make_user: Callable[..., User],
    add_member: Callable[..., Any],
) -> Home:
    member = make_user("member@example.com")
    add_member(alice_home, member, Role.MEMBER)
    fill_home(alice, member, alice_home)
    return alice_home


@pytest.fixture
def archive(original: Home) -> bytes:
    return archive_bytes(original)


def copy(actor: User, data: bytes, **options: Any) -> restore.RestoreResult:
    return restore.restore_bytes(actor, data, as_copy=True, **options)


class TestNewIdentifiers:
    def test_a_copy_restores_next_to_its_original_on_the_same_instance(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        result = copy(alice, archive)
        assert result.home.id != original.id
        assert Home.objects.filter(pk=original.pk).exists()
        assert result.home.name == "Main house (copy)"
        assert content(result.home.id) == content(original.id)

    def test_no_row_of_any_model_is_shared_and_every_link_stays_inside_the_copy(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        first = copy(alice, archive).home
        second = copy(alice, archive).home
        homes = [original.id, first.id, second.id]
        for section in restorable():
            ids = [{row.pk for row in rows_of(section, home_id)} for home_id in homes]
            assert ids[0], f"{section.name}: the original has no row to copy"
            assert len(ids[0]) == len(ids[1]) == len(ids[2]), section.name
            assert not ids[0] & ids[1], section.name
            assert not ids[0] & ids[2], section.name
            assert not ids[1] & ids[2], section.name
            for home_id in homes:
                for row in rows_of(section, home_id):
                    for model, pk in references(section, row):
                        target = model._base_manager.get(pk=pk)
                        assert target.home_id == home_id, f"{section.name} -> {model.__name__}"

    def test_the_counts_of_the_copy_are_those_of_the_archive(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        data = export_data(archive)
        result = copy(alice, archive)
        for section in restorable():
            assert result.counts.get(section.name, 0) == len(data[section.name]), section.name

    def test_every_file_is_copied_and_none_is_shared(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        result = copy(alice, archive)
        checked = 0
        for section in restorable():
            for spec in section.files:
                originals = {
                    Path(getattr(r, spec.field).path).read_bytes(): getattr(r, spec.field).path
                    for r in rows_of(section, original.id)
                    if getattr(r, spec.field)
                }
                for row in rows_of(section, result.home.id):
                    stored = getattr(row, spec.field)
                    if not stored:
                        continue
                    data = Path(stored.path).read_bytes()
                    assert data in originals, section.name
                    assert stored.path != originals[data], section.name
                    assert str(result.home.id) in stored.name, section.name
                    assert str(original.id) not in stored.name, section.name
                    thumb = getattr(row, spec.thumbnail) if spec.thumbnail else None
                    if thumb:  # an image has one, a document none
                        assert str(result.home.id) in thumb.name, section.name
                        assert str(original.id) not in thumb.name, section.name
                    checked += 1
        assert checked >= 8  # photos, stock label, attachments, project files, report
        # Removing the files of the copy leaves those of the original.
        before = snapshot(original.id)
        services.erase_home(result.home)
        assert snapshot(original.id) == before

    def test_the_original_is_unchanged_in_count_and_content(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        before = snapshot(original.id)
        home_row = Home.objects.values().get(pk=original.id)
        members = list(Membership.objects.filter(home=original).values_list("user_id", "role"))
        invitations = Invitation.objects.filter(home=original).count()
        copy(alice, archive)
        copy(alice, archive)
        assert snapshot(original.id) == before
        assert Home.objects.values().get(pk=original.id) == home_row
        assert (
            list(Membership.objects.filter(home=original).values_list("user_id", "role")) == members
        )
        assert Invitation.objects.filter(home=original).count() == invitations

    def test_labels_get_new_codes_and_the_old_stickers_open_the_original(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        result = copy(alice, archive)
        old = set(Label.all_objects.filter(home=original).values_list("code", flat=True))
        new = set(Label.all_objects.filter(home=result.home).values_list("code", flat=True))
        assert len(new) == len(old) == 2
        assert not old & new

    def test_the_restore_keeps_refusing_while_the_original_is_here(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        with pytest.raises(restore.ArchiveRefusedError, match="Restore it as a copy"):
            restore.restore_bytes(alice, archive)


class TestPeopleAndName:
    def test_only_the_person_who_copies_is_a_member_and_owner(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        assert Invitation.objects.filter(home=original).exists()
        result = copy(alice, archive, match_people=True)
        roles = list(Membership.objects.filter(home=result.home).values_list("user_id", "role"))
        assert roles == [(alice.pk, Role.OWNER)]
        assert not Invitation.objects.filter(home=result.home).exists()
        assert result.matched == []
        assert result.unattributed == ["member"]

    def test_two_copies_get_two_names_and_a_chosen_name_is_kept(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        assert copy(alice, archive).home.name == "Main house (copy)"
        assert copy(alice, archive).home.name == "Main house (copy 2)"
        assert copy(alice, archive, name="  Test\u200b  house ").home.name == "Test house"

    def test_a_long_name_is_shortened_to_fit_with_its_suffix(self, alice: User) -> None:
        name = restore.copy_name(alice, "x" * 120)
        assert len(name) == 120
        assert name.endswith(" (copy)")

    def test_a_chosen_name_too_long_is_refused_and_nothing_is_made(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        homes = Home.all_objects.count()
        with pytest.raises(restore.ArchiveRefusedError, match="too long"):
            copy(alice, archive, name="x" * 121)
        assert Home.all_objects.count() == homes

    def test_the_copy_is_recorded_in_its_audit_log(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        result = copy(alice, archive)
        assert AuditEvent.objects.filter(action="home.copied", home_id=result.home.id).exists()
        assert not AuditEvent.objects.filter(action="home.copied", home_id=original.id).exists()


class TestRights:
    @pytest.mark.parametrize("role", [Role.MEMBER, Role.READ_ONLY, None])
    def test_who_is_not_an_owner_of_the_original_cannot_copy_it(
        self,
        original: Home,
        archive: bytes,
        make_user: Callable[..., User],
        add_member: Callable[..., Any],
        role: Role | None,
    ) -> None:
        person = make_user("reader@example.com")
        if role is not None:
            add_member(original, person, role)
        homes = Home.all_objects.count()
        with pytest.raises(restore.CopyNotAllowedError):
            copy(person, archive)
        assert Home.all_objects.count() == homes

    def test_another_owner_of_the_original_can_copy_it(
        self,
        original: Home,
        archive: bytes,
        make_user: Callable[..., User],
        add_member: Callable[..., Any],
    ) -> None:
        other = make_user("owner2@example.com")
        add_member(original, other, Role.OWNER)
        result = copy(other, archive)
        assert services.is_owner(other, result.home)

    def test_once_the_original_is_gone_anyone_with_the_archive_can_copy_it(
        self, original: Home, archive: bytes, bob: User
    ) -> None:
        services.erase_home(original)
        result = copy(bob, archive)
        assert services.is_owner(bob, result.home)

    def test_the_web_page_refuses_a_member_with_the_reason(
        self,
        client: Client,
        original: Home,
        archive: bytes,
        make_user: Callable[..., User],
        add_member: Callable[..., Any],
    ) -> None:
        person = make_user("reader@example.com")
        add_member(original, person, Role.READ_ONLY)
        client.force_login(person)
        homes = Home.all_objects.count()
        response = client.post(
            reverse("homes:restore"),
            {"archive": upload(archive, "home.zip", "application/zip"), "mode": "copy"},
        )
        assert response.status_code == 403
        assert "only one of its owners may copy it" in response.content.decode()
        assert Home.all_objects.count() == homes

    def test_the_api_refuses_a_member_and_never_takes_a_token(
        self,
        api_client: APIClient,
        client: Client,
        original: Home,
        archive: bytes,
        make_user: Callable[..., User],
        add_member: Callable[..., Any],
    ) -> None:
        from housecairn.apitokens import services as token_services

        person = make_user("reader@example.com")
        add_member(original, person, Role.MEMBER)
        url = reverse("api-v1:home-restore")
        client.force_login(person)
        response = client.post(
            url, {"archive": upload(archive, "home.zip", "application/zip"), "as_copy": "true"}
        )
        assert response.status_code == 403
        assert "only one of its owners" in response.json()["detail"]
        _token, raw = token_services.issue_token(
            person, name="all", scopes=["read", "write"], homes=[original]
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        response = api_client.post(
            url,
            {"archive": upload(archive, "home.zip", "application/zip"), "as_copy": True},
            format="multipart",
        )
        assert response.status_code == 403

    def test_the_command_refuses_an_owner_who_is_not_one_of_the_original(
        self,
        original: Home,
        archive: bytes,
        tmp_path: Path,
        make_user: Callable[..., User],
        add_member: Callable[..., Any],
    ) -> None:
        person = make_user("reader@example.com")
        add_member(original, person, Role.READ_ONLY)
        path = tmp_path / "home.zip"
        path.write_bytes(archive)
        homes = Home.all_objects.count()
        with pytest.raises(CommandError, match="only one of its owners"):
            call_command("restore_home", str(path), "--owner", person.email, "--copy")
        assert Home.all_objects.count() == homes


class TestAllOrNothing:
    @pytest.mark.parametrize("as_copy", [True, False])
    def test_a_failure_half_way_creates_nothing_and_leaves_no_file(
        self,
        original: Home,
        archive: bytes,
        alice: User,
        media_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        as_copy: bool,
    ) -> None:
        """The restore as well: the files it had stored before the failure were left."""
        if not as_copy:
            services.erase_home(original)
        checked = restore._check_file
        calls = {"n": 0}

        def fail_on_the_third(*args: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 3:
                raise restore.ArchiveRefusedError("refused on purpose")
            return checked(*args, **kwargs)

        monkeypatch.setattr(restore, "_check_file", fail_on_the_third)
        counts = {s.name: s.model._base_manager.count() for s in restorable()}
        homes = Home.all_objects.count()
        memberships = Membership.objects.count()
        files = media_files(media_root)
        with pytest.raises(restore.ArchiveRefusedError, match="on purpose"):
            restore.restore_bytes(alice, archive, as_copy=as_copy)
        assert calls["n"] == 3  # two files were written, then removed
        assert Home.all_objects.count() == homes
        assert Membership.objects.count() == memberships
        assert {s.name: s.model._base_manager.count() for s in restorable()} == counts
        assert media_files(media_root) == files

    def test_a_changed_archive_is_refused_as_by_the_restore(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            entries = {n: zipped.read(n) for n in zipped.namelist()}
        rooms = json.loads(entries["data/rooms.json"])
        rooms["sections"]["rooms"][0]["name"] = "Changed"
        entries["data/rooms.json"] = json.dumps(rooms).encode()
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zipped:
            for name, body in entries.items():
                zipped.writestr(name, body)
        homes = Home.all_objects.count()
        with pytest.raises(restore.ArchiveRefusedError, match="changed after it was made"):
            copy(alice, out.getvalue())
        assert Home.all_objects.count() == homes


class TestFormats:
    def test_a_version_2_archive_is_copied_with_its_providers_made_contractors(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        from tests.test_restore_security import entries, rebuild

        files = entries(archive)
        manifest = json.loads(files["manifest.json"])
        manifest["version"] = 2
        files["manifest.json"] = json.dumps(manifest).encode()
        document = json.loads(files["data/rooms.json"])
        for row in document["sections"]["items"]:
            row.pop("contract_contractor")
            row["contract_provider"] = "Acme Heating"
        files["data/rooms.json"] = json.dumps(document).encode()
        result = copy(alice, rebuild(files))
        from housecairn.rooms.models import Item
        from housecairn.tasks.models import Contractor

        item = Item.objects.get(home=result.home)
        assert item.contract_contractor is not None
        assert item.contract_contractor.home_id == result.home.id
        assert item.contract_contractor.name == "Acme Heating"
        assert not Contractor.objects.filter(home=original, name="Acme Heating").exists()
        assert (
            AuditEvent.objects.get(action="home.copied", home_id=result.home.id).detail["format"]
            == "v2"
        )

    def test_a_version_3_archive_is_copied_with_no_archiving(
        self, original: Home, archive: bytes, alice: User
    ) -> None:
        from tests.test_restore_security import entries, rebuild

        assert export_data(archive)["manifest"]["version"] == exports.FORMAT_VERSION == 4
        files = entries(archive)
        manifest = json.loads(files["manifest.json"])
        manifest["version"] = 3
        files["manifest.json"] = json.dumps(manifest).encode()
        document = json.loads(files["data/homes.json"])
        document["home"].pop("archived_at")  # format 3 has no such field
        files["data/homes.json"] = json.dumps(document).encode()
        result = copy(alice, rebuild(files))
        assert result.home.archived_at is None
        assert (
            AuditEvent.objects.get(action="home.copied", home_id=result.home.id).detail["format"]
            == "v3"
        )


class TestScreen:
    def test_the_page_offers_both_and_says_members_are_not_copied(
        self, client: Client, alice: User
    ) -> None:
        client.force_login(alice)
        page = client.get(reverse("homes:restore")).content.decode()
        assert 'value="restore"' in page
        assert 'value="copy"' in page
        assert "Restore as a copy" in page
        assert "Its members and invitations are not copied" in page
        assert 'data-shown-when="mode=copy"' in page

    def test_a_copy_from_the_web_with_a_name_lands_in_the_list(
        self, client: Client, original: Home, archive: bytes, alice: User
    ) -> None:
        client.force_login(alice)
        response = client.post(
            reverse("homes:restore"),
            {
                "archive": upload(archive, "home.zip", "application/zip"),
                "mode": "copy",
                "name": "Test house",
            },
            follow=True,
        )
        assert response.redirect_chain[-1][0] == reverse("homes:list")
        made = Home.objects.get(name="Test house")
        assert made.id != original.id
        assert "You are its only member" in response.content.decode()

    def test_a_form_without_a_mode_restores_as_before(
        self, client: Client, original: Home, archive: bytes, alice: User
    ) -> None:
        client.force_login(alice)
        response = client.post(
            reverse("homes:restore"),
            {"archive": upload(archive, "home.zip", "application/zip"), "name": "Ignored"},
        )
        assert response.status_code == 200  # refused: the original is here
        assert "Restore it as a copy" in response.content.decode()
        assert not Home.objects.filter(name="Ignored").exists()

    def test_the_api_copies_with_a_name(
        self, client: Client, original: Home, archive: bytes, alice: User
    ) -> None:
        client.force_login(alice)
        response = client.post(
            reverse("api-v1:home-restore"),
            {
                "archive": upload(archive, "home.zip", "application/zip"),
                "as_copy": "true",
                "name": "API copy",
            },
        )
        assert response.status_code == 201
        assert response.json()["name"] == "API copy"
        assert response.json()["id"] != str(original.id)

    def test_the_command_copies_and_says_so(
        self, original: Home, archive: bytes, alice: User, tmp_path: Path
    ) -> None:
        path = tmp_path / "home.zip"
        path.write_bytes(archive)
        out = io.StringIO()
        call_command(
            "restore_home", str(path), "--owner", alice.email, "--copy", "--name", "CLI", stdout=out
        )
        made = Home.objects.get(name="CLI")
        assert f"Restored home {made.id} as a copy: CLI." in out.getvalue()
        roles = list(Membership.objects.filter(home=made).values_list("user_id", "role"))
        assert roles == [(alice.pk, Role.OWNER)]
        with pytest.raises(CommandError, match="add --copy"):
            call_command("restore_home", str(path), "--owner", alice.email, "--name", "X")


def test_every_unique_value_of_a_restored_table_is_drawn_again_by_a_copy() -> None:
    """A unique column other than the identifier would make a copy collide (docs/adr/0069)."""
    for section in restorable():
        # A key (one-to-one) follows the new identifier of its target already.
        unique = [
            f.name
            for f in section.fields()
            if getattr(f, "unique", False) and not f.primary_key and not f.is_relation
        ]
        if unique:
            assert section.on_copy is not None, f"{section.name}: {unique}"


def test_a_copy_keeps_the_dates_of_its_history(original: Home, archive: bytes, alice: User) -> None:
    from housecairn.tasks.models import Occurrence

    result = copy(alice, archive)
    done = sorted(
        Occurrence.objects.filter(home=original).values_list("completed_on", flat=True), key=str
    )
    assert (
        sorted(
            Occurrence.objects.filter(home=result.home).values_list("completed_on", flat=True),
            key=str,
        )
        == done
    )
    assert datetime.date(2026, 10, 2) in done
