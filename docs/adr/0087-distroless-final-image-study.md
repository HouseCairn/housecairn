# 0087. A distroless final image: study and measured trial

- Status: proposed. A study asked for by the maintainer before any decision; nothing in the
  published image, its `Dockerfile` or `.github/workflows/image.yml` changes with this record.
- Date: 2026-11-18
- Related: [0015](0015-container-image-and-publication.md) (container image, its base and its
  amendments of 2026-11-08), [0032](0032-dates-and-time-zone-of-tasks.md) (time zones),
  [0075](0075-impact-on-other-repositories.md) (the Home Assistant app is a layer over the image)

## Context

A public suggestion proposes to base the final stage of the image on a distroless base (no
shell, no package manager, nothing but Python), and to debug it either with a `-debug`
variant or with a container attached to the application's namespaces
(`--pid=container:… --network=container:…`).

ADR 0015 chose `python:3.13.<patch>-slim-trixie` and set a distroless base aside in one
sentence: the entry point needs an interpreter matching the virtual environment's, and the
slim image's Debian packages are scanned and patched by Debian. This record checks that
sentence against real images, on the criteria that matter to this project: the interpreter
and its path, the virtual environment built by uv, amd64 and arm64, rebuild cadence and
vulnerability tracking (including the zlib step of the 2026-11-08 amendment), conditions of
use, signatures, what breaks, and how to debug.

## Candidates

Read on 2026-11-18 from the registries and the projects' public documentation; the Python
version, paths, time zones and locale come from the trial run below.

| | `python:3.13.18-slim-trixie` (today) | `gcr.io/distroless/python3-debian13` | `cgr.dev/chainguard/python` (free tier) |
| --- | --- | --- | --- |
| Python | 3.13.18, python.org build, `/usr/local/bin/python3.13` | **3.13.5**, Debian's `python3.13` package `3.13.5-2+deb13u5`, `/usr/bin/python3.13` | **3.15.2**, Wolfi build, `/usr/bin/python3.15` |
| Base system | Debian 13, `apt`, `dpkg`, `bash`, `dash` | Debian 13 packages, no shell, no package manager; `:debug` adds busybox | Wolfi (`apk` packages), glibc, no shell; `latest-dev` adds shells, `apk`, `pip`, `uv` |
| Platforms | amd64, arm64 and 6 others | amd64, arm64, riscv64 | amd64, arm64 |
| Tags | `3.13.<patch>-slim-trixie`, pinned by digest | `latest`, `nonroot`, `debug`, `debug-nonroot`, plus one tag per commit of the project; pinned by digest | **`latest` and `latest-dev` only**; versioned tags are for paying customers |
| Rebuilds | rebuilt by the Docker official images on Debian and Python updates; the 2026-11-05 build still had the vulnerable zlib (ADR 0015) | images rebuilt on every commit of the project; Debian package snapshots proposed daily (one-day buffer) and merged by its maintainers: snapshot bumps merged 2026-10-31, 2026-11-08 | daily builds |
| Signature, provenance | no Sigstore signature checked by this study | Sigstore keyless signature, **verified** (`cosign verify`, identity `keyless@distroless.iam.gserviceaccount.com`); no signed SLSA or SBOM attestation found on the index digest | Sigstore keyless signature **verified**; signed SLSA v1 provenance and SPDX SBOM attestations **verified** (`cosign verify-attestation`) |
| Time zones | system `tzdata` | system `tzdata` | **none**: `ZoneInfo("Europe/Paris")` fails, and the lock file installs `tzdata` on Windows only |
| Default user | root (the image sets 10001) | `nonroot` (65532) in the `nonroot` tags | `nonroot` (65532) |

**Chainguard's free tier does not fit today.** Its only tags move from one Python minor
release to the next (3.15 on 2026-11-18), while `requires-python` is `>=3.13,<3.14` and a
minor upgrade is a migration lot (ADR 0015, "Move to Python 3.13"). A digest pin would hold
3.15 still, without patch updates; every Dependabot update could change the minor release.
It also has no time zone database. Its supply chain is the best documented of the three.
Measured as a bare base below for reference only.

**Docker Hardened Images** (`dhi.io/python`, Apache 2.0, a `3.13` tag on Debian 13 in the
catalog) were not measured: pulling them needs a Docker account login, and this study did not
put a credential in the CI. Their interpreter path and build were not verified.

**A python.org interpreter on `distroless/cc`** (copying `/usr/local` from the slim image) was
considered and not trialled: the interpreter links libraries that `cc` does not have
(`libsqlite3`, `libffi`, `libbz2`, `liblzma`, `libexpat`, `libuuid`), copied by hand they would
be invisible to Grype and to the SBOM, which read package databases.

That leaves `gcr.io/distroless/python3-debian13`, the base of the trial.

## The trial

`docs/adr/0087-trial/Dockerfile` builds the application on
`gcr.io/distroless/python3-debian13:nonroot` (pinned by digest), with the same build steps as
the root `Dockerfile`, except:

- the build stage is `debian:trixie-slim` with Debian's `python3` package, so that uv builds
  the virtual environment with the interpreter of the final stage, at its path:
  `pyvenv.cfg` reads `home = /usr/bin`, `version_info = 3.13.5`. The python.org build of the
  slim image would leave the virtual environment pointing to `/usr/local/bin`, absent from the
  distroless image;
- the final stage has no `RUN` (no shell to run it): the `housecairn` entry of `/etc/passwd`
  and `/etc/group` (10001:10001, as today) and `/data` (mode `0700`, owner 10001, checked in
  the run) are prepared in the build stage and copied; there is no `pip` to remove and no
  `apt` step;
- `BASE` selects the `debug-nonroot` variant (busybox) for the same build.

`docs/adr/0087-trial/measure.sh` builds the root `Dockerfile` and the trial, then measures
both, and the three bare bases, the same way. It ran once on GitHub Actions (`ubuntu-24.04`,
Docker 28.0.4, 4 CPUs), [run 44340132752](https://github.com/HouseCairn/housecairn/actions/runs/44340132752),
through a temporary workflow removed from the branch afterwards; its results are the run's
`distroless-trial` artifact (30 days). Grype 0.121.0 (the release pinned in `image.yml`) and
Syft 1.56.0 scanned every image with the same binary, the same database (built
2026-11-18T06:21Z) and the same options. linux/amd64 only.

### Results

| linux/amd64 | Current image | Distroless trial | Change |
| --- | ---: | ---: | --- |
| Size (`docker image inspect`) | 319,805,525 bytes | 256,705,639 bytes | −63.1 MB (−19.7 %) |
| Files | 23,156 | 20,719 | −2,437 |
| Packages (Syft, all) | 140 | 90 | −50 |
| … Debian packages | 87 | 38 | −49 |
| … Python packages | 52 | 52 | same |
| `sh`, `bash` | yes, yes | no, no | |
| `apt`, `dpkg` | yes, yes | no, no | |
| Python | 3.13.18 (python.org) | 3.13.5 (Debian, `+deb13u5`) | 13 patch releases behind, Debian's backports instead |
| Grype matches, all severities | 220 | 314 | +94 |
| Distinct vulnerabilities | 99 | 120 | +21 |
| … critical / high | 0 / 23 | 1 / 34 | |
| … medium / low / negligible / unknown | 40 / 9 / 27 / 0 | 49 / 10 / 20 / 6 | |
| Matches with a fix (`--only-fixed`) | 7 | 0 | |
| … of high or critical severity (the blocking rule) | 0 | 0 | |
| `zlib1g` | `1:1.3.dfsg+really1.3.1-1+deb13u1` (apt step) | `1:1.3.dfsg+really1.3.1-1+deb13u1` (in the base) | |
| First start on an empty volume to the first `200` of `/healthz` (5 runs, ms) | 13150, 10848, 10407, 10274, 10398 (median 10407) | 11577, 10731, 10812, 10660, 10903 (median 10812) | +0.4 s median |
| Restart on the same volume (5 runs, ms) | 5193, 5228, 5336, 5164, 5144 (median 5193) | 6279, 5914, 5877, 6013, 5912 (median 5914) | +0.7 s median |
| Build, one cold run (ms) | 22559 | 21792 | |
| `image-smoke.sh`, unchanged, SQLite | passed (21 checks) | **stopped** after 9 checks, exit 127: no `test`, no `find` in the image | |
| `image-smoke.sh`, unchanged, PostgreSQL | passed in the `Container image` job | **stopped** after 10 checks, same error | |
| `image-smoke.sh` on the `debug-nonroot` variant | | passed, SQLite (21) and PostgreSQL (22) | |

Bare bases, same run: `python:3.13.18-slim-trixie` 118,351,570 bytes, 95 packages (87 Debian),
101 distinct vulnerabilities, 9 matches with a fix, one of them high (zlib, CVE-2026-85091);
`distroless/python3-debian13:nonroot` 59,859,855 bytes, 38 packages, 120 distinct
vulnerabilities, none with a fix; `chainguard/python:latest` 71,299,557 bytes, 30 packages,
5 distinct vulnerabilities (all in Python 3.15), none with a fix.

The start-up times are polled every 100 ms from the host and include the migrations on an
empty volume; differences under a second on a shared runner are within what one run can show.

### Reading the vulnerability counts

The distroless image has more known vulnerabilities, not fewer, and the reason is the
interpreter. Debian ships Python 3.13.5 with selected security backports; Grype matches it
against the Debian security tracker, which lists 26 vulnerabilities for `python3.13`: 21
marked "won't fix" by Debian and 5 "not fixed", among them the only critical one
(CVE-2026-19445, "won't fix"). Each is counted four times, once per Debian package of the
interpreter (`python3.13-minimal`, `libpython3.13-minimal`, `libpython3.13-stdlib`,
`python3.13-venv`). The distroless base also carries the Kerberos libraries
(`libkrb5-3`, `libgssapi-krb5-2`, `libk5crypto3`, `libkrb5support0`, 23 matches each), which
the slim image does not. What it drops (`apt`, `dpkg`, `coreutils`, `util-linux` and its
libraries, PAM, `login`, `perl-base`, `tar`, the `openssl` command line and its legacy provider,
the `systemd` libraries) accounted for 111 of the 220 matches of the current image.

"0 with a fix" is therefore not a cleaner image: the blocking rule of ADR 0015
(`--only-fixed --fail-on high`) would pass, because Debian publishes no fix to point to, while
the same interpreter flaws are fixed upstream in 3.13 patch releases that this base does not
ship. Today the project follows those patch releases through the python image and sees the
remaining interpreter findings, fixed only in 3.15 and 3.16, as "fixable" (accepted in ADR
0015).

## What changes, what breaks

- **Entry point and health check**: unchanged. `ENTRYPOINT`, `CMD` and `HEALTHCHECK` are
  already in exec form and run Python, never a shell; both work in the trial (the container
  became healthy, stopped on SIGTERM and restarted in the smoke test's debug run).
- **Virtual environment**: the build stage must use Debian's interpreter at `/usr/bin` (done in
  the trial). The Python patch version is then Debian's, not chosen by the project;
  Dependabot's rule "3.13.x patch releases of the `python` image" has nothing to act on.
- **`compilemessages`, design tokens, static files**: build stage only, unchanged; the French
  sign-in page check passed on the debug variant.
- **Locale, certificates, time zones**: identical to today in the trial: `LANG=C.UTF-8`,
  `/etc/ssl/certs/ca-certificates.crt` (and `SSL_CERT_FILE` set by the base), system
  `tzdata` with `Europe/Paris`; `fr_FR.UTF-8` is unavailable in both, as today (the
  application formats with Django, not the C library locale).
- **User and `/data`**: no `useradd` or `install` in the final stage; the passwd entry and
  `/data` are copied from the build stage (shown working above).
- **zlib step and the ADR 0015 amendment of 2026-11-08**: the distroless base already carries
  `+deb13u1`, so the step would go away. It also could not exist: without `apt` the project
  cannot apply a Debian fix ahead of the base. Copying fixed files in from a build stage would
  hide them from the package database, hence from Grype and the SBOM. The project would wait
  for the distroless maintainers to merge their next snapshot bump (one-day buffer, then a
  review) and for Dependabot to propose the digest. Whether Dependabot updates a `gcr.io`
  digest pin was not verified.
- **Smoke test**: `image-smoke.sh` runs `test`, `find`, `id`, `sh -c touch`, `stat` and
  `sha256sum` inside the container. On the distroless image, the two `test -e` probes of files
  that must be absent fail with "executable file not found", which the script reads as "absent"
  (a pass for the wrong reason); it then stops at `find`, exit 127, after the 9 checks that run
  from the host (health, pages, French catalog, version, static files, Swagger UI). Each
  in-container command has a Python equivalent (the image has Python), so a distroless image
  would need these checks rewritten with `python -c`. On the debug variant, the script passes
  unchanged.
- **Administration commands**: every command of the documentation goes through
  `python -m housecairn.entrypoint` (`manage`, `backup_instance`, `restore_instance`,
  `createsuperuser`) or a separate `debian:stable-slim` container; they keep working
  (`docker exec … python -m housecairn.entrypoint manage check` answered in the trial, and
  `docker cp` works without a shell). Coolify's **Terminal**, which
  `docs/administration/coolify.md` uses for the same commands, presumably needs a shell in the
  container; not tried.
- **Home Assistant app**: a layer over this image (ADR 0075); whether it needs a shell in the
  image was not checked in this repository.

## Debugging without a shell

Measured on the running trial container (results in the run):

- `docker exec <container> sh` fails (`executable file not found`).
- **An attached container works without `--privileged` and without any capability**, if it
  runs as the application's user:

  ```sh
  docker run --rm -it --pid=container:housecairn --network=container:housecairn \
    --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges --read-only \
    busybox:1.37.0@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec sh
  ```

  From it, `ps` lists the entry point, gunicorn and the task worker; `wget` reaches
  `http://127.0.0.1:8000/healthz`; the application's files are readable under
  `/proc/1/root/app`. The same container as root without capabilities is refused
  `/proc/1/root` ("Permission denied"): reading another user's process root needs
  `CAP_SYS_PTRACE`, and the same user does not.
- **A `-debug` variant**: the `debug-nonroot` base adds busybox (with `sh` and a `dpkg`
  applet) for 1,177,496 bytes and 3 more distinct vulnerabilities. Publishing it would mean a
  second image per release with its own tags, provenance, SBOM attestations, scan and smoke
  test in `image.yml`, for every user to choose from.

## Recommendation (not decided)

**Keep `python:3.13-slim-trixie` for now.** The trial shows the distroless image works, is
20 % smaller and has no shell, `apt` or `dpkg`, and that it can be debugged without
`--privileged`. Against that:

1. Its interpreter is Debian's 3.13.5, thirteen patch releases behind, with 21 more distinct
   known vulnerabilities and one critical that Debian will not fix; the blocking scan would stop
   seeing interpreter flaws as fixable.
2. The project loses the zlib-style remedy of ADR 0015: a Debian fix waits for another
   project's review.
3. The gain against an attacker is smaller than "no shell" suggests: whoever runs code in this
   container runs it in a Python interpreter, which does what a shell would. The container
   already runs without root, capabilities or privilege escalation, on a read-only file system,
   where `apt` cannot run anyway.
4. The smoke test would need its in-container checks rewritten, and Coolify's Terminal
   would probably stop working.

**If the maintainer wants a shell-free image anyway**, the trial's path is the one to take:
`distroless/python3-debian13:nonroot` by digest, a Debian build stage, the smoke test's
in-container checks rewritten in Python, no published `-debug` variant, and the attached
container above documented in `docs/administration/hardening.md`. What would change the
recommendation: a distroless or hardened base that ships the upstream 3.13 patch releases with
a scanner-visible package database, or a free, versioned `3.13` tag of a hardened image (Docker
Hardened Images, to be measured with a Docker account in the CI).

## Consequences

- None for the published image: this record is a proposal.
- `docs/adr/0087-trial/` stays as the way to repeat the measurements
  (`GRYPE=… SYFT=… docs/adr/0087-trial/measure.sh` on a machine with Docker). No workflow runs
  it, and Dependabot does not update its digests (its `docker` ecosystem reads the root
  directory only): rerunning it later means refreshing the pins first.
- Not measured: linux/arm64 (the trial built amd64 only; the bases publish both); Docker
  Hardened Images; repeatability of the start-up times across runs; Coolify's Terminal; the
  Home Assistant app on a distroless image.
