---
orphan: true
nosearch: true
---

- **The tasks done before this version count today's budgets.** Nothing kept the budget of a task on the day it was done until now, so the migration gives every time a task was done before this version the labour and material budgets its task has at the update (the material budget only when no stock item with a unit price works the materials out, and none for a task of a project). It counts only where no cost was typed: an approximation, which changes the real cost of those tasks, the History and the Budget of the past years. A task done for less, or for nothing, gets its real amount in **Details**, 0 included. An archive written before this version is restored the same way: its tasks done get the budgets their task has in the archive. Back on 0.5.2, the new columns are not read and the real cost is the amount typed again.
