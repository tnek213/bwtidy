# How bwtidy is built

The map of the code for someone who changes it: what runs where, the layers
and what each may import, where a new thing goes, who writes each kind of
data and how a write flows. The rules bwtidy follows and their reasons are in
[spec.md](spec.md). How one function works is in its module's docstring.
`backend/tests/repo/test_architecture_doc.py` checks that every backend
module and every frontend directory is named here and that every path named
exists.

## The whole on one page

bwtidy is one container with two kinds of process, both entry points of the
Python package `bwtidy`:

- The gateway (`gateway/`, root, entry point `bwtidy-gateway`) is the only
  listener. It serves the built frontend at `/` and the docs site at
  `/docs/`, keeps the browser's routing cookie, proxies `/api/*` to the worker
  of that cookie's account and `/mcp` to the worker of the bearer token's
  account. It makes each account's directory and starts the account's worker
  when the browser unlocks or starts a Bitwarden login. Before a worker runs
  it answers from the account directory whether the account exists, is
  pending and has its CLI logged in. With `BWTIDY_STATS=1` it also records
  the usage stats of the requests it proxies in a file of its own
  (`stats/`, spec: Usage stats). `create_gateway_app` takes extra
  routers (included before the docs site and the static files) and an
  `on_worker_exit` hook, so an outside package can wrap the gateway without
  bwtidy knowing about it (spec: Gateway, Extra routes and the exit hook).
- A worker (`main.py`, entry point `bwtidy`) serves one account for one
  session, as the account's own uid on a unix socket in the account's
  directory. It runs its own `bw serve` as a child on a second socket, holds
  the unlocked vault in RAM and exits when the session ends. Only bw talks to
  the Bitwarden server.

```
browser ------+                 +--> worker A (uid A) --bw.sock--> bw serve A --+
              | http(s)         |    on app.sock                                +--> Bitwarden
MCP client ---+--> gateway -----+                                               |    server
 (Bearer)          root, :8000  +--> worker B (uid B) --bw.sock--> bw serve B --+
```

The worker's code is in layers under `backend/src/bwtidy`. A module imports
from its own layer and the layers below it, never from one above. From the
bottom:

1. The basics, flat modules: `version.py`, `protocol.py`, `logs.py`,
   `timing.py`, `validation.py`, `passphrase.py`, `mcp_token.py` and `db.py`
   import nothing of bwtidy and nothing of each other.
2. `config.py`, `login_state.py` and `stats/`, the basics above the other
   basics: `config.py` reads the version, `login_state.py` the names of
   `protocol.py`, `stats/` (the usage stats) those names and its own modules.
3. `bw/`, talking to the Bitwarden CLI: HTTP to bw serve, its processes and
   bw's JSON.
4. `items/`, the vocabulary: pure functions and models on item dicts.
5. `sections/`, the tool note's sections, and the scoring of an item against
   the templates and datatypes.
6. `operations.py`, one flat module: the operation models and what each
   touches.
7. `vault/`, the account's state in RAM: the vault, the chain of bwtidy's own
   writes, the tool note's id, the formats of the values and the hidden
   values.
8. `backups/`: making a backup, keeping them and reading one back.
9. `session.py`, the session.
10. `worker.py`, the worker's life around the session.
11. `writes/`, the one write path: the check of a batch, the batch driver and
    the writes by kind of operation.
12. `queue/`, the review queue: every edit is an entry, checked on arrival and
    approved into the write path.
13. `mcp/`, the agent's door: read tools without secrets and propose tools
    into the queue.
14. `api/`, the browser's door: one route module per resource.
15. `main.py`, the composition root: `create_app` builds every object and
    passes the hooks at construction.

The gateway stands beside the layers and imports of them only the basics
and `stats/`, never `db.py` and never `stats/report.py`, the worker's side of
the stats, which brings the MCP SDK along. What the gateway, the worker and the frontend all read (cookie
names, headers, the account directory's file names, the backup file name)
lives in `protocol.py`.

A lower layer calls up only through a hook that `main.create_app` passes when
it builds the object, the one place where the flow of a session is visible:

- `SessionHooks` (`session.py`): `on_unlocked` (the background backup, the
  login state, the pending marker), `before_end` (commit the pending UI
  edits), `on_ended` (stop the background backup, drop the decrypted one) and
  `after_end` (the worker exits).
- `VaultCache` `on_load`: keep the tool note's id, prune the to-fill list,
  refresh the formats.
- `ReviewQueue` `on_ui_entry` (the auto-commit's clock) and `on_written` (the
  to-fill list). `BwServeProcess` `on_restart` (unlock the new bw serve).
- `BackupKeeper` `queue_in_use`: the queue's answer to "is this backup in
  use", `backup_in_use` (`queue/wipe.py`) on the database, so the keeper is
  built before the write path and the queue. The queue gets the keeper as a
  peer of the write path, for the account data deletion.

The frontend (`frontend/src`) is React, MUI and AG Grid. `App.tsx` is the
gate (account form, Bitwarden login, unlock, the app) and the page switch.
React Query holds the server's data, one context the commit tracker. The pages
share `api/`, `shared/`, `commit/` and `theme/`, never each other. Every edit
posts an operation to `POST /api/queue`, and the page reloads once the
backend's commit count says the auto-commit wrote it.

## Layers and their rules

Linters in `scripts/ci` hold the layers, so a change that breaks one fails
before it lands. A change that breaks a rule either moves the import to the
layer it belongs in or, when the rule itself is what changes, changes the
config file and this section in the same commit.

The backend's rules are the contracts of `[tool.importlinter]` in
`backend/pyproject.toml`, checked by import-linter (`uv run lint-imports` in
`backend/`). A contract holds for indirect imports too: a chain through other
modules breaks it as a direct import does.

- Layers of the worker: the fifteen layers above, from `main.py` down to the
  basics. The modules of a package import each other, the flat modules that
  share a layer (the basics) never do.
- The gateway takes only basics from the worker: `gateway/` imports no layer
  but the basics and `stats/`, and of those never `db.py` or
  `stats/report.py`.
- Route modules are independent: no route module of `api/` imports another.
  Of `api/` they import only `api/deps.py`, `api/app_state.py`,
  `api/errors.py` and `api/sections.py`.
- bw knows no vocabulary: `bw/` imports nothing of `items/`, `sections/` or
  `vault/`.
- The vocabulary knows no state and no bw: `operations.py`, `sections/` and
  `items/` import nothing of `vault/`, `writes/`, `queue/` or `bw/`.

The frontend's rules are in three config files under `frontend/`:

- `frontend/.dependency-cruiser.cjs` (`npm run lint:deps`), what imports what
  under `src/`: `api/` imports only itself, `shared/` imports `api/`,
  `commit/` imports `api/` and `shared/`, `theme/` is a leaf. A page
  directory (`appbar/` among them) imports those four and itself, never
  another page. Only `App.tsx` and `main.tsx` import a page's `*Page.tsx`. No
  cycle, a type-only import counts.
- `frontend/knip.json` (`npm run lint:exports`): no file, export or type that
  nothing uses. The test files are entries besides `main.tsx`.
- `frontend/.oxlintrc.json` (`npm run lint`): oxlint with the import rules
  (no cycle, no self import, no duplicate import) and
  `consistent-type-imports`.

The rules hold for the production code. A test, `test-utils.tsx`,
`test-setup.ts` and a page's test support (`grid/testSupport.tsx`) may import
what they test from anywhere, and no production file imports them.

## Where a new thing goes

- An operation kind: its model in `operations.py` (in `Operation`,
  `touched`, `item_ids`), its check in `_check_op` of `writes/check.py`, the
  function that applies it in the `writes/` module of its kind with its entry
  in `APPLY` of `writes/path.py` (a planner in `writes/planners.py` when it
  computes an item), its `diff` and `summarize` in `queue/view.py` and an
  entry in `MINIMAL_OPERATIONS` of `tests/support/queue_helpers.py`. If the
  agent may ask for it, a propose tool in `mcp/propose_tools.py` with its
  description in `mcp/texts.py` and its line in `TOOLS` of `mcp/server.py`.
  In the frontend, a builder in `api/operations.ts` and a label in
  `shared/operationLabels.ts`. Every chain ends with `assert_never`, so a
  forgotten branch fails the type check, and a forgotten entry in `APPLY`
  fails `tests/unit/writes/test_path.py`.
- A tool note section: one module in `sections/` that fills in a
  `SectionRules`, its name in `Section` and `SECTIONS` and its change models
  in `SectionChange` (`sections/__init__.py`), a route module in `api/` that
  saves through `queue_changes` and reads the section as the saves under way
  leave it (both in `api/sections.py`), and its page or tab.
- A route: in the `api/` module of its resource, or a new module whose router
  `main.create_app` includes. It takes `Session` from `api/deps.py`, reaches
  the parts through `app_state(request)` and raises domain errors, which
  `api/errors.py` answers. What two route modules need goes into
  `api/deps.py`.
- A name the gateway and the worker (or the frontend) both read, like a
  cookie, a header or a file of the account directory: `protocol.py`.
- A page: a directory under `frontend/src` with its components and its rules,
  the fetchers it needs in the `api/` module of their resource, a value of
  `Page` in `appbar/AppBar.tsx` and a line in the page switch of `App.tsx`. A
  rule that two pages need goes into `shared/`.
- A dialog: a file in the directory of its page. In the grid, a file in
  `grid/dialogs/` named for the dialog, with a case in the `GridDialog` or
  `NormalizeDialog` switch of `grid/dialogs/index.tsx`.
- A setting: one of the deployment is an environment variable read in
  `config.py` or `gateway/settings.py` and named in `example.env`. One of the
  user's lives in the tool note when it travels with the vault (a section,
  like the generator's), in the SQLite settings table when it belongs to the
  account on this machine (the auto-commit numbers, the date format), in a
  cookie or localStorage when it belongs to the browser (the color mode).
- A test: see Tests. A helper two test files share goes into a support
  module, never into a test.

## Changing the architecture

The layers, the packages and the contracts that hold them are the maintainers'
choice, not a side effect of a change. A change fits into them: a new module
goes where "Where a new thing goes" says, and a new import follows the layers.

When a change does not fit, it waits for the maintainers to change the
architecture. That change updates this document and the contract in one
commit, with the reason beside the rule, before or together with the change
that needs it.

A contract is never loosened to let a change pass. That means no new ignore or
exception in a contract, no modules merged to break a cycle, and no import
moved into a function or under `TYPE_CHECKING` to hide it.

A review judges the code against this document. Code that breaks it is a
defect. Another way to organize the code is a proposal for the maintainers,
not a defect, and nothing moves until they take it.

The contracts, each with what runs it:

- The `[tool.importlinter]` contracts in `backend/pyproject.toml`, run by
  `uv run lint-imports`.
- `frontend/.dependency-cruiser.cjs`, run by `npm run lint:deps`.
- `frontend/knip.json`, run by `npm run lint:exports`.
- `backend/tests/repo/test_package_surfaces.py`, one rule for the `__init__`
  of every package.
- `backend/tests/repo/test_architecture_doc.py`, which checks that this
  document names every module and directory and that its paths exist.

## Who writes each kind of data

One writer per kind of data. Where another module writes it too, the table
says why.

| Data | Writer | Module | Exception, and why |
| --- | --- | --- | --- |
| Vault items | `WritePath.apply` under its lock, each kind through its function in `APPLY` | `writes/path.py`, `writes/items.py`, `writes/create.py`, `writes/attachments.py`, `writes/restore.py` | At the unlock `ensure_tool_note` (`vault/tool_note.py`) creates the tool note: no session exists yet, and the write path needs the session's backup |
| Folders | the folder writes, under the same lock | `writes/folders.py` | None |
| The tool note's notes JSON | `_write_note`, for a section save and for the entries of a converted item | `writes/note.py` | `new_tool_note` (`vault/tool_note.py`) gives a new note its first notes, at the unlock |
| The tool note's fields, the backup passwords | `set_backup_passwords`, from the unlock in `session.py`, and `new_tool_note` for a new note | `vault/tool_note.py` | The whole kind is outside the write path: it runs at the unlock, before the backups are re-encrypted to a new master password |
| SQLite schema | `create_schema`, for a new file only | `db.py` | None: nothing changes the tables of an existing file |
| SQLite `change_log` | `Database.record`, from `WritePath.apply` only | `writes/path.py` | `wipe_account_data` (`queue/wipe.py`) clears it, for the account data deletion |
| SQLite `queue` | the intake stores the rows (`enqueue`, `store_ui` in one transaction, `store_refused_secret`), `decide` writes every status (`decide_all` a batch's in one transaction), `dismiss` and `forget_failed_values` delete failed rows and their hidden values | `queue/intake.py`, `queue/entries.py` | `retry` (`queue/commit.py`) puts failed rows back to pending with their operations refreshed, in one transaction (`reset_queue_entries`). `wipe_account_data` deletes every row but the pending MCP proposals kept on Write first |
| SQLite `settings`: `tool_note_id` | `ToolNoteId` | `vault/tool_note.py` | None |
| SQLite `settings`: `auto_commit_after_seconds`, `auto_commit_max_entries` | `AutoCommit.configure` | `queue/autocommit.py` | None |
| SQLite `settings`: `hidden_identity` | `HiddenValues` | `vault/hidden.py` | None |
| SQLite `settings`: `mcp_token_last_used` | `McpToken`, deleted with the token | `mcp/auth.py` | None |
| SQLite `settings`: `date_format`, `hide_tool_note`, `rare_fields_max` | `set_display` | `api/settings.py` | None |
| SQLite `backup_fingerprints` | `create_backup`, one row per backup | `backups/collect.py` | `BackupKeeper` (`backups/keeper.py`) forgets a row with its backup, `wipe_account_data` clears the table |
| Backups `*.zip.age` | `write_encrypted`, from `create_backup` and `reencrypt_all` | `backups/crypto.py` | `BackupKeeper` deletes them: the retention (`prune` in `backups/files.py`), Delete, the account data deletion (`wipe_account_data` in `queue/wipe.py`) |
| `mcp-token.sha256` | `McpToken.create` and `remove` | `mcp/auth.py` | None |
| `bw-login-state` | `LoginStateFile.record`, through `BwLogin.note` | `login_state.py`, `bw/login.py` | None |
| The delete marker `app/delete-requested` | `write_delete_marker`, for Delete my account | `api/account.py` | `drop_stale_delete_marker` deletes one an earlier worker left, at a worker's start. The gateway acts on it when the worker exits (`Gateway.delete_on_request`) |
| The pending marker `.pending` | `Accounts.create` | `gateway/accounts.py` | `WorkerLifetime.unlocked` (`worker.py`) deletes it after the first unlock, which only the worker sees |
| The account directories | `Accounts.create` (under a temporary name, renamed into place last), `Accounts.delete` and `sweep_pending` | `gateway/accounts.py` | None |
| An account's directory under the backups root | `BackupsRoot.make` and `remove` | `gateway/backups.py` | None |
| `stats.db`, the usage stats | `Recorder.write`, batched from what `StatsMiddleware` notes, and `Recorder.capacity` samples, through `stats/db.py` | `stats/recorder.py`, `gateway/stats.py` | `open_stats` (`stats/db.py`) runs the migrations at the gateway's start, `roll_up` the retention |
| `worker.log` | `Accounts.create` makes it, `open_log` opens it for the worker without following a link or blocking | `gateway/accounts.py`, `gateway/workers.py` | None |
| The CLI state `bw/` of an account | bw itself, run by `BwLogin`, `BwServeProcess` and `run_cli` | `bw/` | None |
| The vault in RAM | `VaultCache.load` | `vault/cache.py` | `replace_item` puts a tool note just written in at once (from `WritePath` and `set_backup_passwords`), so a save reads as saved before the next load |
| The chain of own writes | `OwnWrites`, recorded by the write path's PUTs | `vault/own_writes.py` | None |

## The container

`compose.yaml` runs the image of the `Dockerfile` target `app` as container
`bwtidy`. The image is named by `BWTIDY_IMAGE` and `BWTIDY_VERSION` in the
`.env` made from `example.env`. The published image is `docker.io/tnek213/bwtidy`, which the GitHub Actions workflow "Image" builds and pushes on every push to main and every release. `compose.build.yaml` builds it from the
checkout as `bwtidy:local` instead (how to run it: docs/how-to/run.md). The
container has a read-only root file system with a tmpfs `/tmp`, the
capabilities the gateway needs to start workers as other uids, a health check
on `/api/health` and its port 8000 published on the host's `127.0.0.1` (8765
by default). It mounts the volume `bwtidy-accounts` at `/data/accounts` and
the backups at `/backups` (the volume `bwtidy-backups` or the host directory
that `BWTIDY_BACKUPS` names, with `BWTIDY_BACKUPS_ROOT=/backups`). A stop gets 150
seconds, which `STOP_TIMEOUT` in `gateway/workers.py` plus the gateway's
request drain must stay below.

The gateway runs as root and starts each worker as the account's uid. Without
the worker settings `main.py` runs as a single-user backend on a TCP port,
which only the integration tests use.

The `app` stage holds the backend's source with its installed dependencies
(no tests), the built frontend, bw with `docker/unref-share-timers.js` (a bw
command exits as soon as it has answered), `docs/restore.md` at
`/app/docs/restore.md` (every backup holds it as its RESTORE.md) and the docs
site at `/app/site`. The `docs` stage builds that site from `zensical.toml`,
`docs/` and `overrides/main.html` (the beta disclaimer as a banner on every
page) with `scripts/docs-site build`, using the Zensical of the backend's
dependency group `docs`, which stays out of the app's venv. The workflow
`.github/workflows/docs.yml` builds the same site and publishes it on GitHub
Pages. The user docs follow Diátaxis (https://diataxis.fr) with one folder per kind
of page under `docs/` and the rules for writing them are in AGENTS.md
(Writing docs).

The `dev` stage is the app container of the dev stack (`compose.dev.yaml`). It
runs the tests from a mount of `backend/` and mounts the worktree's `docs/` at
`/app/docs` read-only. Its docs site is the one its image was built with, so
`scripts/docs-site serve` previews a change of the docs.

## Backend packages

Under `backend/src/bwtidy`, one line per module, in the order of the layers.
A package's `__init__.py` names its theme and is one of two kinds. Either it
re-exports exactly what modules outside the package import from it, and they
import from the package (`from bwtidy.writes import WritePath`), never a
module of it: `bw/`, `vault/`, `backups/`, `writes/` and `mcp/`. Or it
re-exports nothing and the package's modules are imported by name
(`from bwtidy.queue.review import ReviewQueue`): `items/`, `queue/`, `api/` and
`gateway/`. `sections/` defines `Section`, `SECTIONS` and `SectionChange` in
its `__init__.py` and re-exports what reads and writes the notes JSON as a
whole, its section modules are imported by name. No module imports a name
that starts with an underscore from another module.
`backend/tests/repo/test_package_surfaces.py` checks this.

The basics, the two above the others first:
- `config.py` the worker's settings from the environment, with the session timeouts and the backup retention (`Settings`, `Policy`, `Retention`).
- `login_state.py` the file `bw-login-state` that tells the gateway whether the CLI is logged in.
- `logs.py` the log format: a time on every line, other libraries from WARNING up.
- `timing.py` the step durations of a slow operation, for one INFO line.
- `version.py` `semver`, the version as semver.org spells it, stdlib only so `scripts/devlib/versions.py` loads it by path.
- `validation.py` the answer to a refused request body (never the body), and the one text of a validation error.
- `passphrase.py` age passphrase encryption with the master password and the password hash, the only caller of scrypt.
- `mcp_token.py` the MCP token's record and its checks, which the gateway and the worker share.
- `protocol.py` the strings the gateway, the worker and the frontend agree on.
- `db.py` `Database`, the SQLite file: change log, queue, settings, backup fingerprints, at its one schema.

The usage stats, `stats/` (beside `config.py`, only with `BWTIDY_STATS=1`):
- `stats/db.py` the gateway's stats file: `MIGRATIONS`, the writes and the retention.
- `stats/parse.py` what the gateway reads of a request: route templates, JSON-RPC methods and tool names, outcomes and the error report, each checked against the worker's names.
- `stats/recorder.py` `Recorder`: what was seen in RAM, written in batches off the request path, the capacity samples.
- `stats/report.py` the worker's side: its names at `/stats/names` and the error header of its 500.
- `stats/summary.py` `summary`, the one dict a page or `bwtidy stats` shows, and the top errors.

Talking to bw, `bw/`:
- `bw/client.py` `BwClient`, the async client of bw serve's API.
- `bw/serve.py` `BwServeProcess` starts bw serve and restarts it when it exits.
- `bw/process.py` `run_cli`, the one way to run a bw CLI command, and `stop_bw`.
- `bw/login.py` `BwLogin`, the CLI login in a pseudo-terminal for the web UI, and the CLI's logout.
- `bw/revision.py` `same_revision`, the one comparison of two revisionDates.

The vocabulary, `items/`:
- `items/coordinates.py` where a value lives in an item (its grid column), and `is_secret`.
- `items/normalize.py` rename fields, merge columns and items, near-duplicate hints.
- `items/patterns.py` the datatype pattern engine, bounded for untrusted patterns.
- `items/ssh_keys.py` an SSH key's public key and fingerprint from its private key.

The tool note's sections, `sections/`:
- `sections/__init__.py` `SECTIONS`, each section's rules by name, and `SectionChange`.
- `sections/notes.py` the notes JSON and `SectionRules`, what every section module builds on.
- `sections/templates.py` the templates section: the model, its parser and its changes.
- `sections/datatypes.py` the datatypes section and the check that no datatype a template uses is deleted.
- `sections/generator.py` the generator section and the query of bw's generator for a set of options.
- `sections/to_fill.py` the toFill section as the note stores it, and its changes.
- `sections/note.py` the note section, the name the tool note is expected to have.
- `sections/matching.py` the scoring and matching of an item against the templates and datatypes.

The operations:
- `operations.py` the operation models and what each touches.

The account's state in RAM, `vault/`:
- `vault/cache.py` `VaultCache`, the vault in RAM.
- `vault/own_writes.py` `OwnWrites`, the chain of bwtidy's own writes this session.
- `vault/tool_note.py` finds and creates the tool note, keeps its id (`ToolNoteId`) and its backup password fields.
- `vault/hidden.py` `HiddenValues`, the age identity that encrypts queued secret values.
- `vault/formats.py` the cached note's templates and datatypes, and `Formats`, how the cached values match them.
- `vault/statistics.py` the counters of how hard the pattern checks work, kept by `Formats`.

Backups, `backups/`:
- `backups/files.py` the backup files: listing, names, the retention rule and the fingerprint.
- `backups/crypto.py` the age encryption of a backup file and the re-encryption after a new master password.
- `backups/collect.py` `create_backup`: the exports and listings through bw, checked, into one zip with its RESTORE.md.
- `backups/reader.py` `BackupReader`, a decrypted backup in RAM.
- `backups/keeper.py` `BackupKeeper`: the backup of a write, the background backup and the retention.

The session:
- `session.py` `SessionManager`: the unlock, the session token, the timeouts and the end.
- `worker.py` `WorkerLifetime`: the exit after the session, the pending marker.

The write path, `writes/`:
- `writes/path.py` `WritePath.apply`, the batch driver, `WriteContext`, what the modules of the kinds call on it (the vault in RAM, bw serve, the backups and the shared steps of a write), and `APPLY`, the function of each kind of operation, typed to its class.
- `writes/check.py` `validate`, the check of a batch before anything is written.
- `writes/rules.py` the secret rules every write checks.
- `writes/planners.py` what an operation makes of an item, without writing.
- `writes/saves.py` a section save: its check and the notes as its write leaves them.
- `writes/restore_plan.py` what a restoreItems does per item of a change log row.
- `writes/items.py` the writes of an item or a few: a leaf, a move, the collections, the trash, a rename, a merge, the URIs.
- `writes/create.py` createItem and the conversions that create a new item and trash the old one.
- `writes/folders.py` the folder of a move or a new item, and the folder kinds.
- `writes/attachments.py` renameAttachment, an upload and a delete that leave one copy.
- `writes/note.py` `_write_note`, the one write of the tool note's notes.
- `writes/restore.py` restoreItems, step by step as the plan says.

The review queue, `queue/`:
- `queue/review.py` `ReviewQueue`, the facade the doors and `main.py` call.
- `queue/state.py` `QueueState`, what the modules share, and an entry's stored form.
- `queue/intake.py` what comes in: the checks on arrival and the stores.
- `queue/commit.py` `approve` and `retry`: a batch through the write path, each entry applied or blamed.
- `queue/entries.py` the rows: the one status write, the listing, the decisions without a write.
- `queue/agent.py` what the agent reads of its own entries.
- `queue/sections.py` a section as the saves under way leave it.
- `queue/wipe.py` the account data deletion and the backups the queue still needs.
- `queue/view.py` an entry's summary and its masked diff.
- `queue/autocommit.py` `AutoCommit`, the commit of the UI's entries on time or count.
- `queue/to_fill.py` `ToFill`, the list of secret fields still to fill in.

The agent's door, `mcp/`:
- `mcp/server.py` `BwtidyMcp`, which gates every tool, and `create_mcp_server`, which registers `TOOLS` and hands each tool its interface: `AgentVault` to the read and propose tools, `SessionActions` to the session tools.
- `mcp/vault.py` `AgentVault`, the vault as the agent sees it, what each tool may read, and its proposals into the queue.
- `mcp/texts.py` what the agent reads: the instructions and each tool's description.
- `mcp/read_tools.py` the read tools.
- `mcp/propose_tools.py` the propose tools, one operation each.
- `mcp/session_tools.py` the two tools that end the session after the answer, and `SessionActions`, all they may do.
- `mcp/auth.py` `McpToken`, the worker's token file, and `RequireMcpToken` in front of `/mcp`.

The browser's door, `api/`:
- `api/app_state.py` `AppState`, the parts every route reaches through `app_state(request)`: the settings, the database, the vault in RAM, the session, the write path, the keeper, the queue and the rest.
- `api/deps.py` what the route modules share: the session dependency, the request header check, the session's state as the login routes answer it, the tool note.
- `api/errors.py` the HTTP answer of each domain error.
- `api/sections.py` what the section routes share: the queued changes and the current view.
- `api/session.py` unlock, heartbeat, log out.
- `api/bwlogin.py` the Bitwarden CLI login from the web UI.
- `api/grid.py` the vault as a grid, and the one route that answers a secret.
- `api/queue.py` the queue: UI edits, decisions, the auto-commit.
- `api/operations.py` `POST /api/operations` straight to the write path for scripts and test setup, the change log, restore plans, each from the parts `AppState` holds.
- `api/backups.py` list, take, download and delete backups.
- `api/templates.py` the templates section and the matches, and the tool note's rename back and keep name straight to the write path.
- `api/datatypes.py` the datatypes section and the live pattern checks.
- `api/generator.py` the generator settings and an example value.
- `api/statistics.py` the numbers of the Statistics page.
- `api/mcp.py` the MCP switch and the MCP token.
- `api/settings.py` the display settings.
- `api/account.py` deleting the account's data, or the whole account.

The composition root:
- `main.py` `create_app` and the `bwtidy` entry point, with `bwtidy stats`.

The gateway, `gateway/`:
- `gateway/app.py` the gateway's routes and the account state machine, the static files.
- `gateway/proxy.py` the proxy to the workers: the routing by cookie and token, the headers that pass.
- `gateway/settings.py` the gateway's settings from the environment.
- `gateway/accounts.py` account keys, directories and uids.
- `gateway/backups.py` `BackupsRoot`, each account's directory under the backups root.
- `gateway/workers.py` start, watch and stop one worker per account.
- `gateway/stats.py` `StatsMiddleware` around the proxy: notes each request that reaches a worker for the recorder, and learns the workers' names.

## Frontend

React, MUI and AG Grid under `frontend/src`, with MUI X's date picker for
date fields. `main.tsx` mounts `App.tsx`. Four directories hold what the pages
share, the others are the pages and the app bar. What each may import is in
Layers and their rules.

- `api/` the backend's resources, one file each with its fetchers, React Query keys and JSON types, `apiFetch` in `api/api.ts`, the operation builders in `api/operations.ts`. The rules of a page live in the page.
- `shared/` the helpers and small components without a page: the coordinates (a mirror of `items/coordinates.py`), the account's lifecycle (reopen, switch, the other tabs) and the lock, how a cell value shows, the queue's failure text, the generator's limits and checks, the item types and template coordinates the grid and the Types page share, the date and time format, the date field, the form dialog, the generator form, the password managers' opt-out.
- `commit/` the commit tracker: `AutoCommitProvider`, its context and the unread marks.
- `theme/` the color mode, the layout (Normal or Compact) and `Prose`, the paragraph with balanced lines.
- `about/` the About page: the version, the license, the credits and the third-party packages.
- `appbar/` the app bar: page links, Commit now, the MCP switch, color mode, log out, the more menu.
- `backups/` the alert about backups to re-encrypt after a new master password.
- `changelog/` the Change log page: its filters, restore, delete backup and the diff report.
- `grid/` the Vault grid page, its cells, toolbar and dialogs, one file per dialog in `grid/dialogs/`.
- `queue/` the Queue page.
- `session/` the account form, the Bitwarden login, the unlock and the heartbeat.
- `settings/` the Settings page: the display settings, the default generator, deleting the data.
- `statistics/` the Statistics page.
- `templates/` the Types page: the templates and the datatypes.

## How a write flows

A grid edit, from the cell to the reload:

1. The cell: AG Grid runs with `readOnlyEdit`, the edit becomes an operation
   (`grid/useCellEvents.ts`, the builders of `api/operations.ts`) that
   `grid/writer.ts` posts to `POST /api/queue` (`api/queue.ts`).
2. The route (`api/queue.py`) parses the operations and hands them to
   `ReviewQueue`.
3. The check on arrival (`queue/intake.py`): every operation of the batch
   goes through `validate` (`writes/check.py`) on the vault in RAM, before any
   is stored. A refusal answers 409 or 422 and stores nothing.
4. The store (`queue/intake.py`): a secret value is encrypted with the hidden
   identity (`vault/hidden.py`) and the batch is stored as pending entries in
   one transaction (`db.py`). The grid shows the queued values as an overlay
   (`grid/queued.ts`).
5. The auto-commit (`queue/autocommit.py`) approves every pending UI entry
   once the oldest is old enough or enough are pending, one commit at a time.
6. The approve (`queue/commit.py`) marks the entries approved with the diff
   they show (`queue/view.py`), decrypts their hidden values into RAM and
   hands the operations to the write path as one batch.
7. The sync and the check (`writes/path.py`, under its lock): sync bw serve,
   load the vault (`vault/cache.py`), `validate` again.
8. The backup (`backups/keeper.py`): the newest backup when it holds the vault
   as it is, else a new one (`backups/collect.py`). Delete and the retention
   keep it until the batch ends. Then one more sync and load, and the check
   again when the vault changed.
9. The writes, one operation at a time (the module of its kind in `writes/`,
   through the steps `WriteContext` names): GET the item, check it against
   the chain of own writes (`vault/own_writes.py`) and what the grid showed,
   check the secret rules on it (`writes/rules.py`), change only the touched
   leaves and PUT the whole item.
10. The record: a change log row per operation (`db.py`), the entry marked
    applied at once (`queue/commit.py`).
11. The end of the batch (`writes/path.py`): a reload of the vault and a
    background backup, also when a write failed.
12. The settling (`queue/commit.py`): a conflict or an invalid operation that
    the check found fails the entries it blames and the rest is tried again, a write that fails
    midway fails its entry and puts the later ones back to pending. The
    commit is counted.
13. The reload in the browser: `commit/AutoCommitProvider.tsx` sees the
    commit count change, refetches the grid and the other pages' data, and
    reports the failed entries.

The other ways in:

- A tool note section save (templates, datatypes, generator, the to-fill
  list) is a UI entry of single changes (`api/sections.py`, `queue/intake.py`)
  whose commit the route starts at once. Its write applies the changes to the
  note as it is then (`writes/saves.py`, `writes/note.py`), so two saves never
  undo each other. Every read of a section under way folds the pending saves
  over the cached note (`queue/sections.py`).
- An MCP proposal: a propose tool (`mcp/propose_tools.py`) builds the
  operation and its summary and queues it through `AgentVault` with source
  mcp. The check on arrival refuses any secret value. The auto-commit never
  takes it: the user approves it on the Queue page, through the same approve.
- The tool note's rename back and keep name: `POST
  /api/tool-note/rename-back` and `POST /api/tool-note/keep-name`
  (`api/templates.py`) call the write path directly, without the queue.
- `POST /api/operations` (`api/operations.py`) calls the write path directly,
  without the queue, for scripts and test setup.

## Where state lives

- Worker RAM goes with the session: the master password, its hash and the
  session key (`session.py`), the vault and the chain of own writes
  (`vault/`), the hidden identity, the last decrypted backup, when each UI
  entry was queued, the format results and the pattern counters.
- Gateway RAM: the routing cookie's account and the running workers. A restart
  forgets both, and the browser logs in again.
- The usage stats, with `BWTIDY_STATS=1` only: `stats.db` of the gateway
  (`stats/db.py`), next to the accounts directory unless `BWTIDY_STATS_DB`
  names another, survives restarts and updates through its migrations.
- SQLite, `app/bwtidy.sqlite` (`db.py`), survives a restart and a deploy. At a
  worker's start the queue fails what a killed commit left approved and
  deletes the hidden values of failed entries (`queue/entries.py`), and
  pending UI entries are due at the next unlock.
- The tool note travels with the vault: its notes JSON holds the sections
  `templates`, `datatypes`, `generator` and `toFill` (one module each in
  `sections/`), its hidden fields the backups' passwords.
- The account directory `/data/accounts/HOST/EMAIL/` (spec: Gateway), made at
  the account's first Bitwarden login: `app/` (the SQLite file,
  `mcp-token.sha256`, `bw-login-state`, the delete marker), the CLI state
  `bw/`, the sockets in `run/`, `home/` and `worker.log`.
- The backups `*.zip.age`: under the backups root in `/backups/HOST/EMAIL/`
  with `compose.yaml`, else in `app/backups/` of the account directory
  (`BWTIDY_BACKUPS_DIR` names another).
- The browser: React Query holds the server's data and two entries of the
  page's own, the account (`api/account.ts`) and the note above the unlock
  form (`shared/session.ts`). The context of `commit/autoCommit.ts` holds the
  settling entries, the failures and the conflict marks. localStorage holds
  per account the form's last server and email (`session/account.ts`), the
  account in use (`api/account.ts`) and the last log out
  (`shared/account.ts`), the queue entries seen (`commit/unread.ts`) and the
  failed entries reported (`commit/autoCommit.ts`), and per browser the layout
  (`theme/layout.tsx`). The cookies: the color mode (`theme/colorMode.tsx`),
  `bwtidy_account` of the gateway and `bwtidy_session` of the worker. The tabs
  of a profile share them, so a tab reloads when another changes the account
  or logs out. Drafts and typed secrets stay in their component.

## Where the secret rules are enforced

The rules are in spec.md (Secret values). These places keep them:

- `items/coordinates.py` `is_secret` decides what is secret, every check below
  asks it.
- `api/grid.py` masks secret cells, its `secret` route is the only one that
  answers a secret.
- `mcp/vault.py` gives the agent column names and projections without secret
  values, never an item of the cache, and leaves the tool note out.
  `mcp/server.py` answers only while a session is unlocked and MCP is on, and
  reports an error by its type only.
- `queue/intake.py` refuses an agent's secret, stores a silent refusal without
  its value and encrypts every queued secret through `vault/hidden.py`.
  `queue/agent.py` answers the agent a failure by one fixed word.
- `queue/view.py` masks secrets in every diff.
- `writes/rules.py` keeps a secret out of plain columns, in `validate` in batch
  order, on a new item and at write time on the item each write GETs.
- `writes/path.py` passes on only the text of `BwError` and `ValueError`.
- `db.py` the change log holds kinds, ids and coordinates, never values or
  error messages.
- `stats/parse.py` keeps of a request only the names the worker gives
  (`stats/report.py`), never a path, an id, an argument or a message.
- `validation.py` never quotes a refused value in a validation error, in both
  apps, and `logs.py` keeps other libraries at WARNING.
- `bw/login.py` masks the password and the session key in the CLI's output.
- `backups/collect.py` passes `BW_SESSION` in the environment, and no export
  touches the disk in plain.
- Tests compare a value into a bool before the assert (`secret_is` in
  `tests/e2e/support/api.py`).

## Tests

- Unit (`scripts/ci`): `backend/tests/unit`, mirrored on the packages and
  named after the module, like `tests/unit/writes/test_path.py` for
  `writes/path.py`, with `tests/support/fake_bw.py` in place of bw serve and
  the app built from one `AppEnv` (`tests/support/app_env.py`). Logic and
  routes.
- Frontend (`scripts/ci`): vitest's `*.test.tsx` next to the code, the grid's
  builder and renderer in `grid/testSupport.tsx`.
- Scripts (`scripts/ci`): `backend/tests/scripts`, what the files in
  `scripts/` do, a script loaded by name through `load_script`
  (`tests/scripts/conftest.py`).
- Repo (`scripts/ci`): `backend/tests/repo`, the facts that span files: this
  document (`tests/repo/test_architecture_doc.py`), the two versions
  (`tests/repo/test_versions.py`), the third-party list
  (`tests/repo/test_third_party.py`) and the test tree
  (`tests/repo/test_test_tree.py`: no test imports a test or a conftest,
  every test file is in `unit`, `scripts`, `repo`, `integration` or `e2e`,
  every unit test file is named after a module and every folder of `unit` is
  a package).
- Support: `backend/tests/support`, what several test files share (the fake bw,
  the sample vault, the test environments, the docs shots), imported as
  `tests.support.NAME`. The integration tests share the package
  `backend/tests/integration/support`: the bw CLI and the dev credentials
  (`tests/integration/support/cli.py`), the in-process app and the items made
  elsewhere (`tests/integration/support/app.py`), the `Vault` and the
  comparisons (`tests/integration/support/vault.py`) and the restore round trip
  (`tests/integration/support/restorer.py`). Only the fixtures are in
  `tests/integration/conftest.py`.
- Integration (`scripts/integration`): `backend/tests/integration`, the
  single-user backend against Vaultwarden and real bw, for what depends on
  bw's real answers: backups, writes, the login.
- End-to-end (`scripts/e2e`): `backend/tests/e2e`, Playwright through the
  gateway, for what the UI does. `tests/e2e/conftest.py` holds the fixtures,
  the package `backend/tests/e2e/support` the helpers, one module per theme.
- Docs screenshots (`scripts/docs-shots`): `backend/tests/e2e/docs`, e2e tests
  that write the images of `docs/images`.

`scripts/ci` also checks the generated files (`scripts/third-party`,
`scripts/mcp-reference`) and the docs. How to run and write each kind of test
is in `dev/testing.md`, the dev stack in `dev/dev-vault.md`, the commands for
one file in `AGENTS.md`.
