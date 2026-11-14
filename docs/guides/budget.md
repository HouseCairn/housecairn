# Budget

The budget answers "how much, where, and are we within it" for the year, from what you
already enter: the budget lines and quotes of [projects](projects.md), and the estimate and
real cost of [tasks](tasks-and-recurrence.md). Nothing new is typed for it, and nothing is
stored: the figures are worked out each time you open a page. Open **Tasks**, then
**Budget**. Typing an amount is explained in [Entering an amount](amounts.md).

## What you enter

- On a **task**, a labour budget and a material budget: what it costs each time. They are in
  the form of the task, under **Time and budget**.
- When the task is **done**, its real cost, in **Details** of what was done. The page of the
  task shows its **Cost**: the estimate, what it comes to in a year and what was paid so far.
- On a **project**, budget lines with a planned and a paid amount, and quotes accepted on
  them.

### The stock in the cost of a task

The stock items a task lists add to its estimate: for each one, its quantity times its
[unit price](stock.md#give-an-item-a-unit-price), added to the material budget you typed.
The part that comes from the stock is shown apart, as **Of which stock** on the page of the
task and in the budget of the upkeep, and never mixed into the amount you typed. An item
with no unit price counts for nothing, and the screen says how many: "2 stock items without a
price are not counted". It is a plan, like the rest of the estimate: what was really spent
is the cost typed when the task is done, and in your accounts.

### A task in a project

A task that is in a project counts in the project, never in the upkeep, so that it is in
one place only. The project plans its budget lines, and what its tasks plan beyond them,
nature by nature: the material budget and the stock items of its tasks are materials, their
labour budget is labour. Each time a task is done or open counts once, whatever its date. A
line of a nature covers the tasks of that nature: what they plan above it is added, what it
already holds is not added again. A rental or other line covers no task.

For example, a project "Redo the bathroom" with no line and two tasks estimated at 200 and 66
plans 266. Add a labour line of 500 for a 300 task: the project plans 500, not 800. With a
labour line of 100 and a labour task of 250, it plans 250, the 100 of the line and 150 more
from the task. A quote accepted on the line stays committed on it. **Budget by project**
shows, for each project, **Planned by its tasks**: what its tasks plan in all. An abandoned
project plans nothing, from its lines or its tasks.

## Planned, committed, paid, used, gap

*Planned* is what you plan to spend. *Committed* is what accepted quotes promise. *Paid* is
what was spent. *Used* is the more of committed and paid, line by line, and the *gap* is
planned less used: **... left**, **On budget** or **Over budget by ...**. Rows also show the
*Used* amount, then a gauge with its percentage; colour is never the only signal.

## Example: painting the living room

A project "Paint the living room" has two lines, paint 300 and labour 500, so 800 planned.
You ask two painters; you accept the one at 620 on the labour line, and you pay 350 for the
paint. Used is 350 + 620 = 970 and the gap is 800 - 970 = -170: **Over budget by €170.00**, on the budget of the room, on the project and in the totals of the year. A task of
upkeep in the same room, "Wash the walls", estimated at 50 and done for 80, adds a gap of
-30 to the room and shows in the upkeep, so the room reads 850 planned and 1050 used, -200.

## By room, by project, by task

- **By room**: projects (shared evenly between the rooms they name, in whole cents) and the
  upkeep of the room's tasks.
- **By project**: each project once, in **the year it starts**, whole, even if the work runs
  into the next year, with its budget lines and what its tasks plan beyond them. A year is chosen by the earliest date of its tasks and accepted quotes;
  with none, the year it was created. An abandoned project plans nothing any more; what was
  paid on it shows as spent without a plan.
- **By task**: the tasks that are in no project. The **cost a year** follows the repetition:
  a task every 3 years costs a third of its estimate a year, every 6 months twice, a weekly
  task 52 times, rounded once to the cent. A task that does not repeat has none, and a task
  that repeats by use, when there is one, will not be annualised either.

For a year, **Planned** counts what the task did that year (each time done, at its estimate),
the occurrence that is open, and the times its repetition will still bring before 31
December. A task every month at 25, read in February after a first time in January, plans 12
times 25 for the year, not only the two rows that exist. A year that is over counts only what
happened: an occurrence still open that fell due in it counts in the current year, as the
calendar shows it late. An open task with no date is still to do: it counts in the current
year, once, and in no other year. A year to come counts the whole repetition, however far it is, and a
task that counts from the last time is followed from its open date, as the calendar does.
The plan of a year can differ by one time from the cost a year, since a weekly task falls 52
or 53 times in a year and a daily one 365 or 366. Nothing is written: these times are worked
out when you read the page.

A task done with no cost typed counts at its estimate in the plan and as nothing paid, and is
flagged, so that the gap does not look better than it is.

## The envelope, years and the last 12 months

The top of the overview is three figures and a gauge: what is set aside for the year (the
envelope, or the plans when there is no envelope), what is **spent** (the more of committed and
paid, line by line, projects and upkeep together) and what is **left**, with the share spent in
words next to the gauge. When the spending passes what is set aside, the figure says **Over by**
and the gauge says *over budget*. The other figures (the plans against the envelope, what is
paid, projects and upkeep apart) are under **Details of the year**. The overview also splits the year by nature (materials, labour, rental, other; the real cost of a
task is shared between materials and labour as its estimate is, or counts under Other if it
has no estimate). **By year** lists every year with a figure.
**Last 12 months** is the upkeep done since the same day last year; payments on projects have
no date and are not in it. The envelope is set from **Projects**.

## Several homes and currencies

**Budget** in the navigation opens the budget of the current home. The API adds your homes up
by currency; amounts in different currencies are never converted.

## Files and scripts

Every page has **Download as CSV**: UTF-8, a comma, amounts with a dot. A text that would
start a formula in a spreadsheet (`=`, `+`, `-`, `@`) is written with a leading quote so that
it stays text. The same figures are in the [API](../api/index.md). Read-only members read
everything; nothing here changes data.
