# Data model of the synchronization

The mobile app can keep its homes on a HouseCairn server and exchange changes with it
([ADR 0077](../adr/0077-sync-api-for-the-mobile-app.md), cut into lots by
[ADR 0079](../adr/0079-lots-of-the-server-mode-of-the-mobile-app.md)). This page describes
the `sync` application, which keeps the log of those changes. It has no screen, no route and
no API resource yet: the routes arrive with the lot that reads the feed, and each application
starts to record its writes in its own lot.

## What is recorded

Every write of a row of a home is a **change**: the row (its entity type and its id), the
fields written with their new values, the clock and the device that wrote them. Changes of
one home are numbered by a **position** (`pos`), 1, 2, 3... with no gap and no repeat, and a
phone reads the feed from the last position it has seen.

| Table | Holds |
| --- | --- |
| `sync_homehead` | One row per home: the last position, the last clock, and the `floor`, the highest position the purge removed. |
| `sync_change` | The feed: position, entity type (`rooms.room`), row id, kind (`put`, `delete`, `restore`), clock, device, fields written. |
| `sync_rowclocks` | One row per row of a home: the clock and device of each field, the fields the server has no column for (`extra`), and when the row was deleted. |
| `sync_erasedhome` | The homes that were erased, with the connected apps that were bound to them. Outside the home. |
| `sync_serverdevice` | The device id of the server: one row. |

All but the last two follow the home: erasing a home erases its changes with it.

## Recording a change

Code that writes a row of a home calls `housecairn.sync.changes.record_change` in the
transaction that writes the row:

```python
changes.record_change(home, "rooms.room", room.pk, changes.PUT, {"name": "Hall"})
```

It locks the row of the home until the commit, so the order of the positions is the order of
the commits (SQLite writes one transaction at a time; PostgreSQL waits on the lock). Pass
only the fields whose value changed: a form that sends every field must not give a clock to a
field nobody touched. A deletion is `changes.DELETE`, a restoration `changes.RESTORE`; both
write the field `deleted_at`.

Services do not call it by hand. They write through `housecairn.sync.rows`, which reads the row
as the database holds it, writes, and records the difference, so that only the fields whose
value changed are sent:

| Function | For |
| --- | --- |
| `rows.save(instance, **kwargs)` | `instance.save()`: a new row records every field, an existing one the fields that changed. With `update_fields`, only those fields are compared. |
| `rows.soft_delete(instance)` | A soft deletion of one row, with its tombstone. A row already deleted records nothing. Given a queryset, the same as `rows.soft_delete_queryset`. |
| `rows.soft_delete_queryset(rows)` | A soft deletion of a queryset: one tombstone per row it deleted. |
| `rows.restore(instance)` | `instance.restore()`, with its restoration. A row that is not deleted records nothing. |
| `rows.update_queryset(rows, **values)` | A queryset update with plain values: one change per row and field that changed. |
| `rows.record_created(instances)` | Rows inserted in bulk (restoring an archive). |
| `rows.before(instance)`, then `rows.record_saved(instance, was)` | Read before a write, then recorded after it: the two halves of `rows.save`, for a write it cannot wrap. |

What a row sends is its concrete fields under their column names (`room_id`), without `id`,
`home` (the home keys the change), `created_at`, `updated_at` and `deleted_at`, nor the derived
fields a model lists in `SYNC_DERIVED` (`tasks.occurrence.visible_from`): they are recomputed on
every side and never synced (mobile ADR 0003). Dates are `YYYY-MM-DD`, amounts decimal strings
with the places of their field (`12.50`). An application adjusts that for a model with
`rows.register(model, rows.Spec(...))` in its `ready()`: columns left out (`homes.home` does not
send `required_provider`, a rule of the instance), fields without a column (a photo sends its
file as `image`, a reference with the size, type and dimensions of the stored picture, and no
thumbnail), and fields that read as the clock of their own write (`homes.home.archived_at`: an
archive made on the web is the clock of that write, and an empty value stays empty).

A write made on the web gets a clock from the server's hybrid clock and the device id of the
server. A write that comes from a phone passes its own `stamp` (clock and device id).

## Clocks and conflicts

A clock is a 64-bit number, `(milliseconds since 1970-01-01 UTC << 16) | counter`, written as 16
lower-case hexadecimal digits. A device id is 16 bytes, written in base64url without padding.
Each field of a row keeps the clock and the device of its last write. A write replaces a field
only if its clock is greater, and for equal clocks if its device id is greater, comparing the
bytes: **the last writer wins, field by field**, so two people who edit two fields of one row
both keep their change. The same stamp twice is a repeat and changes nothing. When every field
of a write loses, no change and no position are recorded, and the result says which stamp won.

The server's clock never goes back when the wall clock does: it is the greater of the last
clock plus one and the wall clock. It is kept per home, under the lock that numbers the
positions. A clock read from a phone is adopted unless it is more than 24 hours ahead of the
wall clock, so that a phone with a wrong date cannot push the others into the future. When the
16-bit counter overflows, it carries into the milliseconds.

A known field that has no clock yet reads as the clock of the row's `updated_at`, with the
device id of the server: rows written before the change log exists are not migrated.

The vectors of `tests/fixtures/sync_clock.json` pin these rules (format, order, the two update
rules, malformed inputs). The mobile app replays the same file, so a change to a rule is a
change to the file in both repositories.

## Fields the server does not know

A field with no column goes to `extra`, a JSON value per row with, for each field, its value,
clock and device. It merges by the same rule and is given back by the feed, so a second phone
gets the field a newer phone wrote. When a later version gains the column, its migration
moves the value out of `extra`.

## Erased homes

Erasing a home writes one row to `sync_erasedhome` in the same transaction, with the ids of the
connected apps bound to the home. That row, not the home, tells a phone that the home is gone.
It is kept: it is a few dozen bytes, and the 400 days of retention below do not apply to it.

Restoring an archive (as a copy or not) records the new home and every row it inserts, whatever
their application, with every field: a phone that reads the feed from position 0 gets them all.
An archive whose `updated_at` of the home or of a row is more than 24 hours ahead of the clock of
the instance is refused: a field with no clock of its own reads as that date, and a date in the
future would hold the clock of the home there.

## Retention

A change, a deleted row and its tombstone are kept 400 days; a task of the worker removes
them every night at 03:30 (time zone of the instance). The purge removes the changes created
more than 400 days ago (exactly 400 days old is kept), records in `floor` the highest position
it removed, so that a cursor below it can be answered with 410, and removes the clocks of the
rows deleted more than 400 days ago, with those rows when they are still deleted. What hangs off
a purged row goes with it: the items and photos of a purged room lose their clocks too, and the
files of their photos are removed from the disk once the purge is committed. A row that stays but
whose reference the purge empties (`SET_NULL`: the paint of a room whose article is purged, the
contract of an item whose contractor is) records that change, as any write of a row does.

The rows a purged row takes with it by cascade (the quotes of a project, the rows of its link
tables) lose their clocks too, and their files are removed from the disk once the purge is
committed: the purge leaves no clock without its row and no file without its record.

## The guard test

`tests/test_sync_guard.py` lists the models of the homes and fails when a write of a model of a
covered application adds no change. A lot that covers an application adds its name to
`COVERED_APPS` and, for each model, one scenario per kind of write (save, queryset update, soft
deletion, hard deletion) that runs the code the application really runs, in a file of its own,
`tests/sync_scenarios_<lot>.py`, that the test loads. A kind that cannot happen goes to
`NOT_APPLICABLE` with its reason. An application that is listed without scenarios, or whose
scenario writes without recording, fails. Covered today: `homes` (the home itself) and `rooms` (rooms, items, photos); the later lots add their application to the table below.

## Covered applications

| Application | Lot | Rows |
| --- | --- | --- |
| `projects` | M3e | project, budget line, quote, file, link, task of a project, annual envelope, rooms of a project |
| `tasks` | M3c | contractor, task, task material, occurrence, material draw, attachment |

**Projects.** The services of `housecairn.projects` call `housecairn.projects.changelog`, which
compares the row with what the database held before the write and records the fields whose
value changed. A change carries the columns of the model, a foreign key by its column name
(`project_id`); it leaves out the times the server keeps, where the reading of a link's page
stands (`preview_state`, not data of the home, as in the export) and the stored files of a
`projects.projectfile`, whose references the lot of the files maps. Taking a task out of a
project deletes its link for good, so that the task may join another project, and leaves a
tombstone first; so does deleting a project for the links to its tasks.

**Link tables.** A row of a link table has no id a phone could share: its change is recorded
under `uuid5(home, t + ":" + owner + ":" + other)`, as ADR 0077 names it
(`housecairn.sync.links`). Linking two rows again restores the row from its tombstone.

**Restore.** A restore records the rows it inserts itself (`sync.rows.record_created`), the rows
of the projects included; the rows of a link table have no row of their own there. The projects
section of the export therefore gives `on_written`, which a restore, as a copy or not, calls with
the rows of the section once they and their links are written, in its transaction: the
application records the links under their new ids.

**Tasks.** Taking back a closing deletes the occurrence that followed with a tombstone instead
of erasing it, and lets go of the occurrence it followed, so that the closing can be made again.
An attachment sends its file as `file`, a reference with its size, type and dimensions, as a
photo does. Deleting an account takes it off the tasks it is assigned and the occurrences it
closed, with a change for each, where the database would empty them without one.
