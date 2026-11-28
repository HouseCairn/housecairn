# 0093. An archive of before takes the values of the migrations

- Status: accepted. That an archive of an earlier release restores as an upgrade of its
  instance would leave it was decided on 2026-11-28 for the maintainer, who reviews it with the
  pull request of this record; the table of the fields and the schema are what this record
  decides with it.
- Date: 2026-11-28
- Amends: [0048](0048-export-format-and-restore.md) (a field added without a new version of
  the format), [0090](0090-real-cost-counts-the-stock-used.md) and
  [0091](0091-a-task-done-with-nothing-typed-pays-its-plan.md) ("Data of before": an archive
  of before restored its draws without a price and its occurrences without a plan)
- Related: [0058](0058-contract-provider-is-a-contractor.md) (format 3),
  [0079](0079-lots-of-the-server-mode-of-the-mobile-app.md) (format 4)

## Context

The export writes every concrete field of every section, and 0048 lets a later version add a
field "without a change of format". The restore, though, read every field of the model, and
refused a field that is not nullable when the archive did not have it. `items.ean`, added in
0.4.0 without a new version, made every archive of 0.1.0 to 0.3.2 that holds an item refused
("items, object 1, ean"), while the documentation said versions 2 and 3 restore. The tests
only made an older archive by rewriting the manifest of an archive of `main`, which has every
field, so none saw it. The JSON Schema of format 4 had the same flaw the other way: it made
every field required, so an archive of 0.5.2 no longer met it once `occurrences` and
`stock_draws` gained a field.

## Decision

- **A field added without a new version of the format is optional**, at restore and in the
  published schema. `homes.archive_versions.ADDED` lists, for each version of the format, the
  fields its archives may lack: those added while it was the current version, and after. A
  new field of a section goes into it in the pull request of its migration.
- **The value is the one of the migration that added the field**, for the rows already
  there: its default (`ean` and the warranty fields of an item, the attribution of a photo or
  a label), or what its data migration works out from the other rows of the archive: a draw
  gets the unit price of its stock item (0090), a done occurrence the plan of its task by the
  rule of 0091. A restored archive of before then holds what the instance that made it holds
  once upgraded, rather than less.
- **Every other field is required.** A field absent from an archive and not listed for its
  version is refused, nullable or not, as a link to several objects is: the table is the only
  place where a field may be missing.
- **A real archive of every release is a test fixture.** `tests/fixtures/archives/` holds one
  archive per published tag, made by the export of that tag on the demo home
  (`scripts/archive_fixtures.py`); `tests/test_restore_versions.py` restores each on an empty
  database and checks the rows and the values the table gives. The archives of format 4 of
  before are checked against the published schema.

## Consequences

- An archive of 0.1.0 to 0.3.2 restores again; one of 0.4.0 to 0.5.2 restores with the price
  of its draws and the plan of its done occurrences, as the upgrade gives them, an
  approximation said by the release notes of 0.6.0.
- An archive that lacks a field its version always had is refused where it was silently
  given `null` before.
- A release adds its archive to the fixtures; a field added without its entry in the table
  fails the test of the archives of before.
