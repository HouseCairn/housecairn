---
orphan: true
nosearch: true
---

- **Sync API, reading.** A connected mobile app reads the homes it was given, with their
  members, the changes of a home since its last position and the snapshot of a home, under
  `/api/v1/sync/` ([Sync API](../../../api/sync.md)). An erased home is reported to the phones
  that had it, and a phone away longer than the 400 days changes are kept starts again from the
  snapshot. Sending changes from the phone comes later.
