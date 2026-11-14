# 0079. Lots of the server mode of the mobile app: archiving, OAuth, then the sync API

- Status: proposed. That the web lot starts now, alongside the mobile app, and that the server
  mode is required before the app's public beta are the maintainer's decisions of 2026-11-14;
  the cut into lots and their order are what this record proposes.
- Date: 2026-11-14
- Related: [ADR 0077](0077-sync-api-for-the-mobile-app.md) (sync API),
  [ADR 0078](0078-oauth-2-with-pkce-for-the-mobile-app.md) (OAuth 2 with PKCE), mobile ADR
  0010 and its plan ([housecairn-mobile#11](https://github.com/HouseCairn/housecairn-mobile/pull/11)),
  where lot 4b, "sync with a Housecairn server", waits for these lots.

## Context

The web repository has no plan file: its lots are cut by the maintainer, one pull request
each. The server mode needs six of them, in two repositories besides this one, and the mobile
app plans its own lot 4b on top. This record writes the cut down once, so that each lot can
be started with its dependencies known. One pull request per lot; two lots run side by side
only when they touch different apps, with no migration in the same app, and when the files
they share only receive additions.

## Decision

### Lots of this repository

| Lot | Content | Depends on | Runs next to |
| --- | --- | --- | --- |
| **M1. Archiving of homes** | `homes.Home.archived_at`; archive and unarchive in the home's settings, by an owner ([question](#open-questions)); an archived home is listed apart on **Homes**, hidden from the home switcher, Today, My home and search, sends no reminder, and its calendar feeds stay valid but empty; it stays readable and editable when opened. Resources: `archived_at` read only on homes, `POST /api/v1/homes/{id}/archive/` and `unarchive/` (additive). Export and restore carry the field. Docs: screens of the home and of Homes, release note. | nothing | M2 |
| **M2. OAuth 2 with PKCE, connected apps** | ADR 0078: django-oauth-toolkit at `/o/`, the public client `housecairn-mobile`, the consent page, `apitokens.AppConnection`, **Connected apps** on `/me/tokens/` (edit, revoke), the HTTPS rule, prefixes `hca_` and `hcr_`, the security email, the audit entries, `GET /api/v1/sync/info` (so that a connection can be tested end to end before the sync exists). ADR 0001 gains its two packages. Docs: the API tokens screen, sign-in and security, reverse proxy. | nothing | M1 |
| **M3. Change log** | ADR 0077 needs 2 to 5, without a public route: a new app `sync` with the change sequence per home, the changes, the field clocks, the server's hybrid clock and device id; every write path of a home's rows records its change, for the fields whose value changed only; queryset soft deletions and the hard deletions of today write tombstones; the table of erased homes; the deterministic ids of mobile ADR 0003 on the web (successor, material line, draw, envelope, link rows), recomputed by a restore as a copy; clocks given once to the rows that exist; the nightly retention purge. The guard test that fails when a write records no change. | M1 merged (its field is then covered from the start) | nothing: it touches the services of every app |
| **M4. Sync API, reading** | `GET /api/v1/sync/homes/` (homes of the connection, members, `gone`), `GET .../changes`, `GET .../snapshot`, 410 on an expired cursor; the schema and the API pages of the documentation. | M2 and M3 merged | nothing |
| **M5. Sync API, writing** | `POST .../push`: the per-field rule, the losing values and long texts returned, the repair rules before the constraints, data written with the effects of the web run once, batches run once per connection; `POST /api/v1/sync/homes/` with the phone's id and `homes.create`; read-only members. Tests replay the merge vectors of the mobile repository. | M4 merged | nothing |
| **M6. Sync API, files** | `PUT` and `GET .../files/{file_id}` under the phone's id, repeatable, 413 above `file_max_bytes`, re-encoding of images as today, orphans removed after 7 days, file references in the operations. | M5 merged | nothing |
| **Release** | A release of the application with M1 to M6, so that mobile lot 4b and the Home Assistant app can name a version. | M6 merged | |

Models for the cards, by the project's rule: M2 is authentication, so the default model; the
others are application code, with a review by another model. M3 and M5 carry the rules shared
with the phone: their review checks each rule against mobile ADR 0003 line by line.

Order: **M1 and M2 now, side by side**; M3 after M1; M4 after M2 and M3; then M5, M6 and the
release. The mobile app's lot 4b can be written against M4 as soon as it is merged, and
finished against the release.

### What changes elsewhere

- **Home Assistant app** (`HouseCairn/home-assistant-addons`): the app cannot go through
  Ingress, which requires a Home Assistant session that a Housecairn token cannot give. It
  reaches the add-on on its own port (`8000/tcp`, off unless the person gives it a number),
  in plain HTTP on the household's network, which ADR 0078 accepts on a local address, or
  through a reverse proxy with HTTPS for access from outside. The add-on must then: say in its
  documentation that the port is the way of the mobile app, with `allowed_hosts` and
  `site_url` set to the address the phone uses (the OAuth pages are built on it); accept the
  consent form posted on that address; and perhaps serve HTTPS on the port
  ([question](#open-questions)). Its version follows the release above (its decision D4). An
  issue goes to that repository when M2 is merged, with the variables M2 actually added.
- **Home Assistant integration** (`HouseCairn/home-assistant-housecairn`): nothing is required.
  The resources it calls do not change; M1 adds `archived_at` to a home, which it may use later
  to hide an archived home.
- **Mobile app** (`HouseCairn/housecairn-mobile`): lot 4b uses the routes of ADR 0077 and the
  client of ADR 0078 (scheme `app.housecairn.mobile:/oauth/callback`, `client_id`
  `housecairn-mobile`); its record 0010 says "a Housecairn API token" and becomes "a sign-in
  in the browser" when this record is accepted.

## Open questions

For the maintainer, each with the option these records assume until answered.

1. **How long does a deletion stay visible to the phones?** Assumed: 400 days, then the row and
   its tombstone are purged and an older cursor starts again from the snapshot (ADR 0077).
   Other options: forever (the tables only grow), or 90 days (a phone left in a drawer for a
   season starts again from the snapshot).
2. **Is the mobile app's client on by default on every instance?** Assumed: yes, with an
   instance setting to turn it off; it does nothing until a person consents. Other option: off
   until an administrator turns it on, as for the integrations.
3. **Who may archive a home?** Assumed: its owners, as for deleting it (M1). Other option: any
   member who may write, as for the other fields of a home.
4. **Refresh tokens: hashed with no grace period, or a grace period with tokens in clear?**
   Assumed: hashed, rotated, a reuse revokes the connection, so a phone that loses the answer
   of a refresh signs in again (ADR 0078). Other option: a grace period of two minutes, which
   django-oauth-toolkit allows only with tokens stored in clear.
5. **Does the Home Assistant app serve HTTPS on its port?** Assumed: yes, as an option with the
   certificate Home Assistant keeps in `/ssl`, so that the phone reaches it in HTTPS on the
   household's network and from outside. Other option: the port stays plain HTTP, the phone uses
   it on the local network only, and access from outside goes through a reverse proxy the person
   sets up.

## Consequences

- The server mode reaches the phones only after six web lots and a release: before the beta of
  the app, as decided, so the beta waits for them.
- M3 touches the services of every app: no other lot that changes those services runs beside
  it, and it is cut into several pull requests per app if its review gets too long, with the
  guard test in the first one, marking the apps not yet covered.
- Each lot updates the "Status" line of ADR 0077 or 0078 when it changes something they
  decide, in the same pull request.
