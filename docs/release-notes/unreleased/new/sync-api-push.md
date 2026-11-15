---
orphan: true
nosearch: true
---

- **Sync API, pushing.** A connected mobile app sends what was typed on the phone to a home it
  was given (`POST /api/v1/sync/homes/{id}/push`, [Sync API](../../../api/sync.md)). Each field
  is decided on its own by its clock, so two people who edit two fields of one task both keep
  their change; the app learns which of its values lost, and keeps its long texts that lost as
  an other version. A push sent twice runs once, a read-only member writes nothing, and only an
  owner archives a home.
