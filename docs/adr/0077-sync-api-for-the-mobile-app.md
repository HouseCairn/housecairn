# 0077. A sync API for the mobile app: the operations of its protocol, in clear, per home

- Status: proposed. The server mode of the mobile app, its sign-in and the archiving of homes
  are the maintainer's decisions of 2026-11-14; the shape of the API below is what this record
  proposes.
- Date: 2026-11-14
- Amends: [ADR 0005](0005-stable-identifiers-and-soft-deletion.md) (ids made by a client,
  deterministic ids, how long a deletion stays visible) and
  [ADR 0004](0004-versioned-rest-api.md) (a feed ordered by a change sequence, besides the
  lists ordered by creation).
- Related: [ADR 0078](0078-oauth-2-with-pkce-for-the-mobile-app.md) (how the phone signs in),
  [ADR 0079](0079-lots-of-the-server-mode-of-the-mobile-app.md) (the lots that build it).

## Context

The mobile app gets a third way to work, "My Housecairn server"
([mobile ADR 0010](https://github.com/HouseCairn/housecairn-mobile/pull/11)): the homes of a
phone live on a Housecairn instance, the self-hosted web application or the Home Assistant
add-on, and what is typed on the phone appears on the web and the other way round. The server
reads the data; it is not an end-to-end encrypted relay. The maintainer decided on 2026-11-14:

- the phone signs in with OAuth 2 and PKCE in the browser, as [ADR 0007](0007-authentication-and-api-tokens.md)
  planned, and each phone can be revoked on its own;
- the phone reaches the homes chosen at sign-in, and may create homes;
- HTTPS, and plain HTTP only on a local address;
- the phone acts as the account that signed in;
- the web gets the archiving of a home;
- merging a phone's homes with non-empty homes of the server comes after v1;
- this way is required before the public beta of the app.

The phone already keeps its data the way a sync needs: every id is made on the phone, every
field has a clock (`field_clock`), and the relay protocol defines the operation it exchanges
([mobile protocol](https://github.com/HouseCairn/housecairn-mobile/blob/main/docs/protocol.md)
section 2.2: `t`, `h`, `id`, `k`, `c`, `f`, with the field names of this application's
models) and the merge rules ([mobile ADR 0003](https://github.com/HouseCairn/housecairn-mobile/blob/main/docs/adr/0003-sync-protocol.md):
last writer wins per field by hybrid logical clock, deterministic ids where the model has a
uniqueness rule, invariants repaired on read and never enforced by refusing a merge).

The mobile record lists eleven things the API of today lacks for a safe two-way sync, with
`file:line` references at `254f889`: ids made by the client; the same deterministic ids on the
server; changes since a cursor; deletions visible; conflicts decided per field; rules across
rows repaired; writes as data besides actions; a credential for a device that can make a home;
fields the web lacks (`archived_at`, members); files under the phone's ids; versions and limits
announced. Two shapes could carry them:

1. **Extend the resources of `/api/v1/`**: client ids on `POST`, `?changed_after=` on each list,
   deleted rows in the lists, `If-Match` on `PATCH`, an idempotency key on actions.
2. **One feed of operations per home**, the operation of the mobile protocol in clear, next to
   the resources.

## Decision

**The second shape: a sync API of operations, per home, under `/api/v1/sync/`.** The resources
of `/api/v1/` do not change; the routes below are added, which [ADR 0004](0004-versioned-rest-api.md)
allows in v1.

Why not the resources:

- a phone would need one request per entity type (twenty-five of them, link tables included)
  to learn what changed, with no common order between them: a task changed after its room was
  deleted could be read before the deletion;
- a change made offline often touches several rows (closing an occurrence writes the
  occurrence, its successor and the draws of its materials): resources apply it in several
  requests, and a cut connection leaves half of it;
- `If-Match` decides per row, the merge rule decides per field: two people who edit two fields
  of one task must both keep their change;
- the app would need a second sync engine besides the one of the relay.

Why the operations: the app has one sync engine and two transports (mobile ADR 0010), and the
server transport sends the same operations as the relay one, in clear. The server applies them
with the same rules, in one transaction per request, and gives them back in one order.

### Routes

All under `/api/v1/sync/`, JSON, answers carry `HC-Sync: 1`. They need an access token of a
connected app ([ADR 0078](0078-oauth-2-with-pkce-for-the-mobile-app.md)), except `info`
without a token; an API token is refused with 403, so Home Assistant and the scripts of today
never write through them.

| Method and path | Body | Answer |
| --- | --- | --- |
| `GET /api/v1/sync/info` | | without a token: `{"sync": [1], "oauth": {"authorize", "token", "revoke", "client_id"}}`, nothing else; with one: also `{"server": "0.3.1", "features": [...], "limits": {...}, "connection": {"id", "may_create_homes"}}` |
| `GET /api/v1/sync/homes/` | | the homes bound to the connection: `{"homes": [{"id", "name", "role", "archived_at", "head"}], "gone": [{"id", "at"}]}` |
| `POST /api/v1/sync/homes/` | `{"id", "name", "currency", "hemisphere"}` | 201 the home, created with the phone's id and bound to the connection; 200 if the same id and fields exist; 409 with other fields; 403 without the right to create homes |
| `GET /api/v1/sync/homes/{h}/changes?after={seq}&limit={n}` | | `{"changes": [{"seq", "op"}], "head", "more"}` in `seq` order; 410 `cursor_expired` when `after` is older than the oldest change kept |
| `GET /api/v1/sync/homes/{h}/snapshot` | | JSON lines: one row per line with every field and its clock, tombstones within the retention included, then `{"covers": seq}` |
| `POST /api/v1/sync/homes/{h}/push` | `{"batch": n, "sv": n, "ops": [op, ...]}` | `{"head", "results": [...]}`, see below; 409 when the batch number was used with other bytes |
| `PUT /api/v1/sync/homes/{h}/files/{file_id}` | octet-stream, `Content-Type`, `HC-Sha256` | 201; 200 if the same bytes exist; 409 with other bytes; 413 above the limit |
| `GET /api/v1/sync/homes/{h}/files/{file_id}` | | the file, and `?size=thumbnail` for an image |

Errors are those of the API today (`{"detail": ...}`), with a `code` (`cursor_expired`,
`batch_conflict`, `too_large`, `sync_version`) so that the app words them itself.

### The operation

The operation is section 2.2 of the mobile protocol, unchanged: entity type `t` as
`<app>.<model>`, home `h`, row `id`, kind `k` (`put`, `delete`, `restore`), clock `c`, fields
`f` with the column names of the models (`room_id`). Instants are hybrid logical clocks
(16 hexadecimal digits), amounts decimal strings, dates `YYYY-MM-DD`. The only difference with
the relay is the device: the relay reads it from the signature of a batch, the server from the
token, whose connection keeps the phone's device id from its first push and refuses another.
The protocol's family types (`family.member`, `family.settings`) are refused: with a
server, the members are its accounts, read only on the phone.

### How the eleven needs are met

1. **Ids made by the client.** A `put` of an unknown row creates it with the operation's `id`.
   The id must be a UUID, and is refused if it exists in another home or in another table (the
   answer does not say which, so that it reveals nothing). The resources keep `id` read only.
2. **The same deterministic ids on the server.** The web makes the rows that have a uniqueness
   rule with the ids of mobile ADR 0003, in the namespace of the home: the successor of an
   occurrence `uuid5(home, "next:" + occurrence)` (in `_spawn_next`), a material line, a draw,
   the annual envelope and the link rows likewise. The web and a phone that close the same
   occurrence then make the same successor, whose fields merge. A restore as a copy
   ([ADR 0069](0069-restore-as-a-copy-with-new-identifiers.md)) computes these ids again from
   the new ids of their parents.
3. **Changes since a cursor.** Each home has a change sequence: an integer, given by the server
   in the transaction that writes the change, under a lock on the home's row, so that the
   order of `seq` is the order of commits (SQLite writes one transaction at a time; PostgreSQL
   waits on the lock). Every write of a row of a home, from the web, the API, a background task
   or a sync push, adds a change: the row, the fields written, their clocks, the device. The
   feed reads changes in `seq` order, every entity type of the home together.
4. **Deletions visible.** A deletion is a change like any other (`k: delete`), for soft
   deletions of one row and of a queryset alike. The hard deletions of today become soft or
   write a tombstone first: the successor removed by an undo, a task detached from a project.
   Erasing a home cannot leave its changes behind (they are erased with it): it writes one row
   in a table of erased homes, outside the home, which `GET /sync/homes/` returns in `gone`; the
   phone erases the home as the purge of the protocol does.
   **Retention**: a deleted row and its tombstone stay at least 400 days, so a phone offline
   for a year still learns of it, then a nightly task purges both, which settles the purge
   [ADR 0005](0005-stable-identifiers-and-soft-deletion.md) left open. A cursor older than the
   oldest change kept answers 410, and the phone starts again from the snapshot. The duration
   is the maintainer's to confirm (question in [ADR 0079](0079-lots-of-the-server-mode-of-the-mobile-app.md)).
5. **Conflicts per field.** The server keeps a clock per field of every row of a home (the
   clock and the device of the last write, as `field_clock` on the phone). A `put` writes a
   field only if its `(clock, device)` is greater than the stored one, which is mobile ADR 0003
   applied by the server. Each result says, for every field that lost, the value, clock and
   device that won; for a long text that lost (notes, procedure, goal), the losing text is
   returned too, so that the phone keeps it as "other version". **A write from the web gets a
   clock** from the server's hybrid clock and the server's device id, and only for the fields
   whose value changed: a form that sends every field must not bump the clocks of the fields
   nobody touched, or it would beat the phone's edits of them. Rows written before the change
   log exists get, once, the clock of their `updated_at`.
6. **Rules across rows repaired, not refused.** After applying the operations of a push, in
   the same transaction, the server runs the repair rules of mobile ADR 0003 on the merged state
   before it is written (two open occurrences of one task: the one that follows stays open, the
   earlier one is closed as done if someone marked it done, else skipped; an undo whose
   successor was edited elsewhere: the edit wins and the undo is reverted, with a result that
   says so). The constraints of the database stay as they are; the repair runs before them, so
   they are never hit by a sync. Each repair is a change of its own, with the server's clock.
7. **Writes as data, actions once.** A push carries data, never actions: closing an occurrence
   is `state`, `completed_on` and `completed_by` on the occurrence, a `put` of its successor
   with the deterministic id and the draws of its materials. The server checks them against the
   rules of the task (the successor's date is the one recurrence gives; a mismatch is repaired to
   it) and runs the effects the web runs once per new state (audit, stock that follows Done,
   [ADR 0046](0046-stock-linked-to-tasks-and-rooms.md)), keyed by the deterministic ids, so an
   effect that already happened is not done again. **A batch runs once**: the phone numbers its
   batches; the server keeps, per connection, the number, the digest of the body and the answer of
   each batch for 30 days, and answers a repeated batch with the first answer, without applying
   it again. The same number with another body is 409.
8. **A credential per phone, which can make a home.** [ADR 0078](0078-oauth-2-with-pkce-for-the-mobile-app.md).
   `POST /sync/homes/` creates a home with the phone's id when the connection has the right, makes
   the account its owner, and binds the home to the connection. The resources keep refusing home
   creation and restore to every token.
9. **Fields the web lacks.** `homes.Home.archived_at` comes to the web (lot "Archiving of
   homes", [ADR 0079](0079-lots-of-the-server-mode-of-the-mobile-app.md)): an archived home is
   hidden from the lists and the today page, sends no reminder, and its calendar feeds stay
   empty; it keeps syncing. The members of a home are in `GET /sync/homes/` and in the
   snapshot (the accounts with their display name and role, never their address), so that the
   phone can show who did what and assign a task. The phone acts as the account that signed in:
   a `completed_by` written by a push is that account, whatever the operation says, and the
   audit log names it.
10. **Files under the phone's ids.** A file reference in an operation names a file the phone
    uploaded under an id it made: `{"file": uuid, "size", "type", "sha256", "width", "height"}`.
    The phone uploads the file before the push that references it (as with the relay); a push
    that references a file the server does not have is applied, and the field reads "file on
    another phone" until the file arrives. A repeated upload of the same bytes is 200. The size
    limit is `file_max_bytes` of the instance ([ADR 0035](0035-instance-settings-in-the-interface.md)),
    announced in `info`. The server checks and re-encodes an image as it does today
    ([ADR 0030](0030-photo-storage-and-processing.md)) and serves its own copy; the phone keeps
    its own. A file no row references, live or deleted within the retention, is removed after
    7 days.
11. **Versions and limits announced.** `GET /sync/info` gives the server's version, the
    versions of the sync protocol it speaks, its features and its limits: operations per push
    (500), size of a push body (1 MiB), file size, requests per minute. A field the server does
    not know is not kept: the server is the copy the web reads, and it has no column for it. The
    operation is applied without it, and its result lists the field in `unknown`, so the phone
    keeps it on its side and nothing is lost silently. A phone newer than the server reads, in
    `info`, which features are missing and does not send what the server cannot keep. A schema
    version `sv` below the server's `min_sv` reads but does not write, as in mobile ADR 0003.

### Results of a push

The push is applied as a whole, in one transaction, or not at all (400 when an operation is
malformed, with its index). Each operation gets one result:

```json
{"i": 3, "status": "applied", "lost": {"title": ["Clean the gutters", "0193a1b2c3d40007", "srv"]},
 "other_version": {"notes": "Old notes"}}
```

`status` is `applied` (written, perhaps with some fields lost), `ignored` (every field lost, or
a repeat), `repaired` (written, then changed by a repair rule: the changes follow in the feed),
or `rejected` with a `code` (`forbidden` for a read-only member, `unknown_type`,
`invalid_reference`, `wrong_home`), which the phone shows on the row without blocking the rest.

### Who may do what

The rights are those of the account in each home ([ADR 0020](0020-household-roles-and-invitations.md)):
a read-only member's phone reads and pushes nothing that is accepted; deleting a home is not
an operation (only an owner, on the web, after typing its name). Home scoping
([ADR 0006](0006-home-scoping.md)) narrows by the homes bound to the connection, and a home that
requires a sign-in provider follows the rule of tokens
([ADR 0057](0057-api-tokens-of-a-home-that-requires-a-provider.md)).

### Compatibility

- The resources of `/api/v1/`, their fields, their order and the API tokens do not change. The
  Home Assistant integration, which calls the resources with an API token, keeps working as it
  is. The successor of an occurrence gets a deterministic id instead of a random one; both are
  UUIDs and no client may read a meaning in them.
- A change made through the resources is a change of the feed like any other: a phone sees
  what Home Assistant or a script did.

## Consequences

- One sync engine on the phone, two transports; the rules of mobile ADR 0003 are written twice,
  in Dart and in Python, and must agree. The tests of the sync lot run the vectors of the
  mobile repository (random histories applied in random orders give the same rows), as the
  recurrence vectors already do.
- Every write path of a home's rows must record its change. A guard test lists the models of
  the homes and fails when a write of one of them (save, queryset update, soft deletion, hard
  deletion) adds no change, as the parity test of [ADR 0003](0003-web-api-parity.md) does for
  web and API.
- The change log, the field clocks and the batches add three tables whose size follows the
  activity of a home (a few rows per edit). The retention purge keeps them bounded.
- The rules of mobile ADR 0003 become rules of the server too: a new rule across two rows needs
  a line in both records before a lot implements it.
- A server keeps no field it does not know. A newer phone on an older server cannot share a new
  field until the server is updated; the app keeps it locally and says so.
