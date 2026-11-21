---
orphan: true
nosearch: true
---

- **A task done with nothing typed has paid what it planned.** A task with 120.00 of labour planned, marked done without typing a cost, now has a real cost of 120.00, under labour in the budget, instead of a task done without a cost. The material budget counts the same way, under materials, unless the stock items of the task work the materials out: the stock is then counted once, at its unit prices. With stock worth 151.00 and 80.00 of labour, the task costs 231.00. The plan is kept when the task is marked done, so a budget changed later does not change it, and an undo takes it out. An amount typed in **Details** replaces it, 0 included, and the form shows what is counted. The task page, the history, the budget, the CSV files and the API follow the same rule; a task of a project keeps its money in the budget lines of the project ([ADR 0091](../../../adr/0091-a-task-done-with-nothing-typed-pays-its-plan.md), [Task](../../../screens/task.md)).
