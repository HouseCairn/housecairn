# Budget by project

`/h/<home id>/budget/projects/`, with `?export=csv` for a file.

One row per [project](project.md): **Counted in** is the year the project starts (the earliest
date of its tasks and accepted quotes, else the year it was created); the whole project is
counted there even if the work runs into the next year. Then **Planned**, **Committed**,
**Paid**, the gauge, where it stands in words and **Still to pay**, which is worked out line
by line. Under the name of a project whose tasks have an estimate, **Planned by its tasks**
gives what they plan in all; the file has it in a column of its own. **Planned**
is the budget lines, and what the tasks plan beyond the lines of the same nature, materials or
labour: an estimate a line already holds is not counted twice
([A task in a project](../guides/budget.md#a-task-in-a-project)). An abandoned project plans nothing any more, from its lines or its tasks, so what was paid on
it shows as spent without a plan. A row opens the project.

A project of the table opens its [page](project.md): Budget stays marked in the bar, and the
link at the top of the project says **Budget** and comes back here.
