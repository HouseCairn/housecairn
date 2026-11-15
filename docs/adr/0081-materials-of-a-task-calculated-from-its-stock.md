# 0081. The materials of a task are calculated from its priced stock items

- Status: accepted
- Date: 2026-11-15
- Amends: [0062](0062-unit-price-of-stock-items.md) (the plan of a task was the typed material
  budget plus the stock)
- Related: [0073](0073-every-planned-amount-of-a-task-is-planned.md) (every planned amount is
  in Planned)

## Context

Rule 0062 added the stock items of a task, at their unit prices, to the material budget typed on
it. The maintainer found after using it that people typed the price of the filter in the
material budget as well, and the plan counted it twice. He asked on 2026-11-15 that a task with
priced stock items has its material budget **calculated**, quantity times unit price, each time
and over the year, with no second figure to type, the field typed staying only without a
priced stock item; and that planned and real read apart at a glance, on the task and in Budget.
His example: descaling the espresso machine every 3 months, one filter at 7.90, plans 31.60 a
year.

## Decision

- **One rule, `budget.services.material_of`.** With at least one stock item that has a unit
  price (a price of zero is a price), the materials of one time are the stock plan of 0062 and
  nothing else; without one, they are the material budget typed. The screens, the files, the API,
  the totals, the share of a real cost by nature and the tasks of a project all read it.
- **The amount typed is kept, not counted.** A material budget typed before the stock items were
  priced stays in the database and in the export; it counts again if the priced items go. The
  task form shows the calculated amount in place of the field and sends the stored value back
  unchanged, so that saving the form erases nothing. Alternative rejected: clearing the field
  when a priced item is added; a removed line would lose what the person had typed.
- **An item without a price** still counts for nothing and is said apart, as in 0062. A task
  with only unpriced items keeps its typed budget.
- **Planned and real apart.** The card *Cost* of a task has a part *Planned* (estimate each time,
  materials from the stock, cost a year) and a part *Real* (paid so far), each under a label.
  The budget of the upkeep already has its columns for each; the line under the estimate says
  *materials from the stock*.

## Consequences

- A task that had a typed material budget and priced stock items plans less than before: the
  release notes say so.
- The API keeps `material_budget` as typed; `estimate` and `stock_estimate` of the budget routes
  follow the rule. The mobile app applies the same rule when it shows a cost.
