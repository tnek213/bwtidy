# syntax=docker/dockerfile:1@sha256:572cebbc2b15e6f84c077742952674889cb63cd5e9b80abdffcbe587b6f19183

## every image by tag and digest, so a rebuild starts from the same base (scripts/update-deps
## refreshes the digests, Dependabot proposes new ones)
FROM ghcr.io/astral-sh/uv:0.11@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7 AS uv

FROM node:24-trixie-slim@sha256:4d00acb4bcc42c454c4eb801526f7ad7f772ea3bd4fd168c5cc2e384abcbb9d4 AS node

FROM node AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

## the Bitwarden CLI and every package it brings along, as its own lock file pins them. npm stays
## in this stage: the app stage takes node and the installed tree only.
FROM node AS bitwarden-cli
WORKDIR /usr/local/lib/bitwarden-cli
COPY docker/bitwarden-cli/package.json docker/bitwarden-cli/package-lock.json ./
RUN npm ci --no-fund --no-audit

FROM python:3.13-slim-trixie@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50 AS python

## the documentation site the gateway serves at /docs/, built strictly with the Zensical that
## backend/uv.lock pins (the backend's group docs, without bwtidy and its dependencies)
FROM python AS docs
ENV UV_PYTHON=/usr/local/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
WORKDIR /src
COPY backend/pyproject.toml backend/uv.lock backend/
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --project backend --locked --only-group docs
COPY scripts/docs-site scripts/
## the version the restore page names, as in every backup's RESTORE.md
COPY backend/src/bwtidy/version.py backend/src/bwtidy/
COPY zensical.toml ./
COPY overrides/ overrides/
COPY docs/ docs/
## the build has no .git, so the commit comes in as an arg (scripts/docs-site reads it)
ARG BWTIDY_COMMIT=""
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    scripts/docs-site build /site

## the backend's venv, built at the path the app stage runs it from (its scripts name that path),
## so uv's cache and the sources stay out of the image
FROM python AS backend
## use the image's Python, never a uv managed download
ENV UV_PYTHON=/usr/local/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
## dependencies first, so a source change does not reinstall them
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-dev --no-install-project
COPY backend/src ./src
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-dev --no-editable

FROM python AS app

COPY --from=bitwarden-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=bitwarden-cli /usr/local/lib/bitwarden-cli/node_modules /usr/local/lib/bitwarden-cli/node_modules

## bw exits once it has answered instead of 2 s later (see the file). exec keeps the pid, so a
## SIGTERM to bw reaches node. The workers run bw as the account's uid, hence the file modes.
COPY docker/unref-share-timers.js /usr/local/lib/bwtidy/
RUN chmod 644 /usr/local/lib/bwtidy/unref-share-timers.js \
    && printf '#!/bin/sh\nexec /usr/local/bin/node --require %s %s "$@"\n' \
        /usr/local/lib/bwtidy/unref-share-timers.js \
        /usr/local/lib/bitwarden-cli/node_modules/@bitwarden/cli/build/bw.js > /usr/local/bin/bw \
    && chmod 755 /usr/local/bin/bw \
    && bw --version \
    && rm -rf /root/.config

## the gateway makes one directory per account in here, owned by the account's uid
RUN mkdir -p /data/accounts

COPY --from=backend /app/backend/.venv /app/backend/.venv
COPY --from=frontend /src/frontend/dist /app/static
## every backup holds it as RESTORE.md (collect.RESTORE_DOC)
COPY docs/restore.md /app/docs/restore.md
COPY --from=docs /site /app/site

ENV BWTIDY_HOST=0.0.0.0 \
    BWTIDY_PORT=8000 \
    BWTIDY_ACCOUNTS_DIR=/data/accounts \
    BWTIDY_STATIC_DIR=/app/static \
    BWTIDY_DOCS_DIR=/app/site

## last, so a new commit only rebuilds this layer. The build has no .git, so the hash comes in as an arg.
## Without it /api/health answers the package version alone.
ARG BWTIDY_COMMIT=""
ENV BWTIDY_COMMIT="$BWTIDY_COMMIT"

## the gateway runs as root so it can start each account's worker as that account's uid
USER 0:0
WORKDIR /data/accounts
EXPOSE 8000
CMD ["/app/backend/.venv/bin/bwtidy-gateway"]

## integration tests start the app themselves, so this target must not start a second bw serve.
## They run the single-user backend as uid 1000, scripts/e2e and scripts/dev-app start the
## gateway as root (scripts/dev-vault exec --root).
FROM app AS dev
COPY --from=uv /uv /uvx /usr/local/bin/
## use the image's Python, never a uv managed download
ENV UV_PYTHON=/usr/local/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --home-dir /home/app --shell /bin/sh app \
    && mkdir -p /data/app /data/bw /home/app \
    && chown 1000:1000 /data/app /data/bw /home/app
## keeps uv in the container away from a backend/.venv mounted from the host
ENV UV_PROJECT_ENVIRONMENT=/home/app/venv \
    BWTIDY_DATA_DIR=/data/app \
    BITWARDENCLI_APPDATA_DIR=/data/bw \
    HOME=/home/app
USER 1000:1000
WORKDIR /home/app
CMD ["sleep", "infinity"]
