# 0091. A task done with nothing typed has paid what it planned

- Status: accepted. The rule is the maintainer's decision of 2026-11-21; where the plan is
  kept, what the sync API does with it, the projects and the migration are what this record
  decides with it.
- Date: 2026-11-21
- Amends: [0090](0090-real-cost-counts-the-stock-used.md) (the real cost was the stock used
  plus the amount typed only)
- Amended by: [0092](0092-the-phone-reads-the-real-cost-the-server-keeps.md) (the phone reads
  the plan of an occurrence, read only)
- Amended by: [0093](0093-an-archive-of-before-takes-the-values-of-the-migrations.md) (a done
  occurrence of an archive of before is restored with the plan the migration gives it)
- Related: [0081](0081-materials-of-a-task-calculated-from-its-stock.md) (the materials planned
  from the stock), [0062](0062-unit-price-of-stock-items.md) (the money of a task of a project),
  [0077](0077-sync-api-for-the-mobile-app.md) (the fields of the protocol)

## Context

Under 0090, the real cost of a done occurrence is the value of the stock it used plus
`actual_cost`, the amount typed. A task with a labour budget of 120.00 and nothing else, marked
done in one gesture, cost nothing: the Budget showed 120.00 planned and 0.00 paid, and flagged
it *done without a cost*. Someone who did not open **Details** to type 120.00 again saw a task
that cost nothing. The maintainer decided on 2026-11-21: "It is the same when a task only has a
material or labour budget and is marked done. If it is done with its amounts, the amounts are
paid."

## Decision

- **The rule, in `tasks.costs`.** The real cost of a done occurrence is the stock used plus the
  rest. The rest is `actual_cost` when one is typed; otherwise it is the plan of the task: the
  labour planned, plus the material budget typed **only when the stock does not work the
  materials out**. With a stock item that has a unit price, the materials planned are the stock
  (0081), which the draws already count: the material budget typed is then not counted, never
  twice. A task with stock worth 151.00 and 80.00 of labour costs 231.00 done with nothing
  typed. The real cost is null only when nothing is typed, nothing is planned and nothing of
  value was taken.
- **An amount typed replaces the plan, not the stock.** `actual_cost` of 0 is an amount: it
  makes the rest 0, and is not read as empty. The form, the REST API and the sync protocol
  already keep empty and 0 apart (an empty field is null, `"0.00"` is zero); the tests hold
  each of them to it.
- **The plan is kept on the occurrence when it is marked done.** `Occurrence.planned_labour`
  and `Occurrence.planned_material` hold the plan of that moment, so that a budget changed
  later does not rewrite what was done, as `MaterialDraw.unit_price` does for the price of the
  stock (0090). Alternative rejected: reading the budget of the task each time, which would
  change the past at each edit of a budget. `complete` sets them; `undo` empties them, and
  closing again keeps the plan of that day. Changing the details of a done occurrence leaves
  them alone.
- **The server owns the plan.** The two fields are not fields of the sync protocol, as
  `unit_price`: a push that writes them is `forbidden`. When a push makes an occurrence done
  (from any other status, a new row included, a repair of the rules too), the server keeps
  the plan of its task at that moment; when a push makes it not done, it empties it; any other
  write of a done occurrence leaves it as it is.
- **By nature**, in the Budget: the stock used is *Materials*; with nothing typed, the labour
  planned is *Labour* and the material budget kept is *Materials*; an amount typed is shared
  by `real_cost_split` as before, or counted under *Other* without an estimate.
- **Everything reads the same rule**: the page of the task (part *Real*, with *of which
  planned, with nothing typed*), its history (*as planned, nothing typed*), the History list and
  its total, the Budget (by task, by room, by nature, by year, last 12 months, envelope), the
  CSV files of the budget (*Of which planned, with nothing typed*) and of the history (the two
  fields, and `real_cost`), the archive (the two fields of `occurrences`), and the API
  (`planned_labour`, `planned_material` and `real_cost` of an occurrence, `total_from_plan` of
  the budget of a task).
- **The form of what was done says what is counted**: *Stock used: 151.00*, *Labour planned:
  80.00* and, if any, *Materials planned*, with a sentence that an amount typed replaces the
  planned amounts and that the stock stays counted.
- **Projects are unchanged.** A task of a project keeps no plan when it is done: its money is
  in the budget lines of the project (0062), whose *Paid* is what the lines say. Counting its
  labour or material budget as paid as well would count it twice on the Budget, and once more
  in the History. The plan of the task still counts in the *Planned* of its project, as before.
  A task taken out of a project later keeps, for the times done before, the empty plan of that
  moment.
- **Data of before.** The migration gives every done occurrence the plan its task has now, by
  the same rule (no plan for a task of a project, no material budget over a priced stock):
  nothing kept the plan of the day it was done, so this is an approximation, said in the
  release notes. It counts only where no amount is typed. An archive of before has no plan on
  its occurrences: restored, they count the stock and the amount typed only, as in that
  version. The archive keeps the plan from now on, two fields added to `occurrences` with no
  change of the format version, as 0090 did.

## Consequences

- A task done in one gesture now shows its planned amounts as paid in the Budget and the
  History, and is no longer *done without a cost*. Someone who paid less or nothing types it in
  **Details**, 0 included.
- No query more: the two fields are read with the occurrence.
- The mobile app does not receive the two fields. It shows the same real cost once it reads
  `real_cost` from the REST API; until then it shows the amount typed. It must not send them.
