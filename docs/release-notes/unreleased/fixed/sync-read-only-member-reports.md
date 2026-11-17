---
orphan: true
nosearch: true
---

- **A read-only member's phone shows their own reports only.** With a Housecairn server, the [sync API](../../../api/sync.md#the-changes) gave the phone of a read-only member every report of the home, with its photo, when the site shows them only the reports they made. It now gives them the same reports as the site. When an owner makes a member read-only, or a member again, their phone starts again from the server's data at its next sync, with the reports their new role shows; a phone that held the reports of others before this version drops them at its next sync too.
