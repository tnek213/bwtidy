# How bwtidy is built

This page maps the code for someone who changes it. It shows what runs where
and what each layer may import. It says where a new thing goes and who writes
each kind of data, and it follows one write from the cell to the vault. The
rules bwtidy follows and their reasons are in [spec.md](spec.md). How one
function works is in its module's docstring.
`backend/tests/repo/test_architecture_doc.py` checks that every backend
module and every frontend directory is named here and that every path named
exists.

## The whole on one page

bwtidy is one container with two kinds of process. Both are entry points of
the Python package `bwtidy`.

The gateway (`gateway/`, entry point `bwtidy-gateway`) runs as root and is the
only listener. It serves the built frontend at `/` and the docs site at
`/docs/`. It keeps the browser's routing cookie. It answers `/api/health`
itself and proxies the rest of `/api/*` to the worker of that cookie's
account. It proxies `/mcp` to the worker of the bearer token's account. A
Bitwarden login for a new account makes the gateway create the account's
directory. When the browser unlocks or starts a Bitwarden login, the gateway
starts the account's worker. Before a worker runs it answers from the account
directory whether the account exists and whether it is pending and has its CLI
logged in.

With `BWTIDY_STATS=1` the gateway also records the usage stats of the requests
it proxies in a file of its own. The code for that is in `stats/`
([spec.md, Usage stats](spec.md#usage-stats)). `create_gateway_app` takes
extra routers and an `on_worker_exit` hook as well. The routers come before
the docs site and the static files. So an outside package such as the demo
can wrap the gateway without bwtidy knowing about it
([spec.md, Extra routes and the exit hook](spec.md#extra-routes-and-the-exit-hook)).

A worker (`main.py`, entry point `bwtidy`) serves one account for one
session. It runs as the account's own uid on a unix socket in the account's
directory. It starts its own `bw serve` as a child on a second socket and
holds the unlocked vault in RAM. It exits when the session ends. Only bw talks
to the Bitwarden server.

[How bwtidy fits together](explanation/overview.md) draws these processes for
two accounts. The gateway listens on port 8000 in the container. A worker
answers on `run/app.sock` in its account's directory and its `bw serve` on
`run/bw.sock`.

The worker's code lies in fifteen layers under `backend/src/bwtidy`. A module
imports from its own layer and the layers below it but never from a layer
above. The picture shows the stack with `main.py` on top and the gateway
beside it.

```mermaid alt="The fifteen layers of the worker with main.py on top, and the gateway beside the two lowest"
block-beta
  columns 3
  main["main.py, the composition root"]:2 space
  api["api/, the browser's door"]:2 space
  mcp["mcp/, the agent's door"]:2 space
  queue["queue/, the review queue"]:2 space
  writes["writes/, the one write path"]:2 space
  worker["worker.py, the worker's life around the session"]:2 space
  session["session.py, the session"]:2 space
  backups["backups/, making and keeping backups"]:2 space
  vault["vault/, the account's state in RAM"]:2 space
  operations["operations.py, the operation models"]:2 space
  sections["sections/, the tool note's sections"]:2 space
  items["items/, the vocabulary"]:2 space
  bw["bw/, talking to the Bitwarden CLI"]:2 space
  config["config.py, login_state.py and stats/"]:2 gateway["gateway/"]
  basics["the basics, eight flat modules"]:2 space
  gateway --> config
  gateway --> basics
```

At the bottom lie the basics. They are eight flat modules that import nothing
of bwtidy and nothing of each other ([Backend packages](#backend-packages)
names them). Above them `config.py` and `login_state.py` as well as `stats/`
form a layer of their own. They are basics too but read other basics, as
`config.py` reads the version and `login_state.py` the names of
`protocol.py`. `stats/` holds the usage stats and reads those names too.

Next comes `bw/`, which talks to the Bitwarden CLI. It covers HTTP to bw serve
as well as bw's processes and its JSON. The vocabulary follows. `items/` holds
pure functions and models on item dicts. `sections/` holds the tool note's
sections and the scoring of an item against the templates and datatypes.
`operations.py` is one flat module with the operation models and what each
touches.

The state comes next. `vault/` keeps the account's state in RAM. That is the
vault and the chain of bwtidy's own writes as well as the tool note's id, the
formats of the values and the hidden values. `backups/` makes a backup, keeps
them and reads one back. Above them `session.py` holds the session and
`worker.py` the worker's life around it.

The top layers are the write path and the doors. `writes/` is the one write
path. It holds the check of a batch and the batch driver as well as the writes
by kind of operation. `queue/` is the review queue, where every edit becomes
an entry that is checked on arrival and approved into the write path. `mcp/`
is the agent's door with read tools that hold no secrets and propose tools
that feed the queue. `api/` is the browser's door with one route module per
resource. At the top `main.py` is the composition root. Its `create_app`
builds every object and passes the hooks at construction.

The gateway stands beside the layers. Of the worker's code it imports only the
basics (`config.py` and `login_state.py` as well as `stats/` among them). It
never imports `db.py` or `stats/report.py`. That module is the worker's side
of the stats and brings the MCP SDK along. What the gateway, the worker and
the frontend all read lives in `protocol.py`. That covers the cookie names
and the headers as well as the file names of the account directory and the
backup file name.

A lower layer calls up only through a hook. `main.create_app` passes each hook
when it builds the object, which makes it the one place where the flow of a
session is visible. The hooks are these:

- `SessionHooks` (`session.py`) has four. `on_unlocked` starts the background
  backup, records the login state and clears the pending marker. `before_end`
  commits the pending UI edits. `on_ended` stops the background backup and
  drops the decrypted one. `after_end` makes the worker exit.
- `VaultCache` calls `on_load` to keep the tool note's id, prune the to-fill
  list and refresh the formats.
- `ReviewQueue` calls `on_ui_entry` for the auto-commit's clock and
  `on_written` for the to-fill list.
- `BwServeProcess` calls `on_restart` to unlock the new bw serve.
- `BackupKeeper` asks `queue_in_use` whether a backup is in use. The queue
  answers with `backup_in_use` (`queue/wipe.py`) on the database, so the
  keeper is built before the write path and the queue. The queue in turn gets
  the keeper as a peer of the write path for the account data deletion.

The frontend (`frontend/src`) uses React, MUI and AG Grid. `App.tsx` is the
gate and the page switch. As the gate it shows the account form, the Bitwarden
login or the unlock until the app opens. React Query holds the server's data
and one context holds the commit tracker. The pages share `api/` and
`shared/` as well as `commit/` and `theme/` but never import each other.
Every edit posts an operation to `POST /api/queue`. The page refetches its
data once the backend's commit count says that the auto-commit wrote it.

## Layers and their rules

Linters in `scripts/ci` hold the layers, so a change that breaks one fails
before it lands. A change that breaks a rule moves the import to the layer it
belongs in. When the rule itself is what changes, the config file and this
section change in the same commit.

The backend's rules are the contracts of `[tool.importlinter]` in
`backend/pyproject.toml`. import-linter checks them
(`uv run --locked lint-imports` in `backend/`). A contract holds for indirect
imports too, so a chain through other modules breaks it just like a direct
import. The contracts are these:

- "Layers of the worker" holds the fifteen layers above from `main.py` down to
  the basics. The modules of a package may import each other but the flat
  modules that share a layer never do.
- "The gateway takes only basics from the worker" lets `gateway/` import no
  layer but the basics (`stats/` among them). Of those it never imports
  `db.py` or `stats/report.py`.
- "Route modules are independent" means that no route module of `api/`
  imports another. Of `api/` they import only `api/deps.py` and
  `api/app_state.py` as well as `api/errors.py` and `api/sections.py`.
- "bw knows no vocabulary" means that `bw/` imports nothing of `items/`,
  `sections/` or `vault/`.
- "The vocabulary knows no state and no bw" covers `operations.py` with
  `sections/` and `items/`. They import nothing of `bw/` and nothing of the
  state in `vault/`, `writes/` and `queue/`.

The frontend's rules live in three config files under `frontend/`:

- `frontend/.dependency-cruiser.cjs` (`npm run lint:deps`) says what imports
  what under `src/`. `api/` imports only itself and `shared/` imports `api/`.
  `commit/` imports `api/` and `shared/` while `theme/` is a leaf. A page
  directory (`appbar/` among them) imports those four and itself but never
  another page. Only `App.tsx` and `main.tsx` import a page's `*Page.tsx`. No
  cycle is allowed, and a type-only import counts.
- `frontend/knip.json` (`npm run lint:exports`) allows no file, export or type
  that nothing uses. The test files are entries besides `main.tsx`.
- `frontend/.oxlintrc.json` (`npm run lint`) runs oxlint with the import rules
  and `consistent-type-imports`. The import rules forbid a cycle, a self
  import and a duplicate import.

The rules hold for the production code. A test may import what it tests from
anywhere. So may `test-utils.tsx` and `test-setup.ts` as well as a page's
test support such as `grid/testSupport.tsx`. No production file imports any of
them.

## Where a new thing goes

- An operation kind has its model in `operations.py`. It joins the union
  `Operation` there and gets a branch in `touched` and in `item_ids`. Its
  check goes in `_check_op` of `writes/check.py`. The function that applies it
  lives in the `writes/` module of its kind with its entry in `APPLY` of
  `writes/path.py`. When it computes an item it also gets a planner in
  `writes/planners.py`. Its `diff` and `summarize` go in `queue/view.py` and
  it needs an entry in `MINIMAL_OPERATIONS` of
  `tests/support/queue_helpers.py`.

    In the frontend it gets a builder in `api/operations.ts` and a label in
    `shared/operationLabels.ts`. If the agent may ask for it, it also gets a
    propose tool in `mcp/propose_tools.py` with its description in
    `mcp/texts.py` and its line in `TOOLS` of `mcp/server.py`. Every
    chain ends with `assert_never`, so a forgotten branch fails the type
    check. A forgotten entry in `APPLY` fails
    `tests/unit/writes/test_path.py`.
- A tool note section is one module in `sections/` that fills in a
  `SectionRules`. Its name goes into `Section` and `SECTIONS` and its change
  models into `SectionChange` (all in `sections/__init__.py`). It needs a
  route module in `api/` that saves through `queue_changes` and reads the
  section as the saves under way leave it (both in `api/sections.py`). It
  also needs its page or tab.
- A route goes in the `api/` module of its resource or in a new module whose
  router `main.create_app` includes. It takes `Session` from `api/deps.py` and
  reaches the parts through `app_state(request)`. It raises domain errors,
  which `api/errors.py` answers. What two route modules need goes into
  `api/deps.py`.
- A name that the gateway and the worker (or the frontend) both read goes into
  `protocol.py`. A cookie, a header and a file of the account directory are
  such names.
- A page is a directory under `frontend/src` with its components and its
  rules. The fetchers it needs go in the `api/` module of their resource. It
  also gets a value of `Page` in `appbar/AppBar.tsx` and a line in the page
  switch of `App.tsx`. A rule that two pages need goes into `shared/`.
- A dialog is a file in the directory of its page. A dialog of the grid is a
  file in `grid/dialogs/` named for the dialog, with a case in the
  `GridDialog` or `NormalizeDialog` switch of `grid/dialogs/index.tsx`.
- A setting of the deployment is an environment variable read in `config.py`
  or `gateway/settings.py` and named in `example.env`. Where a setting of the
  user lives depends on what it belongs to. One that travels with the vault
  lives in the tool note as a section (like the generator's). One that belongs
  to the account on this machine lives in the SQLite settings table (like the
  auto-commit numbers and the date format). One that belongs to the browser
  lives in a cookie or localStorage (like the color mode).
- A test goes where [Tests](#tests) says. A helper that two test files share
  goes into a support module and never into a test.

## Changing the architecture

The layers, the packages and the contracts that hold them are the
maintainers' choice. They are never the side effect of a change. A change fits
into them, so a new module goes where [Where a new thing
goes](#where-a-new-thing-goes) says and a new import follows the layers.

When a change does not fit, it waits for the maintainers to change the
architecture. That change updates this document and the contract in one
commit with the reason beside the rule. It lands before the change that needs
it or together with it.

A contract is never loosened to let a change pass. That means no new ignore or
exception in a contract and no modules merged to break a cycle. It also means
no import moved into a function or under `TYPE_CHECKING` to hide it.

A review judges the code against this document, and code that breaks it is a
defect. Another way to organize the code is a proposal for the maintainers
rather than a defect. Nothing moves until they take it.

These are the contracts and what runs each:

- The `[tool.importlinter]` contracts in `backend/pyproject.toml`, run by
  `uv run --locked lint-imports`.
- `frontend/.dependency-cruiser.cjs`, run by `npm run lint:deps`.
- `frontend/knip.json`, run by `npm run lint:exports`.
- `backend/tests/repo/test_package_surfaces.py`, one rule for the `__init__`
  of every package.
- `backend/tests/repo/test_architecture_doc.py`, which checks that this
  document names every module and directory and that its paths exist.

## Who writes each kind of data

Each kind of data has one writer. Where another module writes it too, the
table says why.

| Data | Writer | Module | Exception, and why |
| --- | --- | --- | --- |
| Vault items | `WritePath.apply` under its lock, each kind through its function in `APPLY` | `writes/path.py`, `writes/items.py`, `writes/create.py`, `writes/attachments.py`, `writes/restore.py` | `ensure_tool_note` (`vault/tool_note.py`) creates the tool note at the unlock. No session exists yet then and the write path needs the session's backup |
| Folders | the folder writes under the same lock | `writes/folders.py` | None |
| The tool note's notes JSON | `_write_note`, for a section save and for the entries of a converted item | `writes/note.py` | `new_tool_note` (`vault/tool_note.py`) gives a new note its first notes at the unlock |
| The tool note's fields, the backup passwords | `set_backup_passwords` from the unlock in `session.py` and `new_tool_note` for a new note | `vault/tool_note.py` | The whole kind is outside the write path. It runs at the unlock before the backups are re-encrypted to a new master password |
| SQLite schema | `create_schema`, for a new file only | `db.py` | None. Nothing changes the tables of an existing file |
| SQLite `change_log` | `Database.record`, from `WritePath.apply` only | `writes/path.py` | `wipe_account_data` (`queue/wipe.py`) clears it for the account data deletion |
| SQLite `queue` | The intake stores the rows with `enqueue` and `store_refused_secret` or with `store_ui` in one transaction. `decide` writes every status, and `decide_all` writes those of a batch in one transaction. `dismiss` and `forget_failed_values` delete failed rows and their hidden values | `queue/intake.py`, `queue/entries.py` | `retry` (`queue/commit.py`) puts failed rows back to pending with their operations refreshed in one transaction (`reset_queue_entries`). `wipe_account_data` deletes every row except the pending MCP proposals kept on Write first |
| `tool_note_id` in SQLite `settings` | `ToolNoteId` | `vault/tool_note.py` | None |
| `auto_commit_after_seconds`, `auto_commit_max_entries` in SQLite `settings` | `AutoCommit.configure` | `queue/autocommit.py` | None |
| `hidden_identity` in SQLite `settings` | `HiddenValues` | `vault/hidden.py` | None |
| `mcp_token_last_used` in SQLite `settings` | `McpToken`, deleted with the token | `mcp/auth.py` | None |
| `date_format`, `hide_tool_note`, `rare_fields_max` in SQLite `settings` | `set_display` | `api/settings.py` | None |
| SQLite `backup_fingerprints` | `create_backup`, one row per backup | `backups/collect.py` | `BackupKeeper` (`backups/keeper.py`) forgets a row with its backup and `wipe_account_data` clears the table |
| Backups `*.zip.age` | `write_encrypted`, from `create_backup` and `reencrypt_all` | `backups/crypto.py` | `BackupKeeper` deletes them for the retention (`prune` in `backups/files.py`), for Delete and for the account data deletion (`wipe_account_data` in `queue/wipe.py`) |
| `mcp-token.sha256` | `McpToken.create` and `remove` | `mcp/auth.py` | None |
| `bw-login-state` | `LoginStateFile.record`, through `BwLogin.note` | `login_state.py`, `bw/login.py` | None |
| The delete marker `app/delete-requested` | `write_delete_marker`, for Delete my account | `api/account.py` | At a worker's start `drop_stale_delete_marker` deletes one that an earlier worker left. The gateway acts on it when the worker exits (`Gateway.delete_on_request`) |
| The pending marker `.pending` | `Accounts.create` | `gateway/accounts.py` | `WorkerLifetime.unlocked` (`worker.py`) deletes it after the first unlock, which only the worker sees |
| The account directories | `Accounts.create` (under a temporary name that it renames into place last) as well as `Accounts.delete` and `sweep_pending` | `gateway/accounts.py` | None |
| An account's directory under the backups root | `BackupsRoot.make` and `remove` | `gateway/backups.py` | None |
| `stats.db`, the usage stats | `Recorder.write` in batches from what `StatsMiddleware` notes and from the samples of `Recorder.capacity`, both through `stats/db.py` | `stats/recorder.py`, `gateway/stats.py` | `open_stats` (`stats/db.py`) runs the migrations at the gateway's start and `roll_up` runs the retention |
| `worker.log` | `Accounts.create` makes it and `open_log` opens it for the worker without following a link or blocking | `gateway/accounts.py`, `gateway/workers.py` | None |
| The CLI state `bw/` of an account | bw itself, run by `BwLogin` and `BwServeProcess` as well as `run_cli` | `bw/` | None |
| The vault in RAM | `VaultCache.load` | `vault/cache.py` | `replace_item` puts a tool note just written in at once (from `WritePath` and `set_backup_passwords`), so a save reads as saved before the next load |
| The chain of own writes | `OwnWrites`, recorded by the write path's PUTs | `vault/own_writes.py` | None |

## The container

`compose.yaml` runs the image of the `Dockerfile` target `app` as the
container `bwtidy`. `BWTIDY_IMAGE` and `BWTIDY_VERSION` name the image in the
`.env` made from `example.env`. The published image is
`docker.io/tnek213/bwtidy`, which the GitHub Actions workflow "Image" builds
and pushes on every push to main and every release. `compose.build.yaml`
builds it from the checkout as `bwtidy:local` instead, and [Run
bwtidy](how-to/run.md) says how to run it.

The container has a read-only root file system with a tmpfs `/tmp`. It has
the capabilities the gateway needs to start workers as other uids and a health
check on `/api/health`. Its port 8000 is published on the host's `127.0.0.1`
(8765 by default). It mounts the volume `bwtidy-accounts` at `/data/accounts`
and the backups at `/backups`. The backups are the volume `bwtidy-backups` or
the host directory that `BWTIDY_BACKUPS` names, with
`BWTIDY_BACKUPS_ROOT=/backups`. It mounts the volume `bwtidy-stats` at
`/stats` too, with `BWTIDY_STATS_DB=/stats/stats.db`. A stop gets 150
seconds. `STOP_TIMEOUT` in `gateway/workers.py` plus the gateway's request
drain must stay below that.

The gateway runs as root and starts each worker as the account's uid. Without
the worker settings `main.py` runs as a single-user backend on a TCP port,
which only the integration tests use.

The `app` stage holds the backend's source with its installed dependencies but
no tests. It also holds the built frontend and bw with
`docker/unref-share-timers.js`, which makes a bw command exit as soon as it
has answered. `docs/restore.md` sits at `/app/docs/restore.md` because every
backup holds it as its RESTORE.md. The docs site sits at `/app/site`.

That bw is the open source build of the Bitwarden CLI. The
`bitwarden-cli-source` stage builds it from the commit of Bitwarden's source
that `docker/bitwarden-cli/source.json` pins. The `bitwarden-cli` stage puts
that build in place of the npm package's own and installs the packages it
brings along from `docker/bitwarden-cli/package-lock.json`.

The `docs` stage builds that site with `scripts/docs-site build` from
`zensical.toml` and `docs/` as well as `overrides/main.html`, which shows the
beta disclaimer as a banner on every page. It uses the Zensical of the
backend's dependency group `docs`, which stays out of the app's venv. The
workflow `.github/workflows/docs.yml` builds the same site and publishes it on
GitHub Pages. The user docs follow Diátaxis (<https://diataxis.fr>) with one
folder per kind of page under `docs/`. AGENTS.md (Writing docs) holds the
rules for writing them.

The `dev` stage is the app container of the dev stack (`compose.dev.yaml`). It
runs the tests from a mount of `backend/` and mounts the worktree's `docs/` at
`/app/docs` read-only. Its docs site is the one its image was built with, so
`scripts/docs-site serve` previews a change of the docs.

## Backend packages

Under `backend/src/bwtidy` each module gets one line, in the order of the
layers. A package's `__init__.py` names its theme and takes one of three
forms:

- It re-exports exactly what modules outside the package import from it.
  They import from the package (`from bwtidy.writes import WritePath`) and
  never from a module of it. This holds for `bw/` and `vault/` and for
  `backups/`, `writes/` and `mcp/`.
- It re-exports nothing and the package's modules are imported by name
  (`from bwtidy.queue.review import ReviewQueue`). This holds for `items/` and
  `queue/` and for `api/` and `gateway/`.
- `sections/` lies in between. Its `__init__.py` defines `Section`,
  `SECTIONS` and `SectionChange` and re-exports what reads and writes the
  notes JSON as a whole. Its section modules are imported by name.

No module imports a name that starts with an underscore from another module.
`backend/tests/repo/test_package_surfaces.py` checks all of this.

The basics, with the two above the others first:

- `config.py` the worker's settings from the environment (`Settings`), with the
  session timeouts (`Policy`) and the backup retention (`Retention`).
- `login_state.py` the file `bw-login-state` that tells the gateway whether the
  CLI is logged in.
- `logs.py` the log format, with a time on every line and other libraries from
  WARNING up.
- `timing.py` the step durations of a slow operation, for one INFO line.
- `version.py` `semver`, the version as semver.org spells it. It uses the stdlib
  only so that `scripts/devlib/versions.py` can load it by path.
- `validation.py` the answer to a refused request body (never the body), and the
  one text of a validation error.
- `passphrase.py` age passphrase encryption with the master password and the
  password hash, the only caller of scrypt.
- `mcp_token.py` the MCP token's record and its checks, which the gateway and
  the worker share.
- `protocol.py` the strings the gateway, the worker and the frontend agree on.
- `db.py` `Database`, the SQLite file at its one schema. It holds the change log
  and the queue as well as the settings and the backup fingerprints.

The usage stats in `stats/`, beside `config.py` and kept only with
`BWTIDY_STATS=1`:

- `stats/db.py` the gateway's stats file with `MIGRATIONS` as well as the writes
  and the retention.
- `stats/parse.py` what the gateway reads of a request and checks against the
  worker's names. That is the route templates and the JSON-RPC methods and tool
  names as well as the outcomes and the error report.
- `stats/recorder.py` `Recorder`, which holds what was seen in RAM and writes it
  in batches off the request path. It also takes the capacity samples.
- `stats/report.py` the worker's side with its names at `/stats/names` and the
  error header of its 500.
- `stats/summary.py` `summary` (the one dict that a page or `bwtidy stats`
  shows) and the top errors.

Talking to bw in `bw/`:

- `bw/client.py` `BwClient`, the async client of bw serve's API.
- `bw/serve.py` `BwServeProcess` starts bw serve and restarts it when it exits.
- `bw/process.py` `run_cli` (the one way to run a bw CLI command) and `stop_bw`.
- `bw/login.py` `BwLogin`, the CLI login in a pseudo-terminal for the web UI and
  the CLI's logout.
- `bw/revision.py` `same_revision`, the one comparison of two revisionDates.

The vocabulary in `items/`:

- `items/coordinates.py` where a value lives in an item (its grid column), and `is_secret`.
- `items/normalize.py` renaming fields and merging columns and items, with the
  near-duplicate hints.
- `items/patterns.py` the datatype pattern engine, bounded for untrusted patterns.
- `items/ssh_keys.py` an SSH key's public key and fingerprint from its private key.

The tool note's sections in `sections/`:

- `sections/__init__.py` `SECTIONS` (each section's rules by name) and `SectionChange`.
- `sections/notes.py` the notes JSON and `SectionRules`, what every section
  module builds on.
- `sections/templates.py` the templates section with its model, its parser and
  its changes.
- `sections/datatypes.py` the datatypes section and the check that no datatype a
  template uses is deleted.
- `sections/generator.py` the generator section and the query of bw's generator
  for a set of options.
- `sections/to_fill.py` the toFill section as the note stores it, and its changes.
- `sections/note.py` the note section, the name the tool note is expected to have.
- `sections/matching.py` the scoring and matching of an item against the
  templates and datatypes.

The operations:

- `operations.py` the operation models and what each touches.

The account's state in RAM in `vault/`:

- `vault/cache.py` `VaultCache`, the vault in RAM.
- `vault/own_writes.py` `OwnWrites`, the chain of bwtidy's own writes this session.
- `vault/tool_note.py` finds and creates the tool note, keeps its id
  (`ToolNoteId`) and its backup password fields.
- `vault/hidden.py` `HiddenValues`, the age identity that encrypts queued secret
  values.
- `vault/formats.py` the cached note's templates and datatypes and `Formats`,
  how the cached values match them.
- `vault/statistics.py` the counters of how hard the pattern checks work, kept
  by `Formats`.

Backups in `backups/`:

- `backups/files.py` the backup files with their listing and names, the
  retention rule and the fingerprint.
- `backups/crypto.py` the age encryption of a backup file and the re-encryption
  after a new master password.
- `backups/collect.py` `create_backup`, which checks the exports and listings
  through bw and puts them into one zip with its RESTORE.md.
- `backups/reader.py` `BackupReader`, a decrypted backup in RAM.
- `backups/keeper.py` `BackupKeeper` with the backup of a write, the background
  backup and the retention.

The session:

- `session.py` `SessionManager` with the unlock and the session token as well as
  the timeouts and the end.
- `worker.py` `WorkerLifetime` with the exit after the session and the pending marker.

The write path in `writes/`:

- `writes/path.py` `WritePath.apply` (the batch driver) and `WriteContext`, what
  the modules of the kinds call on it. That covers the vault in RAM and bw serve
  as well as the backups and the shared steps of a write. It also holds `APPLY`
  with the function of each kind of operation typed to its class.
- `writes/check.py` `validate`, the check of a batch before anything is written.
- `writes/rules.py` the secret rules every write checks.
- `writes/planners.py` what an operation makes of an item, without writing.
- `writes/saves.py` a section save with its check and the notes as its write
  leaves them.
- `writes/restore_plan.py` what a restoreItems does per item of a change log row.
- `writes/items.py` the writes of one item or a few. They change a leaf or the
  URIs and set the collections. They move, rename and merge items and handle the
  trash.
- `writes/create.py` createItem and the conversions that create a new item and
  trash the old one.
- `writes/folders.py` the folder of a move or a new item, and the folder kinds.
- `writes/attachments.py` renameAttachment, an upload and a delete that leave
  one copy.
- `writes/note.py` `_write_note`, the one write of the tool note's notes.
- `writes/restore.py` restoreItems, step by step as the plan says.

The review queue in `queue/`:

- `queue/review.py` `ReviewQueue`, the facade the doors and `main.py` call.
- `queue/state.py` `QueueState` (what the modules share) and an entry's stored form.
- `queue/intake.py` what comes in, with the checks on arrival and the stores.
- `queue/commit.py` `approve` and `retry`, which send a batch through the write
  path and apply or blame each entry.
- `queue/entries.py` the rows with the one status write, the listing and the
  decisions without a write.
- `queue/agent.py` what the agent reads of its own entries.
- `queue/sections.py` a section as the saves under way leave it.
- `queue/wipe.py` the account data deletion and the backups the queue still needs.
- `queue/view.py` an entry's summary and its masked diff.
- `queue/autocommit.py` `AutoCommit`, the commit of the UI's entries on time or
  count.
- `queue/to_fill.py` `ToFill`, the list of secret fields still to fill in.

The agent's door in `mcp/`:

- `mcp/server.py` `BwtidyMcp`, which gates every tool. Its `create_mcp_server`
  registers `TOOLS` and hands each tool its interface, which is `AgentVault` for
  the read and propose tools and `SessionActions` for the session tools.
- `mcp/vault.py` `AgentVault`, the vault as the agent sees it. It says what each
  tool may read and puts its proposals into the queue.
- `mcp/texts.py` what the agent reads, which is the instructions and each tool's
  description.
- `mcp/read_tools.py` the read tools.
- `mcp/propose_tools.py` the propose tools, one operation each.
- `mcp/session_tools.py` the two tools that end the session after the answer and
  `SessionActions` (all they may do).
- `mcp/auth.py` `McpToken` (the worker's token file) and `RequireMcpToken` in
  front of `/mcp`.

The browser's door in `api/`:

- `api/app_state.py` `AppState`, the parts that every route reaches through
  `app_state(request)`. Among them are the settings and the database with the
  vault in RAM. The session and the write path are there too along with the
  keeper and the queue.
- `api/deps.py` what the route modules share, such as the session dependency and
  the request header check. It also gives the session's state as the login
  routes answer it and the tool note.
- `api/errors.py` the HTTP answer of each domain error.
- `api/sections.py` what the section routes share, which is the queued changes
  and the current view.
- `api/session.py` unlock, heartbeat and log out.
- `api/bwlogin.py` the Bitwarden CLI login from the web UI.
- `api/grid.py` the vault as a grid, and the one route that answers a secret.
- `api/queue.py` the queue with the UI edits, the decisions and the auto-commit.
- `api/operations.py` `POST /api/operations` straight to the write path for
  scripts and test setup. It also answers the change log and restore plans, each
  from the parts that `AppState` holds.
- `api/backups.py` listing and taking backups as well as downloading and
  deleting them.
- `api/templates.py` the templates section and the matches, and the tool note's
  rename back and keep name straight to the write path.
- `api/datatypes.py` the datatypes section and the live pattern checks.
- `api/generator.py` the generator settings and an example value.
- `api/statistics.py` the numbers of the Statistics page.
- `api/mcp.py` the MCP switch and the MCP token.
- `api/settings.py` the display settings.
- `api/account.py` deleting the account's data, or the whole account.

The composition root:

- `main.py` `create_app` and the `bwtidy` entry point with its command `bwtidy stats`.

The gateway in `gateway/`:

- `gateway/app.py` the gateway's routes, the account state machine and the
  static files.
- `gateway/proxy.py` the proxy to the workers, with the routing by cookie and
  token and the headers that pass.
- `gateway/settings.py` the gateway's settings from the environment.
- `gateway/accounts.py` account keys, directories and uids.
- `gateway/backups.py` `BackupsRoot`, each account's directory under the backups
  root.
- `gateway/workers.py` start, watch and stop one worker per account.
- `gateway/stats.py` `StatsMiddleware` around the proxy, which notes each
  request that reaches a worker for the recorder and learns the workers' names.

## Frontend

The frontend under `frontend/src` uses React, MUI and AG Grid with MUI X's
date picker for date fields. `main.tsx` mounts `App.tsx`. Four directories
hold what the pages share and the others are the pages and the app bar. What
each may import is in [Layers and their rules](#layers-and-their-rules).

- `api/` the backend's resources, one file each with its fetchers and React
  Query keys and JSON types. It also holds `apiFetch` in `api/api.ts` and the
  operation builders in `api/operations.ts`. The rules of a page live in the
  page.
- `shared/` the helpers and small components that belong to no page:
    - the coordinates (a mirror of `items/coordinates.py`)
    - the account's lifecycle (reopening, switching and the other tabs) and the
      lock
    - how a cell value shows
    - the queue's failure text
    - the generator's limits and checks
    - the item types and template coordinates that the grid and the Types page share
    - the date and time format and the date field
    - the form dialog and the generator form
    - the password managers' opt-out
- `commit/` the commit tracker, which is `AutoCommitProvider` with its context
  and the unread marks.
- `theme/` the color mode and the layout (Normal or Compact) as well as `Prose`,
  the paragraph with balanced lines.
- `about/` the About page with the version and the license as well as the
  credits and the third-party packages.
- `appbar/` the app bar. It holds the page links and Commit now as well as the
  MCP switch and the color mode. Log out and the more menu sit there too.
- `backups/` the alert about backups to re-encrypt after a new master password.
- `changelog/` the Change log page with its filters and restore as well as
  delete backup and the diff report.
- `grid/` the Vault grid page with its cells, toolbar and dialogs. Each dialog
  is one file in `grid/dialogs/`.
- `queue/` the Queue page.
- `session/` the account form and the Bitwarden login as well as the unlock and
  the heartbeat.
- `settings/` the Settings page with the display settings and the default
  generator as well as deleting the data.
- `statistics/` the Statistics page.
- `templates/` the Types page with the templates and the datatypes.

## How a write flows

A grid edit travels from the cell to a reload in the browser. The picture
shows who hands what to whom, and the text below walks through the same
steps.

```mermaid alt="A grid edit is queued, approved by the auto-commit and written by the write path one operation at a time"
%%{init: {"htmlLabels": false, "sequence": {"actorMargin": 12, "width": 96, "mirrorActors": false}}}%%
sequenceDiagram
  participant Grid as Browser
  participant Queue as ReviewQueue
  participant DB as SQLite
  participant Path as WritePath
  participant Keeper as BackupKeeper
  participant Bw as bw serve
  Grid->>Queue: POST /api/queue
  Queue->>Queue: validate
  Queue->>DB: pending entries
  Note over Queue: auto-commit
  Queue->>DB: approved
  Queue->>Path: one batch
  Path->>Bw: sync and load
  Path->>Path: validate again
  Path->>Keeper: backup
  Path->>Bw: sync and load again
  loop every operation
    Path->>Bw: GET item
    Path->>Path: check
    Path->>Bw: PUT item
    Path->>DB: change log row
    Path->>Queue: on_applied
    Queue->>DB: applied
  end
  Path->>Bw: reload
  Path->>Keeper: background backup
  Note over Queue: settle, count
  Grid->>Queue: auto-commit status
  Queue-->>Grid: commitCount
  Grid->>Grid: refetch
```

AG Grid runs with `readOnlyEdit`, so an edit in a cell becomes an operation
(`grid/useCellEvents.ts` with the builders of `api/operations.ts`).
`grid/writer.ts` posts it to `POST /api/queue` (`api/queue.ts`). The route
(`api/queue.py`) parses the operations and hands them to `ReviewQueue`.

On arrival `queue/intake.py` checks every operation of the batch with
`validate` (`writes/check.py`) on the vault in RAM before it stores any. A
refusal answers 409 or 422 and stores nothing. Otherwise the hidden identity
(`vault/hidden.py`) encrypts each secret value and the batch is stored as
pending entries in one transaction (`db.py`). The grid shows the queued values
as an overlay (`grid/queued.ts`).

The auto-commit (`queue/autocommit.py`) approves every pending UI entry once
the oldest is old enough or enough are pending. It runs one commit at a time.
Then `approve` (`queue/commit.py`) marks the entries approved with the diff they
show (`queue/view.py`). It decrypts their hidden values into RAM and hands the
operations to the write path as one batch.

Under its lock the write path (`writes/path.py`) syncs bw serve and loads the
vault (`vault/cache.py`) before it runs `validate` again. Then it takes a
backup from `backups/keeper.py`. That is the newest backup when it holds the
vault as it is or else a new one (`backups/collect.py`). Delete and the
retention keep it until the batch ends. One more sync and load follow, and the
check runs again when the vault changed.

The module of its kind in `writes/` then writes one operation at a time,
through the steps that `WriteContext` names. It GETs the item and checks it
against the chain of own writes (`vault/own_writes.py`) and against what the
grid showed. It checks the secret rules on it (`writes/rules.py`) and changes
only the touched leaves before it PUTs the whole item. Each operation then
gets a change log row (`db.py`) and its entry is marked applied at once
(`queue/commit.py`).

At the end of the batch `writes/path.py` reloads the vault and starts a
background backup, even when a write failed. Then `queue/commit.py` settles
the batch. A conflict or an invalid operation that the check found fails the
entries it blames and the rest is tried again. A write that fails midway fails
its entry and puts the later ones back to pending. The commit is counted.

In the browser `commit/AutoCommitProvider.tsx` polls the auto-commit's status
(`GET /api/queue/auto-commit`). Once the commit count has changed and no
commit runs, it refetches the grid and the other pages' data and reports the
failed entries.

The other ways in:

- A tool note section save covers the templates and the datatypes as well as
  the generator and the to-fill list. It is a UI entry of single changes
  (`api/sections.py` and `queue/intake.py`) whose commit the route starts at
  once. Its write applies the changes to the note as it is then
  (`writes/saves.py` and `writes/note.py`), so two saves never undo each
  other. Every read of a section under way folds the pending saves over the
  cached note (`queue/sections.py`).
- An MCP proposal comes from a propose tool (`mcp/propose_tools.py`). The tool
  builds the operation and its summary and queues it through `AgentVault`
  with the source mcp. The check on arrival refuses any secret value. The
  auto-commit never takes it, so the user approves it on the Queue page
  through the same approve.
- The tool note's rename back and keep name (`POST
  /api/tool-note/rename-back` and `POST /api/tool-note/keep-name` in
  `api/templates.py`) call the write path directly without the queue.
- `POST /api/operations` (`api/operations.py`) calls the write path directly
  without the queue, for scripts and test setup.

## Where state lives

- Worker RAM goes with the session. It holds the master password with its
  hash and the session key (`session.py`) as well as the vault and the chain
  of own writes (`vault/`). It also holds the hidden identity and the last
  decrypted backup. When each UI entry was queued is kept there too along with
  the format results and the pattern counters.
- Gateway RAM holds the routing cookie's account and the running workers. A
  restart forgets both, and the browser logs in again.
- The usage stats exist only with `BWTIDY_STATS=1`. The gateway keeps them in
  `stats.db` (`stats/db.py`), which lies next to the accounts directory
  unless `BWTIDY_STATS_DB` names another place. The file survives restarts
  and updates through its migrations.
- SQLite in `app/bwtidy.sqlite` (`db.py`) survives a restart and a deploy. At
  a worker's start the queue fails what a killed commit left approved and
  deletes the hidden values of failed entries (`queue/entries.py`). Pending UI
  entries are due at the next unlock.
- The tool note travels with the vault. Its notes JSON holds the sections
  `templates` and `datatypes` as well as `generator` and `toFill` (one module
  each in `sections/`). Its hidden fields hold the backups' passwords.
- The account directory `/data/accounts/HOST/EMAIL/` ([spec.md, Account
  directory](spec.md#account-directory)) is made at the account's first
  Bitwarden login. It holds `app/` with the SQLite file and `mcp-token.sha256`
  as well as `bw-login-state` and the delete marker. Next to it lie the CLI
  state `bw/` and the sockets in `run/` as well as `home/` and `worker.log`.
- The backups `*.zip.age` live under the backups root in
  `/backups/HOST/EMAIL/` with `compose.yaml`. Otherwise they live in
  `app/backups/` of the account directory, or where `BWTIDY_BACKUPS_DIR`
  points.
- The browser holds state of four kinds:
    - React Query holds the server's data and two entries of the page's own.
      These are the account (`api/account.ts`) and the note above the
      unlock form (`shared/session.ts`).
    - The context of `commit/autoCommit.ts` holds the settling entries and
      the failures as well as the conflict marks.
    - localStorage holds per account the form's last server and email
      (`session/account.ts`), the account in use (`api/account.ts`) and the
      last log out (`shared/account.ts`). It also holds the queue entries
      seen (`commit/unread.ts`) and the failed entries reported
      (`commit/autoCommit.ts`). Per browser it holds the layout
      (`theme/layout.tsx`).
    - The cookies are the color mode (`theme/colorMode.tsx`) and
      `bwtidy_account` of the gateway as well as `bwtidy_session` of the
      worker. The tabs of a profile share them, so a tab reloads when another
      changes the account or logs out.

    Drafts and typed secrets stay in their component.

## Where the secret rules are enforced

The rules are in [spec.md (Secret values)](spec.md#secret-values). These
places keep them:

- `items/coordinates.py` `is_secret` decides what is secret, and every check
  below asks it.
- `api/grid.py` masks secret cells. Its `secret` route is the only one that
  answers a secret.
- `mcp/vault.py` gives the agent column names and projections without secret
  values. It never hands over an item of the cache and leaves the tool note
  out. `mcp/server.py` answers only while a session is unlocked and MCP is on,
  and it reports an error by its type only.
- `queue/intake.py` refuses an agent's secret and stores a silent refusal
  without its value. It encrypts every queued secret through
  `vault/hidden.py`. `queue/agent.py` reports a failure to the agent as one
  fixed word.
- `queue/view.py` masks secrets in every diff.
- `writes/rules.py` keeps a secret out of plain columns. It checks in
  `validate` in batch order and on a new item, and at write time on the item
  each write GETs.
- `writes/path.py` passes on only the text of `BwError` and `ValueError`.
- `db.py` keeps kinds, ids and coordinates in the change log but never values
  or error messages.
- `stats/parse.py` keeps of a request only the names that the worker gives
  (`stats/report.py`). It never keeps a path or an id and never an argument
  or a message.
- `validation.py` never quotes a refused value in a validation error in
  either app. `logs.py` keeps other libraries at WARNING.
- `bw/login.py` masks the password and the session key in the CLI's output.
- `backups/collect.py` passes `BW_SESSION` in the environment, and no export
  touches the disk in plain.
- Tests compare a secret with the value they expect and assert only the bool
  (`secret_is` in `tests/e2e/support/api.py`).

## Tests

- Unit tests (`scripts/ci`) live in `backend/tests/unit` and cover logic and
  routes. They mirror the packages and are named after the module, like
  `tests/unit/writes/test_path.py` for `writes/path.py`. They use
  `tests/support/fake_bw.py` in place of bw serve and build the app from one
  `AppEnv` (`tests/support/app_env.py`).
- Frontend tests (`scripts/ci`) are vitest's `*.test.tsx` next to the code.
  The grid's builder and renderer are in `grid/testSupport.tsx`.
- Script tests (`scripts/ci`) live in `backend/tests/scripts` and cover what
  the files in `scripts/` do. They load a script by name through
  `load_script` (`tests/scripts/conftest.py`).
- Repo tests (`scripts/ci`) live in `backend/tests/repo` and check the facts
  that span files. They check this document
  (`tests/repo/test_architecture_doc.py`) and the two versions
  (`tests/repo/test_versions.py`) as well as the third-party list
  (`tests/repo/test_third_party.py`) and the test tree
  (`tests/repo/test_test_tree.py`). The test tree check holds four rules:
    - No test imports a test or a conftest.
    - Every test file is in `unit` or `scripts` or `repo` or `integration`
      or `e2e`.
    - Every unit test file is named after a module.
    - Every folder of `unit` is a package.
- Support lives in `backend/tests/support` and holds what several test files
  share, imported as `tests.support.NAME`. That is the fake bw and the sample
  vault as well as the test environments and the docs shots.
- The integration tests share the package `backend/tests/integration/support`
  instead. It holds the bw CLI and the dev credentials
  (`tests/integration/support/cli.py`) and the in-process app with the items
  made elsewhere (`tests/integration/support/app.py`). It also holds the
  `Vault` and the comparisons (`tests/integration/support/vault.py`) and the
  restore round trip (`tests/integration/support/restorer.py`). Only the
  fixtures are in `tests/integration/conftest.py`.
- Integration tests (`scripts/integration`) live in
  `backend/tests/integration`. They run the single-user backend against
  Vaultwarden and real bw for what depends on bw's real answers. That means
  the backups and the writes as well as the login.
- End-to-end tests (`scripts/e2e`) live in `backend/tests/e2e` and drive the
  UI with Playwright through the gateway. `tests/e2e/conftest.py` holds the
  fixtures and the package `backend/tests/e2e/support` holds the helpers, one
  module per theme.
- Docs screenshots (`scripts/docs-shots`) come from `backend/tests/e2e/docs`,
  e2e tests that write the images of `docs/images`.

`scripts/ci` also checks the generated files (`scripts/third-party` and
`scripts/mcp-reference`) and the docs. `dev/testing.md` says how to run and
write each kind of test. The dev stack is in `dev/dev-vault.md` and the
commands for one file are in `AGENTS.md`.
