# 0047. QR labels and reports: an opaque code, a PDF made on the server, a reader who reports

- Status: accepted
- Date: 2026-11-08

## Context

Stickers on the boiler, the water meter and the shelf of the cellar answer "what is this
and what do I do about it" for the people who live in the house and for the ones who only
visit it. A sticker is stuck on a wall for years and is read by whoever holds a phone, so
what it carries is public the day it is photographed. The same people (a tenant, a
tradesman, a guest with a read-only role) often see a problem before the person who
runs the home, and have no right to write anything else.

## Decision

**Two new apps, `labels` and `reports`.** Neither adds a migration to `tasks`, `rooms`,
`stock` or `homes`. Their hooks in those apps are one line each: a block of buttons on the
page of a room, an item and a stock item, a row on the home page, one line on Today. The
creation of a task from a report calls `tasks.services.create_task`; the alert calls the
new module `notifications.alerts`, which is an addition to `notifications`.

**The code is opaque.** A label has a code of 16 random bytes (128 bits), from
`secrets.token_urlsafe`, which is 22 URL-safe characters. It holds no identifier of the
object or of the home, no name, no date: it cannot be derived from anything, and it names
nothing. It is unique in the whole table, revoked rows included, so a printed code never
comes back to life nor moves to another object. A label can be revoked, or regenerated
(revoked and replaced in one transaction); an object has at most one active label, which
the database enforces with a partial unique constraint. The target is a room, an item or a
stock item: three nullable foreign keys, with a check constraint that exactly one is set
and matches the kind. The label of a stock item prints its storage location where a room
label prints nothing: the stock has no table of places, so the place is a line of text on
the item and not an object of its own.

**Scanning.** A sticker holds `<public address of the instance>/l/<code>/`. The address is
`site_url` when the administrator has set it, else the host of the request that prints:
documentation says to set it before printing. Signed out, the person signs in and comes
back (`next`). Signed in, a member of the home goes to the page of the object. For every
other case the answer is the same 404, byte for byte except the anti-forgery token: a code
never issued, a revoked one, one of a deleted object, one of a home the person is not in,
one that is not even shaped like a code, and an administrator of the instance who is not a
member (ADR 0022). The lookup is one query bound to the homes of the person. There is no rate limit: 128 bits cannot be guessed.
A member of a home that requires another sign-in (ADR 0021) is told which one, as on
every page of that home. The API resolves a code with `GET /api/v1/labels/resolve/<code>/`,
same rules, same 404.

**Universal link.** The address is chosen to be an iOS universal link: a native
application can claim `/l/*`. The file `/.well-known/apple-app-site-association`, with
`applinks` details for `/l/*`, is for the application's release and is not served yet;
nothing is added for iOS here.

**Print format, a PDF made on the server.** Two layouts: an A4 sheet of 3 by 8 labels of
63.5 by 33.9 mm (the common format of adhesive sheets), and a 62 mm roll for a label
printer of the Brother QL kind, one label per page. A label shows the code, the name of
the object and where it is. The text is set in Helvetica, which every reader has, so
nothing is embedded or fetched; a character that font has no glyph for (Windows Latin-1
is its encoding) prints as `?`, and the code still opens the page. Up to 200 labels per
PDF. The PDF is made in memory and sent as a download with `Cache-Control: private,
no-store`: nothing is stored, so the quota of files (ADR 0042) is not touched. The choice
of the batch is a list of check boxes on the screen and a list of identifiers in the API.
A person who writes gets a label made for each ticked object that has none; a reader
prints the labels that exist and makes none.

**Libraries.** One new runtime dependency, **ReportLab** (BSD licence,
the reference PDF library of Python, no system library), added to the list of ADR 0001.
The lock holds 5.1.0 (published on 2026-10-22, more than the seven days of ADR 0014). It
brings no new transitive package that is not already in the lock: Pillow and
charset-normalizer are there. Its own QR encoder (`reportlab.graphics.barcode.qrencoder`,
error correction level Q) makes the code, so a second library is not needed; the same
matrix is drawn in the PDF and, as an inline SVG, on screen, with the 4 modules of white
the standard asks for around it. A rendered PDF page (24 codes on a sheet, and the roll)
and a rendering of the SVG were decoded back with an independent reader (OpenCV) when this
record was written. The development group adds `types-reportlab` (typeshed stubs;
5.0.0.20261008 is locked, 5.1.0.20261104 is inside the seven days). Alternatives
rejected: `segno` for the code and `fpdf2` (LGPL-3.0) for the page, two dependencies where
one does both; WeasyPrint, which needs Pango and Cairo in the image; a PDF made in the
browser, which needs a script library and is no use to a client of the API. All
licenses are on the list of the Dependency review.

**Reports.** `Report` has a title (what is wrong, required, 200 characters), an optional
place (a room, an item or a stock item, at most one, written down as a text when the
report is made so that it still reads right after a rename or a deletion), an optional
photo, a reporter, a status (`open`, `task`, `resolved`, `no_action`) and, when it has
one, a link to a task. No date, no priority, no owner. **Every member reports, a reader
included**: it is the only thing a reader writes, and it changes nothing else. A reader
sees the reports they made and no others; a member who writes sees them all, makes one into
a task with no date (the link is kept, the report leaves the open list), closes it as
resolved or as needing no action, and reopens it (back to `task` when it has a task that
still exists, else `open`). A read-only member has at most 20 open reports (status `open`)
at the same time; those made into a task or closed are not counted, and the owner and the
members who write are not limited. The
transitions are conditional updates, so that two requests cannot both win.

**The photo** follows the rules of the attachments and the label photo of the stock
(ADR 0030, 0034, 0038): type decided by decoding, size limit of the instance, rewritten
without its metadata, a thumbnail, stored under `MEDIA_ROOT/reports/<home id>/` by
identifiers only, served only by a view that checks who may see the report, inline with
`nosniff` and a sandbox policy, counted by the quota of the home, erased with it. Images
only: a report is a message, not a place for documents.

**Visibility and alerts.** Today shows, to members who write, one line when reports are
open, with a link; the home page has a row for the reports and one for the labels. When a
report is made, a message goes, in the background (django-q2, after the commit), to the
members who write, whose account is active and who have a channel on, except the reporter.
It holds the title the reporter typed, the name of the home and a link when the instance
has a public address, like a reminder (ADR 0043): nothing else leaves the instance. Quiet
hours of the person apply (the message is not sent then; the report is on Today). No new
table: the existing claim table is not reused, since its list of kinds would need a
migration in `notifications`, and an alert is sent once, by a task, not found by a scan.

**Audit.** `report.created`, `report.converted` and `report.closed` record the title and
nothing else (ADR 0045); `label.revoked` and `label.regenerated` record the name of the
object. A code is never written in the log.

**Names.** A reporter is named by `accounts.names.person_name` (ADR 0045), like every other
person: the display name with its filler characters trimmed, else the full name, else the
part of the address before the `@`. An alert holds no name. A deleted account reads "a former
member". In a list, on screen and in the API, `accounts.names.labels` tells two people of one
name apart by their address in brackets (the one place an address is shown, as in the other
lists), so that a reader cannot pass for a namesake in front of those who write. Reports does
not name people by a rule of its own.

**Isolation, export, erasure.** Every query is bound to the home; a label or a report of
another home, a reader's view of others' reports, an administrator of the instance who is
not a member and a token bound to another home all get a 404 (ADR 0006, 0022). Export:
`labels` (the code is exported for an active label, not for a revoked one) and `reports`,
with the photos under `files/reports/` (ADR 0023, 0036); the erasure of the home removes
the rows by cascade and the directory by `homes.files`. An API token needs `write` to
make, revoke or print (a `POST`) and `read` to list and read, as everywhere (ADR 0007).

## Consequences

- The address on the stickers is the address of the instance: moving the instance to
  another address breaks every printed sticker, and the documentation says so.
- A person who photographs a sticker learns a code that opens a page only for members of
  the home. Revoking and regenerating are the answer to a lost or shared sticker.
- A report cannot be deleted, only closed; the person who reported cannot withdraw it.
  Both are left to a later lot if asked for.
- Quiet hours may hide an alert; nothing hides the report.
- The 200-label limit and the 20-open-reports limit are constants, not settings.
