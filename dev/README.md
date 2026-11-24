# Developer guide

How to set up a checkout, which script does what, and the credentials of the
dev vault. Every worktree runs its own Vaultwarden server and app container
(Dockerfile target `dev`) with a test account, two organizations and a seeded
test vault. Never use a real vault for development or tests (see AGENTS.md).

The other pages of this guide:

- [The dev vault](dev-vault.md): the stacks, the snapshot, the fixture, the
  gateway in the dev stack, the bw wrapper, and how to try the web login and
  the auto-commit.
- [Testing](testing.md): the test suites, how to run and write a test of each
  kind, the timeouts, the Playwright gotchas, the documentation screenshots
  and a smoke test on a real account.
- [Releasing](release.md): the version, the documentation site, GitHub Pages
  and publishing the image.

## Getting started

```sh
uv sync --project backend      # backend venv with dev tools
npm ci --prefix frontend       # frontend dependencies
scripts/ci                     # all fast checks, backend and frontend
scripts/ci --fix               # let ruff fix what it can, then check
scripts/ci install-githooks    # run ci on every commit, --force replaces a hook that is not ours
```

Never start the backend on the host (`uv run bwtidy` would run the host's
`bw` with the host's Bitwarden CLI state). Run it in the dev stack instead:
`scripts/dev-app` gives you the app against the seeded dev vault, log in with
the account `dev@bwtidy.test` on `https://vaultwarden` (see
[Credentials](#credentials)).

Three compose files: the two without `dev` in their name run bwtidy (see
[Run bwtidy](../docs/how-to/run.md)), and `compose.dev.yaml` is the test stack
of `scripts/dev-vault` (Vaultwarden and an app container for the tests), not a
way to run bwtidy.

For frontend work with hot reload, run the two parts yourself:

```sh
# the gateway in the dev container, on 127.0.0.1:$BWTIDY_APP_PORT (see .dev-vault.env)
scripts/dev-app --no-open
# frontend dev server on http://localhost:5173, proxies /api, /gateway and /docs to the gateway
cd frontend && BWTIDY_API=http://127.0.0.1:<BWTIDY_APP_PORT> npm run dev
```

## Commands

Every script prints its usage with `--help`.

| Script                  | What it does                                                                             |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| `scripts/ci`            | every fast check: lint, format, types, imports, unit tests, docs, build ([Testing](testing.md)) |
| `scripts/dev-vault`     | the worktree's dev stacks: `up`, `setup`, `seed`, `exec`, `reset`, `down` ([The dev vault](dev-vault.md#commands)) |
| `scripts/dev-app`       | starts the stack and the gateway, opens bwtidy at the account form                       |
| `scripts/manual`        | tests main by hand: updates it, resets the vault, runs `scripts/dev-app` ([The dev vault](dev-vault.md#testing-by-hand)) |
| `scripts/integration`   | the integration tests in the app container ([Testing](testing.md#integration-tests))     |
| `scripts/e2e`           | the browser tests against the gateway ([Testing](testing.md#end-to-end-tests))           |
| `scripts/docs-shots`    | takes the documentation screenshots ([Testing](testing.md#documentation-screenshots))    |
| `scripts/docs-lint`     | checks the images, links and prose of every markdown file ([Testing](testing.md#docs-lint)) |
| `scripts/mcp-reference` | writes `docs/reference/mcp-tools.md` from the MCP tools ([Testing](testing.md#the-mcp-reference)) |
| `scripts/third-party`   | writes the About page's list of packages ([Testing](testing.md#dependencies))           |
| `scripts/docs-site`     | builds or serves the documentation site ([Releasing](release.md#the-documentation-site)) |

After ty, `scripts/ci` runs `uv run lint-imports --no-cache` in `backend/`:
import-linter checks the backend's imports against the contracts in
`[tool.importlinter]` of `backend/pyproject.toml` and names each broken
contract with the chain of imports that breaks it (what the contracts say:
[Layers and their rules](../docs/architecture.md#layers-and-their-rules)).

`scripts/dev-app` runs `scripts/dev-vault up` every time, so after a
`git pull` it rebuilds the image and the app container picks up the new
code. It prints the account and the master password. The gateway's output
goes to the terminal and is appended to `.dev-vault/backend.log`, after a
line with the start time. `scripts/dev-vault down` and `reset` delete that
file with the rest of `.dev-vault/`.

Every script but `scripts/docs-site` is a command line over the package
`scripts/devlib/`, one module per theme: the stacks (numbers, env files, ports,
`run_stacks`, `default_stacks`), compose and the certificate, the snapshot, the
seeding of the dev vault, the gateway in the app container (how it starts and
stops, and the run `scripts/dev-app` and `scripts/manual` share), `/api/health`,
the options and test paths of the two test scripts with the sleep lock, git, the
version and the backend venv. `scripts/docs-site` stays
on its own, the Dockerfile's docs stage copies it alone.

## Credentials

Dev only, never used anywhere else.

| What            | Value                    |
| --------------- | ------------------------ |
| Email           | `dev@bwtidy.test`       |
| Name            | `Dev`                    |
| Master password | `bwtidy-dev-password-1` |
| Organization    | `Family`                 |
| Collections     | `Shared`, `Youth`        |
| Organization    | `Club`                   |
| Collections     | `Shared`                 |

`setup` also registers a second account with the same master password and an
empty vault, for importing a backup into (`test_backup_import.py`,
[docs/restore.md](../docs/restore.md)). The test empties it again when done.
Besides its own organization `Restore` it has organizations named like the dev
account's, `Family` and `Club`, so a restore can match organizations by name.
None of the three has a collection.

| What            | Value                       |
| --------------- | --------------------------- |
| Email           | `restore@bwtidy.test`      |
| Name            | `Restore`                   |
| Master password | `bwtidy-dev-password-1`    |
| Organizations   | `Restore`, `Family`, `Club` |

What the dev vault holds is in [The dev vault](dev-vault.md#the-fixture).
