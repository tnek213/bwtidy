# syntax=docker/dockerfile:1@sha256:572cebbc2b15e6f84c077742952674889cb63cd5e9b80abdffcbe587b6f19183

## every image by tag and digest, so a rebuild starts from the same base (scripts/update-deps
## refreshes the digests, Dependabot proposes new ones)
FROM ghcr.io/astral-sh/uv:0.13@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f AS uv

FROM node:24-trixie-slim@sha256:4d00acb4bcc42c454c4eb801526f7ad7f772ea3bd4fd168c5cc2e384abcbb9d4 AS node

FROM node AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

## the open source build of the Bitwarden CLI, built from Bitwarden's source at the commit
## docker/bitwarden-cli/source.json pins. Bitwarden's npm package is their commercial build, which
## bundles code under the Bitwarden License. git and the monorepo's tree stay in this stage.
FROM node AS bitwarden-cli-source
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
COPY docker/bitwarden-cli/source.json /src/source.json
WORKDIR /src/clients
## a tag can move, so the build fetches the pinned commit and fails while the tag names another one
RUN repository="$(node -p 'require("/src/source.json").repository')" \
    && tag="$(node -p 'require("/src/source.json").tag')" \
    && commit="$(node -p 'require("/src/source.json").commit')" \
    && tagged="$(git ls-remote "$repository" "refs/tags/$tag" "refs/tags/$tag^{}" \
        | awk -v ref="refs/tags/$tag" '$2 == ref { plain = $1 } $2 == ref "^{}" { peeled = $1 } END { print peeled ? peeled : plain }')" \
    && if [ "$tagged" != "$commit" ]; then \
        echo "The tag $tag of $repository names the commit '$tagged', source.json pins $commit" >&2; \
        exit 1; \
    fi \
    && git init --quiet \
    && git fetch --quiet --depth 1 "$repository" "$commit" \
    && git checkout --quiet FETCH_HEAD \
    && rm -rf .git
## no install scripts: they build native modules of the desktop app and of tools such as nx and
## set up git hooks, the CLI's webpack build needs none of them
RUN npm ci --ignore-scripts --no-fund --no-audit
RUN cd apps/cli && npm run build:oss:prod
## a module under bitwarden_license in the bundle stops the build. The pattern names the source
## folders, as the bundled package.json names bitwarden_license/bit-cli/webpack.config.js in its
## build:bit scripts, a plain string and no code.
RUN if grep -o 'bitwarden_license/bit-c[a-z]*/src/[^"]*' apps/cli/build/*.js; then \
        echo "apps/cli/build includes code under the Bitwarden License (the modules above)" >&2; \
        exit 1; \
    fi
## where the CLI's corresponding source is, for the image to say so beside the build
RUN node -e 'const s = require("/src/source.json"); console.log(`The build of the Bitwarden CLI is its open source build (GPL-3.0, LICENSE_GPL.txt), built from ${s.repository} at the tag ${s.tag}, commit ${s.commit}.`)' > /src/SOURCE

## the Bitwarden CLI and every package it brings along, as its own lock file pins them, its build
## replaced by the open source one. npm stays in this stage: the app stage takes node and the
## installed tree only.
FROM node AS bitwarden-cli
WORKDIR /usr/local/lib/bitwarden-cli
COPY docker/bitwarden-cli/package.json docker/bitwarden-cli/package-lock.json ./
RUN npm ci --no-fund --no-audit
## the npm package's build is the one with code under the Bitwarden License, and its package.json
## names a LICENSE.txt the package does not hold
RUN rm -rf node_modules/@bitwarden/cli/build
COPY --from=bitwarden-cli-source /src/clients/apps/cli/build node_modules/@bitwarden/cli/build
COPY --from=bitwarden-cli-source /src/SOURCE /src/clients/LICENSE_GPL.txt node_modules/@bitwarden/cli/

FROM python:3.13-slim-trixie@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50 AS python

## the documentation site the gateway serves at /docs/, built strictly with the Zensical that
## backend/uv.lock pins (the backend's group docs, without bwtidy and its dependencies)
FROM python AS docs
ENV UV_PYTHON=/usr/local/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
## resvg draws the text of a diagram with the system's fonts and the base image has none. This
## stage only, the app stage stays without them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
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
## the Kroki server that renders the diagrams of the docs, https://kroki.io when empty
ARG BWTIDY_KROKI_URL=""
## the repo's star count for the header comes in as an arg too, as the docs ask GitHub for nothing
## (the image workflow looks it up, scripts/docs-site leaves it out when empty)
ARG BWTIDY_STARS=""
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

## pip comes with the base image and nothing here runs it (the venv is built by uv in the backend stage)
RUN rm -rf /usr/local/lib/python3.13/site-packages/pip /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
    /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13

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
