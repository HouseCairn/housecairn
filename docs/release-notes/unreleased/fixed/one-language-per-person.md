---
orphan: true
nosearch: true
---

- **Your language, everywhere.** The language chosen in the
  [display preferences](../../../screens/display-preferences.md) now applies to reminders,
  notifications on your devices and calendar feeds (the notification settings no longer have
  a language of their own; a language chosen there becomes your language if you had none),
  to the API with a token, to the message of the change and of the sign-in, and to the pages
  kept for reading offline. It is reached from **Profile** too, and the signed-out pages have
  a language switch ([Language](../../../guides/language.md)).

- **API change: no `language` in the notification settings.** The `language` field is gone
  from `GET` and `PATCH /api/v1/me/notification-preferences/` and from
  `GET /api/v1/me/notifications-export/`; a `PATCH` that still sends it is ignored. The language
  is read and changed at `/api/v1/me/display-preferences/`.

- **Upgrade: two limits of the migration.** A person whose notification language was never
  chosen (it stayed at the default, `en`) and who had no display language now gets `en` as an
  explicit choice: set the display language back to **Automatic** to follow the browser again.
  Rolling the migration back recreates the column with `en` and does not restore the earlier
  notification language.
