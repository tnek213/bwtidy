# The dev vault

Each worktree runs its own dev stacks: a Vaultwarden server and the app
container (Dockerfile target `dev`), with the accounts of
[Credentials](README.md#credentials) and the seeded vault of
[The fixture](#the-fixture). The tests and `scripts/dev-app` run against it,
never against a real vault.

## Commands

```sh
scripts/dev-vault up          # start the stack, ports go to .dev-vault.env
scripts/dev-vault setup       # register the accounts, create the orgs, log bw in, seed
scripts/dev-vault seed        # import the fixture into an empty vault
scripts/dev-vault exec ARGS   # run ARGS in the app container (--root runs them as root)
scripts/dev-vault reset       # down, up, setup: a fresh vault, from the snapshot in seconds
scripts/dev-vault down        # stop the stack and delete its volumes
```

Without `BWTIDY_STACK` (see [Several stacks](#several-stacks)) `down` stops
every stack of the worktree and `reset` resets stack 1 and every other stack
that is up.

`up` picks two free ports on 127.0.0.1 and names the compose project
`bwtidy-<worktree dir name>` (with `-K` for stack K), so stacks in different
worktrees, and the stacks of one worktree, do not collide. `setup` is
idempotent, it skips what exists (on a stack from the snapshot it only checks
the CLI, see [The snapshot](#the-snapshot)). It seeds only a vault with no
items and nothing in the trash, so a second `setup` changes nothing. `seed`
refuses a vault that is not empty, `reset` gives a fresh one. The first
`setup` installs Playwright's chromium (`uv run --locked playwright install chromium`
in `backend/`).

## How it fits together

- Vaultwarden serves HTTPS on its container port 443 with a self-signed
  certificate that `up` writes to `.dev-vault/`. bw 2026.10.0 and the web vault
  refuse any server URL that is not `https://`.
- Inside the stack the server is `https://vaultwarden`. That is also its
  `DOMAIN`, so attachment links work from the app container. The app container
  trusts the certificate through `NODE_EXTRA_CA_CERTS`.
- From the host the web vault is `https://localhost:<BWTIDY_VW_PORT>` (the
  browser warns about the certificate). The app port is
  `127.0.0.1:<BWTIDY_APP_PORT>`, nothing listens there until a script or a
  test starts the app in the container.
- The worktree's `backend/` is mounted at `/src/backend` in the app container.
  `BITWARDENCLI_APPDATA_DIR=/data/bw` and `BWTIDY_DATA_DIR=/data/app` (the
  single-user backend of the integration tests) and the gateway's
  `/data/accounts` are named volumes, wiped by `down`.
- compose build needs buildx 0.17 or later, so `up` builds the app image with
  plain `docker build` and tags it with compose's default name
  (`<project>-app`).
- `setup` leaves bw logged in and locked. To use it by hand:

```sh
S=$(scripts/dev-vault exec bw unlock bwtidy-dev-password-1 --raw)
scripts/dev-vault exec bw list organizations --session "$S"
```

## Several stacks

A worktree can run several stacks side by side, each with its own Vaultwarden,
app container, ports, certificate and volumes. `BWTIDY_STACK=K` picks stack K
for every command of `scripts/dev-vault` (and of `scripts/integration` and
`scripts/e2e` on one stack). Stack 1 is the default: `.dev-vault.env`,
`.dev-vault/` and the compose project `bwtidy-<worktree dir name>`, which
`scripts/dev-app` and `scripts/manual` use. Stack K from 2 up has
`.dev-vault-K.env`, `.dev-vault-K/` and the project
`bwtidy-<worktree dir name>-K`. The env file names the stack's directory
(`BWTIDY_TLS_DIR`), where `compose.dev.yaml` finds the certificate.

```sh
BWTIDY_STACK=2 scripts/dev-vault up       # start stack 2 (from the snapshot)
BWTIDY_STACK=2 scripts/dev-vault exec bw status
BWTIDY_STACK=2 scripts/dev-vault down     # stop only stack 2
scripts/dev-vault down                    # stop every stack of the worktree
docker ps --filter name=bwtidy-           # the containers, stack K's project ends in -K
```

Only stack 1 builds the snapshot. A stack from 2 up starts from it: when there
is none, `up` waits while stack 1 runs from a bare Vaultwarden (its `setup`
saves one) and fails otherwise. `scripts/e2e`, `scripts/integration` and a
`reset` of several stacks therefore run stack 1 first when there is no snapshot
of the current HASH, then the others at once. Their output lines start with
`stack K: `.

`scripts/e2e` and `scripts/integration` run their whole suite on three stacks
by default (`default_stacks` in `scripts/devlib/stacks.py`): fewer when
`MemAvailable` minus 1.5 GB does not hold a stack each (`STACK_MB`, 1.8 GB: an
e2e stack with its browser takes 1.3 to 1.7 GB), never more than `MAX_STACKS`.
`--stacks N` sets the number. A test path or `-k` runs on one stack, and with
one stack `scripts/e2e` runs one pytest, `scripts/integration` one pytest and
then the root run of `test_gateway.py` (see
[Integration tests](testing.md#integration-tests)). The stacks stay up
afterwards, a plain `scripts/dev-vault down` removes them all.

## The snapshot

A full `setup` takes about 2 minutes (every `bw` call in the app container
takes about a second, see [The bw wrapper](#the-bw-wrapper)). So the seeded
state is kept as a local image, `bwtidy-dev-vaultwarden:HASH`:
`FROM vaultwarden/server` with Vaultwarden's `/data` after a setup (the SQLite
database, the attachments and the RSA key) and the dev account's CLI state
(`/data/bw`, logged in and locked) in `/bwtidy-snapshot/bw`. It holds the two
dev accounts, their organizations and collections and the seeded fixture,
nothing else. It is never pushed.

HASH is a hash of the files that shape the seeded state, never of a guide:
`scripts/dev-vault`, the seeding code (`scripts/devlib/seed.py` and the modules
it imports, `compose.py`, `paths.py` and `stacks.py`, and `snapshot.py`), the
fixtures (`dev/fixture-*.json`), the attachments (`dev/attachments/*`),
`compose.dev.yaml`, the `Dockerfile`, the bw CLI's `package.json` and
`package-lock.json` in `docker/bitwarden-cli/` (its version and the packages it
brings along) and `docker/unref-share-timers.js`. An edit of a page under `dev/` leaves it.
`up` prints the current one, `docker image ls bwtidy-dev-vaultwarden` lists all
of them.

- When the image of the current HASH exists, `up` writes it to `.dev-vault.env`
  as `BWTIDY_VW_IMAGE`, compose starts Vaultwarden from it (a fresh `vw-data`
  volume is filled from the image's `/data`) and `up` copies the CLI state into
  the fresh `bw-data` volume. `setup` then only checks that bw is logged in as
  the dev account and locked, and runs in full when it is not. A `reset` then
  takes under 5 seconds.
- When it does not exist, `up` starts a bare Vaultwarden and `setup` builds the
  state in full. When that `setup` seeded the vault, it stops Vaultwarden
  (so the SQLite files are consistent), copies `/data` and the CLI state out,
  builds the image and starts Vaultwarden again. A `setup` that skipped the
  seed never saves one, its vault may hold what tests wrote.

A change of any input gives a new HASH, so the next `reset` builds a new
snapshot by itself. So does a snapshot older than 14 days (the label
`bwtidy.dev-vault-snapshot.created`): Vaultwarden's refresh tokens last 30
days, the CLI login in an older one could not sync. A running stack keeps the
image it started with until `down`.

The certificate is new on every `up` and in neither the image nor the CLI
state (the CLI trusts it through `NODE_EXTRA_CA_CERTS`), so the snapshot works
with any.

To force a rebuild of the current snapshot, or to remove old ones (`down`
keeps them, each is about 3 MB on top of the Vaultwarden image), remove the
images and reset. A snapshot rebuilt for its age leaves the old one untagged,
`docker image prune` with the label removes those:

```sh
docker image rm $(docker image ls -q bwtidy-dev-vaultwarden)
docker image prune --filter label=bwtidy.dev-vault-snapshot
scripts/dev-vault reset
```

An image a running stack uses cannot be removed, run `down` in that worktree
first.

## The fixture

`dev/fixture-personal.json`, `dev/fixture-org.json` and
`dev/fixture-org-club.json` are in Bitwarden's JSON import format
(`bw import bitwardenjson`). All data is made up. The shape follows "The vault
today" in `docs/spec.md`.

Personal vault, 69 items imported:

- 40 plain logins with username, password and uris (one without uris), 8 of
  them with a TOTP, 10 each in the folders Work, Home and Infra, 10 in no
  folder. Three of them are favorites: `Wiki` (Work), `Streaming video`
  (Home) and `Dashboards` (Infra).
- A computer in three shapes: `ThinkPad X1` (login: `MSDM Windows Key`,
  `BIOS Password`, `LUKS`, `User`, `User password`), `Gaming desktop` (login:
  `LUKS/dm-crypt and Bitlocker`, `Bitlocker RecoveryPassword`,
  `UEFI Administrator Password`), `Media PC` (note: `user`, `user password`,
  `dm-crypt password`).
- 6 notes that are really logins: 4 with `username` and `Password`, 1 with
  `username` and `password`, 1 with `username` and `code`.
- 8 notes with free text and no fields (license keys, a PIN, recovery codes, a
  bank account).
- 7 notes with odd field names: `ca key passphrase`, `Public Key`, `token`,
  `vault_pass_ai`, `sudo` plus `infra`, `Encryption passphrase ` (trailing
  space), `Recovery code`, `Recovery key`.
- `Home router`, a login with the field `WPA key` twice.
- 2 cards.
- `Dev Identity`, an identity with title, first and last name, email, phone,
  address, city, postal code, country and username, in no folder.
- `Build server key`, an SSH key item with private key, public key and
  fingerprint (a freshly generated ed25519 key), in no folder.
- `Example Forge` has a passkey (`login.fido2Credentials`, a freshly
  generated P-256 key). `Example Mail`, `Online bank` and `Proxmox` have
  `passwordHistory`.

Hidden values are custom fields of type 1. Custom field names: 26 distinct.

Organization `Family` (`dev/fixture-org.json`), 10 items: 6 in the
collection `Shared`, 4 in `Youth`, including the computer note `Youth laptop`.

Organization `Club` (`dev/fixture-org-club.json`), 1 item: the secure note
`Club locker code` (plain notes, no fields, no attachment) in its collection
`Shared`. That collection has the name of one of Family's on purpose, for the
tests of a backup with two organizations.

The organization and collection ids in both org files are placeholders, each
file its own. `seed` replaces them with the real ids before the import, so the
server reuses the collections that `setup` made instead of creating new ones.

After the imports `seed` attaches `dev/attachments/*.txt` to `Internal CA`,
`ThinkPad X1` (personal), `Home insurance` and `Youth laptop` (org), then moves
`Old forum account` and `Expired license key` to the trash. The active vault
is 67 personal items plus 10 Family items and 1 Club item, with 2 items in the
trash. With the tool note made at the first login the grid has 79 rows.

`bw import bitwardenjson` 2026.10.0 into Vaultwarden 1.37.4 keeps
`fido2Credentials` (every key of the export format), `passwordHistory`,
duplicate field names, the trailing space in a field name, `favorite`, the
identity and the SSH key with all their values.
`backend/tests/integration/test_seed.py` checks all of the above.

Item names are unique across the three files, the seed and the tests look
items up by name.

## The gateway in the dev stack

`scripts/dev-app`, `scripts/manual` and `scripts/e2e` run the gateway
(docs/spec.md, Gateway), never the single-user backend. They first run
`uv sync --locked` in the container as uid 1000 (the venv `/home/app/venv` is that
user's), then start `/home/app/venv/bin/bwtidy-gateway` as root
(`scripts/dev-vault exec --root`) with `BWTIDY_ALLOWED_SERVERS=https://vaultwarden`
(the dev server has a private address) and `PYTHONDONTWRITEBYTECODE=1` (no
root-owned bytecode in the mounted worktree). `NODE_EXTRA_CA_CERTS` comes
from `compose.dev.yaml` and reaches the workers. The gateway listens on the
container's port 8000, so on `127.0.0.1:<BWTIDY_APP_PORT>` from the host.

Each account gets a worker as its own uid (70001 and up) and a directory in
the volume at `/data/accounts/vaultwarden/EMAIL`: the SQLite file and the
backups in `app/` (the dev stack sets no `BWTIDY_BACKUPS_ROOT`), the CLI
state in `bw/`, the worker's output in `worker.log`. Only root can read them:

```sh
scripts/dev-vault exec --root ls -l /data/accounts/vaultwarden
scripts/dev-vault exec --root tail /data/accounts/vaultwarden/dev@bwtidy.test/worker.log
```

`setup` logs the container's own CLI state (`/data/bw`) in, which only the
integration tests and the second CLI state of the tests use. An account of
the gateway starts with its CLI logged out: after `reset` the first login of
`dev@bwtidy.test` creates the account (Continue, then Create) and runs the
Bitwarden login.

The scripts stop the gateway with SIGTERM: it stops every worker cleanly
(each ends its session and commits pending edits) and exits. A run that was
killed leaves the gateway and its workers running in the container, and the
next run refuses to start while the port answers. Stop them as root (with
`BWTIDY_STACK=K` for stack K):

```sh
scripts/dev-vault exec --root python3 -c 'import os, signal
for pid in filter(str.isdigit, os.listdir("/proc")):
    args = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
    if b"/home/app/venv/bin/bwtidy-gateway" in args:
        os.kill(int(pid), signal.SIGTERM)'
```

A recreated container (`scripts/dev-vault up` after a change of the image)
drops them too.

### Testing by hand

`scripts/manual [--keep-vault]` is the way to test by hand. Run it in the main
checkout: it refuses when the checkout is not on main or has uncommitted
changes (it lists the files), fetches origin and fast-forwards main to
origin/main (it refuses when that is not possible, so an old checkout is never
tested by mistake), prints the version it is about to test (like
`0.1.0-alpha.1 (261111 7db8830)`), runs `scripts/dev-vault reset` unless
`--keep-vault`, then `scripts/dev-app`. When the backend answers it compares
the version from `/api/health` with the expected one and prints
"Testing VERSION" in a banner. On a mismatch it stops the backend and fails.

## The bw wrapper

bw 2026.10.0 stays alive about 2 seconds after it has printed its answer and
set its exit code: its state providers share their observables with rxjs
`share` and a reset timer (`resetOnRefCountZero`, 1000 ms, two in a chain),
and those timers keep Node's event loop alive. They only drop a cache nobody
subscribes to any more.

So in the Dockerfile target `app` (a deployed instance and the dev stack
alike, the target `dev` is built from it) `/usr/local/bin/bw` is a shell
wrapper that runs `exec node --require
/usr/local/lib/bwtidy/unref-share-timers.js .../build/bw.js "$@"`. The
preload, `docker/unref-share-timers.js`, calls `unref()` on exactly the
timers started from `share`'s reset and leaves every other timer alone.

- Every `bw` of the image runs through it by `PATH`: the workers, `bw serve`,
  the web login, the backups, the integration tests, `elsewhere` and
  `scripts/dev-vault`.
- A `bw status` takes about 0.6 seconds instead of 2.6. The output, the exit
  codes and the errors are the same.
- The command line keeps bw's arguments (it starts with
  `/usr/local/bin/node --require ...` instead of `node /usr/local/bin/bw`), so
  the scripts that find a `bw serve` by its socket argument still do.
- The files are readable by every uid, as the workers run bw as the
  account's uid.

## Trying the web login

1. Run `scripts/dev-app` (after a `reset` the dev account is new).
2. In the account form type the server `https://vaultwarden` and the email
   `dev@bwtidy.test` (or `restore@bwtidy.test`), Continue, then Create. The
   Bitwarden login page and its console show.
3. The server and the email are filled in and read-only, they belong to the
   account. Type the master password, then Log in. Vaultwarden asks no
   one-time code, so the console shows "You are logged in!" with the session
   key masked, and the page unlocks into the grid.
4. To see the login again, Log out in the app bar with "Also log out of
   Bitwarden" checked: it logs the account's CLI out.

What to expect:

- A wrong password ends with "Username or password is incorrect" and Try
  again.
- A new account whose Bitwarden login never succeeded is deleted when you
  leave it (Switch account) or its worker exits.
- While the CLI is logged out, `bw serve` exits at once with "You are not
  logged in." and the backend restarts it with a growing delay. The web login
  starts it again when it is done.
- Vaultwarden asks nothing, so the answer field is not needed here.
  `test_bw_login.py` covers it: it starts a login with an empty password,
  which makes the CLI ask "? Master password:", and answers through the input
  route.
- Never run `bw logout` in an account's directory by hand while its worker
  runs: a bw serve that sees the CLI log out under it hangs on the next
  unlock.

## Trying the auto-commit

Grid edits go into the queue and the auto-commit writes them (see
[How edits are saved](../docs/explanation/edits.md)). To see what really
reached the dev vault while the app runs, use a second CLI state in the app
container. A `bw unlock` of the CLI the app uses would invalidate the app's
session key, a second state in its own directory logs in as another device and
leaves the app alone:

```sh
C="env BITWARDENCLI_APPDATA_DIR=/tmp/bwcheck"
scripts/dev-vault exec $C bw config server https://vaultwarden
scripts/dev-vault exec $C bw login dev@bwtidy.test bwtidy-dev-password-1
S=$(scripts/dev-vault exec $C bw unlock bwtidy-dev-password-1 --raw)
scripts/dev-vault exec $C BW_SESSION="$S" bw sync
scripts/dev-vault exec $C BW_SESSION="$S" bw get item ID
```

The same state can edit an item before a commit (`bw edit item ID BASE64`),
which makes the queued edit of that item fail with a conflict. The state is
in the container's `/tmp`, so a recreated container has none.

What to expect:

- A background backup takes about 12 seconds on the dev vault (every
  attachment and the trash listing are one bw CLI process each), and a write
  waits for a running one.
- The first edit after a quiet spell is in the vault about 180 seconds after
  it was made (the default). Set a lower After seconds in the Queue page's
  Auto-commit box or press Commit now to try it faster. An edit right after a
  commit is written up to that backup's time later.
- The queue table (`created_at`, `decided_at`) and the change log in
  `/data/accounts/vaultwarden/dev@bwtidy.test/app/bwtidy.sqlite` (read it as
  root) tell the times exactly.
- A clean stop of the gateway (SIGTERM) stops the workers cleanly, each ends
  its session, which commits first. To leave edits pending across a restart,
  kill the account's worker and its bw serve with `kill -9` as root in the
  container (it has no `ps`, look in `/proc/*/cmdline`), the next unlock then
  commits them.
