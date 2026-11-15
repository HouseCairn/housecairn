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

## Retention

A change, a deleted row and its tombstone are kept 400 days; a task of the worker removes
them every night at 03:30 (time zone of the instance). The purge removes the changes created
more than 400 days ago (exactly 400 days old is kept), records in `floor` the highest position
it removed, so that a cursor below it can be answered with 410, and removes the clocks of the
rows deleted more than 400 days ago, with those rows when they are still deleted.

## The guard test

`tests/test_sync_guard.py` lists the models of the homes and fails when a write of a model of a
covered application adds no change. A lot that covers an application adds its name to
`COVERED_APPS` and, for each model, one scenario per kind of write (save, queryset update, soft
deletion, hard deletion) that runs the code the application really runs. An application that is
listed without scenarios, or whose scenario writes without recording, fails.
