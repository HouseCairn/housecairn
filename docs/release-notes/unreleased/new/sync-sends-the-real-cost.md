---
orphan: true
nosearch: true
---

- **The mobile app receives what the real cost of a done task counts.** The sync API now sends the unit price each stock draw was taken at and the labour and materials a task planned when it was marked done, so that the app can show the same real cost as the web: the stock used plus the amount typed, or with nothing typed what was planned. The server alone sets these values: the app reads them and a change of one from the app is refused. At the update, the values of the tasks done before are sent once to the apps already in step, which read them with their next changes. An app of before ignores them and keeps showing the amount typed only ([ADR 0092](../../../adr/0092-the-phone-reads-the-real-cost-the-server-keeps.md), [Sync API](../../../api/sync.md#the-real-cost)).
