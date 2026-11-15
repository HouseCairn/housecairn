# Barcodes

A stock item and an item of a room keep their barcode (EAN): it is typed, or scanned with
the camera of the phone or the computer. Scanning a code can also fill in the empty fields of
the form (name, brand, category) and bring a photo of the product, from open databases. That
lookup is **off by default**: an instance that does not turn it on keeps the codes typed and
calls nobody. The choices are in [ADR 0083](../adr/0083-barcode-lookup.md).

| Setting | Default | What leaves the server |
| --- | --- | --- |
| `HOUSECAIRN_BARCODE_LOOKUP_ONLINE` | `false` | The code, and nothing else. |

The setting is also in **Settings of the instance**, section **Barcodes**. When an
administrator scans a code while the lookup is off, the answer offers a link to turn it on.

## The online lookup

The worker asks [Open Products Facts](https://world.openproductsfacts.org), then
[Open Food Facts](https://world.openfoodfacts.org), in the background; the first that knows
the code answers.

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

The data of Open Products Facts and Open Food Facts is under the
[Open Database License](https://opendatacommons.org/licenses/odbl/1-0/), their photos under the
[Creative Commons Attribution-ShareAlike 3.0](https://creativecommons.org/licenses/by-sa/3.0/)
licence. When a person keeps a photo, HouseCairn downloads it, re-encodes it without its
metadata like any photo, and stores its **attribution** with it: the source, the page of the
product, the licence and, when the source gives it, its author. The attribution is shown under
the photo. A photo is only added to an item that has none, or as the label of a stock item that
has none: a photo taken by a member is never replaced.

The About page names these sources and their licences. HouseCairn ships none of their data, so
nothing changes in its `NOTICE`.
