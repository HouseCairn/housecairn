# Task

One task, at `/h/<home id>/tasks/<task id>/`. The address never changes.

The link at the top leads back to the screen the task was opened from, and names it:
an item, a room, a contractor. Opened from nowhere known, it leads to the
task list.

**The top card** shows the state of the occurrence to do now (late, today, the
date, postponed), the priority, how the task repeats, the title, the room and the
item (both link to their page) and the person it is assigned to. Its buttons:

- **Done**: the occurrence is closed today and, if the task repeats, the next one is
  created. The stock items of **Materials** are taken from the stock; if it held less,
  the task is done anyway and a message says what was missing. A banner offers
  **Undo**, which brings back the occurrence, removes the one that was created and puts
  the stock back.
- **Postpone** asks for a new date, which cannot be in the past. The occurrence
  keeps the status *postponed* and the page shows the date it was moved from.
- **Skip this time** closes the occurrence without doing it; the next one is
  created as for **Done**, and **Undo** is offered too.
- **Edit the task** opens the [task form](task-form.md).

When a task done once has been closed, the card shows its outcome and **Undo**
reopens it.

**Reported problem** appears when the task was made from a report ([Make it a
task](report.md)). For each report, the most recent first, it gives the date and who
reported, the text of the report, which opens the [report](report.md), and its photo as a
thumbnail that opens the photo full size. A report without a photo has the same block
without the thumbnail. The photo is served by the same protected address as on the report:
a member of the home sees it, nobody else does. Members who may write see every report the
task was made from; a read-only member sees their own only. The section is absent for a
task that does not come from a report.

**When** gives the rule of repetition, the date, the masking delay and the next
three dates, were each occurrence done on time. **How to do it** shows the
procedure written in Markdown: the text is shown as written (HTML is not
interpreted), links to web pages are kept, images are never loaded.
**Details** lists the estimated duration, the budgets, the contractor and the
notes. **Photos and files** are described on the [files page](task-files.md).

**Photos of the item** (or **of the room**, when the task has no item) shows, read only, the
first six photos of the item or room the task is linked to, with a link to its sheet where
they are managed; the section is absent when there is none. Photos of another home are
never shown.

**Materials** lists the stock items the task needs, with the quantity, what is in
stock, the unit price of the stock item when it has one, and a label when it is not
enough; **Remove** takes one off, the quantity field
changes it and **Add the stock item** adds one. On a phone the quantity field moves
under the name, which keeps its full width. The choice of the stock item in the add form
starts on a dash.

**History** lists the occurrences done or skipped, the most recent first, with the
real cost and the note. **Details** on a row opens a form to fill in or correct what
is known of that time: the date it was done (not in the future), the real cost,
a note and, for each stock item the closing took, the quantity really used (the stock
follows). Changing the date does not move the occurrence created after it.
Once the next occurrence has been changed (postponed, noted, given a photo or done),
**Undo** is refused for the one before it, because it would lose that work.

Read-only members see everything and none of the buttons.
