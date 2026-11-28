# 0015. Container image and its publication

- Status: accepted
- Date: 2026-11-07

## Context

Housecairn is installed by people who run a home server, mostly with Docker
Compose, on x86 machines and on ARM boards. They expect one image that works
with SQLite out of the box and with PostgreSQL if they want it (ADR 0008, 0012),
and they will not read a hardening guide. The image must also be verifiable:
anyone should be able to check that a published image was built by this
repository's workflow from a given commit, and what it contains.

## Decision

### Base image

`python:3.13.<patch>-slim-trixie`, the official Python image on Debian 13
slim, pinned by digest with its tag on the same line; both stages use the same
digest so the virtual environment built in the first runs in the second.
Debian rather than Alpine: Debian's glibc takes the `manylinux` wheels, the
format every Python package with compiled code publishes first, for amd64 and
arm64. Today every locked runtime dependency with compiled code (`cffi`,
`cryptography`, `psycopg-binary`, `charset-normalizer`, `pyyaml`, `rpds-py`)
also publishes `musllinux` wheels for both, but that is not guaranteed for the
next dependency, and a missing wheel means a compiler and build headers in the
image build. musl also differs from glibc in ways that surface at run time
(DNS resolution, locales, thread stack size), which the tests on the runner,
a glibc system, would not see. A distroless base was not chosen:
the entry point needs a Python interpreter matching the one of the virtual
environment, and the slim image's small set of Debian packages is scanned
and patched by Debian.

### Build

Two stages:

1. **build**: uv (copied from its official image, same version as the CI)
   installs the locked dependencies with `uv sync --locked --no-dev --extra
   postgres --no-install-project` into `/app/.venv`, with bytecode compiled.
   The project is not installed as a package: its source is on `PYTHONPATH`
   (`/app/src`), so no unlocked build backend is downloaded. Then design
   tokens (`build_design_tokens`) and translations (`compilemessages`, GNU
   gettext installed in this stage only) are generated from their sources,
   and static files are collected with `CompressedManifestStaticFilesStorage`
   (hashed names, compressed copies).
2. **final**: the same base, without its `pip` (unused at run time, and the
   source of most scanner findings), a system user and group `housecairn`,
   UID/GID **10001**, and `/app` copied from the build stage, owned by root,
   hence read-only for the application.

`.dockerignore` excludes everything, then lets in only `pyproject.toml`,
`uv.lock`, `LICENSE`, `NOTICE`, `manage.py`, `design/` and `src/`: no `.git`,
no test, no local data or `.env` file can reach the image. The OCI labels
`source`, `licenses` (`AGPL-3.0-only`), `version` and `revision` are set from
build arguments.

### Runtime dependencies added

ADR 0001 is amended by this record:

| Package | Why |
| --- | --- |
| gunicorn | Production WSGI server, pure Python, MIT. Started with its control socket disabled (it would need a writable path) and its worker heartbeat files in `/dev/shm`. |
| whitenoise | Serves the collected static files from the application, with long-lived `immutable` cache headers for hashed names and precompressed copies. Keeps the image a single process tree, without a web server in front only for static files. MIT. |

### Run time

- **Single writable place.** Everything written at run time goes to `/data`:
  SQLite database, uploaded files (`/data/media`), the secret key, backups.
  The rest of the file system works mounted read-only (`--read-only`), with
  `/tmp` as a `tmpfs`. Python does not write bytecode at run time
  (`PYTHONDONTWRITEBYTECODE`; it is compiled at build time).
- **Entry point** `python -m housecairn.entrypoint` (`src/housecairn/entrypoint.py`,
  tested in `tests/test_entrypoint.py`):
  - creates `/data/media` and fails with an explanation if `/data` is not
    writable by the application user;
  - sets the process umask to `077`: files in `/data` are readable by the
    application user only;
  - uses `SECRET_KEY` from the environment if set, otherwise
    `/data/secret_key`, created on first start with 50 random bytes
    (`secrets.token_urlsafe`), mode 600, then reused;
  - applies the migrations (`migrate --noinput`), then starts gunicorn and the
    django-q2 worker (`qcluster`) and supervises them: if either stops, the
    other gets SIGTERM and the container exits with a non-zero status (128 +
    signal number if it was killed), so the container's restart policy applies;
    SIGTERM or SIGINT stops both, waits up to `HOUSECAIRN_STOP_TIMEOUT`
    seconds (20 by default) then kills what remains, and exits 0. As PID 1, it
    also collects orphaned processes. A tiny supervisor in the entry point was
    preferred to supervisord or s6: two processes, no configuration file, and
    behaviour covered by tests.
  - `healthcheck`, `backup` (SQLite online backup API into `/data/backups/`)
    and `manage <command>` subcommands.
- **No account is created.** The first administrator comes from the existing
  sign-in paths (ADR 0007): the first OpenID Connect sign-in, optionally pinned
  to `HOUSECAIRN_BOOTSTRAP_ADMIN_EMAIL`, or `manage createsuperuser`.
- **Health check.** `HEALTHCHECK` runs `entrypoint healthcheck`, which requests
  `/healthz` on the loopback with a host name taken from `ALLOWED_HOSTS`; the
  endpoint answers only `{"status": "ok"}` and needs no authentication
  (ADR 0012).
- **Proxy headers.** gunicorn trusts `X-Forwarded-Proto` from the addresses
  of `HOUSECAIRN_FORWARDED_ALLOW_IPS` only (`--forwarded-allow-ips`, default
  the loopback), and from nobody unless `HOUSECAIRN_BEHIND_TLS_PROXY` is true;
  Django applies the same list to every forwarding header (ADR 0012). Trusting
  any peer (`*`) was the first choice, since a proxy's address in a container
  network is not known in advance; it let a client that reaches the port
  directly pass for HTTPS and choose its address for the sign-in limits. The
  example `compose.yaml` therefore fixes its network's range, so that the
  address a proxy on the host comes from, the gateway, is known.

### Workflow `image.yml`

| Job | Trigger | Permissions | What it does |
| --- | --- | --- | --- |
| `Container image` | pull request, push to `main`, tag `v*` | `contents: read` | Builds the image for amd64 and runs `.github/scripts/image-smoke.sh` on it, with SQLite then with PostgreSQL: started with `--read-only --cap-drop ALL --security-opt no-new-privileges`, `/tmp` in memory and an empty volume; healthy; `/`, the sign-in page and a hashed static file answer; user 10001; writes outside `/data` and `/tmp` refused; secret key mode 600; clean stop on SIGTERM; restart on the same volume keeps the database and the key and applies no migration; killing the task worker stops the container. Validates `compose.yaml`. Scans the image with Grype: an inventory of every finding (job summary and `grype-inventory` artifact), then the fixable ones for code scanning and the check. |
| `Container image (code scanning)` | same, except pull requests from forks and Dependabot | `security-events: write` | Uploads the Grype SARIF of the previous job to code scanning; that file holds only vulnerabilities with a fix available. Runs no code of the pull request. |
| `Image changes` | pull request, merge queue entry, push | `contents: read` | Not a required check. Runs `.github/scripts/arm64-needed.sh` on the diff of the change and tells the next job whether `linux/arm64` must be built (amendment of 2026-11-09). |
| `Container image (multi-arch build)` | pull request, merge queue entry | `contents: read` | Builds `linux/amd64` with provenance and SBOM, as the publication does, without pushing, and `linux/arm64` too when `Image changes` reports a change that can affect it. Always reports a status. |
| `Publish image` | push to `main`, tag `v*`, after `Container image` passed | `contents: read`, `packages: write`, `id-token: write`, `attestations: write` | Builds and pushes both platforms to GHCR, then attests provenance and SBOMs. |

### Workflow `image-scan.yml`

| Job | Trigger | Permissions | What it does |
| --- | --- | --- | --- |
| `Published image scan` | daily, manual | none | Scans `ghcr.io/housecairn/housecairn:edge` (linux/amd64) with the same Grype release as `image.yml`; fails on a fixable high or critical vulnerability. Not a required check. |
| `Published image scan (code scanning)` | after the scan | `security-events: write` | Uploads its SARIF (fixable findings only) to code scanning, category `container-image`. |

**Multi-architecture**: QEMU emulation on the amd64 runner (the
`tonistiigi/binfmt` image, pinned by digest, run with `docker run`) and a
`docker-container` BuildKit builder (`moby/buildkit`, pinned by digest). The
image only installs prebuilt wheels and runs Python, so emulation is fast
enough, and one `docker buildx build --platform linux/amd64,linux/arm64`
produces the multi-platform image and its attestations in one step. Native
arm64 runners were not chosen: they would need one job per architecture, an
artifact per digest and a merge job, for a build of a few minutes.

**Tags** (`scripts/image_tags.py`, tested): on `main`, `edge` and
`sha-<7 hex>`; on a tag `vX.Y.Z`, `X.Y.Z`, `X.Y`, `latest` and `sha-<7 hex>`;
on a pre-release tag `vX.Y.Z-<pre>`, `X.Y.Z-<pre>` and `sha-<7 hex>` only.
Nothing is published from a pull request or another branch.

**Provenance and SBOM.** BuildKit generates SLSA provenance
(`--provenance=mode=max`) and an SPDX SBOM per platform (`--attest
type=sbom`, with the `docker/buildkit-syft-scanner` image pinned by digest),
stored with the image. The workflow then signs them with GitHub artifact
attestations (Sigstore, keyless, through the job's OIDC token): build
provenance on the multi-platform index digest, and the SPDX SBOM of each
platform, extracted with `docker buildx imagetools inspect`, on that
platform's digest. Attestations are also pushed to the registry. They are
checked with `gh attestation verify` (docs/install.md). Artifact storage
records are not created (`create-storage-record: false`), so the job needs no
`artifact-metadata` permission.

The card that specified this lot asked for `actions/attest-build-provenance`
and `actions/attest-sbom`. Both are now wrappers of **`actions/attest`**,
which their own documentation asks to migrate to (`attest-sbom`'s README
announces its deprecation); this workflow uses `actions/attest` directly, with
the same inputs.

**Vulnerability scan: Grype** (Anchore), installed from its GitHub release
archive whose SHA-256, taken from the release's checksums file, is pinned in
the workflow. Not Trivy: its release binary and its GitHub actions were
compromised in March 2026 (CVE-2026-33634, GHSA-69fq-xp46-6x23); Grype does
the same job with an equivalent database. The `Container image` job fails if
the image has a vulnerability of **high or critical** severity **with a fix
available** (`--only-fixed --fail-on high`). Code scanning (category
`container-image`) receives the same set, every severity but only what has a
fix (see the amendment of 2026-11-07 below). A vulnerability without a fix
does not block a release: there is nothing to update to.

**Amendment, 2026-11-07: code scanning receives fixable vulnerabilities only.**
The first scans put 189 alerts in the Security tab, 181 of them without any fix
published by Debian (glibc, SQLite, util-linux, systemd, tar, coreutils). The
project cannot act on those: no dependency update, no code change or base image
digest removes them, and they buried the few alerts that can be resolved. The
SARIF now comes from `grype --only-fixed` (an option of the pinned Grype
0.121.0: "ignore matches for vulnerabilities that are not fixed"), the same
filter as the blocking check, so the tab lists what the project can fix. The
job proves it on every run: the JSON of the same scan must contain no match
without a fix, and its match count must equal the number of SARIF results.
The blocking command itself is unchanged.

Nothing is dropped. A separate step, which cannot fail the job
(`continue-on-error`), scans again without the filter and writes to the job
summary the counts by severity and by package and the list of fixable
vulnerabilities; the full result (`grype-all.json` and a table) is the
`grype-inventory` artifact of the run, kept 14 days. The published image's
SBOM, attested on every release, lets anyone rescan it later.

An empty Security tab therefore does **not** mean "no known CVE": the image
carries vulnerabilities that Debian has not fixed yet, and the base image may
carry more tomorrow. They are listed in the inventory of each run. Alerts
already open without a fix close by themselves at the first analysis uploaded
after this change reaches `main`, because the new SARIF no longer contains
them.

**Versions and cooldown.** The tools pinned in the workflow (Grype, BuildKit,
binfmt, the SBOM scanner) were chosen among releases public for at least
seven days, as the Dependabot cooldown of ADR 0014 does for dependencies. The
Python base image digest is the latest of its tag: those rebuilds carry Debian
security updates. Dependabot updates the `FROM` lines (`docker` ecosystem) and
the PostgreSQL image of `compose.yaml` (`docker-compose` ecosystem). The
pinned tools of `image.yml` (environment variables) and the PostgreSQL image of
`ci.yml` and `image-smoke.sh` are updated by hand, together.

### Third-party actions

None. `image.yml` uses only actions published by GitHub: `actions/checkout`,
`actions/upload-artifact` v7.0.2 (the SARIF and the inventory), `actions/download-artifact` v8.0.2,
`actions/attest` v4.2.2 and `github/codeql-action/upload-sarif`. QEMU,
BuildKit and the registry login go through the `docker` command line rather
than Docker's actions.

### Checks to add to the `main` ruleset

Once each has passed once on GitHub:

- `Container image`
- `Container image (multi-arch build)`

Not required: `Container image (code scanning)` (skipped for forks and
Dependabot), `Publish image` (never runs on a pull request).

## Consequences

- One image, `ghcr.io/housecairn/housecairn`, for both databases and both
  architectures; an instance's state is the `/data` volume and nothing else.
- A host directory mounted on `/data` must belong to `10001:10001`.
- What only happens after a merge to `main` cannot be tested on a pull
  request: the push to GHCR, the attestations and the first publication of the
  package (its visibility and its link to the repository are set once, by the
  maintainer, after the first push).
- A fixable high or critical vulnerability in the base image or a dependency
  blocks pull requests until it is updated. Dependabot proposes the update
  for a dependency; for a Debian package of the base image, see the
  amendment of 2026-11-08 below.
- The `X.Y` and `latest` tags move to the most recently published release,
  even if it is a patch of an older line.

## Move to Python 3.13 (2026-11-07)

The image started on `python:3.12.16-slim-trixie`. The scanner (Grype) reported
eight CVEs against the Python 3.12.16 interpreter, with fixed versions in
3.13 or later. The CPython CNA records list:

- fixed in 3.13: CVE-2025-12781 (3.13.10), CVE-2026-3446 and CVE-2026-3479
  (3.13.13), CVE-2026-6019 (3.13.14), CVE-2025-15366 (3.13.15);
- not fixed in any 3.13 release: CVE-2025-15367 (fixed in 3.15.0a6),
  CVE-2026-103995 and CVE-2026-116566 (fixed in 3.16.0).

The project therefore moved to Python 3.13, which Django 5.2 LTS supports. The base image is now
`python:3.13.18-slim-trixie`, pinned by digest as before, in both stages. The
CI runs the tests, the type checks and the Django checks on 3.13, so that what
is tested is what is shipped. `requires-python` is `>=3.13,<3.14` and
`uv.lock` was regenerated for it (same package versions).

The Dependabot rule is unchanged in substance: it ignores minor and major
updates of the `python` image (a jump to 3.14 is a migration lot) and keeps the
3.13.x patch releases. The three CVEs above stay reported until a Python line
that fixes them is adopted; they are accepted until then.

## Amendment, 2026-11-08: Debian security fixes ahead of the base image

Debian published DSA-6613-1 for zlib (CVE-2026-76844 and CVE-2026-85091,
high, plus CVE-2026-27171, medium), fixed in `1:1.3.dfsg+really1.3.1-1+deb13u1`
in trixie-security. The pinned `python:3.13.18-slim-trixie` still had
`1:1.3.dfsg+really1.3.1-1+b1`, so the `Container image` check failed on every
pull request and on `main`, and no image could be published. No newer image of
the 3.13 line carried the fix: the tag had been rebuilt the same day, but its
amd64 and arm64 manifests were the same as those of the pinned digest (only
riscv64 had changed), and their Debian sources point to a snapshot of
2026-11-04, without the fix.

The final stage therefore upgrades the named packages, and only those, from
the base image's own trixie-security source (`apt-get install --only-upgrade`),
then checks that the installed version is at least the fixed one, so that a
mirror still serving the old package fails the build instead of shipping it.
The step lists each package with its CVEs and fixed version.

The compromise: the image no longer depends only on the base image digest and
the lock file. Two builds of the same commit can get different versions of a
listed package if Debian publishes a newer security update in between; the
version can only go up, and never below the fixed one. Pinning the exact
version was not chosen, for the reason the build stage already gives: a pinned
Debian version leaves the mirrors at its next security update and breaks every
build. Installing from a fixed `snapshot.debian.org` timestamp would be
reproducible but makes every build depend on that service, slower and rate
limited. A general `apt-get upgrade` was not chosen either: it would change
every package of the base image, not just the ones a finding names. The
blocking scan is unchanged (`--only-fixed --fail-on high`); nothing is ignored.

The step is temporary per package: once Dependabot moves the `FROM` lines to a
base image digest that carries the fix, the package leaves the list, and the
step goes away when the list is empty.

Dependabot could not have proposed this change. Its security updates follow
the GitHub Advisory Database, which does not cover the Debian packages of an
image, and no base image digest with the fix existed. When the Python image is
rebuilt with it, the weekly schedule and the 7-day cooldown of the `docker`
ecosystem delay the digest update by up to two weeks. The second amendment
below applies the setting that removes that delay.

## Amendment, 2026-11-08 (2): keeping the base image current

Question asked: is the pinned base image the latest one, and does it stay so
without intervention?

**Finding.** The Docker Hub index of `python:3.13.18-slim-trixie` was
republished on 2026-11-08 (digest `sha256:e48f59cd...`, the pin was
`sha256:09a17cad...`). Only the riscv64 manifest changed: the amd64 manifest
(`sha256:48d88481...`) and the arm64 manifest (`sha256:7e9b0661...`) are the same
in both, built on 2026-11-05, with `zlib1g` `1:1.3.dfsg+really1.3.1-1+b1`, the
vulnerable version (read from `/var/lib/dpkg/status` of the base layer). No
newer `3.13.x-slim-trixie` exists. The pin is therefore not stale for the
platforms the project ships, the base image cannot carry the zlib fix yet, and
the `apt` step of the previous amendment stays, with its version floor and its
guard test. The three fixable findings left on `main` after that step are not
zlib: they are the Python interpreter itself (CVE-2025-15367, CVE-2026-116566,
CVE-2026-103995, medium and low, fixed only in 3.15.0a6 and 3.16.0), accepted in
the section "Move to Python 3.13" above.

**Why no pull request proposed a newer base.** Dependabot checked the `docker`
ecosystem weekly (Monday) with a 7-day cooldown, so a rebuild was proposed one
to two weeks after its publication, and the first rebuild with the zlib fix may
be proposed that late. Nothing scanned the published image between pushes.

**Changes.**

- The `docker` ecosystem is checked daily, and `python` is excluded from the
  cooldown (`cooldown.exclude`): a rebuild of the base image is proposed the
  day it is published, as a tag and digest update. The cooldown stays for the
  other images of the Dockerfile (uv), for the `uv`, `github-actions` and
  `docker-compose` ecosystems, which have no distribution of Debian fixes to
  wait for. The pull request still has to pass every required check, so a base
  image that breaks the build or brings a fixable high vulnerability does not
  merge. A base image rebuilt by a compromised account would be proposed
  without delay; the digest pin, the required checks and the maintainer's review
  are the control, as for any pull request.
- `.github/workflows/image-scan.yml` scans the published image
  (`ghcr.io/housecairn/housecairn:edge`, linux/amd64) every day with the same
  Grype release and the same rule as the `Container image` check
  (`--only-fixed --fail-on high`). It fails visibly when a new fixable high or
  critical vulnerability appears in what is published, and refreshes the code
  scanning alerts of `main` (same category, `container-image`). It is not a
  required check, runs without repository access (`permissions: {}` for the scan;
  `security-events: write` only for the upload job) and publishes nothing. The
  Grype pin is duplicated from `image.yml` and updated by hand, together.

Nothing is ignored and the threshold of the blocking scan is unchanged.

## Amendment, 2026-11-09: a shorter image workflow on the merge queue

**Measurement.** Over the 20 latest successful runs of `image.yml` (10 merge
queue entries, 10 pull requests), the workflow took 155 to 221 s, the longest of
two parallel jobs. `Container image` took 129 to 191 s: the build 21 to 30 s, the
two smoke tests 16 to 23 s and 22 to 31 s, and the first Grype scan (inventory)
49 to 98 s, most of it the download of the vulnerability database, while the
second scan, on the same database, took 7 to 12 s. `Container image (multi-arch
build)` took 122 to 199 s, nearly all of it the build under QEMU (`linux/arm64`).

**Changes.**

- The Grype database downloads in the background from the start of the job
  (`.github/scripts/grype-db.sh`), while the image builds and is tested. The scan
  waits for it and fails, with the download log, if the download failed: it never
  runs on a missing database. Nothing is removed from the scan: the inventory,
  the SARIF for code scanning and the `--only-fixed --fail-on high` check are
  unchanged, and the image is still started on amd64 with SQLite and PostgreSQL.
- `linux/arm64` is built on pull requests and queue entries only when the
  `Image changes` job sees, in the diff of the change, the `Dockerfile`,
  `.dockerignore`, `uv.lock`, `pyproject.toml`, `image.yml` or the scripts it uses
  for the build (`retry.sh`, `arm64-needed.sh`). Otherwise `linux/amd64` alone is
  built, with provenance and SBOM. The filter is in a job, not in the `on:` of the
  workflow, so that the required check always reports a status. Anything unknown
  (no base commit, a diff that cannot be read, a failed detection job) builds
  both platforms. Every `push` (`main`, tags) publishes both, as before.
- What this gives up: a source change that breaks only on arm64, without touching
  the files above, is no longer found before `main`. It is found by `Publish
  image` on the merge commit, which builds both platforms. Dependencies, the
  usual cause of a platform-specific failure, are in `uv.lock` and keep the full
  build.
- Not done, on the measurement: a `type=gha` layer cache and a cache mount for uv.
  The amd64 build takes 21 to 30 s, `uv sync` 6 s of it, and every build starts on
  a new builder, so a cache mount would persist nothing; the layer cache would
  help only the arm64 build, which is now rare, and its layers after
  `COPY src/` change on almost every pull request. The smoke tests stay in
  sequence: both clean up a container the other starts (`hc-smoke-postgres`).

## Amendment, 2026-11-28: glibc, zstd and shadow fixes

**Finding.** The daily `Image scan` of `edge` failed from 2026-11-22, and with it
the `Container image` check of every pull request. Grype matched, with a fix
available:

- `libc6` and `libc-bin` `2.41-12+deb13u4`: CVE-2026-19499 and CVE-2026-5435
  (high, the cause of the failure) and twelve medium and low ones, fixed in
  `2.41-12+deb13u5` (in `trixie`, Debian 13.8);
- `libzstd1` `1.5.7+dfsg-1`: CVE-2026-124851, fixed in `1.5.7+dfsg-1+deb13u1`
  (DSA-6657-1, `trixie-security`);
- `login.defs` and `passwd` `1:4.17.4-2`: CVE-2026-135901, fixed in
  `1:4.17.4-2+deb13u1` (DSA-6661-1, `trixie-security`);
- the Python 3.13.18 interpreter: eight CVEs, below.

**Base image.** `python:3.13.18-slim-trixie` was rebuilt on 2026-11-24 (index
`sha256:168d3a5d...`). Unlike the rebuild of 2026-11-08, its amd64 and arm64
manifests are new, and their `/var/lib/dpkg/status` has `libc6` and `libc-bin`
`2.41-12+deb13u5` and `zlib1g` `1:1.3.dfsg+really1.3.1-1+deb13u1`, but still the
vulnerable `libzstd1`, `login.defs` and `passwd`. Both `FROM` lines now pin that
digest. No Dependabot pull request had proposed it by 2026-11-28.

**Changes.**

- The upgrade step of the final stage no longer upgrades `zlib1g`, which the
  base image now carries, and upgrades `libzstd1`, `login.defs` and `passwd`
  from `trixie-security` instead, each with its version floor.
- The step keeps a version floor for every package it guards, upgraded or not:
  `libc6`, `libc-bin` and `zlib1g` too. A floor costs nothing when the base image
  carries the fix, and fails the build, on every platform, if a later base image
  does not. The previous rule (a package leaves the step once the base image
  carries its fix) now applies to the upgrade only. The step prints the
  installed versions, so the build log of each platform shows them.
- `tests/test_image.py` lists the guarded packages with their floors and which
  of them are upgraded, and checks the Dockerfile against that list.

**Python.** No Python release newer than 3.13.18 exists in the 3.13 line
(python.org and the Docker Hub tags, 2026-11-28), and the project stays on 3.13
(section "Move to Python 3.13"). Grype matches the interpreter binary by its
CPE, against the CPython CNA records, which give one range per CVE: every
version below the first release of the newest line that has the fix (3.16.0,
3.16.0a2 or 3.15.0a6). They say nothing of the 3.13 line, so no 3.13 release can
leave those ranges. The facts on 2026-11-28, from the CPython pull requests:

- CVE-2026-103995, CVE-2026-115559, CVE-2026-115791, CVE-2026-116566,
  CVE-2026-16052, CVE-2026-124249 and CVE-2026-19552: the backport to 3.13 is an
  open, unmerged pull request; no 3.13 release has the fix yet.
- CVE-2025-15367 (POP3): fixed in 3.15.0a6; there is no backport to 3.13, only
  open ones to 3.12.

These eight remain reported, medium and below; they do not fail the check, and
nothing ignores them. A 3.13.x release that carries the backports will still be
matched by Grype until the CNA records list fixed 3.13 versions.
