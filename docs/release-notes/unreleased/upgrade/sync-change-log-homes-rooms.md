---
orphan: true
nosearch: true
---

- **Homes and rooms now fill the change log.** Every change to a home, a room, an item or a photo, whether made on the web, through the API, by a CSV import or by restoring an archive, is recorded in the change log of the mobile app, field by field, only for what really changed. Nothing reads the log yet. Restoring an archive whose "updated at" date of the home or of an object is more than a day ahead of the clock of the instance is now refused. The nightly purge of the log now also removes the files of the photos it deletes, and records the change of an object whose link it empties (a room whose paint is an article of the stock it removes). See [Data model of the synchronization](../../../developer/sync-data-model.md).
