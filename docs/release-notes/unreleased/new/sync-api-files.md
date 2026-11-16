---
orphan: true
nosearch: true
---

- **Sync API: files of the phone.** The mobile app uploads the file of a photo, an attachment or a file of a project with `PUT /api/v1/sync/homes/{h}/files/{file_id}`, under an id it makes, and reads any file of the home with `GET` ([Sync API](../../../api/sync.md#files)). The server checks and writes an image again as on the web, which drops its metadata, accepts the documents the web accepts, counts the file in the quota of the home and refuses one above the largest file of the instance, which `GET /api/v1/sync/info` now gives (`file_max_bytes`). A photo or an attachment pushed before its file waits for it; a file no row refers to within 7 days is removed by the nightly purge.
