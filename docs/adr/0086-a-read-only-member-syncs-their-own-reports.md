# 0086. A read-only member syncs their own reports only

- Status: accepted
- Date: 2026-11-17
- Amends: [0077](0077-sync-api-for-the-mobile-app.md) (the feed, the snapshot and the files)
  and [0085](0085-photos-of-reports-and-labels-of-articles-as-sync-files.md) ("Who reads")
- Related: [0047](0047-qr-labels.md) (reports, and who sees them),
  [0079](0079-lots-of-the-server-mode-of-the-mobile-app.md) (starting again after 410)

## Context

On the site, a read-only member sees the reports they made and no others
(`reports.services.visible_reports`): a tenant who reports a leak does not read what the other
people of the home reported. The sync API did not follow that rule. Its feed, its snapshot and
its files gave the phone of a read-only member every report of the home, with its photo, so the
mobile app showed them all.

The maintainer decided on 2026-11-17 to correct this on the server: the sync API gives a
read-only member their own reports only, by exactly the rule of the site.

## Decision

**One rule, read by both.** `reports.services.visible_among(actor, home, reports)` narrows any
set of reports of a home to those the person may see: every report for a member who writes
(`sees_every_report`), their own for anyone else. `visible_reports`, the site's, reads it on the
live reports; the sync API reads it on every report the server keeps, deleted ones included, so
that a phone gets the tombstone of its own report as it got the report (`sync.visibility`).
Nothing else of the home is narrowed: every member gets every other row.

**What leaves the server.** For a read-only member:

- the **feed** leaves out every change of a report they may not see, in the query, so that a
  page holds `limit` changes they read and `more` says whether another follows: a page is never
  empty while `more` is true, and paging gives the same changes as one long page, none lost, none
  twice;
- the **snapshot** has no line for such a report, its tombstone neither;
- the **file** of its photo, and its thumbnail, answer `404`, the same answer as an id nobody
  holds, through every home of the connection.

**A task made from a hidden report names it nowhere.** The link between the two is on the report
(`task_id`); a task has no field that points at a report. The reader gets the task, as the site
shows it to them, and not the report, so nothing of it (title, place, photo, text) reaches the
phone. The mobile app finds the reports of a task by the `task_id` of the reports it holds: it
finds none and shows none, without an error.

**A view that changes starts the phone again.** A member who stops writing has received the
reports of the others; one who starts writing has not. When the view of a member changes (an
owner makes them read-only, or a member again, or they join the home again by an invitation),
`sync.changes.view_changed`, in the same transaction, moves the head of the home on by one
position **with no change** and records that position in `sync.MemberView` as `since`. A cursor
below `since` answers `410` `cursor_expired`. The phone then does what ADR 0077 and the mobile
protocol (section 2.10, "Starting again after 410") already say: it reads the snapshot of its new
view, drops every row it had from the server that the snapshot lacks, keeps and sends again what
it never sent, and reads the feed after `covers`. No change of the app is needed: the same rule
handles a cursor older than the retention.

- The new position is needed: without it, a phone whose cursor is the head at the time of the
  change, the usual case, would hold a cursor equal to `since` and never start again.
- `since` is read after the head, in the same transaction for the snapshot: the change of role
  commits its view and its position together, so a page that holds the new head was read with
  the new view, and a snapshot never covers `since` with the old one.
- A change between owner and member keeps the view (both write) and starts nobody again; nor does
  the change of another member's role.
- The migration of this record does it once for every read-only member of a real home: their
  phones were given every report until now.

The app tells the person, once per row it drops, "Something deleted on the server was removed
from this phone.": the words of a row deleted on the server, which says nothing of the hidden
report.

**What a reader can still tell.** The positions of the feed are those of the home. The changes
of the hidden reports leave gaps between the positions a reader gets, and the `head` of a page
and of the list of homes moves past the last change they got. A reader can thus tell that
something they do not see changed, and when, not what nor which row. Avoiding it would need a
numbering per member, which the cursor shared with the push and the list of homes does not have.
This record accepts it: no title, place, photo or id of a hidden report leaves the server.

## Consequences

- `tests/test_sync_read_only_reports.py` holds one test per behaviour: the feed, its pages, the
  tombstones, the snapshot, the files, the task made from a hidden report, the changes of role
  both ways, a cursor at the head, joining again, a member who writes, an administrator of the
  instance who is not a member, another home, and the migration.
- A position may now have no change (`view_changed`); the positions still never repeat.
- The mobile app keeps working as it is; the comment of its `reportsOfTask` that describes the
  former gap with the web is out of date and is corrected on its side.
