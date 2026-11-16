---
orphan: true
nosearch: true
---

- **Sync API: who the connected account is.** Each home of `GET /api/v1/sync/homes/` now carries `member_id`, the `id` of the entry of `members` that is the account of the connection, so that the mobile app can keep the reminders that are its own. The field is added; nothing else changes.
