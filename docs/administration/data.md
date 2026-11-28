# Export and deletion

The data of a home belongs to its owners, who can take it out and delete it;
the data of a person is erased with their account
([ADR 0023](../adr/0023-audit-log-export-erasure-and-email-alerts.md)). The
administrator of the instance does neither for a home they do not own.

## Export of a home

An owner downloads the archive from the page of the home (**Export all
data**) or from the API (`POST /api/v1/homes/<id>/export/`, signed in; an API
token cannot). The export is written to the audit log of the home
([ADR 0048](../adr/0048-export-format-and-restore.md)).

When the files of the home take more than 50 MB, the archive is made in the
background by the worker: the page of the export says when it is ready, and the
API answers `202` with the job (`GET /api/v1/homes/<id>/export/<job id>/`).
The archive is then downloaded by an owner, signed in, at most 3 times and
within 24 hours (`POST .../download/`, never a link that opens without
signing in). After that, the worker removes it within the hour, with any
partial archive left by an export the worker could not finish.

The archive is a zip:

| Part | Content |
| --- | --- |
| `manifest.json` | `format` (`"housecairn-export"`), `version` (`4`), `application_version`, `exported_at`, `home` (its id), `apps` (the documents below) and `entries`: the SHA-256 digest and the size of every other file of the archive. |
| `data/<app>.json` | The data of one part of the application: `{"app": ..., "sections": {<section>: [objects]}}`. |
| `files/<folder>/<id>.<extension>` | The files: `photos/`, `attachments/` (files of tasks), `stock/` (labels of stock items), `projects/` (photos, link pictures, quotes) and `reports/`. |
| `README.txt` | A reminder of the format. |

Its [JSON Schema](../_static/schemas/housecairn-export-v4.schema.json) is
published with this documentation (the ones of [version 3](../_static/schemas/housecairn-export-v3.schema.json) and [version 2](../_static/schemas/housecairn-export-v2.schema.json) stay). The rules that hold for every section:

- **Every field of the table**, by its name in the code: an object of
  `rooms` has `id`, `name`, `kind`, `position`, `notes`, `paint_colour`, ...,
  `created_at` and `updated_at`. A field added by a later version is in the
  archive without a change of format; a field removed or renamed raises the
  version. A field added without a change of format is **optional**, at restore
  and in the JSON Schema: the archives of that version made before it lack it
  (see [Archives of earlier releases](#archives-of-earlier-releases)). Every
  other field is required: a restore refuses an object that lacks one.
- **Identifiers are the UUIDs** of the objects, never an internal number. A
  reference is the id of an object of the same archive, or `null`; a link to
  several objects (the rooms of a project) is a list of ids.
- **People** are named by an id of the `people` list of `data/homes.json`,
  which alone holds their e-mail address and display name; the restore uses
  the address to find their account.
- **Amounts and quantities** are text with a point (`"1250.50"`), never a
  number of JSON; amounts are in the currency of the home (`home.currency`).
  Dates are ISO 8601.
- **Files** are a path of the archive, or `null` when the file is missing on
  the disk. Photos are exported as stored, already stripped of their metadata
  at upload; documents as received. Thumbnails are not exported: a restore makes
  them again.
- **Only what is not deleted**, and nothing whose parent is: the history of a
  deleted task is not exported.

| Document | Sections |
| --- | --- |
| `homes` | `home` (id, name, currency, hemisphere, sign-in requirement, `archived_at`, dates), `people`, `members` (person and role) and `pending_invitations` (address, role, dates; never the link). |
| `events` | `events` received, without the token they came with. |
| `rooms` | `rooms` (with the room memory), `items`, `photos`. |
| `stock` | `stock_items`, `stock_suppliers`, `stock_movements`. The quantity in stock is the sum of the movements. |
| `tasks` | `contractors`, `tasks`, `task_materials`, `occurrences` (the history: done, skipped, planned), `stock_draws`, `attachments`. |
| `projects` | `projects`, `project_budget_lines`, `project_quotes`, `project_files`, `project_links`, `project_tasks`, `project_envelopes`. What a line has committed is the sum of its accepted quotes. |
| `calendar` | `calendar_feeds`: that a feed exists and when; never its address or the digest of its secret. |
| `labels` | `labels`: the `code` an active QR label carries, so that the stickers still open the right page after a restore; `null` for a revoked one. |
| `reports` | `reports`, with their photo. |

The archive never holds a secret: no API token (not even its digest), no link
of an invitation or of a calendar feed, no address of a notification channel,
no password, no setting or single sign-on secret of the instance, nothing of
the first setup. Each app adds its tables to the registry of the export
(`homes.exports.register`); a test fails on a table that is in no section.

### Archives of earlier releases

| Version | Made by | Restored |
| --- | --- | --- |
| 1 | before 0.1.0 | No: export the home again. |
| 2 | 0.1.0 | Yes |
| 3 | 0.2.0 to 0.3.2 | Yes |
| 4 | 0.4.0 and later | Yes |

Version 1 archives (a single `export.json`) cannot be restored. Version 2 archives
are restored: the provider of the maintenance contract of an item was a text
(`contract_provider`) and is, in version 3, the id of a contractor
(`contract_contractor`). The restore links the item to the contractor of the archive
of that name (case and extra spaces ignored) and, when the archive has none, adds one,
with the trade Other ([ADR 0058](../adr/0058-contract-provider-is-a-contractor.md)); an
item with no provider has no contractor.

Version 4 adds `archived_at` to the `home` of `data/homes.json`, the date and time an
owner archived the home or `null`
([ADR 0079](../adr/0079-lots-of-the-server-mode-of-the-mobile-app.md)). A version 3 or 2
archive restores a home that is not archived; a version 4 archive of an archived home
restores it archived, as a copy too.

The fields added since, without a change of format, are missing from the archives
made before them. A restore gives each the value its migration gives the rows of an
instance that is upgraded, so that a restored archive holds what that instance would
([ADR 0093](../adr/0093-an-archive-of-before-takes-the-values-of-the-migrations.md)):

| Section | Field | Missing from | Value when missing |
| --- | --- | --- | --- |
| `stock_items` | `unit_price` | versions 2 and 3 made before 0.3.0 | `null` |
| `items` | `ean`, `warranty_provider`, `warranty_provider_name` | versions 2 and 3, and 4 made before 0.4.0 | empty |
| `items` | `warranty_duration_unit` | the same | `"year"` |
| `items` | `purchased_on`, `warranty_duration`, `warranty_starts_on` | the same | `null` |
| `photos` | `attribution` | the same | `{}` |
| `stock_items` | `ean`; `label_attribution` | the same | empty; `{}` |
| `stock_draws` | `unit_price` | every version, made before 0.6.0 | the unit price of its stock item, or `null` |
| `occurrences` | `planned_labour`, `planned_material` | the same | a done occurrence: the labour budget of its task, and its material budget unless a stock item with a unit price works the materials out; `null` for a task of a project, or an occurrence that is not done |

The last two are the approximation of the upgrade to 0.6.0, said in its release notes:
the price of today and the budget of today, nothing having kept those of the day.
`tests/test_restore_versions.py` restores a real archive of every release on an empty
instance (`scripts/archive_fixtures.py` makes them, from the tree of each tag).

## Tables for a spreadsheet

Every member downloads a table of the home as CSV from the page of the home
(**Tables for a spreadsheet**), or from the API
(`GET /api/v1/homes/<id>/export/<table>.csv/`, a token with the `read` scope
on the home included): `rooms`, `items`, `tasks`, `history`, `stock`,
`contractors`, `reports`. The file is UTF-8 with a byte order mark, which
Excel needs to read accents; the columns are the fields of the archive, people
are named by their display name and never by their address, amounts are numbers
with a point and the currency is a column of its own. `history` adds two columns after the
fields: `stock_cost`, the stock each closing took at its unit prices of that day, and
`real_cost`, that plus `actual_cost`, the amount typed, or with nothing typed the plan kept
when it was done, `planned_labour` plus `planned_material` (empty when none says anything;
[ADR 0090](../adr/0090-real-cost-counts-the-stock-used.md),
[ADR 0091](../adr/0091-a-task-done-with-nothing-typed-pays-its-plan.md)). A text that a spreadsheet
would run as a formula (starting with `=`, `+`, `-`, `@`, a tab or a carriage
return) is written after a `'`, so that it stays text.

Every CSV of HouseCairn, exported or imported, is separated by **commas**,
whatever the language.

## Opening a table in a spreadsheet

A spreadsheet that opens a CSV file by a double click guesses its separator,
its encoding and the type of each column from the settings of the computer. On
a French system it expects semicolons and puts every row in one column; it also
turns dates and identifiers into numbers. Open the file by the import of the
spreadsheet instead, and save it the same way before an import.

**Microsoft Excel, in French:**

1. In a new workbook, **Données › À partir d'un fichier texte/CSV** (*Data ›
   From Text/CSV*), then choose the file.
2. **Origine du fichier**: *65001 : Unicode (UTF-8)*; **Délimiteur**:
   *Virgule*; **Détection du type de données**: *Ne pas détecter les types de
   données*, so that dates stay `YYYY-MM-DD` and identifiers stay text.
   Then **Charger**.
3. To save it for an import: **Fichier › Enregistrer sous**, type **CSV UTF-8
   (délimité par des virgules)**. Excel writes the *list separator* of
   Windows, a semicolon on a French system, despite the name of the type: set
   it to a comma first (Windows settings, **Heure et langue › Région ›
   Paramètres régionaux supplémentaires › Séparateur de liste**), or save the
   file with LibreOffice. An import refuses a file separated by semicolons and
   says so.

**LibreOffice Calc:**

1. **Fichier › Ouvrir** (*File › Open*), then choose the file. In **Import de
   texte**: **Jeu de caractères** *Unicode (UTF-8)*, **Séparé par** *Virgule*
   only; select every column and give it the type *Texte* to keep dates and
   identifiers as they are.
2. To save it: **Fichier › Enregistrer sous**, type **Texte CSV (.csv)**,
   tick **Éditer les paramètres du filtre**, then **Utiliser le format Texte
   CSV**: **Jeu de caractères** *Unicode (UTF-8)*, **Séparateur de champ**
   `,`, **Séparateur de chaînes de caractères** `"`.

The `'` before a text that starts with `=`, `+`, `-` or `@` stays visible in
the cell; leave it there, the import takes it off.

## Import of tables

Owners and members add rows to a home from CSV files: on the page of the home,
**Tables for a spreadsheet › Import tables** ([the screen](../screens/home-import.md)),
or `POST /api/v1/homes/<id>/import/` (a token with the `write` scope on the
home included). The tables are those of the exports, with the same columns:
`rooms`, `items`, `tasks`, `stock` and `contractors`; a table exported, changed
in a spreadsheet and saved again comes back, one file per table, several tables
in one import.

- **Nothing is written before the confirmation.** The preview runs the import
  and rolls it back: it shows each row accepted or refused, with the reason,
  and is what the confirmation then writes, in **one transaction**, through
  the same checks as the forms. The API previews unless it is sent
  `confirm=true`.
- **All or nothing.** A single row refused, and nothing is imported: the page
  offers no confirmation, and the API answers `confirm=true` with 400 and the
  reason of each row. Correct the file and import it again, whole. A warning (a
  name already used, a person left unassigned) does not stop the import.
- **Every file is checked whole first**: UTF-8 with or without a byte order
  mark, separated by commas, a header naming each known column once (by its
  name in the export, or its label in English or in French), and the column
  that names a row (`name`, or `title` for tasks).
- **Limits**: 1,000 rows and 2 MB for the files of one import, whatever their
  language; import a larger set in several times. Each row goes through the
  checks of the forms, twice (preview and confirmation): 1,000 rows keep each
  request within seconds, far from the 30 seconds after which the web server
  stops it. Files past a limit are refused whole, with the reason.
- **Between the preview and the confirmation**, the page keeps the text of the
  files on the server, in the database, for the person who previewed it and
  this home only, for **30 minutes**: the confirmation sends back only a signed
  token, so the limits hold up to it. The text is removed once confirmed, when
  the same person previews again in the same home, and within the hour after
  it expires; a confirmation after that asks for the files again. The API
  keeps nothing: each call sends the files, `confirm=true` included.
- **A row is always new.** It gets a new identifier; its `id` only lets the
  other files of the import refer to it. A row whose `id` is a row already in
  this home is refused as a duplicate, never merged; a name already used is
  accepted and flagged.
- **References stay in the home**: the `id` of a row of this home or of the
  same import, or the exact name of a row of this home. An identifier or a name
  of another home is refused. People are named by the display name of a member
  of the home; otherwise the row is left unassigned, and flagged.
- `created_at`, `updated_at`, the position of a room and the type of a stock
  label are made again by the import; a task is due on its `series_start`.
- The import is recorded in the audit log of the home, with the number of rows.

## Restore of a home

**Homes > Restore a home** (or `POST /api/v1/homes/restore/`, signed in; an
API token cannot) makes the archive a new home, owned by the person who
restores it. It does not become the current home. The identifiers are kept, so
that QR labels and links still lead to the same objects. The archive holds no
API token: create new ones after a restore, including the one of the Home
Assistant integration, which then asks for it again.

The archive is checked before anything is written, and refused with the
reason when it is not one HouseCairn made, or was changed since:

- only the names the format allows, no symbolic link, no encrypted file, no
  name that leaves the archive;
- a size, once unpacked, read from the zip before unpacking, no larger in all
  than the file quota of a home plus 64 MB (room for the data files); no file
  larger than the largest upload;
- a format and a version this instance knows, and every file present with the
  digest and the size its manifest gives;
- every value accepted by its field (a choice of the list, a valid date, a text
  no longer than allowed), every reference to an object the archive holds,
  never to an object of another home;
- no object already on this instance: the home it was made from must be
  deleted first, or the archive restored on another instance, or restored as a
  copy ([below](#restore-as-a-copy));
- every file decoded again, of the type its object says.

People are never given an account. From the web, the person restoring is
attached to what was theirs; the others are listed, left unattributed, and can
be invited again. For an instance that moves, the administrator runs:

```sh
docker compose exec housecairn python -m housecairn.entrypoint manage \
  restore_home /data/backups/housecairn-home.zip --owner owner@example.com
```

which attaches again every person of the archive who has an account here with
the same address, with their role.

### Restore as a copy

**Restore as a copy** on the same page (or `as_copy=true` on
`POST /api/v1/homes/restore/`, or `--copy` on `restore_home`) makes a new home
next to the one the archive was made from, which it does not touch: to
duplicate a home, or to check a backup without deleting the original
([ADR 0069](../adr/0069-restore-as-a-copy-with-new-identifiers.md)). Archives
of format 2 and 3 are accepted.

- The archive goes through every check of the restore above, and the copy is
  written in one transaction: if anything is refused, nothing is created.
- Every object (rooms, items, tasks and their history, stock, projects, quotes,
  budget lines, contractors, photos and files, links, labels, reports, events)
  gets a new identifier, and every link between them follows: the copy shares no
  row and no file with the original. Each file is stored again, under the copy.
- QR labels get new codes: the stickers of the original still open the
  original.
- The person who restores owns the copy and is its only member. The members and
  the invitations of the archive are not copied; anyone else the archive names
  (an assignee, who did a task) is left unattributed.
- The copy is named after the home, followed by "(copy)" (then "(copy 2)"…),
  unless another name is given (`name`, or `--name`); it can be renamed later.
- While the home of the archive is on this instance, only one of its owners may
  copy it: anyone else is refused (403 from the page and the API). Once that home
  is gone, whoever holds the archive may restore it or copy it.

```sh
docker compose exec housecairn python -m housecairn.entrypoint manage \
  restore_home /data/backups/housecairn-home.zip --owner owner@example.com \
  --copy --name "Test home"
```

A home that required a sign-in provider still requires it once restored. On an
instance that does not declare that provider, it stays locked, for its owner
too, until the rule is lifted: the owner opens the locked page and chooses
**Remove the sign-in requirement**, or the administrator uses **Remove these
requirements** on the instance page
([Requiring single sign-on for a home](oidc.md#requiring-single-sign-on-for-a-home)).

## Deletion of a home

An owner deletes the home from its page (**Delete this home**): type its exact
name. The home and everything in it go for good, for all its members: members,
invitations, events, calendar feeds, its audit log and its files; an API token left without a
home is revoked. **There is no grace period and no way back**: export first.
The audit log of the instance records that a home was deleted, without its
name. The instance also keeps the identifier of the home, the time it was deleted and the
identifiers of the connected apps that were linked to it, nothing else, so that the mobile app can tell that a home it knew is gone.

The photos, the labels of stock items, the photos, link pictures and quote files of projects, the photos of reports and the files attached to tasks are erased from the disk with the home
(photos deleted one by one included). If the disk refuses (permissions, a full or
read-only volume), the home is still deleted and the error is logged by
`housecairn.files` with the path of the directory (`photos/<home id>`,
`attachments/<home id>`, `stock/<home id>`, `projects/<home id>`, `reports/<home id>` or `exports/<home id>`, the archives made in the background): remove that directory by hand.

The budget screens (by room, by project, by task, the envelope) keep no data of their own:
they are worked out from the projects, tasks and envelopes above, so they are in the export
and go with the home.

## The example home

The guide makes an example home for a person who asks for it, so that they can try the
application without touching their own data ([ADR 0084](../adr/0084-interactive-guide-and-example-home.md)).
It is a home like another one, marked as an example, and it **never leaves the instance**: it is
not exported (the archive, the job that makes it, the tables for a spreadsheet, the CSV of the
budget pages and the contact file of a contractor refuse it), not in a backup of the instance
(see [Backup](backup.md)), not in the sync API or its snapshot, not in the list of homes or of
the homes of an API token, not in a calendar feed or in the QR labels, and it sends no reminder,
alert or event. It has no other member and cannot be shared. It is deleted in one gesture, with
its files, and with the account of the person who made it, which it never keeps from being erased.

## Erasure of an account

**Delete my account** (see [Account pages](../screens/account.md)) erases the
account, its sign-in methods, devices, API tokens and calendar feeds, the homes where it is
the only member (an example home is not counted: it goes with the account), and the invitations to its address. It is refused for the only
administrator and for the only owner of a home with other members: hand
ownership over, or delete the home, first. The audit log keeps that it happened
and no longer says who.

## Backups

Deleted data stays in backups until they expire: tell people how long you keep
them (see [Backup](backup.md)).
