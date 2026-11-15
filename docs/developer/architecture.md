# Architecture

HouseCairn is a Django application with HTMX for the interactive parts, in a
single container. The main rules, each recorded in a decision:

- **One service layer** holds the business rules; web views and API views
  call it and hold none of their own
  ([ADR 0002](../adr/0002-single-service-layer.md)).
- **Web and API parity**: what the web interface does, the API does too,
  checked by a test ([ADR 0003](../adr/0003-web-api-parity.md)).
- **Every query is scoped** to the homes of the user, or of the API token
  ([ADR 0006](../adr/0006-home-scoping.md)).
- **Stable identifiers and addresses**: UUIDs, soft deletion, addresses that
  do not change ([ADR 0005](../adr/0005-stable-identifiers-and-soft-deletion.md),
  [ADR 0010](../adr/0010-stable-urls.md)).
- **Background tasks** with django-q2 and the database as broker
  ([ADR 0008](../adr/0008-background-tasks.md)).
- **No external resource** and a strict Content Security Policy
  ([ADR 0011](../adr/0011-no-external-resources-and-csp.md)).
- **English source, French shipped**, no hard-coded string
  ([ADR 0013](../adr/0013-internationalization.md)).
- **No table escapes the export of a home**: each app registers an exporter
  ([ADR 0023](../adr/0023-audit-log-export-erasure-and-email-alerts.md)), and
  `tests/test_export_registry.py` fails on a table of the project that the export
  does not read, unless it lists the table with its reason, as data of a home left out
  of the archive (then erased with the home) or as data of the instance or of a person.
- **A change log for the mobile app**: the `sync` application numbers every change of a home
  and keeps a clock per field ([Data model of the synchronization](sync-data-model.md),
  [ADR 0077](../adr/0077-sync-api-for-the-mobile-app.md)).
- **No query per row** in the export and the API lists of stock items and projects:
  `tests/test_query_counts.py` checks that 50 rows take as many queries as 5.

The code is in `src/housecairn/`, one Django application per domain
(`accounts`, `apitokens`, `core`, `events`, `homes`, `rooms`, `sync`).
