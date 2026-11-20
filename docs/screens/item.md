# Item

One piece of equipment or one surface (a wall, a floor), at
`/h/<home id>/items/<item id>/`.

The page shows what you noted: brand and model, serial number, purchase and installation
dates, the manufacturer's warranty (a label with its end tells whether it is still running,
then its duration, its start and who gives it), the maintenance
contract (its provider is a link to the [sheet](contractor.md) of the contractor, then the
number and the link of the contract), notes, then the links and the photos of the item; an item with no photo shows a dashed
frame, *No photo yet.*, and **Add a photo** under it. Only
what is filled in is shown. The actions are one row of buttons, each as wide as its text,
going on to a second line when it must: **Edit the item**, which opens the
[item form](item-form.md), the QR label and **Report a problem**.

**Materials** lists the stock items that fit the item: the name, the quantity in stock with its
unit and the unit price when there is one; each opens the [stock item](stock-item.md). It is the
same link as the **Compatible items** of the stock item, seen from the equipment. Members and
owners have three actions there:

- **Link a stock item** opens a page with the search field of the home's stock items that are not
  linked yet (without JavaScript, the list itself); pick one or several, then **Link**.
- **New stock item** opens the [form of a stock item](stock-item-form.md) with this item already
  chosen as compatible; once saved, you come back to the item.
- **Remove**, under a stock item, takes the link away. The stock item, its quantity and its
  movements stay in the stock.

Read-only members see the section and none of the buttons.

The maintenance log and the cost of ownership of an item arrive with the tasks:
there is no tab for them yet.

**QR label** opens the [label](qr-label.md) of this item; **Make a QR label** makes it
when there is none, for members who may write. Read-only members see the label and
print it. **Report a problem** opens the [report form](report-form.md) with the
**Where?** list already filled in with this item; every member sees it, read-only
members included.

**Delete the item** (in the edit form) asks for confirmation, then leads to the room
of the item. The link at the top leads back to the screen the item was opened from,
and to its room otherwise. Members and owners
change the item; read-only members see it all and change nothing.
