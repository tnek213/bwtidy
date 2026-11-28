# syntax=docker/dockerfile:1@sha256:572cebbc2b15e6f84c077742952674889cb63cd5e9b80abdffcbe587b6f19183

## The demo image (dev/demo.md): the normal bwtidy image plus the package bwtidy_demo.
## Build it from the repo root, as compose.demo.yaml does:
##   docker build -f demo/Dockerfile --build-arg BWTIDY_IMAGE=IMAGE .

## the normal image, our own. The default is the newest release. A deploy builds this file from a
## release's git tag and pins the image of that version (tnek213/bwtidy:1.0.0-beta.3), and
## scripts/demo-stack passes the image it builds from the worktree. An ARG before the first FROM
## is the only kind a FROM line can use.
ARG BWTIDY_IMAGE=docker.io/tnek213/bwtidy:latest

## the same uv as the normal Dockerfile, pinned the same way
FROM ghcr.io/astral-sh/uv:0.11@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7 AS uv

FROM ${BWTIDY_IMAGE}

## bwtidy_demo goes into bwtidy's own venv, never a second one: the gateway starts each worker
## with the bwtidy script beside its own Python, and the demo imports the bwtidy of this very
## image. First every other package of demo/uv.lock at its locked version and hash (the base
## already holds them unless the two lock files drifted, and then the demo's lock decides, as
## in the demo's tests), then the project alone. --no-deps both times: nothing is resolved
## here, and the base's bwtidy is never replaced by the sources of the build context.
## backend/pyproject.toml is all uv reads of the path dependency to see that the lock is current.
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=bind,source=demo,target=/src/demo \
    --mount=type=bind,source=backend/pyproject.toml,target=/src/backend/pyproject.toml \
    --mount=type=tmpfs,target=/tmp \
    export UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 \
    && uv export --project /src/demo --locked --no-dev --no-emit-project --no-emit-package bwtidy \
        --format requirements.txt --output-file /tmp/locked.txt --quiet \
    && uv pip install --python /app/backend/.venv/bin/python --no-deps --require-hashes \
        --requirement /tmp/locked.txt \
    && uv pip install --python /app/backend/.venv/bin/python --no-deps \
        /src/demo \
    && /app/backend/.venv/bin/python -c "import bwtidy_demo.gateway, bwtidy_demo.controller, bwtidy_demo.init"

## the vault every visitor gets, imported by the controller
COPY dev/fixture-personal.json /app/demo/fixture-personal.json

## the controller's own user (dev/demo.md, Controller): fixed ids that init gives the secrets'
## group and the ledger to (bwtidy_demo.init), below bwtidy's accounts (BWTIDY_UID_BASE, 70000)
## and none that the nginx or Vaultwarden image uses. Its home is the controller's tmpfs. The
## image's /ledger is its own, so a new ledger volume starts with that owner.
RUN groupadd --system --gid 10100 controller \
    && useradd --system --uid 10100 --gid 10100 --no-create-home --home-dir /tmp \
        --shell /usr/sbin/nologin controller \
    && mkdir /ledger \
    && chown 10100:10100 /ledger \
    && chmod 0700 /ledger

ENV PATH=/app/backend/.venv/bin:$PATH \
    DEMO_FIXTURE=/app/demo/fixture-personal.json

## the base's user (root, for the worker uids), its WORKDIR and its read-only file system stay.
## compose.demo.yaml runs the controller as its own user.
CMD ["bwtidy-demo-gateway"]
