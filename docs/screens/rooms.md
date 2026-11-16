# Rooms

The rooms of a home, at `/h/<home id>/rooms/`. **Rooms** in the navigation
opens it directly (`/rooms/`); with several homes, on the
[current home](my-home.md#the-current-home). Each row shows the name of the room, its type and
how many items it holds; a row opens the [room](room.md). The tabs **Equipment** and **Stock**
list [every item of the home](equipment.md) and its [stock](stock.md).

- **Add a room** opens the [room form](room-form.md). Only the name is
  required.
- **Change the order** shows an up and a down button per room instead of the
  links; **Done** goes back. The order is the one of the list, here and in the
  API.
- The search field looks for a name among the rooms and the items of the
  home. It has a magnifier and no button: what you type replaces the list of rooms, in
  place, with the rooms and the items found, listed apart, each linking to its page
  (`/h/<home id>/rooms/?q=<text>`); empty the field to get the list back. Without
  JavaScript, Enter opens the [search](search.md) page. The search ignores case and looks
  for the text anywhere in the name.

Read-only members see the same list and none of the buttons that change it.
