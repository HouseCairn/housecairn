# Trial image of ADR 0087: the application on a distroless final stage.
#
# NOT the published image. The published image is built from the Dockerfile at
# the root of the repository by .github/workflows/image.yml, which this file does
# not change and which never builds it. This file exists so that the
# measurements of docs/adr/0087-distroless-final-image-study.md can be repeated:
#
#   docker build -f docs/adr/0087-trial/Dockerfile -t housecairn:distroless .
#   docs/adr/0087-trial/measure.sh
#
# Differences with the root Dockerfile, and why:
# - Final stage: gcr.io/distroless/python3-debian13 (no shell, no package
#   manager, no apt step). Its Python is Debian's python3.13, at
#   /usr/bin/python3.13, not the python.org build of the python image at
#   /usr/local/bin/python3.13.
# - Build stage: debian:trixie-slim with Debian's python3 package, so that the
#   virtual environment is built by, and points to, the same interpreter path
#   and build as the final stage (pyvenv.cfg: home = /usr/bin).
# - No RUN in the final stage (there is no shell to run it): the user entry and
#   /data are prepared in the build stage and copied.
# Base images are pinned by digest, as in the root Dockerfile. Dependabot does
# not update this file (its docker ecosystem reads the root directory only).

# The final base. BASE selects the variant: the default is the shell-free
# "nonroot" image; the "debug-nonroot" variant adds a busybox shell.
ARG BASE=gcr.io/distroless/python3-debian13:nonroot@sha256:6c4cb3bfa4b555429dd19541796a6ba6b30c84675b460bd0bd5072f7d5bdae71

FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv

FROM ${BASE} AS runtime

# ---------------------------------------------------------------------------
# Build stage: same steps as the root Dockerfile, on Debian's Python.
# ---------------------------------------------------------------------------
FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 AS build

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 gettext \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON=/usr/bin/python3.13 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --extra postgres --no-install-project

COPY LICENSE NOTICE manage.py ./
COPY design/ design/
COPY src/ src/

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

# What the root Dockerfile does with groupadd, useradd and install in its final
# stage: the final base's own passwd and group files plus the housecairn entry
# (10001:10001, same as the published image), and an empty /data owned by it.
COPY --from=runtime /etc/passwd /etc/group /rootfs/etc/
RUN echo 'housecairn:x:10001:10001:housecairn:/nonexistent:/sbin/nologin' >>/rootfs/etc/passwd \
    && echo 'housecairn:x:10001:' >>/rootfs/etc/group \
    && install -d -o 10001 -g 10001 -m 0700 /rootfs/volume/data

# ---------------------------------------------------------------------------
# Final stage: distroless, no RUN.
# ---------------------------------------------------------------------------
FROM runtime

COPY --from=build /rootfs/etc/passwd /rootfs/etc/group /etc/
COPY --from=build --chown=10001:10001 /rootfs/volume/ /
COPY --from=build /app /app

ENV PATH=/app/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/busybox \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=housecairn.settings \
    HOUSECAIRN_DATA_DIR=/data \
    STATIC_ROOT=/app/static

USER 10001:10001
WORKDIR /app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --start-interval=2s --retries=3 \
    CMD ["python", "-m", "housecairn.entrypoint", "healthcheck"]

ENTRYPOINT ["python", "-m", "housecairn.entrypoint"]
CMD ["run"]

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="HouseCairn (distroless trial, not published)" \
    org.opencontainers.image.source="https://github.com/HouseCairn/housecairn" \
    org.opencontainers.image.licenses="AGPL-3.0-only" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.revision="${REVISION}"
