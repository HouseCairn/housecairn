<p align="center"><img src="design/brand/housecairn-logo.svg" alt="HouseCairn" width="360"></p>

# HouseCairn

Self-hosted planner for home maintenance and renovation work, for the people
who live in the house: what needs doing, in which room, when, with what and for
how much.

[![CI](https://github.com/HouseCairn/housecairn/actions/workflows/ci.yml/badge.svg?event=merge_group)](https://github.com/HouseCairn/housecairn/actions/workflows/ci.yml?query=event%3Amerge_group)
[![Image](https://github.com/HouseCairn/housecairn/actions/workflows/image.yml/badge.svg?branch=main)](https://github.com/HouseCairn/housecairn/actions/workflows/image.yml?query=branch%3Amain)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/HouseCairn/housecairn/badge)](https://securityscorecards.dev/viewer/?uri=github.com/HouseCairn/housecairn)
[![License: AGPL-3.0-only](docs/assets/license-badge.svg)](LICENSE)
![status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)
[![Release](https://img.shields.io/github/v/release/HouseCairn/housecairn)](https://github.com/HouseCairn/housecairn/releases)
[![Container image](https://img.shields.io/badge/image-ghcr.io-2496ED?logo=docker&logoColor=white)](https://github.com/HouseCairn/housecairn/pkgs/container/housecairn)
[![Docs](https://img.shields.io/badge/docs-docs.housecairn.app-41BDF5)](https://docs.housecairn.app/)
![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![Django 5.2](https://img.shields.io/badge/Django-5.2-092E20?logo=django&logoColor=white)
[![Home Assistant add-on](https://img.shields.io/badge/Home%20Assistant-add--on-41BDF5?logo=homeassistant&logoColor=white)](https://github.com/HouseCairn/home-assistant-addons)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/HouseCairn/home-assistant-housecairn)

> [!WARNING]
> HouseCairn is alpha software. Expect bugs and breaking changes between versions.
> Back up your data before every upgrade.

<p align="center">
  <img src="docs/assets/readme-today-desktop.png" width="600"
       alt="The Today screen on a computer: two overdue tasks (change the smoke detector battery, 3 days late; clean the cooker hood filter, 1 day late), two tasks due today and the boiler service coming up, each with its room, its item and a Done button.">
  <img src="docs/assets/readme-item-phone.png" width="190"
       alt="An item on a phone: the gas boiler of the utility room, with its brand and model, installation date, warranty end date, a Photos section and the boiler service task.">
</p>

Documentation, in English and French: <https://docs.housecairn.app>.

## What it does

- **Today**: overdue tasks, tasks due today and what is coming up, each marked
  done in one tap, with Skip and Undo.
- **Recurring maintenance**: every few days, weeks, months or years, on a fixed
  date or counted from the last time it was done, optionally limited to a
  season.
- **Rooms and items**: the boiler, the smoke detectors, the windows, with brand,
  model, warranty and photos; each room remembers its paint, floor and wall
  coverings.
- **Projects and budget**: a renovation with its steps, its quotes and its
  budget lines; planned costs by room and by project, in the currency of the
  home.
- **Stock and QR labels**: spare filters, paint and batteries with an alert
  threshold; a printed label on an item opens its page.
- **A household**: invite the others with a role (owner, member, read-only),
  sign in with a password, a passkey or single sign-on (OpenID Connect).
- **Your data, out and in**: reminders through ntfy, email, Telegram and other
  [Apprise](https://github.com/caronc/apprise) services, calendar feeds, CSV
  import and export, a full export of a home, and a REST API.

## Demo

Try it in a browser at <https://demo.housecairn.app>, with one of these public
accounts:

| Account | Address | Password |
| --- | --- | --- |
| Administrator | `admin@example.com` | `housecairn-demo` |
| User | `user@example.com` | `housecairn-demo` |

What you enter there is public, and everything is erased and loaded again every
night at 03:00 (Europe/Paris). The demo sends nothing (no email, no
notification) and its settings are read only.

## Quick start

Try it on your computer, from the source, with [uv](https://docs.astral.sh/uv/)
and GNU gettext installed:

```sh
git clone https://github.com/HouseCairn/housecairn.git && cd housecairn
cp .env.example .env    # development settings (DEBUG=true)
mkdir -p data           # the SQLite database and the uploaded files
uv sync --locked
uv run python manage.py compilemessages --ignore=.venv --ignore=docs
uv run python manage.py migrate
uv run python manage.py setup_code   # prints a one-time first-account code
uv run python manage.py runserver
```

Open <http://localhost:8000/setup/>, give the code, your name, your email
address and a password: you are the administrator of the instance. Then create
your home.

To run it on a server, HouseCairn is a single container,
`ghcr.io/housecairn/housecairn`, for `linux/amd64` and `linux/arm64`, with
SQLite by default and PostgreSQL as an option, behind a reverse proxy that
terminates HTTPS. Follow [Install with Docker Compose](https://docs.housecairn.app/getting-started/install/),
then [First start](https://docs.housecairn.app/getting-started/first-start/).

## Integrations

HouseCairn works fully on its own; every integration is optional and off by
default.

- **Home Assistant**, experimental, in two repositories of its own:
  - [home-assistant-addons](https://github.com/HouseCairn/home-assistant-addons):
    HouseCairn as a Home Assistant app (add-on), opened in Home Assistant's
    sidebar through ingress.
  - [home-assistant-housecairn](https://github.com/HouseCairn/home-assistant-housecairn):
    an integration, installed with HACS, that shows the tasks of a home as
    sensors and a to-do list, and marks a task done from Home Assistant. It
    uses a HouseCairn API token; HouseCairn never holds a Home Assistant token.
- **Notifications** through [Apprise](https://github.com/caronc/apprise) URLs,
  and **calendar feeds** (iCalendar) for any calendar application.
- **REST API** (`/api/v1/`), with tokens limited to chosen homes and rights:
  see [API](https://docs.housecairn.app/api/).
- **Paperless-ngx** (documents) and **Sure** (actual spending): planned.

## Project status

**Alpha: [v0.3.2](https://github.com/HouseCairn/housecairn/releases/tag/v0.3.2).**
It has not been used in a real home for any length of time yet, and what it
stores can still change shape between 0.x versions: try it, and keep a backup
of what you enter. Released images are tagged `X.Y.Z`, `X.Y` and `latest`;
`edge` and `sha-<commit>` follow the `main` branch and are for testing only.

## Privacy and security

- **Self-hosted**: your data stays on your server, in one volume (or your
  PostgreSQL database).
- **No telemetry, no tracker, no external resource**: the pages load nothing
  from another site, and HouseCairn makes no outgoing connection you have not
  configured (email, notifications, single sign-on).
- **Secure defaults**: HTTPS required in production, strict content security
  policy, rate-limited sign-in, an instance administrator who cannot read the
  homes' content. Every published image carries a signed build provenance and
  an SBOM: see [Verify an image](https://docs.housecairn.app/security/verify-image/).
- To report a vulnerability, see [SECURITY.md](SECURITY.md); do not open a
  public issue.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Contributions require signing the
[Contributor License Agreement](CLA.md) and a `Signed-off-by` line on each
commit. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md). AI coding
agents start with [AGENTS.md](AGENTS.md).

HouseCairn is developed with an AI coding agent, under human review. Each pull
request is reviewed by a second AI model, then read and merged by the human
maintainer, who is responsible for what enters the repository. Every pull
request states which AI tool and model were used.

## License

HouseCairn, by Robvred. Copyright (C) 2026 Robvred.

HouseCairn is licensed under the
[GNU Affero General Public License v3.0 only](LICENSE)
(`AGPL-3.0-only`), with the additional terms in [NOTICE](NOTICE): modified
versions must keep the author attribution given there, and the HouseCairn name
and logo are not licensed as a trademark.

Self-hosting HouseCairn is free, for everyone, under this license. An official
paid hosted offering may exist in the future; it would not change the license
of the code.
