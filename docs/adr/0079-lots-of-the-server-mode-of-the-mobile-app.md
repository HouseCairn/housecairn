# 0079. Lots of the server mode of the mobile app: archiving, OAuth, then the sync API

- Status: proposed. That the web lot starts now, alongside the mobile app, that the server
  mode is required before the app's public beta, and the answers written in
  [Decisions of the maintainer](#decisions-of-the-maintainer-2026-11-14) are the maintainer's
  decisions of 2026-11-14; the cut into lots and their order are what this record proposes.
- Date: 2026-11-14
- Related: [ADR 0077](0077-sync-api-for-the-mobile-app.md) (sync API),
  [ADR 0078](0078-oauth-2-with-pkce-for-the-mobile-app.md) (OAuth 2 with PKCE), mobile ADR
  0010 and its plan ([housecairn-mobile#11](https://github.com/HouseCairn/housecairn-mobile/pull/11)),
  where lot 4b, "sync with a Housecairn server", waits for these lots.

## Context

The web repository has no plan file: its lots are cut by the maintainer, one pull request
each. The server mode needs thirteen of them (six lots, two of them cut in several pull
requests), in two repositories besides this one, and the mobile
app plans its own lot 4b on top. This record writes the cut down once, so that each lot can
be started with its dependencies known. One pull request per lot; two lots run side by side
only when they touch different apps, with no migration in the same app, and when the files
they share only receive additions.

## Decision

### Lots of this repository

| Lot | Content | Depends on | Runs next to |
| --- | --- | --- | --- |
| **M1. Archiving of homes** | `homes.Home.archived_at`; archive and unarchive in the home's settings, by an owner only ([decisions](#decisions-of-the-maintainer-2026-11-14)); an archived home is listed apart on **Homes**, hidden from the home switcher, Today, My home and search, sends no reminder, and its calendar feeds stay valid but empty; it stays readable and editable when opened. Resources: `archived_at` read only on homes, `POST /api/v1/homes/{id}/archive/` and `unarchive/` for owners (additive); `GET /api/v1/homes/` **keeps listing archived homes**, with the field, so the Home Assistant integration sees the homes it saw. Export and restore carry the field: the export format goes to version 4 ([ADR 0048](0048-export-format-and-restore.md)), and archives of formats 2 and 3 are still restored, with `archived_at` empty. Docs: screens of the home and of Homes, export format, release note. | nothing | M2 |
| **M2. OAuth 2 with PKCE, connected apps** | ADR 0078: django-oauth-toolkit at `/o/` with the settings of its table, the public client `housecairn-mobile`, the consent page, `apitokens.AppConnection`, **Connected apps** on `/me/tokens/` (edit, revoke), the instance setting **Mobile app** (on), the transport rule on `core.netguard`, prefixes `hca_` and `hcr_`, `ConnectionAuthentication`, `credential_of`, `CredentialFitsView` and the twenty sites rewritten, the test of the boundary, the security email, the audit entries, and `GET /api/v1/sync/info`. **Where `info` lives**: a view of the `apitokens` app (it describes the client and the connection), routed by one line `sync/info` in `api_urls.py`, with `sync_api = True`; M2 creates no `sync` app and no `sync/urls.py`, so M3a creates the app and M4 adds its routes by one `include` line after `info`, with no conflict. ADR 0001 gains its two packages. Docs: the API tokens screen, sign-in and security, reverse proxy, instance settings. | nothing | M1 |
| **M3a. Change log: the `sync` app** | A new app `sync`: the change position per home (`pos`) under the lock of the home's row, the changes, the field clocks with `extra`, the server's hybrid clock and its device id of 16 bytes (made by the app's first migration), the table of erased homes with the connections bound to them, the function every write path calls to record a change, the clock read from `updated_at` for a field that has none, the nightly retention purge (400 days). The guard test, which lists the models of the homes and fails when a write of a model of an app in its list `COVERED_APPS` records no change; M3a leaves the list empty. Erasing a home writes its row in the table of erased homes (`homes/services.py`, the only service of another app it touches). | M1 merged | nothing |
| **M3b. Change log: homes and rooms** | Every write path of `homes` (the home itself) and `rooms` (room, item, photo) records its change, for the fields whose value changed only; their queryset soft deletions write tombstones; their apps are added to `COVERED_APPS`. | M3a merged | M3c, M3d, M3e |
| **M3c. Change log: tasks** | The same for `tasks` (contractor, task, task material, occurrence, material draw, attachment), and the hard deletion of a successor by an undo replaced by a tombstone. | M3a merged | M3b, M3d, M3e |
| **M3d. Change log: stock and reports** | The same for `stock` (article, supplier, movement, rooms and compatible items of an article) and `reports`. | M3a merged | M3b, M3c, M3e |
| **M3e. Change log: projects** | The same for `projects` (project, budget line, quote, file, link, project task, annual envelope, rooms of a project), and the hard deletion of a task detached from a project replaced by a tombstone. | M3a merged | M3b, M3c, M3d |
| **M3f. Deterministic ids** | The ids of the table of ADR 0077 need 2 in `_spawn_next`, material lines, draws, the annual envelope and the link rows, recomputed by a restore as a copy; the vectors `tests/fixtures/sync_ids.json`. | M3c, M3d and M3e merged (it touches tasks, stock and projects) | nothing |
| **M4. Sync API, reading** | `GET /api/v1/sync/homes/` (homes of the connection, members, `gone`), `GET .../changes`, `GET .../snapshot`, 410 `cursor_expired` and `home_gone`; the routes in `sync/urls.py`, one `include` line in `api_urls.py`; the schema and the API pages of the documentation. | M2 and M3a to M3f merged | nothing |
| **M5a. Sync API, writing: push** | `POST .../push` for plain data of every type: the per-field rule, the losing values and long texts returned, `extra` kept, the push numbers run once per connection (`push`, 30 days), the results and their codes (`forbidden` with `archived_at` by a member who is not an owner, `unknown_type`, `invalid_reference`, `wrong_home`, `id_taken`), read-only members, `home_gone`. Tests replay the merge vectors of the mobile repository. | M4 merged | nothing |
| **M5b. Sync API, writing: rules and effects** | The repair rules of mobile ADR 0003 before the constraints, each a change of its own; closing an occurrence as data checked against recurrence, with the effects of the web run once by the deterministic ids (audit, stock that follows Done); the undo reverted when the successor was edited. | M5a merged | M5c |
| **M5c. Sync API, writing: homes** | `POST /api/v1/sync/homes/` with the phone's id under `homes.create`, the account its owner, the home bound to the connection. Adds one route to `sync/urls.py` and touches no service M5b touches. | M5a merged | M5b |
| **M6. Sync API, files** | `PUT` and `GET .../files/{file_id}` under the phone's id, repeatable, 413 above `file_max_bytes`, re-encoding of images as today, orphans removed after 7 days, file references in the operations as ADR 0077 maps them. | M5b and M5c merged | nothing |
| **Release** | A release of the application with M1 to M6, so that mobile lot 4b and the Home Assistant app can name a version. | M6 merged | |

Models for the cards, by the project's rule: M2 is authentication, so the default model; the
others are application code, with a review by another model. M3a, M3f and M5a to M5c carry
the rules shared with the phone: their review checks each rule against mobile ADR 0003 line by
line.

Order: **M1 and M2 now, side by side**; M3a after M1; M3b to M3e side by side after M3a (each
touches the services of its own apps, none adds a migration to an app another one changes,
and the only file they share, the guard test, gets one line added per app to
`COVERED_APPS`); M3f after M3c, M3d and M3e; M4 after M2 and every M3; M5a; M5b and M5c side
by side; M6 and the release. The mobile app's lot 4b can be written against M4 as soon as it
is merged, and finished against the release.

### What changes elsewhere

- **Home Assistant app** (`HouseCairn/home-assistant-addons`): the app cannot go through
  Ingress, which requires a Home Assistant session that a Housecairn token cannot give. It
  reaches the add-on on its own port (`8000/tcp`, off unless the person gives it a number),
  in plain HTTP on the household's network by the add-on's IP address, which ADR 0078 accepts
  between local addresses (not by a `.local` name), or in HTTPS. **The add-on serves HTTPS on
  that port as an option** (the maintainer's decision), with the certificate and key Home
  Assistant keeps in `/ssl` (the usual `ssl`, `certfile` and `keyfile` options of an add-on),
  so the phone reaches it in HTTPS on the household's network and from outside; without the
  option, the port stays plain HTTP for the local network, and access from outside goes
  through a reverse proxy the person sets up. The add-on must then: say in its documentation
  that the port is the way of the mobile app, with `allowed_hosts` and `site_url` set to the
  address the phone uses (the OAuth pages are built on it); accept the consent form posted on
  that address; and offer the HTTPS option. Its version follows the release above (its
  decision D4). An issue goes to that repository when M2 is merged, with the variables M2
  actually added.
- **Home Assistant integration** (`HouseCairn/home-assistant-housecairn`): nothing is required.
  The resources it calls do not change; M1 adds `archived_at` to a home, which it may use later
  to hide an archived home.
- **Mobile app** (`HouseCairn/housecairn-mobile`): lot 4b uses the routes of ADR 0077 and the
  client of ADR 0078 (scheme `app.housecairn.mobile:/oauth/callback`, `client_id`
  `housecairn-mobile`); its record 0010 says "a Housecairn API token" and becomes "a sign-in
  in the browser" when this record is accepted.

## Decisions of the maintainer (2026-11-14)

The five questions this record first asked were answered by the maintainer the same day, and
two more points were decided with them. ADR 0077 and 0078 are written with these answers.

1. **Deletions stay visible 400 days**, then the row, its tombstone and the changes are purged;
   an older cursor gets 410 and the phone starts again from the snapshot by the reconciliation
   rule of ADR 0077 ([Starting again after 410](0077-sync-api-for-the-mobile-app.md#starting-again-after-410)):
   a row the server never saw is sent again, a row it saw and the snapshot lacks is deleted on
   the phone, waiting changes are replayed field by field. Not chosen: forever (the tables only
   grow), 90 days (a phone left in a drawer for a season starts again).
2. **The mobile app's client is on by default** on every instance, with an instance setting
   **Mobile app** to turn it off (ADR 0078); it does nothing until a person consents.
3. **Only the owners of a home archive it**, as they alone delete it (M1). An `archived_at`
   pushed by the phone of a member who is not an owner is rejected with `forbidden`; the HLC of
   the operation becomes a date and time on the web and is given back as it was sent
   (ADR 0077 need 9).
4. **Refresh tokens are hashed, rotated, and a reuse revokes the connection, with no grace
   period**: a phone that loses the answer of a refresh signs in again (ADR 0078, with the
   settings of django-oauth-toolkit 3.4.1 that do it). Not chosen: a grace period of two
   minutes, which needs tokens stored in clear.
5. **The Home Assistant app serves HTTPS on its port, as an option**, with the certificate Home
   Assistant keeps in `/ssl` ([What changes elsewhere](#what-changes-elsewhere)).
6. **The server keeps the fields it does not know** (`extra`) and gives them to the other
   phones, as protocol section 2.2 requires of every receiver (ADR 0077 need 11); the first
   draft, which dropped them and listed them in `unknown`, is replaced.
7. **M3 and M5 are cut now**: M3a to M3f and M5a to M5c above, one pull request each.

## Consequences

- The server mode reaches the phones only after thirteen web pull requests and a release:
  before the beta of the app, as decided, so the beta waits for them.
- M3b to M3e touch the services of every app between them: while one of them is open, no
  other lot that changes the services of its apps runs beside it. The guard test arrives in
  M3a, and each later lot adds its apps to `COVERED_APPS`, so the test protects what is
  covered from the day it is covered; M4 starts only when every app of the homes is listed.
- Each lot updates the "Status" line of ADR 0077 or 0078 when it changes something they
  decide, in the same pull request.
