# 0042. Rate limits, quotas per home, language, time zone and name of the instance

- Status: accepted
- Date: 2026-11-08

## Context

ADR 0035 paired the instance settings with environment variables and left five of them for
later: the rate limits, quotas of files per home, the default language, the time zone and
the name of the instance. Until now the rate limits were fixed in `settings.py`, the time
zone was the variable `TIME_ZONE` only, a file was limited by `HOUSECAIRN_PHOTO_MAX_BYTES`
only, and nothing bounded what a home stores: with open sign-up, anyone could fill the
disk of the instance.

## Decision

All of them join the registry of ADR 0035, without changing its mechanism: variable, then
database, then default; a variable locks the field and the screen shows the value in force;
every value is checked for its range on the web, by the API and in the service, and a
restored default is checked on the state it leaves. All apply within the five seconds of the
settings snapshot, without a restart.

**Rate limits** (section *Rate limits*). Nine numbers: sign-ins a minute per address (30),
failed sign-ins of an account in five minutes (5), sign-ups a minute (10) and a day (60) per
address, password reset requests a minute per address (20), invitations sent a minute (10)
and a day (50) per person, invitation links opened a minute per address (30), and tests of
the mail server or of an issuer a minute per administrator (5). The defaults are the limits
in force before. Each is a whole number from 1 (0 would refuse everything) to a maximum
(1000 a minute, 100 000 a day, 60 tests a minute): no limit can be switched off. A day
cannot allow fewer than a minute. `settings.ACCOUNT_RATE_LIMITS` is a mapping computed at
each read (`instance.ratelimits`), which django-allauth reads at each limited request. The
fixed parts stay fixed: ten failed sign-ins a minute per address, five reset emails a
minute per address, five account erasures a minute per person. Changing a limit starts its
counters again, since allauth keys them on the rate. The counters stay per process unless
`HOUSECAIRN_CACHE_URL` shares them (ADR 0025).

**Quotas per home** (section *Files of a home*): the largest file (10 MB, from 10 KB to
100 MB, the variable `HOUSECAIRN_PHOTO_MAX_BYTES` of ADR 0030, now a setting), the space for
the files of a home (5 GB) and their number (5000). The same quotas apply to every home.

- **Measured on disk**, in the directories that every app declares for a home (ADR 0036):
  photos of rooms and items, attachments of tasks, label photos of the stock, and the
  thumbnails, which take space but are not files of their own. The files of deleted
  records count until the purge removes them: otherwise deleting and adding again would
  fill the disk. Measuring the disk rather than the rows also counts what an app would
  forget to record, and needs no migration.
- **Every way to store a file** goes through `homes.quotas.room_for`, from the service
  layer, so the screens and the API alike: a photo of a room or an item, an attachment of a
  task (each file of a multiple upload), a label. A new label that replaces the old one is
  not a new file. Over a quota, the upload is refused with a message that gives the numbers
  and says to delete files or ask the administrator; nothing stays on disk.
- **Two uploads at once** cannot both pass: `room_for` takes a write lock on the home (an
  update that changes nothing: a row lock on PostgreSQL, the database lock on SQLite) before
  it measures, and keeps it until the file and its row are written.
- **The administrator sees the usage** on **Instance administration, Files of the homes**
  and at `GET /api/v1/instance/usage/`: each home by its identifier, its bytes, its files
  and the share of the quota closer to its limit, the fullest first. No name, member or
  file: the rule of ADR 0022 holds, and the query reads only identifiers.

**Language** (section *Name, language and time zone*). The default language, `en` or `fr`
(`HOUSECAIRN_DEFAULT_LANGUAGE`, default `en`), serves the people whose browser asks for no
language the instance offers. The person's choice always wins: the language cookie, then
the browser's `Accept-Language`, matched as Django does (`fr-CA` gives `fr`). A person's own choice, saved in their
display preferences, goes before the cookie and the browser (ADR 0045), on every page and in
everything sent to the person ([ADR 0072](0072-one-language-per-person-everywhere.md)).
An invitation email sent without the language of the person who invites uses the default.

**Time zone**: an IANA zone (the variable `TIME_ZONE` of ADR 0012, default `UTC`), checked
against the zones Python knows. It is activated for each request, so dates on pages follow
it, and `tasks.services.today()` reads it, so "today" is the same in a request and in a
worker (ADR 0032). There is no zone per person or per home yet.

**Name of the instance** (`HOUSECAIRN_INSTANCE_NAME`, default `HouseCairn`), at most 64
characters, one line, no control or formatting character (no text direction trick). It is
shown in the app bar (in place of the product name on a phone, below the logo elsewhere; the
logo stays the product's), at the end of every page title, in the web app manifest, at the
start of every email subject and in the header and signature of the emails. A built-in
template tag, `{% instance_name %}`, gives it to every template, emails included, without a
context processor. Django escapes it in pages and HTML emails like any variable; the manifest
escapes `<`, `>` and `&` as `json_script` does. In a subject it cannot add a header: it has no
line break, and Django refuses one anyway. Empty, it shows the product name. The sentences
that present the product ("an account on HouseCairn, the planner for...") keep the product
name; the page of a server error (`500.html`) too, since it is rendered without the database.

## Consequences

- A deployment that sets none of the new variables behaves as before; one that set
  `HOUSECAIRN_PHOTO_MAX_BYTES` or `TIME_ZONE` keeps them, now locking their field. An
  invalid `HOUSECAIRN_PHOTO_MAX_BYTES` stops the start-up with the others (ADR 0035).
- Measuring a home walks its directories at each upload: a few milliseconds for thousands of
  files. The usage screen walks every home; for a large instance it is a slow page, not a
  slow upload.
- Until the purge of deleted records exists, a deleted file keeps counting: a household that
  reaches a quota asks the administrator for more.
- Under SQLite, uploads to the instance wait for each other while one is written, as every
  write already does.
