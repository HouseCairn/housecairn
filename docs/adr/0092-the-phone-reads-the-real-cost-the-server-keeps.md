# 0092. The phone reads the real cost the server keeps

- Status: accepted. That the server sends these values to the phone, read only, was decided on
  2026-11-21 for the maintainer, who reviews it with the pull request of this record; how the
  feed carries them and how the values of before reach a phone already in step are what this
  record decides with it.
- Date: 2026-11-21
- Amends: [0090](0090-real-cost-counts-the-stock-used.md) and
  [0091](0091-a-task-done-with-nothing-typed-pays-its-plan.md) ("not a field of the sync
  protocol": the unit price of a draw and the plan of an occurrence)
- Related: [0077](0077-sync-api-for-the-mobile-app.md) (the fields of the protocol, the change
  log), [0079](0079-lots-of-the-server-mode-of-the-mobile-app.md) (the repair rules of a push),
  mobile ADR 0003 (fields a phone does not know)

## Context

Since 0090 and 0091 the real cost of a done task is the stock it used, valued at the unit price
of each article when it was taken (`MaterialDraw.unit_price`), plus the amount typed, or with
nothing typed the plan kept when it was marked done (`Occurrence.planned_labour` and
`planned_material`). Both records left the three columns out of the sync protocol, so that a
phone could not write them. The phone then never received them either: the mobile app could
only count `actual_cost`, and a task done with stock or with a plan showed no cost there while
the web showed one. It was decided on 2026-11-21 that the server stays the only writer of these
values and sends them to the phone, read only.

## Decision

- **Three fields of the protocol, read only.** `tasks.materialdraw.unit_price`,
  `tasks.occurrence.planned_labour` and `tasks.occurrence.planned_material` are in the changes
  and the snapshot, amounts as decimal strings or `null`, like any field. A model declares such
  columns in `rows.Spec.read_only`: they are sent, and a push that sets one is still rejected
  with `forbidden`, as any column of the server.
- **Every value the server sets is a change.** On the web the closing, the undo and the draws
  already wrote through `sync.rows`, which now records these columns with the others. In a push
  the server sets them itself: the price of a draw a phone made or brought back
  (`Session._settle_draw`), the plan of an occurrence a push made done or not done
  (`Session._kept_plans`). Each is recorded as a change of the server, with its clock, after
  the phone's own change, so that the phone that pushed reads the value in its next page and
  the clock of the server wins on every side. Such a change is not a repair: the result of the
  operation stays `applied`.
- **The values of before are sent once.** The rows that held a value before this version (set
  by a closing, or by the migrations of 0090 and 0091) were never in the feed, and a phone past
  them would never read them. The migration `sync.0006` writes, for each live draw with a price
  and each live occurrence with a plan, one change of the server with those fields after the
  head of its home, the clocks of the fields with them. A phone in step reads them in its next
  page of changes; nothing makes it start again from the snapshot (410), which would cost every
  phone a full download for three fields. The example home of the guide gets none (0084).
- **No new version.** Adding fields is the change the protocol allows within one version
  (mobile ADR 0003: the schema only grows): the schema version `sv`, the lowest version the
  server accepts and `HC-Sync` stay as they are. An app that does not know the fields keeps them
  apart (`extra`), never shows them and never sends them back; it counts `actual_cost` only, as
  before.

## Consequences

- The mobile app can show the real cost by the rule of the web
  ([Sync API, The real cost](../api/sync.md#the-real-cost)), and must keep these fields read
  only: an operation that sets one is kept aside as `forbidden`.
- A closing pushed by a phone gives one or two more changes in the feed (the price of each new
  draw, the plan), and the update of the server one change per done occurrence with a plan and
  per draw with a price, once.
- The clock the server gives these fields is after the phone's write: a phone that applies the
  feed takes the server's value, whatever it wrote.
