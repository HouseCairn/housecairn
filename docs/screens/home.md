# Home

The page of one home, at `/h/<home id>/`: its name, your role in it, its
currency and its hemisphere. Its address stays the same as long as the home exists. The tabs above
the page switch between **Home settings**, **Household and roles** (its members) and,
for owners, the **Home audit log**; they are the same on those three pages.

When the home requires signing in through a provider and your session did not,
the page asks you to continue with that provider instead.

An example home, the one that the guide makes ([ADR 0084](../adr/0084-interactive-guide-and-example-home.md)),
opens like another home but never leaves the instance: it has no **Export all data**, no QR labels,
no invitations and no calendar feed, it is not in the list of your homes of the connected apps, and
nobody but you sees it ([Export and deletion](../administration/data.md#the-example-home)).
Its name carries the mark **Example**, and every page of the example home starts with a banner
**Example home**, with **Back to the guide** and **Delete the example home**: one press shows the
question in place of the button, with **Delete** and **Cancel**, on the same page, with no name
to type and no export ([Delete the example home](guide.md#delete-the-example-home)). The bottom
of its page has no **Archive this home** nor **Delete this home**.

## Name, currency and hemisphere

Owners and members open **Name, currency and hemisphere** (`/h/<home id>/edit/`) to change
them. The hemisphere, **Northern** by default, decides what spring, summer, autumn and
winter mean for the [tasks](task-form.md): it is never guessed from a place.
Changing it moves the window of the tasks that use a predefined season. Read-only members
do not see the row.

Under **Settings**, two rows lead to the [QR labels](qr-labels.md) and to the
[reports](reports.md) of the home; the second shows how many reports wait when you
may write.

## Archive a home

Owners see **Archive this home** above **Delete this home**. A page asks for a confirmation
first. An archived home is not deleted and not locked: it is **listed apart** on
[My homes](homes.md), under **Archived**; it leaves [Today](today.md), the
[calendar](../guides/calendar.md) and the home selector; it **sends no reminder** and no
alert of a new report; and its [calendar feeds](../guides/calendar.md) keep their address
but hold no event, and it gets no new feed. Opening it from the list shows its page, marked
**Archived**, and every page of the home still opens and can be changed as before: marking a
repeating task done still makes its next occurrence, as in any home, and that occurrence
sends no reminder until the home is brought back.

On an archived home, owners see **Bring this home back** instead: after a confirmation the
home is listed again and its reminders resume. Only owners archive or bring back a home,
as only they delete it. Both actions are recorded in the [audit log](audit.md). After
either, you go back to where you came from.

## Tables for a spreadsheet

Every member sees the list **Tables for a spreadsheet**: the rooms, items,
tasks, history of the tasks, stock, contractors and reports, each as a CSV file
to open in a spreadsheet (see [Export and deletion](../administration/data.md)).
Owners and members also find **Import tables** at the end of the list, which adds
rooms, items, tasks, stock and contractors from CSV files ([Import tables](home-import.md)).

## Your data

Owners see two more actions at the bottom of the page.

- **Export all data** downloads one archive with everything of the home, in an
  open format (see [Export and deletion](../administration/data.md)). The
  export is recorded in the [audit log](audit.md). For a home with many files,
  the archive is made in the background: a page says when it is ready and
  offers **Download the archive**, at most three times within a day.
- **Delete this home** removes the home and everything in it, for good, for
  every member. The page asks you to type the exact name of the home, and
  offers the export first. There is no way back.
