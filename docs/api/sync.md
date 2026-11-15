# Sync API

The mobile app keeps its homes on a HouseCairn server through the sync API, under
`/api/v1/sync/` ([ADR 0077](../adr/0077-sync-api-for-the-mobile-app.md)). It exchanges the
operations of the mobile app's protocol, in clear, home by home: the app reads what changed
since its last position, and starts from a snapshot of the home when it has none. This page
describes the reading routes; sending changes comes in a later version.

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

Reads are limited to 600 a minute per connection; beyond that the answer is `429`.

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

## Errors

Errors are those of the rest of the API, `{"detail": "..."}`, with a `code` the app words
itself:

| Code | Status | When |
| --- | --- | --- |
| `sync_needs_connection` | `403` | An API token, a session or no credential. |
| `insecure_transport` | `403` | Plain HTTP outside the local network. |
| `sso_required` | `403` | The home requires a sign-in provider the connection was not made with. |
| `cursor_expired` | `410` | `after` is older than the oldest change kept. |
| `home_gone` | `410` | The home was erased while it was bound to this connection. To any other connection, the same home is a `404`. |
