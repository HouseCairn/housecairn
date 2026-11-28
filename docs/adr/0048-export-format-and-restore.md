# 0048. Export format 2, restore of a home, tables as CSV and backup of the instance

- Status: accepted
- Date: 2026-11-10
- Amended by: [0058](0058-contract-provider-is-a-contractor.md) (format 3: the provider of a
  contract is a contractor; version 2 archives are still restored),
  [0060](0060-secret-key-apart-from-the-backups.md) (the backup of the instance leaves the
  secret key out),
  [0079](0079-lots-of-the-server-mode-of-the-mobile-app.md) (format 4: the archiving date of
  the home; versions 2 and 3 are still restored),
  [0093](0093-an-archive-of-before-takes-the-values-of-the-migrations.md) (a field added
  without a new version is optional and takes the value of its migration)

## Context

Each app exported its own section of `export.json` (format 1,
[ADR 0023](0023-audit-log-export-erasure-and-email-alerts.md)), written by hand. That
archive was for reading: nothing could put it back, its sections left fields out (the
start of a series, what an occurrence followed, who closed a report) and named people by
their address. The maintainer's own home is about to live on an instance for real (J4):
an owner must be able to move a home to another instance and get back every row and
every file, and an administrator must be able to back up and restore the instance as a
whole. Restoring is also the most dangerous input of the application: an archive is a
file anybody can forge.

## Decision

**Format 2: a manifest, one document per app, the files.** `manifest.json` gives the
format, its version, the version of the application, the date, the home, the list of
documents and the SHA-256 digest and size of every other entry. `data/<app>.json` holds
the sections of one app; `files/<folder>/<id>.<extension>` the files. The JSON Schema is
generated from the registry (`manage.py export_schema`) and published with the
documentation; a test fails when the published file and the code disagree, or when a
full archive does not validate. Format 1 is no longer written, and is not restored: its
archives carried neither digests nor every field.

**A registry of tables, no list of fields.** Each app registers its tables as `Section`s
in its `ready()`. The export writes **every concrete field** of each live row, by its name
in the code; a section only says what to leave out (a secret, a link to what is not
exported), which file fields carry a file, and, rarely, a hook (the code of a revoked QR
label). So a field added later is exported and restored without touching the export, and
a later lot adds a table with one call. Two tests hold it: every table of the project is
in a section, in the list of what the archive leaves out on purpose, or not data of a home
(ADR 0023); and every key of a section leads to the home, to a person or to another
section, every file field has its specification.

**Closed under its references.** A row whose required parent is not exported (the history
of a deleted task) is left out, until nothing changes; an optional reference to a row not
exported is written `null`. The archive never names an object it does not hold.

**Identifiers kept, people by reference.** Rows keep their UUIDs, so that QR labels, links
and integrations still point at the same objects after a restore. A person is an id of the
`people` list of `data/homes.json`, the only place with addresses and display names.

**Restore as a new home, in four checks before any write.** The zip (names on an
allow-list, no link, no encryption, no duplicate, sizes read from the headers and capped
before anything is unpacked, each entry read no further than its header says), the
manifest (known format and version, every entry listed with the right digest and size,
and nothing unlisted), the objects (each field parsed by its type and run through the
validators and choices of its model; each reference to an object of the same archive;
no identifier already used on the instance), the files (decoded again, of the type their
row says, within the upload limit and the quota of the home). The digests detect damage
and casual edits, not a forger, who can recompute them: the reference and identifier
checks are what keep an archive from reaching into another home. Then one transaction
writes the home, its rows (timestamps kept), its links and its files; a file already
written is removed if anything fails. Files are kept byte for byte, so a restore gives
back the same files; thumbnails are made again. A conflict on the same instance is refused
with the reason: delete the original home first, or restore elsewhere.

**Nobody gets an account.** From the web (and the API, signed in only), the person who
restores owns the new home and is attached to what was theirs; every other person is left
unattributed and named in the result, to be invited again. Matching every person of the
archive to an existing account by address would let anyone attach a stranger to a home,
and learn which addresses have an account here. `manage.py restore_home --owner`, run by
the administrator of the instance who moves homes, does match every person by address,
with their role. The restored home does not become the current home.

**Large homes in the background.** Above 50 MB of files, the export is an `ExportJob`: the
worker writes the archive under `MEDIA_ROOT/exports/<home id>/` (erased with the home, not
counted in its quota), and an owner, signed in, downloads it by a POST at most 3 times
within 24 hours; then an hourly schedule of the worker removes it, with any partial
archive older than that. No link opens without signing in.

**Tables as CSV, for a spreadsheet.** The rooms, items, tasks, history, stock,
contractors and reports, one table per file, from the same sections: UTF-8 with a byte
order mark, people by display name, amounts with a point and the currency in a column.
A text that starts with `=`, `+`, `-`, `@`, a tab or a carriage return is written after a
`'` (OWASP's advice on CSV injection); numbers are left alone. Any member, and a token
with the `read` scope on the home, may download them; the archive stays an owner's,
signed in.

**Import of tables, all or nothing.** The rooms, items, tasks, stock and contractors come
back from CSV files with the columns of their export, one table per file, several tables in
one import (`homes.csv_import`). Every file is read as hostile:

- UTF-8 with or without a byte order mark, separated by commas, as the export writes them;
  nothing is guessed. A file in another encoding, or separated by semicolons (what a
  spreadsheet set to French saves), is refused with what to do. A NUL byte or broken
  quoting refuses the file.
- The header names each known column once, by its field name or its label in English or
  French; the mapping is automatic and shown in the preview, with the columns ignored. The
  column that names a row (`name`, `title`) is required.
- Every cell is parsed by the type of its field, and the `'` the export puts before a
  formula is taken off again.
- Rows are always new, with a new identifier: an import never updates a row. The `id`
  column only lets the other files of the same import name a row; a row whose `id` is
  already in this home is refused as a duplicate, never merged.
- A reference is the `id` of a row of this home or of the same import, or the exact name
  of a row of this home; an identifier or a name of another home is refused. A person is
  the display name of a member, else the row is left unassigned and flagged.

The preview is the import itself: every row goes through the service of its app, with the
checks of its forms, inside a transaction that is rolled back. The confirmation runs it
again on the same text and commits it, in one transaction, with one `home.imported` entry
in the audit log. **A single row refused, and nothing is written**: the screen offers no
confirmation and the API answers `confirm=true` with 400 and the reasons. Warnings (a name
already used, a person left unassigned) do not stop it. Owners and members may import, and
a token with the `write` scope on the home; the right is checked before any file is read.

**At most 1,000 rows and 2 MB for the files of one import.** Each row costs about 7 ms
through its service and runs twice, preview and confirmation (1,000 rooms of 1.9 MB,
previewed then confirmed, in 13.5 s on SQLite in the tests); 1,000 rows keep each request
well within gunicorn's 30-second timeout.
2 MB leaves room for 1,000 rows with long notes.

**The text waits on the server between the preview and the confirmation.** Sent back
through hidden fields, a file inside the cap could weigh three times as much once
URL-encoded (each accented letter) and pass Django's limit on form fields
(`DATA_UPLOAD_MAX_MEMORY_SIZE`, 2.5 MB). So the preview of the screen keeps the text it
checked in an `ImportPreview` row, for one person and one home, and the page carries only
a token signed with the secret key that names it and the SHA-256 digest of the text. The
confirmation is refused, with a message, when the token is not signed, is older than 30
minutes or was already used, is another person's or another home's, or when the text no
longer has its digest. The row is removed once confirmed, when the same person previews
again in the same home, and by the hourly schedule of the worker once expired; it goes with
the home and with the account. The API keeps nothing: each call sends the files (as files,
which that limit does not count), the confirmation included.

**Backup of the instance in one file.** `manage.py backup_instance` writes a tar of a
consistent copy of the database (SQLite's online backup API, or `pg_dump` in its custom
format), the uploaded files and the secret key, with a manifest of digests, mode 600.
`restore_instance` checks every part first, refuses links and names that leave their
directory, keeps what it replaces aside (`<name>.before-restore-<date>`), and can restore
elsewhere to check a backup.

## Consequences

- The archive of a home can be read by a person and restored by a machine; its sections
  follow the models, so a migration that renames a field changes the format and raises its
  version.
- A restore on the instance the home came from is refused while that home exists.
- Archives of format 1 cannot be restored.
- An import that is partly wrong writes nothing: the person corrects the file and
  imports it whole again; nothing is left half imported.
- The text of an import waits at most 30 minutes on the server, in the database, then is
  removed; a person never has more than one waiting per home.
- The library of task templates (ADR 0049) and the guide of the API are a second pull
  request of the same lot.

## Options not taken

- **Django's `dumpdata` and `loaddata`.** They carry every table of the instance, internal
  ids of content types, and no check: `loaddata` writes what the file says, in any home.
- **A format per app written by hand**, as in format 1, with a restorer per section: every
  field would have to be listed twice, and forgetting one loses data silently.
- **Signing the archive.** The key would have to leave the instance to restore elsewhere;
  the checks above do not depend on who made the archive.
- **Matching every person by address from the web**: it leaks which addresses have an
  account and attaches people who did not agree to it.
- **Importing the rows accepted and leaving the refused ones out**: the person would have
  to find which rows are missing and import them again without the others, or get
  duplicates; all or nothing leaves one thing to do, correct the file.
- **Updating a row by its `id`**: a file edited in a spreadsheet would overwrite the home
  without a trace of what it was; an import adds, and a duplicate is refused.
- **Guessing the encoding and the separator**, or choosing the columns by hand: a wrong
  guess imports wrong data that looks right; a clear refusal and an automatic mapping
  shown in the preview are safer and quicker.
- **Sending the text back through the browser** (hidden fields, a signed cookie): it does
  not hold the 2 MB cap once encoded, and a cookie holds 4 KB.
- **The cache or the session** for the text between the two steps: the default cache is
  in the memory of each worker, which the confirmation may not reach, and the session is
  read on every request.
