---
orphan: true
nosearch: true
---

- **The real cost counts the stock used.** When a task is marked done, the stock it takes now counts in its real cost, each item at its unit price on the day it is taken: two stock items worth 151.00 make a real cost of 151.00 without typing anything. The amount you type is added to it, for the rest (labour, purchases outside the stock): with 40.00 typed, 191.00. A price changed later does not change what was spent, an undo takes the value of the stock out again, and an item without a unit price counts for nothing, as the screens say. The task page, the history, the budget (by nature, the stock is materials), the CSV files and the API follow the same rule. Projects keep their money in their budget lines, as before ([ADR 0090](../../../adr/0090-real-cost-counts-the-stock-used.md), [Task](../../../screens/task.md)).
