---
orphan: true
nosearch: true
---

- **Sync API: homes made on the phone.** `POST /api/v1/sync/homes/` creates on the server a home the mobile app made, with the phone's id, the account of the connection its owner, and adds it to the homes of the connection ([Sync API](../../../api/sync.md#creating-a-home)). It needs the scope `homes.create` and the right **May create homes** given when the phone was connected. Sent again with the same fields, it gives the same home; an id held by anything else is `409` `id_taken`, which does not say what holds it.
