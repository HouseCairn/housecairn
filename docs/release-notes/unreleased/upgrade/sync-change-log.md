---
orphan: true
nosearch: true
---

- **A change log for the mobile app, not used yet.** A new `sync` application adds tables to the database (a migration runs at start) and a nightly task at 03:30 that removes what is older than 400 days. Nothing is recorded in it and nothing reads it until the next versions. The identifier the server will write with is made once, by that migration, and is part of the database, so a backup carries it. When a home is deleted, the instance now keeps its identifier, the time it was deleted and the identifiers of the connected apps that were linked to it. See [Data model of the synchronization](../../../developer/sync-data-model.md).
