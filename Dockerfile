# Container image of HouseCairn (docs/adr/0015, docs/getting-started/install.md).
#
# One image for every instance: SQLite by default, PostgreSQL when DATABASE_URL
# says so. Everything written at run time goes to the /data volume; the rest of
# the file system can be mounted read-only. Base images are pinned by digest,
# with their tag in the same line, and updated by Dependabot (tag and digest
# together).

# uv, to install the locked dependencies (ADR 0001). Same version as the CI.
FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv

# ---------------------------------------------------------------------------
# Build stage: dependencies, design tokens, translations, static files.
# ---------------------------------------------------------------------------
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS build

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# GNU gettext compiles the translation catalogs. Build stage only: it is not in
# the final image. Debian package versions are not pinned: the base image is
# pinned by digest, and a pinned Debian version disappears from the mirrors at
# its next security update, which would break every build.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

# The virtual environment uses the Python of the base image (same path in the
# final stage); uv never downloads another one. Bytecode is compiled now, since
# the final image's file system is read-only.
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON=/usr/local/bin/python3.13 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

WORKDIR /app

# Dependencies first, in their own layer: they change less often than the code.
# The postgres extra is included so the same image serves both databases. The
# project itself is not installed as a package: its source is on PYTHONPATH,
# which avoids downloading an unlocked build backend.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --extra postgres --no-install-project

COPY LICENSE NOTICE manage.py ./
COPY design/ design/
COPY src/ src/

# Design tokens and translations are generated from their sources, then the
# static files are collected with hashed names and compressed copies. The key
# below only lets Django load its settings during the build; it is not kept
# and never used at run time.
#
# What the application never serves is left out: Redoc (the API reference is
# Swagger UI, /api/docs/; no route serves Redoc) and the assets of Django REST
# framework's browsable API (the API renders JSON only). Swagger UI stays. Once
# the static files are collected, the copies of these two in the virtual
# environment are not read either, and no translation catalog source (`.po`)
# is: Django reads the compiled `.mo` files, the French one of the application
# included (the catalog of `core`, which holds the shared strings).
RUN DEBUG=true .venv/bin/python manage.py build_design_tokens \
    && DEBUG=true .venv/bin/python manage.py compilemessages --ignore=.venv \
    && DEBUG=false SECRET_KEY=build-time-only-not-a-secret HOUSECAIRN_DATA_DIR=/tmp/build-data \
        STATIC_ROOT=/app/static .venv/bin/python manage.py collectstatic --noinput \
        --ignore 'drf_spectacular_sidecar/redoc/*' --ignore rest_framework \
    && .venv/bin/python -m compileall -q src \
    && site=$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))') \
    && rm -rf "$site/drf_spectacular_sidecar/static/drf_spectacular_sidecar/redoc" \
        "$site/rest_framework/static" \
    && find /app -type f -name '*.po' -delete \
    && test -f src/housecairn/core/locale/fr/LC_MESSAGES/django.mo \
    && rm -rf design manage.py /tmp/build-data

# ---------------------------------------------------------------------------
# Final stage.
# ---------------------------------------------------------------------------
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# Debian security fixes that the base image does not carry yet (docs/adr/0015,
# amendment of 2026-11-08). The Python image is rebuilt some days after a
# Debian security advisory; until then, a fixable high vulnerability of the
# base image would block every pull request. Only the packages named here are
# upgraded, from the base image's own trixie-security source, and the build
# fails if the installed version is still below the fixed one. Remove a
# package from this step once the pinned base image carries its fix.
# - zlib1g: CVE-2026-76844, CVE-2026-85091 (DSA-6613-1), fixed in
#   1:1.3.dfsg+really1.3.1-1+deb13u1.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade zlib1g \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' zlib1g)" \
        ge '1:1.3.dfsg+really1.3.1-1+deb13u1' \
    && rm -rf /var/lib/apt/lists/*

# The base image's pip is not used at run time (dependencies are in
# /app/.venv): removed, with its advisories. Then a fixed, unprivileged
# identity, also the owner of /data. A host directory mounted on /data must
# belong to 10001:10001.
RUN python -m pip uninstall --yes --no-cache-dir pip \
    && groupadd --system --gid 10001 housecairn \
    && useradd --system --uid 10001 --gid housecairn --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin housecairn \
    && install -d -o 10001 -g 10001 -m 0700 /data

# Application, dependencies and static files, owned by root: read-only for the
# application user.
COPY --from=build /app /app

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=housecairn.settings \
    HOUSECAIRN_DATA_DIR=/data \
    STATIC_ROOT=/app/static

USER 10001:10001
WORKDIR /app
EXPOSE 8000

# /healthz answers without authentication and says nothing but "ok"
# (docs/adr/0012). Checked every 2 s while the container starts, then every 30 s.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --start-interval=2s --retries=3 \
    CMD ["python", "-m", "housecairn.entrypoint", "healthcheck"]

ENTRYPOINT ["python", "-m", "housecairn.entrypoint"]
CMD ["run"]

# Last, so that a new commit only changes this layer's metadata.
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="HouseCairn" \
    org.opencontainers.image.description="Self-hosted planner for home maintenance and renovation projects." \
    org.opencontainers.image.source="https://github.com/HouseCairn/housecairn" \
    org.opencontainers.image.url="https://github.com/HouseCairn/housecairn" \
    org.opencontainers.image.documentation="https://docs.housecairn.app/getting-started/install/" \
    org.opencontainers.image.licenses="AGPL-3.0-only" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.revision="${REVISION}"
