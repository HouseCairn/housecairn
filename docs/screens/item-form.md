# Add or edit an item

The form to add an item to a room, at
`/h/<home id>/rooms/<room id>/items/new/`, and to edit one, at
`/h/<home id>/items/<item id>/edit/`.

| Field | Content |
| --- | --- |
| Room | The room the item is in. Choose another one to move the item; its photos go with it. |
| Name | Required, 120 characters at most. |
| Type | Equipment or surface. |
| Brand | Free text, 120 characters at most. The brands already entered for the items of the home are suggested as you type; any other text is kept. |
| Model, serial number | Free text, 120 characters at most each. |
| Purchased on, installed on | Dates. |
| Manufacturer's warranty | Apart from the maintenance contract. **Duration**: a number from 1 to 99, in months or years (years when nothing is chosen). **Given by**: the manufacturer or the shop, and its name. **Starts on**: empty, from the purchase date. **Ends on** is worked out from the start and the duration when both are known, and shown under the fields; type it only when you do not know them. |
| Maintenance contract: provider | Who services it: one of the contractors of the home, or the last choice, **Add a contractor…**, which shows the fields of a new one right under the list: its name (required) and its trade. The contractor is created when the item is saved; complete it later on its [sheet](contractor.md). A form refused for another reason creates no contractor, and a name that is already a contractor of the home, whatever its case, is refused: choose it in the list. Without script the fields of a new contractor are always shown, and read only when **Add a contractor…** is chosen. Optional. |
| Maintenance contract: number | The number to quote when you call them, 120 characters at most. |
| Contract link | An `http` or `https` address. |
| Links | One address per line, then an optional label; ten at most, `http` or `https` only. |
| Notes | Free text, 4,000 characters at most. |

Only the name is required.

The link at the top and the form once sent lead back to the screen the form was
opened from: an item added from its room goes back to the room, with the message
**Item added · Open**. Saved changes go back to where the form was opened.
