# Data and backups

Everything HouseCairn writes is in the `/data` volume:

| Path | Content |
| --- | --- |
| `/data/db.sqlite3` (and `-wal`, `-shm`) | The SQLite database, unless `DATABASE_URL` points to PostgreSQL. |
| `/data/secret_key` | The key that signs sessions and encrypts the stored secrets, mode 600, generated when `SECRET_KEY` is not set. Losing it signs everyone out and makes the stored secrets unreadable; keep it secret, and apart from the backups (see [The secret key](#the-secret-key)). |
| `/data/media/` | Uploaded files. |
| `/data/backups/` | Copies made by the `backup_instance` and `backup` commands below. |

Files are owned by `10001:10001` and readable by that user only. If you mount a
host directory instead of a named volume, give it to that user first:
`sudo chown -R 10001:10001 /srv/housecairn`.

## Back up

With SQLite, the default, the simplest complete backup, while HouseCairn runs,
is one command ([ADR 0048](../adr/0048-export-format-and-restore.md)):

```sh
docker compose exec housecairn python -m housecairn.entrypoint manage backup_instance
# prints /data/backups/housecairn-<UTC date and time>.tar
```

The archive holds a consistent copy of the database (SQLite's online backup
API, the same as `sqlite3 db.sqlite3 ".backup ..."`), the uploaded files of
`/data/media/`, and `manifest.json` with the SHA-256 digest of each. It is
written with mode 600: it holds every home, keep it as safe as the server. Copy
it off the machine, and remove old copies from `/data/backups/` yourself.
`--output <directory>` writes it elsewhere.

**The secret key is not in the archive**, unless you add `--include-secret-key`.

**The example home is not in the archive** either: a person who follows the guide works in an
example home that never leaves the instance, so `backup_instance` removes it, with its rows, its
audit entries and its files in every folder of `/data/media/`, from the copy of the database
before it enters the archive (the live database is not touched). The command says how many it
left out. With PostgreSQL the same cleaning is done on a scratch database made from the dump
(the database role needs the right to create a database), and `backup_instance` stops with a
message if it cannot ([ADR 0084](../adr/0084-interactive-guide-and-example-home.md)).

### The secret key

The stored secrets (the SMTP password, the secrets of the sign-in providers added in the
interface, the addresses of the notification channels, the addresses and keys of the
devices that receive notifications, the private push key of the instance) are encrypted in
the database with
a key derived from the secret key. An archive that held the key as well would give them
all to whoever got a copy of it, so `backup_instance` leaves it out
([ADR 0060](../adr/0060-secret-key-apart-from-the-backups.md)). Keep the key apart:

- best, set `SECRET_KEY` in `deploy/production.env` (see
  [Secrets](settings.md#secrets)), and keep a copy of that file, or of the key, somewhere
  else than your backups, such as a password manager;
- if the key was generated in `/data/secret_key`, keep a copy of that file the same way.

`--include-secret-key` puts `/data/secret_key` in the archive all the same, for a copy
that must restore alone; it then gives the stored secrets to whoever has it. Without a
key file (the key comes from `SECRET_KEY`), there is nothing to include.

Restored without its key, an instance cannot read its push key either: it makes a new one
at the first notification on a device and removes the devices, which people add again
([ADR 0066](../adr/0066-notifications-on-the-device-by-web-push.md)).

### With PostgreSQL

With PostgreSQL, `backup_instance` calls `pg_dump`, and `restore_instance`
calls `pg_restore`. **The official image contains neither**: in it, both
commands stop with `pg_dump is not installed in this container` (or
`pg_restore`). Back up the two parts separately instead, with the `postgres`
service of `compose.yaml`, whose image has both programs and is of the same
version as the server:

```sh
# 1. The database, in pg_dump's custom format, written on the host.
docker compose --profile postgres exec -T postgres \
  pg_dump -U housecairn -d housecairn --format=custom > housecairn-db.dump

# 2. The uploaded files, from the volume, read only.
docker run --rm -v housecairn_housecairn-data:/data:ro -v "$PWD":/backup \
  debian:stable-slim tar -cf /backup/housecairn-files.tar -C /data media
```

Keep the secret key apart from these two files, as [above](#the-secret-key). Both
files hold every home; keep them as safe as the server.

These commands follow the `compose.yaml` of the repository (service, user,
database and volume names), but have not yet been tried on a real server: try
a restore on another machine before you rely on them.

Two other ways remain:

- **Cold copy**: stop the container, copy the volume, start it again. The copy
  holds `/data/secret_key` when the key was generated there: keep it as you would
  the key.

  ```sh
  docker compose stop housecairn
  docker run --rm -v housecairn_housecairn-data:/data:ro -v "$PWD":/backup \
    debian:stable-slim tar -czf /backup/housecairn-data.tar.gz -C /data .
  docker compose start housecairn
  ```

  (The volume name is `<project>_housecairn-data`; `docker volume ls` shows it.)

- **The database only**, with `python -m housecairn.entrypoint backup`
  (prints `/data/backups/db-<UTC date and time>.sqlite3`); then copy
  `/data/media/` as well, and keep the key apart.

The owners of a home can also keep their own copy of it: **Export all data**,
which a restore makes a new home again ([Export and deletion](data.md)).

## Restore

With SQLite, stop the application, then restore the archive of
`backup_instance`, and start the application again:

```sh
docker compose run --rm housecairn python -m housecairn.entrypoint manage \
  restore_instance /data/backups/housecairn-20261110T120000Z.tar --yes
```

A home that arrives marked as an example, in an archive written by another hand or by an older
version, is erased by the restore with its rows, audit entries and files, and the command says
so: an instance never holds an example home that it did not make itself.

Every part is checked against the manifest first; a changed backup, a name that
leaves its directory or a link is refused and nothing is replaced. The
database, `/data/media/` and the secret key that were there are not deleted:
they are kept next to it, renamed `<name>.before-restore-<date>`, until you
remove them. Without `--yes`, the command refuses to replace anything.

An archive without the secret key (the default) leaves `/data/secret_key` as it
is, and the command says so. Start the instance with the key it had when the
backup was made: in `SECRET_KEY`, or by putting its `secret_key` file back in
`/data`. With another key the homes, their files and the accounts are all there,
but everyone signs in again and the stored secrets cannot be read: the screens
say so, the SMTP password and the secrets of the sign-in providers are entered
again, and the notification channels are added again.

On a new data directory without `SECRET_KEY`, `manage` generates a key in
`/data/secret_key` before the restore runs. The command then says that this key
was generated just now and is not the one of the backup: replace it with the key
of the instance the backup comes from before you start the instance.

To check a backup without touching the instance, restore it elsewhere:

```sh
python -m housecairn.entrypoint manage restore_instance <backup>.tar \
  --database-file /tmp/check/db.sqlite3 --media-root /tmp/check/media \
  --data-dir /tmp/check
```

For a cold copy, stop the container, put the files back in the volume (owned
by `10001:10001`), and start it.

With PostgreSQL, restore the two parts of [With PostgreSQL](#with-postgresql),
the application stopped and the `postgres` service running:

```sh
docker compose stop housecairn
docker compose --profile postgres exec -T postgres \
  pg_restore -U housecairn -d housecairn --clean --if-exists --no-owner \
  --single-transaction < housecairn-db.dump
docker run --rm -v housecairn_housecairn-data:/data -v "$PWD":/backup \
  debian:stable-slim sh -c \
  'tar -xf /backup/housecairn-files.tar -C /data && chown -R 10001:10001 /data'
docker compose start housecairn
```

`--clean --if-exists` drops each table of the dump before it is recreated, so
the database is the one of the dump; `--single-transaction` leaves it as it
was if anything fails. Unlike `restore_instance`, the files are not checked
against a manifest and those already in `/data/media/` are overwritten, not
kept aside. Like the backup, this has not yet been tried on a real server.
