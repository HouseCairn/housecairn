"""A consistent backup of the whole instance, in one file (docs/adr/0048).

The database is copied while the application runs: with SQLite's online backup API (as
`sqlite3 db.sqlite3 ".backup ..."`), or with `pg_dump` in its custom format for
PostgreSQL. The files of `MEDIA_ROOT` go with it, and `manifest.json` gives the SHA-256
digest of each part, which `restore_instance` checks. The archive holds every home: keep it
as safe as the server.

The secret key of `<data>/secret_key` is left out unless `--include-secret-key` asks for
it (docs/adr/0060): the stored secrets (SMTP password, secrets of sign-in providers,
addresses of notification channels) are encrypted with a key derived from it, so an archive
that held both would give them all to whoever got a copy. Keep the key apart.
"""

import contextlib
import datetime
import hashlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from collections.abc import Iterator
from importlib import metadata
from pathlib import Path
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import DatabaseError, connection, connections, transaction
from django.db.models import sql
from django.db.models.deletion import Collector

from housecairn.homes import files as home_files

FORMAT = "housecairn-instance-backup"
VERSION = 1


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def postgres_environment() -> dict[str, str]:
    """The connection of `pg_dump` and `pg_restore`, from the settings, password included."""
    db = settings.DATABASES["default"]
    env = dict(os.environ)
    for key, name in (
        ("HOST", "PGHOST"),
        ("PORT", "PGPORT"),
        ("USER", "PGUSER"),
        ("PASSWORD", "PGPASSWORD"),
        ("NAME", "PGDATABASE"),
    ):
        if db.get(key):
            env[name] = str(db[key])
    return env


@contextlib.contextmanager
def copy_connection(database: Path | str) -> Iterator[str]:
    """A connection alias on a copy of the database, never on the live one.

    SQLite: the file of the copy. PostgreSQL: the name of a scratch database. The settings are
    those of the instance, without the options of its own connection (the copy is not opened
    in WAL mode).
    """
    alias = f"backup_copy_{secrets.token_hex(4)}"
    live = connections["default"]
    settings_dict = dict(live.settings_dict)
    settings_dict["NAME"] = str(database)
    if connection.vendor == "sqlite":
        settings_dict["OPTIONS"] = {}
    # Set on the handler of the connections, not added to the settings: a connection of the
    # moment, which no other code of the program can reach by name.
    wrapper = type(live)(settings_dict, alias)
    connections[alias] = wrapper
    try:
        yield alias
    finally:
        wrapper.close()
        del connections[alias]


def remove_example_homes(alias: str) -> list[UUID]:
    """Remove the example homes of the database of `alias`, and what hangs off them.

    The rows go by the deletion the application runs for a home, without its signals (they act
    on the live database): the home, every row of a table that hangs off it, soft-deleted ones
    included, the change log, and the entries of its audit log, whose `home_id` is no foreign
    key. Returns the ids of the homes removed, so that their files are left out too.
    """
    from housecairn.audit.models import AuditEvent
    from housecairn.homes.models import Home

    database = connections[alias]
    with database.cursor() as cursor:
        columns = {
            c.name for c in database.introspection.get_table_description(cursor, "homes_home")
        }
    if "is_example" not in columns:
        return []  # a database of before the guide: it cannot hold the mark
    homes = list(Home.all_objects.using(alias).filter(is_example=True))
    if not homes:
        return []
    ids = [home.pk for home in homes]
    with transaction.atomic(using=alias):
        collector = Collector(using=alias)
        collector.collect(homes)
        collector.sort()
        for fast in collector.fast_deletes:
            fast._raw_delete(using=alias)
        for model, instances in collector.data.items():
            sql.DeleteQuery(model).delete_batch([instance.pk for instance in instances], alias)
        AuditEvent.objects.using(alias).filter(home_id__in=ids)._raw_delete(using=alias)
    return ids


def clean_sqlite_copy(path: Path) -> list[UUID]:
    """Take the example homes out of a copy of the database in a SQLite file.

    Then the copy is written whole in its own file again: no WAL left aside, and no free page
    that still holds a title of the removed rows.
    """
    with copy_connection(path) as alias:
        ids = remove_example_homes(alias)
    if ids:
        raw = sqlite3.connect(path)
        try:
            raw.execute("PRAGMA journal_mode=DELETE")
            raw.execute("VACUUM")
        finally:
            raw.close()
    return ids


def _pg_run(
    program: str, arguments: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed arguments, the connection from the environment
        [program, *arguments], env=env, capture_output=True, text=True, check=False
    )


def example_homes_in_pg_dump(dump: Path) -> list[UUID]:
    """The example homes a PostgreSQL dump holds, read from the `homes_home` rows of the dump."""
    program = shutil.which("pg_restore")
    if program is None:
        raise CommandError("pg_restore is not installed in this container.")
    result = _pg_run(
        program,
        ["--data-only", "--table=homes_home", "--file=-", str(dump)],
        postgres_environment(),
    )
    if result.returncode != 0:
        raise CommandError(f"pg_restore failed: {result.stderr.strip()[:500]}")
    return example_homes_in_copy_text(result.stdout)


def example_homes_in_copy_text(text: str) -> list[UUID]:
    """The ids of the rows of `homes_home` marked as example in the `COPY` block of a dump."""
    columns: list[str] | None = None
    found: list[UUID] = []
    for line in text.splitlines():
        if line.startswith("COPY "):
            names = line[line.index("(") + 1 : line.index(")")]
            columns = [name.strip().strip('"') for name in names.split(",")]
        elif line == "\\.":
            columns = None
        elif columns is not None and "is_example" in columns:
            cells = line.split("\t")
            if cells[columns.index("is_example")] == "t":
                found.append(UUID(cells[columns.index("id")]))
    return found


def clean_pg_dump(dump: Path, ids: list[UUID]) -> None:
    """Leave the example homes out of a PostgreSQL dump, through a scratch database.

    The dump is restored into a scratch database, the homes are removed there, and the
    scratch database is dumped again in its place. It needs the right to create a database:
    without it the backup stops, rather than keep a home that must never leave the instance.
    """
    restore = shutil.which("pg_restore")
    program = shutil.which("pg_dump")
    if restore is None or program is None:
        raise CommandError("pg_dump and pg_restore are not installed in this container.")
    quote = connection.ops.quote_name
    scratch = f"{connection.settings_dict['NAME']}_backup_{secrets.token_hex(4)}"
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE {quote(scratch)}")
    except DatabaseError as exc:
        raise CommandError(
            f"{len(ids)} example home(s) must be left out of the backup, which takes a scratch "
            "database, and the database user cannot create one. Delete the example home "
            f"(Guide > Delete the example home) or allow CREATEDB: {str(exc)[:200]}"
        ) from None
    env = {**postgres_environment(), "PGDATABASE": scratch}
    again = dump.with_name(dump.name + ".clean")
    try:
        result = _pg_run(
            restore, ["--no-owner", "--exit-on-error", "--dbname", scratch, str(dump)], env
        )
        if result.returncode != 0:
            raise CommandError(f"pg_restore failed: {result.stderr.strip()[:500]}")
        with copy_connection(scratch) as alias:
            remove_example_homes(alias)
        result = _pg_run(
            program, ["--format=custom", "--no-owner", f"--file={again}", scratch], env
        )
        if result.returncode != 0:
            raise CommandError(f"pg_dump failed: {result.stderr.strip()[:500]}")
        again.replace(dump)
    finally:
        again.unlink(missing_ok=True)
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {quote(scratch)}")


def dump_database(target: Path) -> tuple[str, list[UUID]]:
    """Write the database into `target`, example homes left out (docs/adr/0084).

    Returns its kind (`sqlite` or `postgresql`) and the ids of the example homes left out,
    whose files the walk of the media leaves out too. This and the walk of `MEDIA_ROOT` are the
    only places that copy data out of the instance. The exclusion is made on the copy, never on
    the live database.
    """
    vendor = connection.vendor
    if vendor == "sqlite":
        connection.ensure_connection()
        destination = sqlite3.connect(target)
        try:
            connection.connection.backup(destination)
        finally:
            destination.close()
        return "sqlite", clean_sqlite_copy(target)
    if vendor == "postgresql":
        program = shutil.which("pg_dump")
        if program is None:
            raise CommandError("pg_dump is not installed in this container.")
        result = subprocess.run(  # noqa: S603 - fixed arguments, the connection from the environment
            [program, "--format=custom", "--no-owner", f"--file={target}"],
            env=postgres_environment(),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            # pg_dump says why on stderr (a server newer than the client, a refused login).
            raise CommandError(f"pg_dump failed: {result.stderr.strip()[:500]}")
        ids = example_homes_in_pg_dump(target)
        if ids:
            clean_pg_dump(target, ids)
        return "postgresql", ids
    raise CommandError(f"Backups of a {vendor} database are not supported.")  # pragma: no cover


def _holds_the_key(key_file: Path) -> bool:
    """Whether the key in use is the one of `key_file`, not one set by SECRET_KEY."""
    try:
        held = key_file.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return bool(held) and secrets.compare_digest(held, settings.SECRET_KEY)


def _app_version() -> str:
    try:
        return metadata.version("housecairn")
    except metadata.PackageNotFoundError:  # pragma: no cover
        return "dev"


class Command(BaseCommand):
    help = "Back up the database and the uploaded files, in one archive (not the secret key)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--output",
            type=Path,
            default=None,
            help="directory of the archive (default: <data>/backups)",
        )
        parser.add_argument(
            "--include-secret-key",
            action="store_true",
            help=(
                "also put <data>/secret_key in the archive; whoever has the archive can then "
                "read the stored secrets"
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from housecairn.entrypoint import SECRET_KEY_FILE, data_dir

        output: Path = options["output"] or data_dir() / "backups"
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
        target = output / f"housecairn-{stamp}.tar"
        if target.exists():
            raise CommandError(f"{target} already exists; wait a second and run it again.")
        with tempfile.TemporaryDirectory(dir=output) as scratch:
            work = Path(scratch)
            kind, left_out = dump_database(work / "database")
            skipped = [d for home_id in left_out for d in home_files.home_directories(home_id)]
            parts = {"database": sha256(work / "database")}
            secret = data_dir() / SECRET_KEY_FILE
            media = Path(settings.MEDIA_ROOT)
            partial = target.with_suffix(".part")
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w") as archive:
                archive.add(work / "database", arcname="database")
                if options["include_secret_key"] and secret.is_file():
                    archive.add(secret, arcname="secret_key")
                    parts["secret_key"] = sha256(secret)
                files = 0
                if media.is_dir():
                    for path in sorted(media.rglob("*")):
                        if path.is_symlink() or not path.is_file():
                            continue
                        if any(path.is_relative_to(directory) for directory in skipped):
                            continue  # a file of an example home never leaves the instance
                        name = "media/" + path.relative_to(media).as_posix()
                        archive.add(path, arcname=name, recursive=False)
                        parts[name] = sha256(path)
                        files += 1
                manifest = {
                    "format": FORMAT,
                    "version": VERSION,
                    "database": kind,
                    "application_version": _app_version(),
                    "made_at": datetime.datetime.now(datetime.UTC).isoformat(),
                    "parts": parts,
                }
                data = json.dumps(manifest, indent=2).encode()
                info = tarfile.TarInfo("manifest.json")
                info.size = len(data)
                info.mode = 0o600
                archive.addfile(info, io.BytesIO(data))
            partial.replace(target)
        self.stdout.write(str(target))
        self.stderr.write(
            f"database ({kind}), {files} files" + (", secret key" if "secret_key" in parts else "")
        )
        if "secret_key" not in parts:
            # Where the key in use comes from: its file only if the file holds it.
            where = secret if _holds_the_key(secret) else "SECRET_KEY"
            self.stderr.write(
                f"The secret key is not in the archive: keep a copy of {where} apart. "
                "Without it, a restore cannot read the stored secrets."
                if not options["include_secret_key"]
                else f"No {secret}: the key comes from SECRET_KEY, keep a copy of it apart."
            )
