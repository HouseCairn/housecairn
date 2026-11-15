---
orphan: true
nosearch: true
---

- **Sync API, rules across rows.** What a phone pushes is checked against the rules of the
  tasks, as the web applies them ([Sync API](../../../api/sync.md)): a task never keeps two open
  occurrences, an undo taken on a phone while someone else postponed the next occurrence gives
  way to that edit, the next date of a task done on a phone is the one its repeat rule gives,
  and the stock follows a task marked done on a phone once. The home's activity log lists the
  tasks marked done or skipped from a phone, with the person who did it.
