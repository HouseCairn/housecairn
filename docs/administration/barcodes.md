# Barcodes

A stock item and an item of a room keep their barcode (EAN): it is typed, or scanned with
the camera of the phone or the computer. Scanning a code can also fill in the empty fields of
the form (name, brand, model, category) and bring a photo of the product. Two sources can be
turned on, independently, and **both are off by default**: an instance that turns nothing on
keeps the codes typed and calls nobody. The choices are in
[ADR 0083](../adr/0083-barcode-lookup.md).

| Source | Setting | What leaves the server |
| --- | --- | --- |
| Local catalogue (Nudger) | `HOUSECAIRN_BARCODE_CATALOGUE_ENABLED` | Nothing when a code is looked up. The weekly download of the catalogue only. |
| Open Products Facts, then Open Food Facts | `HOUSECAIRN_BARCODE_LOOKUP_ONLINE` | The code, and nothing else. |

Both are also in **Settings of the instance**, section **Barcodes**. When an administrator
scans a code while the online lookup is off, the answer offers a link to turn it on.

## The local catalogue

The [Nudger open data](https://nudger.fr/opendata/gtin) (open4goods) lists about 114 million
barcodes, with a brand, a model, a name and categories, under the
[Open Database License](https://opendatacommons.org/licenses/odbl/1-0/). It has no photos.
HouseCairn keeps a copy in its own database, so that a lookup never leaves the server: it is
the private way.

Turn it on, then import it once by hand, or let the weekly task do it:

```console
docker compose exec housecairn python -m housecairn.entrypoint manage import_barcode_catalogue --download
```

- `--download` fetches the export (`gtin-open-data.zip`, 6 GB) from Nudger. The source limits
  its speed to 256 kB/s, so the download takes **about seven hours**. A download that stops
  goes on where it was the next time.
- `--file <path>` imports an export already downloaded (the ZIP, or the CSV inside it).
- By default only the **categories useful in a house** are kept: household appliances, DIY,
  tools, lighting and bulbs, batteries, filters, heating, plumbing, electricity, garden,
  cleaning, paint, hardware. `--all-categories` keeps everything.
- The file is read as a stream, row by row: the memory used does not grow with its size. An
  import that stops (restart, crash) goes on from the row where it stopped.

When the setting is on, a task of the worker downloads and imports the export every week, by
slices of a few minutes.

### Size and duration

Measured on 2026-11-15 on the **first 2,000,000 rows** of the export, with SQLite on 8
processor cores, then extrapolated to the 114.3 million rows of the full file. The full import
was not run: these figures are estimates, the categories of the rest of the file may differ.

| Import | Rows kept | Space in the database | Duration of the import |
| --- | --- | --- | --- |
| House categories (default), measured | 218,646 of 2,000,000 (10.9 %) | 58 MB | 6 min 32 s |
| House categories (default), extrapolated | about 12.5 million | about 3.3 GB | about 6 hours |
| All categories, measured | 2,000,000 | 385 MB | 14 min 8 s |
| All categories, extrapolated | about 114 million | about 22 GB | about 13.5 hours |

Add the download (about seven hours) to the first import. Keep the space free on the data
volume before you turn the catalogue on, and count it in the [backups](backup.md).

## The online lookup

When a code is not in the local catalogue, the worker asks
[Open Products Facts](https://world.openproductsfacts.org), then
[Open Food Facts](https://world.openfoodfacts.org), in the background:

- **Only the code is sent**, in the address of the request, with the `User-Agent`
  `HouseCairn/<version> (+https://github.com/HouseCairn/housecairn)` that those databases ask
  for. No name, no home, no account, no cookie.
- The same protections as the preview of links: public addresses only, every redirect checked,
  short timeouts, bounded answers.
- The answers are kept in a cache of the instance, one per code, for 30 days (1 day for a code
  not found): a code scanned twice is asked once.
- At most 10 lookups a minute per person and 30 per home, under the 15 a minute those
  databases allow per address.
- Off on a [demonstration instance](demo.md), whatever the setting says.

## Photos and their licence

The photos of Open Products Facts and Open Food Facts are under the
[Creative Commons Attribution-ShareAlike 3.0](https://creativecommons.org/licenses/by-sa/3.0/)
licence. When a person keeps one, HouseCairn downloads it, re-encodes it without its metadata
like any photo, and stores its **attribution** with it: the source, the page of the product,
the licence and, when the source gives it, its author. The attribution is shown under the
photo. A photo is only added to an item that has none, or as the label of a stock item that
has none: a photo taken by a member is never replaced.

The About page names these sources and their licences. HouseCairn does not ship the catalogue:
each instance downloads it, so nothing changes in its `NOTICE`.
