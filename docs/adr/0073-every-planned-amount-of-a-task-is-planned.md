# 0073. Every amount planned on a task is in Planned, once

- Status: accepted
- Date: 2026-11-14
- Extends: [0051](0051-budget-views-and-annual-envelope.md) (budget views and the annual
  envelope)

## Context

The maintainer reported that a task with a planned amount did not show in the budget
summary: "the forecast should show too, even before it is committed, so that the summary is
complete". A run of the screens, the files and the API on two sets of data (the demo home,
then one home per case) found two kinds of task whose estimate was in no total:

- **An open task with no date**, in the upkeep. Rule 0051 put an occurrence with no date "in
  no year": its estimate showed as *Estimate each time* on the budget of the upkeep, and in
  no figure of a year (envelope, by room, the page of the room, by nature, by year, files,
  API). A task with stock items only, and one with no room, were lost the same way.
- **Every task of a project**, whatever the status of the project (idea, planned, in
  progress) and whether it had budget lines or not. Rule 0051 left a task of a project out of
  the upkeep, "a project holds its own money", and the project counted its lines only: a
  project with no line, whose tasks were estimated, planned nothing anywhere.

A task of the upkeep with a date, done, open or to come by its repetition, in a room or not,
with its stock, was already counted everywhere; so was a task of a year to come, in that year.

## Decision

**Every amount planned on a task is in *Planned*, committed or not, and never twice.**
Committed and paid do not change: they stay those of the budget lines, the quotes and the
costs typed on what was done.

**An open task with no date counts in the current year.** It is still to do and has no
date to put it in a later year. It counts in the year of today, once, and
in no other year, past or to come; read next year, it is in next year, the way an occurrence
left late is counted in the current year from 1 January (0051). Its estimate is in the
upkeep of the year, its room (or *No room*), its natures and every file and route. A done
occurrence keeps the day it was done. Alternative rejected: a line *Not planned* apart from
every year. It shows the amount but leaves it out of the year the envelope is set against,
which is the summary the maintainer asked for.

**A project plans its lines, and what its tasks plan beyond them, nature by nature.** A task
of a project stays out of the upkeep (it is never in two places). Its estimate each time,
material budget and stock items at their unit price as materials, labour budget as labour, is
counted once for each occurrence done or open, whatever its date, since the whole project is
counted in one year (0051); a skipped one counts for nothing, and the repetition is not
projected, a project having no end year. For each of the two natures, the lines of that nature
cover the tasks: what the tasks plan above the lines is added to the planned amount of the
project, what the lines already hold is not added again. So the planned amount of each nature
is the more of its lines and its tasks; rental and other, which a task does not plan, stay the
lines. An abandoned project plans nothing, from its lines or from its tasks.

Examples: a project with no line and two tasks at 200.02 and 66.06 plans 266.08; a labour line
of 500 and a labour task of 300.03 plan 500; a labour line of 100 and a labour task of 250.05
plan 250.05 (100 from the line, 150.05 from the task); a rental line of 400 and a labour task of
300 plan 700, the scaffolding does not pay the roofer.

Alternatives rejected:

- **Lines only, the tasks shown beside.** Nothing is counted twice, but a project with no line
  still plans zero in the envelope, by room and by year: the summary stays incomplete.
- **Lines plus tasks.** Every amount is in *Planned*, but a line typed for the same work as a
  task counts it twice, and households type both.
- **The tasks only when the project has no line.** One line of 10 for the permit makes the
  estimates of every task disappear from the total.
- **The more of the lines and the tasks over the whole project.** A rental line would cover
  labour: the 400 of scaffolding would hide the 300 of the roofer.

**What the tasks plan is shown.** *Budget by project*, its file and its route give, for each
project, **Planned by its tasks** (`tasks_planned` in the API): what its tasks plan in all,
zero for an abandoned project. The difference with *Planned* is the part the lines hold.

The figures are still worked out when read, in `Decimal`, with no new table, query per task or
dependency: the tasks, occurrences and stock lines of the home were already loaded.

## Consequences

- The planned amount of a project in the budget can be higher than the total of its budget
  lines on the page of the project, which shows its lines; the budget guide says why.
- The projects screen (its list, its envelope summary and its API) still shows the lines only:
  a later change can bring the same rule there.
- The task page of a task of a project still shows no cost block.
- Paid stays the lines: the cost typed on a done task of a project is not added to the paid
  amount of the project.
