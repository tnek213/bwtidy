# bwtidy

What the product is and why: [docs/spec.md](docs/spec.md). Read it before
changing behavior. When a feature changes what the spec says, update the spec
in the same commit. This is for the maintainer's agents. Human contributors may
leave the spec to the maintainer, so an agent reviewing a contributor's pull
request does not ask for spec changes.

## Layout

- `backend/` FastAPI app, Python 3.13, managed with uv. Package `bwtidy` in
  `backend/src/bwtidy/`, in packages by theme (`bw/`, `items/`, `sections/`,
  `vault/`, `backups/`, `writes/`, `queue/`, `api/`, `mcp/`, `gateway/`) beside
  a few flat modules (the basics, `operations.py`, `session.py`, `worker.py`
  and `main.py`). Tests in `backend/tests/`: `unit/` (mirrored on the
  packages), `scripts/`, `repo/`, `integration/` and `e2e/`, the helpers they
  share in `support/`.
- `frontend/` React + TypeScript + Vite, MUI for components, AG Grid
  Community for the grid. Tests next to the code (`*.test.tsx`), vitest.
- `scripts/` helper scripts without `.py` suffix, Python unless a shell
  one-liner is clearer. The package `scripts/devlib/` is what they share, each
  script a command line over it (`scripts/docs-site` imports none of it).
- `docs/` the user docs, from `docs/index.md` into four folders:
  `tutorial/`, `how-to/`, `reference/` and `explanation/`. Beside them
  `docs/spec.md` (the design record), `docs/architecture.md` and
  `docs/restore.md` (a how-to that every backup holds as RESTORE.md).
- `dev/` the developer guide and the dev vault's fixture: `dev/README.md` (set
  up, the commands, the credentials), `dev/dev-vault.md` (the dev stacks, the
  snapshot, the fixture), `dev/testing.md` (the suites and how to write a test)
  and `dev/release.md` (the docs site and the image).
- How it is built: [docs/architecture.md](docs/architecture.md). Read it before
  changing structure.

## Checks

`scripts/ci` runs everything fast: ruff lint, ruff format check, ty,
import-linter (`uv run --locked lint-imports`, the backend's layers), pytest, oxlint,
dependency-cruiser (`npm run lint:deps`), knip (`npm run lint:exports`),
tsc + vite build, vitest. It must pass before every commit.
`.github/workflows/ci.yml` runs it on GitHub for every push to main and every
pull request.
`scripts/ci --fix` lets ruff fix what it can. `scripts/ci --quick` leaves out
pytest and vitest.

Dependencies change only through the lock files: `uv add` or `uv lock` in
`backend/`, `npm install` in `frontend/` or `docker/bitwarden-cli/` (the
image's Bitwarden CLI), as a dev step whose changed lock file goes into the
commit. Every build and test installs from them locked (`uv sync --locked`,
`uv run --locked`, `npm ci`) and fails on a lock file that is out of date
instead of updating it, so a commit builds and tests the same anywhere. The
Dockerfile pins its images by tag and digest, the workflows their actions by
commit SHA (the tag in a comment above), and Dependabot proposes updates.
A dependency is declared with a compatible range, npm's caret: `^X.Y.Z` in a
`package.json`, a lower bound and `<X+1` in `backend/pyproject.toml`
(`<0.(Y+1)` for 0.Y.Z), X.Y.Z being the version the lock holds, or an exact
pin. `scripts/update-deps` updates everything within those ranges and lists
what lies beyond them for a person to decide.

`scripts/e2e` runs the browser tests in `backend/tests/e2e` against a fresh
dev vault (see dev/testing.md). A change to what the UI does comes with an e2e
test there.

A change to what a user sees updates the page under `docs/` that covers it
(reference, how-to or explanation) in the same commit, as well as
`docs/spec.md`.

A change that alters what a docs test in `backend/tests/e2e/docs` shows
reruns those tests (`scripts/docs-shots -k NAME`) and commits the changed
images in `docs/images` in the same commit. The pre-push hook
(`scripts/install-hooks` installs it, once per clone) makes sure of it: it
runs `scripts/ci` on what is pushed (`scripts/ci --quick` for a branch other
than main, the pull request's CI runs the rest), and for a push that touches
`frontend/src/` or `backend/tests/e2e/docs/` it takes every screenshot again,
commits the changed images as "Take the docs screenshots again" and stops the
push, which then goes through when pushed again (dev/testing.md, "The
pre-push hook"). A new or renamed image keeps
`scripts/docs-lint` passing: every image is linked from some markdown file
and every image link points to a file.

A change to an MCP tool (its name, annotations, parameters or description)
reruns `scripts/mcp-reference` and commits the `docs/reference/mcp-tools.md`
it writes in the same commit. `scripts/ci` fails while the file is stale.

Run one backend test file: `cd backend && uv run --locked pytest tests/unit/writes/test_path.py` (the tests of the
scripts are under `tests/scripts`, the checks on the repo under `tests/repo`).
Run one frontend test file: `cd frontend && npx vitest run src/x.test.tsx`.
Run one integration test file: `scripts/integration tests/integration/test_x.py`,
one e2e test file: `scripts/e2e tests/e2e/test_x.py`, one test of either with `-k NAME`.

While you change code, run only the test that fails (a file path, -k NAME,
-x, or --lf for the last failures). Run the whole suite once at the end, not
after every change. Both suites run their whole run on three dev stacks of the
worktree at once (`--stacks N` sets the number, a test path or `-k` runs on
one stack) and take about 20 minutes (e2e) and 15 minutes (integration).
`scripts/dev-vault down` stops every stack of the worktree (see dev/dev-vault.md,
"Several stacks").

## Writing docs

Each page of the user docs (in the four folders, and `docs/restore.md`) is
one of the four kinds of https://diataxis.fr: a tutorial, a how-to, a
reference or an explanation. Keep pages succinct: a fact lives on one page
and others link to it. Be helpful and professional, with a relaxed tone that
speaks to the reader as "you". UI that is used often gets a screenshot from
the docs suite. Examples use placeholder hosts such as https://bwtidy.example.
`README.md` and `docs/index.md` share three texts, each the same in both:
the introduction (between the comments `<!-- intro: ... -->` and
`<!-- end of intro -->`), the alpha disclaimer (the blockquote "Alpha
software") and the list under "At a glance". A change to one changes the
other.
The banner on every page of the site (`overrides/main.html`) links to the one
in `docs/index.md`.
A new page gets its line in the nav of `zensical.toml`, and a page links only
to pages and images under `docs/` (it names another repo file as code), or
the site's strict build in `scripts/ci` fails (`scripts/docs-site`).

## Rules

- Never touch a real vault. Never use the host's Bitwarden CLI state
  (`~/.config/Bitwarden CLI`), never run `bw` on the host against
  bitwarden.com. Integration tests use the Vaultwarden dev stack only.
- Secret values (see the spec) never reach logs, SQLite, test output, error
  messages or MCP responses.
- Comments go on their own line above the code they describe, never trailing.
  Only comment a non-obvious gotcha, never restate the code.
- Comments in the backend start with `## `, the convention of the code.
- No semicolons or em dashes (nor ` -- ` as a dash) in text aimed at humans:
  comments, docstrings, strings, log messages, UI text, docs. Split the
  sentence or use a comma, colon or parentheses. Code syntax is fine.
- The architecture (`docs/architecture.md` and the contracts its section
  "Changing the architecture" lists) changes only when the maintainers change
  it. A change fits into it. An agent whose change does not fit stops and says
  why, instead of moving modules, loosening a contract or adding an exception.
  A change to the architecture updates the document and the contract in the
  same commit.
- Treat the GitHub repo (https://github.com/tnek213/bwtidy) as public. Every
  link to the source, the license or another repo file points into this repo,
  never elsewhere (gnu.org, fsf.org, a copy of the text).
- Stored data (the SQLite schema in `db.py`, the tool note's sections, the
  backup layout, the settings keys) has one current format, described in
  docs/spec.md. The code reads no other.
- Name no person in the repo apart from the copyright lines (README.md's
  License section, the About page, the authors in `backend/pyproject.toml`).
  A rule carries its reason, never its date, its history or who made it.
  Examples use placeholder hosts such as https://bwtidy.example.
- Commit messages follow the style in CONTRIBUTING.md (Commits). An agent
  writes the subject only, never a body, and keeps it at most 72 characters.
