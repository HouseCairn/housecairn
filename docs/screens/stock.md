# Stock

The stock of a home, at `/h/<home id>/stock/`. It is the **Stock** tab of
**Rooms** in the navigation; `/stock/` opens it directly, on the
[current home](my-home.md#the-current-home) when you belong to several. Each row shows the name of the item, its current quantity with
its unit and where it is stored; a **Low** label appears when the quantity has
fallen to the threshold or below. A row opens the [stock item](stock-item.md).

- **Add a stock item** opens the [stock item form](stock-item-form.md). Only the
  name is required.
- The search field looks for the text anywhere in the name, in the manufacturer
  reference or in the name or reference of a supplier, ignoring case:
  `/h/<home id>/stock/?q=<text>`. It has a magnifier and no button: the list follows what
  you type. Without JavaScript, press Enter.
- Two tabs, **All** and **Low stock**, switch between every item and the items at or below
  their threshold: `/h/<home id>/stock/?low=1`. An item with no threshold is never low. The
  words searched stay when you change tab.

Read-only members see the same list and none of the buttons that change it.
