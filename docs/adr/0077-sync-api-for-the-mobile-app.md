# 0077. A sync API for the mobile app: the operations of its protocol, in clear, per home

- Status: proposed. The server mode of the mobile app, its sign-in and the archiving of homes
  are the maintainer's decisions of 2026-11-14, and so are the answers to the questions of
  [ADR 0079](0079-lots-of-the-server-mode-of-the-mobile-app.md#decisions-of-the-maintainer-2026-11-14)
  (deletions kept 400 days, then a fresh start by a reconciliation rule; archiving by owners;
  unknown fields kept by the server); the shape of the API below is what this record proposes.
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
without a token. An API token or a session is refused with 403 (`sync_needs_connection`), so
Home Assistant and the scripts of today never write through them; the other way round, an
access token of a connection is refused with 403 on every other route of the API. Both
directions are enforced and tested as ADR 0078 says in
[A connection opens the sync API only](0078-oauth-2-with-pkce-for-the-mobile-app.md#a-connection-opens-the-sync-api-only-closed-by-default).
Every route of `/api/v1/sync/` follows the transport rule of ADR 0078 (HTTPS, or plain HTTP
between local addresses only).

| Method and path | Body | Answer |
| --- | --- | --- |
| `GET /api/v1/sync/info` | | without a token: `{"sync": [1], "oauth": {"authorize", "token", "revoke", "client_id"}}`, nothing else, and `"oauth": null` when the instance turned the mobile app off; with one: also `{"server": "0.3.1", "server_device": b64, "features": [...], "limits": {...}, "connection": {"id", "may_create_homes"}}` |
| `GET /api/v1/sync/homes/` | | the homes bound to the connection: `{"homes": [{"id", "name", "role", "archived_at", "head"}], "gone": [{"id", "at"}]}` |
| `POST /api/v1/sync/homes/` | `{"id", "name", "currency", "hemisphere"}` | 201 the home, created with the phone's id and bound to the connection; 200 if the same id and fields exist; 409 with other fields; 403 without the right to create homes |
| `GET /api/v1/sync/homes/{h}/changes?after={pos}&limit={n}` | | `{"changes": [{"pos", "op"}], "head", "more"}` in `pos` order; 410 `cursor_expired` when `after` is older than the oldest change kept |
| `GET /api/v1/sync/homes/{h}/snapshot` | | JSON lines: one row per line with every field and its clock, tombstones within the retention included, then `{"covers": pos}` |
| `POST /api/v1/sync/homes/{h}/push` | `{"push": n, "sv": n, "ops": [op, ...]}` | `{"head", "results": [...]}`, see below; 409 `push_conflict` when the push number was used with other bytes |
| `PUT /api/v1/sync/homes/{h}/files/{file_id}` | octet-stream, `Content-Type`, `HC-Sha256` | 201; 200 if the same bytes exist; 409 with other bytes; 413 above the limit |
| `GET /api/v1/sync/homes/{h}/files/{file_id}` | | the file, and `?size=thumbnail` for an image |

Errors are those of the API today (`{"detail": ...}`), with a `code` (`cursor_expired`,
`push_conflict`, `too_large`, `sync_version`, `home_gone`, `sync_needs_connection`,
`insecure_transport`) so that the app words them itself.

**A home that was erased.** Every route under `/api/v1/sync/homes/{h}/` answers 410 `home_gone`
when `h` is in the table of erased homes and was bound to the connection when it was erased (the
row of the erased home keeps the ids of those connections); the phone then erases the home as
the purge of the protocol does and drops every operation of it, sent or not. For any other
connection the answer is 404, as for a home it never had, so an erased home reveals nothing.
A push to an erased home applies nothing.

**Three numbers, three names.** `pos` is the server's position in the change feed of one home,
the phone's cursor (the relay's word for the same thing, protocol section 3.3). `push` is the
number the phone gives each push, per connection, so that a repeated push runs once. `seq`
stays the relay's number of the batches of one device (protocol section 2.3) and does not
appear in this API.

### The operation

The operation is section 2.2 of the mobile protocol, unchanged: entity type `t` as
`<app>.<model>`, home `h`, row `id`, kind `k` (`put`, `delete`, `restore`), clock `c`, fields
`f` with the column names of the models (`room_id`). Instants are hybrid logical clocks
(16 hexadecimal digits), amounts decimal strings, dates `YYYY-MM-DD`. The only difference with
the relay is the device: the relay reads it from the signature of a batch, the server from the
token, whose connection keeps the phone's device id (16 bytes in `b64`, protocol section 1)
from its first push and refuses another. The protocol's family types (`family.member`,
`family.settings`) are refused: with a server, the members are its accounts, read only on the
phone.

**The server's device id.** The server writes as one device: 16 random bytes made once per
instance by the first migration of the `sync` app, kept in the database (an instance backup
carries it), given in `info` as `server_device` in `b64`. A tie between two equal clocks is
broken by comparing the bytes of the two device ids, as protocol section 1 says, so the phone
and the server order a web write against a phone write the same way. No name such as `srv`
stands for it.

**Row ids are unique on the server.** The protocol keys rows by `(t, h, id)`; the server keys
them by `id` alone (`core/models.py:35`, one table per type). The two agree because, in server
mode, a phone gives every row an id of its own (a random UUID version 4, or `uuid5` in the
namespace of the home, which differs per home), so two rows of one server never share an id.
A `put` that would create a row whose id exists in another home or another table is
`rejected` with `id_taken`, which does not say where; protocol section 2.2 states the rule for
the server mode.

### How the eleven needs are met

1. **Ids made by the client.** A `put` of an unknown row creates it with the operation's `id`.
   The id must be a UUID, and is `rejected` with `id_taken` if it exists in another home or in
   another table (see [the operation](#the-operation)). The resources keep `id` read only.
2. **The same deterministic ids on the server.** The web makes the rows that have a uniqueness
   rule with the ids of mobile ADR 0003, in the namespace of the home, from these exact names
   (UTF-8; ids in the lower-case canonical text of a UUID; the year in four decimal digits):

   | Row | Id |
   | --- | --- |
   | successor of an occurrence (`_spawn_next`) | `uuid5(home, "next:" + occurrence_id)` |
   | material line of a task | `uuid5(home, "material:" + task_id + ":" + article_id)` |
   | draw of a material | `uuid5(home, "draw:" + occurrence_id + ":" + article_id)` |
   | annual envelope | `uuid5(home, "envelope:" + year)` |
   | rooms of a project | `uuid5(home, "projects.project_rooms:" + project_id + ":" + room_id)` |
   | rooms of an article | `uuid5(home, "stock.article_rooms:" + article_id + ":" + room_id)` |
   | compatible items of an article | `uuid5(home, "stock.article_compatible_items:" + article_id + ":" + item_id)` |

   A link row is named by its entity type `t` (protocol section 2.2), then the id of the row
   that owns the link, then the other one. The lot that writes these ids publishes vectors
   (`tests/fixtures/sync_ids.json`: a home, the inputs and the expected id for every row of the
   table), which the mobile repository pins and compares as it does the recurrence vectors;
   mobile ADR 0003 carries the same table. The web and a phone that close the same
   occurrence then make the same successor, whose fields merge. A restore as a copy
   ([ADR 0069](0069-restore-as-a-copy-with-new-identifiers.md)) computes these ids again from
   the new ids of their parents.
3. **Changes since a cursor.** Each home has a change position `pos`: an integer, given by the
   server in the transaction that writes the change, under a lock on the home's row held until
   the commit, so that the order of `pos` is the order of commits and a reader never sees
   position n + 1 before n is committed (SQLite writes one transaction at a time; PostgreSQL
   waits on the lock). Every write of a row of a home, from the web, the API, a background task
   or a sync push, adds a change: the row, the fields written, their clocks, the device. The
   feed reads changes in `pos` order, every entity type of the home together.
4. **Deletions visible.** A deletion is a change like any other (`k: delete`), for soft
   deletions of one row and of a queryset alike. The hard deletions of today become soft or
   write a tombstone first: the successor removed by an undo, a task detached from a project.
   Erasing a home cannot leave its changes behind (they are erased with it): it writes one row
   in a table of erased homes, outside the home, which `GET /sync/homes/` returns in `gone`; the
   phone erases the home as the purge of the protocol does.
   **Retention** (the maintainer's decision): a deleted row, its tombstone and the changes of
   the feed stay 400 days, so a phone offline for a year still learns of the deletion, then a
   nightly task purges them, which settles the purge
   [ADR 0005](0005-stable-identifiers-and-soft-deletion.md) left open. A cursor older than the
   oldest change kept answers 410 `cursor_expired`, and the phone starts again from the
   snapshot by the rule of [Starting again after 410](#starting-again-after-410), which takes
   the place, in the server mode, of the promise of mobile ADR 0003 that tombstones are never
   dropped.
5. **Conflicts per field.** The server keeps a clock per field of every row of a home (the
   clock and the device of the last write, as `field_clock` on the phone). A `put` writes a
   field only if its `(clock, device)` is greater than the stored one, which is mobile ADR 0003
   applied by the server. Each result says, for every field that lost, the value, clock and
   device that won; for a long text that lost (notes, procedure, goal), the losing text is
   returned too, so that the phone keeps it as "other version". **A write from the web gets a
   clock** from the server's hybrid clock and the server's device id, and only for the fields
   whose value changed: a form that sends every field must not bump the clocks of the fields
   nobody touched, or it would beat the phone's edits of them. A field with no stored clock
   (rows written before the change log exists) reads as the clock of the row's `updated_at`
   with the server's device id; nothing is migrated.
6. **Rules across rows repaired, not refused.** After applying the operations of a push, in
   the same transaction, the server runs the repair rules of mobile ADR 0003 on the merged state
   before it is written (two open occurrences of one task: the one that follows stays open, the
   earlier one is closed as done if someone marked it done, else skipped; an undo whose
   successor was edited elsewhere: the edit wins and the undo is reverted, with a result that
   says so). The constraints of the database stay as they are; the repair runs before them, so
   they are never hit by a sync. Each repair is a change of its own, with the server's clock.
   The other rules of mobile ADR 0003 (an accepted quote whose line was deleted, stock below
   zero) are shown, not changed: the server writes nothing for them.
7. **Writes as data, actions once.** A push carries data, never actions: closing an occurrence
   is `state`, `completed_on` and `completed_by` on the occurrence, a `put` of its successor
   with the deterministic id and the draws of its materials. The server checks them against the
   rules of the task (the successor's date is the one recurrence gives, computed by the web's
   own recurrence code; a mismatch is repaired to it) and runs the effects the web runs once per
   new state (audit, stock that follows Done,
   [ADR 0046](0046-stock-linked-to-tasks-and-rooms.md)), keyed by the deterministic ids, so an
   effect that already happened is not done again. `visible_from` is derived (mobile ADR 0003):
   it is never in an operation, and the server computes it as the web does. **A push runs
   once**: the phone numbers its pushes (`push`); the server keeps, per connection, the number,
   the digest of the body and the answer of each push for 30 days, and answers a repeated push
   with the first answer, without applying it again. The same number with another body is 409
   `push_conflict`.
8. **A credential per phone, which can make a home.** [ADR 0078](0078-oauth-2-with-pkce-for-the-mobile-app.md).
   `POST /sync/homes/` creates a home with the phone's id when the connection has the right, makes
   the account its owner, and binds the home to the connection. The resources keep refusing home
   creation and restore to every token.
9. **Fields the web lacks.** `homes.Home.archived_at` comes to the web (lot "Archiving of
   homes", [ADR 0079](0079-lots-of-the-server-mode-of-the-mobile-app.md)): an archived home is
   hidden from the lists and the today page, sends no reminder, and its calendar feeds stay
   empty; it keeps syncing. **Only an owner archives or unarchives** (the maintainer's
   decision): an operation that sets `archived_at` on `homes.home`, pushed by the phone of an
   account that is not an owner of the home, is `rejected` with `forbidden`, the whole
   operation, so its other fields are not written either; in this mode the phone offers
   archiving to owners only. **Value**: in an operation `archived_at` is an HLC string or
   `null` (protocol section 2.2); the web keeps a date and time. The server stores the physical
   part of the HLC (its upper 48 bits, milliseconds since 1970-01-01 UTC) as `Home.archived_at`,
   and keeps the HLC string itself with the clock of the field, so the feed and the snapshot
   give back the exact string the phone sent. An archive made on the web gets as its value the
   server's HLC of that write. The members of a home are in `GET /sync/homes/` and in the
   snapshot (the accounts with their display name and role, never their address), so that the
   phone can show who did what and assign a task. The phone acts as the account that signed in:
   a `completed_by` written by a push is that account, whatever the operation says, and the
   audit log names it.
10. **Files under the phone's ids.** A file reference in an operation names a file the phone
    uploaded under an id it made: `{"file": uuid, "size", "type", "sha256", "width", "height"}`.
    It maps to the reference of the relay (protocol section 2.9,
    `{"blob": b64, "key": b64, "size", "type", "width", "height"}`): `file` is a UUID made by
    the phone instead of 16 random bytes in `b64`; there is no `key`, since the server stores
    the file in clear; `sha256` (lower-case hexadecimal) names the bytes, so that a repeated
    upload is recognised; `size`, `type`, `width` and `height` mean the same. The phone's
    server transport writes this form, and protocol section 2.9 states it.
    The phone uploads the file before the push that references it (as with the relay); a push
    that references a file the server does not have is applied, and the field reads "file on
    another phone" until the file arrives. A repeated upload of the same bytes is 200. The size
    limit is `file_max_bytes` of the instance ([ADR 0035](0035-instance-settings-in-the-interface.md)),
    announced in `info`. The server checks and re-encodes an image as it does today
    ([ADR 0030](0030-photo-storage-and-processing.md)) and serves its own copy; the phone keeps
    its own. A file no row references, live or deleted within the retention, is removed after
    7 days. `?size=thumbnail` is a convenience of the server for its own pages; the phone makes
    its thumbnails itself, as in the protocol.
11. **Versions and limits announced.** `GET /sync/info` gives the server's version, the
    versions of the sync protocol it speaks, its features, the entity types it knows and its
    limits: operations per push (500), size of a push body (1 MiB), file size, and requests per
    minute per connection (proposed defaults: 60 pushes and 600 reads, under the rate limits of
    [ADR 0042](0042-rate-limits-quotas-language-and-instance-name.md)). **A field the server
    does not know is kept** (the maintainer's decision, and protocol section 2.2: "a receiver
    MUST keep fields it does not know"): the server stores it in `extra`, a JSON value per row
    kept by the `sync` app with, for each unknown field, its value, clock and device; it merges
    by the same per-field rule and gives it back in the feed and the snapshot, so a second phone
    gets the field a newer phone wrote. The web neither shows nor edits it. When a later version
    of the server gains the column, its migration moves the value and its clock out of `extra`.
    An unknown field counts in the size of the push. An unknown entity type is still `rejected`
    with `unknown_type`, since there is no table to keep its rows: the phone keeps the
    operation in `pending` (mobile ADR 0003) until the server is updated, and does not send the
    types `info` does not list. A schema version `sv` below the server's `min_sv` reads but
    does not write, as in mobile ADR 0003.

### Results of a push

The push is applied as a whole, in one transaction, or not at all (400 when an operation is
malformed, with its index). Each operation gets one result:

```json
{"i": 3, "status": "applied",
 "lost": {"title": ["Clean the gutters", "0193a1b2c3d40007", "q2Xh0dVt3kWmYc1bS8fP9A"]},
 "other_version": {"notes": "Old notes"}}
```

The device that won is a device id in `b64`, here the server's `server_device`.

`status` is `applied` (written, perhaps with some fields lost), `ignored` (every field lost, or
a repeat), `repaired` (written, then changed by a repair rule: the changes follow in the feed),
or `rejected` with a `code` (`forbidden` for a read-only member, or for `archived_at` set by a
member who is not an owner; `unknown_type`, `invalid_reference`, `wrong_home`, `id_taken`),
which the phone shows on the row without blocking the rest. Every status but `rejected` tells
the phone that the server has seen the row ([next section](#starting-again-after-410)).

### Starting again after 410

After the purge of the retention, the snapshot no longer holds a row deleted more than 400 days
ago, nor its tombstone. A phone that comes back after longer than that gets 410
`cursor_expired`, reads the snapshot, and reconciles every row of the home it holds by this
rule (the maintainer's decision). It keeps the promise of mobile ADR 0003 that a phone back
from a long absence cannot resurrect what others deleted, and loses nothing the phone made
that the server never had:

1. **How the phone knows the server saw a row.** The phone keeps, per row and outside the
   row's fields (in its sync tables, never synced), a mark "seen by the server". It sets the
   mark when the row arrives in the feed or in a snapshot, and when a push result for an
   operation of that row is `applied`, `ignored` or `repaired`. A `rejected` result, or a push
   whose answer never came, does not set it; the same push sent again with the same number
   gets the first answer, and sets it then.
2. **A row in the snapshot** is merged field by field with the phone's copy, as any incoming
   change, and gets the mark.
3. **A row absent from the snapshot and seen by the server** was deleted and then purged on
   the server: the phone deletes it, its children and its local files, and drops the
   operations of it still waiting to be sent, a `restore` included; a long text so dropped
   goes to the "other version" list, and the app lists what it dropped.
4. **A row absent from the snapshot and never seen by the server** was made on the phone and
   never reached the server: the phone keeps it and sends it again as a `put` of every field
   with its clock, in its next push.
5. **Operations still waiting** for the rows of rules 2 and 4 are pushed as usual and decided
   field by field by the server; the results say which fields lost.
6. **Rows of the snapshot the phone did not have** are added, tombstones included; the cursor
   becomes the snapshot's `covers`.

The limit of the rule: a push the server applied but whose answer was lost, never sent again
within the 30 days a push number is kept, and whose row someone else deleted and the purge
then removed, comes back once by rule 4, and the person deletes it again. It needs a phone that
stayed away more than 400 days right after a cut connection.

### Who may do what

The rights are those of the account in each home ([ADR 0020](0020-household-roles-and-invitations.md)):
a read-only member's phone reads and pushes nothing that is accepted; deleting a home is not
an operation (only an owner, on the web, after typing its name). Home scoping
([ADR 0006](0006-home-scoping.md)) narrows by the homes bound to the connection, and a home that
requires a sign-in provider follows the rule of tokens
([ADR 0057](0057-api-tokens-of-a-home-that-requires-a-provider.md)).

### Compatibility

- The resources of `/api/v1/`, their fields, their order and the API tokens do not change,
  beyond what M1 adds (`archived_at` on a home, two actions). The
  Home Assistant integration, which calls the resources with an API token, keeps working as it
  is. `GET /api/v1/homes/` keeps listing archived homes, with `archived_at` added, so the
  integration sees what it saw before. The successor of an occurrence gets a deterministic id
  instead of a random one; both are UUIDs and no client may read a meaning in them.
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
- The change log, the field clocks (with `extra`) and the pushes add three tables whose size
  follows the activity of a home (a few rows per edit), and the erased homes a fourth, small
  one. The retention purge keeps them bounded.
- The rules of mobile ADR 0003 become rules of the server too: a new rule across two rows needs
  a line in both records before a lot implements it.
- A server keeps the fields it does not know, in `extra`, and gives them to the other phones:
  a newer phone on an older server shares a new field with the other phones at once, and the
  web shows it once the server is updated.
- A phone away more than 400 days starts again from the snapshot by the reconciliation rule:
  on a server, tombstones are not kept forever as on the relay, and the rule takes the place of
  that promise.
