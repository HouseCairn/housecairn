# QR labels and reports

Two things for the people who share a house: **stickers** that open the page of a boiler,
a room or a shelf of the cellar when scanned, and a **report** that anyone, a tenant or a
tradesman included, can send in two taps when something is wrong.

## Print stickers

Open **QR labels** on the home page, tick the rooms, items and stock items to label,
choose a layout and press **Print the labels**. You get a PDF: an A4 sheet of 3 by 8
labels (63.5 by 33.9 mm, the format of common adhesive sheets) or a 62 mm roll with one
label per page for a label printer of the Brother QL kind. Print at 100 %: no scaling,
no "fit to page". Each label has the QR code, the name of the object and where it is (the
room of an item, the storage location of a stock item).

The code holds the address of the instance and a random identifier of 22 characters, and
nothing else: no name, no room, no number that could be guessed. **Set the public
address** of the instance (the `HOUSECAIRN_SITE_URL` setting) **before printing**: without
it the code holds the address the page was opened from, which may not be the one other
people use. If the instance later moves to another address, the stickers already stuck
stop working and must be printed again.

## What a sticker does

Scanning opens `/l/<code>/`. If you are not signed in, you sign in and come back. A member
of the home goes to the page of the object, read-only members included. Anyone else sees
"Page not found", the same page as for a sticker that was revoked, deleted or never
issued, so a code tells nothing to someone who is not in the home.

A sticker that is lost, photographed or shared is replaced: open the label and press
**Make a new label** (the old sticker stops opening anything) or **Revoke the label**.

The address is built to work as a universal link of a future iOS application, which would
claim `/l/*` through an `apple-app-site-association` file. That file is not served yet.

## Report a problem

Every member of the home has **Report a problem**, on the page of a room, an item or a
stock item (the place is then filled in), on the list of reports and after scanning a
sticker. The form has two fields and a photo: *What is wrong?* (required) and *Where?*
(a room, an item or a stock item, optional). The photo is optional too: it is checked by
its content, stripped of its location and counts in the storage of the home.

A report is not a task: it has no date and no owner, and may stay open as long as nobody
decides. A person who may write opens it on the **Reports** screen, or from the line on
**Today**, and chooses:

- **Make it a task**: a task with the same title, place and no date, linked to the report.
  The page of the task then shows the report it comes from (who reported, when, the text
  and the photo as a thumbnail that opens it full size), and the task list and Today mark
  such a task with a **Photo** label when the report has one;
- **Mark as resolved** or **Close without action**;
- **Reopen**, if a closed report comes back.

The person who reported sees their own reports under **My reports** and nothing of anyone
else's; they cannot change anything else in the home. Reports are shown by the name of the person, as everywhere
(two people of one name are told apart by their address in brackets).

## Be told

When a report is sent, a message goes to the members who may write, whose account is
active and who added a notification channel (see [Notifications](notifications.md)),
except the person who reported. It holds the title typed by the reporter, the name of the
home and, when the instance has a public address, a link. Quiet hours apply: the message
is not sent during them, and the report is on Today in the morning. Nothing is sent when
notifications are off on the instance.

## Scripts

Labels and reports are in the [API](../api/index.md) (`labels` and `reports`), with the
same rules as the screens. They are in the [export of the home](../administration/data.md)
and are erased with it.
