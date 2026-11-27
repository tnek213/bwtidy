# Testing

The suites, how to run one file of each, how to write a test of each kind,
and what `scripts/ci` checks besides the tests. The dev stack the integration
and e2e tests run on is in [The dev vault](dev-vault.md).

## The suites

| Suite        | Where                                     | Runs                                              |
| ------------ | ----------------------------------------- | ------------------------------------------------- |
| Unit         | `backend/tests/unit`, mirrored on the packages of `bwtidy` | `scripts/ci`, on the host, with fakes  |
| Scripts      | `backend/tests/scripts`, the scripts of `scripts/` | `scripts/ci`                             |
| Repo         | `backend/tests/repo`, checks on the repo (the architecture doc, the test tree, versions, third-party) | `scripts/ci` |
| Frontend     | `frontend/src`, `*.test.tsx` next to the code | `scripts/ci`, vitest                          |
| Integration  | `backend/tests/integration`               | `scripts/integration`, in the app container      |
| End-to-end   | `backend/tests/e2e`                       | `scripts/e2e`, Playwright on the host            |
| Docs         | `backend/tests/e2e/docs`                  | `scripts/docs-shots`                              |
| Demo         | `demo/tests`, the demo's own project      | `scripts/ci`, with fakes                          |
| Demo stack   | `demo/tests/stack`                        | `scripts/demo-stack test`, against the running demo stack |

One file of each:

```sh
cd backend && uv run --locked pytest tests/unit/queue/test_commit.py
cd backend && uv run --locked pytest tests/scripts/test_docs_lint.py
cd backend && uv run --locked pytest tests/repo/test_test_tree.py
cd frontend && npx vitest run src/App.test.tsx
scripts/integration tests/integration/test_seed.py
scripts/e2e tests/e2e/test_dismiss.py
scripts/docs-shots -k more_menu
cd demo && uv run --locked pytest tests/test_gateway.py
scripts/demo-stack test -k edge
```

`-k NAME` runs one test of any of them. While you change code run only what
fails (a file, `-k NAME`, `-x`, `--lf`), and the whole suite once at the end.

The helpers that several files share go into a support module, never into a
test file: no test imports from another (`tests/repo/test_test_tree.py`).
`backend/tests/support/` is shared by every suite, `backend/tests/e2e/support/`
by the e2e and docs tests.

## Unit, scripts and repo tests

A unit test goes into the file of `backend/tests/unit` named after the
module it covers, in the folder that mirrors its package
(`tests/unit/writes/test_path.py` for `bwtidy/writes/path.py`,
`tests/unit/test_session.py` for `bwtidy/session.py`), a test of a script
into `tests/scripts`, a check on the repo's files into `tests/repo`
(`tests/repo/test_test_tree.py` checks the names). The folders of
`tests/unit` are packages (an empty `__init__.py`), so two of them can hold a
file of the same name. A fixture that the files of one folder share goes into
its `conftest.py`, a helper into a module of `backend/tests/support/`. The
modules of `backend/tests/support/` that help them:

- `fake_bw.py`, an in-memory `bw serve`, and `fake_bw_script.py`, a fake `bw`
  executable, so no unit test runs the real CLI.
- `app_env.py`, the app every unit test builds: `AppEnv(data_dir)` puts the
  fake bw serve with the sample vault, the settings, the app and its
  `TestClient` together, `login()` and `settle()` beside them and the app's
  parts (`queue`, `writes`, `cache`, `db`, `keeper`, `session`, `mcp`) as
  attributes. Its arguments are what tests differ in (another fake vault,
  `extra` items, a clock, the backup `tests/conftest.py` stubs in place of
  the fake one, any setting),
  `with env:` starts it without a login and `env.unlock()` logs in. The
  fixture `env` of `backend/tests/unit/conftest.py` is one logged in.
- `gateway_env.py`, the gateway with fake workers and the helpers of the
  gateway's tests, and `login_env.py`, a fake `bw` executable for the bw
  login and `CliEnv`, the app that runs it, with the helpers of the worker
  mode's tests.
- `sample_vault.py`, items of every type with `SECRETS`, every secret value
  in them, so a test can check none leaks.
- `mcp_helpers.py`, `queue_helpers.py`, `writes_helpers.py`,
  `sections_helpers.py`, `section_changes.py`, `datatypes_helpers.py`,
  `normalize_data.py` and `ssh_keys.py` for the tests of their theme.
- `quiet.py`, see [Vault values in a failure](#vault-values-in-a-failure).
- `hooks.py`, hooks that do nothing, for a test that builds an object such as
  `VaultCache` or `BwServeProcess` without the app around it.
- `git_repo.py`, a clone with a bare origin and `commit`, for the tests of a
  script that reads git (the pre-push hook's).
- `restore_doc.py`, the Python of `docs/restore.md` (its program `attach.py`
  and its `python3 -c` commands), which `tests/repo/test_restore_doc.py`
  parses as Python 3.8 and the integration test `test_backup_import.py` runs.

The tests of `tests/scripts` load a script by name with the fixture
`load_script` (`load_script("dev-vault")`), and `backend/tests/conftest.py`
puts `scripts/` on the import path so they import `devlib` too.

The tests of `scripts/docs-site` (`tests/scripts/test_docs_site_script.py`)
convert a diagram with the real resvg, so they need one of the fonts that
[Releasing](release.md#diagrams) lists, as a docs build does. Without one
they fail with the build's message that no installed font draws the text.
On Debian or Ubuntu `fonts-dejavu-core` is enough, which the CI workflow
installs before `scripts/ci`. Releasing names the packages of other systems.

### Workers and timeouts

`scripts/ci` runs the backend tests of `tests/unit`, `tests/scripts` and
`tests/repo` in parallel with pytest-xdist (`-n WORKERS --dist worksteal`):
one worker per core, half the cores when the 1 minute load average is above
half the cores (another `scripts/ci` is running, say). A `PYTEST_WORKERS` you
set yourself wins (`PYTEST_WORKERS=4 scripts/ci`). A plain
`uv run --locked pytest tests/unit` runs one test at a time, `-n auto` runs it like
`scripts/ci`. With xdist a test must not share a fixed port, file,
environment variable or global with another test, use `tmp_path` and
`monkeypatch`. To run one file without xdist when its output or a debugger
matters, pass `-n 0` (or `-p no:xdist` to leave the plugin out):

```sh
cd backend && uv run --locked pytest tests/unit/queue/test_commit.py -n 0
```

One age passphrase pass (scrypt) takes over a second, so in the unit tests
`bwtidy/passphrase.py` is a fast stand-in (the autouse fixture
`scrypt_stand_in` in `backend/tests/conftest.py`, the functions in
`backend/tests/support/passphrase_stand_in.py`): a magic prefix, a hash of
the password and the plaintext, the plaintext. It raises `pyrage.DecryptError`
for a wrong password or damaged bytes like age. A test that needs real age
files carries the marker `real_scrypt` (`pytestmark = pytest.mark.real_scrypt`
for a whole file): one that checks the age format, decrypts with pyrage or the
age CLI, reads a file written by real age, or tests the re-encryption with a
new master password. The integration and e2e tests always use real age.

pytest-testmon is in the dev group for local runs only. `uv run --locked pytest
--testmon` runs only the tests that the changed code reaches (the first run
records which code each test runs in `backend/.testmondata`, later runs pick
from it). `scripts/ci` never uses it, a landing runs every test.

The backend gateway tests wait up to 60 s for a worker to exit.

## Frontend tests

A frontend test is a `*.test.tsx` next to the code it covers, and takes its
render and fetch helpers from `src/test-utils.tsx`.

Frontend tests time out at 10 s (`testTimeout` in `frontend/vite.config.ts`),
Testing Library's `findBy` and `waitFor` at 5 s. A test that waits on a poll
or a delay (the auto-commit's 1 s poll, the Bitwarden login's 500 ms, a
debounced check) runs on `vi.useFakeTimers({ shouldAdvanceTime: true })` and
moves the clock with `advanceClock` or `waitForPolls` from
`src/test-utils.tsx` instead of waiting in real time. Both move it outside
`act` and settle in `act` after, because AG Grid waits for each header it
mounts on zero delay timeouts. Inside `act` that wait spins on the fake clock
for a second of real time. `scripts/ci` caps vitest's workers: when the 1
minute load average is above half the cores, it sets `VITEST_MAX_WORKERS` to
half the cores, else vitest uses all cores but one. A `VITEST_MAX_WORKERS` you
set yourself wins.

## Vault values in a failure

A test that compares a vault value, a password or a console with a text uses
`assert_absent(value, text, what)` or `assert_present(value, text, what)` from
`backend/tests/support/quiet.py`, never a plain `assert value not in text`:
pytest prints both operands of a failed assert, the helpers fail with
"WHAT holds the value" or "WHAT lacks the value". A secret never stands in a
plain `assert`, not in `==`, `in` or a call like `same_secret(a, b)` either:
compare into a bool first and assert the bool, or use `assert_absent` and
`assert_present`. A timeout in a test that reads a console names the time and
`last_prompt(console, prompts)`, a prompt the test knows, never the console. `BwCli.run` and the seeding in
`scripts/devlib/seed.py` fail with bw's stderr only, or with the exit code
when stderr is empty.

## Integration tests

```sh
scripts/integration                # backend/tests/integration on three stacks, file by file
scripts/integration --stacks 2     # on two stacks
scripts/integration -k trash       # extra arguments go to pytest, -k runs on one stack
scripts/integration --help         # the usage, starts no stack
```

`scripts/integration tests/integration/test_x.py` runs only that file (a
test path, also with `backend/` in front or as a node id with `::`, replaces
the whole directory), `-k NAME` runs one test. Both run one pytest in the app
container of one stack (`BWTIDY_STACK`, 1 by default), as does a list of files
with `--stacks 1`. One pytest ends with pytest's own summary line (for example
`162 passed in 5183.00s`).

The whole suite runs on several stacks (see
[Several stacks](dev-vault.md#several-stacks)): a scheduler in the script
hands out one file per stack at a time, the longest first by `DURATIONS` in
the script (a file missing there goes last, add it), and each run is one
`scripts/integration FILE` with the other arguments. The `--root` run of
`test_gateway.py` goes to a stack that has had a plain run, since a root run
as a stack's first one leaves a root-owned venv. `--stacks N` with a list of
the suite's files schedules only those (and the root run when
`test_gateway.py` is among them). On one stack (`--stacks 1`, or a machine
that `default_stacks` gives one) the whole suite is one pytest and then the
root run on the same stack, so the uid isolation is tested there too. Each
run's output goes to `.integration-logs/FILE.log` (`test_gateway-root.log` for
the root run, `integration.log` for the whole suite in one pytest), emptied at
the start of such a run. The script prints a line when a run starts and ends,
then the short traceback of every failed run, a `FAILED FILE on stack K, log
PATH` line each, a `FAILED FILE, no result` line for every planned run that
has none (a run that raised, such as a log that cannot be opened, is a failed
run), and one summary line that adds the counts of every run, with the wall
time of the whole run, in pytest's form (for example `===== 1 failed, 161 passed, 3 skipped in 1234.56s (0:20:34) =====`).

The script takes a sleep and idle lock (`systemd-inhibit --what=sleep:idle`,
shown as "bwtidy tests" by `systemd-inhibit --list`) by running itself again
under it, so the laptop does not suspend on battery during a long run. If
`systemd-inhibit` is missing or refuses the lock, it prints one line on stderr
and runs without it. The lock does not hold against closing the lid, which
still suspends.

It runs `up` and `setup` first when a stack is not up, and leaves the stacks
running. After an e2e run `scripts/dev-vault reset` (without `BWTIDY_STACK`)
gives every stack a fresh vault. The tests carry the pytest marker
`integration` and skip unless `BWTIDY_DEV_STACK=1` (set in `compose.dev.yaml`
for the app container), so `bw` never runs on the host. `scripts/ci` does not
run them (`--ignore=tests/integration`).

### Writing an integration test

A test goes into `backend/tests/integration` and takes the fixtures of its
`conftest.py`: `bw_cli` (the container's CLI, logged in first when it is
logged out, the way `setup` does), `bw` (a session of it for a module) and
`app` (the single-user backend for a module, with the auto-commit of the test
stacks). The package `integration/support` holds the helpers the files share, imported by module
(`support.cli`: `BwCli` and the dev credentials, `support.app`: `App`, `Elsewhere` and
`wait_until_listed`, `support.vault`: the `Vault`, `support.restorer`). A test never imports
`conftest`.

- A test that writes to the vault undoes every change at the end of the
  module. `test_writes.py` does so with a PUT of the original item, a restore
  from the trash and a permanent delete of a created item, so the suite can
  run again on the same stack. A run that fails midway can leave the vault
  changed: `scripts/dev-vault reset` gives a fresh one.
- A `bw unlock` by the CLI invalidates the session key of a running bw serve
  for the CLI (the app's backups then fail with "Vault is locked"), so a test
  that needs the CLI while the app is logged in runs it with the app's own key
  (`app.session.session_key`) and never unlocks.
- Right after a `POST /sync` that fetched something, `GET /list/object/items`
  answers an empty list for a moment (see `VaultCache`), and bw serve answers
  "Not found" for every object while it decrypts again. A test that reads one
  item made or changed elsewhere (by the CLI, `Elsewhere`) calls
  `wait_until_listed(app, item_id)` from `support.app` (`tests/integration/support/app.py`)
  first: it syncs and waits until bw serve lists the item. A sync followed by
  `cache.load` needs no such wait.

`test_gateway.py` starts the real gateway (docs/spec.md, Gateway) on a
free port and logs two accounts in through it, each worker as its own uid.
Its tests run twice, with the usage stats off and on (`stats-off` and
`stats-on` in their ids). That needs root in the container, so the test skips in a plain run:

```sh
scripts/integration --root tests/integration/test_gateway.py
```

`--root` runs pytest as uid 0 (`scripts/dev-vault exec --root ...` does the
same for any command) with `uv run --locked --no-sync`, so uv never touches the
container's venv, which belongs to uid 1000, and without bytecode or the
pytest cache, so nothing root-owned is left in the mounted worktree. Run a
plain `scripts/integration` once before, it creates that venv. The test makes
the account directories under `/tmp` in the container (short socket paths,
and the workers' uids can enter them) and deletes them at the end. Root in
the container has no `CAP_SYS_PTRACE`, so the test finds a worker through
its bw serve's command line, never through `/proc/PID/environ`.

`test_bw_login.py` logs the CLI out through `POST /api/bw-logout` and in
again through the web login routes. Whatever happens, it logs the CLI in again
at the end, and every run logs it in first when it is logged out (the
`bw_cli` fixture).

## End-to-end tests

```sh
scripts/e2e                       # reset three dev stacks, run backend/tests/e2e on them
scripts/e2e --no-reset -k logout  # keep the vault, extra arguments go to pytest
scripts/e2e --stacks 2 -vv        # on two stacks, -vv shows each test's xdist worker
scripts/e2e --help                # the usage, starts no stack
```

`scripts/e2e tests/e2e/test_x.py` runs only that file (a test path, also
with `backend/` in front or as a node id with `::`, replaces the whole
directory), `-k NAME` runs one test. Both run on one stack (`BWTIDY_STACK`, 1
by default), which saves the reset of the others.

`scripts/e2e` runs `reset` of each stack (or with `--no-reset` only `up`, and
`setup` when the stack was not up), starts a gateway as root in each stack's
app container (see
[The gateway in the dev stack](dev-vault.md#the-gateway-in-the-dev-stack)),
waits for `/api/health` and runs pytest on the host with
`BWTIDY_E2E_URL=http://127.0.0.1:<BWTIDY_APP_PORT>` (one stack) or
`BWTIDY_E2E_URLS` (several). Playwright's chromium opens that URL, `bw` never
runs on the host. Afterwards it stops every gateway with SIGTERM (a clean stop
of every worker, which ends its session and commits pending edits), also after
a failure or Ctrl+C, and leaves the stacks up. It exits with pytest's code and
ends with pytest's summary line. Like `scripts/integration` it holds a sleep
and idle lock for the run (closing the lid still suspends). A killed run
leaves the gateways running, stop them as
[The gateway in the dev stack](dev-vault.md#the-gateway-in-the-dev-stack)
says.

A full run takes about 20 minutes on three stacks (every test opens its
account through the account form, and a worker starts again at the unlock
after every log out), and `--no-reset` saves the reset when the tests before
left the vault as it was.

The whole suite runs on several stacks (see
[Several stacks](dev-vault.md#several-stacks)) with pytest-xdist,
`-n N --dist loadfile --no-loadscope-reorder`: every file runs on one worker,
in its order, and worker `gwK` drives stack K+1. Each worker takes the next
file in the order of one stack (alphabetical), so the files of a stack run in
the same order as on one stack. `scripts/e2e` passes the stacks' URLs in
`BWTIDY_E2E_URLS` (comma separated), and `conftest.py` (`pick_stack`) sets
`BWTIDY_E2E_URL` and `BWTIDY_STACK` of its worker at import, so the tests'
own `scripts/dev-vault exec` calls reach the same stack. `--stacks N` sets the
number, also for a list of files. A crashed worker is not replaced
(`--max-worker-restart 0`), a new one would have no stack.

The gateway's output of the run is appended to `.dev-vault/backend.log`
(`.dev-vault-K/backend.log` for stack K), after a line with the start time, so
a failure can be read afterwards. With `-vv` (`-v` only cancels the `-q` of
`addopts`) pytest names each test's worker (`[gw1]`), so its stack (2). The
workers' output is in each account's `worker.log`. Every line of both starts
with its time in UTC, only what `bw serve` itself prints to stderr has none.
To place a frozen login, read `POST /api/session for ACCOUNT starts` and
`answered STATUS` in the gateway's log and the `Login: STEP` lines in
`worker.log`: the step after the last line is the one that hangs.

### Writing an e2e test

Every test in `backend/tests/e2e` carries the pytest marker `e2e` and skips
unless `BWTIDY_E2E_URL` is set, `scripts/ci` does not run them
(`--ignore=tests/e2e`). The tests reach the app only through the gateway, like
a browser on a deployed instance: every fresh browser context starts at the
account form, and the first test after a reset creates the dev account through
it (`enter_account`, which also runs the Bitwarden login when `unlocked_page`
needs it). `test_accounts.py` deletes the restore account's directory at its
start, so it can create that account anew. `test_delete_data.py` deletes the
restore account through Delete my account (never the dev account), and runs
Delete my data on the dev account, which empties its change log and its
backups.

To add a test, write a `test_*.py` in that directory and take what it needs
from the fixtures in `conftest.py` and the helpers in
`backend/tests/e2e/support`:

- `base_url`, the app's URL. `page.goto("/")` works, the context has it as
  its base URL.
- `page`, a page in a fresh browser context per test (no cookie, no
  `localStorage` from another test, so it starts at the account form), and
  `unlocked_page`, the same page with the dev account open, unlocked with
  the dev master password and showing the grid (it creates the account and
  logs in through the web login when the account is new or its CLI is
  logged out).
- `api`, the API with the page's cookies: `api.get("/api/queue?status=pending")`
  answers the parsed JSON and fails on a status that is not 2xx.
  `api.wait_saved()` waits until no UI edit is pending and no commit runs.
- `mcp_call`, a tool call over streamable HTTP at `/mcp` that answers the
  tool's text: `mcp_call("list_items", folder="Work")`. It carries the token
  of `mcp_token`, which makes a new MCP token in the unlocked page's app bar
  (MCP token, Create or Replace) and reads it from the dialog, so the token
  of an earlier test stops working (the gateway finds the account by the
  token). `create_mcp_token(page)` and `mcp_caller(base_url, token)` (both
  in `support.mcp`) do the same for a test that needs its own.
  MCP is disabled after every login, a test enables it with the switch in
  the app bar (click it, then wait until it is checked, it follows the
  server's answer). The switch refuses without a token that has not
  expired, so a test makes one first (`mcp_call` and `mcp_token` do).
- `fixture_items`, the items of the three fixture files by name, and
  `assert_no_secrets(text)`, which fails when the text holds one of their
  secret values (values under 6 characters are left out, they turn up by
  chance), naming the item and the column, never the value.
- `file_put_back` and `vault_put_back` put the vault back at the end of a file
  or of each test (`put_back` in `support.elsewhere`): one restore of every
  item the change log rows since the start wrote, from the oldest row's
  backup, then as another device the permanent delete of what that leaves in
  the trash (the items the writes made, an item a move or a convert replaced)
  and of the folders no item is in. A file takes the first with
  `pytestmark = pytest.mark.usefixtures("file_put_back")`, which also ends the
  session at the end of the file, so the next unlock loads what was put back
  as another device. `put_back` fails when the oldest row's backup is gone
  (`test_backups.py` deletes one and puts its items back itself).
- The test stacks commit after 10 s, not the product's 180 s: the session
  fixture `quick_auto_commit` puts `TEST_AUTO_COMMIT` (10 s, 20 changes) for
  the dev account before the first test of a stack, and the account keeps it
  until the next reset. A test that changes the auto-commit puts back what it
  found. The integration tests' `app` fixture (and `test_mcp.py`'s server)
  set the same for their app.

The plain helpers to import, one module of `support` for each theme:

- `support.login`, the login and account flows: `enter_account(page, email)`
  (opens `/` and goes through the account form unless the browser remembers
  the account, creating it when it is new), `open_account` (fills the form and
  continues), `log_in` (unlocks, or runs the web login when the account's CLI
  is logged out), `unlock`, `web_login`, `web_login_done`, `log_out` (the
  Bitwarden box unchecked), `log_out_of_bitwarden` (checked, waits for the
  Bitwarden login form), `log_out_dialog` (opens the dialog and sets the box)
  and the dev credentials.
- `support.pages`, the grid and dialog helpers: `grid`,
  `cell(page, item, column)` (searches for the item and answers its cell),
  `item_cell(page, item_id, column)` (the cell by item id, the search box
  untouched), `row(page, item_id)`, `search` (types into the search box and
  waits for the rows), `settle` (waits for the rows after a filter or a sort),
  `type_into` (an inline edit with Enter), `editor` (any open cell editor or
  dialog), `pick(scope, combobox, option)` (picks in a MUI select),
  `hover_tooltip`, `shown_tooltip` and `point_at` (see the gotchas), `MASK`
  and `QUEUED` (the class of a cell with a queued value).
- `support.api`, `Api` and the queue helpers: `rows_by_name(api)` answers the
  grid's rows (secrets masked) and `secret_is(api, row, column, value)`
  compares a secret cell without showing it. `last_entry_id(api)` before a UI
  edit and `commit_new_entries(api, after)` after it commit the edit at once
  instead of waiting for the auto-commit, and fail when it is not applied.
- `support.elsewhere`, the bw helpers of another device:
  `elsewhere(script, *args)` runs a shell script with `bw` as another device
  (a second CLI state in the app container, see
  [Trying the auto-commit](dev-vault.md#trying-the-auto-commit)),
  `trashed_ids()` answers the ids in the trash that way,
  `edit_notes_elsewhere(item_id, notes)` changes an item's notes and
  `put_notes_back_elsewhere(item_id, notes)` puts them back.
- `support.vault_items` makes and removes items and templates through the app
  (`new_item_dialog`, `trash`, `save_templates`, `wait_templates_saved`,
  `NAME`, `GENERATED_LENGTH`).
- `support.queue_page`, the MCP switch and the queue page (`mcp_switch`,
  `enable_mcp`, `item_id`, `open_queue`, `select`, `approve_result`,
  `APPROVE_TIMEOUT`).

Tests may write to the vault, but every file leaves it as it found it: the
files of a stack run one after the other on the same vault, and which file
follows which depends on the split over the stacks. A file that writes
through the app uses `file_put_back` (or `vault_put_back` when its tests need
the seeded items each time), and a test that changes an item as another device
puts it back itself, also as another device. What a put back cannot undo: an
item moved into an organization comes back as a new item with the same
content (a new id), the password history and the revision dates show the put
back, and the tool note keeps a section a test saved and emptied again (an
empty list, or the generator's built-in default stored where none was).
Within a file a test must not depend on what another test wrote: pick items
no other test touches.

`test_dismiss.py` makes a grid edit fail with a conflict: it changes the
item through the second CLI state of
[Trying the auto-commit](dev-vault.md#trying-the-auto-commit)
(`bw_elsewhere`) before it presses Commit now, with the auto-commit set to
wait an hour meanwhile (and set back at the end). Then one test dismisses
the failed entry and one retries it over the change elsewhere.

### Playwright gotchas

- The Queue button's label is `Queue, N pending` and it polls every 10
  seconds, so wait at least 12 seconds for an MCP proposal to show.
- On the Queue page each entry's checkbox has the entry's summary as its
  accessible name.
- Approve all runs in the background: read the tabs after the Working spinner
  is gone and the pending list is empty.
- AG Grid fades a row out after the rows change, and a row shown again can be
  in the DOM twice for about 300 ms (the leaving one has `ag-opacity-zero`).
  `item_cell` skips fading rows and `search` waits.
- A click on a cell focuses it, and AG Grid keeps the row of the focused cell
  rendered when it scrolls out of view.
- For a moment after Hide empty columns the grid's scroll area keeps the width
  of every column, so a hover that scrolls then can go past the real end and
  the grid pulls the headers back under the pointer, onto another column.
  `show_columns_of` in `test_normalize.py` waits until it has settled.
- A failure message must not show a secret: compare a revealed value as a
  bool, never with `expect`, and never assert on a dict that holds an item.
- A UI entry that failed at a commit (`test_auto_commit.py` does that on
  purpose) shows its snackbar in every later test's fresh browser context, so
  look for an alert by its text.
- AG Grid keeps a fading cell tooltip in the page with the class
  `ag-tooltip-hiding` until its animation ends, so read `shown_tooltip(page)`,
  never every `.ag-tooltip`. `hover_tooltip(page, target)` points the mouse at
  a cell or header and answers `shown_tooltip(page)`.
- Every reload of the rows (after a commit, also the auto-commit of the
  `Update the to-fill list` entry a commit queues, about 10 s later on the
  test stacks) refreshes the cells, and AG Grid hides a shown tooltip then and
  shows it again only at the next mouseenter. So `hover_tooltip` points again
  when no tooltip shows within 3 s, as a person moves the mouse off and back
  on.
- `point_at(page, target)` moves the mouse off the grid first (so the target
  gets a mouseenter of its own), scrolls a target out of view to the middle of
  the grid, leaves one in view where it is, and moves the mouse to its centre
  once it has held still for 300 ms. Playwright's `hover()` scrolls a pinned
  cell sideways and waits only two animation frames for the target to hold
  still, while the rows may still slide after a filter.

## Documentation screenshots

```sh
scripts/docs-shots                # take every screenshot again, delete the images no test wrote after a pass
scripts/docs-shots -k more_menu   # take only the shots of the tests named, delete nothing
```

The screenshots in the docs come from the docs tests in
`backend/tests/e2e/docs`: Playwright tests like the e2e tests, with the same
fixtures (`unlocked_page`, `api` and the rest) and helpers (`cell` and the
rest, `from support.pages import ...` as in the e2e tests), driving the app
against the seeded dev vault in a 1600 by 900 viewport. `scripts/docs-shots`
runs them through `scripts/e2e tests/e2e/docs` with `BWTIDY_DOCS_SHOTS=1`, so
on one stack and after its reset (`--no-reset` and other arguments go on to
`scripts/e2e`). Without that variable every test in the folder skips (its
`conftest.py`), so a whole `scripts/e2e` run writes no image. The folder's
`conftest.py` adds to the e2e one, and the `support` package is found because
pytest puts `backend/tests/e2e` on the import path.

A test calls `shot(page, NAME, frame=..., around=[...], margin=...,
highlight=[...])` from `backend/tests/support/docs_shots.py` (import it with
`from tests.support.docs_shots import shot`) for each image, before a click,
with a menu open, in the dialog it opens. It writes `NAME.png` into the
staging directory that `scripts/docs-shots` names in `BWTIDY_DOCS_SHOTS_DIR`
(a hidden folder inside `docs/images`), or into `docs/images` (`IMAGES`, the
one folder of every documentation image) when the variable is not set. It does
nothing outside a docs run, so an e2e test may call it too. The script moves
the images of the staging directory over `docs/images` only when the run
passed, and removes the staging directory in every case, so a failed run
leaves `docs/images` as it was.

- Frames: `window` (the default) is the whole viewport. `band` is the full
  width, cut above and below to the elements in `around` plus the margin,
  which is one number or `(top, bottom)`. `box` is the elements in `around`
  plus the margin on every side. The margin is 16 px by default, the
  rectangle is clamped to the viewport, and an element in `around` (or a
  highlight) outside the viewport or hidden fails the test with its selector.
- Highlights: each entry of `highlight` is a locator or `(locator, label)`.
  It gets a 3 px magenta outline a little outside the element, and a label
  such as `"1"` in a small badge on its top left corner. They are elements
  drawn above the page for the shot and removed right after it, also when it
  fails, so the next step and the next shot see the page as it was.
- The shot waits until the page's animations are over (a menu or popover
  grows in, measured sooner its box is too small), runs with animations off
  and the caret hidden, and hides every element with `data-shot-hide` (the
  faint build commit after the title in the app bar and the Version line of
  the About page, which change with every commit). A hidden element keeps its
  room, but in the app bar it takes none, so the build commit's width does not
  move the buttons after it.
- The docs tests' Chromium runs with `--disable-partial-raster` (their
  `conftest.py`): a partial raster draws the grid's rounded corners a few
  pixels differently from run to run. A shot of the whole grid calls
  `wait_for_grid(page)` first: the grid lays out its horizontal scrollbar a
  moment after its rows show.
- Names are lowercase letters, digits and hyphens, flat and unique across
  the repo: a name taken twice in one run fails with both tests. So a doc can
  move or split and an image can be used by several docs without a new name.
  Nothing depends on which markdown file links which image.

A docs test that writes to the vault puts it back at its end (`put_back`), so
the tests after it see the seeded vault. The tutorials are the exception: a
reader takes them in order, so `test_tutorials.py` holds one test for each,
in the order of the docs, each continuing from the vault the one before left.
Its module-scoped fixture `one_vault` puts the vault back once, after the
last, and a tutorial whose predecessor did not pass in the run skips
(`-k test_tutorials` runs them all). Each carries the marker `tutorial`
with its page, and `tests/repo/test_tutorial_order.py` checks that their
order is the one of the docs' nav and that only that fixture puts the vault
back. Times, backup names and backup sizes differ from run to run, and a
hidden cell (`data-shot-hide`) keeps its width, so the table still sizes its
column by the real text. A shot that shows them
routes the API through `FixedValues` (`docs_shots.py`, through `page.route`)
instead of hiding the cells: it maps them onto fixed ones, times in their
order. `test_tutorials.py` does so for its Queue and Change log shots,
`test_explanation.py` for its Change log row.

The full run deletes every image in `docs/images` first, so an image no test
takes any more is gone. A run of some tests (`-k NAME` or a test path)
deletes nothing and rewrites only their images. Both then run
`scripts/docs-lint`, so a new image no doc links yet, or a link to an image
no test takes any more, fails the run. Two runs on the same code give the same
files on one machine, byte for byte, but another machine's fonts give other
files. A UI change reruns the docs tests of what it touches
(`scripts/docs-shots -k NAME`) and commits the changed images with it. The
[pre-push hook](#the-pre-push-hook) makes sure of it: a push that touches the
UI takes every screenshot again and commits those that changed.

## The pre-push hook

```sh
scripts/install-hooks           # once after cloning, --force replaces a pre-push hook that is not ours
```

`scripts/install-hooks` links `hooks/pre-push` of the repo's git directory
(`git rev-parse --git-common-dir`, which every worktree shares) to the
committed `scripts/hooks/pre-push`, in the main checkout when it has that
file. Running it again changes nothing. It keeps a pre-push hook that is not
its own and prints it, and it never changes git's config: with
`core.hooksPath` set git runs the hooks there instead, which it says. The hook
runs `scripts/pre-push` of the checkout you push from, so the checks are those
of the code you push. Its decisions are in `scripts/devlib/prepush.py`.

On every push it reads the refs git pushes. A delete, a tag on a commit the
remote has and a branch on such a commit check nothing, so a push without a
new commit runs nothing. Otherwise, in two steps that each say what they do:

1. `scripts/ci` on the commit of each ref pushed, in full for a push to `main`
   or a tag, else `scripts/ci --quick` (everything but pytest and `npm test`,
   the CI workflow runs those on the pull request). When that commit is the
   checked-out HEAD and the checkout has no changes nor untracked files, ci
   runs in place. Otherwise it runs in a temporary worktree of the commit
   (`git worktree add --detach`, removed after), where ci first installs the
   dependencies. Either way it tests what is pushed. A failure stops the push.
2. When the push changes a file under `frontend/src/` or
   `backend/tests/e2e/docs/` (against the remote's commit, or for a new
   branch against its merge base with the remote's `main`),
   `scripts/docs-shots` takes every screenshot again, about 3 minutes when the
   dev stack's snapshot exists (the dev stack stays up, `scripts/dev-vault down`
   stops it). That needs the pushed commit checked out without changes, else
   the push stops and says so. When `docs/images` changed, the hook commits
   exactly those files as "Take the docs screenshots again" and stops the push.
   Push again: the screenshots are then current.

A step that passed on a tree (the same files, whatever the commit) is noted in
`bwtidy-pre-push` in the git directory and skipped when that tree is pushed
again, so the push after the screenshots commit runs only `scripts/ci`. The full
ci is noted as `ci` and skips both kinds, the quick one as `ci-quick` and
skips only the quick kind, so a tree that passed only the quick ci is tested
in full on a push to `main`.

## Checks on the repo

Besides lint, types, the build and the tests, `scripts/ci` runs these.

### Dependencies

Dependencies change only through the lock files, and every script installs
from them locked (AGENTS.md, Checks): `uv sync --locked` and `uv run --locked`
fail on a `backend/uv.lock` or `demo/uv.lock` that is out of date, `npm ci` on a
`package-lock.json` that does not match its `package.json`. `scripts/ci` runs
`npm ci` in a fresh worktree and again when `frontend/package-lock.json`
changed since the last install (it stamps
`frontend/node_modules/.bwtidy-lock-sha256` with the lock file's sha256). Its
check `lock files` (`scripts/devlib/locks.py`) compares each npm lock
file (`frontend/`, `docker/bitwarden-cli/`) with its `package.json` every
time.

Each dependency is declared with a compatible range in npm's caret sense
(AGENTS.md, Checks), which `tests/repo/test_dependency_ranges.py` checks for
`backend/pyproject.toml`. `scripts/update-deps` updates everything within the
ranges: `uv lock --upgrade` in `backend/` and `demo/`, `npm update` and `npm ci` in
`frontend/`, `npm update --package-lock-only` in `docker/bitwarden-cli/` (only
the image installs the CLI), the digest of each image that the `Dockerfile`,
`demo/Dockerfile` and `compose.demo.yaml` name
(its tag stays) and the commit SHA of each action to the newest release tag
of its major version. Then it runs `scripts/third-party` and the version
check, prints what changed per ecosystem and lists the updates beyond the
ranges (new majors, newer image tags) without applying them.
`scripts/update-deps --dry-run` shows the same and writes nothing. It never
commits and never changes the version of bwtidy, review and commit what it
changed as any other change.

After a change to the dependencies (`npm install` in `frontend/`, `uv add` or
`uv lock` in `backend/`), run `scripts/third-party` and commit the
`frontend/src/about/thirdParty.json` it writes: the About page lists every
package from it, and `scripts/ci` fails while it differs from the lock files.
A package whose metadata names no license makes it stop with the package's
name. Look the license up in the package's own repository and add it to
`OVERRIDES` in the script, with where you checked it.

### Docs lint

`scripts/docs-lint` (stdlib only, run by `scripts/ci` after the third-party
check) reads every markdown file of the repo (`git ls-files -co
--exclude-standard`) and checks that each local image link (`![alt](path)`,
`![alt][ref]`, an `img` tag's `src`, outside code) points to a file, and that
every file in `docs/images/` is linked from some markdown file, whichever
one. Every picture, also one that points to a URL, needs a non-empty alt text (an `img`
tag needs a non-empty `alt` attribute). It also checks that each other local link
(`[text](path)`, reference style, an `a` tag's `href`) points to a file, and
that a `#anchor` on a link to a markdown file (or a bare `#anchor`) matches a
heading of that file by GitHub's slug rules. Finally it applies the prose
style rule of `AGENTS.md`: a semicolon, an em dash or ` -- ` as a dash
outside code, HTML comments and link targets is a problem (an HTML entity
such as `&amp;` is not). A diagram (a fenced code block of a Kroki type,
see [Releasing](release.md#diagrams)) without `alt="TEXT"` after its type is
a problem too. It prints one line per problem and exits 1, nothing
and 0 when all is well.

### Frontend linters

The frontend's linters run in `frontend/` as `scripts/ci` runs them:

```sh
npm run lint            # oxlint, with the import rules and consistent type imports
npm run lint:deps       # dependency-cruiser: what imports what, no cycles (.dependency-cruiser.cjs)
npm run lint:exports    # knip: no file, export or type that nothing uses (knip.json)
```

### The MCP reference

`scripts/mcp-reference` (run by `scripts/ci` with `--check` after the docs
lint) writes `docs/reference/mcp-tools.md` from the tools the MCP server
registers, so after a change to a tool run it and commit the page. It imports
bwtidy, so it starts itself again through `uv run --locked` in the backend venv when
needed. Its intro is in the script (`INTRO`). A tool's description is short
paragraphs and lists split by blank lines (`paragraphs` and `bullets` in
`backend/src/bwtidy/mcp/texts.py`), which the page keeps, its `code` spans as
code, and its parameters follow in a table.

`scripts/ci` then builds the documentation site strictly, see
[Releasing](release.md#the-documentation-site).

## The demo stack test

`scripts/demo-stack test` builds the demo image from the worktree, starts
the demo stack (`compose.demo.yaml`) and runs
`demo/tests/stack/test_demo_stack.py` against it, through the edge: a
visitor's whole demo, every refused route, that no bwtidy container reaches
the internet, the cleanup at the end and the limits. What it checks and how
to run a part of it is in [The demo](demo.md#the-stack-test). It takes
about three minutes and about 1 GB of RAM, on a stack of its own beside the
dev stacks. `scripts/demo-stack down` removes it. `scripts/ci` never runs
it, so run it after a change to `demo/`, `compose.demo.yaml` or the gateway.

`backend/tests/repo/test_demo_stack_files.py` reads `compose.demo.yaml` and
`demo/Dockerfile` alone, in `scripts/ci`: the internal networks, the one
published port, the one allowed server, the dropped capabilities, the
Vaultwarden version of the dev stack and that the normal image holds no demo
code.

## Smoke test on a real account

Before a first run on a real vault, check the basics on an account whose
vault you have exported (in the Bitwarden web vault, Tools, Export vault).
Stop at the first surprise.

1. Build and start bwtidy as [Run bwtidy](../docs/how-to/run.md#start-it)
   says. `docker port bwtidy` prints only `8000/tcp -> 127.0.0.1:8765`.
2. Log in as [Log in the first time](../docs/how-to/run.md#log-in-the-first-time)
   says. The console never shows the master password or the session key, and
   `docker logs bwtidy` holds no secret value.
3. Read the grid as [Look at the grid](../docs/tutorial/first-cleanup.md#look-at-the-grid)
   says: one row per item of the web vault plus the tool note, secret cells
   masked.
4. Edit the notes of an item nobody needs and commit it
   ([How edits are saved](../docs/explanation/edits.md)). The Change log shows
   the write with its backup.
5. Roll that edit back with Restore on its row
   ([Change log](../docs/reference/ui.md#change-log)) and check the item in
   the web vault.
6. Make a token and connect a client as
   [Connect an MCP client](../docs/how-to/mcp.md) says, switch MCP on, let the
   agent propose one change and approve it on the
   [Queue](../docs/reference/ui.md#queue) page. The agent sees no secret
   value.
7. Stop it as [Update, stop and logs](../docs/how-to/run.md#update-stop-and-logs)
   says. Should the vault ever be lost, [restore.md](../docs/restore.md)
   brings it back from a backup.
