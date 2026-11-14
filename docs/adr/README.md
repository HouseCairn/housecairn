# Architecture decision records

Each significant architecture decision is recorded here, in a short format:
context, decision, consequences. A record is never rewritten once accepted: a
later record supersedes it.

| No. | Decision | Date |
| --- | --- | --- |
| [0001](0001-record-architecture-decisions-and-dependencies.md) | Record decisions; runtime dependencies | 2026-11-07 |
| [0002](0002-single-service-layer.md) | A single service layer for web and API | 2026-11-07 |
| [0003](0003-web-api-parity.md) | Web/API parity, checked by a test | 2026-11-07 |
| [0004](0004-versioned-rest-api.md) | Versioned REST API, OpenAPI 3.1, cursor pagination | 2026-11-07 |
| [0005](0005-stable-identifiers-and-soft-deletion.md) | UUID identifiers, timestamps, soft deletion | 2026-11-07 |
| [0006](0006-home-scoping.md) | Every query scoped to the user's homes | 2026-11-07 |
| [0007](0007-authentication-and-api-tokens.md) | django-allauth, scoped API tokens, OAuth 2 later | 2026-11-07 |
| [0008](0008-background-tasks.md) | Background tasks with django-q2 and the ORM broker | 2026-11-07 |
| [0009](0009-design-tokens.md) | Design tokens in one JSON file | 2026-11-07 |
| [0010](0010-stable-urls.md) | Stable addresses for every record | 2026-11-07 |
| [0011](0011-no-external-resources-and-csp.md) | No external resources, strict CSP | 2026-11-07 |
| [0012](0012-configuration-and-security-defaults.md) | Configuration by environment, safe defaults | 2026-11-07 |
| [0013](0013-internationalization.md) | English source, French shipped, no hard-coded strings | 2026-11-07 |
| [0014](0014-continuous-integration-and-supply-chain.md) | Continuous integration and supply-chain security | 2026-11-07 |
| [0015](0015-container-image-and-publication.md) | Container image, signed publication to GHCR | 2026-11-07 |
| [0016](0016-documentation-site.md) | Documentation site: Sphinx, two languages, screenshots, checks | 2026-11-07 |
| [0017](0017-visual-system-and-brand.md) | Visual system, brand and application shell | 2026-11-07 |
| [0018](0018-sign-in-page-and-passkeys.md) | Sign-in page: passkeys without a button, one button per provider | 2026-11-07 |
| [0019](0019-property-based-tests.md) | Property-based tests with Hypothesis | 2026-11-07 |
| [0020](0020-household-roles-and-invitations.md) | Household roles and invitations | 2026-11-07 |
| [0021](0021-per-home-single-sign-on.md) | Single sign-on required per home | 2026-11-07 |
| [0022](0022-instance-administrator-without-content.md) | Instance administrator without access to content | 2026-11-08 |
| [0023](0023-audit-log-export-erasure-and-email-alerts.md) | Audit log, export, erasure and security emails | 2026-11-08 |
| [0024](0024-open-sign-up-and-anti-robot-check.md) | Open sign-up and its anti-robot check | 2026-11-08 |
| [0025](0025-rate-limit-counters-and-workers.md) | Rate-limit counters and several web processes | 2026-11-08 |
| [0029](0029-rooms-items-and-room-memory.md) | Rooms, items and room memory | 2026-11-08 |
| [0030](0030-photo-storage-and-processing.md) | Photo storage and processing | 2026-11-08 |
| [0031](0031-tasks-occurrences-and-recurrence.md) | Tasks, occurrences and recurrence | 2026-11-08 |
| [0032](0032-dates-and-time-zone-of-tasks.md) | Dates and time zone of tasks | 2026-11-08 |
| [0033](0033-done-with-undo.md) | "Done" in one gesture, with undo | 2026-11-08 |
| [0034](0034-task-attachments.md) | Photos and files attached to tasks | 2026-11-08 |
| [0035](0035-instance-settings-in-the-interface.md) | Instance settings in the interface, paired with environment variables | 2026-11-08 |
| [0036](0036-export-rooms-photos-and-file-erasure.md) | Export of rooms and photos, erasure of files with the home | 2026-11-08 |
| [0037](0037-today-my-home-history-and-display-preferences.md) | Today, My home, History and display preferences | 2026-11-08 |
| [0038](0038-stock-suppliers-and-movements.md) | Stock: items, suppliers and an append-only ledger of movements | 2026-11-08 |
| [0040](0040-merge-queue.md) | Merge queue, required checks on each queue entry | 2026-11-08 |
| [0041](0041-sign-in-providers-in-the-interface.md) | Sign-in providers in the interface, last way to sign in | 2026-11-08 |
| [0042](0042-rate-limits-quotas-language-and-instance-name.md) | Rate limits, quotas per home, language, time zone and name of the instance | 2026-11-08 |
| [0043](0043-notifications-through-apprise.md) | Notifications through Apprise: channels of a person, reminders, quiet hours | 2026-11-08 |
| [0045](0045-generic-lists-with-other-tabs-and-audit-of-content.md) | Generic lists with "Other", tabs, language of a person and audit of content | 2026-11-08 |
| [0046](0046-stock-linked-to-tasks-and-rooms.md) | Stock linked to tasks and rooms, the stock follows Done | 2026-11-09 |
| [0047](0047-qr-labels.md) | QR labels and reports: an opaque code, a PDF made on the server, a reader who reports | 2026-11-08 |
| [0048](0048-export-format-and-restore.md) | Export format 2, restore of a home, tables as CSV and backup of the instance | 2026-11-10 |
| [0049](0049-task-templates.md) | A library of task templates, versioned in the repository | 2026-11-10 |
| [0050](0050-projects-quotes-and-budget-lines.md) | Projects, quotes and budget lines | 2026-11-09 |
| [0051](0051-budget-views-and-annual-envelope.md) | Budget views and the annual envelope | 2026-11-09 |
| [0052](0052-calendar-and-secret-ics-feeds.md) | Calendar views and secret, revocable iCalendar feeds | 2026-11-08 |
| [0053](0053-target-navigation-and-return-rule.md) | Target navigation and return rule | 2026-11-09 |
| [0054](0054-amounts-with-babel.md) | Amounts written by Babel, with the symbol of the currency | 2026-11-10 |
| [0055](0055-serving-under-a-path-prefix.md) | Serving the application under a path prefix | 2026-11-10 |
| [0056](0056-first-administrator-on-a-web-screen.md) | First administrator on a web screen, with a one-time code of the log | 2026-11-10 |
| [0057](0057-api-tokens-of-a-home-that-requires-a-provider.md) | API tokens of a home that requires a sign-in provider | 2026-11-10 |
| [0058](0058-contract-provider-is-a-contractor.md) | The provider of a maintenance contract is a contractor, and export format 3 | 2026-11-11 |
| [0059](0059-notifications-refuse-private-networks.md) | Notifications refuse private networks unless allowed, and `connect` checks its address | 2026-11-11 |
| [0060](0060-secret-key-apart-from-the-backups.md) | The secret key: rotation by `SECRET_KEY_FALLBACKS`, kept out of the backups | 2026-11-11 |
| [0062](0062-unit-price-of-stock-items.md) | Unit price of a stock item, planned cost of a task from its materials | 2026-11-12 |
| [0063](0063-search-field-with-chips-for-multiple-choices.md) | A search field with chips for multiple choices among the records of a home | 2026-11-12 |
| [0064](0064-every-apprise-service-with-a-fixed-format.md) | Every Apprise service with a fixed format, each one reviewed | 2026-11-12 |
| [0065](0065-installable-application-and-offline-reading.md) | Installable application and offline reading | 2026-11-11 |
| [0066](0066-notifications-on-the-device-by-web-push.md) | Notifications on the device by Web Push, on `cryptography` alone | 2026-11-12 |
| [0068](0068-one-date-format-and-decimal-mark-messages.md) | One date format per language, and a decimal field that says what it expects | 2026-11-11 |
| [0069](0069-restore-as-a-copy-with-new-identifiers.md) | Restore as a copy, with new identifiers | 2026-11-12 |
| [0070](0070-slow-network-calls-in-the-background.md) | Reading a link's page and testing a channel run in the background worker | 2026-11-12 |
| [0071](0071-demonstration-instance.md) | A demonstration instance: public accounts, nothing sent, reset every night | 2026-11-12 |
| [0072](0072-device-offered-by-default.md) | The device is offered by default: one offer, one tap | 2026-11-14 |
| [0073](0073-every-planned-amount-of-a-task-is-planned.md) | Every amount planned on a task is in Planned, once | 2026-11-14 |
| [0074](0074-one-language-per-person-everywhere.md) | One language per person, everywhere they read HouseCairn | 2026-11-14 |
