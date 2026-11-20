# 0087. The stock of an item is edited from the item as well as from the stock item

- Status: accepted
- Date: 2026-11-20
- Related: [0038](0038-stock-suppliers-and-movements.md) (the link `compatible_items`, declared on
  the stock side with no reverse name, "so the item page does not show it yet"),
  [0063](0063-search-field-with-chips-for-multiple-choices.md) (the search field with chips)

## Context

The link between a stock item and a piece of equipment was edited from the stock item only
(`compatible_items`). The maintainer, using the app, found that he could link a stock item to an
equipment but not the other way round, from the page of the equipment, where he thinks of "what
this machine uses".

## Decision

- **One link, two screens.** The page of an item has a **Materials** section: the stock items that
  fit it (name, quantity in stock, unit price), **Link a stock item**, **New stock item** and
  **Remove**. The data is `Article.compatible_items`: no migration, no change of the sync
  protocol, no change of the API.
- **Written by the same service.** Linking and removing go through `stock.services.update_article`
  with the new list of items, so that the checks, the rows of the sync and the audit entry are the
  ones of the form of the stock item. Linking an article already linked, or removing one that is
  not, writes nothing.
- **Same home.** Only stock items of the home of the item can be linked; the field offers no other,
  and the service refuses one that is posted.
- **Created in place, not edited in place.** **New stock item** opens the usual form of a stock item
  with the item already chosen, and the item is linked whatever the form posts. It is the same
  form and the same validation as the creation from the stock. No second form to keep in step.
- **Roles.** The three actions are writes of the stock: members and owners only; a read-only member
  gets a 403 and does not see the buttons. An archived home stays editable (0079), as everywhere.
- **No script needed.** Each action is a link or a form that posts and redirects; the search field
  is the one of 0063, which shows a plain list without script.

## Consequences

- The product rule "the link is edited from the stock item" becomes "from either side".
- The item page gains a query for its stock items.
- Alternative rejected: an inline panel on the item page. It would have a second form to keep in
  step with the stock item form, for a task done once in a while.
