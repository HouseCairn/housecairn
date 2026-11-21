# History

What was done or skipped in a home: the **Done** filter of the
[task list](tasks.md) (**All**, then **Done**), at `/h/<home id>/tasks/?status=done`,
the most recent first. The former address, `/h/<home id>/history/`, leads there with
its filters. Each row shows the task, where it is, the contractor and who closed it, the
outcome with its date, and the real cost when there is one: the stock the closing took, at
its unit prices of that day, plus the cost typed, or with nothing typed what the task planned
when it was marked done, its labour and material budgets
([ADR 0090](../adr/0090-real-cost-counts-the-stock-used.md),
[ADR 0091](../adr/0091-a-task-done-with-nothing-typed-pays-its-plan.md)). A row opens the
[task](task.md).

People are named by their [display name](profile.md), never by their address.

The search field at the top narrows the list as you type: it has a magnifier and no button,
and the list follows after a short pause. The other fields are folded under **Filters**,
which says how many are applied, as in **Filters (2)**, so the list stays in reach on a
phone. The list follows each choice too, and the address keeps the filters, so that
Back and a bookmark find them again. Without JavaScript, press Enter in the search field,
or use the **Filter** button of the sheet. All fields are optional:

- **Search** looks in the task title and in the note written when it was closed.
- **Room**, **Item**, **Contractor** and **Done by**.
- **Outcome**: done or skipped (`?outcome=`).
- **From** and **To**: the dates it was closed. A skipped occurrence has no
  completion date, so the day it was skipped is used.

Above the list, a line gives the number of entries and their **real cost**. An entry with
no cost typed, no budget planned and no stock of value taken counts for nothing, not for zero:
the sum is that of the entries that have a cost. A cost typed as 0 is a cost of zero. The
list shows 30 rows at a time, and the filters stay when you change page.

Exporting the history (CSV, PDF file) comes with a later version.
