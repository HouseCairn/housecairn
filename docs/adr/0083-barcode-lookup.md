# 0083. Barcode lookup: Open Products Facts then Open Food Facts, behind a list of providers

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

- **Open Products Facts** and **Open Food Facts**: a free read API, `GET
  /api/v2/product/<code>.json`, that asks every application for a `User-Agent` of the form
  `AppName/Version (contact)` and allows 15 product reads a minute per address
  (<https://openfoodfacts.github.io/openfoodfacts-server/api/>). The database is under the ODbL,
  its contents under the Database Contents License, **its photos under CC BY-SA 3.0**
  (<https://world.openfoodfacts.org/terms-of-use>): attribution and share-alike. A product of the
  other database answers `status: 0` with "product found with a different product type".
- **Nudger (open4goods)**, <https://nudger.fr/opendata/gtin>: 114,307,320 barcodes (update of
  4 June 2026 shown on the page), under the ODbL, with a brand, a model, a name and categories,
  **no photo**. It has **no free API**: the site's own API (front-api) needs a JWT, the
  professional (B2B) one is paid. Its data comes only as one weekly export, a compressed CSV of
  6.0 GB, and the direct download is limited to 256 kB/s per client (stated on the page), about
  seven hours. The data.gouv.fr dataset "Base de codes barres, noms et catégories produits"
  points to the same page, but its GTIN file is dated 11 March 2025 and weighs 100 MB: an old
  extract, not a mirror of the weekly export.

## Decision

- **A field `ean`** on a stock item and on an item: digits only, 8, 12, 13 or 14 of them
  (EAN-8, UPC-A, EAN-13, GTIN-14) with a valid check digit (GS1 modulo 10), stored as typed once
  spaces and dashes are taken out, optional. It is a field like the others: in the forms, the API,
  the sync API (same name, ADR 0077), the exports and the search of the stock and of the list of
  items. Two stock items may share a code (two tins of the same paint).
- **A new app `barcodes`**, so that the stock and the rooms only gain a field: the check of a
  code, the providers, the lookup, the cache of answers and the attribution of a photo.
- **Providers.** A source is a class of `barcodes.providers`: a `name` (the key kept in the
  cache), an `attribution` (label, home page, licence and its address, shown with the data and
  the photo) and `lookup(ean)`, which returns what the source knows of the code, `None` when it
  does not know it, or raises `UnavailableError` when it could not be read. `enabled()` says
  whether the source can be asked now (true by default). The **ordered list `PROVIDERS`** in the
  code is the order of the lookup, and the first that knows the code answers: **Open Products
  Facts, then Open Food Facts** (one class for both, they share the API). A source that needs a
  free account (a key) is added with its class, an instance in the list and, for the key, a
  setting of the instance read by its `enabled()`, so that it is skipped while the key is not
  set; the lookup, the cache, the views, the API and the attribution do not change. Every code is
  compared as a GTIN-14 (padded with zeros on the left), so that a UPC-A and its EAN-13 are the
  same product.
- **The online lookup, off by default** (`barcode_lookup_online`). An administrator turns it on;
  when an administrator scans a code while it is off, the answer offers to turn it on (a link to
  the setting). **Only the code leaves the server**: no name, no home, no account, no cookie; the
  `User-Agent` is `HouseCairn/<version> (+https://github.com/HouseCairn/housecairn)`. The call
  goes through `core.netguard.fetch` (public addresses only, every redirect checked, 1 MB at most,
  6 seconds for all the providers together), and the photo too (5 MB, 6 seconds). It runs **in the
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
  **attribution** is stored with it (`source`, `source_url` of the product page, `license` and
  `license_url` of its provider, `author` = the `creator` the API gives) and shown under the
  photo, with a link to the licence. A cached photo whose provider has left the list is not used,
  since it could not be credited.
- **The scanner.** A *Scan* button next to the field, on the sheet and on the creation form,
  opens the camera. It uses the browser's `BarcodeDetector` when there is one, otherwise the
  vendored ZXing library (`static/vendor/zxing/`, `@zxing/library` 0.23.0, Apache-2.0, loaded
  only when the button is pressed, from the instance, never from a CDN). The licence of every
  vendored file is checked by a test against the list of licences of the dependency review
  (ADR 0014). Typing the code by hand always works, without camera and without script. The
  `Permissions-Policy` header allows the camera to the instance's own pages (`camera=(self)`,
  it was `camera=()`), and no other origin; the microphone stays off.
- **Licences of the data.** The About page names the sources and their licences (ODbL for the
  data, CC BY-SA 3.0 for the photos). HouseCairn ships none of their data, so `NOTICE` gains
  nothing; the ODbL's share-alike applies to whoever publishes a database derived from it, which
  a home does not do.

## Options set aside

- **A local copy of the Nudger catalogue** (set aside by the maintainer on 2026-11-15). It was
  the only way to use Nudger, since it has no free API, and it would have kept every lookup on
  the server. A first version of this pull request imported the export as a stream into a table,
  by default with only the categories useful in a house, resumed after a stop, refreshed every
  week. It was measured on the first 2,000,000 rows of the export of 2026-11-15 (SQLite, 8 cores),
  then extrapolated to the 114.3 million rows: house categories only, about 12.5 million products,
  **about 3.3 GB** in the database and **about 6 hours** of import; all categories, **about
  22 GB** and about 13.5 hours; on top of the download, about seven hours at 256 kB/s. Weighed
  against what it brings (no photo, and a lookup that the open databases already answer for most
  products of a house), the maintainer found it out of proportion and dropped it; no table and no
  migration of it remains.

## Future options

- **Open Icecat** (<https://icecat.biz>), the free part of the Icecat catalogue of products,
  which needs a free account: a possible provider for appliances and electronics, added as a
  class of `barcodes.providers` with its key in a setting, if its terms (attribution, use of the
  photos) fit. Not studied further, and no commitment.

## Consequences

- An instance that turns nothing on calls nobody and stores nothing more than the codes typed.
- A lookup that finds nothing still leaves the code on the sheet.
- A new provider is one class and one line in `PROVIDERS`; its attribution comes with it.
- The mobile app gains the field `ean` in the sync API; it can scan with its own camera library
  and call the same lookup endpoint.
