# Room

One room, at `/h/<home id>/rooms/<room id>/`. The address stays the same when
the room is renamed or reordered, so it can be printed on a label.

The page has these parts:

- the **type** and the **notes** of the room, then one row of actions, each button as wide
  as its text and the row going on to a second line when it must: **Edit the room**, the
  [QR label](qr-label.md) and **Report a problem**;
- the **room memory**: paint (colour, brand, reference, batch, and the tin left over
  with its quantity and place, from the [stock](stock.md)), floor and wall coverings and
  free notes, with **Edit the memory**. It is empty until you note something, and
  nothing in it is required;
- the **stock** that concerns the room: the items listed on it, those compatible with
  one of its items and its paint left over, with their quantity, where they are kept and
  a **Low stock** label;
- **Photos** of the room in one list, with **Add a photo**, see
  [Add a photo](photo-form.md). Each photo shows its category (**General**,
  **Behind the walls**, **Before the work**, **After the work** or **Other**), and the
  chips above the list keep one category (`?category=`). **Behind the walls** are the
  photos of pipes and cables taken before a wall was closed, to know where not to
  drill;
- the **items** in the room, with **Add an item**;
- the open **tasks** of the room, with **Add a task** and **Add from a template**, which
  opens the [library of templates](task-templates.md) with this room already chosen.

**Delete the room** (in the edit form) asks for confirmation. Deleting a room
also deletes its items and their photos; they disappear from every screen and
from the API.

**QR label** opens the [label](qr-label.md) of this room; **Make a QR label** makes it
when there is none, for members who may write. Read-only members see the label and
print it. **Report a problem** opens the [report form](report-form.md) with the
**Where?** list already filled in with this room; every member sees it, read-only
members included.

The link at the top leads back to the screen the room was opened from, and to the
list of rooms otherwise. A task or an item added from this page comes back to it.

**Budget of the room**, at the bottom of the page, shows what is planned for the room this
year and what is used of it: its share of the projects counted this year, and the upkeep
of its tasks. **See the budget detail** opens the [budget of the room](budget-rooms.md#budget-of-one-room),
whose link at the top comes back here.

Members and owners change the room; read-only members see it all and change
nothing. An address that does not exist, or that belongs to a home you are not
a member of, answers the same "Page not found".
