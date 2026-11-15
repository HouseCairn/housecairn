# Sync API

The mobile app keeps its homes on a HouseCairn server through the sync API, under
`/api/v1/sync/` ([ADR 0077](../adr/0077-sync-api-for-the-mobile-app.md)). It exchanges the
operations of the mobile app's protocol, in clear, home by home: the app reads what changed
since its last position, starts from a snapshot of the home when it has none, and pushes what
was typed on the phone. Creating a home from the phone and sending files come in a later
version.

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

Reads are limited to 600 a minute per connection, and pushes to 60 a minute per connection,
counted apart; beyond that the answer is `429`.

## Values

The operations and rows are those of the mobile protocol: entity types `<app>.<model>` in
lower case (`rooms.room`), field names those of the models with foreign keys by their column
name (`room_id`), amounts as decimal strings, dates `YYYY-MM-DD`. A **clock** is a hybrid
logical clock, 16 lower-case hexadecimal digits; a **device** is the id of the device that
wrote, 16 bytes in base64url without padding. The server writes the changes made on the web
as one device, whose id `GET /api/v1/sync/info` gives. A file is given as a reference named by
the id of its row, never by the name of the file the server stores.

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
- `members`: the accounts of the home, with the name shown for them and their role, never
  their address. The rows name a person by this `id` (`assignee_id`, `completed_by_id`).
- `gone`: the homes erased while they were bound to this connection, and when. The app erases
  them on the phone.

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
the other phones; a file reference is kept the same way until the server stores the files of
the phone. The server writes who acted, whatever the operation says: `completed_by_id` and
`closed_by_id` are the account of the phone, and so is `reporter_id` of a new report, which no
later operation changes. `assignee_id` may name any member of the home.

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

## Errors

Errors are those of the rest of the API, `{"detail": "..."}`, with a `code` the app words
itself:

| Code | Status | When |
| --- | --- | --- |
| `sync_needs_connection` | `403` | An API token, a session or no credential. |
| `malformed` | `400` | A push whose body or one of its operations cannot be read; with `index` for an operation. |
| `too_large` | `400` | A push of more than 500 operations or 1 MiB. |
| `sync_version` | `400` | A push from an app whose schema version is below the server's lowest. |
| `push_conflict` | `409` | A push number used before with another body. |
| `wrong_device` | `409` | A device id other than the one the connection pushed with, or one of another connection. |
| `insecure_transport` | `403` | Plain HTTP outside the local network. |
| `sso_required` | `403` | The home requires a sign-in provider the connection was not made with. |
| `cursor_expired` | `410` | `after` is older than the oldest change kept. |
| `home_gone` | `410` | The home was erased while it was bound to this connection. To any other connection, the same home is a `404`. |
