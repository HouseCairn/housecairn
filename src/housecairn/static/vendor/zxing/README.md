# ZXing for JavaScript (vendored)

- Version: 0.23.0
- Source: npm package `@zxing/library@0.23.0`, file `umd/index.min.js`, unmodified, renamed
  `zxing-library.min.js`.
- Tarball integrity, as published on the npm registry and checked against the download:
  `sha512-6fkkoFwP8CHxl6ugnPsj74PLJgX2iRv5zczGAyt5OBzQgxFhuhF0NCEc4t4OvSr8xAv2MRLlI0Iu9ZGDZQ2urA==`
- `zxing-library.min.js` SHA-256: `3ede94153fb0c5b67a12d7adff6decd827c2b22714fdc6faecf27a8f20937ea6`
- `zxing-library.min.js` subresource integrity (used in `static/js/barcode-scan.js`):
  `sha384-0ASr5PEWAMtTnWsn0PzKmioHVDA4+QqFiJr94io/0DCrGP6E1gRAmbO6O8y5WZW9`
- License: Apache-2.0, see `LICENSE`. Compatible with AGPL-3.0-only (docs/adr/0014, 0083).

Loaded only when a person presses *Scan* in a browser without `BarcodeDetector`, from the
instance, never from a CDN (docs/adr/0011, 0083). The tests
`test_vendored_zxing_matches_its_recorded_hash` and
`test_vendored_files_have_an_allowed_license` in `tests/test_barcodes_scan.py` check the
SHA-256 above and the licence. To upgrade: download the new tarball from the npm registry,
check its integrity against the registry, copy `umd/index.min.js`, then update this file,
the script and the test in the same pull request.
