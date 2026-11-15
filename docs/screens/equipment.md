# Equipment

Every item of a home, all rooms together, at `/h/<home id>/items/`. It is the **Equipment**
tab of **Rooms** in the navigation, between **Rooms** and **Stock**. The items are listed by
room, in the order of the rooms, then by name. Each row shows the name of the item, its room
and its brand, its next task and its manufacturer's warranty; a row opens the
[item](item.md).

- **Next** is the soonest open task of the item, a late one first, with its date; *No task to
  come* when it has none.
- The label of the warranty gives its end: *warranty until* while it runs, *warranty ended*
  once it is over. An item with no end of warranty has no label.
- The search field looks for the text in the name, the brand, the model and the serial
  number, ignoring case: `/h/<home id>/items/?q=<text>`.
- **Room** keeps the items of one room: `/h/<home id>/items/?room=<room id>`. The two filters
  add up; **Search** applies both.

Items are added from the page of their [room](room.md). Read-only members see the same list.
