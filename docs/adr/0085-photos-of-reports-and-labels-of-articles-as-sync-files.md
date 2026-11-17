# 0085. The photo of a report and the label of an article as files of the sync API

- Status: accepted
- Date: 2026-11-17
- Amends: [0077](0077-sync-api-for-the-mobile-app.md) (need 10, "As built (lot M6)") and
  [0079](0079-lots-of-the-server-mode-of-the-mobile-app.md) (lot M6, "As built")
- Related: [0030](0030-photo-storage-and-processing.md) (photos re-encoded),
  [0038](0038-stock-suppliers-and-movements.md) (label photo of an article),
  [0047](0047-qr-labels.md) (reports and their photo),
  [0084](0084-interactive-guide-and-example-home.md) (the example home never leaves the instance)

## Context

Lot M6 made the files of the sync API: `PUT` and `GET /api/v1/sync/homes/{h}/files/{file_id}`,
and a file reference in the operations of a photo of a room, an attachment and a file of a
project. The photo of a report and the label photo of an article were left for a later lot.
The feed already gave their reference, named by the id of the row, but the file route served
neither, and a reference a phone pushed was kept in `extra`. So the mobile app showed the
photos of rooms and items, and not those of reports: a phone could not fetch them, nor send
one.

## Decision

**Two more rows hold a file.** `reports.report` (field `photo`) and `stock.article` (field
`label`) join `rooms.photo`, `tasks.attachment` and `projects.projectfile` in
`sync.files.HOLDERS`, images only. They take the same routes, the same `HC-Sha256` header, the
same refusals (`400` digest, `403`, `404`, `409`, `413`, `415`) and the same limits
(`file_max_bytes`, the rate of pushes and reads) as the photo of a room, and the same rules:

- a file made on the web is read by the id of its row, the form the feed has always given, and
  has no `sha256`;
- a phone uploads its file under an id it makes, then pushes a full reference (`file`,
  `sha256`, `size`, `type`); the row takes the file, moved into the folder of its app under the
  phone's id (`<home>/<file id>.jpg`), or waits for it when it has not arrived, and the upload
  gives it the file later without a change of the feed;
- the server keeps its own copy, decoded and written again (EXIF and everything else of the
  file dropped), and the reference keeps the digest of what the phone sent;
- a reference whose `sha256` is not the digest of the file the home holds under its `file`, or a
  value that is not a reference, makes the push `400` `malformed`; a row never takes the file of
  another home, nor a document;
- a reference to another file replaces the photo: the file the row held is removed once the
  push is written.

**The columns stay those of the models: no migration.** A holder names the columns its model
keeps for its file (`Holder.content_type`, `size`, `thumbnail`, `dimensions`): a report keeps
`photo_content_type`, `photo_size_bytes` and its thumbnail, an article only
`label_content_type`. The feed still reads the stored name of the file and gives it as a
reference (`Spec.file_names`); the file id is now read from that name, so a photo a phone sent
is given under the phone's id, with its digest. An article has no column for the size of its
label: the snapshot gives `size` as `null`, as before.

**Optional, unlike the photo of a room.** A report and an article are made without a photo on
the web, and the app writes `photo: null` and `label: null` when it makes one without. So these
two fields take `null`: when a row is made, no photo; afterwards, the photo removed, its file
removed once the push is written, as for a reference to another file. A new report or article
whose operation leaves the field out is made without a photo: the shared vectors of the repair
rules (`tests/fixtures/sync_repairs.json`, pinned by the mobile repository) make articles
without the field, and a refusal would turn them into `400`. The photo of a room keeps its rule:
a new photo needs its reference, and `null` is `malformed`.

**What a phone pushed before.** A reference pushed before this change sits in `extra`, with its
clock. The first write of the field that beats that clock, from a phone or from the web, takes
the column and removes the value from `extra`; a write older than it loses, as any write of a
field does. Nothing else moves: such a reference names no file the server has.

**Who reads.** As for the photo of a room: a home of the connection only, `404` otherwise, an
upload refused to a read-only member (`403`) who still reads the files, a row deleted read
while it can be restored and `404` once gone for good, nothing of the example home. Since
[ADR 0086](0086-a-read-only-member-syncs-their-own-reports.md), the photo of a report made by
another member answers `404` to a read-only member.

## Consequences

- The mobile app fetches and sends the photo of a report and the label of an article as it does
  the photo of a room; its protocol (mobile `docs/protocol.md`, section 2.10) says so on its side.
- The web pages, the export of a home and its erasure handle a photo a phone sent as one made on
  the web: the export names it by the id of its row, the erasure removes it.
- A photo removed or replaced from a phone loses the credit of an open database it may have had
  (`label_attribution` of an article) only when the phone writes that field too: the server does
  not clear it by itself.
- `tests/test_sync_files_reports_stock.py` holds one test per behaviour, each failing on the
  code before this record.
