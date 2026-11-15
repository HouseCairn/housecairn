# Add or edit a stock item

The form to add an item to the [stock](stock.md), at `/h/<home id>/stock/new/`, and
to edit one, at `/h/<home id>/stock/<item id>/edit/`.

| Field | Content |
| --- | --- |
| Name | Required, 120 characters at most. |
| Manufacturer reference, dimensions | Free text, 120 characters at most each. |
| Storage location | Where it is kept, for example "garage, second shelf". |
| Unit, Which unit | A unit of the list (piece, litre, millilitre, metre, kilogram, roll, box) or **Other**, with the unit written in **Which unit**. |
| Threshold | The item is flagged when the stock falls to this quantity or below. |
| Unit price | What one unit costs, in the currency of the home; optional, not negative. The cost of the tasks that list the item is planned from it ([budget](../guides/budget.md#the-stock-in-the-cost-of-a-task)). A supplier's last price paid fills it when it is empty. |
| Rooms | The rooms where the item is used, besides those of the equipment it fits; only rooms of this home can be chosen. A search field: type to narrow the list, pick a room, and it becomes a chip you remove with its **Remove** button (or Backspace in the empty field). Without JavaScript, a list where you choose several rooms. |
| Compatible items | The equipment or surfaces of the home this fits; only items of this home can be chosen. The same search field, whose suggestions show each item with its room; without JavaScript, a list of the items grouped by room. |
| Photo of the label | JPEG, PNG or WebP, treated like the [photos of a room](photo-form.md): the content is checked, the location and the other information stored in the file are removed, a large picture is reduced. The default limit is 10 MB. |
| Notes | Free text, 4,000 characters at most. |

The quantity is not a field: the stock starts at zero and changes with the
movements, which you record on the [page of the item](stock-item.md). Only the name
is required.
