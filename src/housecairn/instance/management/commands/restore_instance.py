"""Put a backup of `backup_instance` back: database, uploaded files and, if it holds it,
the secret key.

Stop the application first (docs/administration/backup.md). Every part is checked against
the manifest before anything is replaced; a name that would leave its directory, a link
or a device is refused. What was there before is kept next to it, renamed
`<name>.before-restore-<date>`, never deleted. `--database-file`, `--media-root` and
`--data-dir` restore elsewhere, to check a backup without touching the instance.

A backup without the secret key (the default since docs/adr/0060) leaves the key of the
data directory as it is, and says what it means: the instance must start with the key it
had, or the secrets stored in the database cannot be read and are entered again.
"""

import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from housecairn.homes import files
from housecairn.instance.management.commands.backup_instance import (
    FORMAT,
    VERSION,
    clean_sqlite_copy,
    copy_connection,
    postgres_environment,
    remove_example_homes,
)

WITH_ANOTHER_KEY = (
    "With another key, the stored secrets cannot be read: enter the SMTP password and the "
    "secrets of the sign-in providers again, and add the notification channels again; everyone "
    "signs in again. The homes, their files and the accounts are restored either way."
)
KEY_LEFT_OUT = (
    "The backup holds no secret key: start the instance with the key it had when the backup "
    "was made, in SECRET_KEY (or in secret_key of the data directory). " + WITH_ANOTHER_KEY
)
# The data directory was new: the entrypoint generated its key just before this command.
KEY_JUST_GENERATED = (
    "The backup holds no secret key, and {key_file} was generated just now, for this new "
    "data directory: it is not the key of the instance the backup was made on. Before you "
    "start the instance, replace it with that key: its secret_key file in place of this one, "
    "or its value in SECRET_KEY. " + WITH_ANOTHER_KEY
)


def _generated_just_now(key_file: Path) -> bool:
    from housecairn.entrypoint import KEY_GENERATED

    generated = os.environ.get(KEY_GENERATED, "")
    return bool(generated) and Path(generated).resolve() == key_file.resolve()


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        not path.is_absolute() and ".." not in path.parts and "\\" not in name and bool(path.parts)
    )


def _set_aside(path: Path, stamp: str) -> Path | None:
    if not path.exists():
        return None
    aside = path.with_name(f"{path.name}.before-restore-{stamp}")
    path.rename(aside)
    return aside


class Command(BaseCommand):
    help = "Restore a backup made by backup_instance (stop the application first)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("backup", type=Path)
        parser.add_argument("--yes", action="store_true", help="replace what is there now")
        parser.add_argument("--database-file", type=Path, default=None)
        parser.add_argument("--media-root", type=Path, default=None)
        parser.add_argument("--data-dir", type=Path, default=None)

    def handle(self, *args: Any, **options: Any) -> None:
        from housecairn.entrypoint import SECRET_KEY_FILE, data_dir

        target_data: Path = options["data_dir"] or data_dir()
        media: Path = options["media_root"] or Path(settings.MEDIA_ROOT)
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
        try:
            archive = tarfile.open(options["backup"], mode="r:")  # noqa: SIM115
        except (OSError, tarfile.TarError) as exc:
            raise CommandError(f"Not a backup of HouseCairn: {exc}") from None
        with (
            archive,
            tempfile.TemporaryDirectory(
                dir=target_data if target_data.is_dir() else None
            ) as scratch,
        ):
            work = Path(scratch)
            members = archive.getmembers()
            for member in members:
                if not member.isfile() or not _safe_name(member.name):
                    raise CommandError(
                        f"The backup holds an entry that is refused: {member.name[:120]}"
                    )
            by_name = {m.name: m for m in members}
            if "manifest.json" not in by_name:
                raise CommandError("Not a backup of HouseCairn: no manifest.")
            handle = archive.extractfile(by_name["manifest.json"])
            assert handle is not None  # noqa: S101 - a regular file, checked above
            try:
                manifest = json.loads(handle.read())
            except ValueError:
                raise CommandError(
                    "Not a backup of HouseCairn: its manifest is not JSON."
                ) from None
            if manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
                raise CommandError(
                    "Not a backup of HouseCairn, or of a version this one cannot restore."
                )
            parts: dict[str, str] = manifest.get("parts", {})
            if set(parts) != set(by_name) - {"manifest.json"} or "database" not in parts:
                raise CommandError(
                    "The backup was changed: its files are not those of its manifest."
                )
            for name, expected in parts.items():
                source = archive.extractfile(by_name[name])
                assert source is not None  # noqa: S101
                destination = work / "parts" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with destination.open("wb") as out:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        out.write(chunk)
                if digest.hexdigest() != expected:
                    raise CommandError(f"The backup was changed: {name}.")
            database = work / "parts" / "database"
            # A home that arrives marked as an example is erased (docs/adr/0084): the backup
            # of an instance never holds one, whatever a file carries.
            left_out: list[UUID] = []
            if manifest.get("database") == "sqlite":
                target = options["database_file"] or Path(
                    str(settings.DATABASES["default"]["NAME"])
                )
                on_sqlite = settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
                if str(target) == ":memory:" or (not on_sqlite and not options["database_file"]):
                    raise CommandError(
                        "This instance does not use a SQLite file: give --database-file."
                    )
                if target.exists() and not options["yes"]:
                    raise CommandError(
                        f"{target} exists: add --yes to replace it (it is kept aside)."
                    )
                left_out = clean_sqlite_copy(database)
                target.parent.mkdir(parents=True, exist_ok=True)
                for suffix in ("", "-wal", "-shm"):
                    _set_aside(Path(f"{target}{suffix}"), stamp)
                source_db = sqlite3.connect(database)
                destination_db = sqlite3.connect(target)
                try:
                    source_db.backup(destination_db)
                finally:
                    source_db.close()
                    destination_db.close()
            elif manifest.get("database") == "postgresql":
                if not options["yes"]:
                    raise CommandError("Restoring PostgreSQL replaces the database: add --yes.")
                program = shutil.which("pg_restore")
                if program is None:
                    raise CommandError("pg_restore is not installed in this container.")
                result = subprocess.run(  # noqa: S603 - fixed arguments
                    [
                        program,
                        "--clean",
                        "--if-exists",
                        "--no-owner",
                        "--dbname",
                        str(settings.DATABASES["default"]["NAME"]),
                        str(database),
                    ],
                    env=postgres_environment(),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    raise CommandError(f"pg_restore failed: {result.stderr.strip()[:500]}")
                with copy_connection(str(settings.DATABASES["default"]["NAME"])) as alias:
                    left_out = remove_example_homes(alias)
            else:
                raise CommandError("The backup names a database this instance does not know.")
            restored_media = work / "parts" / "media"
            for home_id in left_out:
                # Every folder an app declares, never a path written here.
                for directory in files.home_directories(home_id, restored_media):
                    shutil.rmtree(directory, ignore_errors=True)
            if media.exists() and any(media.iterdir()) and not options["yes"]:
                raise CommandError(
                    f"{media} is not empty: add --yes to replace it (it is kept aside)."
                )
            _set_aside(media, stamp)
            if restored_media.is_dir():
                shutil.move(str(restored_media), str(media))
            else:
                media.mkdir(parents=True, exist_ok=True)
            secret = work / "parts" / "secret_key"
            if secret.is_file():
                target_data.mkdir(parents=True, exist_ok=True)
                key_file = target_data / SECRET_KEY_FILE
                _set_aside(key_file, stamp)
                shutil.move(str(secret), str(key_file))
                key_file.chmod(0o600)
        files_count = sum(1 for name in parts if name.startswith("media/"))
        key = "secret key, " if "secret_key" in parts else ""
        self.stdout.write(f"Restored: database, {key}{files_count} files.")
        if left_out:
            self.stdout.write(f"{len(left_out)} example home(s) in the backup were left out.")
        if "secret_key" not in parts:
            key_file = target_data / SECRET_KEY_FILE
            self.stdout.write(
                KEY_JUST_GENERATED.format(key_file=key_file)
                if _generated_just_now(key_file)
                else KEY_LEFT_OUT
            )
