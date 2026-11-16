# Sync API

The mobile app keeps its homes on a HouseCairn server through the sync API, under
`/api/v1/sync/` ([ADR 0077](../adr/0077-sync-api-for-the-mobile-app.md)). It exchanges the
operations of the mobile app's protocol, in clear, home by home: the app reads what changed
since its last position, starts from a snapshot of the home when it has none, pushes what was
typed on the phone, creates on the server the homes the phone made, and sends and fetches
the files of its photos and attachments.

## Access

Every route but `info` takes only the access token of a
[connected app](../screens/api-tokens.md#connected-apps) (`Authorization: Bearer hca_...`),
with the scope `sync`, given when a person connects the app in the browser
([ADR 0078](../adr/0078-oauth-2-with-pkce-for-the-mobile-app.md)). An API token or a session
gets `403` with `"code": "sync_needs_connection"`. The routes answer over HTTPS, and over plain
HTTP only between local addresses (`403` with `"code": "insecure_transport"` otherwise). Every
answer carries the header `HC-Sync: 1`.

A connection reads the homes chosen when it was made, as long as its account is a member of
them, with the account's role. A home it was not given, a home its account left, and a home
that does not exist all answer `404`, never `403`. A home that requires a sign-in provider is
read only by a connection made after signing in through it (`403` with
`"code": "sso_required"` otherwise), as for an API token. An archived home is listed and read
like the others.

Reads are limited to 600 a minute per connection, pushes to 60 a minute per connection, and
snapshots to 6 a minute per connection (a snapshot also counts as a read), each counted apart;
beyond that the answer is `429`. An upload of a file counts as a push, a download as a read.
`GET /api/v1/sync/info`, with the access token of a connection, gives these limits in `limits`:
`reads_per_minute`, `snapshots_per_minute`, `pushes_per_minute`, `ops_per_push`, `push_bytes`
and `file_max_bytes`, the largest file the instance accepts (its setting of the largest file).

## Values

The operations and rows are those of the mobile protocol: entity types `<app>.<model>` in
lower case (`rooms.room`), field names those of the models with foreign keys by their column
name (`room_id`), amounts as decimal strings, dates `YYYY-MM-DD`. A **clock** is a hybrid
logical clock, 16 lower-case hexadecimal digits; a **device** is the id of the device that
wrote, 16 bytes in base64url without padding. The server writes the changes made on the web
as one device, whose id `GET /api/v1/sync/info` gives. A file is given as a reference, never by
the name of the file the server stores ([Files](#files)).

A field that holds a code of a list takes one of the codes below; a code a phone does not know
yet is kept and shown as it is, never refused:

- `rooms.item.warranty_duration_unit`: `month` or `year` (with `warranty_duration`, from 1 to
  99); `rooms.item.warranty_provider`: `manufacturer`, `shop`, or empty. `warranty_ends_on` is
  worked out from `warranty_starts_on`, else `purchased_on`, plus the duration, the same date
  that many months or years later (the last day of a shorter month); without a duration or a
  start, it is the date typed. A phone that changes one of these fields sends the end it works
  out the same way.
- `stock.article.unit`: `piece`, `litre`, `millilitre`, `metre`, `kilogram`, `roll`, `box`,
  `other` (with `unit_other`), or empty. There is no conversion between units: a quantity is
  in the unit of its item.

## The homes

`GET /api/v1/sync/homes/`

```json
{
  "homes": [
    {
      "id": "5b7e8a1c-2d3f-4a5b-9c6d-7e8f9a0b1c2d",
      "name": "Main house",
      "role": "owner",
      "archived_at": null,
      "head": 42,
      "member_id": "0d1e2f3a-4b5c-4d6e-8f7a-9b0c1d2e3f4a",
      "members": [
        {"id": "0d1e2f3a-4b5c-4d6e-8f7a-9b0c1d2e3f4a", "name": "Sam", "role": "owner"}
      ]
    }
  ],
  "gone": [{"id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d", "at": "2026-11-14T10:00:00+00:00"}]
}
```

- `role`: `owner`, `member` or `read_only`, the role of the account in the home.
- `archived_at`: null, or the clock of the archiving.
- `head`: the position of the last change of the home.
- `member_id`: the `id` of the entry of `members` that is the account of the connection, in this
  home. Look it up there to know who "me" is, for example to keep the reminders of the tasks
  whose `assignee_id` is this value. It is the same value as the account's own `id` in
  `members`, so it tells nothing more about the account. It is the account's id, like `members[].id` and `assignee_id`, not the membership id that the routes `/api/v1/homes/{home id}/members/{id}/` take. Added after the first shape: a client
  written before it ignores it.
- `members`: the accounts of the home, with the name shown for them and their role, never
  their address. The rows name a person by this `id` (`assignee_id`, `completed_by_id`).
- `gone`: the homes erased while they were bound to this connection, and when. The app erases
  them on the phone. A connection whose account had already left the home when it was erased
  is not told.

## Creating a home

`POST /api/v1/sync/homes/`, with a JSON body (`Content-Type: application/json`):

```json
{
  "id": "5b7e8a1c-2d3f-4a5b-9c6d-7e8f9a0b1c2d",
  "name": "Main house",
  "currency": "EUR",
  "hemisphere": "north"
}
```

The app sends the homes of the phone this way when the person keeps the phone's homes on the
server, then pushes their rows. The server creates the home with the phone's `id`, the account
of the connection its owner, and adds it to the homes of the connection. The answer is `201`
with the home as `GET /api/v1/sync/homes/` lists it (`role`, `head`, `member_id`, `members`).
The creation is the first change of the home: `changes` and `snapshot` give the row
`homes.home`, written with the server's clock.

- `id`: a UUID in its canonical text form, made by the phone.
- `name`, `currency`, `hemisphere`: checked as the web checks a new home. The name is trimmed,
  120 characters at most, not empty; the currency an ISO 4217 code in capitals (`EUR`); the
  hemisphere `north` or `south`, `north` when left out. A field the web would refuse is `400`
  with `"code": "malformed"` and `fields`, the messages for each field; a body that is not such
  an object is `400` `malformed` too.
- **Sent again**: the same `id` with the same fields, for a home the connection already has,
  answers `200` with the home, and nothing is written. The app can send it again when it lost
  the first answer.
- **The id is taken**: the same `id` with other fields, or an `id` held by anything else on the
  server (a home of another account, a home of the account that the connection was not given,
  a deleted or erased home, a row of a home), is `409` with `"code": "id_taken"`. The answer is
  the same in every case: it does not say who holds the id.
- **The right to create homes**: the access token needs the scope `homes.create`, which the app
  asks for when the person sends the phone's homes, and the connection the right **May create
  homes**, ticked at consent and shown on
  [Connected apps](../screens/api-tokens.md#connected-apps). Without either, `403` with
  `"code": "cannot_create_homes"`.

Creations count with the pushes, 60 a minute per connection.

## The changes

`GET /api/v1/sync/homes/{home id}/changes?after={position}&limit={n}`

```json
{
  "changes": [
    {
      "pos": 43,
      "device": "q2Xh0dVt3kWmYc1bS8fP9A",
      "op": {
        "t": "rooms.room",
        "h": "5b7e8a1c-2d3f-4a5b-9c6d-7e8f9a0b1c2d",
        "id": "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
        "k": "put",
        "c": "0193a1b2c3d40002",
        "f": {"name": "Hall"}
      }
    }
  ],
  "head": 43,
  "more": false
}
```

The changes of the home after the position `after` (0 at first), in position order, every
entity type together. Each change is the operation of the protocol (`op`: type `t`, home `h`,
row `id`, kind `k` among `put`, `delete` and `restore`, clock `c`, fields `f`, empty for a
deletion or a restoration) and the device that wrote it. Positions are numbered 1, 2, 3...
per home, with no gap; the app keeps the last one it applied and asks for the next page with
it while `more` is true.

- `limit`: 500 by default, at most 1 000; a larger value gives 1 000.
- `after` or `limit` that is not a whole number, or `after` beyond `head`: `400`.
- `after` older than the oldest change kept: `410` with `"code": "cursor_expired"`. Changes
  and deleted rows are kept 400 days; the app then starts again from the snapshot.

## The snapshot

`GET /api/v1/sync/homes/{home id}/snapshot`

The state of the home, in JSON lines (`application/x-ndjson`): one row per line, with every
field, its clock and its device, then the members, then the position the snapshot covers.

```text
{"t":"homes.home","h":"5b7e...","id":"5b7e...","f":{"name":["Main house","0193a1b2c3d40001","q2Xh0dVt3kWmYc1bS8fP9A"]}}
{"t":"rooms.room","h":"5b7e...","id":"1c2d...","f":{"name":["Hall","0193a1b2c3d40002","q2Xh0dVt3kWmYc1bS8fP9A"]}}
{"members":[{"id":"0d1e...","name":"Sam","role":"owner"}]}
{"covers":43}
```

A deleted row is in the snapshot for 400 days after its deletion, with the field `deleted_at`
(its value is the clock of the deletion). The fields the server has no column for, written by a
newer app, are given back as the others. The app then reads the changes after `covers`; a
change it already has from the snapshot changes nothing when applied again.

## Pushing changes

`POST /api/v1/sync/homes/{home id}/push`, with a JSON body (`Content-Type: application/json`):

```json
{
  "push": 17,
  "sv": 1,
  "device": "AAECAwQFBgcICQoLDA0ODw",
  "ops": [
    {
      "t": "rooms.room",
      "h": "5b7e8a1c-2d3f-4a5b-9c6d-7e8f9a0b1c2d",
      "id": "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
      "k": "put",
      "c": "0193a1b2c3d40007",
      "f": {"name": "Hall", "notes": "Paint in May"}
    }
  ]
}
```

- `push`: the number the app gives this push, 1 or more, per connection. A push sent again with
  the same number and the same bytes gets the first answer, and is not applied again; the same
  number with other bytes is `409` with `"code": "push_conflict"`. A number is kept 30 days.
- `sv`: the schema version of the app. Below the lowest version the server accepts (1), the
  app reads but does not write: `400` with `"code": "sync_version"`.
- `device`: the device id of the phone, 16 bytes in base64url. The first push of a connection
  fixes it; another one afterwards, the device id of another connection in use or the server's
  own is refused (`409` with `"code": "wrong_device"`, or `400`). A revoked connection holds no
  device id: a phone signed out then in again pushes with its own.
- `ops`: at most 500 operations of the protocol, in the order the phone made them. A body is at
  most 1 MiB. Beyond either limit: `400` with `"code": "too_large"`.

The push is applied in one transaction, as a whole or not at all. An operation the server
cannot read (a type, an id, a clock or a value out of form, a text that is not valid Unicode, a
value that nests lists or objects more than 32 deep, a new row without the fields it needs, a
write that breaks a rule of the data) makes the whole push `400` with `"code": "malformed"` and
the `index` of the operation, and nothing is written. So does an operation that would leave a
row with more than 64 fields the server does not know, or more than 64 KiB of them as JSON.

**Each field is decided on its own**: a field is written only if the clock of the operation,
then its device id, is greater than the ones the field holds; the server keeps a clock per field
of every row, and a write made on the web gets one from the server's clock. A `put` of a row the
server does not have creates it with the phone's id; the fields it does not set can be written
by any later operation. A `delete` or a `restore` writes the field `deleted_at`; a `put` never
brings back a deleted row, only a `restore` with a later clock does. A `delete` may come before
the `put` of its row, from another phone: the server keeps it, the row then comes deleted, and
the snapshot gives the deletion of a row the server does not have yet. A field the server has no column for (written by a newer app) is kept and given back to
the other phones; so is, for now, the file reference of the label of an article and of the
photo of a report. A photo, an attachment and a file of a project take the file their
reference names ([Files](#files)). The server writes who acted, whatever the operation says: `completed_by_id` and
`closed_by_id` are the account of the phone, and so is `reporter_id` of a new report, which no
later operation changes. `assignee_id` may name any member of the home.

**Rules across rows are repaired, not refused.** Once the operations are applied, in the same
transaction, the server runs the rules the phone runs (mobile ADR 0003), each repair a change of
its own with the server's clock, which the feed then gives:

- **Two open occurrences of one task**: the one that follows the other (through `follows_id`)
  stays open, else the one whose `task_id` has the later clock, then the greater device id, then
  the greater id. The other is closed as `done` if it has a `completed_on`, else `skipped`.
- **An undo whose successor was edited elsewhere** (postponed, closed, given a note, a cost or a
  photo) is reverted: the successor comes back, the occurrence is closed again with the values it
  had before the undo, its draws come back, and the server takes their quantity from the stock
  again, once.
- **The date of a successor**: a `put` that sets `follows_id` and `due_on`, the successor of a
  closing, gets the date the rule of the task gives, counted from `completed_on`, or, for a skip,
  from the day of the clock of its `status` in the time zone of the instance.

The server also computes what is derived: `visible_from` of the occurrences, the months of the
tasks on a predefined season when `hemisphere` changes, and the effect of each stock movement on
the stock, in the order of the clocks (an inventory makes the stock its count). It runs once what
the web runs on a closing: an audit entry for each occurrence that becomes done or skipped, in
the name of the account of the phone; and for one that becomes done, the draws of its materials
that the push did not carry (a phone sends a closing in one push, with its draws and exits). An
undo pushed is data only: the server writes nothing more for it. Other rules of the data (two
material lines of one article, two occurrences that follow the same one, a draw above what it
wanted) have no repair: the push is `400` with `malformed`.

```json
{
  "head": 44,
  "results": [
    {
      "i": 0,
      "status": "applied",
      "lost": {"notes": ["Paint in June", "0193a1b2c3d50001", "q2Xh0dVt3kWmYc1bS8fP9A"]},
      "other_version": {"notes": "Paint in May"}
    }
  ]
}
```

Each operation gets one result, with its index `i`:

| Status | Meaning |
| --- | --- |
| `applied` | Written, perhaps with some fields lost. |
| `ignored` | Nothing written: every field lost, or the same operation was applied before. |
| `repaired` | Written, then changed by a repair rule: the changes of the repair follow in the feed. |
| `rejected` | Not written, with a `code` the app shows on the row without blocking the rest of the push. |

- `lost`: for each field that lost, the value, the clock and the device of the write that won.
- `other_version`: for a long text that lost (notes, procedure, goal), the phone's own text, so
  that the app keeps it as an other version.
- `head`: the position of the last change of the home after the push.

The codes of a rejected operation:

| Code | When |
| --- | --- |
| `forbidden` | The account is a read-only member of the home; or the operation sets `archived_at` on the home and the account is not an owner (the whole operation is rejected, its other fields too); or it writes a field the server keeps for itself (`required_provider`, `visible_from`, a thumbnail); or it deletes or restores the home itself, which is erased on the web only. |
| `unknown_type` | The entity type is not one the server keeps (`family.member` and `family.settings` included). |
| `invalid_reference` | A reference names a row that is not in this home, or a person who is not a member; or the id of a link row does not name its two rows. |
| `wrong_home` | The operation names another home than the one of the route. |
| `id_taken` | The id is held by a row of another home or of another type. The answer does not say where. |
| `clock_ahead` | The clock of the operation is more than 24 hours ahead of the server's: the date of the phone is wrong. The other operations of the push apply. |

A push to a home that was erased answers `410` with `"code": "home_gone"`, and applies nothing.

## Files

A photo of a room or an item (`rooms.photo`, field `image`), an attachment of a task
(`tasks.attachment`, field `file`) and a file of a project (`projects.projectfile`, field
`file`) hold a **file reference**:

```json
{"file": "3c1d9e2a-7b4f-4c8d-9e1a-2b3c4d5e6f70", "sha256": "9f86d081...", "size": 48213,
 "type": "image/jpeg", "width": 1280, "height": 960}
```

`file` is an id the phone makes for the file, a UUID that is not the id of the row; `sha256` is
the SHA-256 of the bytes it uploads, in lower-case hexadecimal; `width` and `height` are given
for an image only. A photo or a file made on the web is named by the id of its row, and has no
`sha256`. A new photo, attachment or file of a project needs its reference: without it, or with
a value that is not one, the push is `400` `malformed`.

The phone uploads the file **before** the push that refers to it:

`PUT /api/v1/sync/homes/{h}/files/{file_id}`, the bytes of the file as the body
(`Content-Type: application/octet-stream`, or the type of the file), with the header
`HC-Sha256` holding their digest.

| Answer | When |
| --- | --- |
| `201` | The file is kept: `{"file": "...", "sha256": "..."}`. |
| `200` | The same bytes were uploaded before under this id: nothing changes. |
| `400` `malformed` | `HC-Sha256` missing, not 64 lower-case hexadecimal digits, or not the digest of the body; or an empty body. |
| `403` `forbidden` | The account is a read-only member of the home. |
| `409` `file_conflict` | The id holds other bytes, or is held by anything else on the server (a file of another home, a file of the web). The answer does not say which. |
| `413` `too_large` | The file is larger than `file_max_bytes`. A length announced above it is refused before the body is read. |
| `413` `quota_exceeded` | The home has no room left for the file, or holds as many files as it may ([settings](../administration/settings.md)). |
| `415` `unsupported_type` | Not a file the web accepts: a JPEG, PNG or WebP image, or a PDF, text, Word, Excel or OpenDocument file. HEIC photos are refused, as on the web. |

The server checks the file as the web checks an attachment, by its content and never by the
type the phone gives: an image is decoded and written again, which drops its metadata (the
location of the photo included), and gets a thumbnail; a document is kept as it is. The
server keeps its own copy and the phone keeps its own, so the bytes the server sends back are
not those of the upload. The file counts in the quota of the home from its upload.

When a row refers to a file the server has, the row takes it; a photo takes an image only.
When the server does not have it yet (the upload failed, or another phone holds it), the push
is applied all the same and the row waits: the web shows no picture and a download is `404`,
until the upload comes and the row takes the file. A file that no row refers to within 7 days
of its upload is removed by the nightly purge: a phone that pushes later than that uploads it
again first, with the same id and bytes.

`GET /api/v1/sync/homes/{h}/files/{file_id}`, and `?size=thumbnail` for a small JPEG of an
image (made by the server for its own pages; the app makes its thumbnails itself). The answer
is the file the server keeps, with the type it found: an image inline, anything else as a
download (`Content-Disposition`), with `X-Content-Type-Options: nosniff` and a policy that runs
nothing. The file of a photo or an attachment made on the web is read by the id its reference
gives. The answer is `404` for a file no row holds and never uploaded, or for the thumbnail of
a document, and `400` for another `size`. A read-only member reads the files.

## Errors

Errors are those of the rest of the API, `{"detail": "..."}`, with a `code` the app words
itself:

| Code | Status | When |
| --- | --- | --- |
| `sync_needs_connection` | `403` | An API token, a session or no credential. |
| `malformed` | `400` | A push whose body or one of its operations cannot be read; with `index` for an operation. A new home whose body cannot be read, or with `fields` for a field the web refuses. |
| `too_large` | `400` | A push of more than 500 operations or 1 MiB. |
| `sync_version` | `400` | A push from an app whose schema version is below the server's lowest. |
| `push_conflict` | `409` | A push number used before with another body. |
| `id_taken` | `409` | A new home whose id is held, by a home with other fields or by anything else on the server. |
| `cannot_create_homes` | `403` | A new home sent without the scope `homes.create` or the right to create homes. |
| `wrong_device` | `409` | A device id other than the one the connection pushed with, or one of another connection. |
| `insecure_transport` | `403` | Plain HTTP outside the local network. |
| `sso_required` | `403` | The home requires a sign-in provider the connection was not made with. |
| `cursor_expired` | `410` | `after` is older than the oldest change kept. |
| `forbidden` | `403` | A file uploaded by a read-only member. |
| `file_conflict` | `409` | A file id that holds other bytes, or is held by anything else. |
| `too_large` | `413` | A file above `file_max_bytes`. |
| `quota_exceeded` | `413` | A file the home has no room left for. |
| `unsupported_type` | `415` | A file that is not one the web accepts. |
| `home_gone` | `410` | The home was erased while it was bound to this connection and its account was still a member. To any other connection, the same home is a `404`. |
