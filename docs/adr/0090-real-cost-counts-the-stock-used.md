# 0090. The real cost of a done task counts the stock it used

- Status: accepted. The rule is the maintainer's decision of 2026-11-21; where the price is
  kept, what the sync API does with it and the migration are what this record decides with it.
- Date: 2026-11-21
- Amends: [0062](0062-unit-price-of-stock-items.md) ("It is a plan": the real cost was only
  the amount typed when a task is done)
- Amended by: [0091](0091-a-task-done-with-nothing-typed-pays-its-plan.md) (with nothing typed,
  the rest is the plan kept when the task was done)
- Amended by: [0092](0092-the-phone-reads-the-real-cost-the-server-keeps.md) (the phone reads
  the unit price of a draw, read only)
- Related: [0046](0046-stock-linked-to-tasks-and-rooms.md) (the draws of a closing),
  [0081](0081-materials-of-a-task-calculated-from-its-stock.md) (the materials planned from the
  stock), [0077](0077-sync-api-for-the-mobile-app.md) (the fields of the protocol)

## Context

A task lists two stock items, each with a unit price, 151.00 in all. Marked done, it takes them
from the stock (`tasks.materials.draw_for_done`, ADR 0046), but its real cost stayed at zero:
under 0062 the real cost was only `Occurrence.actual_cost`, typed by hand. Someone who did type
the price of the stock again counted what the stock already holds, and someone who did not saw
a task that cost nothing. The maintainer decided on 2026-11-21: "Real cost = value of the stock
used (quantity times unit price) + the amount typed for the rest (labour, purchases outside the
stock)."

## Decision

- **The price is kept on the draw, when the stock is taken.** `MaterialDraw.unit_price` holds
  the unit price of the article at the moment the closing takes it, or nothing when the article
  had none. Its value is `drawn` times that price. A price changed later does not change the
  past. Alternative rejected: reading the price of the article each time, which would rewrite
  the real cost of every task done before a change of price. The draw rather than the stock
  movement: a draw is the one line per article and closing, which an undo ends and a new
  closing brings back (ADR 0077, need 2); the movements are a ledger of exits and entries,
  where a correction of the quantity used adds lines that would each need a value.
- **One closing, one price.** A correction of the quantity used (`adjust_draws`, the form of
  what was done, `PATCH` of the API, a phone) keeps the price of the closing: the value follows
  the quantity. An undo ends the draws and their value; closing again takes the stock again at
  the price of that day. An article without a price counts for nothing and the screens say how
  many were taken without one; a price of zero is a price. An article deleted since still counts:
  what was spent was spent.
- **One rule, `tasks.costs`.** The stock used by a done occurrence is the sum of the values of
  its live draws, rounded once to the cent, half up, in `Decimal`. Its real cost is that plus
  `actual_cost`; it is unknown (null) only when nothing was typed and nothing of value was
  taken, which is what "done without a cost" now means. The page of the task (part *Real*,
  with *of which stock used*), its history, the History list and its total, the Budget (totals,
  by task, by room, last 12 months), the CSV files of the budget and of the history, and the API
  read it from there.
- **By nature**, the stock used is *Materials*; the amount typed is shared between materials
  and labour as the estimate of the task is, as before (`real_cost_split`), or counted under
  *Other* without an estimate.
- **`actual_cost` stays the amount typed.** The form of what was done names it *Other costs
  (labour, purchases outside the stock)* when the closing took stock, and shows *Stock used:
  151.00* above it, so that the stock is not typed again. The API adds `stock_cost` and
  `real_cost`, read only, to an occurrence, `unit_price` and `value` to each line of its
  `stock`, and `total_stock_used` to the budget of a task.
- **Projects are unchanged.** A task of a project keeps its money in the budget lines of the
  project (0062): what was paid on a project is what its lines say, and the real cost of its
  tasks is not added to it, nor was the amount typed before.
- **The server owns the price.** The field is not a field of the sync protocol: a phone that
  pushes it is refused as `forbidden`, like any column of the server. When a push brings a
  new draw, or brings one back (`restore`), the server sets the price of the article at that
  moment; any other write of a draw leaves it as it is. The draws the server makes for a
  closing a phone pushed without them take the price as the web does.
- **Data of before.** The migration gives the draws made before this version the unit price
  their article has now, since nothing kept the price of that day: an approximation, said in
  the release notes. A draw whose article has no price stays without one. An archive of
  before has no price on its draws: restored, they count for nothing, as they did in that
  version. The archive keeps the price from now on, a field added to `stock_draws` with no
  change of the format version, as 0062 did.

## Consequences

- One query more to load the Budget and the page of a task, whatever the number of tasks.
- A task done before this version with stock and an amount typed that already held its price
  counts that stock twice: the amount typed can be corrected in **Details**.
- The mobile app shows the real cost by the same rule: until it reads `real_cost`, it shows the
  amount typed only. It must not send `unit_price`, which it reads since 0092.
