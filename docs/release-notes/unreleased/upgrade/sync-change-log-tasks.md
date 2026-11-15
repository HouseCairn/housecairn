---
orphan: true
nosearch: true
---

- **The change log records the tasks.** Every write of a contractor, a task, a stock item of a task, an occurrence, what a closing took from the stock and an attachment now records its change, for the fields that changed only. Taking back a "Done" keeps the next occurrence as a deleted row instead of erasing it, so the mobile app will learn of it; the nightly purge removes it after 400 days, with the files and clocks of the rows that go with it. Deleting an account takes it off the tasks of the homes that stay, with a change for each. Nothing reads the log yet. See [Data model of the synchronization](../../../developer/sync-data-model.md).
