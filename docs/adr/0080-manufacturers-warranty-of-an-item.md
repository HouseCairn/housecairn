# 0080. The manufacturer's warranty of an item, apart from its maintenance contract

- Status: accepted
- Date: 2026-11-15
- Extends: [0029](0029-rooms-items-and-room-memory.md) (items), [0058](0058-contract-provider-is-a-contractor.md) (maintenance contract)
- Related: [0077](0077-sync-api-for-the-mobile-app.md) (sync API)

## Context

An item had one date for its warranty, its end. The maintainer asked on 2026-11-15, after using
the application, for the warranty of the maker or of the shop as people read it on the receipt:
a duration in months or years, who gives it, the manufacturer or the shop with its name, a start,
by default the purchase date, and the end worked out and shown. The item had no purchase date.

## Decision

- **Six fields on `rooms.Item`**: `purchased_on`, `warranty_duration` (1 to 99),
  `warranty_duration_unit` (`month` or `year`, `year` by default), `warranty_provider`
  (`manufacturer`, `shop` or empty), `warranty_provider_name` and `warranty_starts_on`. They are
  apart from the maintenance contract (provider, number, link), which stays as it is.
- **`warranty_ends_on` stays a column, worked out by the service.** With a duration and a start
  (`warranty_starts_on`, else `purchased_on`), `rooms.services` sets it to the same date that many
  months or years later, the last day of the month when that month is shorter
  (`rooms.models.warranty_end`). Without them, the end typed is kept. So an item noted before keeps
  its end with no migration of data, and *Warranties ending soon* still reads one column.
  Alternative rejected: a derived field (`SYNC_DERIVED`) computed on read; the ends typed before,
  with no duration, would be lost to the phone and to the list.
- **Synced like the other fields** (ADR 0077): the change log records them, a push writes them. A
  push writes its fields as they come (lot M5a has no repair rule): a phone that changes the
  duration, the unit or a start sends the end it works out the same way.
- **The form groups them** in a fieldset *Manufacturer's warranty*; the sheet shows the end as
  before, then the duration, the start and who gives it.

## Consequences

- The export carries the new fields without a new format version; an archive written before
  reads them as empty.
- The mobile app adds the six fields and the rule of the end before it edits a warranty.
