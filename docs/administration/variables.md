# Environment variables

Set them in `deploy/production.env`. The ones marked in [Settings](settings.md) can also be
changed in the interface; a variable that is set locks its field there. The full list, with defaults, is in
[`deploy/production.env.example`](../../deploy/production.env.example) and
[`.env.example`](../../.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `ALLOWED_HOSTS` | (none: required) | Host names HouseCairn answers to, comma-separated. |
| `CSRF_TRUSTED_ORIGINS` | (none) | `https://` origins of those host names. |
| `HOUSECAIRN_BEHIND_TLS_PROXY` | `false` | `true` behind a reverse proxy that sets `X-Forwarded-Proto`. |
| `HOUSECAIRN_FORWARDED_ALLOW_IPS` | `127.0.0.1,::1` | Addresses or networks of that proxy, comma-separated: only their forwarding headers count (see [Reverse proxy](reverse-proxy.md)). |
| `HOUSECAIRN_URL_PREFIX` | (none: the root) | Path the instance is served under, such as `/housecairn` (see [Serving under a path prefix](url-prefix.md)). |
| `HOUSECAIRN_URL_PREFIX_HEADER` | (none: off) | Header in which a trusted proxy gives the path prefix of each request, such as `X-Ingress-Path`. |
| `HOUSECAIRN_ALLOW_SAME_ORIGIN_FRAMING` | `false` | `true` lets a page of the same origin show HouseCairn in a frame, as Home Assistant's Ingress panel does (see [Serving under a path prefix](url-prefix.md)). |
| `HOUSECAIRN_DISALLOWED_HOST_HINT` | (none) | Sentence the 400 page of a refused host name shows, for a host that knows which option to change; at most 300 characters (see [Settings](settings.md#the-page-of-a-refused-host-name)). |
| `SECRET_KEY` | generated in `/data/secret_key` | Signs sessions and encrypts the stored secrets. Set it here to keep the key apart from `/data` and its backups (see [Secrets](settings.md#secrets)). |
| `SECRET_KEY_FALLBACKS` | (none) | Former keys, comma-separated, still accepted after a rotation: the secrets they encrypted stay readable (see [Rotate the key](settings.md#rotate-the-key)). |
| `DATABASE_URL` | SQLite in `/data` | For example `postgres://housecairn:<password>@postgres:5432/housecairn`. |
| `TIME_ZONE` | `UTC` | Time zone of the households, such as `Europe/Paris`: it decides what today is. Also in the interface. |
| `HOUSECAIRN_DEFAULT_LANGUAGE` | `en` | `en` or `fr`, for browsers that ask for no language the instance offers. Also in the interface. |
| `HOUSECAIRN_INSTANCE_NAME` | `HouseCairn` | Name in the app bar, the page titles and the emails, at most 64 characters. Also in the interface. |
| `HOUSECAIRN_PHOTO_MAX_BYTES` | `10485760` | Largest photo or attachment, in bytes, from 10240 to 104857600. Also in the interface. |
| `HOUSECAIRN_HOME_STORAGE_MB`, `HOUSECAIRN_HOME_MAX_FILES` | `5120`, `5000` | Space (MB) and number of files each home may hold. Also in the interface. |
| `HOUSECAIRN_RATELIMIT_*` | see [Rate limits](rate-limits.md) | The rate limits, one variable each. Also in the interface. |
| `HOUSECAIRN_SIGNUP_OPEN` | `false` | Allow local sign-up from the web page; needs email. See [Open sign-up](sign-up.md). |
| `HOUSECAIRN_SIGNUP_POW_BITS` | `16` | Work of the anti-robot check of sign-up, from 8 to 24 bits. |
| `HOUSECAIRN_SIGNUP_ALLOWED_DOMAINS`, `HOUSECAIRN_SIGNUP_BLOCKED_DOMAINS` | (none) | Comma-separated domains that may, or may not, sign up. |
| `ACCOUNT_EMAIL_VERIFICATION` | `optional`, `mandatory` with open sign-up | Whether an address must be verified before sign-in. |
| `HOUSECAIRN_CACHE_URL` | `locmemcache://housecairn` | Cache of the rate-limit counters; `dbcache://housecairn_cache` shares them between web processes. See [Rate limits](rate-limits.md). |
| `OIDC_SERVER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | (none) | Single sign-on through any OpenID Connect provider. |
| `HOUSECAIRN_OIDC_SIGNUP`, `HOUSECAIRN_BOOTSTRAP_ADMIN_EMAIL` | `closed`, (none) | Who may get an account through single sign-on. |
| `HOUSECAIRN_SETUP_SCREEN` | `true` | The first-account screen (`/setup/`), opened by a one-time code written in the log, while there is no administrator. `false` if you create the administrator another way. See [First start](../getting-started/first-start.md). |
| `HOUSECAIRN_DEMO` | `false` | `true` makes a public demonstration instance: two public accounts, settings read only, nothing sent, everything erased every night. Never on an instance with real data. See [Demonstration instance](demo.md). |
| `EMAIL_HOST` | (none) | Outgoing mail server (SMTP); email is off while it is empty. See [Email](email.md). |
| `EMAIL_USE_SSL`, `EMAIL_TIMEOUT` | `false`, `15` | TLS from the first byte; seconds to wait for the server. See [Email](email.md). |
| `HOUSECAIRN_SITE_URL` | (none: set it in production) | Public address of the instance, such as `https://home.example.com`, used for links in emails and for the address a printed QR label holds, followed by `HOUSECAIRN_URL_PREFIX`. Empty: the address of the request, checked against `ALLOWED_HOSTS`; `check --deploy` warns when `EMAIL_HOST` is set without it. |
| `HOUSECAIRN_INVITATION_LIFETIME_DAYS` | `7` | Days an invitation link stays valid, from 1 to 90. |
| `HOUSECAIRN_NOTIFICATIONS_ENABLED` | `false` | Reminders through Apprise channels; off, nothing is sent. See [Notifications](../guides/notifications.md). |
| `HOUSECAIRN_NOTIFICATIONS_INTERVAL` | `5` | Minutes between two looks for reminders to send, from 1 to 60. |
| `HOUSECAIRN_NOTIFICATIONS_PRIVATE_NETWORKS` | `false` | Let channels send to private network addresses, for a ntfy or a Gotify of the local network. Off, they are refused. Also in the interface. See [Hardening](hardening.md). |
| `HOUSECAIRN_WEBPUSH_ENABLED` | `false` | Notifications on the device, by Web Push through the push service of each browser (Google, Mozilla, Apple, Microsoft). Also in the interface. See [Notifications](../guides/notifications.md#on-this-device). |
| `HOUSECAIRN_WEBPUSH_DISCREET` | `true` | A notification on the device says only what kind of message it is, with a link: no title of a task, no name of a home. Also in the interface. |
| `HOUSECAIRN_LINK_PREVIEW_ENABLED` | `false` | Read the title and the picture of a page when a link is added to a project. Off, nothing is read. The server only calls public addresses. Also in the interface. See [Projects](../guides/projects.md). |
| `HOUSECAIRN_BARCODE_CATALOGUE_ENABLED` | `false` | Look a scanned barcode up in a copy of the open Nudger catalogue kept on the server, refreshed every week. No lookup leaves the server; the download takes hours and gigabytes. Also in the interface. See [Barcodes](barcodes.md). |
| `HOUSECAIRN_BARCODE_LOOKUP_ONLINE` | `false` | When a code is not in the local catalogue, ask Open Products Facts, then Open Food Facts. Only the code is sent. Also in the interface. See [Barcodes](barcodes.md). |
| `HOUSECAIRN_DOCS_URL` | `https://docs.housecairn.app` | Address of this documentation, used by the help link of each screen. |
| `HOUSECAIRN_WEB_WORKERS` | `2` | Web server processes. |
| `HOUSECAIRN_TASK_WORKERS` | `2` | Task worker processes. |
| `HOUSECAIRN_STOP_TIMEOUT` | `20` | Seconds given to both to stop; keep it below Compose's `stop_grace_period`. |
| `HOUSECAIRN_PORT` | `8000` | Port inside the container. |
| `LOG_LEVEL` | `INFO` | Application log level; logs go to the container output. |

Set inside the image, not to be changed: `HOUSECAIRN_DATA_DIR=/data`,
`STATIC_ROOT=/app/static`, `DJANGO_SETTINGS_MODULE=housecairn.settings`.

## Email

HouseCairn sends email for invitations, address verification, password reset
and security alerts. Set `EMAIL_HOST` to your SMTP server to turn it on, with
`EMAIL_PORT` (`587`), `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
`EMAIL_USE_TLS` (`true`), `EMAIL_USE_SSL`, `EMAIL_TIMEOUT` and
`DEFAULT_FROM_EMAIL`. All of it is on the [Email](email.md) page, with what
becomes of the features that need a mailbox when there is none.

Links in emails point to `HOUSECAIRN_SITE_URL` when it is set. **Set it in
production.** Without it, a link takes the host of the request that triggered
it: `ALLOWED_HOSTS` limits that to the names the instance answers to, but a
proxy that lets a request choose its host, or several names in
`ALLOWED_HOSTS`, can make a link point where you did not intend.
`manage.py check --deploy` reports `EMAIL_HOST` set without
`HOUSECAIRN_SITE_URL`.

## PostgreSQL

SQLite is the default and is enough for a household. To use PostgreSQL, the
example `compose.yaml` has a `postgres` service, started only with the
`postgres` profile:

1. `cp deploy/postgres.env.example deploy/postgres.env` and set a password.
2. In `deploy/production.env`, set
   `DATABASE_URL=postgres://housecairn:<password>@postgres:5432/housecairn`.
3. Uncomment `depends_on` in the `housecairn` service, then
   `docker compose --profile postgres up -d`.

An existing PostgreSQL server works too: set `DATABASE_URL` to it.
