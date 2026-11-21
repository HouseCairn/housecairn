---
orphan: true
nosearch: true
---

- **The stock taken before this version is valued at today's prices.** Nothing kept the unit price of the day the stock was taken until now, so the migration gives the stock taken by tasks done before this version the unit price each item has at the update: an approximation, which changes the real cost of those tasks, the History and the Budget of the past years. An item without a unit price counts for nothing. If you typed the price of the stock in the cost of a task done before, it is now counted twice: correct the amount in **Details**, where the field is now **Other costs**. An archive written before this version restores its stock without a price. Back on 0.5.2, the new column is not read and the real cost is the amount typed again.
