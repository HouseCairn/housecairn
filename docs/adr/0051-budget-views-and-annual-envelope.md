# 0051. Budget views and the annual envelope

- Status: accepted
- Date: 2026-11-09
- Extended by: [0073](0073-every-planned-amount-of-a-task-is-planned.md) (an open task with
  no date counts in the current year; a project plans its tasks beyond its lines)

## Context

Projects (ADR 0050) hold a budget in lines, with a planned, a committed and a paid amount.
Tasks already hold a labour budget and a material budget, and an occurrence a real cost
(ADR 0031). The household wants one answer to "how much, where, and are we within it":
by room, by project and for the upkeep, for the year and over time, with the envelope of
the year set against all of it. Sure will one day supply the real payments (principle P4);
until then the paid amount is typed. The budget never calls Sure: no network, no token.

The fields the card first thought to add to `tasks` (the estimate of a task, the cost of an
occurrence) exist already, with their forms and API. Nothing is added to `tasks` or
`projects`: no field, no migration.

## Decision

**A new app, `budget`, with no table.** It reads `projects` and `tasks` and works every
figure out when it is read, in `Decimal`, in Python (never SQL `SUM`, which SQLite computes
in floating point). Because it stores nothing, there is nothing of its own to export or to
erase: the export and the erasure of a home (ADR 0023) already cover every figure it reads.
A test pins that the app has no model.

**Four screens and their API**, each read only and with a file: `/h/<home id>/budget/` (the
envelope of a year, then by nature, by year and over 12 months), `.../budget/rooms/`,
`.../budget/projects/` and `.../budget/tasks/`, and `/budget/` for several homes. The API
has the same five routes under `/api/v1/`. Every view takes `?year=` and `?export=csv`.
A query count that does not grow with the tasks: a home loads in a fixed number of queries
(tested with 500 tasks).

**Three amounts, one gap, as in ADR 0050.** *Planned*, *committed* and *paid*; *used* (in
the code and the API, `engaged`) is the more of committed and paid, worked out line by line;
the *gap* is planned less used, negative when exceeded, said in words and never by colour
alone; *still to pay* is committed less paid, also line by line (a line paid above its quotes
does not lower what another line still owes: `Amounts.to_pay` was a difference of totals and
is now summed per line, as the gap is). Gauges are labelled with their percentage.

**Projects are counted in one year: the year they start.** The year of a project is the year
of the earliest date among its tasks (the date done, or the date due of one that is open), and
its accepted quotes (the date of the quote); with no date at all, the year it was created.
The whole project, planned, committed and paid, is counted there, even when the work runs into
the next year. Alternatives rejected: sharing it between the years by days, which needs dates
the budget lines do not have; counting the payments in the year they were made, which needs
dated payments (Sure will give them, and this rule then gains a second mode); counting it in
the last year, which moves every year while the work is going on. The first date is stable
once the work has begun and is the date a household decides, and sets money aside, on. The
envelope of a year is compared with the projects counted in it.

**An abandoned project plans nothing any more.** Its planned amounts count as zero in every
figure; what was paid and what was committed stay, and show as spent without a plan.

**Upkeep is the tasks that are in no project.** A task of a project is paid by the lines of
the project; counting its own estimate too would count the money twice, so the budget ignores
it, and its page says so by showing no cost block. For a task:

- *Estimate each time* is the material budget plus the labour budget.
- *Planned* for a year is the estimate times the occurrences that are done in the year (the
  date done, else the date due), open and due in it, or still to come by the repetition
  before 31 December. *Paid* is the sum of the actual costs of those done. An occurrence
  done with no cost typed is planned and paid as zero, and is counted and shown ("done
  without a cost"), never hidden.
- *Still to come* is worked out after the latest open occurrence, with
  `tasks.recurrence.coming` (the same dates as `next_due`, the RFC 5545 engine the calendar
  also uses, ADR 0031), each time taken as closed on its due date; a task that counts from
  the last time is followed from its open date. Nothing is written and no query is made per
  task, so the number of queries still does not grow with the tasks.
  - *Only the year asked is worked out.* A fixed series is entered near the start of the
    year, and a chain (counted from the last time) jumps straight to it, so the cost follows
    the length of the year and not its distance from the open date: 2040 is projected as
    cheaply as next year, and a daily task gives 366 dates in 2032, a leap year. No bound on
    the walk is left to reach, so a count is never partial without saying so (a first version
    stopped after 2000 dates and gave 122 days in 2032 and none in 2035).
  - *A year already over has no projection*: only what happened counts. An occurrence still
    open that fell due in such a year is not part of that year's plan any more; it is counted
    in the current year, from 1 January, as the calendar shows it late, and the repetition is
    projected from there (a monthly task at 25 left late over a new year plans 12 times 25,
    not 13). A year to come is projected whole.
  - *The plan of a year and the cost a year may differ by one time.* The cost a year divides
    52 weeks or 365 days evenly; the calendar does not: a weekly task falls 52 or 53 times in
    a year, a daily one 365 or 366. The plan of the year follows the calendar, so it can
    exceed or fall short of the cost a year by one estimate.
  - Alternative rejected: counting only the rows that exist, which showed 50 for the year of
    a monthly task at 25 whose cost a year is 300, and so made the plan look far lower than
    the envelope it is set against.
- A skipped occurrence counts for nothing. An occurrence with no date at all is in no year;
  it is in the total.
- *Cost a year* is the estimate times the occurrences a year of its repetition: 12 divided
  by the interval for months, 52 for weeks, 365 for days, 1 for years (so a task every 3
  years is a third of its estimate, every 6 months twice). It is rounded once, at the end, to
  the cent, half up: the estimate is multiplied by the times a year, then divided by the
  interval, so that the rate is never rounded before it meets the estimate (0.24 every 48
  years is 0.01). A task that does not repeat has none. There is no repetition by use yet
  (every 200 hours of running); when there is, it will not be annualised either, since its
  pace is not on the calendar, and the screens will say so. Seasons do not change the rate.

**By room.** A task is in its room, or in "No room". A project is in the rooms it names, its
amounts shared evenly between them in whole cents, the odd cents going to the first rooms in
the order of the rooms, so that the rooms add up to the project exactly. A project with no
room is in "No room".

**By nature.** The natures of budget lines (materials, labour, rental, other, with the list of
ADR 0045). The estimates of upkeep go to materials and labour. The cost typed on a done
task has no nature of its own: it is shared between materials and labour in proportion to
the estimate of the task (the material part rounded to the cent, labour taking the rest, so that the two
add up to what was paid). Counted under *Other* it would stand against a plan of nothing and
show an overrun that is not one. Alternative rejected: a line of its own for the upkeep,
which is not a nature of a budget line and would break the list shared with projects. A
task with no estimate has no proportion to share by, so its cost is counted under *Other*,
and the page says so.

**By year and over 12 months.** By year lists every year with a figure (a project, a dated
occurrence, an envelope), newest first. The 12 months slide: from the same day a year ago,
exclusive, to today, inclusive, for the upkeep that was **done** in it. Payments on projects
carry no date, so they are not in this figure, and the page says so.

**The envelope** (ADR 0050) is compared with, for the year: the planned, the used and the paid,
for the projects counted in it and for the upkeep, with what is left to plan (envelope less
planned) and left to spend (envelope less used), said in words when exceeded. No envelope is
"none", not zero. There is a place for a budget read from Sure, `sure_budget`, always empty
and shown as "Not connected": nothing is fetched.

**Several homes.** `/budget/` and `GET /api/v1/budget/` add the homes of the person by
currency, with no conversion; a person with one home goes straight to its budget. A token
bound to some homes adds up those homes only.

**CSV.** One file per view, UTF-8, a comma, CRLF, amounts with a dot and two decimals so
that any tool reads them back. A text cell that starts with `=`, `+`, `-`, `@`, a tab or a
carriage return is written with a leading quote, so a spreadsheet keeps it as text. Numbers
made by the program are not touched: a negative gap stays a number. A test feeds every kind of
hostile title, room and task name through every file.

**Who sees what.** The budget changes nothing, so any role that can open the home reads it,
read-only members included (ADR 0020); the one change it leads to, the envelope, stays with
owners and members on the projects screen. Another home, an instance administrator who is not
a member (ADR 0022) and an API token bound to another home get a 404. A token needs the
`read` scope; no method but `GET` exists.

**Typing an amount.** The cost fields of tasks (labour budget, material budget, actual cost)
use the amount field of projects, now in `core.forms.MoneyField` and still importable from
`projects.forms`: the comma in French, the point too, spaces that group thousands, and the
ambiguous forms (`1,250.50`, `1.250,50`) refused. A guide page, "Entering an amount", says it
for every screen. Amounts go up to 9999999999.99 as typed.

**Data.** The API gives amounts as strings with two decimals, as projects do; `used` is
`engaged`, `gap` is signed, `budget_state` is `over`, `under` or `on`.

## Out of scope

Valuing the stock consumed by a task at its last price paid waits for the stock-to-task link
(a separate lot); the lines that carry it will join the upkeep figures without a change of
rule. The check of payments against Sure, the purchase forecast and a budget per room fixed
by hand are later lots.
