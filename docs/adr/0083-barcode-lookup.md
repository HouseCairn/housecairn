# 0083. Barcode lookup: a local copy of an open catalogue first, then Open Products Facts and Open Food Facts

- Status: accepted
- Date: 2026-11-15
- Related: [0070](0070-slow-network-calls-in-the-background.md) (slow network calls),
  [0071](0071-demonstration-instance.md) (demonstration), [0077](0077-sync-api-for-the-mobile-app.md)
  (sync), [0030](0030-photo-storage-and-processing.md) (photos), [0035](0035-instance-settings-in-the-interface.md)
  (settings)

## Context

The maintainer decided on 2026-11-15 that scanning the barcode (EAN) of a stock item or of an
item fills its sheet (name, brand, model, category, photo) from **open** databases, without a
commercial one. The sources were read on 2026-11-15:

- **Nudger (open4goods)**, <https://nudger.fr/opendata/gtin>: 114,307,320 barcodes (update of
  4 June 2026 shown on the page), under the ODbL, one compressed CSV of 6.0 GB
  (`gtin-open-data.zip`, one member `open4goods-gtin-dataset.csv`, deflate), refreshed weekly
  by the page's own account. Columns, in the file's order: `gtin`, `brand`, `model`, `name`,
  `last_updated`, `gs1_country`, `gtinType`, `offers_count`, `min_price`, `currency`,
  `categories`, `url`. The categories are free text, in French capitals, separated by `|` and
  `>` (for example `LIGHTING | LUMINAIRE > LAMPE A POSER`). **No photo.** The direct download is
  limited to 256 kB/s per client (stated on the page), so a full download takes about seven
  hours. The data.gouv.fr dataset "Base de codes barres, noms et catégories produits" points to
  the same page, but its GTIN file is dated 11 March 2025 and weighs 100 MB: it is an old extract,
  not a mirror of the weekly export. No free public API: the site's own API needs a token, the
  professional one is paid.
- **Open Products Facts** and **Open Food Facts**: a free read API, `GET
  /api/v2/product/<code>.json`, that asks every application for a `User-Agent` of the form
  `AppName/Version (contact)` and allows 15 product reads a minute per address
  (<https://openfoodfacts.github.io/openfoodfacts-server/api/>). The database is under the ODbL,
  its contents under the Database Contents License, **its photos under CC BY-SA 3.0**
  (<https://world.openfoodfacts.org/terms-of-use>): attribution and share-alike. A product of the
  other database answers `status: 0` with "product found with a different product type".

## Decision

- **A field `ean`** on a stock item and on an item: digits only, 8, 12, 13 or 14 of them
  (EAN-8, UPC-A, EAN-13, GTIN-14) with a valid check digit (GS1 modulo 10), stored as typed once
  spaces and dashes are taken out, optional. It is a field like the others: in the forms, the API,
  the sync API (same name, ADR 0077), the exports and the search of the stock and of the list of
  items. Two stock items may share a code (two tins of the same paint).
- **A new app `barcodes`**, so that the stock and the rooms only gain a field: the check of a
  code, the lookup, the local catalogue, the cache of answers and the attribution of a photo.
- **The order of the lookup** is the local catalogue, then Open Products Facts, then Open Food
  Facts; the first that knows the code answers. Every code is compared as a GTIN-14 (padded with
  zeros on the left), so that a UPC-A and its EAN-13 are the same product.
- **The local catalogue, off by default** (`barcode_catalogue_enabled`). The command
  `import_barcode_catalogue` reads the export **as a stream** (the CSV member of the ZIP is
  inflated and parsed row by row, by batches of 5,000) into one table,
  `barcodes_catalogueproduct`, keyed by the GTIN as an integer (the row identifier of SQLite, no
  second index). It keeps only the columns the sheet needs: GTIN, brand, model, name and
  categories. By default it keeps the **categories useful in a house**, a list of keywords in
  the module (household appliances, DIY, tools, lighting, bulbs, batteries, filters, heating,
  plumbing, electricity, garden, cleaning, paint, hardware); `--all-categories` keeps everything.
  Its progress is a row (`CatalogueImport`: source, size, rows read, state): stopped at any point,
  the next run skips the rows already read and goes on, and a new file starts over. When the
  catalogue is on, a weekly task downloads the export with HTTP ranges (a download that stops goes
  on where it was), then imports it, by slices of at most four minutes that queue the next one, so
  that no slice outlives the worker's timeout. **A lookup in the catalogue never leaves the
  server**: it is the sovereign way.
- **The online lookup, off by default** (`barcode_lookup_online`). An administrator turns it on;
  when an administrator scans a code while it is off, the answer offers to turn it on (a link to
  the setting). **Only the code leaves the server**: no name, no home, no account, no cookie; the
  `User-Agent` is `HouseCairn/<version> (+https://github.com/HouseCairn/housecairn)`. The call
  goes through `core.netguard.fetch` (public addresses only, every redirect checked, 1 MB at most,
  6 seconds for both sources together), and the photo too (5 MB, 6 seconds). It runs **in the
  background worker** (ADR 0070): the request saves a `BarcodeLookup` row with its state
  (`running`, `done`, `not_found`, `failed`) and queues the task; the screen asks again every two
  seconds, at most twelve times. The answers are kept in that row, **a cache per code** for 30
  days (not found: 1 day), shared by the instance, so a code scanned twice calls nobody the
  second time. Off on a demonstration instance (`demo.mode.FORCED`), like the preview of links.
  Rate limit: 10 lookups a minute per person and 30 per home, under the 15 a minute that the
  sources allow per address.
- **Filling a sheet** takes only the empty fields, and never without the person: the answer
  shows what was found next to each field (name, brand, model, category, photo), and only
  *Use these values* writes them, through the same form and services as a change by hand. A
  field that has a value keeps it. The category goes in the notes of a stock item (it has no
  category) and in the notes of an item.
- **The photo** of Open Products Facts or Open Food Facts, when there is one (`image_front_url`,
  else `image_url`), is downloaded by the task, then stored as any photo is (ADR 0030):
  re-encoded, without EXIF, with its thumbnail, counted in the space of the home. It is used only
  when the item has no photo yet, or for the label of a stock item that has none. Its
  **attribution** is stored with it (`source`, `source_url` of the product page, `license` =
  `CC BY-SA 3.0`, `author` = the `creator` the API gives) and shown under the photo, with a link
  to the licence. The Nudger catalogue has no photo.
- **The scanner.** A *Scan* button next to the field, on the sheet and on the creation form,
  opens the camera. It uses the browser's `BarcodeDetector` when there is one, otherwise the
  vendored ZXing library (`static/vendor/zxing/`, `@zxing/library` 0.23.0, Apache-2.0, loaded
  only when the button is pressed, from the instance, never from a CDN). The licence of every
  vendored file is checked by a test against the list of licences of the dependency review
  (ADR 0014). Typing the code by hand always works, without camera and without script. The
  `Permissions-Policy` header allows the camera to the instance's own pages (`camera=(self)`,
  it was `camera=()`), and no other origin; the microphone stays off.
- **Licences of the data.** The About page names the sources and their licences (ODbL for the
  data, CC BY-SA 3.0 for the photos). HouseCairn does not ship the catalogue: an administrator
  downloads it, so `NOTICE` gains nothing; the ODbL's share-alike applies to whoever publishes a
  database derived from it, which a home does not do.

## Consequences

- An instance that turns nothing on calls nobody and stores nothing more than the codes typed.
- The full catalogue weighs several gigabytes and takes hours to download (the source's limit),
  so the default keeps only the useful categories; the figures measured are in
  [the administration guide](../administration/barcodes.md).
- A lookup that finds nothing still leaves the code on the sheet.
- The mobile app gains the field `ean` in the sync API; it can scan with its own camera library
  and call the same lookup endpoint.
