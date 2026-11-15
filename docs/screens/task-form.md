# Task form

The form to add a task, at `/h/<home id>/tasks/new/`, and to edit it, at
`/h/<home id>/tasks/<task id>/edit/`. Only the **title** is required. From a room
or an item, **Add a task** fills the room and the item in.

The first block holds the title, the room, the item (listed with its room), the
due date and, below, five sections that open when you need them. The last choice of the
room list, **Add a room…**, and of the item list, **Add an item…**, adds one without
leaving the form (see [Adding a room or an item](#adding-a-room-or-an-item)):

| Section | Content |
| --- | --- |
| Repeat (open when the task is added from an item, or when it repeats) | One sentence: **Repeat this task**, **Repeat every** N days, weeks, months or years, **Count from** the due date or the day it is done (see below). A **season** outside of which nothing falls (see below), the number of days before the date from which the task shows, and the repetition said in plain words. |
| Who | Priority, the person in charge (a member of the home, shown by name) and the contractor, chosen in the list of the contractors of the home; its last choice, **Add a contractor…**, adds one without leaving the form (see below). The dash removes the contractor. |
| Time and budget | Estimated duration (a number and a unit: minutes, hours, days, weeks, months or years), labour and material budgets, in the currency of the home. With a priced stock item in **Materials**, the material budget is calculated from the stock and shown in place of its field. |
| Materials | The stock items the task needs, one line each: the stock item and the quantity; the last choice of the list, **Add a stock item…**, adds one. One empty line is offered; **+** adds a line and **−** removes one, up to 50 (see [Materials](#materials)). Marking the task done takes these quantities from the stock. |
| How to do it | The procedure in Markdown, notes and, when adding, photos and files. |

## Repeat

Leave **Repeat this task** unticked for a task done once. Ticked, the task comes back
**every** N days, weeks, months or years, counted from:

- **the due date**: the task keeps its day, whenever it is done. A date field, **Starting
  on**, opens with the calendar of the device; it is the due date of the first block, and
  typing in one fills in the other. A task due on 15 January 2027, every 3 months, stays on
  the 15th even when it is done on the 20th.
- **the day it is done**: the period is the number of days, weeks, months or years chosen,
  counted from the day the task is marked done. A filter changed on 3 March, every 3 months,
  is due on 3 June. The date field is hidden, as it is not used.

Under the fields, **In short** says the repetition in plain words and follows what you
type: "Next time: 3 months after the last time", "Day 15 of every quarter, from
01/15/2027", with the months of the season if there is one. A task set on the 31st falls on
the last day of shorter months, one set on 29 February on 28 February in other years: the
sentence says so.

Without script, every field stays visible and nothing is lost: the server ignores what the
choices do not use (the interval of a task done once, the date field when counting from the
day it is done). If both date fields were changed to two different dates, the form asks for
one.

## Season

A task can be limited to a season: **Spring**, **Summer**, **Autumn**, **Winter** or
**Custom months** (a first and a last month; November to March is fine). The two month
fields show only with **Custom months**; without script they stay visible and are ignored
for another season. The predefined seasons follow the **hemisphere** of the home, which you
set on the [home](home.md) page: in the northern hemisphere spring is March to May, summer
June to August, autumn September to November and winter December to February; in the
southern one they are shifted by six months. Changing the hemisphere of the home moves the
tasks on a predefined season; a custom season keeps its months.

The heating season is no longer offered: for the heating, take **Custom months**, October to
April in the north. A task or a project that had it was given those months when the
application was updated, and an archive, a CSV file or an API client of before that sends
it gets them too, so nothing changes for it.

## Estimated duration

A whole number and a unit, on one line from 360 px: the number takes the room, the unit its
own width. From 1 minute to 1 year. The units are fixed lengths, as the estimate is of
elapsed time: a week is 7 days, a month 30 days, a year 365 days. It is stored in minutes and
shown as people say it, biggest unit first: **45 min**, **1 h 30**, **2 days**, **2 weeks**,
**1 month 1 week**, **1 year**. Beyond a year, the form says so under the field.

## Adding a room or an item

The last choice of the **Room** list, **Add a room…**, opens the fields of a new room right
under the list: its name (required) and its type, from the generic list of types of room
(Kitchen, Bedroom, … Other). The last choice of the **Item** list, **Add an item…**, opens
the name of a new item. The new item goes in the room chosen above, or in the room added in
the same send; with neither, the form asks you to **choose the room of the new item**. A
known item cannot go with a room that is added: choose the room it is in.

The room and the item are created when the task is saved, in the same transaction as the
task: a form refused for another reason creates nothing. An empty name, or a name already
in the home (for an item, already in that room), whatever the case, is refused with a
message under the field: choose the existing one in the list. Complete them later on their
[room](room.md) and [item](item.md) pages. Without script the fields of the new records are
always shown and are read only with their choice. A member who may only read adds nothing:
the form is closed to them.

## Materials

One line per stock item: the stock item, in the list of the stock of the home, and the
quantity. The last choice of the list, **Add a stock item…**, opens its name (required) and
its unit, from the list of units (**Which unit** for Other) right under the list; the stock
item joins the stock of the home when the task is saved, linked to the equipment chosen for
the task (the one added by the same form included), and a form refused creates
nothing. A name already in the stock, or added by another line, is refused with a message.

One empty line is offered. With script, **+** adds a line and **−** removes the line it
is on, up to 50 lines (the number of stock items a task may list); when the limit is reached
**+** is disabled and a notice says so, which a screen reader announces. Without script,
**Add a line** sends the form back with what was typed and one more line, without
checking or saving anything (the files chosen have to be chosen again); to remove a line,
set its stock item to the dash. Pressing Enter in a field saves the task, never **Add a line**.

## Contractor

The list holds the contractors of the home, by name. Its last choice, **Add a contractor…**,
opens the fields of a new one right under the list: its name (required) and its trade, from
the list of trades, with **Which trade** for Other. The contractor is created when the task is
saved, and the task is linked to it; complete it later on its [sheet](contractor.md). A form
refused for another reason creates no contractor. A name that is already a contractor of the
home, whatever its case, is refused with a message: choose it in the list. Without script the
fields of a new contractor are always shown, and are read only when **Add a contractor…** is
chosen.

When the task is added from an [item](item.md) whose maintenance contract has a provider (a
contractor of the home), that contractor is already chosen in the list, and the **Who**
section is open so that you see it. Choose the dash to remove it. An item without a provider
chooses nobody.

## Photos and files

Photos chosen from the device are checked by their content, not their name: JPEG (including
the multi-picture JPEG an iPhone writes), PNG and WebP are accepted and written again
without their metadata. A HEIC photo is refused with a message that names the file and says
to choose the JPEG version of the photo or to set the camera to Most Compatible; the file
chooser only offers the accepted types, so a phone usually hands over a JPEG.

Editing the date moves the occurrence that is open. Deleting the task, at the
bottom of the edit form, asks for confirmation and removes the task, its history
and its files from the screens.

When you add files together with the task and one of them is refused, the task is
created anyway and a message names the file and the reason.

## Going back

The link at the top and the form once sent lead back to the screen the form was
opened from: a task added from an item goes back to the item, with the message
**Task added · Open**, whose link opens the new task. Opened from the task list, the
form opens the new task instead. A form sent with an error stays open and still goes
back to the same screen. After **Delete the task**, the page of the deleted task no
longer exists: you land on the task list, or on the screen you came from if it was
another one.
