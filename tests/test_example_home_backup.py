"""The instance backup leaves the example home out, and a restore never makes one (docs/adr/0084).

`backup_instance` is the single point of passage of a backup of the server: its `dump_database`
and its walk of `MEDIA_ROOT` are the only places that copy data out of the instance. The
exclusion is applied on the copy, never on the live database, before the copy enters the
archive. On PostgreSQL `pg_dump` runs by hand (its client is older than the CI service), so the
real archive is read on SQLite; the PostgreSQL path is checked here with the programs faked.
"""

import io
import json
import sqlite3
import subprocess
import tarfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.core.management import CommandError, call_command
from django.db import connection

from housecairn.accounts.models import User
from housecairn.audit.models import AuditEvent
from housecairn.guide.models import StepTick
from housecairn.homes import files
from housecairn.homes.models import Home, Role
from housecairn.instance.management.commands import backup_instance
from housecairn.reports import services as report_services
from housecairn.rooms.models import Room
from tests.data_support import fill_home
from tests.example_support import make_example, write_a_file_in_every_folder

pytest_plugins = ("tests.rooms_fixtures",)

on_sqlite = pytest.mark.skipif(
    connection.vendor != "sqlite", reason="the backup of a SQLite file; pg_dump is checked by hand"
)

# Texts that only the example home holds (French: the real home of `fill_home` is in English).
ONLY_THE_EXAMPLE = (
    b"Machine expresso",
    b"Le robinet de la cuisine goutte",
    b"Plomberie des Tilleuls",
    b"The cupboard door is loose",
)


@pytest.fixture
def instance(
    media_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alice: User,
    alice_home: Home,
    make_user: Callable[..., User],
    add_member: Callable[..., Any],
) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    (data / "secret_key").write_text("k" * 60 + "\n")
    (data / "secret_key").chmod(0o600)
    monkeypatch.setenv("HOUSECAIRN_DATA_DIR", str(data))
    member = make_user("member@example.com")
    add_member(alice_home, member, Role.MEMBER)
    fill_home(alice, member, alice_home)
    return data


def run_backup(output: Path) -> Path:
    out = io.StringIO()
    call_command("backup_instance", "--output", str(output), stdout=out, stderr=io.StringIO())
    return Path(out.getvalue().strip())


def run_restore(archive: Path, target: Path) -> str:
    out = io.StringIO()
    call_command(
        "restore_instance",
        str(archive),
        "--database-file",
        str(target / "db.sqlite3"),
        "--media-root",
        str(target / "media"),
        "--data-dir",
        str(target / "data"),
        stdout=out,
    )
    return out.getvalue()


def home_tables() -> list[str]:
    """The tables with a `home` column, and the table of the homes: where a home has rows."""
    return sorted(
        model._meta.db_table
        for model in apps.get_models(include_auto_created=True)
        if model._meta.app_config is not None
        and model._meta.app_config.name.startswith("housecairn.")
        and any(f.name == "home" for f in model._meta.concrete_fields)
    )


def rows_of(database: Path, home_id: uuid.UUID) -> dict[str, int]:
    found = {}
    db = sqlite3.connect(database)
    try:
        for table in home_tables():
            found[table] = db.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE home_id = ?',  # noqa: S608
                (home_id.hex,),
            ).fetchone()[0]
        found["audit_auditevent"] = db.execute(
            "SELECT COUNT(*) FROM audit_auditevent WHERE home_id = ?", (home_id.hex,)
        ).fetchone()[0]
    finally:
        db.close()
    return found


def live_rows(home: Home) -> dict[str, int]:
    found = {}
    with connection.cursor() as cursor:
        for table in home_tables():
            cursor.execute(f'SELECT COUNT(*) FROM "{table}" WHERE home_id = %s', [home.pk.hex])  # noqa: S608
            found[table] = cursor.fetchone()[0]
    found["audit_auditevent"] = AuditEvent.objects.filter(home_id=home.pk).count()
    return found


def with_a_file_in_every_folder(*homes: Home, root: Path) -> dict[uuid.UUID, list[Path]]:
    return {home.pk: write_a_file_in_every_folder(home, root) for home in homes}


@on_sqlite
@pytest.mark.django_db(transaction=True)
def test_the_instance_backup_leaves_out_the_example_home(
    instance: Path, tmp_path: Path, media_root: Path, alice: User, alice_home: Home
) -> None:
    example = make_example(alice, language="fr")
    report_services.create_report(alice, example, title="The cupboard door is loose")
    report_services.create_report(alice, alice_home, title="The gate squeaks")
    assert AuditEvent.objects.filter(home_id=example.pk).exists()
    assert StepTick.objects.filter(home=example).exists()
    written = with_a_file_in_every_folder(example, alice_home, root=media_root)
    assert len(written[example.pk]) == len(files.MEDIA_FOLDERS) >= 2  # every folder an app declares
    live_before = (live_rows(example), live_rows(alice_home))

    archive = run_backup(tmp_path / "backups")

    with tarfile.open(archive) as tar:
        names = tar.getnames()
        manifest = json.loads(tar.extractfile("manifest.json").read())  # type: ignore[union-attr]
        database = tmp_path / "database"
        database.write_bytes(tar.extractfile("database").read())  # type: ignore[union-attr]
    # No file of the example home, in any folder; every file of the real home, in each.
    assert not [n for n in names if str(example.pk) in n]
    for path in written[alice_home.pk]:
        assert f"media/{path.relative_to(media_root).as_posix()}" in names
    assert not [n for n in manifest["parts"] if str(example.pk) in n]
    # No row, no audit entry and no tick of the example home; the real home whole.
    assert all(count == 0 for count in rows_of(database, example.pk).values())
    assert rows_of(database, alice_home.pk) == live_before[1]
    assert sum(live_before[1].values()) > 20
    db = sqlite3.connect(database)
    try:
        assert db.execute("SELECT COUNT(*) FROM homes_home WHERE is_example = 1").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM guide_steptick").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM homes_home").fetchone() == (1,)
        assert db.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    finally:
        db.close()
    # Not even a free page that still holds a text of the example home.
    raw = database.read_bytes()
    for text in ONLY_THE_EXAMPLE:
        assert text not in raw, text
    # The live database was not touched.
    assert (live_rows(example), live_rows(alice_home)) == live_before
    assert Home.objects.filter(pk=example.pk, is_example=True).exists()
    assert all(path.exists() for path in written[example.pk])


@on_sqlite
@pytest.mark.django_db(transaction=True)
def test_a_backup_without_an_example_home_is_what_it_was(
    instance: Path, tmp_path: Path, media_root: Path, alice_home: Home
) -> None:
    """With no example home the copy is not opened by the exclusion: nothing is rewritten."""
    archive = run_backup(tmp_path / "backups")
    with tarfile.open(archive) as tar:
        database = tmp_path / "database"
        database.write_bytes(tar.extractfile("database").read())  # type: ignore[union-attr]
    assert rows_of(database, alice_home.pk) == live_rows(alice_home)


def forged_backup(output: Path, media: Path) -> Path:
    """A backup made by hand, the database copied with its example homes: nothing refuses one.

    The format is the one of `backup_instance`; only the exclusion is missing, as in a file
    written by another hand or by an older version of the program.
    """
    output.mkdir(parents=True, exist_ok=True)
    database = output / "database"
    destination = sqlite3.connect(database)
    connection.ensure_connection()
    connection.connection.backup(destination)
    destination.close()
    parts = {"database": backup_instance.sha256(database)}
    archive = output / "forged.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(database, arcname="database")
        for path in sorted(media.rglob("*")):
            if path.is_file():
                name = "media/" + path.relative_to(media).as_posix()
                tar.add(path, arcname=name, recursive=False)
                parts[name] = backup_instance.sha256(path)
        manifest = json.dumps(
            {
                "format": backup_instance.FORMAT,
                "version": backup_instance.VERSION,
                "database": "sqlite",
                "application_version": "test",
                "made_at": "2026-11-16T00:00:00+00:00",
                "parts": parts,
            }
        ).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
    return archive


@on_sqlite
@pytest.mark.django_db(transaction=True)
def test_a_restore_never_creates_an_example_home(
    instance: Path, tmp_path: Path, media_root: Path, alice: User, alice_home: Home
) -> None:
    """A database in which a home is marked: the home, its rows, its audit entries and its files
    are erased by the restore, in every folder; the real home comes back whole."""
    example = make_example(alice)
    written = with_a_file_in_every_folder(example, alice_home, root=media_root)
    archive = forged_backup(tmp_path / "forged", media_root)
    with tarfile.open(archive) as tar:
        assert any(str(example.pk) in name for name in tar.getnames())  # it does hold it

    target = tmp_path / "restored"
    said = run_restore(archive, target)

    assert "1 example home(s) in the backup were left out" in said
    assert all(count == 0 for count in rows_of(target / "db.sqlite3", example.pk).values())
    assert rows_of(target / "db.sqlite3", alice_home.pk) == live_rows(alice_home)
    restored = sqlite3.connect(target / "db.sqlite3")
    try:
        assert restored.execute(
            "SELECT COUNT(*) FROM homes_home WHERE is_example = 1"
        ).fetchone() == (0,)
        assert restored.execute("SELECT COUNT(*) FROM guide_steptick").fetchone() == (0,)
    finally:
        restored.close()
    for path in written[example.pk]:
        assert not (target / "media" / path.relative_to(media_root)).exists()
        assert not (target / "media" / path.relative_to(media_root)).parent.exists()
    for path in written[alice_home.pk]:
        assert (target / "media" / path.relative_to(media_root)).exists()


@on_sqlite
@pytest.mark.django_db(transaction=True)
def test_a_backup_made_before_the_mark_restores_untouched(
    instance: Path, tmp_path: Path, media_root: Path, alice_home: Home
) -> None:
    """A database with no `is_example` column (made before the guide) cannot hold an example."""
    archive = forged_backup(tmp_path / "old", media_root)
    old = tmp_path / "old-database"
    with tarfile.open(archive) as tar:
        old.write_bytes(tar.extractfile("database").read())  # type: ignore[union-attr]
    db = sqlite3.connect(old)
    try:
        db.execute("ALTER TABLE homes_home DROP COLUMN is_example")
        db.commit()
    finally:
        db.close()
    assert backup_instance.clean_sqlite_copy(old) == []


# ---------- PostgreSQL: the programs are faked ----------


COPY = (
    "COPY public.homes_home (id, created_at, name, is_example, archived_at) FROM stdin;\n"
    "{real}\t2026-11-16 10:00:00+00\tMain house\tf\t\\N\n"
    "{example}\t2026-11-16 10:00:00+00\tExample house\tt\t\\N\n"
    "\\.\n"
)


def test_the_example_homes_of_a_postgresql_dump_are_read_from_its_copy_block() -> None:
    real, example = uuid.uuid4(), uuid.uuid4()
    text = (
        "-- data\n"
        + COPY.format(real=real, example=example)
        + "COPY public.other (id) FROM stdin;\nx\n\\.\n"
    )
    assert backup_instance.example_homes_in_copy_text(text) == [example]
    assert (
        backup_instance.example_homes_in_copy_text(
            COPY.format(real=real, example=real).replace("\tt\t", "\tf\t")
        )
        == []
    )


def test_a_postgresql_dump_with_an_example_home_goes_through_a_scratch_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real, example = uuid.uuid4(), uuid.uuid4()
    calls: list[tuple[str, list[str]]] = []
    statements: list[str] = []

    class Cursor:
        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def execute(self, statement: str) -> None:
            statements.append(statement)

    class Ops:
        quote_name = staticmethod(lambda name: f'"{name}"')

    class FakeConnection:
        vendor = "postgresql"
        settings_dict = {"NAME": "housecairn"}
        ops = Ops()

        def cursor(self) -> Cursor:
            return Cursor()

    def which(name: str) -> str:
        return f"/usr/bin/{name}"

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        program = Path(args[0]).name
        calls.append((program, args[1:]))
        if program == "pg_dump":
            target = next(a for a in args if a.startswith("--file=")).removeprefix("--file=")
            Path(target).write_bytes(b"dump of " + kwargs["env"].get("PGDATABASE", "live").encode())
        output = COPY.format(real=real, example=example) if "--data-only" in args else ""
        return subprocess.CompletedProcess(args, 0, output, "")

    removed: list[str] = []

    def remove(alias: str) -> list[uuid.UUID]:
        removed.append(alias)
        return [example]

    class Copy:
        def __init__(self, name: Any) -> None:
            self.name = str(name)

        def __enter__(self) -> str:
            return self.name

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(backup_instance, "connection", FakeConnection())
    monkeypatch.setattr(backup_instance.shutil, "which", which)
    monkeypatch.setattr(backup_instance.subprocess, "run", run)
    monkeypatch.setattr(backup_instance, "remove_example_homes", remove)
    monkeypatch.setattr(backup_instance, "copy_connection", Copy)
    monkeypatch.setattr(
        backup_instance, "postgres_environment", lambda: {"PGDATABASE": "housecairn"}
    )
    target = tmp_path / "database"

    kind, left_out = backup_instance.dump_database(target)

    assert (kind, left_out) == ("postgresql", [example])
    programs = [name for name, _args in calls]
    assert programs == ["pg_dump", "pg_restore", "pg_restore", "pg_dump"]
    scratch = removed[0]
    assert scratch.startswith("housecairn_backup_")
    assert statements[0] == f'CREATE DATABASE "{scratch}"'
    assert statements[-1] == f'DROP DATABASE IF EXISTS "{scratch}"'
    # The archive is the second dump, taken from the scratch database, not the live one.
    assert target.read_bytes() == f"dump of {scratch}".encode()
    assert not list(tmp_path.glob("*.clean"))
    assert ["--no-owner", "--exit-on-error", "--dbname", scratch, str(target)] == calls[2][1]


def test_a_postgresql_backup_stops_when_no_scratch_database_can_be_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from django.db import DatabaseError

    class Cursor:
        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def execute(self, statement: str) -> None:
            raise DatabaseError("permission denied to create database")

    class Ops:
        quote_name = staticmethod(lambda name: f'"{name}"')

    class FakeConnection:
        vendor = "postgresql"
        settings_dict = {"NAME": "housecairn"}
        ops = Ops()

        def cursor(self) -> Cursor:
            return Cursor()

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "--data-only" in args:
            return subprocess.CompletedProcess(
                args, 0, COPY.format(real=uuid.uuid4(), example=uuid.uuid4()), ""
            )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(backup_instance, "connection", FakeConnection())
    monkeypatch.setattr(backup_instance.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(backup_instance.subprocess, "run", run)
    with pytest.raises(CommandError, match="cannot create one"):
        backup_instance.dump_database(tmp_path / "database")


@pytest.mark.django_db
def test_the_walk_of_the_media_goes_through_the_directories_every_app_declares(
    alice: User, media_root: Path
) -> None:
    """A folder an app declares later is left out too: the exclusion names no path of its own."""
    home = make_example(alice)
    files.register_media_folder("added-later")
    try:
        written = write_a_file_in_every_folder(home, media_root)
        assert any("added-later" in str(path) for path in written)
        assert Room.objects.filter(home=home).exists()
        assert [d.parent.name for d in files.home_directories(home.pk, media_root)] == list(
            files.MEDIA_FOLDERS
        )
    finally:
        files.MEDIA_FOLDERS.remove("added-later")
