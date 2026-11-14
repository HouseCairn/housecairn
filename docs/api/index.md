# API

HouseCairn has a REST API, mounted at `/api/v1/`, with the same rules as the
web interface: every request only sees the homes of its user
([ADR 0004](../adr/0004-versioned-rest-api.md),
[ADR 0006](../adr/0006-home-scoping.md)).

- **JSON** for requests and responses, except the upload of a photo or of a file
  of a task, which is `multipart/form-data` (`POST /api/v1/homes/<home id>/photos/`
  and `.../attachments/`), and the image, thumbnail or file themselves, which are
  served as they are.
- **Versioned**: version 1 only gets additive changes; a breaking change will
  go to `/api/v2/`, served alongside version 1 for a deprecation period.
- **Cursor pagination**, up to 200 items per page.
- **Updates** with `PATCH`; `PUT` is not offered.
- **Authentication** by API token, `Authorization: Bearer hc_...`, with the
  scopes `read`, `write` and `events`, bound to one or more homes and expiring
  after 90 days by default
  ([ADR 0007](../adr/0007-authentication-and-api-tokens.md)). A person creates,
  lists and revokes their tokens on the [API tokens](../screens/api-tokens.md)
  screen, or through `/api/v1/me/tokens/` (below). An API token is tied to homes: it can read the routes under
  `/api/v1/me/` where it could, but it cannot change the account, the display
  preferences or the notification channels and reminders of the person. Those are for the
  person signed in, and a token gets a 403 there:
  - `PATCH /api/v1/me/` (the display name);
  - `PATCH /api/v1/me/display-preferences/`;
  - `POST`, `PATCH` and `DELETE` on `/api/v1/me/notification-channels/` and
    `POST .../<id>/test/` (a test sends a message);
  - `PATCH /api/v1/me/notification-preferences/`;
  - `POST /api/v1/account/delete/`;
  - `GET` and `POST /api/v1/me/tokens/` and `DELETE /api/v1/me/tokens/<id>/`: a token
    never lists, creates nor revokes a token.

  `GET /api/v1/me/`, `me/home/` and `me/display-preferences/` stay open to a token;
  the `GET` routes of notifications need the `write` scope
  ([ADR 0045](../adr/0045-generic-lists-with-other-tabs-and-audit-of-content.md)).
- **API tokens** of the person signed in: `GET /api/v1/me/tokens/` lists the tokens that are
  not revoked (name, `prefix`, `scopes`, `homes`, `created_at`, `expires_at`, `last_used_at`,
  never the value). `POST` creates one from `name`, `homes` (ids, among the homes the person
  may open with a token), `scopes` (`read` by default; `read`, `write` and `events` do not
  imply each other) and `expires_in_days` (`30`, `90` or `365`, the lifetime of the instance
  by default; there is no token without an expiry). The answer, `201`, carries the secret in
  `token`: it is shown once, is not kept and cannot be asked again. `DELETE .../<id>/`
  revokes a token, which then fails with `401` on its next request. These routes are for a
  person signed in, with the CSRF token of the session on writes: an API token gets a `403`
  on all of them, and another person's token is a `404`. Creating and revoking go to the
  audit log of the instance, by name, never with the value
  ([ADR 0007](../adr/0007-authentication-and-api-tokens.md)).
- **Rooms, items and photos** of a home: `/api/v1/homes/<home id>/rooms/`,
  `.../items/`, `.../photos/` and `.../search/?q=`, with the same rules as the
  screens ([ADR 0029](../adr/0029-rooms-items-and-room-memory.md),
  [ADR 0030](../adr/0030-photo-storage-and-processing.md)). A token needs the
  `read` scope to read them and `write` to change them, and its user needs a role
  that writes. Rooms and items can be filtered with `q` (name); items with
  `room`; photos with `room`, `item` and `category` (`general`, `behind_walls`, `before`,
  `after` or `other`, [ADR 0045](../adr/0045-generic-lists-with-other-tabs-and-audit-of-content.md)).
  An item carries `contract_contractor` (the id of one of the contractors of the home, or
  `null`) and `contract_number`. Up to 0.1.0 the provider of the contract was a text,
  `contract_provider`; it is gone ([ADR 0058](../adr/0058-contract-provider-is-a-contractor.md)).
- **Task templates** ([ADR 0049](../adr/0049-task-templates.md)):
  `GET /api/v1/task-templates/` returns the library shipped with the instance, every text
  in English and in French, to any person signed in and any token with the `read` scope;
  `POST /api/v1/homes/<home id>/tasks/from-templates/` with `templates` (identifiers),
  and optionally `room` and `due_on`, makes one task per template in the language of the
  request and answers `201` with them; an unknown template is a `400` and makes none. It
  needs a role that writes, and the `write` scope.
- **Tasks** of a home: `/api/v1/homes/<home id>/tasks/`, `.../occurrences/`,
  `.../contractors/` (name, `trade` from a list or `other` with `trade_other`, `phone`,
  `email`, `address`, `note`; `GET .../contractors/<id>/vcard/` gives the contact file) and
  `.../attachments/`
  ([ADR 0031](../adr/0031-tasks-occurrences-and-recurrence.md),
  [ADR 0033](../adr/0033-done-with-undo.md),
  [ADR 0034](../adr/0034-task-attachments.md)). Only `title` is required to create
  a task; `due_on` is the date of its open occurrence. `season` is `spring`, `summer`,
  `autumn`, `winter` (the months follow the `hemisphere` of the home), `custom`
  (with `season_start_month` and `season_end_month`) or empty; `heating`, no longer
  offered, is still accepted and stored as `custom` with the months of the heating season
  (October to April in the north), for tasks and projects;
  `estimated_minutes` is in minutes, from 1 to 525600 (one year), and `estimated_text` reads it;
  `assignee_name` names the person in charge
  ([ADR 0045](../adr/0045-generic-lists-with-other-tabs-and-audit-of-content.md)). Everything the screens do
  is there: `POST .../occurrences/<id>/complete/` marks it done and creates the next
  occurrence (the response holds both), `.../undo/` takes it back, `.../skip/` and
  `.../postpone/` (body `{"due_on": "YYYY-MM-DD"}`) the other outcomes, and `PATCH`
  fills in the date, real cost and note of what was done. Tasks are filtered with
  `status` (`open`, `closed`), `room`, `item`, `assignee`, `due_before`, `due_after`
  `contractor` and `q`, and with `due=none` only the active tasks without a due date
  (an open occurrence with no date; closed tasks are left out, and the filter combines with
  the others and with the cursor); occurrences with the same, plus `task`, `overdue=true` and `status`
  (`planned`, `postponed`, `done`, `skipped`, `open`, `closed`). Occurrences come
  oldest date first, the arrears on top, and the open ones hidden by their task
  (masked until N days before) only appear with `include_hidden=1`. The scopes are
  the same as for rooms: `read` to read, `write` to change, and a role that writes.
- **Me**: `GET` and `PATCH /api/v1/me/` read and change the `display_name` of the person
  signed in (`email` and `id` are read only); `name` is the name shown for them.
  `display_name_source` says who sets the display name (`oidc`: the sign-in provider, at
  each sign-in through it; `manual`: the person, which any change of `display_name` makes
  it); setting it to `oidc` gives the name back to the provider. `display_name_provider`,
  read only, is the identifier of the provider it was taken from (see
  [Profile](../screens/profile.md)).
- **Today, My home, History and display preferences**
  ([ADR 0037](../adr/0037-today-my-home-history-and-display-preferences.md)):
  `GET /api/v1/homes/<home id>/today/` returns the sections of the Today screen as the
  display preferences of the caller lay them out; `GET /api/v1/me/home/` (with an optional
  `home`) what concerns the caller in each home; `GET` and `PATCH
  /api/v1/me/display-preferences/` the layout (`today_group`, `horizon_days`,
  `show_overdue`, `show_upcoming`, `show_season`, `language`), which only its owner can
  read or change (a token reads, the person signed in changes). `language` is a language the instance ships (`en`, `fr`) or empty to
  follow the browser; the web screens apply it from the next page. History is the occurrence list with `status=closed&ordering=recent` and the
  filters `contractor`, `completed_by`, `q`, `closed_after` and `closed_before`;
  `GET .../occurrences/cost/` gives the real cost entered on the same selection.
- **QR labels and reports** ([ADR 0047](../adr/0047-qr-labels.md)):
  `/api/v1/homes/<home id>/labels/` and `.../reports/`. `POST .../labels/` with `kind`
  (`room`, `item` or `article`) and `target` returns the active label of the object (201
  when it was made, 200 when it already had one); `DELETE .../labels/<id>/` revokes it,
  `POST .../labels/<id>/regenerate/` replaces it, and `POST .../labels/print/` with
  `labels` (identifiers) and `layout` (`a4-3x8` or `roll-62`) answers the PDF. Every
  member lists, reads and prints; only a role that writes makes, revokes or regenerates.
  `GET /api/v1/labels/resolve/<code>/` says where a scanned code leads, and answers the
  same 404 for a code that does not exist, one that was revoked and one of a home the
  caller (or the token) is not in. A report is created by any member, a read-only one
  included, with `title` and optionally one of `room`, `item` or `article`, and `photo`
  (`multipart/form-data`); a read-only member lists and reads their own reports only.
  `POST .../reports/<id>/task/`, `.../close/` (body `{"resolution": "resolved"}` or
  `"no_action"`) and `.../reopen/` need a role that writes. Reports are filtered with
  `status` (`open`, `task`, `resolved`, `no_action`, `closed`) and `mine=true`. A token needs `write` for every `POST`, printing
  included, and `read` for the rest.
- **Stock** of a home: `/api/v1/homes/<home id>/articles/`, `.../suppliers/` and
  `.../movements/` ([ADR 0038](../adr/0038-stock-suppliers-and-movements.md)). Only
  `name` is required to create an article; its `quantity` is read-only, the sum of its
  movements, `unit_price` is optional (a string with two decimals, or `null`), and `low` tells whether it is at or below its `threshold`. A movement is
  created with `POST` (`kind` is `entry`, `exit` or `inventory`, with a `quantity`) and
  never changed or deleted. `compatible_items` takes item identifiers of the same home;
  `label` (multipart) sets the photo of the label and `GET .../articles/<id>/label/`
  returns it. Articles are filtered with `q` (name, manufacturer reference or supplier)
  and `low=true`, and `room` for those that concern a room; suppliers and movements with
  `article`, movements also with `kind`. `unit` is a code of the list (`piece`, `litre`,
  `metre`, `kilogram`, `roll`, `box`, `other`) with `unit_other` for `other`, and `rooms`
  takes room identifiers of the same home. The materials of the tasks are
  `.../task-materials/` (`task`, `article`, `quantity`; filtered by `task` and `article`),
  also listed, read-only, in `materials` on a task
  ([ADR 0046](../adr/0046-stock-linked-to-tasks-and-rooms.md)). Closing a task takes them
  from the stock; a movement carries the `occurrence` that wrote it. The scopes are the
  same as for rooms.
- **Projects** of a home ([ADR 0050](../adr/0050-projects-quotes-and-budget-lines.md)):
  `/api/v1/homes/<home id>/projects/`, `.../budget-lines/`, `.../quotes/`,
  `.../project-files/`, `.../project-links/`, `.../project-tasks/` and `.../envelope/`. Only `title` is required
  to create a project; `budget`, on creation only, becomes its first budget line. Every
  amount is a string with two decimals in the currency of the home (`"1250.50"`), from
  `"0.00"` to `"9999999999.99"`; a JSON number (a float or an integer), a negative amount or a
  third decimal is a 400.
  A project and a budget line give `planned`, `committed` (the sum of the quotes accepted
  on it, read-only), `paid`, `gap` and `budget_state` (`over`, `under` or `on`).
  `POST .../quotes/<id>/accept/` with a `budget_line` of the same project accepts a quote
  and commits its amount on that line; a quote takes `contractor` (an id) or
  `contractor_name` (found or created). `project-tasks` takes a `title` (a new task is made
  in the project, with optional `due_on` and `room`) or a `task` (an existing one that is
  in no project); deleting a row takes the task out and keeps it. `project-files` takes
  `file` (multipart), with `quote` for the file of a quote, and `GET .../content/` and
  `.../thumbnail/` return the file. `project-links` takes a `url` (http or https) and an
  optional `title`; when the instance reads previews, the page is read once, by the background worker,
  just after creation (`preview_state`: `running`, then `done` when the page was read, `failed` when it could not be (unreachable, refused, not a
  web page) or has not ended 30 seconds after it started; empty if the instance does not read pages),
  and `title` (if none was given), `image_url` and `thumbnail_url` are filled when it is
  `done`, else `image_url` and `thumbnail_url` are `null`; a link is created, read or deleted, never changed. `GET` and `PUT .../envelope/` read and set the budget
  envelope of a `year`. Projects are filtered with `status`, `priority`, `season` and
  `room`, the other lists with `project`. The scopes are the same as for rooms.
- **Budget** ([ADR 0051](../adr/0051-budget-views-and-annual-envelope.md)), figures only,
  read-only (`GET`, the `read` scope): `/api/v1/homes/<home id>/budget/` (the envelope of a
  year, then `by_nature`, `by_year` and the last 12 months as `rolling`),
  `.../budget/rooms/`, `.../budget/projects/` and `.../budget/tasks/`, and `/api/v1/budget/`
  for all your homes added up by currency, with no conversion. `?year=` picks the year (this
  year by default; a year that is not one is a 400) and `?export=csv` answers with the same
  figures as a file. Every amount is a string with two decimals; a row gives `planned`,
  `committed`, `paid`, `engaged` (shown as *Used*: the more of committed and paid, line by
  line), `gap` (negative when exceeded), `to_pay` and `budget_state`. `sure_budget` of the
  envelope is always `null`. A line of `.../budget/tasks/` gives `estimate` (each time, the
  stock items at their unit price included), `stock_estimate` (the part of it that comes from
  the stock) and `unpriced_materials` (the stock items of the task with no unit price, which
  count for nothing; [ADR 0062](../adr/0062-unit-price-of-stock-items.md)). A home that is not yours, or that your token is not bound to, is
  a 404.
- **Notifications** of the caller
  ([ADR 0043](../adr/0043-notifications-through-apprise.md)):
  `/api/v1/me/notification-channels/` lists, adds (`name` and `url`, an Apprise address),
  renames or turns off (`PATCH` of `name`, `enabled`; any other field, `url` included, is
  a 400) and deletes the caller's channels; `url` is write-only and never returned.
  `POST .../<id>/test/` queues a real test message for the background worker and answers
  `202` with `{"state": "running", "detail": ...}` (a test already running is not started
  twice); the channel's `test_state` then goes from `running` to `sent`, `failed` or
  `private` (refused: private network), and is `failed` if the test has not ended 30
  seconds after it started ([ADR 0070](../adr/0070-slow-network-calls-in-the-background.md)). `GET` and `PATCH
  /api/v1/me/notification-preferences/` the reminders (`days_before`, `send_at`,
  `quiet_start`, `quiet_end`, `digest`, `time_zone`; the language is not one of them, it is read
  and changed with the display preferences at `/api/v1/me/display-preferences/`), and `GET
  /api/v1/me/notifications-export/` both, without any address. A token with the `write`
  scope reads them (any other token gets a 403); adding, renaming, deleting and testing a
  channel and changing the reminders are for the person signed in, and a token gets a 403
  there. Someone else's channel is a 404.
- **Calendar** ([ADR 0052](../adr/0052-calendar-and-secret-ics-feeds.md)):
  `GET /api/v1/calendar/?from=YYYY-MM-DD&to=YYYY-MM-DD` returns the occurrences of the
  caller's homes (at most 400 days; `truncated` tells when the limit of 5 000 entries, or of 1 000 repeating
  tasks, cut the answer, always on the farthest dates) with the filters `home`, `room`, `person` (an id, or `none`) and `priority`. Dates of a
  task that repeats and has no occurrence yet are returned with `projected: true` and
  `occurrence: null`. A token needs the `read` scope and only sees the homes it is bound to;
  `can_complete` is false for a reader and for a token without the `write` scope. `/api/v1/me/calendar-feeds/` lists, creates (`home`, or
  empty for all homes), regenerates (`POST .../<id>/regenerate/`) and deletes the caller's
  [feeds](../guides/calendar.md); the secret `url` is in the answer that creates or regenerates a
  feed and nowhere else. These endpoints are for a person signed in: **an API token of any
  scope is refused**, since an address read with a token would outlive it.

The [reference](reference.md) lists every operation. It is generated from the
OpenAPI 3.1 schema of the code at each build of this site:
{download}`download the schema (openapi.json) </_generated/openapi.json>`.
A running instance serves the same schema at `/api/schema/` and an
interactive explorer at `/api/docs/`, both to signed-in users only.

The [guide](guide.md) shows the API at work with `curl`: a token, reads, writes, the
pages of a list and the errors.

```{toctree}
:maxdepth: 1

guide
reference
```
