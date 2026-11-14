---
orphan: true
nosearch: true
---

- **Export format 4, and `archived_at` in the API.** The archive of a home now carries the date the home was archived (`archived_at` in `data/homes.json`), so its export format is version 4; archives of versions 2 and 3 are still restored, as homes that are not archived. In the API, a home has a read-only `archived_at` (`null` when it is not archived), and `GET /api/v1/homes/` still lists archived homes: nothing changes for an integration. `POST /api/v1/homes/<id>/archive/` and `unarchive/` are new, for owners signed in. See [Export and deletion](../../../administration/data.md).
