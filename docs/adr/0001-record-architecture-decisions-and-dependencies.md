# 0001. Record architecture decisions; runtime dependencies

- Status: accepted
- Date: 2026-11-07

## Context

Housecairn is built in small pull requests, by contributors and by an AI agent
under human review. Decisions taken in one pull request must be visible to the
next one, and the reasons behind them must survive. Every runtime dependency is
also code we ship and must keep up to date.

## Decision

Architecture decisions are recorded in `docs/adr/`, one file per decision, in
the format context, decision, consequences.

Runtime dependencies are limited to well-maintained projects that replace code
we would otherwise write (principle "do not reinvent"):

| Package | Why |
| --- | --- |
| Django 5.2 LTS | Web framework, long-term support release. |
| djangorestframework | REST API (0004). |
| drf-spectacular, drf-spectacular-sidecar | OpenAPI 3.1 schema and a locally served interface (0004, 0011). |
| django-allauth, with its `mfa` and `socialaccount` extras | Local accounts, TOTP, passkeys, generic OpenID Connect (0007). The extras pull in `fido2` (passkeys) and the OAuth/OIDC client libraries (`requests`, `PyJWT`, `cryptography`); they are part of allauth's own feature set, not separate choices. |
| django-q2 | Background tasks (0008). |
| django-environ | Configuration from environment variables (0012). |
| django-csp | Content Security Policy (0011). |
| Babel | Writes an amount as the language of the page does, with the symbol of the currency (0054). BSD-3-Clause. Already in the lock for the documentation group. |
| python-dateutil | RFC 5545 recurrence rules and month arithmetic for tasks (0031). |
| Pillow | Decodes and writes again the photos of rooms and items, to check their content and drop their metadata (0030). |
| markdown-it-py | Renders the Markdown procedure of a task with raw HTML and images off (0031). MIT. |
| apprise | Sends reminders to the channels of a person (ntfy, Telegram, email...) through one URL per service (0043). BSD-2-Clause. Pulls in `markdown` and `requests-oauthlib`. |
| icalendar | Writes the iCalendar feed of the calendar and reads it back in the tests (0052). BSD-2-Clause. Needs python-dateutil and tzdata, already in the lock. |
| reportlab | Draws the PDF of the QR labels and encodes their QR codes, on the server, with no system library (0047). BSD-3-Clause. Pulls in nothing that was not already locked (Pillow, charset-normalizer). |
| cryptography | Encrypts the secrets an administrator stores from the interface, with a key derived from `SECRET_KEY` (0035). Already installed by django-allauth's passkey support; now a direct dependency. |
| django-stubs-ext | Lets generic classes such as `ModelAdmin[Home]` be subscripted at runtime for the type checker. Companion of django-stubs, maintained by the same project, no runtime behaviour otherwise. |
| django-oauth-toolkit (3.4.x) | OAuth 2 authorization server for the mobile app: authorization code with PKCE, refresh token rotation with reuse detection, revocation, tokens stored hashed (0078). BSD-2-Clause. Builds on oauthlib and requests, already in the lock. |
| jwcrypto | Pulled in by django-oauth-toolkit, for OpenID Connect, which stays off (0078). LGPL-3.0-or-later, compatible with AGPL-3.0-only. |

Optional runtime dependency, installed with the `postgres` extra
(`uv sync --extra postgres`) only by instances that use PostgreSQL:

| Package | Why |
| --- | --- |
| psycopg, with its `binary` extra | PostgreSQL driver supported by Django. The `binary` extra ships a prebuilt `libpq`, so no compiler or system library is needed. Added by ADR 0014, which tests every pull request on PostgreSQL. |

Development tools: uv (locking), ruff, mypy with django-stubs, djangorestframework-stubs and
types-reportlab, pytest, pytest-django, pytest-cov.

## Consequences

- Adding a runtime dependency requires a new ADR, or an amendment to this one in
  the same pull request.
- `uv.lock` pins every package; updates go through pull requests.
