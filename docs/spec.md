# bwtidy specification

bwtidy is a local web UI and MCP server for keeping a Bitwarden
vault consistent. It shows the whole vault as a wide grid, normalizes field
names across items, applies templates, and lets an AI agent propose
reorganizations without ever seeing secret values.

This document describes bwtidy as it is: every rule in the present tense,
with its reason. Where the code lives is in [architecture.md](architecture.md).

## The vault it is made for

bwtidy is made for a personal vault of a few hundred items, mostly logins and
secure notes, whose custom field names have grown inconsistent: the same thing
spelled several ways across items, logins stored as secure notes, the same
kind of item in several shapes with different field names. An organization
shares some of the items with other people through collections. The problem
is inconsistency, not size: normalizing is the key feature, and the whole
vault fits in RAM.

## Architecture

bwtidy is one docker container: a gateway that is the only listener proxies
each browser and MCP client to the worker of its account (see Worker mode and
Gateway). Each worker talks to its own `bw serve` of the official Bitwarden
CLI (`@bitwarden/cli`, pinned to 2026.10.0) on a unix socket in the account's
directory, and keeps the account's vault in RAM while it is unlocked, loaded
in one listing: a few hundred items need no paging. `bw serve` has no
authentication of its own, so it is never published, and only the web port
is published, on the host's 127.0.0.1: that keeps the unlocked vault away
from other local programs. It is not hacker proof (whoever is in the host's
`docker` group can reach it), and that is accepted. bwtidy talks to `bw
serve` rather than running one `bw` CLI call per operation, because every
CLI call pays a Node startup (the login and the backups still run the CLI,
see Backups). How bwtidy is built (what runs where, the modules, how a write
flows, where state lives) is in [architecture.md](architecture.md).

## Worker mode

Several Bitwarden accounts (for example each person's own account and a shared
one) use one bwtidy URL, each one fully separated from the others. There are
only a few accounts, so isolation and simple code matter more than scale.

- A gateway is the only listener. It serves the frontend and the account
  form, and proxies everything else to the worker of the browser's account.
- Each account gets its own worker while someone uses it: the backend
  described here, started by the gateway as a process of its own, running as
  its own uid. Only a click on Unlock or Log in starts it (see Gateway): each
  worker is a Python process of about 100 MB plus its own bw serve of about
  190 MB, and opening the page or filling in the account form needs no
  credential.
- Everything about an account lives in its own directory under `accounts/`
  (mode 0700, owned by that uid): `app/` (SQLite, and the backups unless they
  have a root of their own, see Gateway, Backups root), `bw/` (the
  CLI state), `run/` (the sockets), `home/`, the MCP token hash, the CLI's
  login state for the gateway and `worker.log`. No shared SQLite: the
  directory tree is the account list.
- The account's `app/bwtidy.sqlite` is not encrypted. A secret value is in
  it only as an age ciphertext (see Secret values), but the queue's diffs
  hold names, notes, URIs and the other plain values, and the change log
  the coordinates each write changed, custom field names included (see
  Writes). The account directory (mode 0700) is what keeps it from other
  uids: only the account's own uid (and root) can read it. What it holds is
  documented here rather than encrypted.
- One account per browser profile at a time: switch account, then log in as
  another. All tabs of the profile share the account, so the other tabs
  reload when it changes (see The account form). MCP takes a bearer token per
  account.
- A deployed bwtidy runs behind a reverse proxy that terminates TLS (for
  example at `https://bwtidy.example`), reachable only over a private network
  such as a VPN.
- A deploy keeps every account's data (see Schema versions).

The backend becomes a worker through these settings. All are optional, and
without them it runs as a single-user app: `bw serve` on `127.0.0.1:8087`
inside the container, its data in `BWTIDY_DATA_DIR` (`/data/app` in the dev
stack), the CLI state in `BITWARDENCLI_APPDATA_DIR`. Only the integration
tests start it that way, in the image's dev target as uid 1000.
`compose.yaml`, `scripts/dev-app` (and so `scripts/manual`) and the
end-to-end tests run the gateway.

- `BWTIDY_SOCKET=PATH`: the app listens on this unix socket instead of
  `BWTIDY_HOST` and `BWTIDY_PORT`. A stale socket file is replaced, and the
  file is removed when the app exits.
- `BWTIDY_BW_SOCKET=PATH`: `bw serve` listens on the unix socket
  (`--hostname unix://PATH`, which bw passes to node's `server.listen`)
  instead of a port, and the backend talks to it through that socket. A
  socket file left by a killed bw serve is removed before every start, else
  the new one cannot listen. Reason: no process can reach another account's
  unlocked bw serve over TCP, the socket's directory belongs to the account's
  uid. `--port` is still passed and the requests still carry `Host:
  127.0.0.1:PORT`: bw serve answers 403 to any Host outside `localhost:PORT`,
  `127.0.0.1:PORT` and `[::1]:PORT` (PORT defaults to 8087), even on a
  socket.
- `BWTIDY_ACCOUNT_SERVER` and `BWTIDY_ACCOUNT_EMAIL` (both or none, the
  start fails with only one): the worker's account is fixed. `POST
  /api/bw-login` with another server (compared without a trailing slash and
  case) or email (compared lowercased) answers 422 `This bwtidy worker
  belongs to EMAIL on SERVER.` `GET /api/session`'s `bwLogin` carries the
  fixed server and email (never the browser's) and `accountFixed: true`,
  and the Bitwarden login form shows the server and the email read-only.
  Without these settings `accountFixed` is left out.
- `BWTIDY_EXIT_AFTER_SESSION=1`: the worker exits right after its session
  ended (after the pending `ui` entries were committed and bw serve locked,
  see Session and access), and also when no session started within
  `BWTIDY_IDLE_EXIT` seconds (default 600) of its start (the click that
  started it, see Gateway) while no Bitwarden login runs (a login counts until
  its finish or its drop). The exit is a clean shutdown: the lifespan stops bw
  serve, exit code 0. Reason: nothing of the vault stays in RAM between
  sessions. A new login that replaces a running session does not exit. The
  exit counts as settled from the moment the session's end starts (it can
  take long, it commits the pending edits), or the idle time passed. From
  then on `/api/health` answers 503 `{"status": "exiting"}`, so the gateway
  never hands a browser a worker that is on its way out, and a login that
  reaches the worker anyway (it was handed out a moment earlier, or it waited
  for the session's lock) starts no session: it answers 502 with the gateway's
  own text `The account's worker does not answer`. The frontend reads that
  like a gateway's 502: it opens the account again and repeats the login, and
  for that click the gateway waits for the old worker to go and starts the
  next. Reason: otherwise a new page's unlock during the old page's log out
  would succeed on the exiting worker, whose exit then drops the session
  right away, and the page would be back at the unlock form.
  The exit and a SIGTERM end within a bounded time, also when bw serve or the
  Bitwarden server does not answer (each call to bw serve waits up to 120
  seconds for its answer). The session's end gives up on its commit after 60
  seconds, on a running background backup after 30 and on the lock of bw
  serve after 10 (see Session and access). A stop's own end of the session
  cancels the background backup at once instead (its `bw` gets SIGTERM), so
  the 30 seconds never add to a stop.
  Then uvicorn waits 30 seconds for the requests still running, cancels them
  (a commit one of them runs is cut off, see MCP and the queue) and runs the
  clean shutdown: it waits 60 seconds for the commits under way, cancels them
  when they are not done, ends the session (without a new commit after such a
  cancel) and stops bw serve. A cancelled commit gets 10 seconds to end, its
  next step is cancelled too. So a stop usually ends before the gateway kills
  the worker 120 seconds after its SIGTERM. Reason: with bw serve frozen,
  a session's end that waited the full 120 seconds for each call would exit
  the worker minutes after its timeout, and a SIGTERM would take as long,
  while the worker started bw serve again whenever it was stopped.
- `BWTIDY_BACKUPS_DIR=PATH`: the backups are in this directory instead of
  `backups` in `BWTIDY_DATA_DIR` (see Backups). The gateway sets it when it
  has a backups root (see Gateway). Everything that touches the backups uses
  it: the backups themselves, the retention, the re-encryption at a login with
  a new master password and the backup routes. The fingerprints in SQLite
  name a backup by its file name only.
- `BWTIDY_PENDING_MARKER=PATH`: the worker deletes this file after its
  first successful unlock (once the cache, the tool note and the backups'
  password are done). The gateway creates it with a new account's
  directory (at its first Bitwarden login), which is kept only when bw login
  and the unlock succeed, so the marker tells a new account that never logged
  in from a real one.
- `BWTIDY_SECURE_COOKIES=1`: the session cookie carries `Secure` (the
  gateway runs behind https on a deployed instance).

Every backend, a worker or not, writes `bw-login-state` in `BWTIDY_DATA_DIR`
(`login_state.py`): one line, `logged in` or `logged out`, mode 0600, swapped in
atomically, and only when it differs from what this process wrote last. It
notes what `GET /api/session` and the start of a Bitwarden login find (bw serve's
or the CLI's status, nothing when `bw status` gave no answer), a `bw login` that
exits 0, every successful unlock and a `bw logout` that exits 0. The gateway reads it to
show the unlock or the Bitwarden login before a worker runs (see Gateway). Reason:
the gateway runs as root and never asks a worker that does not run, and the
file is the only thing besides the MCP token hash it reads from an account.

Stopping a `bw` process: always SIGTERM first, SIGKILL only when it is still
there 5 seconds later. This holds for every `bw` the backend starts: the
commands of a backup, bw serve, `bw login` and `bw logout`. Reason: every `bw`
process shares one CLI state, `bw/data.json`. It rewrites the whole file for a
change (truncate, then write) under the lock directory `data.json.lock`. A
SIGKILL between the truncate and the write leaves an empty file, and it always
leaves the lock behind. An empty file means the CLI is logged out, and a bw
serve that already runs then does not answer its next `/unlock` at all: the
login holds the form disabled until the 120 second limit of the bw serve
client (answer 503), and the next look at the session shows the Bitwarden
login. A lock left behind costs the other `bw` processes about 10 seconds
until they call it stale (the stale rule of proper-lockfile in bw). On SIGTERM
node finishes its synchronous write and then exits, and the lock goes with it.
The one `bw` a session's end would cut off is the background backup's (see
Backups), when a new login or a log out comes while it runs.

So a session's end never stops a `bw` it does not have to. Even on SIGTERM a
dying `bw export` can leave `data.json.lock` behind, and bw serve's `POST
/lock` then waits the 10 seconds of the stale rule: were the session's end to
give up on it and the new login to unlock, bw serve would run the lock a few
hundred milliseconds afterwards and the next step would find the vault locked
(502 `Vault is locked.`, "Login failed, try again"), or the lock would land
during the cache load and the login would answer 200 on a locked bw serve.
Hence:

- A session's end stops the background backup between its steps: the
  backup checks a flag before each call to bw serve or the CLI and stops
  there (`Background backup stopped between its steps, before the STEP`).
  The `bw` that runs is waited for, never cut off, up to 30 seconds
  (`BACKUP_STOP_LIMIT`, `Session end: background backup ended, waited N s`),
  and only after that the backup is cancelled (`Session end: the
  background backup did not end within 30 s, cancelling it`). Then comes
  `POST /lock`. A worker's stop (SIGTERM) cancels it at once.
- A `POST /lock` that gave no answer within its 10 seconds is not
  dropped: bw serve still runs it. The next login waits for it to end
  before it unlocks (`Login: waiting for the lock of bw serve that gave no
  answer`, then `Login: the lock of bw serve ended, waited N s more`), up to
  20 seconds more (`LATE_LOCK_LIMIT`). When it still runs then, the login
  sends no unlock and answers 503 `bw serve has not finished locking the
  vault, try again`. While it runs it stands for the next lock too, so no
  second lock queues behind it. Waiting is chosen over a restart of bw
  serve: the lock lands within a second after the 10 seconds, while a
  restart costs bw serve's stop, its start and the wait until it answers.
- After the cache load the login asks bw serve's status and fails with 502
  `bw serve locked the vault again during the login, try again` unless it
  reports `unlocked` (`Login: unlock check failed, ...`), so a login never
  answers 200 on a locked bw serve.

Logs: every line of the gateway and the workers starts with its time
(uvicorn's lines too, `uvicorn_log_config`), our own lines from INFO up, other
libraries' from WARNING up (INFO of a library can name what passes through
it, like the URLs of httpx). `Session.login` logs each step by name: `Login:
started`, `bw serve locked`, `unlocked`, `synced`, `cache loaded`, `unlock
checked`, `note ensured`, `backups checked`, the wait for a lock that gave no
answer (see Stopping a `bw` process), and a refusal (`Login: refused, ...`),
never a value. Uvicorn writes its request line
only when the answer starts, so for a request that never answers the gateway
logs `POST /api/session for ACCOUNT starts` (and the same for DELETE), and
`answered STATUS` once the worker answered. So a frozen login shows where it
stopped. The end of a session logs each step the same way: `Session end:
started, REASON` (timeout, logout, a new login, bw serve restarted), the
commit's result (`Session end: commit done, ...`, `commit could not run`,
`commit given up after 60 s`), the stop of a running background backup
(`Session end: stopping the background backup before its next step`, then
how long it waited, see Stopping a `bw` process), `Session end: bw serve
locked` (or the warning of a lock that failed or gave no answer), `Session
ended`, then for a
worker `The session ended, exit requested`. A stop that gives up on the
commits logs `Stop: the commits did not finish within 60 s, cancelling them`.

## Gateway

The gateway (`backend/src/bwtidy/gateway/`, entry point `bwtidy-gateway`,
a FastAPI app under uvicorn on `BWTIDY_HOST` and `BWTIDY_PORT`) is the
one process that listens. It starts the workers (only for an unlock or a
Bitwarden login, see Workers start on a click) and proxies to them. It
never parses, logs or stores anything that passes through the proxy. It
logs account keys, uids, worker starts and exits, when a session request
starts and what status it got (see Worker mode, Logs), and a request a worker
did not answer (see Proxy).

The image's `CMD` is the gateway and runs it as root (`USER 0`): it needs
root to start each worker as the account's uid. `compose.yaml` (how to run it
is in docs/how-to/run.md) drops every capability but `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETUID`,
`SETGID` and `KILL` (it makes and owns the account directories, switches to
their uids and stops the workers), sets `no-new-privileges` and makes the
root file system read-only (the two volumes and a tmpfs on `/tmp` are the
only places that take writes).

Settings, all from the environment: `BWTIDY_ACCOUNTS_DIR` (default
`/data/accounts`), `BWTIDY_BACKUPS_ROOT` (unset by default, see Backups root
below), `BWTIDY_UID_BASE` (default 70000: a host's own service uids
often take the range from 10000 up, a value below 1 stops the gateway at
its start), `BWTIDY_ALLOWED_SERVERS`
(comma separated server URLs that skip the address check below, the dev
stack's `https://vaultwarden`), `BWTIDY_SECURE_COOKIES`,
`BWTIDY_STATIC_DIR`, `BWTIDY_DOCS_DIR`, `BWTIDY_COMMIT`, `BWTIDY_MAX_PENDING` (default 2)
and `BWTIDY_MAX_WORKERS` (default 8, see Caps below), and
`BWTIDY_GATEWAY_SWITCH_UID` (default 1: the gateway refuses to start unless
it is root, 0 only for the unit tests, which cannot switch uids). `PATH`, `NODE_EXTRA_CA_CERTS`,
`BWTIDY_IDLE_EXIT`, `BWTIDY_MCP_HOSTS`, `BWTIDY_HEARTBEAT_TIMEOUT`,
`BWTIDY_SESSION_IDLE_TIMEOUT`, `BWTIDY_BACKUP_KEEP_NEWEST`,
`BWTIDY_BACKUP_KEEP_DAYS`, `BWTIDY_BACKUP_KEEP_ALWAYS` and
`BWTIDY_FAILED_KEEP_HOURS` are passed on to the
workers when the gateway has them (the session timeouts, the backup retention
and the failed entries' age limit so a deployment's policy reaches every
worker, see Session and access, Backups, Retention and MCP and the queue, the idle exit so a test can shorten it, the MCP hosts
so the public host name passes the worker's DNS rebinding check, see MCP and
the queue).

- Account key: a server and an email.
  - The server is an https URL without user info, query or fragment, with an
    empty path or `/`, lowercased: `https://host`, plus `:PORT` for a port
    other than 443. The directory name is the host, plus `_PORT` for such a
    port. Unless the server is in `BWTIDY_ALLOWED_SERVERS`, every address
    the host resolves to (`loop.getaddrinfo`) must be global (`ipaddress`
    `is_global`: no private, loopback, link-local or documentation
    addresses), not multicast, and outside `64:ff9b::/96` and `64:ff9b:1::/48`
    (NAT64), `::/96` (IPv4 compatible), `::ffff:0:0:0/96` (IPv4 translated),
    `2002::/16` (6to4) and `2001::/32` (Teredo), because `ipaddress` counts some
    of them as global but they lead to an IPv4 address, private ones too, else
    422 `SERVER does not resolve to a public address.` A host that does not
    resolve gets the same answer. Reason: any https
    server, but nothing on the private network or in the container's network.
  - The check runs again right before bw login: a `POST /api/bw-login`
    (exactly that path) routed to a worker whose server is not in
    `BWTIDY_ALLOWED_SERVERS` is checked first, and on a refusal the
    gateway answers 422 with the same text and never forwards it. Known
    limit: bw resolves the name itself afterwards, so a DNS rebinding server
    can still win the race after the check and make bw connect to a private
    address. The second check only narrows that window. The rest of the login
    (`/api/bw-login/output`, `/input`, `/finish`) is not checked again.
  - The email is lowercased, at most 254 characters, matching
    `^[a-z0-9_%+-][a-z0-9._%+-]*@[a-z0-9.-]+[.][a-z]{2,}$` and without `..`:
    the local part does not start with a dot, as a directory name with a
    dot in front is one of the gateway's temporary ones. Else 422.
  - The account directory is `ACCOUNTS_DIR/HOST/EMAIL`. A path that resolves
    outside the accounts directory or through a symlink is refused (422).
- Account directory: `app/` (`BWTIDY_DATA_DIR`, SQLite, see Schema
  versions, `backups/` without a backups root, `mcp-token.sha256`, `bw-login-state` and, once Delete my account ran, `delete-requested`), `bw/` (`BITWARDENCLI_APPDATA_DIR`),
  `run/` (`app.sock`, `bw.sock`), `home/` (`HOME`), `worker.log` (the
  worker's stdout and stderr, appended), and `.pending` until the first
  unlock (see Worker mode). It is made at the account's first Bitwarden
  login, not when the account form opens it. Everything is owned
  by the account's uid and gid (the gid is the uid), mode 0700, made by the
  gateway as root. The directory is made under the temporary name
  `.new-EMAIL` beside its final name and renamed into place once its uid
  owns everything in it, so an interrupted creation leaves no account
  directory: a leftover `.new-EMAIL` is no account (a name with a dot in
  front never is) and the gateway deletes it at its start. The accounts
  directory and the host directories are mode
  0711: a worker can enter them to reach its own directory but cannot list
  the other accounts.
- Backups root: with `BWTIDY_BACKUPS_ROOT=ROOT` an account's backups live in
  `ROOT/HOST/EMAIL` (the same host and email parts as its account directory,
  with the same checks) instead of its `app/backups`, so `ROOT` can be any
  mount apart from the account data: another disk, a network share, a
  directory a backup tool reads. Unset, nothing changes. `compose.yaml` sets
  it to `/backups`, its own volume.
  - Before each worker start, under the account's lock, the gateway makes the
    directory as root the way it makes an account directory: `ROOT` (at the
    gateway's start) and the host directory mode 0711, the account's mode
    0700, owned by the account's uid and gid (also when it was there
    already: an account made anew gets a new uid, and the files of the old
    one in it are given to the new uid). It hands the path to the worker as
    `BWTIDY_BACKUPS_DIR`.
  - Never through a symlink: when the host directory or the account's
    directory is a symlink or not a directory, the worker is not started
    (503 `The worker could not start.`, the reason in the gateway's log).
    `ROOT` itself may be a symlink, the deployment chose it. Reason: the
    account's uid owns its directories and could point a symlink at another
    account's backups, which the gateway reads and writes as root.
  - A pending account directory that is deleted takes its directory under
    the root with it, unless that holds backups: they can be from before its
    account directory was deleted by hand, and the root is meant to outlive
    it.
  - `ROOT` and `BWTIDY_ACCOUNTS_DIR` must not be one inside the other (with
    symlinks resolved), else the gateway stops at its start.
- uid: the owner of the account directory. A new account gets one more than
  the highest owner among all account directories (pending ones too) and all
  directories under the backups root (one can outlive its account directory,
  so its uid is never handed to another account), at
  least `BWTIDY_UID_BASE + 1`. The uids have no passwd entries. The gateway
  refuses an account directory owned by root, by a uid at or below
  `BWTIDY_UID_BASE` or by the owner of another account directory (422 `The
  account directory is not usable.` at the unlock or the login, no worker
  starts, the reason in the gateway's log), and logs every such directory at
  its start. Whatever the base, no worker starts as uid 0 or below (503 `The
  worker's uid is not usable.`).
- Routes of the gateway itself:
  - `GET /api/health` (and `/api/health/`) answers `{"status": "ok",
    "version": ...}` from the gateway, never from a worker.
  - `GET /gateway/account`: what the page needs before a worker runs, for
    the browser's routing cookie: `{account: {server, email}, running,
    exists, pending, loggedIn}`. `running`: the account's worker runs and its
    `/api/health` answers 200 (one on its way out does not count). `exists`:
    the account directory exists, `pending`: it has `.pending`. `loggedIn`:
    the line of `app/bw-login-state` (see Worker mode), read with the rules of
    the MCP token file (a regular file owned by the account's uid, reached
    without a symlink, at most 64 bytes, see MCP below). Without a file to
    trust (none yet, a symlink, another owner, another content) the account
    counts as logged out. No directory: all three false. Without a routing cookie `account` is null and the rest false.
    `account` stays after the worker exited.
  - `POST /gateway/account {server, email, create}`: validates and sets the
    routing cookie, answering like `GET /gateway/account`. It starts no
    worker and makes no directory. A missing directory answers 404
    `{"exists": false}` unless `create` is true. With `create` the cookie is
    set for the account without a directory: its first Bitwarden login makes
    it. 422 for a server, an email or a directory it refuses. The browser's
    old routing cookie, if any, is forgotten: one account per browser
    profile.
  - `DELETE /gateway/account` (204) forgets the routing cookie, so the
    browser can log in as another account. The worker keeps its own session
    rules (it exits after its session, see Worker mode). Only the worker of
    an account that still has `.pending` is stopped (SIGTERM), and the answer
    waits until it exited and its directory is gone: the browser left the
    account before its first login succeeded, and the account form offers to
    create it again at once.
- Workers start on a click: `POST /api/session` (the unlock) and `POST
  /api/bw-login` (the start of a Bitwarden login), exactly these, with a
  routing cookie are the only requests that start the account's worker when
  none runs. Under a lock per account key the gateway starts it (unless it
  runs), waits until its `/api/health` answers (see Workers), then passes the
  request on unchanged. A running worker whose `/api/health` does not answer
  200 (its session ends and it exits) is waited for (outside the lock, up to
  30 seconds, then 503) and a new one started. The start of a Bitwarden login
  of an account without a directory makes the directory with `.pending` in
  it first, after the server's address check (see Account key). An unlock of
  an account without a directory answers 401 `{"detail": "No account"}`. 503
  `{"detail": ...}` when the worker cannot be started (`The worker could not
  start.`), exits or does not answer in time, 429 when a cap is reached (see
  Caps). Reason: opening the account (the form, or the page with a remembered
  account) must not start a worker, about 300 MB for up to 10 minutes
  without any credential.
- Caps: anyone who reaches the gateway (on a deployed instance, anyone on
  its private network) can
  click Log in for a new account or Unlock for an existing one without the
  master password, and each click starts a worker as root's child for up to
  10 minutes. So:
  - When `BWTIDY_MAX_PENDING` account directories with `.pending` exist, a
    Bitwarden login that would make a new directory answers 429 `Too many
    new accounts wait for their first login. Try again in a few minutes.` An
    existing pending account still starts its worker.
  - When `BWTIDY_MAX_WORKERS` workers run or are starting, starting one
    more (for a new or an existing account) answers 429 `Too many accounts
    are open at once. Try again later.` This is checked before a new
    directory is made, so a refused login leaves nothing behind.
  - A worker counts from the moment its start begins until its start fails
    or it exits. Each check and the reservation it guards happen without an
    await in between, so concurrent clicks for different accounts cannot
    pass a cap together.
  - No timeout on proxied requests (only the gateway's shutdown cuts them
    off, see below): reaching a worker needs a routing cookie or an MCP
    token, and logins, backups and MCP streams are long on purpose.
- Routing cookie `bwtidy_account`: 32 random bytes, HttpOnly,
  SameSite=Strict, Path=/, no Max-Age, Secure with `BWTIDY_SECURE_COOKIES=1`,
  mapped in RAM to the account key. A gateway restart forgets the map, the
  frontend logs in again. The cookie names the account, not the worker: it
  reaches the account's next worker too.
- Workers: started as root with the `bwtidy` entry point next to the
  gateway's own Python, dropping to the account's uid and gid (`user`,
  `group`, `extra_groups=[]`, umask 0o077, a new session), with an
  environment built from scratch: `HOME`, `BWTIDY_SOCKET`,
  `BWTIDY_BW_SOCKET`, `BWTIDY_DATA_DIR`, `BITWARDENCLI_APPDATA_DIR`,
  `BWTIDY_ACCOUNT_SERVER`, `BWTIDY_ACCOUNT_EMAIL`,
  `BWTIDY_EXIT_AFTER_SESSION=1`, `BWTIDY_PENDING_MARKER`,
  `BWTIDY_SECURE_COOKIES`, `BWTIDY_BACKUPS_DIR` with a backups root, plus `PATH`, `NODE_EXTRA_CA_CERTS`,
  `BWTIDY_IDLE_EXIT`, `BWTIDY_MCP_HOSTS`, `BWTIDY_HEARTBEAT_TIMEOUT`,
  `BWTIDY_SESSION_IDLE_TIMEOUT`, `BWTIDY_BACKUP_KEEP_NEWEST`,
  `BWTIDY_BACKUP_KEEP_DAYS`, `BWTIDY_BACKUP_KEEP_ALWAYS` and `BWTIDY_FAILED_KEEP_HOURS` as the gateway has them. Cwd is the account directory,
  stdout and stderr go to `worker.log`, which the gateway opens without
  following a link and without blocking, refusing to start the worker (503)
  when it is not a regular file owned by the account's uid, or a FIFO or a
  directory (a missing one is made, mode 0600, owned by the uid). The gateway waits up to 60 seconds
  for `/api/health` on `run/app.sock`, else it kills the worker (its whole
  process group, as at the stop below) and answers 503. One worker per
  account at a time.
- When a worker exits the gateway forgets it. When its directory holds the
  delete marker `app/delete-requested`, the account asked to be deleted (see
  Settings, Deleting data): the gateway deletes the account directory and its
  directory under the backups root even with backups in it, and forgets the
  browser's routing to it the way `DELETE /gateway/account` does. The marker is
  trusted only the way the MCP token file is: a regular file owned by the
  account's uid, reached without a symlink, small, so a worker can ask the
  gateway to delete nothing but its own account, never through a symlink. A
  worker that exits without the marker changes nothing, and a new worker already
  running in the directory keeps it (a worker deletes a marker it finds at its
  start, so the account stays when it was opened again at once). If its directory still has
  `.pending` the directory is deleted, unless a new worker already runs in
  it: the login never succeeded. A worker that cannot be started at all (its
  process does not spawn) leaves no `.pending` directory either, the click
  answers 503 and an account past its first unlock keeps its directory. At its
  start the gateway deletes every account directory that has `.pending`. A
  directory is only ever deleted directly at `ACCOUNTS_DIR/HOST/EMAIL`,
  never through a symlink. Its directory under a backups root goes with it
  (see Backups root). At the gateway's shutdown it waits up to 10
  seconds for the requests in flight (an MCP stream stays open for good), cuts
  off the rest (a worker still finishes what it runs), then every worker gets
  SIGTERM (its clean shutdown commits, ends the session and stops bw serve)
  and is killed when it still runs 120 seconds later. The kill goes to the
  worker's whole process group, so its bw serve (the unlocked vault in RAM)
  and a running CLI command die with it, never outliving the worker as the
  account's uid. Only the kill goes to the group: the SIGTERM goes to the
  worker alone, whose clean shutdown needs bw serve. `compose.yaml` gives
  docker's stop 150 seconds (`stop_grace_period`, docker's default is 10):
  more than those 10 and 120 plus bw serve's 5, so docker never kills the
  container while a worker stops cleanly. Reason: a docker kill would hit
  every `bw` at once (see Worker mode, Stopping a `bw` process). The 120
  seconds are meant for a commit with a fresh backup, a background backup the
  session end waits for, and that commit.
- Proxy: every request under `/api/` (except `/api/health` and
  `/api/health/`) with a routing
  cookie of a running worker goes to that worker over its
  socket (httpx's `AsyncHTTPTransport(uds=...)`, used directly: a client
  would keep a cookie jar and carry one browser's session cookie into
  another browser's requests), streamed both ways, with the hop-by-hop
  headers dropped and the `bwtidy_account` cookie taken out of the Cookie
  header. Each request gets a connection of its own, the transport keeps no
  idle one. Reason: the worker's uvicorn closes a connection that idled 5
  seconds, and a request sent on a kept connection just as the worker closes
  it is lost. A new connection on a unix socket costs next to nothing. A
  request the worker does not answer (its socket refuses it, or the
  connection closes before the answer's head) answers 502 `{"detail": "The
  account's worker does not answer"}`, and the gateway logs the worker's pid,
  how long ago it started, whether its process still runs, whether the
  connection was new and httpx's error, never the path or a body. Every
  `Set-Cookie` of the worker's answer that sets `bwtidy_account` is dropped
  too, other cookies pass: a worker cannot change the browser's routing
  cookie. Without a routing cookie, and without a running worker for anything
  but the two clicks above, those paths answer 401 `{"detail": "No account"}`,
  so the frontend opens the account again and shows the unlock or the
  Bitwarden login. Every other path is the gateway's: its own routes, then
  the documentation site at `/docs/` from `BWTIDY_DOCS_DIR`, then the static
  files from `BWTIDY_STATIC_DIR` (mounted last, like the single-user app
  does). The workers get no static directory, so the frontend always comes
  from the gateway, also for a browser with a routing cookie.
- The documentation site: the gateway serves the site in `BWTIDY_DOCS_DIR`
  (`/app/site` in the image) at `/docs/` to anyone, without a routing cookie
  or a login. `/docs` redirects to `/docs/`, a missing page is a 404 of the
  site, never the frontend, and no request under `/docs` reaches a worker.
  FastAPI's own API pages (`/docs`, `/redoc`) are off. Without
  `BWTIDY_DOCS_DIR` there is no site. How the site is built is in
  [architecture.md](architecture.md), The container.
- MCP: an MCP client has no routing cookie, so a request to `/mcp` (or
  below it) is routed by its `Authorization: Bearer TOKEN` alone, a routing
  cookie plays no part. The gateway hashes the token with sha256 and looks
  for the account directory whose `app/mcp-token.sha256` holds that hash
  on its first line (compared with `hmac.compare_digest`, the files read as
  root on every request, there are few). A token file counts only when it is a regular
  file owned by the account's uid and reached without a symlink, so a
  worker cannot point the gateway at another account's hash. The same rules
  hold for `app/bw-login-state` and the delete marker `app/delete-requested`
  (see below), the only other files the gateway reads from an account
  directory. When two
  accounts hold the same hash, neither is used.
  - No token, no match: 401 with the worker's own text, `bwtidy MCP needs
    a token. Create one in the web UI.`, and `WWW-Authenticate: Bearer`.
  - A match whose token expired (the file's second line, see MCP and the
    queue): 401 with the worker's own text, `bwtidy MCP token expired.
    Create a new one in the web UI.`, and `WWW-Authenticate: Bearer`, never
    naming the account, whether or not its worker runs. The hash is
    compared first, then the time. A file whose expiry cannot be read holds
    no token.
  - A match whose worker is not running (logged out, the session ended, the
    gateway restarted): 503 `bwtidy is locked for this account. Log in in
    the web UI.` (plain text). An MCP request never starts a worker.
  - A match with a running worker: the request is proxied there unchanged
    like the browser's requests above, the `Authorization` header with it,
    and the worker checks the token again.

## Schema versions

- Each account's `app/bwtidy.sqlite` holds its schema version in SQLite's
  `PRAGMA user_version`. The schema has one version, 1: `SCHEMA` in `db.py`,
  the tables `change_log` (with the index `change_log_at` on `change_log
  (at)`, which the Change log's filters sort and filter by), `queue`,
  `settings` and `backup_fingerprints`.
- A new file (version 0 without tables) gets the schema whole, in one
  transaction, and is set to version 1.
- A worker opens only a file at version 1. Any other version fails its start
  with a log line that names the file, its version and version 1, and
  nothing in such a file changes. The browser sees that the worker could not
  start, as for any other reason.

## Session and access

- Login form takes the master password. `POST /api/session` unlocks
  `bw serve` (`POST /unlock`). The password and the session key stay in RAM
  only, never on disk, never in logs. The field has an eye button at its end
  ("Show master password", then "Hide master password") that switches it
  between hidden and plain text. The password is hidden again when the form is
  sent or fails. The field keeps its name and `current-password`
  autocomplete, so password managers fill it. Only these login pages invite
  them, see Grid for the inputs inside the app.
- Once the vault is unlocked and the session started, the unlock's side
  effects follow: the background backup (see Backups), the CLI's login state
  (`bw-login-state`) and the deletion of the worker's `.pending` (see Worker
  mode). They never fail the unlock: a failure is logged with its type and
  the login still answers the cookie.
- The session key is `data.raw` of the unlock response. Backups pass it to the
  CLI. Every unlock (`bw serve` or `bw unlock`) and `POST /lock` invalidate
  the keys handed out earlier, so nothing else may unlock or lock bw in the
  container while a session runs.
- The session is a cookie `bwtidy_session`: 32 random bytes, HttpOnly,
  SameSite=Strict, Path=/, no Max-Age (plus Secure in a worker with
  `BWTIDY_SECURE_COOKIES=1`). One session at a time, a new login
  replaces the old one, but only with the running session's master password:
  - While a session runs, `POST /api/session` (and `POST
    /api/bw-login/finish`) checks the password against a hash of the running
    session's one first: scrypt (`hashlib.scrypt`, N 2^17, r 8, p 1, a random
    16 byte salt, made at the login), run in a thread outside the session's
    lock and compared with `hmac.compare_digest`. A wrong one answers 401
    `Wrong master password` and changes nothing: no commit, no lock of bw
    serve, the session and its cookie stay. A right one replaces the session.
    Without a running session the unlock of bw serve checks it.
  - After 3 wrong passwords in a row against a running session, each further
    try (also with the right password) is refused with 429 `Too many wrong
    passwords. Try again in N s.` and `Retry-After: N` until N seconds after
    the last wrong one: 1, then doubling up to 60. A right password (and every
    new session) starts the count again. The count is per worker, not per
    address: the gateway and the proxy or VPN in front of it hide the address. Checks run one at a
    time, so parallel guesses cannot pass the wait together.
  - Reason: anyone who reaches the gateway and knows an account's server and
    email gets a routing cookie for it (see Gateway). Only the logged in
    browser (its session cookie) and an MCP client with the account's token
    may end a session or log the CLI out. The hash is slow so the check is no
    fast oracle for guessing the master password, and it is a hash of its own
    because every unlock of bw serve invalidates the running session's key.
  - A refusal with a session running takes the hash's time, one without
    takes bw serve's unlock time, so the timing says whether a session runs
    (accepted). When the master password changed on the Bitwarden server
    during a session, a login with the new one is refused until the session
    ends (log out, or let it time out).
- Every open tab sends `POST /api/session/heartbeat` every 30 seconds. When no
  heartbeat arrives for 120 seconds (all tabs closed) the session ends.
  `BWTIDY_HEARTBEAT_TIMEOUT` (seconds, default 120) changes that. It must
  stay above the frontend's 30 second heartbeat, so less than 60 is refused.
  Reason: closing the tabs should log the user out. Basic auth cannot
  do that, browsers keep its credentials until they quit.
- 2 hours without user activity ends the session too
  (`BWTIDY_SESSION_IDLE_TIMEOUT`, seconds, default 7200). Heartbeats do not
  count as activity, nor requests the frontend makes on a timer (they carry
  the header `X-Bwtidy-Background: 1`, like the queue badge's poll). Every
  other API request does.
- Ending a session (timeout, logout, a new login with the right password,
  process exit, MCP's `end_session` or `log_out_of_bitwarden`): commit the
  pending `ui` entries (see MCP and the queue), then lock `bw serve`
  (`POST /lock`), wipe the password and session key from RAM, disable MCP.
  A worker with `BWTIDY_EXIT_AFTER_SESSION=1` then exits, except when a new
  login replaced the session (see Worker mode). Ahead of the lock a running
  background backup stops before its next step, and the `bw` it runs is
  waited for (see Worker mode, Stopping a `bw` process). The end never waits
  long on bw: a commit still running after 60 seconds is cancelled (the
  entries not written stay pending, the one being written fails as
  interrupted), a background backup still running after 30 seconds is
  cancelled (its `bw` gets SIGTERM), and a lock without an answer after 10
  seconds is given up. The end goes on either way, and an exiting worker's
  stop of bw serve takes the vault out of its RAM. A lock given up on still
  runs in bw serve, so the next login waits up to 20 seconds more for it
  before it unlocks, and answers 503 when it still runs (see Worker mode,
  Stopping a `bw` process).
- MCP works only while a logged in tab is open, on purpose.
  An MCP client can also end the session (see MCP and the queue, Session
  tools).
- When `bw serve` exits, the backend starts it again and unlocks it with the
  password from RAM, so a running session survives. If that unlock fails, the
  session ends.
- The CLI must be logged in before anything can be unlocked, and `bw serve`
  does not even start while it is not (bw exits at once with "You are not
  logged in."). So `GET /api/session` always carries `bwLogin`
  (`loggedIn`, `server`, `email`), and the frontend shows a Bitwarden login
  instead of the unlock form while `loggedIn` is false. `loggedIn` comes from
  bw serve's status when it answers and knows a server, else from the CLI's
  own `bw status`. Behind the gateway, while the account's worker does not
  run, the page asks no worker: `loggedIn` is the gateway's (`GET
  /gateway/account`, from the file the last worker wrote, see Gateway) and
  the server and email are the account's (see The account form). `server` and `email` are what the CLI reports, else
  `https://vault.bitwarden.com` and an empty email. bwtidy saves no server
  or email. The Bitwarden login form
  prefills Server and Email from the browser's localStorage instead: the last
  values typed into the account form or the Bitwarden login
  (`bwtidy.accountForm`), else the account in use (`bwtidy.account`), else
  what the session sends. A worker's fixed account (`accountFixed`) always
  comes from the session. Log in writes the typed values to
  `bwtidy.accountForm`. Log in stays disabled
  while the server or the email is empty (bw login would wait for an email).
  The master password from the form is used for both the CLI login and the
  unlock, so the user types it once. Its field is the same as the unlock's,
  with the same eye button to show it.
- `POST /api/bw-login` (`server`, `email`, `password`) runs `bw config server
  SERVER` when the server differs from the CLI's (it is refused while logged
  in), then `bw login EMAIL --passwordenv BWTIDY_LOGIN_PASSWORD`, both in a
  pseudo-terminal, so the CLI asks its questions the way it does at a
  terminal. Even without two-step login, bitwarden.com mails a one-time
  code on the first login from a new device and the CLI asks for it. The
  password reaches the CLI only through the child's environment and stays in
  RAM for this one login. The answer is a `loginId` that every later call
  carries in the header `X-Bwtidy-Login`, 409 while a login runs or the CLI
  is logged in already, 404 for an unknown token. None of these routes
  answers 401, the frontend reads that as "locked". bw serve is stopped
  (with its restarts) before `bw config server` and started again when the
  CLI has exited, whatever its exit code, and on a cancel. Reason: a bw serve
  that starts while `bw login` runs reads the logged out state and writes it
  back, which drops the fresh login about one time in three (`bw login`
  exits 0, the CLI is unauthenticated afterwards and the unlock fails), most
  often after a wrong password, when the restarts come often.
- The console: `GET /api/bw-login/output?after=N` answers the `text` from
  offset N on, `next`, `running` and `exitCode`. The text is what a terminal
  would show. The CLI's prompts redraw their line for every typed character
  (erase line, cursor to column 1), and that is applied before a line is
  shown, so each prompt appears once with its answer. The master password and
  the session key (the `BW_SESSION="..."` lines and after `--session`) are
  replaced by eight bullets before anything is stored. Nothing the CLI prints
  and nothing typed into it is ever logged. `POST /api/bw-login/input` with
  `text` writes that line to the CLI, with `password: true` the kept password
  instead (its echo masked), 409 when nothing runs. `DELETE /api/bw-login`
  kills the CLI and forgets the password and the token.
- `POST /api/bw-login/finish`, only after exit code 0: starts bw serve again
  when it is not answering (it could not run while logged out), then unlocks
  exactly like `POST /api/session` (same cookie, same errors) and forgets the
  password and the token. Both are also dropped when the CLI exits with
  another code (once that exit was reported) and ten minutes after the start.
- `POST /api/bw-logout` needs the session cookie (401 `Not logged in`
  without a valid one, and nothing is logged out) and the header
  `X-Bwtidy-Request: 1` (403 without it): a page on another site cannot
  send a custom header without a CORS preflight, which bwtidy never
  answers. It ends the session like `DELETE /api/session`, stops bw serve (one
  that sees the CLI log out stays up but hangs on the next unlock), runs `bw logout` and starts bw serve
  again, which keeps retrying until the next login. MCP's
  `log_out_of_bitwarden` runs the same steps (`BwLogin.log_out`). Only the
  logged in browser or the MCP token logs the CLI out, so with no session
  running a log out of Bitwarden means unlocking first (see the UI below).
- UI: one Log out button in the app bar opens a dialog "Log out of bwtidy.
  Unsaved changes are written first." with a checkbox "Also log out of
  Bitwarden (the next login asks for the new device code again)", unchecked
  whenever the dialog opens, and Cancel and Log out. Unchecked, Log out ends
  the session (`DELETE /api/session`) and shows the unlock page of the same
  account: the CLI stays logged in, so the next login needs only the master
  password. Checked, it calls `POST /api/bw-logout` and shows the Bitwarden
  login of the same account: the next login runs `bw login` again, and
  bitwarden.com may ask for a new device code. Switching account does not
  need it (see The account form). When the session ended behind the page
  (`POST /api/bw-logout` answers 401 `Not logged in`, or `No account` or the
  502 of a worker on its way out when the worker exits with it, also after
  the retry), the CLI stays logged in and the page shows
  the unlock with the note "Unlock first to log out of Bitwarden" above the
  form, gone once unlocked: after the unlock, Log out with the box checked
  logs the CLI out. Any other failed log out of Bitwarden says so in the
  dialog and asks the backend for the session again. A 429 of the unlock
  shows its text (how long to wait). The choice sits inside the one Log out
  dialog, so there is no second button or menu entry for logging out of
  Bitwarden.

### The account form

Behind the gateway the page first needs the browser's account (see Gateway).

- Before anything else the frontend asks `GET /gateway/account`. With no
  account it shows the account form: Server (default
  `https://vault.bitwarden.com`) and Email, both remembered in localStorage
  (the last values typed), and Continue. A backend without the gateway
  answers 404 there, and the frontend goes straight to the session.
- Continue posts `/gateway/account`. A 404 `{exists: false}` opens a dialog
  "There is no bwtidy account for EMAIL on SERVER yet. Create it? It is
  kept only if the Bitwarden login succeeds." with Cancel and Create (posts
  again with `create: true`, the account's directory is made at its first
  Log in). A 422 shows its reason under the form.
- Neither the form nor the page starts a worker (see Gateway). With an
  account the page shows the Bitwarden login (server and email read-only, see
  Worker mode) or the unlock: from `GET /api/session` while the account's
  worker runs (`running` in the gateway's answer), else from the gateway's
  `loggedIn`. Under both the page shows "Account EMAIL" and a Switch account
  button.
- Unlock and Log in start the account's worker when none runs (the gateway
  does, on that request). Until the answer the page shows "Starting" with a
  spinner under the button, when the page knew no worker ran. A failed start
  shows the gateway's text under the form (429 at a cap, 503 when the worker
  did not start). After a failed unlock or login the page asks the gateway
  again (the click may have started the worker), and an unlock that failed
  for another reason than the password asks for the session again, so a CLI
  that turned out to be logged out shows the Bitwarden login.
- The account in use is remembered in localStorage too. When the gateway
  does not know the browser's account (it restarted and forgot the routing
  cookie, the browser was closed) the app posts `/gateway/account` with it
  by itself and shows the unlock, so a returning user types only the master
  password. A 404 then forgets it and shows the form.
- The worker exits with its session, so after a Log out (with or without the
  box) the app opens the account again before it shows the unlock or the
  Bitwarden login, without a worker. A 401 `No account` from the gateway on
  any request (the worker exited after a timeout) opens it again too, and
  the page shows what the gateway knows. The unlock, the start of a
  Bitwarden login and the log out of Bitwarden are tried once more after
  such a reopen when their request found no worker (401 `No account`, or 502
  from a worker on its way out). Nothing starts a worker for the log out of
  Bitwarden, and a worker started since runs no session, so it then answers
  401 (`No account` or `Not logged in`) or 502 (still on its way out) and the
  page asks for the unlock first (see Session and access, UI).
- The app bar shows the account: the email, plus the server's host in
  parentheses when it is not `vault.bitwarden.com`. The email stays
  selectable text, so it can be copied. The title (bwtidy) at the left of the
  bar opens the Vault page, the default page, like the Vault link. It is a
  button with a pointer cursor, with no text selection (the h1 stays for the
  page structure). Right after the title, on
  its baseline and outside its button (so the button's name stays), the bar
  shows the build commit faintly: only the short hash, like `d475d7f`, with
  `-dirty` when it is there, or the version alone (`0.1.0-alpha.1`) when the
  image knows no commit. It is 11px in the bar's own text colour at 35%
  opacity, so it follows light and dark mode and the bar colour of MCP mode
  (no fixed colour), and it can be selected. Hovering it shows the full
  version text as a tooltip (a `title` attribute), like `0.1.0-alpha.1
  (261116 d475d7f)`. Nothing shows until `GET /api/health` has answered, and
  the bar keeps its height in both layouts. Reason: the build is then in
  every screenshot, where the Version line of the About page alone is easy to
  miss. Switch account in the
  More actions menu ends the session (`DELETE /api/session`, which commits
  the unsaved edits first), calls `DELETE /gateway/account`, forgets the
  remembered account and shows the account form (prefilled with the last
  values typed). Switch account under the unlock does the same without a
  session. A reload then stays on the form.
- The other tabs of the browser profile follow. All tabs share the routing
  cookie, so a tab that kept the old account would send its ids to the new
  account's worker (a queue approval of entry N approves the other account's
  entry N, a templates save replaces the other account's templates). So a
  tab reloads when another tab changes the
  remembered account (a switch forgets it, the form opens another one) and
  when another tab logs out (Log out keeps the account, so it writes the time
  under `bwtidy.loggedOut` for the others). The reload drops what the tab
  had not saved yet, such as a templates draft or a settings form. Accounts
  are compared by server and email, and a write of the same account reloads
  nothing. A tab that finds its worker gone opens its account again only
  while the remembered account is still its own, else it reloads, so it
  cannot route the shared cookie back to the old account in the middle of
  another tab's switch.

## Coordinates

Every value in the grid has a coordinate, which is also its column name.

- Built-in leaves use their JSON path: `name`, `notes`, `login.username`,
  `login.password`, `login.totp`, `card.cardholderName`, `card.brand`,
  `card.number`, `card.expMonth`, `card.expYear`, `card.code`,
  `identity.<key>` (title, firstName, middleName, lastName, address1, address2,
  address3, city, state, postalCode, country, company, email, phone, ssn,
  username, passportNumber, licenseNumber), `sshKey.privateKey`,
  `sshKey.publicKey`, `sshKey.keyFingerprint`.
- `login.uris` is one multi-value column (the list of `uri` strings, each with
  its `match` setting kept). The grid, the search and every operation but
  `setUris` see the strings only, MCP shows each URI with its match (see MCP
  and the queue). Bitwarden's `match` is 0 domain, 1 host, 2 startsWith, 3
  exact, 4 regularExpression, 5 never, or null for the client's default, a
  setting of the browser extension that is not in the vault and that bwtidy
  never tries to read.
- Organizational columns: `type`, `folder` (folder name), `organization`
  (org name or empty for personal), `collections` (multi-value, names),
  `favorite`, `reprompt`.
- Custom fields: `fields.<name>`, with the name verbatim (spaces, dots and
  trailing spaces kept). Field names may contain dots, so a coordinate is never
  parsed by splitting on dots. Internally a coordinate is structured:
  `{"kind": "path", "path": "login.username"}` or
  `{"kind": "field", "name": "BIOS Password"}`. When one item has the same
  field name twice, the second is `fields.<name>#2`. A field name that itself
  ends in `#<digits>` always carries its occurrence (`fields.key#2#1`), so the
  display string parses back to the same coordinate.
- Read-only info columns: `attachments` (the file names of the item's
  attachments in the item's order, a list shown like `login.uris`, one chip
  per name in the grid cell, sorted by the first name, found by the search and
  not a value a merge can move), `passkeys` (count of
  `login.fido2Credentials`), `revisionDate`. A file name is not secret. Logs
  and backup errors still name an attachment by its id only, never by its
  file name.
- Never shown or written as leaves: `id`, `object`, `creationDate`,
  `deletedDate`, `passwordHistory`, `login.passwordRevisionDate`,
  `login.fido2Credentials` content, attachment metadata other than the file
  names (the id, size and url, and above all the url, which holds a token and
  is never shown). The grid row carries each attachment's id with its name,
  for a rename (see Grid), but no cell shows it. They survive every write
  untouched (see Writes).

## Secret values

Secret coordinates: `login.password`, `login.totp`, `card.number`,
`card.code`, `sshKey.privateKey`, and every custom field with `type` 1
(hidden).

- The grid shows secret cells masked, reveal on click. A text field's value in
  a secret column is no secret cell: it is shown plain with a mark (see Grid).
- The secret route answers with `Cache-Control: no-store`, so the browser keeps
  no copy of a revealed value.
- A revealed value whose answer arrives after its row left the view, or whose
  row left the view, changed item or column, or was hidden while the answer was
  pending, is dropped and never shown.
- MCP never returns a secret value, not even in an error message.
- No operation may move a secret value into a non-secret coordinate (`name`,
  `notes`, a text custom field, any built-in non-secret leaf). Such an
  operation is rejected with a clear error. Reason: the agent could read the
  value afterwards. An agent's proposal is the exception: it is refused
  silently (see MCP and the queue), so the refusal is no oracle.
- A non-secret value of an item never equals one of its secret values, in
  either order: a plain value equal to a secret value of the item is refused,
  and so is a secret value equal to one of its plain values (`name`, the folder
  name, `notes`, a non-secret built-in leaf, an entry of `login.uris`, a text
  custom field). The type, the organization, the collections and the info
  columns do not count. All values count, short ones too, only an empty one
  does not. It holds across the entries of one commit and against the queue's
  pending entries of the item (their hidden values decrypted in RAM for the
  comparison only), and again at write time on the item as bw serve has it
  then, which also catches a generated value. The refusal names both columns
  and never a value. Collisions already in the vault stay: only a write of one
  of the two equal values is refused, any other edit of the item passes. A
  folder's new name (`renameFolder`, its restore) becomes the folder of every
  item in it, those in the trash too, so it is checked against the secret
  values of each of them. Reason: without the rule two grid edits (in one
  commit or in two, in most orders) could make a login's username its
  password, a plain copy in the grid, in MCP answers and in SQLite.
- `name`, `notes`, `login.uris` and text custom fields are not secret. If a
  note holds a secret, the user moves it to a hidden field.
- Secret values never reach logs, the SQLite file, or test output. A queued
  UI edit of a secret column or a queued new item's secret value is in SQLite
  only as an age ciphertext (see MCP and the queue, Hidden values).
- A refused request body is never echoed: it can hold the master password or a
  one-time code. A validation error names the field and the problem only
  (`type`, `loc` and `msg`, the worker's and the gateway's FastAPI validation
  alike, without its `input` and `ctx`). The `msg` is
  pydantic's own only for the error types whose text never quotes the input.
  A tag error is rebuilt from the expected tags without the tag, our own
  validators raise an error of type `bwtidy` whose text we wrote, and every
  other type gets one fixed text. The hand-parsed sections (operations,
  templates, generator settings) follow the same rule.
- A person creating an item may type its secret values (`secretValues`, see
  Writes, `createItem`), MCP never gives one. A secret field left blank starts
  empty and the user fills it in the grid afterwards, like any edit. A
  `login.password` or a hidden field can get a value from bw's generator at
  write time instead, which nobody sees until the user reveals it in the grid
  (for MCP: blank or generated, never a value it gives or can read). There
  are no placeholder values. The secret fields left empty go on the to-fill
  list (see The tool note), so nothing is forgotten.
- `generateLeaf` (see Writes) replaces a value with one from bw's generator.
  The value goes from bw serve's answer into the item and nowhere else. The
  queue and the change log show `(generated)`.
- An example of the generator (`POST /api/generator/example`, see Settings) is
  no value of an item. It is answered to the dialog that asked and never
  stored or logged, and the frontend keeps it in component state only.

## Writes

- Every write goes through one serialized write path in the backend (one at a
  time).
- Before every operation: `POST /sync`, reload the affected items, and abort
  if an item's `revisionDate` differs from the one the operation was based on.
  Reason: other people and the user's other devices edit the vault too.
- Two `revisionDate`s are compared as instants, not as strings
  (`revision.py`): at most 1 ms apart is the same revision, not a conflict,
  and a value that is no timestamp (none, empty) equals only itself. Reason:
  the server keeps more digits than the 3 of bw serve's answers
  (bitwarden.com 7, Vaultwarden 6), so one instant can reach bwtidy in two
  spellings, cut and rounded, and a merge into an item nobody had changed
  would fail as a conflict because the MCP tools showed one spelling when it
  was proposed and bw serve answered the other at the write. The CLI of the
  image cuts on every path (a server's `...19.458839Z` is `...19.458Z` in bw
  serve's listing, `GET` and `PUT` answer alike). Every write gives an item a
  new `revisionDate`, and no two writes of one item are 1 ms apart. The same
  comparison checks right before each write and follows the own writes
  (below). bwtidy also keeps, for every item, the spelling bw serve answered
  first for a revision (in RAM while the app runs, item ids and
  `revisionDate`s only) and answers that one while bw serve answers the same
  instant spelled otherwise, so the grid, MCP and a backup's fingerprint (see
  Backups) see one spelling per revision.
- A folder, favorite or collection change made elsewhere is a conflict too,
  like any other change made elsewhere. The Bitwarden clients change an
  item's folder and favorite flag through a partial update without a new
  `revisionDate`, and Vaultwarden its collections (see Backups), so the
  `revisionDate` alone does not show it, and a `moveToFolder`, a `setLeaf` of
  `favorite` or a `setCollections` would overwrite the other device's choice
  without a word. So these operations carry what the grid showed, as
  optional fields: `shownFolderIds` (`{itemId: folderId or null}`, items of
  `itemIds` only), `shownFavorite` (a `setLeaf` of `favorite` only) and
  `shownCollectionIds` (compared as a set). The check is a conflict when the
  item now differs, unless bwtidy's own writes since turned the shown value
  into the one now (followed like the `revisionDate`s, below, as an edit made
  before a commit's reload still shows the folder from before). The conflict
  is the item with its expected and actual `revisionDate` (which may be the
  same) and `column` (`folder`, `favorite` or `collections`), and in the queue
  it fails only the entry that carried the shown value, another entry of the
  same item is tried again. The grid row has `folderId` and `collectionIds`
  for this (its cells hold the names), and `attachments`, one `{id,
  fileName}` per attachment in the item's order (left out when it has none,
  never the url, which holds a token, or the size), for the `attachmentId`
  and `shownFileName` of a `renameAttachment`. The grid sends all three (a removal of
  the favorite flag is a `setLeaf` of `false`), and the MCP tools
  `propose_move_to_folder` and `propose_set_collections` send the values as
  the agent's view held them when it proposed, so a change made elsewhere
  before the user approves fails the entry. An operation without these fields (a
  script) is checked by its `revisionDate`s only, and the
  write keeps a change made elsewhere to other values anyway, because it GETs
  the full item before the `PUT`.
- After the write's backup bwtidy always syncs once more and reloads, and
  runs the check again (the `revisionDate`s and the shown values) when the
  vault is not the one the check saw. Reason: a fresh backup takes long (a
  backup's own sync is at its start, its export runs for many seconds), and a
  change another client made in that time would be overwritten with no
  conflict, a partial one even with this check, as bw serve's copy of the
  item that the write GETs and PUTs does not hold it yet. That is one sync
  more per write, also when the newest backup is reused. A change to other
  items passes the check.
- Right before each item is written, its `revisionDate` in bw serve's answer
  to the `GET` is checked again against the one the operation was based on,
  and so are the shown values (following bwtidy's own writes, below, also
  those of earlier operations of the same batch), for every kind that writes
  an existing item, `trashItem` and the steps of `restoreItems` included.
  Reason: a sync inside the batch (the one after a create, after a restore's
  move into an organization or untrash, or a backup by hand) can bring in
  another client's change after the check, and the `PUT` on top of it would
  pass. A mismatch fails that operation as a conflict
  (the item with its expected and actual `revisionDate` and, for a shown
  value, its `column`, as in a 409), the operations before it stay written
  and the ones after it are not applied. An item without a `revisionDate` to
  check (a new one, the tool note of a section save, one in the trash) is not
  checked. A `GET` that answers a `revisionDate` one of bwtidy's own writes
  turned into a later one (see Own writes) is asked again, every 0.2 seconds
  for up to 5 seconds, before it is checked. Reason: right after its own `PUT`
  bw serve answers a `GET` of that item for a moment as it was before the
  `PUT` (its cache of the items reads its state file again only after the
  write's file lock is released), and the batch's next write of the item
  would fail as a conflict with its own earlier write, with expected and
  actual `revisionDate` the same. Such a `revisionDate` is never another
  client's change, as the server gives every write a new one. Any other
  answer is checked at once, and one still from before the own write after
  the wait fails as a conflict.
- The server refuses a write on a copy of the item older than its own, and
  bwtidy shows that as a conflict of the item. bw serve's copy, which the
  write GETs and PUTs, is only as fresh as its last sync, so a change another
  client made after that sync is not in it. bw sends that copy's
  `revisionDate` as `lastKnownRevisionDate` (the body's own is ignored), and
  Vaultwarden refuses the edit when its stored one is newer by 2 seconds or
  more ("The client copy of this cipher is out of date"), the official server
  when they differ by more than a second ("The item cannot be saved because it
  is out of date"), both with a 400. Both servers check a move into an
  organization too, but whether bw (which makes that request in its SDK)
  sends the date there is not verified. bwtidy knows the answer by those
  texts, syncs, and fails the operation as a conflict like a mismatch right
  before the write (the item with its expected `revisionDate` and the actual
  one after the sync): the other client's change stays, the operations before
  it stay written, the ones after it are not applied, and in the queue only
  its entry fails. Any other refusal fails the write with bw's text. An
  integration test with a second CLI login as another device proves it
  against Vaultwarden. The server does not check every change: a
  partial update of the folder or favorite flag (what the Bitwarden clients
  send for these), the collections, the trash and its restore, and on
  Vaultwarden an attachment, all pass on a stale copy, and Vaultwarden lets an edit
  less than 2 seconds after the item's last one through. For those only the
  checks above stand.
- An operation syncs before it reads the first item it writes when the
  batch's last sync (any of them: the one at its start, the one after the
  backup, the one before an earlier operation, the one after a create or a
  restore's move or untrash, the one after a refused write) started more than
  5 seconds ago. The check right before the write (above) then sees what the
  server holds now, so a folder, favorite or collections change another
  client made since is a conflict of the shown value instead of being
  overwritten without a word by the full `PUT` (or the collections write).
  Reason for the 5 seconds: every own write bumps the account's revision, so
  the next sync is always a full one, and a sync before every operation would
  make a commit of many queued single edits several times slower, as each
  queue entry is its own operation. A sync with nothing changed is cheap, a
  sync right after an own write costs a second or two, and a commit of a
  few dozen queued edits writes faster than that, so it rarely syncs more
  than once between its operations. After such a sync even a write without
  shown values (a `setLeaf` of the notes, say) keeps a folder or favorite
  change made elsewhere, as it `PUT`s the fresh copy. What is still not
  covered: a partial change made elsewhere after the last sync and before the
  write is overwritten, a window of up to 5 seconds plus the time the
  operation's own writes take (one operation can write many items). There is
  no check after the batch: by then the server holds bwtidy's values and the
  other client's change cannot be told from it. The sync is bw serve's `POST
  /sync` without `force`: bw first asks the server's revision date (`GET
  /accounts/revision-date`) and skips the full sync when it is not newer than
  its last sync, answering "Syncing complete." either way. That last sync
  time is the client's own clock, so a host clock ahead of the server's can
  skip a change made in between. bwtidy does not force anyway: `force` always
  syncs fully, which costs every sync with nothing changed about as much as
  one after an own write.
- Own writes: the write path remembers, for each item it wrote, the
  `revisionDate` before and after its write (from bw serve's answer to that
  very `PUT`, in RAM, cleared when the session ends). When an operation's
  expected `revisionDate` is a "before" of such a write, it is checked
  against the "after" instead, following the chain. So an edit made while a
  commit of the queue runs, or right after it before the grid reloaded,
  never conflicts with bwtidy's own commit. A "before" is found in any
  spelling of its instant (the comparison above), so a second edit of an item
  (a password set in the grid, then its notes removed on the same page)
  applies in every order and timing. Any other change still conflicts,
  whichever `revisionDate` the edit carries. Nothing that bwtidy did not
  write itself ever goes into the chain (a listing after the write could
  already hold someone else's change). The same writes remember the folder,
  favorite flag and collections the write found on the item (bw serve's
  `GET`) and the ones it left: a shown value holds when a run of them, each
  finding what the one before it left, leads from it to the value now.
- Every UI write is a `ui` entry of the queue (see MCP and the queue) but
  one: the tool note's rename back (`POST /api/tool-note/rename-back`, see
  The tool note) goes straight to the write path. `POST /api/operations` is
  for scripts and tests.
- Every operation (an operation may touch many items, it gets one backup, not
  one per item) has a full backup that matches the vault as it is right before
  the write, taken ahead of time in the background when possible (see
  Backups). No backup, no write.
- An item is written by `GET` of the full item, changing only the touched
  leaves, and `PUT` of the full object. Reason: unknown fields (passkeys,
  password history, attachments, fields added in future Bitwarden versions)
  must survive.
- Changing an item's type is never a `PUT` with a new `type`. It is: create a
  new item of the new type with the mapped values, copy folder, organization,
  collections, favorite and reprompt, re-upload attachments, then move the old
  item to trash. Reason: type change in place is not documented to work, and
  this path works on every server.
- Deletes only move to trash. Permanent delete is never offered. The one
  exception is the old attachment of a `renameAttachment` (below), deleted
  once its copy under the new name is listed: Bitwarden has no trash for an
  attachment, and the write's backup holds the file.
- Every applied operation is logged in SQLite (time, source `ui`, `mcp` or
  `mixed` (a batch with entries from both), operation, affected item ids, the
  ids of the items it created, the coordinates it wrote, result, backup file
  name). Values and error messages are never logged (either could carry a
  value). `GET /api/change-log` returns the
  latest 200 entries (see the filters below), the created items as `createdItemIds` (table column
  `created_item_ids`, a JSON list): the new item of a `convertItem`, of an
  `applyTemplate` across types, of a `moveToPersonal`, of a `createItem` and
  of a `restoreItems` that created an item anew. Reason: a restore of the row
  must find them. A `restoreItems` row also has `restoredBackup` and
  `restoredChangeId` (see Restoring a change log row), and so does a
  `restoreFolder` row. A row of a folder kind (`renameFolder`, `deleteFolder`,
  `restoreFolder`) has no item ids and names its folder in `folderId` (table
  column `folder_id`, null on every other row): the folder renamed, deleted or
  restored, for a restore that created the folder again the new one. A
  `renameAttachment` row names its attachments in `attachmentIds` (table
  column `attachment_ids`, JSON, null on every other row): `{"before": id,
  "after": id}`, the attachment it replaced and the copy that replaced it,
  `after` null when the write failed before a copy was kept.
- The coordinates are `columns` (table column `columns`, a JSON list), named
  like the grid's columns (`login.username`, `fields.Pin`): the `column` of a
  `setLeaf`, `removeLeaf` and `generateLeaf` (a rename of an item is a
  `setLeaf` of `name`), `folder` for a `moveToFolder`, `collections` for a
  `setCollections`, `organization` and `collections` for a
  `moveToOrganization` and a `moveToPersonal`, `attachments` for a
  `renameAttachment`, both names of a `renameField`,
  the sources and the target of a `mergeColumns`, the target columns of a
  `mergeItems` (the other item goes to the trash whole), `login.uris` for a
  `setUris`, and for a
  `convertItem` and an `applyTemplate` each mapped source and target plus
  every coordinate the plan changes on the item as the write found it (`type`,
  the old type's built-in leaves and the custom fields they become, the
  template's fields the item lacked), `folder` for the three folder kinds,
  plus `sshKey.publicKey` and `sshKey.keyFingerprint` when the write derived
  them (see below). The
  read-only columns are left out. A
  `trashItem`, `createItem`, `restoreItems` and `setToolNoteSection` work on
  whole items and log none. A custom field's name is vault content, and the
  log holds it anyway: knowing what each change touched is worth that, since
  the names are plain in the grid, in MCP answers and in the queue's diffs
  already.
- `GET /api/change-log` takes optional query parameters, all combined with
  AND: `from` and `to` (UTC instants, inclusive, a fraction of a second of
  `from` counts as the next whole second), `source` (`ui`, `mcp` or `mixed`),
  `kind`, `column` (a row matches when its `columns` hold any of them), `item`
  (a row matches when its `itemIds` or `createdItemIds` hold any of them),
  each of those four repeatable (a row matches any of the values given), and
  `result` (`ok` or `failed`). An unknown `source` or `result` or an instant
  that does not parse is a 422. The filter runs in SQL over the whole log,
  and the answer is the newest 200 rows that match (newest first, still a
  JSON list), with the header `X-Bwtidy-More: true` when more rows matched
  (`false` otherwise).
- `POST /api/operations` takes `{"operations": [...]}`, the whole list being
  one operation in the sense above: one sync, one check of every touched item
  (its revisionDate and the shown values), one backup, one more sync, then the
  writes one by one (each operation of the list syncing first when the last
  sync is older than 5 seconds, see above). 409 with the conflicting items
  (expected and actual revisionDate, and the `column` of a shown value) when
  an item changed or vanished, 422 for an operation that is not allowed, 500
  naming the step when the backup fails. Nothing is written in those cases. When a write fails
  midway, the response says which operations were applied, which failed and
  which are pending, plus the backup name and `conflicts` (the item the failed
  one found changed right before its write or the server refused as out of
  date, else empty), and the vault is reloaded. Kinds:
  `setLeaf`, `removeLeaf`, `moveToFolder` (a missing folder is created, an
  empty name means no folder), `setCollections` (organization items only),
  `moveToOrganization` (`POST /move/{itemId}/{organizationId}`, personal items
  only), `moveToPersonal` (organization items only), `trashItem`,
  `convertItem`, `applyTemplate` (see
  Templates), the normalizing kinds `renameField`, `mergeColumns`,
  `mergeItems` (see Normalizing), `setUris` (below), `restoreItems` (see Backups, Restoring
  a change log row), `createItem` (below), the folder kinds `renameFolder`,
  `deleteFolder` and `restoreFolder` (below), `renameAttachment` (below) and
  `setToolNoteSection` (below, bwtidy's own only). No other operation may touch the tool note (see The
  tool note).
- `renameFolder` (`folderId`, `shownName`, `newName`) gives a folder a new
  name (`PUT /object/folder/{id}` of bw serve), `deleteFolder` (`folderId`,
  `shownName`) deletes an empty folder (`DELETE /object/folder/{id}`).
  Reason: in Bitwarden itself, renaming a folder means moving every item and
  deleting the old folder by hand. Neither writes an item. A
  folder has no `revisionDate`, so `shownName` is its name as the client
  showed it, and a folder that has another name now or is gone is a conflict
  (409, `conflicts` holding `{folderId, expected, actual}` with the names,
  `actual` null for a gone folder), checked like an item's: at `POST
  /api/queue`, in the write path's check and right before the write on bw
  serve's folders (after the sync of an operation whose batch last synced
  more than 5 seconds ago), where a mismatch fails the operation as a
  conflict like an item's. In the queue a folder's conflict fails only the
  entries of that folder ("the folder changed since the change was queued"),
  and Retry with overwrite renames or deletes the folder under the name it
  has then (see MCP and the queue).
  Refused (422) for a rename: an empty name (also one of spaces only), the
  folder's name now, a name another folder has ignoring case (`another folder
  is named "X"`) and a name equal to a secret value of an item in the folder
  (see Secret values). Refused for a delete: a folder that holds an item,
  active (`the folder holds N items, move them out first`) or in the trash
  (Bitwarden would take the item out of the folder too). Both refusals are
  checked again at write time on bw serve's folders and items, so an item
  moved into the folder after the check fails the delete. Each write's answer
  goes into the cache and into the batch's own view of the folders, as bw
  serve lists a folder as it was before an own write for a moment (and one it
  created only after a sync), so a later operation of the
  batch sees the folders as written. The queue's diff of either is one row of
  the folder without an item id (`itemName` the folder's name now, column
  `folder`, `before` its name, `after` the new name or null), the summaries
  `Rename the folder "OLD" to "NEW"` and `Delete the folder "NAME"`.
- `setUris` (`items`, a list of `{itemId, revisionDate, shownUris, uris}`,
  each of `uris` and `shownUris` a list of `{uri, match}` with `match` 0 to 5
  or null, see Coordinates) replaces the whole URI list of each login with
  `uris`, in its order and with every match exactly as given. Reason: an
  agent can then see and fix the match settings, so a login that autofills on
  the wrong site or on none can be found and fixed without ever reading or
  writing a secret. Only MCP proposes it (see MCP and the queue).
  Refused (422) at `POST /api/queue`, in the write path's check and at write
  time alike: an item that is not a login (`only a login has URIs`), the tool
  note (like every operation on it), no items or one named twice, a uri
  empty after trimming (`URI N is empty`), a match other than 0 to 5 or null,
  and a uri with match 4 that Python's `re` cannot compile (`URI N is not a
  valid regular expression`). Bitwarden evaluates such a uri as a JavaScript
  regular expression, so the check catches only what Python rejects too. The
  messages name the URI by its position, never its text. A uri equal to a
  secret value of the item is refused like any plain value (see Secret
  values), checked after every other refusal, so an error never tells whether
  a uri equals a secret. `shownUris` is the item's list (each uri with its
  match, an entry without a uri left out) as the proposer was shown it, a
  shown value like `shownFolderIds`: when the item's list differs now, the
  item is a conflict with `column` `login.uris`, in the check and right before
  the write. Unlike a folder, bwtidy's own writes since do not make it hold:
  the URIs change with a new `revisionDate`, which follows bwtidy's own write
  (see Own writes), and the whole new list would undo a grid edit of the URIs
  committed after the proposal. Every item is checked before the first one is
  written. Retry with overwrite takes `shownUris` from the vault then, like
  the `revisionDate`s. The queue's diff has one row per item in `login.uris`,
  `before` and `after` the uri strings (null for none) as the grid shows
  them, plus `uris` with `before` and `after` the lists of `{uri, match}`,
  each match as its word (see MCP and the queue) or null. Its change log row
  names `login.uris`, the summary of a UI entry is `Set the URIs of "NAME"`
  (`Set the URIs of N items`).
- `renameAttachment` (`itemId`, `revisionDate`, `attachmentId`, `fileName`
  the new name, `shownFileName` the name the proposer saw) gives an
  attachment a new file name. Reason: with the attachment metadata in MCP an
  agent can name the attachment, bwtidy renames it when the user approves,
  and the contents never pass through MCP. Both an agent (MCP,
  `propose_rename_attachment`) and the user in the grid (Rename attachments
  in the cell menu, see Grid) queue it, a UI entry committed by AutoCommit
  like any UI edit: what MCP can do, the UI can do too.
  - In place is not possible through bw serve: an item `PUT` with another
    `fileName` in its `attachments` answers success, gives the item a new
    `revisionDate` and leaves the name as it was, also after a sync, and bw
    serve has no other route for it (the Bitwarden clients send attachment
    metadata with a cipher update only when keys rotate). bwtidy never calls
    the server's API around bw serve. So the write is: `GET
    /object/attachment/{id}?itemid=` (the bytes, in the write's RAM only,
    never logged or kept), `POST /attachment?itemid=` under the new name, a
    check that bw serve's answer lists exactly one new attachment with the
    new name and the old one's `size`, then `DELETE
    /object/attachment/{id}?itemid=` of the old one. The attachment gets a new
    id. When the upload or anything after it fails, the copy is deleted
    again and the entry fails, so the item keeps exactly one: while the
    delete of the old one has not started, at once (after a failed upload the
    copy the item lists anyway, found by its name and an id it did not have),
    and after it started only when the item still lists the old one (a
    delete that failed late may have happened, and then the copy is the one
    left). A copy that cannot be deleted again
    is logged by its id and both stay. On Vaultwarden neither the upload nor
    the delete changes the item's `revisionDate` (bw serve answers the delete
    with `{"success": true}` and no item). The own write is recorded like a
    `PUT`'s (see Own writes) from bw serve's answer to the upload and from a
    `GET` right after the delete, with no sync in between, so its copy of the
    item changed only by these two writes. An edit queued before the rename
    never conflicts with it.
  - Refused (422): the tool note, an `attachmentId` the item does not have
    (`the item has no attachment with the id ID`), an empty new name (also
    one of spaces only), one with a slash or a backslash (the backups file an
    attachment under `item-attachments/<itemId>/<fileName>`, and no system
    saves such a name), the name it has now, and the name of another
    attachment of the item (`the item has another attachment named "NAME"`, NAME as that
    attachment has it). Names are compared ignoring case (`str.casefold`), so
    a download of both to a file system that ignores case (macOS, Windows)
    never overwrites one with the other. Bitwarden itself allows two
    attachments of one name, bwtidy does not. Changing only the
    case of the attachment's own name (`Codes.TXT` to `codes.txt`) stays
    allowed as long as no other attachment of the item matches. Two renames
    of two attachments of one item to the same name end with the first
    written: queued in one request, the second is refused at once (422,
    checked on the item as the renames before it in the request leave it),
    queued in two requests, the second entry fails at the write, where the
    check runs again on the item bw serve answers right before it and writes
    nothing. The file name is no secret
    and an info column (see Coordinates), so the rule of equal values does
    not check it (see Secret values).
  - A conflict when the item's `revisionDate` differs, and when the
    attachment has another name than `shownFileName`: a rename elsewhere
    leaves the `revisionDate` as it is, like a folder's `shownName`. The
    conflict is `{itemId, expected, actual, column: "attachments"}` with the
    two `revisionDate`s, checked at `POST /api/queue`, in the write path's
    check and right before the write on bw serve's `GET`, and in the queue it
    fails only the rename's entry. Retry with overwrite takes the name the
    attachment has then. A second rename of the same attachment queued before
    the first is written fails, its attachment id is gone.
  - The queue's diff is one row of the item in the column `attachments`,
    `before` its file names now and `after` with the new one in place, the
    summary `Rename attachment "OLD" of "ITEM" to "NEW"`. The change log row
    has kind `renameAttachment`, the item in `itemIds`, column `attachments`
    and `attachmentIds`. Restore of the row renames the copy back, see
    Restoring a change log row.
- `setToolNoteSection` (`section`, one of `templates`, `toFill`, `generator` and
  `datatypes`, and `changes`, no `revisionDate`)
  saves one section of the tool note's notes (see The tool note). Only
  bwtidy's own code queues it (the templates save, the to-fill list, the
  generator settings, the datatypes save): `POST /api/queue`, `POST
  /api/operations` and MCP refuse it like every other operation on the note.
  At write time the write path GETs the tool note as it is then, saves only
  that section of its notes and PUTs the note (nothing when the notes stay as
  they are), so it never works on a stale copy and never conflicts on the
  note's `revisionDate`. Several such entries in one commit apply one after
  the other, each on the note as it is then, so none loses another's section.
  - `changes` is a list of changes of one entry each, applied in order to the
    section as that GET holds it, so two saves built on the same section both
    land, whatever order they are committed in. `generator`: `{"op":
    "setDefault", "options"}`, `{"op": "setItem", "itemId", "options"}` and
    `{"op": "removeItem", "itemId"}`. `toFill`: `{"op": "addColumn",
    "itemId", "column", "since"}` (`since` only for a new entry, an entry the
    item has keeps its own) and `{"op": "removeColumn", "itemId", "column"}`
    (the entry goes with its last column). `templates`, known by name:
    `{"op": "addTemplate", "template"}` (last in the list, refused when the
    name is taken ignoring case), `{"op": "setTemplate", "template"}` (the
    template of that name replaced in its place), `{"op": "renameTemplate",
    "name", "to"}`, `{"op": "moveTemplate", "name", "index"}` (to that place
    in the list, the last for a larger index, since the first of several
    matches wins) and `{"op": "removeTemplate", "name"}`. A change that names
    a template a save before it renamed or deleted fails its entry ("no
    template named 'X', a save before this one renamed or deleted it"), it
    never writes the old template back. `datatypes`, known by id:
    `{"op": "addDatatype", "datatype"}` (the id given when it is queued, see
    Datatypes), `{"op": "setDatatype", "datatype"}` (fails its entry when the
    datatype is gone) and `{"op": "removeDatatype", "id"}` (nothing when it
    is gone already, refused while a template of the note as it is then uses
    it). Templates have no id, they are known by name. Reason for changes
    rather than whole sections: two tabs that save templates or datatypes
    would otherwise lose each other's change, and a save of the whole section
    would undo another save built on the same base. Every item id is followed
    through the conversions this session committed (`convertItem`,
    `moveToPersonal`, an `applyTemplate` that changes the type, kept in RAM
    like the own writes), so a change queued before a conversion and
    committed after it lands on the new item instead of being written back
    under the old id and then dropped. A `generator`, `toFill` or `templates`
    section that does not parse starts over empty, as a whole save replaced
    it. The `datatypes` keep their broken entries, so the section still fails
    its check until a broken one is fixed or deleted. The `generator` write
    drops the settings of items that are gone (the trash counts, an item a
    conversion made since the batch began does not).
  The changes are checked against their section (options against the
  limits, columns as the to-fill list's parser takes them, a template or a
  datatype like the section's parser checks one) and the section they leave
  is checked by its parser as a whole: when they are queued, on the note as
  the saves under way of every section leave it (a change that does not
  apply any more is refused then, 422), in the check before the backup, on
  the note as the entries before it in the batch leave it (such an entry
  fails there and the rest goes on), and at write time on the note as that
  GET holds it.
  Every read of a section while saves of it are pending or being committed
  (the routes that say `saving`, the base of the next change, the queue's
  diff of an entry, which shows the saves queued before it applied) applies
  all of them over the note in queue order, the same way the writes will.
  Its change log row names
  the tool note in `itemIds`, so its Restore is refused like the note's.
  bw serve's answer to the note's `PUT` replaces the note in RAM at once, so
  once its entry is `applied` every read of the section (the routes that say
  `saving`, the template matches, the base of the next save) shows the saved
  value, also before the reload after the batch. Otherwise an applied save
  would read as `saving` false with the old section until that reload, and a
  save built on that read would be refused (422) for a name it held twice.
  The check when queued takes the saves of the other sections too, as a
  deleted datatype is checked against the templates: a datatype goes right
  after the save that removed the template using it, while that save waits,
  is being committed or is written. With the datatypes saves alone the cached
  note would still show the template and the datatype's removal would be
  refused (422) until the templates save was written.
- The secret rule for a `setLeaf`: the request carries only the value, so a
  value equal to one of the item's own secret leaves may not go into a
  non-secret column of that item. That is the one move a grid edit can make.
  The reverse holds too, a value for a secret column may not equal a plain
  value of the item (see Secret values). Both are checked at `POST /api/queue`
  on the item with its pending `setLeaf`, `removeLeaf`, `generateLeaf`,
  `setUris` and `renameField` entries and the earlier operations of the
  request applied, in the write path's check with the earlier operations of
  the batch applied, and at write time on the `GET` of the item. The same
  rule refuses a `moveToFolder` whose folder name equals a secret value of a
  moved item and a `generateLeaf` whose generated value
  equals a plain value of the item, the latter at write time, which fails the
  write.
  Changing an existing custom field never changes its type, a new field gets
  the requested type (text by default). A hidden `fieldType` for a field the
  item already has with another type is refused (422, nothing stored): the
  value would stay in the open as a text field, while a secret column's
  dialog promises secrecy. Both rules are checked in
  batch order, each `setLeaf` on the item as the `setLeaf`, `removeLeaf`,
  `generateLeaf` (a hidden field, its value unknown until the write) and
  `renameField` operations before it in the batch leave it (the secrets of
  the item before the batch still count), and checked again on the item the
  write GETs right before its `PUT`. So a hidden value for a field that an
  earlier operation of the same batch or commit creates as text is refused
  too, the entry fails with the reason and nothing of the value is written: Add field with a text
  field, then the secret dialog on that cell before the commit, never writes
  the secret as a text field.
- An SSH key item (type 5) gets its `sshKey.publicKey` and
  `sshKey.keyFingerprint` from its `sshKey.privateKey` whenever bwtidy writes
  it with a private key and one of the two empty: a `createItem`, a
  `convertItem` or an `applyTemplate` into an SSH key, a `moveToPersonal`, a
  restore that creates the item anew, and every `PUT` (a `setLeaf` of the
  private key from the grid's secret dialog, say). They are derived from the
  body right before its create or `PUT`. A value already there is kept, even
  one that does not match the key. The public key is the OpenSSH line without
  a comment (`ssh-ed25519 AAAA...`), the fingerprint `SHA256:` and the
  unpadded base64 of the SHA-256 of the public key blob, the form `ssh-keygen
  -lf` and the Bitwarden clients show. Read are Ed25519, RSA and ECDSA (P-256,
  P-384, P-521) keys in OpenSSH form or PEM (PKCS#1, SEC1 for ECDSA, or
  PKCS#8), with Windows line ends and spaces around them too. A key with a
  passphrase fails the operation with "the private key has a passphrase",
  any other text that is no such key with "the private key cannot be read",
  and nothing of that item is written. Neither text holds a character of the
  key, and neither the key nor the derived values are logged. Reason: bw
  refuses an SSH key without all three ("SSH key public key is
  required", "SSH key fingerprint is required", "SSH key private key is
  required") and fills in none of them itself, and MCP never gives a private
  key, so without this a secure note holding a key could not become an SSH
  key item. The derived columns are not secret. The change log row lists
  them among its `columns` (a `createItem` and a `restoreItems` list none),
  and the queue's diff of a `convertItem` or an `applyTemplate` shows them
  plain when the private key comes from the item itself (its notes or a
  field). A queued hidden value is a ciphertext, so the diff of a grid edit
  or a new item shows the masked private key only. A key that cannot give
  them fails at the write, never in the diff.
- `convertItem` maps `{target column: source column}`. A mapped custom field
  moves (it is dropped from the new item), mapped `notes` are cleared, `name`
  stays. Into an SSH key with `notes` (or a field) mapped to
  `sshKey.privateKey`, the new item gets its public key and fingerprint from
  that key (see above). Unmapped custom fields come along as they are. An unmapped built-in
  leaf of the old type becomes a custom field named after its key
  (`login.password` becomes `password`), hidden when the leaf is secret,
  `login.uris` joined with newlines. Passkeys and password history cannot be
  copied and are lost with the old item (it is in the trash and the backup).
  Attachments are downloaded (`GET /object/attachment/{id}?itemid=`) and
  uploaded again (`POST /attachment?itemid=`) after a `POST /sync`, because bw
  serve knows a created item only after a sync. The old item is trashed last.
  If anything after the create fails, the new item is trashed again.
- `moveToPersonal` (`itemId`, `revisionDate`) takes an organization item into
  the personal vault. Bitwarden has no call for it, so it works like
  `convertItem`: create the item anew with everything as it is (folder,
  favorite, reprompt, notes, custom fields, the type's own values) except
  organization and collections, which become empty, copy the attachments,
  then trash the old item. Passkeys and password history cannot be copied and
  are lost with the old item (it stays in the organization's trash and in the
  backup). Everyone who shares the item's collections loses it. Refused (422)
  for an item in the personal vault and for the tool note.
- `generateLeaf` (`itemId`, `revisionDate`, `column`) replaces the value of
  `login.password` of a login or of a hidden custom field (a missing one is
  created hidden) with a value from bw's generator. At write time it reads the
  `generator` section of the note as it is then, generates with the item's own
  setting or else the default, and sets the leaf, so bw keeps the old value in
  the item's password history. The value is never logged, stored or answered:
  the diff and the change log show `(generated)`, its before is masked. Refused
  (422) for any other column ("a generated value goes only into login.password
  or a hidden field"), for a field of another type and for the tool note. It
  checks the `revisionDate` like a `setLeaf`. MCP has no tool for it.
- `createItem` (`name`, exactly one of `type` and `template`, `folderName`,
  `organizationName`, `collectionNames`, `values`, `secretValues`,
  `generatePassword`, `generateColumns`) adds a
  new item. Not the tool's main purpose, but it makes it complete, and agents
  can propose new items too. The item is
  built from the type's empty sub object (the template's type for a
  template), the name, the organization and its collections (at least one),
  then a template's custom fields, created empty with their field types, then
  `values` (`{column: value}`, columns and values as `setLeaf` takes them, a
  custom field the template does not name becomes a text field), then
  `secretValues` (`{column: text}`, secret columns only, the one way a secret
  value comes in, a custom field the template does not name becomes a hidden
  field). An SSH key's empty public key and fingerprint are derived from the
  private key it was given (see above). Only a person sends `secretValues`: from the queue they come from a
  `ui` entry, encrypted while it waits (see MCP and the queue), and MCP has no
  way to send them. The folder
  is found by name or created, like `moveToFolder`, an empty name means none.
  With `generatePassword` (logins only) `login.password` is set at write time
  from bw's generator (`GET /generate` of bw serve, with the default of the
  `generator` section, see The tool note), never logged, stored or answered.
  `generateColumns` does the same for any
  column it lists, `login.password` of a login or a hidden field (one the
  template does not name is created hidden), each from its own `GET /generate`
  with the same default settings. A `login.password` in both generates once.
  Both parameters are accepted: a stored entry may hold `generatePassword`,
  and agents know it. The New item dialog has no per field boxes for this: a
  person types a value or generates one from the cell menu after.
  The write path syncs, reloads, checks, backs up,
  creates the item (`POST /object/item`) and syncs before it is used. The
  change log row has kind `createItem`, empty `itemIds` (no item it was
  based on) and the new id in `createdItemIds`, so Restore of the row moves
  the new item to the trash. A `createItem` committed from the queue (from
  the UI, or from MCP once the user approved it) puts the secret columns it left
  empty on the to-fill list (see The tool note). Refused (422) before
  anything is written, at
  `POST /api/queue`, in MCP and in the write path alike: a secret column in
  `values` ("COLUMN is secret: a new item never takes a secret value, fill it
  in the grid after it is created"), a column in `secretValues` that is not
  secret (a template's text field too), an empty one, `name`, a column that is
  also generated (`generatePassword` or `generateColumns`), a value in
  `secretValues` that equals the name, the folder or one of the `values` (the
  rule of equal values, see Secret values, it would be a plain copy, in SQLite
  too, and a generated value that does is refused at write time), each
  message naming the column and never the value, `generatePassword` on
  another type than a login, a column in `generateColumns` that takes no
  generated value or is also in `values` ("a generated value goes only into
  login.password or a hidden field"), an unknown type or template, both or
  neither of `type` and `template`, an empty name, `name` among the values,
  an unknown organization, an organization without collections or
  collections without one, a column that does not exist for the type (or is
  no leaf), and a personal secure note named `bwtidy` (it would be taken
  for the tool note).

## The tool note

One secure note named `bwtidy` in the user's personal vault (never in an
organization: its other members would see it). Created on first login when missing.

- `notes` holds one JSON object, `{"version": 1, ...}`, with one key per
  section: `templates` (see Templates), `datatypes` (see Datatypes), `toFill`
  and `generator`. A key bwtidy does not know is kept. Each section
  is saved alone with `setToolNoteSection` (see Writes), so a save of one
  never loses another. Empty notes read as no sections. Notes that are not
  such an object (edited by hand) read as no templates, and the next section
  save starts them over from `{"version": 1}` (the backup of that write
  holds them). One object with sections, so the to-fill list and the
  generator settings live next to the templates. A save holds changes of
  single entries, applied to the section as the note holds it at write time.
- The `generator` section holds the password generator's settings:
  `{"default": OPTIONS, "items": {ITEM_ID: OPTIONS}}`, with the parser in
  `sections/generator.py`. OPTIONS is `{"type": "password", "length",
  "uppercase", "lowercase", "number", "special", "minNumber", "minSpecial",
  "ambiguous"}` or `{"type": "passphrase", "words", "separator", "capitalize",
  "includeNumber"}`, the options of Bitwarden's own generator (`ambiguous` true
  avoids ambiguous characters, like `bw generate --ambiguous`). Limits: length
  5 to 128, words 3 to 20, `minNumber` and `minSpecial` 0 to 9, at least one
  character class, a separator of one character or none (stored as the
  character, `""` for none, sent to bw as `space` and `empty` where needed).
  The minimums have to fit the length, since bw would silently make the
  password longer than asked, and an item's own setting is often there for an
  app that cannot take long passwords. Without a section, without a default or
  with settings that are not valid the default is a password of 24
  with all four classes. An item's own setting moves to the new id with a
  conversion (`convertItem`, `moveToPersonal`, an `applyTemplate` that changes
  the type), written to the section as part of that write, together with its
  entry of the to-fill list. A queued change that names the old id lands on
  the new one (see `setToolNoteSection` in Writes). Settings of items that are
  gone (the trash counts) are dropped the next time the section is saved,
  by that write. Every other change goes through `setToolNoteSection`.
- The section `toFill` is the to-fill list: the secret fields the user still has
  to fill in, a list of `{"itemId": ID, "columns": [COLUMN, ...], "since":
  "YYYY-MM-DD"}`, secret columns only (grid column ids, `fields.NAME` for a
  field), at least one column, each once, at most one entry per item, `since`
  the day the entry was added (`parse_to_fill` in `sections/to_fill.py`
  checks it). A section that does not parse reads as an empty list and the
  next change replaces it. Reason: an agent's new item takes no secret value
  and Apply template adds missing hidden fields empty, so secret fields are
  left for the user to fill in, and the list reminds them.
  - What adds to it: a `createItem` or `applyTemplate` committed from the
    queue (`ui` entries, and `mcp` entries the user approved). A `createItem`
    from a template lists the template's required secret coordinates (an
    optional one may stay empty and a field name pattern names no column,
    see Templates), from a raw type
    `login.password` (unless `generatePassword`) for a login, `card.number`
    and `card.code` for a card and `sshKey.privateKey` for an SSH key, never
    `login.totp` unless a template lists it. An `applyTemplate` lists the
    template's required secret coordinates, under the new item's id when it
    converted the item. Only what is still empty after the write stays (see the prune
    below), so a column a person gave in `secretValues` or a column in
    `generateColumns` is not left on the list. The user
    marks a cell by hand in the grid (see Grid). A write through
    `POST /api/operations` (scripts) adds nothing. Adding
    never fails the write it follows: a list that cannot be queued is only
    logged.
  - What removes from it: after every load of the vault in RAM (an unlock,
    the sync and reload of every write) the list is pruned: a column that
    has a value now is dropped (filled in bwtidy or in any Bitwarden app),
    so is a column the item does not have as a secret any more (a field
    renamed, removed or not hidden any more), and an entry with no column
    left, an item that is gone or in the trash, and the tool note are
    removed. Checking a value only asks whether it is empty, it never logs or
    copies it.
  - What moves in it: a conversion (`convertItem`, `moveToPersonal`, an
    `applyTemplate` that changes the type) moves the item's entry to the new
    id with its `since`, written to the note as part of that write (see The
    tool note, the generator settings), and the next prune keeps the columns
    the new item still has empty as secrets.
  - Every change is a queued `setToolNoteSection` of `toFill` with `changes`
    (a `ui` entry with the summary `Update the to-fill list`, committed by the
    auto-commit like any edit): a column of an item added or removed, the
    difference between the list as the note holds it with every such entry
    still pending or being committed applied, and the list wanted. Only a
    list that differs from that one queues anything, so a list that is
    already right queues nothing and its own commit queues nothing again.
  - What a write left empty and what the user marked stay in RAM until the tool
    note itself holds them, and every load merges them in again. So a
    `toFill` save that failed (a bw error on the note, an `interrupted`
    commit) or could not be queued is queued again at the next load, and
    the failed entry can be dismissed. An Unmark takes its column out of
    them too, also one done while a write runs, so the reload of that write
    does not list it again. Reason: otherwise an Unmark between a write and
    its reload would come back, and a failed save would lose its columns for
    good. Limit: a backend stop before the save is in the note loses what was
    only in RAM.
  - `GET /api/grid` answers `toFill`, `{ITEM_ID: [COLUMN, ...]}` as the vault
    is now (the pruned list, even before it is written), each row's
    `emptySecrets`, the secret columns the item has without a value (left out
    when there are none), each row's `emptyTextFields`, the text custom fields
    the item has without a value (left out when there are none, field names
    are no secret, see Grid), each row's `booleanFields` and `linkedFields`,
    the item's boolean and linked custom fields with or without a value (left
    out when there are none, field types are no secret either), and a secret
    column for every listed cell, also
    when no item has a value in it yet. `POST /api/to-fill` `{itemId, column, mark}` adds
    the cell (`mark: true`) or takes it off and answers `{"entryId": N or
    null, "toFill": {...}}`, `entryId` null when the list stays as it is.
    422 for a column that is no secret column of the item, a cell that has a
    value when marking, the tool note and an unknown item.
  - MCP never sees the list, like the rest of the note. `propose_create_item`
    only says that the secret fields the user fills in show up in the To fill
    in list.
- The note lists every master password the backups may still be encrypted
  with, newest first: the hidden custom field `backup-password` holds the
  newest, and while a re-encryption is unfinished the hidden custom field
  `older-backup-passwords` holds the older ones as a JSON list of strings. A
  note with only `backup-password` reads as a list of one, a note with
  neither as an empty list. Every write of the list makes both fields
  hidden, whatever type they had (a field turned into a text field in
  Bitwarden would show the master password in the grid and to MCP). An
  unlock that finds `backup-password` (the first field of that name) or
  `older-backup-passwords` not hidden writes the list again for that reason
  alone, even when the master password is unchanged. The write reads the
  note fresh from bw serve first and, when it is no personal secure note any
  more (moved into an organization since the cache load), writes nothing and
  fails the login with 409 `the bwtidy note is not a personal secure note
  any more, move it back to your personal vault`, which the unlock form
  shows.
- On login, when the list is not just the master password, re-encrypt the
  backups with it, without asking:
  1. When the master password is not first in the list, put it first (taken
     out of its old place) and save the note, before anything is
     re-encrypted.
  2. The identity for hidden values (see MCP and the queue), then every
     backup that does not open with the master password: each listed older
     password is tried, newest first. One file at a time, each replaced
     atomically, so a run cut off at any point (a stop, a crash, a killed
     worker) leaves every backup readable with a password still in the list
     and the next login finishes the job.
  3. Only as the very last step, once the identity and every backup open with
     the master password or a backup was found unreadable with all of them,
     the list becomes the master password alone.
  A backup that decrypts with none of the passwords is left untouched and
  named in the UI after the login. No password and not the length of the list
  is ever logged, shown or answered. Reason for the list and the order: with
  one old password kept, a run from A to B cut off halfway would leave the
  field at A, and a later change to C would try only A and C, so the backups
  already in B could not be read any more.
- Found by id: bwtidy remembers the note's id in the `settings` table (key
  `tool_note_id`) whenever it finds or creates the note. The lookup tries the
  remembered id first (a personal secure note, whatever its name now), then
  the name. Every user of the note (templates, backup-password, the write
  path, the queue, MCP) uses that one lookup, so a note renamed in Bitwarden
  still holds the templates, still gets the backup password updates and stays
  invisible to MCP. A login creates a note only when neither finds one.
  Reason: a rename in the grid would otherwise make the templates vanish, and
  the next login would create a second, empty note.
- Read-only in the grid: its row is light grey, every cell is read-only
  ("bwtidy's own note. Templates are edited on the Types page.") and the
  cell menu offers no action on it. The normalize dialogs leave it out.
- The write path refuses every operation that touches it (422 "the bwtidy
  note is managed by bwtidy, edit the templates on the Types page").
  The only exceptions are bwtidy's own writes: a section save such as the
  templates save (a `ui` queue entry of `setToolNoteSection` that carries the
  permission with it, so the commit passes it on to the write path for that
  entry alone) and Rename it back, which say so
  explicitly in their call to the write path (never judged by the column).
  Every other operation on the note is refused at `POST /api/queue` too. The
  backup password updates write the note directly through bw serve.
- When the note found is not named `bwtidy`, the grid shows a warning
  (bwtidy's note is named "<name>" now) with a button Rename it back:
  `POST /api/tool-note/rename-back` with the note's `revisionDate`, a
  `setLeaf` of `name` through the write path (backup, revisionDate check, 409
  on a conflict). It answers `toolNoteName`, the new `revisionDate` and the
  backup.
- MCP never lists, reads or touches it.
- The Vault grid can leave out the note's row: the setting "Hide bwtidy's own
  note" (see Settings, Display), off by default. Only the Vault grid hides it.

## Backups

Every write has a backup that matches the vault as it is right before the
write, taken ahead of time in the background when possible. Reason: a backup
takes several seconds (one bw CLI process per export and per attachment, then
the encryption), which would make every edit in the grid slow if the backup
were taken inside the write.

- A backup carries a fingerprint: a sha256 hex over what it covers, computed
  from the listings it stores, so the order does not matter: every item's
  `id`, `revisionDate`, `folderId`, `favorite`, `organizationId`,
  `collectionIds` and attachment ids (an attachment's `url` carries a token
  that differs between two listings, the rest of an item is fixed by its
  `revisionDate`, Vaultwarden changes an item's collections without a new
  `revisionDate`, and the Bitwarden clients change an item's folder and
  favorite flag through the partial update without a new `revisionDate`), and
  the folders, collections and organizations as whole JSON. The fingerprint
  is stored in SQLite (table `backup_fingerprints`, `name`, `fingerprint`),
  one row per backup, removed when the retention deletes the backup. The
  `revisionDate`s it hashes are spelled as bwtidy first saw each revision
  (see Writes), so a backup stays reusable when bw serve answers a revision in
  another spelling later. After a restart of the app the first spelling may
  be the other one, and then one backup more is taken.
- After a write (once the vault is reloaded) and after an unlock (once the
  cache is loaded) a backup starts in the background, unless the newest
  backup's fingerprint already equals the vault's. One backup runs at a time,
  whether for a write, by hand or in the background. The background backup
  holds the session's password and key only while it runs. When the session
  ends it stops before its next step, the `bw` it runs is waited for up to
  30 seconds and only then cancelled (its `bw` gets SIGTERM), and a worker's
  stop cancels it at once (see Worker mode, Stopping a `bw` process). Its
  failure is only logged, the next write then takes its own backup.
- A write, after its sync, reload and checks, computes the vault's fingerprint
  from the cache, waits for a running background backup, and reuses the newest
  backup when its fingerprint equals the vault's (that backup's name goes into
  the change log and the answer). Else it takes a backup right there. Either
  way it syncs once more and checks again when the vault changed since the
  check (see Writes). A backup without a stored fingerprint is never reused.

- File: `BACKUPS/<UTC timestamp>.zip.age`, timestamp format
  `YYYYMMDDTHHMMSSZ`. A file whose name has that shape but no valid instant
  (a month 13, a February 30) is no backup, like any other file. `BACKUPS`
  is `BWTIDY_BACKUPS_DIR`, else `backups` in `BWTIDY_DATA_DIR`
  (`/data/app/backups` in the dev stack). Under the gateway
  that is `ROOT/HOST/EMAIL` with a backups root (`/backups/HOST/EMAIL` in
  `compose.yaml`), else `/data/accounts/HOST/EMAIL/app/backups` (see Gateway,
  Backups root).
- Encryption: age with a passphrase (scrypt), the master password. The `age`
  CLI only reads a passphrase from a terminal, so the backend uses `pyrage`
  (`pyrage.passphrase.encrypt`). The files must decrypt with plain
  `age -d` at a terminal. A login with a new master password re-encrypts
  every backup with it, trying each master password the tool note still lists
  (see The tool note).
- Zip contents:

      personal-export.zip       bw export --format zip (the format bw import takes)
      org-<orgId>-export.json   bw export --organizationid <orgId> --format json, per org
      item-attachments/<itemId>/<fileName>   every attachment of every item, via bw get attachment
      raw/items.json            GET /list/object/items, the exact objects the app PUTs
      raw/trash.json            bw list items --trash
      raw/folders.json, raw/collections.json, raw/organizations.json
      RESTORE.md                docs/restore.md, the steps of a disaster restore

  `item-attachments` is the source of attachments: every attachment of every
  item, personal and organization alike, active and in the trash, fetched one
  by one (`bw get attachment <attachmentId> --itemid <itemId>`, one bw process
  each, one after the other since several at once share one CLI state). A
  second attachment with the same file name on one item is stored as
  `<attachmentId>-<fileName>`, with the id put in front again while that name
  is taken. Reason: the personal export loses or mixes up attachments of items
  with the same name: `bw export --format zip` files them under
  `attachments/<item name>/<file name>` with special characters replaced, a
  second item of the same name gets the folder `NAME_1` (which collides with
  an item really named so, and `a/b` shares the folder of `a_b`), one file
  then overwrites the other, an attachment that fails to download is skipped
  with only a log line, and nothing says which folder belongs to which item.
  So `personal-export.zip` stays as it is for `bw import`, but its
  `attachments` folders cannot be trusted to be complete, to carry the file
  names as they were, or to tell items of the same name apart.
  Restoring a change log row takes attachments from `item-attachments` only
  (see below).
- `RESTORE.md` is `docs/restore.md` as the image has it (the Dockerfile
  copies it to `/app/docs/restore.md`, the dev stack mounts `docs/` there), with
  `COMMIT` in its line "Written for bwtidy COMMIT." replaced by the version
  as `/api/health` shows it: the package version, with the commit when
  `BWTIDY_COMMIT` is set, which a deployed worker's is not. Reason: with the repository gone, the backup itself says
  how to restore it. When the file cannot be read the backup is written
  without it and the log says so (a warning): a backup never fails for its
  manual.
- Completeness check before the zip is built: every attachment listed on an
  item in `raw/items.json` and `raw/trash.json` has its member, and the
  member's length fits the listed `size`. That size is the encrypted blob's
  (one type byte, the IV, the HMAC and the PKCS7 padded AES-CBC ciphertext:
  `49 + (length // 16 + 1) * 16`), not the plain byte count. A download that
  fails, is missing or does not fit raises `BackupError("attachments", ...)`
  naming the item id and the attachment id only, never a name or file name,
  and no `.zip.age` is written.
- Trash: `bw get attachment` fetches an attachment of an item in the trash
  just like an active one, and `bw list items --trash` lists the attachments,
  so trashed items' attachments are in the backup under the same
  `item-attachments/<itemId>/` path. The exports hold nothing from the trash.
- The CLI runs with `BW_SESSION` in its environment (never on the command
  line), `--nointeraction`, stdin closed and a timeout. With a session key
  `bw export` asks for no password (its `--password` option is for
  `encrypted_json` only). `--raw` writes the export to stdout, so no plain
  export ever touches the disk.
- The zip is built in RAM and the encrypted file written to a temporary name
  in the same directory, fsynced and renamed. A failed step leaves no
  `.zip.age` behind, and the error names the step.
- The listings after the sync are not trusted at once. Right after a `POST
  /sync` that fetched something, bw serve answers `GET /list/object/items`
  with an empty list for a moment while it decrypts the items again, so the
  cache load and the backup ask an empty list again for a few seconds before
  they believe it (a vault is only empty before the first login created the
  tool note). The backup also refuses to write a backup with no items: after
  the first login the tool note always exists, so an empty list is bw serve
  still decrypting. An item
  whose `organizationId` is not among the listed organizations makes the
  backup list them again with the same wait, and if the organization is
  still missing the backup is refused too (that organization would get no
  export). A folder or collection an item refers to but the listing lacks is
  asked again the same way, then only logged as counts, and the backup is
  written: the raw items still carry the ids.
- `GET /api/backups` lists the backups (name, size, time), `POST /api/backups`
  takes one by hand. The UI has no button for it, the first write takes one
  anyway.
- `GET /api/backups/{name}` answers the encrypted file as a download
  (`application/octet-stream`, the name as `Content-Disposition`). Only a
  name the listing knows, so no path can be given (404 for anything else).
- `DELETE /api/backups/{name}` deletes one backup and its fingerprint, and
  drops its decrypted copy from RAM, under the backup lock. Refused with 409:
  the newest backup (the next write may reuse it), the backup a running write
  selected (until that write ends, its change log rows name it), a backup a
  pending or approved `restoreItems` reads, and a backup a failed queue
  entry's result names (its write was taken with it, dismiss the entry
  first). A pending entry of any other kind has no backup yet, it is taken at
  the commit. The backups are the user's to prune. The retention keeps a
  backup for the same reasons (see Retention).
- Two restore paths: the per item rollback is the operation `restoreItems`
  below, which puts the items of one change log row back as the row's backup
  holds them. The disaster restore imports the exports into an empty account
  by hand, steps in `RESTORE.md` of every backup (its source is
  [restore.md](restore.md)). It needs only age, unzip, jq and the official
  Bitwarden CLI, nothing of bwtidy. The
  import gives every item a new id, so a jq program in it pairs each backup
  item with its new item by its content (every leaf but ids and dates, folder
  and collections by name, its organization by name or personal), and a shell
  function uploads each attachment from `item-attachments` to it. An
  attachment the new item already has (by file name) is skipped, so a second
  run adds only what is missing. An attachment without a file of its own
  there (a damaged zip) is not uploaded but printed by attachment id and
  backup item id, the other attachments still go up.
- Each organization export of a disaster restore goes into the organization
  of the same name, so the new account needs the old organization names.
  When the backup or the account has two organizations of one name the jq
  program stops before anything is uploaded. Reason: matching only by whether
  an item is in an organization would let twins in two organizations get
  each other's attachments.

### Restoring a change log row

The main concern is data loss: `restoreItems` makes the rollback a button,
and the tests prove that a backup brings the data back.

- `restoreItems {backup, itemIds, revisionDates, changeLogId?}`: `itemIds` are
  the row's `itemIds` plus its `createdItemIds`, `revisionDates` maps the ids
  that are active in the vault when the restore is planned to their
  `revisionDate`, for the usual conflict check. `changeLogId` is the row the
  plan was made for (`restore_operation` fills it), absent when a client builds
  the operation by hand. It goes through the write path like every
  operation: sync, reload, conflict check, a backup of the vault as it is now
  (so a restore can itself be restored), then the writes, and one change log
  row with kind `restoreItems`. Each item is checked again right before its
  first write (see Writes): before the move into the organization, the trash
  or the PUT, and before the untrash of an item that has a `revisionDate` to
  check. The move's answer goes into the chain of own writes, so the PUT after
  it is checked against it, and the PUT after an untrash is not checked again
  (the untrash answers no `revisionDate`). The queue takes it as a `ui` entry
  (`POST /api/queue`, committed by the auto-commit). MCP has no restore tool.
  The restore's own row records which backup it put back: `restored_backup`
  is the operation's `backup` and `restored_change_id` its `changeLogId`
  (both null on every other row, `restored_change_id` also null on a restore
  without `changeLogId`). They come back from `GET /api/change-log` as
  `restoredBackup` and `restoredChangeId`. The row's own `backup` stays the
  new backup taken before the restore, so the user can read the rows just
  before that backup to see what the restore meant.
- A change a later restore undid says so. A restore puts whole items back as
  its backup holds them, so it undoes every change of those items made after
  that backup. Change X is marked by the first later restore R (higher id,
  result `ok`) that put back an item X touched (its `itemIds` or
  `createdItemIds`) from a backup taken at or before X: R's
  `restoredBackup` is X's own `backup` or an older one, by the UTC time in
  the names (`NAME_PATTERN`, so a deleted backup still counts). X is a row
  with result `ok` that is not itself a `restoreItems`. `GET /api/change-log`
  gives each row `restoredBy: {id, at, itemIds}` (R's id and time and the
  items of X it put back) or null, looked up over the whole `change_log`
  table, not only the rows it returns. Only the first such restore counts,
  so nothing beyond the restore rows is stored. X's own backup and not X's
  time is the bound because names have whole seconds: the background backup
  after X can be named in X's second and already holds X.
- What it does per item id, against the decrypted backup:
  - in `raw/items.json` and active now: PUT the backup's item with the
    current `revisionDate` ("put back as before"). The attachments stay as
    they are, no operation removes one.
  - in `raw/items.json` and in the trash now: take it out of the trash
    (`POST /restore/item/{id}`), then the PUT ("take out of the trash and put
    back as before").
  - in `raw/items.json` and gone now (the trash was emptied): create it anew
    from the backup without `id`, `revisionDate`, `attachments`,
    `passwordHistory` and `fido2Credentials`, then upload its attachments from
    the backup ("create anew (passkeys and password history are lost)"). The
    new id goes into the row's `createdItemIds`.
  - in `raw/items.json` with another `organizationId` than now: a PUT cannot
    undo the move. An item that was personal is created anew in its old place
    like `moveToPersonal` does, with the attachments copied from the current
    item, and the current one is trashed ("create anew in its old place and
    trash the current one"). An item that was in an organization and is
    personal now goes back with `POST /move` like `moveToOrganization`, then
    the PUT ("move back into the organization and put back as before").
  - in `raw/trash.json`: move it to the trash when it is active now ("move to
    the trash"), else leave it.
  - in neither file (an item the row's operation created): move it to the
    trash when it is active now, else leave it ("leave as it is").
  - Whichever of these writes the item, its folder and collections go back by
    name when the vault does not have their ids any more. An id the vault
    still has is kept. A folder the vault lacks goes to the first folder of
    that name, or is created like `moveToFolder` does, and without a name in
    the backup the item goes without a folder. A collection the organization
    does not have any more goes to the collection of that name there, else it
    is left out. An item whose collections are all gone keeps the ones it has
    now. One that has none because it is to be created anew or moved back in
    fails with that reason, since an organization item needs at least one.
    The diff shows the item in that place. Reason: the server refuses a
    `folderId` it does not have ("Invalid folder") and a collection id it does
    not have ("Invalid collection ID provided"), so a restore after a deleted
    folder or collection would fail, half done in the collection case.
- Whether an item that is not active is in the trash or gone comes from the
  trash listing (`GET /list/object/items?trash=true`) the vault in RAM loads
  with the items, and is asked of bw serve again right before the item is
  brought back. A failed read alone never counts as gone (right after a sync
  bw serve answers "Not found." for every object for a moment, and a read can
  fail for other reasons), only the settled active and trash listings do. A
  read that fails while the listings hold the item fails the restore, and
  nothing is created. Reason: a failed read of an item in the trash would
  otherwise create a copy with a new id, without its passkeys and password
  history, and leave the real item in the trash.
- Attachments of an item created anew come from the backup's
  `item-attachments/<itemId>/`, by the id the item had in the backup, for
  personal and organization items alike: each attachment `raw/items.json`
  lists on the item is read from `<fileName>`, or from
  `<attachmentId>-<fileName>` for a second file of the same name, and
  uploaded under its original `fileName`. So two items of the same name each
  get their own files. The `attachments` folders of `personal-export.zip` are
  never read (they lose or mix up files, see Zip contents).
- Refused with 422 before anything is written, at `POST /api/queue` and in
  the write path alike: the tool note among the ids (it holds the backup
  password, templates are restored by saving them on the Types page), a
  backup that does not exist or does not decrypt with the session's
  password, an empty `itemIds`, an item that is active now but was not when
  the restore was planned (its `revisionDate` was never checked), and an
  item to create anew whose attachment the backup lacks. Reason: creating
  the item without an attachment, or with another item's, would be silent
  data loss. Without attachments such an item is created anew from such a
  backup too.
- A `renameAttachment` row is restored by a `renameAttachment` of the copy
  it made (`attachmentIds.after`) back to the name its backup holds for the
  attachment it replaced (`attachmentIds.before`), with the item's
  `revisionDate` and the copy's name now as `shownFileName`, through the
  queue like a `restoreItems`, as the same download, upload and delete (the
  copy gets a new id once more). A `restoreItems` would `PUT` the whole item
  back, which leaves its attachments as they are and undoes every later
  change of the item instead. The plan's `items` holds the item with action
  `renameAttachment` and the text `rename the attachment "NOW" back to
  "OLD"`, the dialog says it gives the attachment back the name it had and
  leaves the rest of the item as it is. Refused (422) like the rename (also
  when another attachment of the item has the old name by then) and
  when the item or the copy is gone (`the attachment ID the rename made is
  gone`, also once it was renamed back) or the backup lacks the old
  attachment. Its own row is a `renameAttachment` row, which can be restored
  too, and `restoredBy` does not mark the row it undid.
- `restoreFolder {backup, folderId, changeLogId?}` puts a folder back as the
  backup's `raw/folders.json` holds it, for a row of a folder kind: a folder
  that exists gets that name again ("rename back to "NAME""), one that is
  gone is created again with it ("create again as "NAME"", a new id), and
  nothing happens to a folder that has the name already, or to a gone one
  when a folder of that name exists ("leave as it is"). Refused (422): a
  backup that holds no folder with the id, and a rename back to a name
  another folder has now ignoring case. The rename back passes the secret
  rule like a `renameFolder`. It goes through the write path and the queue
  like a `restoreItems`, its plan made again on bw serve's folders right
  before the write, and its row records `restoredBackup` and
  `restoredChangeId` the same way. Its diff is a `restore` row of the folder
  with the text, then the folder's row as for a rename, its summary `Restore
  the folder "NAME" from BACKUP` (`Restore a deleted folder from BACKUP` when
  it is gone).
- `GET /api/restore-plan?changeLogId=N` answers the plan before anything is
  queued: `backup`, `summary`, the `operation` to queue, and `items`
  (`itemId`, `itemName`, `action`, `text`) with the texts above. For a row
  with a `folderId` the operation is a `restoreFolder`, `items` is empty and
  `folder` (`folderId`, `name` now or else the backup's, `action` one of
  `renameBack`, `createAgain`, `leave`, `text`) says what it does. 404 for an
  unknown row, 422 with the reason when the restore would be refused. The
  queue entry's summary is `Restore N items from BACKUP` and its diff has one
  row per item in the pseudo column `restore` (`after` is the text), plus the
  cell rows of the items it puts back as they were and a `trash` row for the
  items it trashes. The grid shows the cell rows as queued values and skips
  the `restore` rows.
- A decrypted backup is kept in RAM while the session lasts (the last one
  read), because the queue asks for a pending entry's diff on every listing
  and an age decrypt pays scrypt's cost each time. It is dropped when the
  session ends.
- Retention, applied after each new backup: delete a backup that is not among
  the newest 40 or is older than 90 days, but always keep the newest 5
  regardless of age. The three numbers are `BWTIDY_BACKUP_KEEP_NEWEST` (40),
  `BWTIDY_BACKUP_KEEP_DAYS` (90) and `BWTIDY_BACKUP_KEEP_ALWAYS` (5), whole
  numbers of at least 1, because the backup retention decides disk use. The
  timeouts and these are policy a deployment may have, so the environment sets
  them. The worker stop time, the bw time limits, the display limits and the
  datatype pattern limits stay fixed. A value that cannot be used stops the
  app and the gateway at the start with an error that names the variable. The
  text below says 40, 90 and 5 for the defaults. Reason: 90 days without
  changes must not delete every backup. A backup still in use is kept beyond
  40 and 90 days until it is not in use any more: the one a running write
  selected, one a pending or approved `restoreItems` or `restoreFolder`
  reads, one a failed queue entry's result names (the reasons Delete refuses,
  the newest 5 cover the newest one). It still counts among the 40, so the
  directory may hold 40 plus those in use.
  The deleted backups' fingerprints and decrypted copies in RAM go with them.
  Reason: a queued restore would otherwise fail when the retention had
  deleted its backup. Copying the backups directory elsewhere (a backup tool
  on its own schedule) is the deployment's concern, not bwtidy's.

## Grid

- Main view: all items as rows, all coordinates as columns (several dozen for
  a vault with many custom field names). AG Grid Community: column groups, a
  pinned `name` column and hiding columns are all in its free tier.
- Column groups: organizational, login, card, identity, sshKey, custom fields.
  A group header carries the coordinate prefix in parentheses (`Card (card)`,
  `Custom fields (fields)`), the `Item` group has none. The column headers
  under it leave the prefix out (`number`, not `card.number`), while the
  header tooltip, the column ids, the column chooser and the dialogs keep the
  full coordinate. The header tooltip also says how many items have a value in
  the column: `card.number, in 3 items` (`in 1 item`, `in no item`). It counts
  every item of the vault, not only the rows a search or filter shows, a masked
  secret counts as present and a false boolean as empty, like the hide toggle.
  The `template` column keeps its own tooltip.
  Toggle to hide columns with no values among the visible rows.
- A column exists for every coordinate some item has a value in. `favorite`
  and `reprompt` are always there, even when no item has them set, so any
  item can be made a favorite or get reprompt from the grid. A false value
  counts as empty for the hide toggle.
- Rare fields: a column `rareFields` after the vault's columns shows, for
  each item, the fields that few items use, so they show without a column of
  their own that is almost all empty. It is no field of the vault, bwtidy
  computes it. A number N, set with "Set N" in the column's header menu (a
  small dialog with a number field from 0 to 99 and the hint "0 turns Rare
  fields off. A field goes in when at most N items use it."), decides which
  columns go in. The header says `Rare fields ≤ N`, and `Rare fields (off)`
  when N is 0, the default, which leaves the column there and empty.
  Custom fields and the built-in slots (the `login`, `card`, `identity` and
  `sshKey` leaves) can go in, never `name`, `notes`, `type`, `folder`,
  `organization`, `collections`, `favorite`, `reprompt`, the read-only info
  columns or the `template` column. An item uses a custom field when it has
  the field, also with an empty value (the row's `emptyTextFields`,
  `emptySecrets`, `booleanFields` and `linkedFields` say so, an empty field has
  no cell), and a built-in slot only when the slot holds a value, as every item
  of a type has all of its type's slots. A column goes in when between 1 and N
  items use it, counted over every item but the tool note, not only the rows a
  search or filter shows, so the set does not change with a filter. The cell
  shows the rare columns the item uses as JSON, keyed by the column labels as
  the grid's headers show them (the full coordinate when two would be the
  same), in the grid's column order: `{ "sudo": "", "vlan": "20" }`. An empty
  field shows as `""`, `login.uris` as a JSON array, an item that uses none an
  empty cell. A secret value shows an eye icon in place of its JSON value, and
  clicking it reveals the value the way a masked cell does (the same fetch,
  the same 30 seconds and the same hiding when the row scrolls out, never
  cached): `{ "sudo": "qwerty" }`. The cell shows the vault values, not the
  queued ones, like the search. It is read-only: a double click does nothing
  and its cell menu has the Row group only. It sorts by its text (a secret as
  the mask) and the search tints it like a text cell when a shown value (never
  a secret) holds a word. A toolbar switch "Hide rare field columns", next to
  Hide empty columns, hides the columns that went into Rare fields (the column
  chooser still lists them). It is disabled while N is 0, turns on by itself
  whenever N goes from 0 to more and at page load when N is more than 0, and
  can be turned off to see both. It is not saved, like Hide empty columns. N
  is saved per account (`rareFieldsMax` of `/api/settings/display`, see
  Settings).
- The Columns button opens the column chooser, listing the columns under one
  subheader per group (Item, Login, Card, Identity, SSH key, Custom fields).
  Each column has a checkbox that shows or hides it, and `name` is disabled
  because it can never be hidden. Show all shows every column again. Each
  group's subheader has a checkbox labeled with the group, which shows or
  hides the whole group: checked when every column listed under the group is
  shown, unchecked when none is, indeterminate when some are. A click when it
  is checked or indeterminate hides every listed column of the group, when it
  is unchecked it shows them all, as one change. With text in Find column it
  acts only on the columns listed under the group then, the ones the search
  matches. The Item group's checkbox never hides `name` and does not count it,
  so it is unchecked when every Item column but `name` is hidden. It does not
  touch the second checkbox of a column, the one that selects it for a merge.
  Reason: hiding a group one column at a time takes a click per column.
- A double click on a column's right edge fits the column to its content, at
  most about 50 characters wide (a manual drag may go wider).
- The name cell shows a suffix after the name for some columns while they are
  out of view: hidden in the column chooser, or less than 90% inside the
  grid's visible area (a pinned column is always in view). It is worked out
  again on column moves, resizes and horizontal scroll. `type` shows as a
  small icon per item type, its label (`Login`) on hover over the icon only.
  `template` shows a template icon and the matching template's name, or the
  near miss label (`Computer 3/4`, `Computer 4.37/5`). The name itself stays
  as it is. Another suffix is one more entry in `NAME_SUFFIXES` in
  `frontend/src/grid/nameSuffix.tsx`: the column id it stands for and a
  function that renders it for a row.
- Filter by type, folder, organization, collection, template match and format
  (see Datatypes).
- Manage folders: the `folder` column's header has a menu button like a field
  column's, whose one entry "Manage folders…" opens a dialog listing every
  folder of the vault (also those no item is in) by name with its number of
  items (`N items`, plus `, M in the trash` when the trash holds some). Each
  has Rename, which turns its line into a name field with Save and Cancel
  (Save is off for an empty name, the name it has and a name another folder
  has ignoring case, which the field says), and Delete, off with the tooltip
  "Move its N items out first" while the folder holds items ("Its N items in
  the trash are still in it" for the trash). Each action closes the dialog
  and queues a `renameFolder` or `deleteFolder` like the grid's other edits
  (a folder conflict shows "the folder changed elsewhere" in the snackbar). A
  folder with such an entry pending says `Rename to "NEW" is queued` or
  `Delete is queued` and takes no other until it is written. `GET /api/grid`
  carries the folders as `folders`, one `{id, name, items, trashedItems}` per
  folder sorted by name.
- Own generator settings: while at least one item has its own generator
  setting (a row with `ownGenerator`, see the cell menu below), the toolbar
  shows a chip `Own generator settings (N)`, N the items. It is built like the
  To fill in chip: clicking it filters the grid to those items, together with
  the other filters (the To fill in chip too), and a second click (or its
  delete icon) clears the filter. The chip and the filter go when N is 0.
- To fill in: while the to-fill list (see The tool note) is not empty, the
  toolbar shows a chip `To fill in (N)`, N the listed cells. Clicking it
  filters the grid to the items on the list, together with the other filters,
  and a second click (or its delete icon) clears the filter. The filter goes
  when the list is empty. Every listed cell gets a warning colored outline
  (an outline, not a tint, so it shows beside the queued, failed and search
  marks) and the tooltip "Fill this in: double click to set it". Filling it
  is the secret dialog of every secret cell. After the commit and the reload
  the mark goes and the chip counts down. Hide empty columns keeps a column
  with a listed cell among the shown rows. The cell menu of an empty secret
  cell the item has (in `emptySecrets`, and no unsaved edit fills it)
  offers "Mark to fill in", a listed cell "Unmark" (`POST /api/to-fill`, the
  grid shows the answer's list at once). A filled secret cell offers
  neither, nor the tool note.
- The search box keeps the rows where every word is in some text cell, and
  gives each cell that contains at least one of the words (one list element is
  enough, never a secret cell) a very subtle background.
- Secret cells masked, reveal on click. A revealed value is fetched one cell
  at a time (`GET /api/items/{id}/secret?column=...`) and shown until its row
  scrolls out of view or 30 seconds pass. The frontend never caches it.
  A text field's value in a secret column (see the text field note below) is
  plain in `GET /api/grid` and MCP reads it too, so the grid shows it as text
  with no reveal, marked with a small warning colored corner at the top left
  and the hover text "Stored as a text field on this item, so an agent can
  read it through MCP" (under the value). A queued edit of it looks the same.
  The secret route still answers it 404. Reason: the user sees a mask only on
  a value MCP cannot read.
- The user edits in the grid directly. Every edit goes into the queue as a `ui`
  entry and is written by the auto-commit within three minutes, or at
  once with Commit now (see UI edits and the auto-commit). Reason: an edit
  that waits for sync, backup, write and reload feels slow. Double click or
  Enter edits a cell: text inline, `login.uris` and `collections` in a list
  dialog, `folder` as a choice of the folders in use or a new one,
  `organization` in a dialog: on a personal item with the collections (the
  dialog says a collection is needed until one is ticked), on an organization
  item with the only choice "Personal vault" (`moveToPersonal`, moving between
  organizations is out of scope) and a warning before the move ("bwtidy
  makes a personal copy and moves this item to the trash. Attachments are
  copied, passkeys and password history are lost. Everyone who shares its
  collections loses it."), secret cells in a dialog that never shows the
  current value. The organizations and collections these dialogs offer are
  all of the vault's, also those no item is in yet (else the first item could
  never go there). `GET /api/grid` carries them as `organizations`, one
  `{name, collections}` per organization (the collection names sorted, no
  entry for the personal vault). An item that names an organization or a
  collection the listing lacks still offers that name.
  An organization with no collections cannot take an item (a write needs at
  least one collection), so the dialogs say so: in the organization dialog
  and in New item it is a disabled option with the line "has no collections,
  create one in Bitwarden first", and the organization dialog preselects a
  sole organization only when it has collections. When no organization has a
  collection, the organization dialog shows an info text in place of the
  choice. The collections dialog of an organization item whose organization
  has no collection says so too, and it disables Save while no collection is
  ticked, with the hint "Pick at least one collection." (there is no warning
  about the organization's admins, the write would be refused).
  A column is secret when any item holds a hidden field of
  that name, so a cell in it can be a text field on its own item (with a
  value, or empty and in the row's `emptyTextFields`). Its value shows plain
  with the mark (see secret cells above). That field stays text,
  so its dialog sends no `fieldType` and warns: "On this item this is a text
  field, so the value is stored as plain text and an agent can read it through
  MCP." (see the secret rule in Writes). A boolean field there (in the row's
  `booleanFields`, with or without a value) gets a dialog with a checkbox
  "True" (ticked when the value is true) instead of the password input and
  the note "On this item this is a boolean field, so it holds true or false
  as plain text and an agent can read it through MCP.", and sends a `setLeaf`
  with true or false and no `fieldType`. A linked field (in `linkedFields`)
  opens no dialog and says "On this item this is a linked field, it has no
  value of its own." `type` changes only through the convert mapping (see
  Templates).
- A New item button in the toolbar opens a dialog that queues a
  `createItem` (see Writes). Start from (required) lists the stored
  templates first, each as `NAME (Type)`, under a Templates heading that is
  always there (with a disabled "No templates yet" under it when none is
  stored), then the raw types Login, Secure
  note, Card, Identity and SSH key. A chosen template's description, when it
  has one, shows as helper text under the select (see Templates). Then Name (required), Folder like the
  folder dialog (no folder, a folder in use or a new one), and, when the
  vault has an organization, Organization (Personal vault by default) with
  its collections, at least one ticked (all of the vault's organizations and
  collections, also those no item is in yet, an organization with no
  collections as a disabled option). Then one input per field, secret
  ones included: a template's coordinates in its order (a boolean field as a
  checkbox), a raw type's built-in slots and `notes` (`login.uris` one per
  line). Only filled inputs are sent. A secret input is built like the grid's
  secret dialog: a text input that shows discs (see password managers at the
  end of this section), a text area that shows discs for `sshKey.privateKey`. One checkbox
  "Show what I type" under the fields shows all of them as typed, and it is
  there only when the dialog has a secret input. A filled secret input goes
  in `secretValues` and only there, `values` holds the others. The values
  stay in the dialog's state: no query key, log or storage holds them.
  A template's text inputs are required, its secret ones too, except an
  optional coordinate's, whose label ends in ` (optional)` and which may stay
  blank (it does not go on the To fill in list either). A filled optional input
  is still checked against its datatype. A field name pattern gets two
  inputs: "Field name for fields.user_*", prefilled with the pattern's text
  before its first `*`, and the value input labelled with the pattern's
  column. The name must fit the pattern ("The name must fit user_*") and be
  no other field of the template ("fields.X is another field of the
  template"), or Create stays disabled. A required pattern needs its value
  like any required field, an optional one left blank is left out whatever
  its name. The value is sent under the typed name (trimmed), in
  `secretValues` for a hidden pattern, so the new field gets the pattern's
  field type. One field per pattern, there is no Add another. Blank (`login.uris` with no line
  left counts as blank) keeps Create disabled with the text "Required by the
  template", unless the field's datatype matches blank (`shortest` 0 in
  `GET /api/datatypes`). A filled input with a datatype shows the format live
  under it like the grid does (`matches format`, `matches format 80%,
  expected ...` or `too long to check`, the input red when off), checked
  through `POST /api/datatypes/{id}/check` after 250 ms and each URI of
  `login.uris` on its own. Create stays disabled while a filled value is
  being checked or is off its format, so a changed value blocks at once, before
  the answer. A refused check (a datatype that went away or broke) blocks
  nothing, and a datatype `GET /api/datatypes` does not list counts as none.
  When the template has a `name` coordinate with a datatype, the Name input
  gets the same check. A boolean field and a raw type's inputs never block
  (a raw type needs only the Name). The block is the dialog's: `POST
  /api/queue`, MCP and grid edits are not blocked, and MCP may leave a
  template's fields blank.
  A login has a checkbox "Generate a password", on by default
  (`generatePassword`), and next to it the default in a few words
  ("24 characters" or "4 words", see Settings). While it is ticked the
  `login.password` input is not shown and does not block, as the password
  gets its value from the generator, and `generatePassword` is sent (a
  typed password is left out). Unticked the input shows: optional for a raw
  login, required for a template that has `login.password`. Under the fields,
  for a raw type that has a secret field: "Secret fields left empty go on
  your To fill in list.", and with the checkbox ticked also "The password is
  generated and shown only when you reveal it in the grid." (a template gets
  only the second, its secret fields are required or optional). Create sends the entry and closes the dialog once it is queued, a
  refusal (422 or 409) shows its reason in the dialog and nothing is
  queued. The new item is a row after the commit, when the grid reloads.
- A cell that cannot hold a value is a faint grey, the theme's hover tint at
  35% of its strength, and its tooltip says why (`unavailableReason` in
  `frontend/src/grid/editing.ts`): a `login`, `card`, `identity` or `sshKey`
  leaf on an item of another type (`Only login items have login.username.`),
  `collections` on a personal item, and a field occurrence that waits for the
  one before it (an empty `fields.<name>#2` cell of an item without a value in
  `fields.<name>`, the tooltip says which column to fill first, and the
  backend refuses such a `setLeaf` in the same plain words). A double click,
  Enter, typing or Delete on such a cell does nothing and shows no message,
  the tooltip explains it. Not grey: the read-only columns (`attachments`,
  `passkeys`, `revisionDate`, `type`, they hold values), the tool note (its
  row is grey already) and a cell with an agent's proposal (amber). These keep
  their message on an edit attempt. An edit attempt on an `attachments` cell
  (a double click, Enter or typing) says "Rename an attachment from the cell
  menu." (the other read-only columns stay silent). The grey is judged on the vault values and
  lies under the queued, failed and conflict tints (a conversion elsewhere can
  leave a conflict mark on a cell that has no value now).
- A right click on a cell, or the ContextMenu key or Shift+F10 on the focused
  cell, opens a menu in two groups, each with a subheader and shown only when
  it has an entry. "Cell" acts on the cell: Edit value (when the cell can be
  edited), Remove value (when it holds a removable value), Mark to fill in or
  Unmark (see To fill in above), and on
  `login.password` of a login or a hidden custom field (not on a text,
  boolean or linked field of the same column, with or without a value, not on
  the tool note) Generate new value and Generator
  settings for this item. Generate new value asks "Replace the value with a
  generated one? The old value stays in the item's password history." and
  queues a `generateLeaf` (see Writes): the cell shows `(generated)` until the
  commit and has no reveal before that. Generator settings for this item opens
  the options of the Settings page in a dialog, with a switch "Use the default"
  (on for an item without its own setting, the form hidden). Save sends
  `PUT /api/generator/items/{item_id}`, with the switch on
  `DELETE /api/generator/items/{item_id}`, and a refusal stays in the dialog.
  An item with its own generator setting shows it in the grid, without a
  dialog: `GET /api/grid` gives its row `ownGenerator: true` (left out
  otherwise), from the same view the generator routes use (the section folded
  with a save under way, so a save shows at once). The cells the menu offers
  Generator settings for this item on, where the item has the secret (a
  masked value or an empty secret in `emptySecrets`), get a tiny tune icon in
  the cell's bottom left corner in the theme's secondary text color (both
  color modes), and the hover text "Own generator setting" (after the other
  lines of the cell's hover text). Other cells have nothing. Saving the
  dialog reloads the grid.
  On an `attachments` cell of an item with attachments (not the tool note)
  the Cell group has Rename attachments. It opens a dialog "Attachments of
  NAME" (NAME the item's name) that lists each attachment's file name in the
  item's order with a Rename button, which turns its line into a name field
  with Save and Cancel, like Manage folders. Save is off, with the reason
  under the field once the user types, for an empty name or one of spaces only
  ("The name is empty"), one with a slash or a backslash ("A file name may
  not hold a slash or a backslash"), the name it has ("The attachment has
  this name already") and the name of another attachment of the item
  ignoring case ("Another attachment is named NAME"), the rules of
  `renameAttachment` (see Writes, which stays the one that decides).
  Changing only the case of its own name is allowed. Save closes the dialog
  and queues one UI `renameAttachment` with the name the dialog showed as
  `shownFileName`. An attachment with a rename pending says `Rename to "NEW"
  is queued` and its Rename is off until it is written. The dialog never
  shows or fetches an attachment's url, size or contents.
  "Row" acts on
  the item: Add field (name, value, text or hidden. A name with a cell says
  "The item already has a field with this name. Edit its cell instead." An
  empty field of that name keeps its type, and its column may not exist: an
  empty text field fixes the type to Text with "The item has an empty text
  field of this name, the value goes into it as plain text.", an empty hidden
  field fixes it to Hidden with "The item has an empty hidden field of this
  name, the value goes into it as a secret.", and a boolean or linked field
  refuses with "The item has a boolean field of this name, it takes true or
  false and no typed value." or "The item has a linked field of this name, it
  has no value of its own."), Convert to template…,
  Merge into item… and Move to trash (after a confirmation). The `template`
  column holds no value of the item, so its menu has the Row group only.
  Reason: the groups show whether an action is for the row or the cell.
- The tool note's row is light grey and read-only, with every cell menu
  action disabled (see The tool note). The setting "Hide bwtidy's own note"
  (see Settings) leaves the row out of the grid. The rows then shown are the
  rows of the grid, so the item count, the column chooser's counts, hiding the
  empty columns and select all do not count the note.
- Several items move to the trash at once: the first column is a narrow
  checkbox column pinned left, and its header checkbox ticks every shown row
  (only the rows a search or filter keeps). Shift-click on a checkbox ticks a
  range. A click on a cell ticks nothing and edits as usual. The tool note's
  row has no checkbox, it cannot be selected. While any row is ticked a bar
  right below the grid shows "N selected", a button Move to trash and a button
  Clear. Ticking or unticking never moves the rows. Move to trash opens one
  dialog "Move N items to the trash?" ("Move 1 item" for one) that lists the
  item names, scrollable when long, says "They can be restored from the trash
  in Bitwarden or from the Change log." ("It can" for one) and has the
  buttons Cancel and Move to trash. Confirming queues one `trashItem` entry
  per item in one `POST /api/queue`, so the rows are faded as queued ones are
  (see below) and the auto-commit writes them as one batch with one backup.
  The ticks clear when Move to trash is sent, on Clear and whenever a search
  or filter changes which rows are shown, so a row not shown any more is never
  part of an action. The single item path (the cell menu's Move to trash with
  its own dialog) stays. Reason: one item at a time takes a right click, Move
  to trash and a confirmation each. Moving to the trash is the only action
  on a selection.
- Each edit is one call of `POST /api/queue`. A dialog closes as soon as it
  is sent and nothing waits for the answer, so the next edit can start at
  once. A 201 reloads the queue (the grid shows the queued value). On a
  conflict (409, a `revisionDate`, or a folder, favorite flag or collections
  that differ from what the row showed, see Writes) it names the items that
  changed elsewhere, and nothing is queued. The reloaded grid marks the rows
  of those items with a subtle light purple background, and the cells whose
  shown value differs from before the reload with a slightly stronger tint of
  the same hue. Masked cells are never marked (their change cannot be seen),
  nor `revisionDate` (it always differs). An item that vanished is not in the
  grid any more. The marks
  stay until the next accepted edit. The comparison is a pure module
  (`frontend/src/shared/conflicts.ts`) so other writers can reuse it.
- A Change log page lists the latest logged operations (`GET /api/change-log`)
  and the backups (`GET /api/backups`) as one list, newest first: a backup is
  a row of its own (operation `backup`, its size in the Result column, its
  name as a link that downloads the file, `GET /api/backups/{name}`). Since
  a backup is taken before the write that names it, it sorts right under
  that write's row. A backup row has a Delete button that asks first:
  "Delete backup NAME for good? Changes made with it can no longer be
  restored.", then `DELETE /api/backups/{name}`. The newest backup's Delete
  is disabled (the tooltip says why), and a 409 shows its reason in the
  dialog. A change row whose backup is gone (deleted, or by the retention)
  keeps the name, grey and struck through, and has no Restore button. Under
  the table a note says that a backup is age encrypted with the master
  password, is to be decrypted with `age -d` on a tmpfs, and that the steps
  for a lost vault are in `RESTORE.md` in every backup (as text, the app does
  not serve the docs).
  A filter bar above the table narrows the rows (no sorting other than
  newest first): From and To (local days in
  the chosen date format, see Settings, sent as the UTC instants of the day's
  first and last millisecond), Source
  (multi-select of UI, MCP and Mixed), Operation (multi-select of the kinds by
  their labels), Field (autocomplete of `name`, `folder`, `collections`,
  `organization` and the grid's columns that are not read-only, several at
  once, and a coordinate no column has any more can be typed in), Items (a
  text matched against the item names of the grid, case ignored, sent as the
  ids of the matching items. Under it the field says how many items it names.
  When it names none, no row matches and nothing is asked. Above 100 items it
  says to type more of the name and does not filter, the ids would not fit in
  a URL),
  Result (All, OK, Failed) and Clear filters (disabled and grey while every
  field is empty, a blank Items text counts as set so it can be cleared).
  Each change asks
  `GET /api/change-log` again with the filters as its parameters. With no
  filter the backups are rows as above, with any filter only the backups that
  a shown change row names (its backup, or the backup a restore put back). When
  more rows matched than the 200 shown, the page says "Showing the newest 200
  matches." ("Showing the newest 200 changes." with no filter). The rows the
  filters keep are in one place (`useFilteredChangeLog` in
  `frontend/src/changelog/filters.ts`) for whatever reads the shown rows.
  The Items column names the row's items (by id when the vault lacks
  them), for a row that touched none (a `createItem`) the items it
  created, and for a row of a folder kind its folder (`Folder NAME`, by id
  when the vault lacks it). A `renameAttachment` row adds a line under the
  item: `Attachment BEFORE replaced by AFTER` with the two attachment ids, or
  `Attachment BEFORE` when the write kept no copy.
  A Diff report button in the filter bar lists
  what the shown change rows did, never a value. It is disabled when no change
  row is shown (a backup row alone does not count). A tooltip, also shown
  while the button is disabled, says "Lists what
  every shown change did. Filter the log first to narrow it down." It opens a dialog "Diff
  report" with the line "N operations, M lines" (N the change rows, "1
  operation" and "1 line" in the singular) and a table, oldest first, one
  line per row, item and field: Time (formatted like the page), Source (UI,
  MCP or Mixed), Operation (its label), Item (its name, or its id when the
  vault lacks it, for a `createItem` row the item it created, for a folder
  row `Folder NAME` like the Items column, by id when the vault lacks the
  folder) and Field
  (the coordinate from the row's `columns`). A row without `columns` gives one
  line per item with "(whole item)" as Field. A row with neither items nor a
  folder gives no line. Backup rows are left out.
  Copy copies the table as tab separated text with a header line (Time,
  Source, Operation, Item, Field, a tab or line break in an item name
  becomes a space) and says "Copied", or "Could not copy, select it by hand"
  when the clipboard refuses, like the copy fields of the MCP token dialog
  (both use `frontend/src/shared/useCopy.ts`). The lines are built in
  `frontend/src/changelog/diffReport.ts`.
  The Source and Result columns show readable labels, display only (the API
  and SQLite keep the raw values): source `ui` shows "UI", `mcp` "MCP" and
  `mixed` "Mixed", result `ok` shows "OK" and `failed` "Failed". A value
  without a label is shown as it is. The labels are in
  `frontend/src/changelog/rows.ts`.
  Change rows that sit next to each other and name the same backup file (a
  batch shares one backup, and an unchanged vault may reuse the previous one)
  are grouped: the topmost shows its Backup and Action cells as usual, the
  later ones show the file name and the Restore button with their text and
  button colors mixed 43% toward the page background
  (`color-mix(in srgb, COLOR 57%, BACKGROUND)` from the theme's palette, in
  light and dark mode). Restore works on them as on any row. A backup row
  between two change rows ends the group.
  The Operation column shows a plain label, then the kind in small grey
  monospace (`Set a value setLeaf`), and on hover one sentence of what the
  operation did. Below them, in the same grey monospace, the row's
  `columns`, comma separated: at most 3, then `+N more`, with all of them in
  a tooltip. A row without columns shows only the label and the kind. Reason:
  a fixed sentence per kind does not say which fields changed. The labels and
  explanations are in `frontend/src/shared/operationLabels.ts`, one per kind.
  A kind without an entry shows only the kind. The kinds themselves are the
  vocabulary of the API and the MCP tools, so the user and the agent see the
  same words.
  A row with result `ok` whose backup still exists (the retention and Delete
  remove old ones) and that names an item or a folder has a Restore button,
  except the rows of the
  tool note (template saves, see Restoring a change log row). Restore opens a
  dialog: "Put these items back as they were before this change, from backup
  BACKUP.", then one line per item from `GET /api/restore-plan` (its name, or
  its id when the vault and the backup lack one, and what happens to it), for
  a folder's row "Put this folder back as it was before this change, from
  backup BACKUP." and the line `Folder NAME` with what happens to it, then
  "bwtidy takes a new backup first, so this restore can be restored too.",
  with Cancel and Restore. Restore queues the plan's operation like every UI
  edit and closes the dialog. A refusal of the plan or of `POST /api/queue`
  (422 or 409) shows its reason in the dialog and nothing is queued. The
  restore's own row has a Restore button like every write.
  A restore row says below its operation label "From backup NAME" (grey and
  struck through like the Backup column when the backup is gone) and, when
  `restoredChangeId` names a row on the page, ", the change of TIME" (the time
  of that row) as a link. The link scrolls to that row and highlights it for 2
  seconds. A restored row that is not on the page (older than the latest
  entries the page lists) shows the backup name only.
  A change a later restore undid (`restoredBy`, see Restoring a change log
  row) has a small restore icon (a clock with an arrow back) after its
  operation label and kind, with the tooltip "Restored TIME: FIELDS on
  ITEMS": the restore's time, the row's `columns` and the items it put back
  by name (by id when the vault lacks them). A row without columns says
  "Restored TIME: ITEMS". When the restore's row is on the page the icon is
  a button that scrolls to it and highlights it like the link above.
- Queued changes show in the grid as if they were done, from the pending
  queue the app polls every 10 seconds anyway.
  A cell with a pending change shows the queued value on a subtle amber
  background, with the tooltip `Queued in #<id>: <summary>. Was: <before>`
  (bullets for a secret value, empty for a missing one). A secret queued
  value stays masked and has no reveal, it is not in the vault yet. When
  several pending entries change one cell, the newest (highest id) is shown
  and the tooltip lists them all and says that the later ones fail with a
  conflict when approved in order. A row queued for the trash is faded and
  struck through, its tooltip names the entry. A column that only exists in
  a queued change (a rename to a new name, a template adding a field) gets a
  column in its group (from the coordinate's prefix), secret when the queued
  value is masked. An entry with a `diffError` changes nothing in the grid.
- Cells with an agent's proposal are read-only ("A queued change waits on
  the Queue page. Approve or reject it first."). Cells with the user's own
  unsaved edits (`ui` entries) stay editable, the editor starts from the
  queued value, and their tooltip says `Unsaved in #<id>: ...` and when the
  auto-commit writes it (`Saved automatically in 7 s.`). Several unsaved
  edits of one cell get no conflict note: a commit checks a later one against
  bwtidy's own write before it. Search, filters and every write work on the
  vault values, never the queued ones. A toolbar switch "Show queued
  changes", on by default, turns the overlay off. The queue reloads after
  every edit, the grid and the queue after every commit, approval and
  rejection.
- Password managers (Bitwarden, 1Password, LastPass, Dashlane, the browser's
  own) are invited only on the account form, the unlock and the Bitwarden
  login. Inside the app every input edits vault data, so every other input
  (the pages, the dialogs, the selects and autocompletes, the grid's own cell
  editors) carries `autocomplete="off"` and the opt-out attributes the
  managers honor: `data-bwignore`, `data-1p-ignore`, `data-lpignore` and
  `data-form-type="other"`. A secret input (the secret dialog, a hidden value
  in Add field, the secret inputs of New item) is a text input masked with CSS
  (`-webkit-text-security: disc`, which Chromium and Firefox both honor) until
  "Show what I type", with no spell check, autocapitalize or autocorrect: no
  manager sees a password field to fill or to offer saving.

## Light and dark mode

A button in the app bar opens a small menu with three choices: System, Light
and Dark, the current one checked. System is the default. The button's icon
shows the setting (a monitor for System, a sun for Light, a moon for Dark)
and its label is "Color mode: System" (or Light, Dark). Light and Dark are
stored in the cookie `bwtidy_color_mode` (`light` or `dark`, any other value
counts as no cookie), one setting per browser that every account shares.
System is no cookie: choosing it deletes the cookie (`Max-Age=0`) and the
browser's `prefers-color-scheme` decides, also when the scheme changes while
the page is open. The cookie is set by the page itself (`Path=/`, one year,
`SameSite=Lax`, `Secure` on https), so a small script in `index.html` can
read it before the first paint (no cookie means the system's scheme, so the
script needs nothing for System). The gateway passes it on to the worker with
the other cookies and nothing reads it there. The account form, the unlock
page and the Bitwarden login follow the same setting and have no switch of
their own. The grid follows too: AG Grid gets its light or dark color scheme,
and the colors made for a white background (the format marks, the
near-duplicate markers, the tool note's row) have a dark variant.

## Paragraph width

Paragraphs of running text, on every page and in every dialog, are at most 75
characters wide (`75ch`). Tables, the grid and forms keep their width.
The lines of such a paragraph are balanced: the line breaks are chosen for the
whole paragraph, so every line but the last comes out close in length (ragged
right, with an even edge). They follow the paragraph's width when it changes.
Words are never hyphenated and the text is never justified: balanced
ragged-right lines read better than `text-wrap: pretty`, hyphenation or
justified text. The breaks do not change the text: copying a paragraph gives
the same words with the same spaces. A dialog's description text gets the 75
character width but not the balancing.

An alert that holds running text (full sentences, such as the hint on the
Types page or the warnings in the grid's dialogs) follows the same rule:
its text is at most 75 characters wide with balanced lines, and the alert box
shrinks to its text instead of running across the page. Alerts with a short
message, such as an error that names what failed, keep their width.

## Settings

A Settings page, opened with Settings in More actions (the three dots in the
app bar). It has two sections, "Display" and then "Password generator".

"Display" has the select "Date and time format". Every date and time the UI
shows (the Change log, the Queue, the MCP token's times) follows it, in the
browser's time zone, and every page changes at once when the choice changes.
Date fields (the Change log's From and To) follow it too, because the
browser's own date field shows its own order (mm/dd/yyyy) whatever the
choice: they show and take a day in the date part of the format (System in
the browser locale's order of day, month and year), can be typed in or picked
from a calendar, and have a button that clears them. A day typed only in part
changes nothing. Each choice shows the current time in its format:

- System (the default): the browser's locale, as `toLocaleString` gives it.
- ISO 8601: `YYYY-MM-DD 14:05:09` (the date as year, month and day with
  hyphens, a 24 hour time).
- Europe: `12/11/YYYY 14:05:09` (day, month, year).
- Europe (dots): `12.11.YYYY 14:05:09`.
- USA: `11/12/YYYY, 2:05:09 PM` (month, day and hour without a leading zero).

A pick is saved at once, in the `settings` table (key `date_format`), so it
belongs to the account and follows it to every browser. A save that fails says
so and the select shows the stored choice again. Until the choice has loaded,
and when it cannot be loaded, the UI shows System.

The select "Layout" is under the date format, with Normal (the default) and
Compact and the helper text "Saved in this browser only.", for small windows.
Compact shrinks the theme's spacing to 5 px instead of 8 and makes the app
bar's toolbar dense, so more fits in a small window. Nothing else changes:
the same components in the same order, the same fonts, and the Vault grid
keeps its row height while only the page padding around it shrinks. The
choice is one per browser that every account shares, in localStorage under
`bwtidy.layout` (any other or missing value is normal, every read and write
wrapped so no storage reads as normal and a failed write still changes the
page), next to the color mode. The page changes at once with no reload, and
the choice is read before the first render so the first paint is already in
the chosen layout.

The checkbox "Hide bwtidy's own note" is under the Layout select, off by
default, with the helper text "The note keeps your templates and settings.
Hidden only in the Vault grid." While it is ticked the Vault grid leaves out
the tool note's row (see The tool note, and Grid for what then does not count
it). Nothing else changes: the Types page, the alert about a renamed note and
MCP (which never sees the note) stay as they are. It is saved at once like
the date format, in the `settings` table (key `hide_tool_note`, stored as `1`
or `0`), per account. A save that fails says so and the checkbox shows the
stored choice again. Until the choice has loaded, and when it cannot be
loaded, the note shows. A save sends only the field that changed, so changing
one never resets the others.

The N of the grid's Rare fields column (see Grid) is kept the same way, in the
`settings` table (key `rare_fields_max`, the number as text), per account. It is
set in the column's header menu, not on this page, and is 0 (off) until it has
loaded and when it cannot be loaded.

- `GET /api/settings/display` answers `{"dateFormat": ..., "hideToolNote":
  ..., "rareFieldsMax": ...}`. `dateFormat` is one of `system`, `iso`, `europe`,
  `europe-dots` and `usa`. A stored value it does not know reads as `system`, a
  missing or unknown `hide_tool_note` reads as `false`, and a missing or
  unknown `rare_fields_max` (no whole number from 0 to 99) reads as 0.
- `PUT /api/settings/display` takes any of `dateFormat`, `hideToolNote` and
  `rareFieldsMax`, saves only what it got and answers like the `GET`. 422 for
  an unknown format, a `hideToolNote` that is not a boolean, a `rareFieldsMax`
  that is not a whole number from 0 to 99 (a JSON integer, not a string or a
  boolean), a field set to null and a body with none of the fields.

"Password generator" holds the default of the `generator` section of the tool note: the
choice Password or Passphrase, then the options as in Bitwarden (length, A-Z,
a-z, 0-9, special, minimum numbers, minimum special, avoid ambiguous characters,
or number of words, word separator, capitalize, include number), an example
value with a button for another one, and Save. Options outside the limits say
why, turn Save off and get no example. Switching the type back keeps what was
set for it.

- `GET /api/generator` answers the section with the default filled in, `saving`
  while a save is under way (with every save under way applied, see
  `setToolNoteSection` in Writes), and `error` when the stored settings are
  not valid.
- `PUT /api/generator/default` and `PUT /api/generator/items/{item_id}` take
  OPTIONS, `DELETE /api/generator/items/{item_id}` puts the item back on the
  default (nothing is queued when it has no setting of its own). Each queues
  its one change (`setDefault`, `setItem` or `removeItem`) as a `ui` entry of
  `setToolNoteSection`, summary "Save the generator settings", and starts a
  commit at once like a templates save. They answer 202 with the section as
  the saves under way leave it and `entryId`, 422 for options outside the limits
  and for the tool note, 404 for an item not in the vault.
- `POST /api/generator/example` takes OPTIONS and answers `{"value": ...}` from
  bw serve (see Secret values), 422 outside the limits, 502 when bw fails.

### Deleting data

A section "Delete data" at the bottom of the Settings page, for the logged in
account alone. It has two outlined error-colored buttons, each with one
sentence under it saying what goes and what stays:

- "Delete my data" removes every backup of the account (the newest one too),
  every change log row, the backup fingerprints, and every queue entry that is
  not pending any more (applied, failed, rejected, dismissed). The settings,
  the MCP token and the templates (they live in the vault's tool note) stay.
- "Delete my account" does all of that, then logs out of Bitwarden and the
  gateway deletes the account directory and its directory under the backups
  root (see Gateway). Afterwards the browser shows the account form, as after
  Switch account.

Each button opens a dialog that lists what will be deleted and has a text field
"Type EMAIL to confirm": its button stays disabled until the field matches the
logged in email (case-insensitive). When any queue entry is pending the first
try answers 409 and the dialog then asks one more thing, a radio choice:

- "Write the N pending edits first" commits the pending UI entries like Commit
  now (see UI edits and the auto-commit), then deletes. A failed commit deletes
  nothing and says what went wrong. Pending MCP proposals cannot be written
  without approval: with Write first they stay pending (for Delete my data), and
  the dialog says so. A backup a remaining pending restore proposal still needs
  stays, named in the answer.
- "Drop them" deletes the pending entries too (the pending MCP proposals among
  them).

Delete my account always drops the pending MCP proposals too, since the account
goes. After Delete my data the dialog shows a short success text with the counts
and the Change log and Queue caches are invalidated. The deletion refuses with
409 `a write is running, try again` while a commit or a direct write runs,
else it takes the queue's lock and then the write path's (the order a commit
takes them in) and holds both while it deletes, with the backup keeper
stopped, so no write starts meanwhile and nothing is half deleted.

- `POST /api/account/delete-data` needs the session cookie (401 without it)
  and the header `X-Bwtidy-Request: 1` (403 without it), like `POST
  /api/bw-logout`. It takes `{"pending": "write" | "drop" | null}`. It answers 409 `{"pending": N, "mcpPending": M}` when
  entries are pending and `pending` is null, 409 with the failure when Write
  first could not write the pending edits, 409 `{"detail": ...}` while a commit
  is running, else 200 `{"backups": ..., "changeLog": ..., "queue": ...,
  "keptBackups": [...]}` with the counts deleted and the backups kept for a
  pending restore proposal.
- `POST /api/account/delete` takes the same body and answers the same 409s. On
  success it deletes everything (the pending MCP proposals too with Write
  first), writes the marker `app/delete-requested` (see Gateway), logs the CLI
  out and ends the session the way `POST /api/bw-logout` does, so the worker
  exits and the gateway deletes the account.

## About

More actions (the three dots in the app bar) holds Settings, Switch account
(when an account is open) and About bwtidy, in that order. The version and
the source code link are on the About page, not in the menu.

The version: bwtidy is alpha software (how to run it is in
docs/how-to/run.md), and the README carries a warning that the code is mostly AI written, not reviewed by anyone
else and changes the vault. bwtidy follows Semantic Versioning 2.0.0
(https://semver.org): MAJOR.MINOR.PATCH, pre-releases as `-alpha.N`,
`-beta.N` and `-rc.N`, and while MAJOR is 0 anything may change. The version
is `0.1.0-alpha.1`. Everything a person sees uses that spelling.
`backend/pyproject.toml` holds the same version in Python's spelling
(`0.1.0a1`, `a` is alpha, `b` is beta, `rc` is rc), which Python packaging
requires, and it is the only place the version is written by hand. The
backend reads it from the installed package metadata
(`importlib.metadata.version("bwtidy")`, which the image and the dev
container have because they install the project) and spells it the semver
way in `bwtidy/version.py` (`semver`), the one conversion.
`frontend/package.json` repeats it in semver's spelling, nothing reads it,
and `scripts/ci` fails when it differs from pyproject's. The image knows the
commit it was built from as the committer date (YYMMDD), a space and the
short git hash, like `261028 f2f4299`: build arg `BWTIDY_COMMIT` (empty
without it, since the build context has no `.git`), passed by compose from
the variable of the same name and by `scripts/dev-vault up` with `-dirty`
for uncommitted changes. `GET /api/health` answers `{"status": "ok",
"version": "<version> (<commit>)"}`, like `0.1.0-alpha.1 (261028 f2f4299)`,
and the version alone, `0.1.0-alpha.1`, without a commit, so the user knows
which build they test.

About bwtidy opens the About page, titled "About bwtidy", its paragraphs
capped like all running text (see Paragraph width):

- `Version <version>`, the text of `GET /api/health`. The app bar shows the
  same text shortened, after the title (see Session and access).
- License: the copyright line of README.md's License section, then "bwtidy is
  free software under the GNU Affero General Public License, version 3
  only.", with a link to LICENSE in
  the repo (https://github.com/tnek213/bwtidy/blob/main/LICENSE), and the
  sentence of the README that there is no warranty. Then a Source code link to
  https://github.com/tnek213/bwtidy (section 13 of the AGPL: whoever uses the
  running service can get its source). Every link of the page opens in a new
  tab.
- Thanks: a warm paragraph that thanks everyone whose shared code bwtidy
  stands on, then two lists of projects by name (`frontend/src/about/credits.ts`),
  each entry the name as a link to the project's repository, the main language of
  that repository, the license and what bwtidy uses it for. What bwtidy runs
  on: the Bitwarden CLI, Python, FastAPI, Starlette, Pydantic, Uvicorn, HTTPX2,
  the MCP Python SDK, pyrage, cryptography, React, MUI, MUI X Date Pickers,
  date-fns, Emotion, AG Grid Community and TanStack Query. What it is built
  and tested with: Node.js, TypeScript, Vite, Vitest, oxlint, uv, Ruff, ty,
  pytest, Playwright and Vaultwarden. Every direct
  dependency (`dependencies` of `frontend/package.json` and of
  `backend/pyproject.toml`) has an entry, with the license its package names.
- Every package: a sentence that the image also carries the Bitwarden CLI with
  the packages it brings along, licensed by Bitwarden, Inc. under its own terms
  (GPL-3.0). Then a closed accordion "Every package and its license (N)" with a
  table (Package, Version, From, License) of every third-party package bwtidy
  ships.

The table comes from `frontend/src/about/thirdParty.json`, which
`scripts/third-party` writes from the lock files and which is committed: npm,
every package of `frontend/package-lock.json` that is not a dev package (the
project itself left out), and PyPI, every package `uv export --no-dev` names
(one for another platform, like `pywin32`, left out), its license from the
installed metadata (`License-Expression`, else a License classifier that names
one license) or from the script's overrides for a package whose metadata names
none (pyrage). A license it cannot tell is an error naming the package. Each
entry is `{name, version, ecosystem, license, direct}`, sorted by name.
`scripts/ci` fails when the file differs from what the script would write.

## Normalizing

- Rename a custom field across every item that has it (`renameField` with
  `from`, `to` and the items). Every field of that name in an item is renamed,
  type and position kept. An item that already has a field named `to` is a
  merge conflict (409 with `mergeConflicts`), because that is a merge.
- Merge columns: pick two or more columns, choose the target coordinate
  (`mergeColumns` with `sources`, `target`, the items and `resolutions`, item
  id to the column to keep). Items with values in more than one of the merged
  columns are listed for a manual choice instead of being overwritten: without
  a resolution the answer is 409 with `mergeConflicts` (item id and the columns
  holding values) and nothing is written. A resolution is read only where it is
  needed. Values not kept are removed. A field moving into a field the item
  lacks is renamed in place, keeping its type and position.
- Merge into a built-in slot (for example `fields.username` into
  `login.username`) is allowed when the item's type has that slot. For other
  types it is a type conversion (see Writes) and goes through the template
  mapping UI: the merge is a 422 that says so.
- Near-duplicate hints: column names equal after case folding and trimming
  whitespace are grouped and highlighted. `GET /api/near-duplicates` lists the
  groups (`key`, `columns`) of field columns.
- Merge item into item: move chosen leaves of item A into item B (for example
  recovery codes from a separate note into the service's login as a hidden
  field), then trash A (`mergeItems`, mapping `{target column: source column}`).
  The tool note is never A. Values not moved stay in A, so they are in the
  trash and the backup.
- `mergeItems` takes `append`, the target list columns (`login.uris`) that keep
  B's entries and get A's entries added instead of being replaced: B's URIs
  first, then A's URIs that B lacks, each with its own match setting. A column
  in `append` that is not a list column or not in the mapping is a 422. The
  MCP tool `propose_merge_items` always replaces (no `append`).
- The secret rule applies: no merge may move a secret into a non-secret
  coordinate. A new field made by a move gets the type of its source, so a
  hidden value stays hidden.
- bw itself adds `<name>: <value>` to the password history for every hidden
  field an edit renames, changes or removes, and keeps at most 5 entries. So a
  rename or merge of hidden fields can push the oldest entries out of an
  item's history (the backup still has them).
- In the grid: a field column's header menu offers Rename field and Merge
  into, the column chooser selects columns for Merge selected columns, and a
  colored header marker shows near duplicates with a Merge these button. A
  merge is sent without choices first, the items the server lists come back
  as a choice per item (secret values masked, reveal on click), then the merge
  is sent again with the choices. A rename onto a field name that exists
  already is such a merge. Merge into item is a row action: pick the target by
  name, then map the source's leaves to the target's columns. Each row shows
  the column of A, A's value, the column of B it goes into (or Don't move) and
  B's current value of that column (secrets masked, reveal on click), so the
  user sees what a move replaces. A list going into a list B already has values in
  offers Add to the list (the default, sent as `append`) or Replace.
  `login.uris` maps to `login.uris` with Add by default.

## Templates

- A template: name, an optional description, item type, and a list of
  coordinates. A custom field coordinate also carries its field type (0 text,
  1 hidden, 2 boolean). A coordinate can also be a field name pattern
  (`"kind": "fieldPattern"`, see Field name patterns below), which stands for
  the custom fields whose name fits it. A text
  coordinate can carry a `"datatype": ID` (see Datatypes). A template without
  datatypes is made, stored and scored as if datatypes did not exist. Any
  coordinate, `name` too, can carry `"optional": true` (see Optional
  coordinates below), left out when false like `datatype` is left out when
  none, so a coordinate without the key is required. A coordinate is required
  unless it is optional.
- Stored in the tool note as its `templates` section (see The tool note):

      {"version": 1, "templates": [
        {"name": "Computer", "type": 2, "coordinates": [
          {"kind": "field", "name": "user", "fieldType": 0},
          {"kind": "field", "name": "user password", "fieldType": 1}
        ]}
      ]}

- The description is free text of at most 500 characters (422 "A description
  holds at most 500 characters" otherwise), stored as `"description": "..."`
  next to the name and left out when empty, so a template without one reads
  and writes the same. `addTemplate` and `setTemplate` carry it,
  `GET /api/templates` and MCP's `list_templates` answer it when set. It is
  shown in the template editor (a two row Description field under Name, with
  the help "Shown in the list and to an agent connected through MCP."), in a
  Description column of the Templates tab after Name, as help text under the
  Start from select of New item when that template is chosen, and in the
  grid's template column tooltip under the heading. Matching, the fill score,
  the order of several matches and the duplicate warning ignore it, like the
  description of a datatype.
- No marker on items, membership is duck typed: an item matches a template
  when it has the template's type and a non-empty value for every required
  coordinate (for a required pattern, in at least one field that fits it). When several templates match, the one with most required
  coordinates wins (the whole order is below).
- Fill score: per item and template, to find near misses. The item type counts
  as one more thing to match: `total` is the template's required coordinates
  plus 1, `filled` is the required coordinates the item has filled plus 1 when
  its type matches (`typeMatches`), the score is filled/total. A full match has
  filled equal to total, so it still needs the type. `missing` lists the
  template's required coordinates the item has no value for, as grid column
  ids (display strings), empty for a full match. Column names are not secret,
  so MCP carries them too.
- Format terms in the fill score: every required coordinate with a datatype
  adds 1 to `total` and its format score to `filled`: the value's score, the
  lowest over the URIs for `login.uris`, 0 for a missing value, for one longer
  than the value limit and for a boolean field in the item. An optional
  coordinate with a datatype adds its term only when the item has a value for
  it. Each term is floored to two
  decimals, so a partial value never counts as a whole point, and `filled` is a
  number with up to two decimals (whole for a template without datatypes). A
  full match still needs `filled` equal to `total`, so a value off its format
  makes the item a near miss, with the value not in `missing`. An unknown or
  broken datatype counts as no datatype. Among several full matches the one
  with most required coordinates still wins, then the one with most format
  terms, then the one with most optional coordinates the item has filled, then
  the first listed.
- Duplicate templates: two templates with the same item type and the same set
  of (coordinate column, datatype, optional) score alike for every item, so the
  one listed first takes every full match and the other never gets an item. The
  order of the coordinates and the field types (text or hidden) do not change a
  score and are ignored. An unknown or broken datatype counts as no datatype.
  A pattern compares by its text ignoring case, so two templates alike but for
  the pattern are no duplicates, nor is a pattern the same as a field
  coordinate of that very name.
  A duplicate is a warning, not a refusal: saving such a template stays
  allowed. Each template that duplicates one listed before it gets one
  warning, `LATER: same type and fields as EARLIER, which is listed first and
  takes every match.`, naming the first template of its kind (three alike give
  two warnings, both naming the first). The `warnings` of `GET /api/templates`
  carry them, and the template editor shows the same warning under the name
  field while the draft is a duplicate (of a template above it, or when a
  template below it would become one), before saving.
- Field name patterns: a template coordinate can match custom fields by a
  name pattern, so a template can say that fields named like `user_*` exist
  and fit a datatype. Stored as its own kind, `{"kind": "fieldPattern",
  "pattern": "user_*", "fieldType": 0}`, with `datatype` and `optional` as on
  any coordinate. A `*` in the name of an ordinary field coordinate stays a
  literal character.
  - Syntax: a glob where `*` matches any run of characters (also none) and
    everything else is literal, matched against the whole field name,
    ignoring case and surrounding spaces as field names are compared
    elsewhere. A pattern needs at least one `*` and some other character (422
    "a pattern needs a * and some other character"). Its field type is text
    or hidden (0 or 1), never boolean, since a boolean has no format to
    check. Two patterns of one template whose text is the same ignoring case
    and surrounding spaces are refused like a coordinate listed twice. A
    pattern and a field coordinate of the very name `user_*` are two
    coordinates.
  - Which fields: the item's custom fields with a value, less those an exact
    field coordinate of the template names (ignoring case and surrounding
    spaces), which belong to that coordinate. A field that two patterns fit
    counts for the first one listed only.
  - Presence: a required pattern needs at least one such field, an optional
    one is satisfied by none. It counts as one coordinate in `total` and
    `filled` however many fields fit it, and `missing` names it as
    `fields.user_*`. Among several full matches it counts as a coordinate
    like any other.
  - Format: every field it has is scored against the datatype and the
    pattern's format term is the lowest of them (as `login.uris` takes the
    lowest of its URIs). Its `formats` entry is the field that scored lowest,
    under that field's own column (`fields.user_b`), so the Template column's
    tooltip and the grid's format marks name that field. A required pattern
    without a field adds a term of 0 under `fields.user_*`, an optional one
    without a field adds no term. The kept format results and the Statistics
    rows go by the real field's column, so they work per field as for any
    field.
  - Secret: as for any field, what counts as secret goes by each real field's
    type in the item. The pattern's `fieldType` is used only when New item
    creates a field for it.
  - Limit: fields outside the template stay ignored, so a pattern says that
    fields named like it exist and fit, not that every custom field of the
    item follows it.
- Optional coordinates: a template field can be optional, for items that have
  a field only sometimes. An optional coordinate is not needed for a full
  match, adds nothing to `total` or `filled` for its presence and is never in
  `missing`. With a datatype and a value in the item it adds its format term
  like a required one (1 to `total`, its score to `filled`), so a value off
  its format makes the item a near miss, with the column not in `missing`.
  Without a value it adds no term. A template needs at least one required
  coordinate (422 "a template needs at least one required field", and the
  editor refuses it before the request), so an item whose type alone matches
  never matches a template of only optional coordinates. A template with no
  coordinates at all is matched by its type alone. Fields outside the
  template stay ignored. `list_templates` shows `"optional": true` on such a
  coordinate, and its tool description says what optional means for a match.
  It shows a pattern with its kind (`"kind": "fieldPattern"`), and the
  description explains the kind in one sentence.
- MCP and format terms: for MCP a secret column's format term is its presence
  term (1 when set, 0 when not) and it has no `formats` entry, so MCP learns
  nothing about a secret beyond its presence and a pattern can never be an
  oracle on one. What counts as secret goes by the field's type in the item,
  or by the template's `fieldType` when the item has no such field (for a
  pattern, by each field's type in the item). So MCP can call an item a full
  match that the UI calls a near miss.
- Convert to template: pick an item and a template, get a mapping UI that
  maps every existing leaf to a template coordinate or leaves it. Unmapped
  leaves are kept by default. A type change uses the create plus trash path.
  A field name pattern takes only a custom field whose name fits it, which
  keeps its name and type, so nothing for it goes in the mapping.
- Allowed coordinates: `name`, `notes`, the built-in leaves of the template's
  type, custom fields and field name patterns. Names are unique (ignoring case
  and surrounding spaces), a coordinate appears once per template, a pattern
  once by its text ignoring case.
- `GET /api/templates` answers `{"templates": [...], "revisionDate": ...,
  "toolNoteId": ..., "toolNoteName": ...}` with the tool note's revisionDate,
  id and current name. `GET /api/grid` carries `toolNoteId` too, so the grid
  knows the note's row by id, never by name. Notes that hold no valid
  templates (edited by hand) read as an empty list plus `error`, and match
  nothing. `warnings`, only when there are any, names each coordinate whose
  datatype is unknown or broken (a pattern as `the pattern fields.user_*`) and
  each template that duplicates one listed before it (Duplicate templates). `PUT /api/templates` with `{"changes":
  [...]}` changes single templates by name (`addTemplate`, `setTemplate`,
  `renameTemplate`, `moveTemplate`, `removeTemplate`, see
  `setToolNoteSection` in Writes). It validates the changes on the templates
  as the saves under way leave them (422 for an invalid template, a name
  taken or one no template has, nothing queued then), queues a
  `setToolNoteSection` of `templates` with the summary `Save the templates`
  as a `ui` entry that is allowed to touch the note, starts a commit at once
  in the background and answers 202 `{"entryId": N, "templates": [...]}`
  with the templates as the saves under way leave them. A whole list
  (`{"templates": [...]}`) is a 422 that says the templates are saved
  as changes. A `revisionDate` in the body is accepted and ignored: the
  write changes only the templates section of the note as it is then (see
  Writes), so two tabs that save templates both land. `GET /api/templates`
  answers the templates as every pending or committing templates entry
  leaves the stored ones with `"saving": true`, else the stored ones with
  `"saving": false`.
- `GET /api/template-matches` answers per item id (the tool note left out)
  `{"match": name or null, "scores": [{"template", "score", "typeMatches",
  "filled", "total", "missing"}]}`, one score per template in list order. A
  score with format terms also has `formats`: one `{"column", "datatype" (its
  name), "score", "distance", "firstEdit", "expected", "tooLong"}` per
  coordinate with a datatype. The UI gets secret columns too, with `firstEdit`
  and `expected`, never a character of the value.
- The operation `applyTemplate` (`itemId`, `revisionDate`, `template` by name,
  `mapping` `{template column: source column}`): every target must be a
  coordinate of the template. Same type: the mapped values move (a custom
  field source is removed, a built-in source cleared, `name` stays), unmapped
  leaves stay. Another type: a `convertItem` with the same mapping. Either way
  every template field the item lacks is created empty with the template's
  field type, an optional one too so the item shows the slot, and a new
  target field gets the template's field type. A field name pattern creates
  no field (there is no name to give it), so an item without a field that fits
  a required pattern stays a near miss, and a pattern is no target of the
  mapping. A mapping onto an optional
  coordinate is allowed like onto a required one. The
  secret rule applies, a text value into a hidden template field is allowed.
  Committed from the queue, it puts the template's required secret columns
  the item has empty afterwards on the to-fill list (see The tool note). A
  required hidden pattern adds nothing there, it names no column to fill.
- Template design hint shown in the UI: the most copied values belong in the
  built-in login slots (`login.username`, `login.password`, `login.totp`),
  which the browser extension copies straight from the item list. Custom
  fields need the item opened, and autofill matches them by name against form
  input name or id.
- UI: a Types page (app bar) has two tabs, Templates and Datatypes (see
  Datatypes). The page is named Types because it holds both. The Templates
  tab lists, creates, edits and deletes templates.
  Every save sends the change it makes (`PUT /api/templates` with `changes`
  only): a new template is an `addTemplate`, an edit a `setTemplate` after a
  `renameTemplate` when the name changed, a delete a `removeTemplate`. The
  tab has no control that reorders templates, `moveTemplate` exists in the
  API only.
  The editor (and the delete confirmation) closes at once, a refused
  save opens it again with the draft and the reason. While `GET
  /api/templates` says `saving`, the page lists the queued templates with a
  Saving chip and asks again every second. The design hint is shown on the
  page and in the editor.
- Under the type select the editor shows when to use the selected type, two
  or three "Use TYPE for" (Use Login for) and one or two "Not for" examples per type
  (`frontend/src/templates/typeGuidance.ts`), so the user picks types the
  same way every time.
- An editor without coordinates explains them under the heading Coordinates:
  "No coordinates yet. A coordinate is a place in an item
  where the template expects a value: a built-in slot of the item type (such
  as login.username or notes) or a custom field, picked by its name. An item
  matches the template when it has this type and a value in every required
  coordinate. Add a coordinate below, then make it optional or give it a
  datatype if you like. A field name pattern such as user_* covers several
  custom fields at once." It goes away when the first coordinate is added.
- The editor picks coordinates from the vault, so a name is not misspelled.
  Add coordinate adds
  a row whose input is an autocomplete over the built-in slots of the
  template's type and the custom fields of the vault, each with its type
  (`Built-in`, `Built-in, hidden`, `Text`, `Hidden`, `Boolean`), less the ones
  other rows use. Typing filters them. Picking a custom field sets the row's
  field type to the field's, which can still be changed. `GET /api/grid`
  carries the vault's fields as `fields` (`name`, `fieldType`): every custom
  field name some item has, also with an empty value, the tool note left out,
  linked fields left out. Where items disagree, hidden wins (the secret rule),
  else the most common type.
- A field the vault does not have yet takes one step more, like a new folder:
  the last option is always "New field…", which turns the row into a free text
  input (starting from what was typed) with the note "Not in the vault yet". A
  typed name equal to a vault field after case folding and without any
  whitespace is offered as that field instead ("The vault has fields.X
  (Hidden).", Use fields.X), which turns the row back into a pick of that field.
- Next to New field the coordinate input offers "Field name pattern…", which
  turns the row into a pattern row: a small `pattern` chip, a text input
  "Field name pattern N" (starting from what was typed) with the helper text
  "* matches any characters, for example user_*", and the Field type (Text or
  Hidden), Datatype and Optional controls as for a field. The editor refuses a
  pattern without a `*` or with nothing else ("A pattern needs a * and some
  other character.") before the request, and saves it trimmed. A `*` typed in
  a New field name stays part of a literal field name. The template's chip in
  the list ends in ` (pattern)`, or ` (pattern, optional)`.
- Every coordinate row has a checkbox "Optional" (unticked by default) with the
  hover text "Not needed for a match. A value still has to fit its datatype."
  Ticked, it saves `"optional": true` on the coordinate. Another pick in the
  row keeps it. A save with every coordinate optional is refused in the editor
  ("A template needs at least one required field."). The template's chip in
  the list ends in ` (optional)` for such a coordinate.
- Every coordinate row that can take a datatype (not a boolean field) has a
  Datatype select: None by default, then the datatypes by name. A template made
  without touching it is saved with no `datatype` key. Another
  pick in the row keeps the datatype, unless the pick is a boolean field. A
  broken datatype shows as `NAME (broken)` and cannot be picked anew, an id that
  is no datatype shows as `Unknown datatype`, so neither goes by itself on a
  save. The template's chip in the list shows `column: datatype`, and the tab
  shows the `warnings` of `GET /api/templates` (datatypes and duplicates).
- A custom field coordinate that no item has gets a light warning background
  with the hover text "No item has this field", in the editor and on the
  template's chip in the list: a field typed new, or one removed from every
  item while a template still names it. Built-in slots never get it, every item
  of the type has them.
- The grid has a `template` column pinned after `name` (the column chooser
  lists it with the item columns, so it can be hidden): the matching
  template, or else the best near miss with its fill (`Computer 3/4`, filled
  and total of the fill score, the type included, with two decimals when not
  whole: `Computer 4.37/5`). A near miss has at least 2
  matched and is not a full match. The highest score wins, a matching type
  first on a tie. Sorting the column sorts by the score: rows without a
  template, then the near misses from the lowest score, then the matches.
  Hovering the cell shows the template's description (when it has one) under
  its heading: under the name of a match, under `Missing for NAME:` for a near
  miss. Hovering a near miss shows a bullet list of what the item
  lacks: first `Type: note, the template wants login` when the type does not
  match, then one bullet per missing column (`missing`, so required ones
  only), named like the grid's column header (`BIOS password`, `username`,
  `a field named like user_*` for a pattern),
  then one bullet per format below 1
  (see Datatypes). Reason: `3/4` alone does not say what to add. The
  template filter picks the matches of one template, its near misses (items
  whose best near miss it is), or items without a match.
- The row action "Convert to template…" picks a template and shows one row
  per template coordinate with a choice of the item's values (secret values
  masked). The defaults pair equal names after case folding and trimming (a
  custom field's name, a built-in leaf's key). A secret into a non-secret
  coordinate and one value in two coordinates are refused in the dialog. A
  coordinate mapped onto itself is left out of the `applyTemplate` mapping.

## Datatypes

A datatype is a named pattern the user creates (no shipped presets), defined
once and used by template coordinates.

- What it is for: a value of the right shape. It says how well a value matches
  its format, never that a value is right: a pattern checks shape, not meaning,
  so the wording is always "matches format", never "valid".
- bwtidy never writes anything about datatypes into items. Removing a datatype
  leaves every item as it was.
- It applies to text coordinates: `name`, `notes`, the text built-in slots of
  the template's type, `login.uris` (each URI is scored, the lowest counts) and
  custom fields of type text or hidden. Never a boolean field or a number: a
  template that puts a datatype on those is a 422 naming the coordinate.
- Stored in the tool note as its `datatypes` section, a list, with the parser in
  `sections/datatypes.py`:

      {"id": "1f0c9a3e", "name": "PIN", "pattern": "\\d{4,8}",
       "description": "", "ignoreCase": false,
       "examples": ["1234"], "counterExamples": ["12"]}

  `id` is 8 random hex characters made by bwtidy. Names are unique after case
  folding and trimming. Examples must score 1 and counter-examples below 1, at
  most 50 of each. The checks run when the section is queued, when it is written
  and when it is loaded, as the note is untrusted too. A datatype that fails on
  load is kept, marked broken, scores nothing and counts as no datatype. A broken
  one goes only by being fixed or removed: a save with one in it is refused. A
  section that is no list reads as none, with the problem in `error`.
- `"wholeValue": false` is stored only for a datatype whose pattern may match
  part of a value (Whole value unticked in the editor). Without the key the
  pattern matches the whole value. Another value than true or false is
  refused, and `wholeValue: true` is never written.
- A template coordinate names a datatype by `"datatype": ID`. An unknown id counts
  as no datatype (`GET /api/templates` names it in `warnings`), so a template
  survives a datatype that went away by another way than the API.
- Matching runs on our own automaton engine (`patterns.py`), never on a
  backtracking regex engine. The score is an edit distance: d is the least number
  of inserted, deleted or substituted characters that turn the value into a full
  match, and score = 1 - d / max(len(value), shortest match length, 1). A full
  match is exactly 1. With `wholeValue` false the characters before and after
  the matched part are free: d is the least number of edits that turn some part
  of the value into a full match, and len(value) in the score is the length of
  that part (`\d{4}` matches `id 1234` fully). A `^` first in the pattern takes
  away the free characters before (the value starts with a match), an unescaped
  `$` last those after (it ends with one). With `wholeValue` true both anchors
  are allowed and change nothing, and anywhere else they are a pattern error.
  The free characters cost no automaton states (a self loop on the start and
  the accepting state), so the limits and the bound on the work stay as they
  are. `firstEdit` is a position in the value and `expected` holds
  text from the pattern only, so neither shows a character of the value. A score
  in an answer is floored to two decimals like its term in the fill score.
- Results are computed after every load of the vault in RAM, which is also how a
  change of the datatypes or the templates arrives. They are kept in memory only
  (`Formats.results`) by item id, column and the datatype's pattern and
  flags (ignore case, whole value), never keyed by a hash of a value, never logged, dropped by the next load
  and by a logout.
- MCP lists only the datatypes that a non-secret column uses and sees format
  results for non-secret columns only (see Templates, MCP and the queue). No
  MCP tool proposes a datatype or a corrected value.
- UI: the Datatypes tab of the Types page lists the datatypes (name, pattern,
  description, how many template coordinates use it, a broken one marked with its
  reason), with New datatype and per datatype Edit and Delete. Under the intro it
  has the button Pattern limits and statistics, which opens the Statistics page. Delete of one that a
  template uses is refused on the tab with the names of those templates. A save
  sends the change it makes (`addDatatype`, `setDatatype` or `removeDatatype`),
  closes the dialog at once, shows a Saving chip while `GET /api/datatypes` says
  `saving`, and a refused save opens the editor again with the draft and the
  reason, like the templates save.
- The editor is a dialog: Name, Regular expression (monospace), the checkboxes Whole value
  and Ignore case on one line under the pattern, Description, Examples and
  Counter-examples side by side and Try under them, all three one value per line
  (empty lines are left out, spaces stay). The pattern field's helper text says
  `Supports . [ ] \d \w \s ( | ) * + ? {n,m}`, and a small help icon after it
  has a tooltip with the whole syntax of `patterns.py` and what it does not
  take (lookarounds, backreferences, lazy or possessive quantifiers, anchors in
  the middle). Ignore case is about the pattern, so it sits under it, not by the
  name. Whole
  value is ticked for a new datatype and for every one without `"wholeValue":
  false`. While it is ticked the pattern field shows a `^` before and a `$`
  after the typed pattern, in monospace and dimmer than the typed text
  (`text.disabled`), as fixed adornments that cannot be edited. Typing a `^`
  first and an unescaped `$` last (not `\$`) ticks the box and takes both out
  of the typed text. A `^` first or a `$` last alone stays while the box is
  unticked and anchors only that side (starts with, ends with), and goes as
  redundant while it is ticked (also when the box gets ticked). The caret stays
  where the user types. A pattern stored with a `^` first and a `$` last opens
  without them, ticked. While the user types, a debounced `POST /api/datatypes/check` answers: a
  pattern error shows under the pattern with a caret at its column, else the facts
  (states, largest repeat, shortest match) under it.
- Examples, Counter-examples and Try mark every line in a narrow gutter on the
  left inside the box, at the height of its line, updated with the same
  debounced check: a tick for a full match, a cross for anything less. Lines
  never wrap (a long one scrolls sideways), so one value is one line, and the
  gutter scrolls with the box. An empty line has no mark. A line that breaks its
  box's rule (an example that does not match fully, a counter-example that does)
  has a red mark and a faint red background across the box, the others a mark
  in the text color. A Try line is never wrong. While the pattern has an error
  or the check has not answered for a line's value with the pattern and the
  flags on screen, its mark is grey (the earlier answer's tick or cross, or a
  dot for a value never checked). Hovering a mark shows its match in percent,
  its distance and where it differs (`1 change at position 3, expected a
  digit`, positions count from 1). Each mark has a name for screen readers
  (`matches`, `does not match`, `wrong: matches but is a counter-example`,
  `wrong: does not match but is an example`, `not checked yet`). Save stays
  disabled until the examples and counter-examples are right, the name is free
  and the check of what is on screen has answered. Try takes at most 50 lines
  like the others, never affects Save and is never saved. What is
  checked stays in the dialog's state, out of the query cache. The
  wording is always "matches format", never "valid". Above the examples a warning
  says that an agent connected through MCP can read the pattern, the examples and
  the counter-examples of a datatype that a non-secret column uses
  (`list_datatypes`), so they must be made-up values, never a real one.
- In the grid, a cell's datatype is the datatype of its column in the template
  the row's `template` column shows (its match or its best near miss):
  - The template column's hover list gets one bullet per format below 1, under
    the heading `Missing for NAME:`, or `Off format for NAME:` when nothing else
    is lacking: `PIN: matches format 80% (1 change at position 1, expected a
    digit)`, `2 changes, the first at position 3` for more than one, `too long to
    check` for a value over the limit. A secret column has the numbers and
    positions only (`BIOS password: matches format 80% (1 change at position 1)`),
    judged by the grid's column or by the template's coordinate. A column without
    a value has its missing bullet only.
  - A cell with a datatype gets a small round mark in its top right corner, filled
    for a full match, half filled for a score above 0 and empty for 0 (no value
    and too long included). Cells without a datatype have no mark. A cell with a
    queued value has no mark, as the result is about the vault's value, nor a cell
    that cannot hold a value. The mark sits inside the to-fill outline, beside the
    tints of queued, failed and searched cells, and takes no clicks.
  - The cell's hover text is the same line (`PIN: no value` for an empty cell),
    after the value of a text cell. The queued, to-fill and grey texts win over it.
  - Editing a cell with a datatype shows the result live under the input, debounced
    through `POST /api/datatypes/{id}/check`: `matches format`, `matches format 80%,
    expected a digit at position 1` or `too long to check`. That goes for the inline
    editor (a text cell with a datatype edits in a small popup, as the text needs
    room under the input, `notes` in a text area, every other cell keeps AG Grid's
    own editor), the secret dialog (from the first typed character, as it never
    shows the current value) and each URI in the `login.uris` dialog. The value being
    typed is in no query cache. In the grid a value off its format saves like any other,
    the text is a warning and never blocks. The New item dialog does block (a template's
    field must be filled and on format, see Grid, New item), but only the dialog: `POST
    /api/queue` and MCP are not blocked. A
    refused check (a datatype that went away) shows nothing and blocks nothing.
  - The filter `Format` has All, `Off their format` (rows with a format below 1, so
    also an empty value of a coordinate with a datatype) and `Partial` (rows with a
    format above 0 and below 1), by the formats of the template the row shows.
- API:
  - `GET /api/datatypes` answers `{"datatypes": [...], "saving": bool}`, each
    with the stored keys, `wholeValue` always (true or false), the pattern facts
    `states`, `maxRepeat` and `shortest` (null when broken) and `broken` (the reason or null), and `error`
    when the section cannot be read. While a save is under way it lists what that
    save holds.
  - `PUT /api/datatypes` with `{"changes": [...]}` changes single datatypes by
    id (`addDatatype`, `setDatatype`, `removeDatatype`, see `setToolNoteSection`
    in Writes): it validates the changes on the datatypes as the saves under way
    leave them, the whole list they leave checked again (422, nothing queued),
    queues a `setToolNoteSection` of `datatypes` with the summary `Save the
    datatypes`, starts a commit at once and answers 202 `{"entryId": N,
    "datatypes": [...]}` with the datatypes as the saves under way leave them. A
    new datatype comes without an `id` and gets one here, so the id is known
    when the change is queued (an id in an `addDatatype` is a 422). Unknown keys
    (the facts of a GET) are left out. Deleting one a template uses, stored or in
    a save under way, is a 422 naming the templates (one whose template a
    templates save under way removes goes at once), and the write checks it
    again on the note as it is then (a template another save gave it meanwhile
    fails the entry). A whole list (`{"datatypes": [...]}`) is a 422 that says
    the datatypes are saved as changes.
  - `POST /api/datatypes/check` with `{"pattern", "ignoreCase", "wholeValue",
    "examples", "counterExamples", "samples"}` (`wholeValue` true when left out, each list at most 50
    values, a 422 otherwise), for the editor's live preview, answers the facts
    and a result for every example, counter-example and sample (the Try box's
    lines), or `{"error": {"message", "column"}}` for a pattern the engine
    does not take. A result is `{"distance", "score", "firstEdit", "expected",
    "repeatsUsed", "steps", "tooLong"}`. Too long gives score 0, `tooLong` true and
    null for the rest.
  - `POST /api/datatypes/{id}/check` with `{"value"}` answers the result of one
    value against a stored datatype, for the grid's live editing (404 for an
    unknown id, 422 for a broken one).
  - What these two take can be a secret being typed. It is never stored or logged,
    and a refused body is never echoed, so they read the body themselves rather
    than through FastAPI's validation.

## Statistics

A Statistics page, opened with the button Pattern limits and statistics on the
Datatypes tab of the Types page, not from More actions: it is a tool for
tuning patterns, which a normal user only needs once a complicated pattern hits a
limit. It is there so the user can see from real use how close values and
patterns come to the limits of the engine (`patterns.py`) and tune them.

- What is kept (`statistics.py`, one collector per worker process, on the vault
  cache): for every (item, column, datatype) the scoring reached, its last
  scoring: the item name and column, the datatype's name and pattern facts
  (`states`, `maxRepeat`, `shortest`), the length of the value after NFC, the
  distance, the most copies used per counted repetition, the steps, the time it
  took and whether it was too long. A column with several values (`login.uris`)
  has one row: the longest length, the distance that counts, the most copies, and
  steps and time summed. A column without a value has no row.
- Per datatype, counters since the login: values scored, values too long (each
  URI counts as one value) and the slowest value's time, and the total time of the
  last reload. They start over when the datatype gets another pattern, and they
  go when it goes. A broken datatype scores nothing and is not listed.
- Where: in memory only. Every load of the vault scores all values again, so it
  replaces the rows. A logout empties it all, and as a worker exits with its
  session the numbers are per account and start empty with each login. Nothing of
  it is logged or reaches SQLite.
- It never holds a value or a character of one: lengths and counts only. The
  checks behind the live previews do not count, as they score no item.
- MCP cannot reach it: no tool reads it and no answer carries any of it. The
  length of a secret is known to this page only, which needs the session.
- `GET /api/statistics` answers `{"limits", "reloads", "datatypes", "rows",
  "rowCount"}`: the limits from `patterns.py`, how many reloads this session, each
  datatype with its facts and counters, and the slowest 500 rows first, with
  `rowCount` as the number kept. The page sorts those rows by any column.

## MCP and the queue

- MCP server inside the backend process (it holds the unlocked session),
  streamable HTTP at `/mcp` on the web port. Built with the official `mcp`
  Python SDK.
- A toggle in the UI enables it (`POST /api/mcp` with `{"enabled": bool}`,
  session required, answers `{"mcpEnabled": bool}`, `GET /api/session`
  reports `mcpEnabled`). It is disabled after every login. Turning it on
  needs a token that exists and has not expired: without one the answer is
  409 with `{"detail": "Create an MCP token first: MCP token menu,
  Create."}`, and for an expired one `{"detail": "The MCP token has expired.
  Replace it first: MCP token menu, Replace."}`, and MCP stays off. Turning it
  off always works. Disabled or locked (a timed out session counts as locked
  at once): every tool returns the text "bwtidy MCP is disabled. A logged in
  user must enable it in the web UI." and nothing else, not even an argument
  error.
- Every request to `/mcp` needs the header `Authorization: Bearer TOKEN`.
  Without it, with a wrong token or while no token exists, it is answered 401
  with the text "bwtidy MCP needs a token. Create one in the web UI." (and
  `WWW-Authenticate: Bearer`) before the MCP SDK sees it. With the right
  token the toggle still decides whether the tools work. Reason: several
  accounts share one URL (one worker per account behind the gateway, which
  routes an MCP request by its token's hash, see Gateway), and an agent must
  reach only the account its token belongs to. Through the gateway an unknown
  token gets the same 401 from the gateway itself, and the token of an
  account whose worker is not running gets 503 `bwtidy is locked for this
  account. Log in in the web UI.`
- `POST /api/mcp/token` (session required) makes a new token
  (`secrets.token_urlsafe(32)`). It takes `{"days": N}` (an integer from 1
  to 365) or `{"days": null}` for a token without an expiry, and without a
  body or `days` the default of 90 days. It answers 201 `{"token": "...",
  "createdAt": "...", "expiresAt": "..." or null}`. That is the only time
  the token is answered. Only its sha256 is stored, in `mcp-token.sha256` in
  the data directory, written atomically with mode 0600: the hex on the
  first line, the expiry on the second (a UTC ISO timestamp, or `never`)
  and the creation time on the third (a UTC ISO timestamp, the file's own
  time is not kept by every copy), each line ending in a newline. A new token replaces the old one, which stops working at
  once. `DELETE /api/mcp/token` (session required) removes the file and
  answers 204. `GET /api/session` reports `mcpToken` (whether the file
  exists, an expired token too), false while locked like `mcpEnabled`.
  `GET /api/mcp/token` (session required) answers `{"createdAt",
  "expiresAt", "lastUsedAt"}` (each a UTC ISO timestamp or null) and 404
  without a token, never the token or its hash. The token is never stored,
  logged or answered again, the check compares the hashes with
  `hmac.compare_digest`.
- Expiry: a token lives the days chosen when it was made, or until it is
  removed. From its expiry on, a request with it gets 401 with the text
  "bwtidy MCP token expired. Create a new one in the web UI." (and
  `WWW-Authenticate: Bearer`), from the worker and from the gateway, never
  naming the account. Both compare the hash first, in constant time, then the
  time. A wrong token still gets "bwtidy MCP needs a token. Create one in the
  web UI."
- Last use: the worker keeps the time of the last accepted MCP request in
  RAM and in its SQLite settings (`mcp_token_last_used`), updated at most
  once a minute, so it survives the worker. A new token and Remove forget
  it.
- Stateless streamable HTTP, so a backend restart leaves no MCP session for
  the agent to drop. Only requests addressed to 127.0.0.1, localhost or ::1
  are served (the SDK's DNS rebinding protection), so a web page cannot reach
  it through a name of its own. `BWTIDY_MCP_HOSTS` (optional, comma
  separated host names, like `bwtidy.example`) adds each NAME to the allowed
  hosts (`NAME` and `NAME:*`) and `https://NAME` to the allowed origins, for
  bwtidy behind a reverse proxy. Unset, only the loopback names are allowed.
  The gateway passes its own `BWTIDY_MCP_HOSTS` on to every worker, and
  `example.env` leaves it empty for a local run.
- Read tools: `list_items` (id, name, type, folder, organization,
  collections, then every other non-secret value by column, `login.uris` and
  `notes` among them, `attachments` (a list of `{"id", "fileName", "size"}`,
  one per attachment in the item's order, `size` the integer byte count that
  Bitwarden lists, which is the encrypted size, never the file's contents, the
  url or a key, and not repeated in the values), `secretColumns` naming the
  secret coordinates that are set but never their values, the template match and fill scores), with
  optional filters `text` (name, a file name or any non-secret value, never a
  secret one, so the filter is no oracle, and of `login.uris` the uri text
  only, never a match word), `type`, `folder`, `organization`,
  `collection` and `template` (an empty folder, organization or template
  means none). `get_item` (one item, same shape), `list_folders` (each folder's
  `name`, `items` and `itemsInTrash`, plus `itemsWithoutFolder`),
  `list_collections`, `list_templates`, `list_datatypes`,
  `list_near_duplicates`, `list_proposals`.
- `login.uris` in the values of `list_items` and `get_item` is a list of
  `{"uri", "match"}`, in the item's order, `match` the word of Bitwarden's
  setting (`domain`, `host`, `startsWith`, `exact`, `regex`, `never`) or null
  for the client's default (see Coordinates), so the two URI propose tools
  below can work on it. The grid and every other answer keep the plain
  strings.
- `list_proposals` lets an agent see what the user rejected, so it does not
  propose it again. It answers `{"proposals": [...]}`, the entries of source
  `mcp` only, newest first, at most the newest 100, each `{"id", "summary",
  "status", "createdAt", "decidedAt", "reason"}`, with an optional `status`
  filter (pending, approved, rejected, applied or failed, anything else is an
  argument error). `summary` is the one the propose call answered,
  `decidedAt` null while pending. `reason` is null, or for a failed entry one
  fixed word by its result's error: `conflict` (conflicts), `merge conflict`
  (mergeConflicts), `write failed` (writeFailed), `interrupted`, `invalid`,
  and `failed` for anything else. Never a detail, a diff, an operation or a
  value, since a detail can carry one. A silent refusal reads as an entry the
  user has not decided yet, see Silent refusal below.
- `list_datatypes` answers `{"datatypes": [{"name", "pattern", "description",
  "ignoreCase", "examples", "counterExamples", "usedBy": [{"template",
  "column"}]}]}`, with `"wholeValue": false` for a datatype whose pattern may
  match part of a value (its description says so): every datatype that is not broken and that at least one
  non-secret template coordinate uses, with all the template coordinates that use
  it (secret ones too), plus `error` when the datatypes or the templates cannot
  be read. A datatype that only secret coordinates use, or none, is not listed.
  A field coordinate counts as secret when the template says hidden or when any
  item of the vault holds a field of that name as hidden, the safer of the two.
  Reason: an example typed as a real value of a secret column would reach the
  agent. `list_templates` carries a template's
  `description` when it has one. It names the datatype of
  a coordinate that has one as `"datatype": NAME`, so the agent never sees a
  datatype's id. It names a secret coordinate's datatype too, although
  `list_datatypes` may not list it: the name says nothing about a value. A
  coordinate whose datatype is unknown or broken has none, as in the score.
- The secret rule for formats: the fill scores of `list_items` and `get_item`
  count a non-secret column's format with its score and carry its entry in
  `formats` (`column`, `datatype`, `score`, `distance`, `firstEdit`, `expected`,
  `tooLong`, see Templates). A secret column counts as set or not set only and
  carries nothing, the engine does not even run on it for MCP. So the answers
  are byte for byte the same whatever a secret value looks like, as long as it
  is set, and no pattern is an oracle on a secret. The patterns, the examples
  and which secret coordinates use a listed datatype are visible, as they say
  nothing about the values. Those of a datatype that only secret coordinates
  use are not, in case the user typed a real value as an example. Tool descriptions say what a datatype is, that scores are
  "matches format" and that secret columns never show a format. There is no
  propose tool for a datatype or a corrected value.
- Session tools: `end_session` ends the
  session like `DELETE /api/session`, `log_out_of_bitwarden` ends it and
  logs the CLI out like `POST /api/bw-logout` (the next login in the web UI
  runs `bw login` again, bitwarden.com may ask for a device code). Like
  every tool they work only while a session runs and MCP is on, so an agent
  can end the session but never log the CLI out without one. Each answers a
  short text first (never a value) and does the work 0.5 seconds after the
  answer (`BwtidyMcp.after_answer`), so the answer goes out before a worker
  starts its exit. A worker exits with its session (see Worker mode), so
  every later MCP request gets the gateway's 503 until the user logs in again,
  and a clean stop waits for that work (`BwtidyMcp.settle`). The tools carry
  `destructive_hint`, and the instructions tell the agent to call them only
  when asked to.
- Write tools never write. They add an entry to the queue and return its id
  and summary: `propose_move_to_folder`, `propose_set_collections`,
  `propose_move_to_organization`, `propose_move_to_personal`,
  `propose_rename_item`, `propose_rename_field`, `propose_merge_columns`,
  `propose_apply_template` (with a mapping), `propose_convert_item`,
  `propose_merge_items`, `propose_trash_item`, `propose_create_item`, `propose_rename_folder`,
  `propose_delete_folder`, `propose_set_uris`, `propose_set_uri_match`,
  `propose_rename_attachment`. They take
  item ids and column names, never values (except a new item name, folder
  name, URIs and the like), and read the items' current revisionDates themselves.
  Rename field and merge columns default to every item that has the column.
  An optional `reason` is appended to the summary. Tool descriptions say that
  changes are only proposals that the user approves in the web UI and that secret
  values are never available.
- `propose_rename_folder` (`folder`, `new_name`, `reason`) and
  `propose_delete_folder` (`folder`, `reason`) name the folder as
  `list_folders` does and send its name as `shownName`, so a rename made
  elsewhere before the user approves fails the entry. An unknown name, or one two
  folders have, is an error. The summaries are `Rename the folder "OLD" to
  "NEW"` and `Delete the folder "NAME"`. A delete of a folder that holds an
  item is refused with the reason, the description says so.
- `propose_set_uris` (`item_id`, `uris`, a list of `{uri, match}` with match a
  word or null, `reason`) and `propose_set_uri_match` (`item_ids`, `match`,
  `uri_pattern`, `reason`) propose a `setUris` (see Writes), with the item's
  URIs as the agent's view held them as `shownUris`. `propose_set_uris` gives
  one login its whole new list, summary `Set the URIs of "NAME" (N)` with N
  the number of URIs. `propose_set_uri_match` gives, in each item, every URI
  that `uri_pattern` matches the match, the others keep theirs: the pattern
  is a shell glob over the whole URI, case ignored (`*example.com*`), every URI
  without one. Items with no URI whose match it changes are left out (an
  item that is no login has none), and none left is an error. It is one
  queue entry with every item, summary `Set URI match MATCH on N URIs of M
  items` (MATCH the word, `default` for null, N the URIs whose match
  changes). Both descriptions say that no secret value is read or written,
  that the user approves in the web UI, what each match word means and that null
  is the client's default, which is not in the vault. An unknown match word,
  an empty uri or a regex that does not compile comes back to the agent at
  once and nothing is queued. A uri equal to a secret value of the item is a
  silent refusal (below).
- `propose_rename_attachment` (`item_id`, `attachment_id`, `new_name`,
  `reason`) proposes a `renameAttachment` (see Writes) of an attachment as
  `list_items` and `get_item` list it, with the item's `revisionDate` and the
  attachment's name now as `shownFileName`, so a rename made elsewhere
  before the user approves fails the entry. An attachment id the item does not
  have is an error. The summary is `Rename attachment "OLD" of "ITEM" to
  "NEW"`. Its description says that bwtidy renames the file itself once
  the user approves (an upload under the new name and a delete of the old one, so
  the attachment gets a new id), that the contents never pass through MCP,
  and which names are refused. No tool reads or answers an attachment's
  contents.
- `propose_create_item` (`name`, `type` as a word, `login`, `secureNote`,
  `card`, `identity` or `sshKey`, or `template`, `folder`, `organization`,
  `collections`, `values`, `generate_password`, `generate_secrets`, `reason`)
  proposes a `createItem`, `generate_secrets` as its `generateColumns`. A type
  `sshKey`, or a template of type 5, is refused at once, before anything is
  queued: the private key is a secret that MCP never gives, so a new SSH key
  item could only fail with "SSH key private key is required". The message
  says so and points to `propose_convert_item` on an item that holds the key.
  MCP never gives a secret value and has two choices
  per secret field. Left blank, the item shows as near its template (a blank
  field counts as not set in the fill scores, with or without a datatype) and
  the field is on the To fill in list for the user to fill. Or generated, a value
  from bw's generator at write time that MCP can never read: the fill scores
  count it as set and nothing more. The description says that secret values
  are never taken (a value for a secret column is refused with the message of
  the write path), that a secret field is left blank or generated, that the
  blank ones start empty and the user fills them in the web UI, where they show
  up in the To fill in list, that a generated value is one nobody sees through
  MCP, following the default generator settings, and that it is only a
  proposal the user approves.
- `propose_convert_item` (`item_id`, `type` as a word of `propose_create_item`,
  `mapping` {target column: the item's column}, default none, `reason`)
  proposes a `convertItem` with the item's current revisionDate and `newType`
  from the word (an unknown word is an error, so is an unknown item). The
  summary is `Convert "NAME" to TYPE` with TYPE one of the queue's words
  (login, secure note, card, identity, SSH key), followed by the mapped columns
  as `(SOURCE to TARGET, ...)` when there is a mapping, column names only and
  never a value. Reason for the mapping: a secure note that holds a private
  key can become an SSH key item without the secret passing through MCP:
  `{"sshKey.privateKey": "notes"}` maps the notes into the private key, and
  the public key and the fingerprint are filled in from it when they are empty
  when the item is written. What `plan_conversion` refuses (the same type, a
  secret source into a non-secret target) reaches the agent as `refused:
  REASON`, like any proposal, and nothing is queued. The description says that
  the item is created anew with the new type and the old one goes to the trash
  after the user approves, that mapped columns move, that no value passes
  through MCP and that an SSH key needs its private key mapped from a column of
  the item.
- An unexpected error reaches the agent and the log by its type name only,
  its text could carry a value.
- The queue holds many entries. Agent proposals (source `mcp`) wait for
  the user, who reviews them in the web UI as diffs (values masked where secret)
  and approves or rejects them one by one or in batches. An approved batch is
  one operation: one backup, then the writes. Its entries are marked
  `approved` in one transaction once the diffs they keep are computed, so a
  failure there leaves every one of them `pending`. UI edits (source `ui`) are
  queued too and committed on their own (see Auto-commit below), because an
  edit that waits for sync, backup, write and reload feels slow.
- An entry holds one operation of `POST /api/operations` (see Writes), a
  summary, its source and a status: `pending`, `approved` (being applied),
  `rejected`, `applied` or `failed`. An entry stays `approved` only while its
  commit runs, and it is `applied` as soon as its own write is done, before the
  rest of its batch is written. One found `approved` at a start was cut off by a
  kill and fails (see Ending the session). Enqueueing checks the operation against
  the vault in RAM at once (shape, revisionDate, merge conflicts, everything
  the write path refuses), and the write path checks it again at approval.
  Refused at enqueue: any operation touching the tool note (except bwtidy's
  own section saves such as the templates save, see The tool note), a
  `setLeaf` into a secret column
  and a `createItem` with `secretValues` ("the queue never takes a secret
  value") from an agent. Reason: the entry is stored in SQLite, which never
  holds a secret value. A UI `setLeaf` into a secret column and the
  `secretValues` of a UI `createItem` are queued encrypted (see Hidden
  values), only from source `ui`. A `setLeaf`'s column is secret when it is
  on the item in the vault or on the item as the queued `setLeaf`s,
  `removeLeaf`s, `generateLeaf`s, `setUris` and `renameField`s of that item
  before it leave it, so a value without `fieldType` into a field an earlier
  queued edit adds hidden (a hidden `setLeaf`, a `generateLeaf` into a new
  field) or gives the column's name (a `renameField` of a hidden field) is a
  secret too.
- `POST /api/queue` with `{"operations": [...]}` (the shapes `POST
  /api/operations` takes) queues them from source `ui`, one entry per
  operation in order, and answers 201 `{"ids": [...]}`. They are checked at
  once against the vault in RAM, exactly like the write path checks before
  writing but without a sync: 409 with `conflicts` or `mergeConflicts`, 422
  for an operation that is not allowed, and nothing is queued then. The
  entries of one request are stored in one transaction, so a failure stores
  none of them. A
  revisionDate that bwtidy's own commit replaced counts as current (see
  Writes, Own writes). The summary names the kind, the items and the columns,
  never a value. A `createItem` is summarized `Create login "NAME"` (the
  type's word: login, secure note, card, identity, SSH key) or `Create "NAME"
  from template "TEMPLATE"`, plus ` with generated values` with
  `generateColumns`, else ` with a generated password` with `generatePassword`.
- Auto-commit: the backend commits all pending `ui` entries as one approve
  (one backup, one call of the write path, the code of `POST
  /api/queue/approve`) when the oldest of them is at least `afterSeconds` old
  or their count reaches `maxEntries`. Defaults 180 and 20. An account that
  saved its own values keeps them. Only while the vault is unlocked, one
  commit at a time, checked once a second. Entries queued during a commit
  wait for the next one. Entries from MCP are never committed automatically.
  A commit that cannot run (no backup, no vault) leaves the entries pending
  and is tried again 30 seconds later. A `ui` entry that fails at the commit
  gets status `failed` and the result an approval gives (`conflicts`,
  `mergeConflicts`, `invalid`, `writeFailed`). The change log entries of
  these commits have source `ui`.
- `GET /api/queue/auto-commit` answers `{"afterSeconds", "maxEntries",
  "pending", "committing", "nextCommitAt", "commitCount", "phase"}`: `pending` counts the pending
  `ui` entries, `committing` is true for the whole commit (the reload
  included), also for an approval (`POST /api/queue/approve`),
  `nextCommitAt` an ISO time or null when nothing is pending,
  `commitCount` (internal) the number of commits and approvals that ended
  since the worker started (not one that left its entries pending), which the
  frontend watches to reload after one. `phase` says what the write path of
  the running commit or approval does: `syncing` (the sync before the check
  and the one after the backup), `backingUp` (taking a backup or waiting for
  the one under way), `writing` (then also `done`, the operations written so
  far, and `total`, those of the batch) or `reloading`, null while no write
  path runs (also at the start of a commit before it). Reason: a commit can
  take a while when the server is under load, and the user sees where it is.
  `PUT /api/queue/auto-commit` with `{"afterSeconds", "maxEntries"}` saves
  them in the `settings` table: afterSeconds 1 to 3600, maxEntries 1 to 1000,
  else 422. `POST /api/queue/commit` commits every pending `ui` entry now,
  after a running commit has finished, and answers like `POST
  /api/queue/approve`, with empty lists when nothing is pending.
- Ending the session (`DELETE /api/session`, `POST /api/bw-logout`, MCP's
  `end_session` and `log_out_of_bitwarden`, the heartbeat or idle timeout, a
  new login with the right password, a clean stop of the backend) first commits the pending `ui` entries, then ends it. If that commit cannot run,
  fails or is still running after 60 seconds (it is cancelled then, see Session
  and access), the entries stay pending (or failed) and the next unlock commits them
  at once, the same as pending `ui` entries found after a restart (how long an
  entry has waited is known in RAM only).
- A clean stop of the backend waits for a running auto-commit, for a commit
  started in the background and for the commit of a session that timed out,
  before it ends the session (which commits what is still pending). It waits
  60 seconds at most: then it cancels them, and the session's end makes no
  commit of its own (bw does not answer, see Worker mode). A request still
  running 30 seconds after the stop began is cancelled, which cuts off a
  commit of Commit now or an approval the same way. The gateway kills a worker
  120 seconds after its SIGTERM (see Gateway). An entry still `approved` at the next
  start (the process was killed during its commit) gets status `failed`, the
  result `{"error": "interrupted", "detail": ...}` with the reason in `detail`,
  and is never retried (Retry refuses it, see below). Reason: a write of the
  entry may be done, and a create could be made twice. Left `approved` for
  good it would show on no tab, with no message, and its ciphertext could
  not be dismissed.
- A commit that is cut off keeps what it wrote: each entry is `applied` as
  soon as its write is done, so a kill leaves only the unwritten entries
  `approved`. The entry whose write runs when the commit is cancelled (a stop)
  or hit by an unexpected error fails as interrupted at once, its write may or
  may not be done. The entries not started yet go back to `pending` after a
  cancel, so the next commit takes them (at a stop, the session's end right
  after). After an unexpected error they fail as interrupted too, since
  `pending` would repeat that error on every tick of the auto-commit.
- Hidden values: on the first unlock the backend creates an age X25519
  identity (`pyrage.x25519.Identity.generate()`) and stores it in the
  `settings` table (key `hidden_identity`) encrypted with the master password
  (`pyrage.passphrase.encrypt`, like the backups). Every unlock decrypts it
  into RAM, and it is wiped when the session ends. A secret `setLeaf` value
  from the UI and each of the `secretValues` of a `createItem` from the UI are
  encrypted to that identity's recipient before they are stored, so the entry's
  operation holds the armored age ciphertext in place of the value and only the
  unlock pays the scrypt cost. Every API answer shows that value as
  `{"masked": true}`, in the operation as in the diff (a `createItem`'s
  `secretValues` become `{COLUMN: {"masked": true}}`). The value is
  decrypted only when the entry is written. A queued secret `setLeaf` whose
  field does not exist at that moment (an earlier entry of the commit removed
  it) creates it hidden, one whose field an earlier entry of the commit creates
  as text fails (see Writes, the secret rule for a `setLeaf`). The secret
  dialog of a cell whose queued UI edit holds a text value says that the
  value is stored as plain text, as it does for a text field of the item.
  When a login re-encrypts the
  backups for a new master password, the identity is re-encrypted first, with
  each password the tool note lists tried the same way (see The tool note: the
  list shrinks to the master password only once the identity and the backups
  are done). An identity that opens with none of them is replaced, and the
  entries queued under the old one fail at the commit with the reason.
- Silent refusal: an MCP `setLeaf`, `moveToFolder`, `renameFolder` or
  `setUris` whose value (a new name, a folder name, a uri) equals one of the
  item's own secret values (for a folder, of an item in it), also one queued
  from the UI and not written yet (only `propose_rename_item`,
  `propose_move_to_folder`, `propose_rename_folder` and `propose_set_uris`
  send a free value), is not refused with an error.
  It is stored in one INSERT as already `rejected`, with the summary `Refused
  proposal for "<item name>": it would put a secret value of the item into
  <column>` (`folder` for a move, `login.uris` for a `setUris`, `Refused
  proposal for N items: it would put a secret value of an item into folder`
  for a move of several, the same with `login.uris` for a `setUris` of
  several, `Refused proposal for the folder "NAME": it would put a secret
  value of an item in it into its name` for a folder), the value masked in
  the operation (each item's `uris` of a `setUris`) and the diff, and the result `{"error":
  "secretValue", "detail": ...}`. The agent's value and reason are not stored
  in plain. The agent gets the entry id and the same summary it would get
  for an accepted proposal. Reason: no answer, error or
  timing may depend on whether a value the agent chose equals a secret, else
  a refusal confirms a guessed password. The user sees the refused proposal in
  the Rejected tab. The same operation from the UI is still refused with the
  error.
- What `list_proposals` shows of a silent refusal: the summary its propose
  call answered (never the `Refused proposal` text), status pending and no
  decided time, until an entry that is not a silent refusal gets a decided
  time later than the refusal's (an approve, a reject, an auto-commit or
  Commit now, of any source). From then on it is rejected, with that
  decision's time as its decided time (`agent_decided_at`, set once), like an
  entry the user rejected. Reason: until the user decides anything, a refused
  proposal must read exactly like one that waits for them, in shape and
  timing, else listing confirms a guess, and an entry that stayed pending for
  good would make the agent propose it again. The summary the agent was
  answered can hold the guessed secret (a rename's new name, its reason), so
  every entry of source `mcp` keeps it as age ciphertext to the identity for
  hidden values (see Hidden values), and `list_proposals` decrypts each
  listed summary, refused or not. One queued under an identity that was
  replaced reads `(the summary cannot be read)`.
- `GET /api/queue?status=...` lists the entries in id order with the
  operation, the result and a diff: one row per changed cell (`itemId`,
  `itemName`, `column`, `before`, `after`), secret values as
  `{"masked": true}`, a missing value as null, a trashed item as the pseudo
  column `trash` going from false to true. A `createItem` has one row per
  value it gives (`name`, `type`, `folder`, `organization`, `collections`,
  each of `values` and each of `secretValues`, masked) with `before` null and
  `itemId` null (the item has
  no id yet), plus a `login.password` row going to `(generated)` when a
  password is generated. The grid shows no queued value for it, the item has
  no row until it is written. A `setUris` row also has `uris`, both URI
  lists with their match words (see Writes). A pending entry's diff is computed
  against the vault now (`diffError` says why when the operation does not fit
  any more), a decided entry keeps the diff it was decided on. `valueGone` is
  true for a failed entry whose hidden value was deleted at a start (see
  below).
- `POST /api/queue/approve` with `{"ids": [...]}` applies the pending entries
  as one operations call. An entry blamed for a revisionDate conflict, a merge
  conflict or an operation that is not allowed any more fails with the
  reason, and the rest is applied without it, still with one backup. When a
  write fails midway the entries before it are applied, the failed one fails
  and the ones after it go back to pending. A failed one that found its item
  changed right before its write (see Writes) fails with error `conflicts`
  and the item, like a conflict of the check, plus the `backup`. A failed
  backup or an unreachable vault puts every entry back to pending. The answer
  lists the `applied`, `failed` and `pending` ids and the `backup`. `POST
  /api/queue/reject` with `{"ids": [...]}` rejects pending entries. Ids that
  are unknown or not pending any more are a 422 and nothing is decided.
- `POST /api/queue/dismiss` with `{"ids": [...]}` deletes the failed entries
  among them, the age ciphertext of a hidden value with the row (SQLite's
  `secure_delete`, so it does not linger in freed pages). Ids that are unknown
  or not failed are left alone. The answer is `{"dismissed": [...],
  "notFailed": [...]}`. Reason: nobody reads a record of a dismissed failure,
  so it is deleted, not marked.
- `POST /api/queue/retry` with `{"ids": [...], "overwrite": false}` puts the
  failed entries back to pending in one transaction, their result, diff and
  decided time cleared, and approves them at once like `POST /api/queue/approve` (UI and
  MCP entries alike, as one batch with one backup), with the same answer.
  Retried as they are: a write failure (`writeFailed`), an operation that is
  not allowed any more and a hidden value that could not be decrypted (both
  `invalid`). Refused with 409, and nothing is retried: an entry that is not
  failed, one cut off during its commit (`interrupted`, its write may be
  done), a merge conflict (`mergeConflicts`, it needs a choice per item, so
  the merge is proposed again with one), one whose value is gone after a
  restart (below), a conflict (`conflicts`) without `"overwrite": true` and a
  conflict whose item or folder is gone (`entry N changes an item that is
  gone`, `entry N changes a folder that is gone`). No ids or an unknown one
  are a 422. With overwrite a conflict is retried after a sync: its
  revisionDates and the values it says the grid showed (`shownFolderIds`,
  `shownFavorite`, `shownCollectionIds`, and the `shownName` of a
  `renameFolder` or `deleteFolder`, which becomes the folder's name then) are
  taken from the vault as it is then, and nothing else of the operation
  changes. So it writes its own columns over the change made elsewhere, and
  the item's other values stay as they are now. A folder's rename or delete
  goes over a rename made elsewhere the same way. The backup of that write
  holds the other value, which a restore of its change log row brings back.
- A failed entry's hidden value does not outlive its worker. At every start,
  right after the entries left approved fail, each failed entry with an
  encrypted value gets the marker `(gone after a restart)` in place of the
  ciphertext in its operation (each of a `createItem`'s `secretValues`), with
  `secure_delete` like a dismiss, and is marked as having lost its value
  (`valueGone`, the value still answered masked). Such an entry can only be
  dismissed. A pending entry keeps its ciphertext, the next unlock commits
  it.
- A failed entry nobody dismisses is deleted by the worker once it is older
  than `BWTIDY_FAILED_KEEP_HOURS` hours (default 30, a whole number of at
  least 1, counted from the entry's `decidedAt`), the same way as Dismiss
  (`secure_delete`). It runs when the worker starts and then every hour. Only
  failed entries go: pending, applied and rejected ones are kept. A backup
  that a deleted failed entry's result named is not in use any more once the
  entry is gone (see Backups, Retention).
- UI: a Queue page (app bar, with a badge counting the pending entries, polled
  every 10 seconds, see UI edits and the auto-commit) shows each pending
  entry's summary, source, time and diff (secret values masked), with
  checkboxes, Approve selected, Reject selected and Approve all. The row of a
  `setUris` shows the item's old and new URI lists side by side, one line per
  URI with its match word in parentheses (`default` for null). The columns of
  those tables (Item, Column, Before, After) share fixed default widths (22%,
  18%, 30%, 30%) so they line up across entries, and a column grows only when
  its content needs it, a single token that cannot wrap.
  After an approval it names the applied entries and the failed ones with their
  reasons. The grid shows the pending entries too (see Grid). Tabs list the
  applied, rejected and failed entries, a failed entry with its reason and a
  Dismiss button (`POST /api/queue/dismiss`),
  next to it a Retry button (`POST /api/queue/retry`) when the entry can be
  retried, which shows the outcome like an approval. Retry of a conflict
  first asks in a dialog, one line per item of the conflict and column the
  entry writes of it (`ITEM's COLUMN was changed elsewhere since you edited
  it.`) or for the folder of a rename or delete (`The folder NAME was changed
  elsewhere since you edited it.`, NAME as the entry showed it), then `Retry writes your value over that change. A backup is taken
  first, so the other value can be restored from the Change log.`, with
  Cancel and Retry (with overwrite). An entry whose value is gone says `The
  value is gone after a restart. Type it again in the grid.` and a merge
  conflict `Propose the merge again with a choice per item.`, an interrupted
  one only its reason. Each is deleted after `BWTIDY_FAILED_KEEP_HOURS` hours
  without a Dismiss. A rejected one with the detail of its result when it has one (a silent
  refusal). Under the tabs, one line in secondary text says what the shown
  tab's entries mean. Pending: `Waiting for your approval (agent proposals) or
  for the next auto-commit (your grid edits).` Applied: `Written to the
  vault.` Rejected: `Not written: you rejected them, or bwtidy refused a
  proposal that would put a secret value into a plain column.` Failed:
  `Approved, but not written. The reason says why. After a cut-off, the write
  may still have happened.` (an entry that fails as interrupted may be
  written, see above). An MCP switch in the app bar sets the toggle (`POST /api/mcp` with
  `{"enabled": bool}`). Without a token that has not expired the switch stays
  off and a snackbar says "Create an MCP token first: MCP token menu,
  Create." (for an expired token "The MCP token has expired. Replace it first:
  MCP token menu, Replace."), the backend's 409 shows the same text. While MCP
  is on the switch has a white thumb on a green track and the app bar's
  background mixes 20% of the theme's red (`palette.error.main`) into its usual
  color, the primary color in light mode and the dark paper in dark mode. Next to it an MCP token menu offers Create (Replace
  when a token exists) and Remove, and above them, when a token exists,
  when it was created, when it expires (or
  Never) and when it was last used (or Never). Create and Replace first ask
  how long the token lives: 7, 30, 90 (picked at first) or 365 days, or No
  expiry, which shows the warning "A token without an expiry works until you
  delete it.". A created token shows once in a dialog, with the warning "The
  token is shown only now. Copy it before you close this dialog.", the text
  "An MCP client sends the token as the header Authorization: Bearer TOKEN
  to the MCP URL. A new token replaces this one.", when the token expires (or
  that it has no expiry), a copy field "Token" and a copy field "MCP URL"
  with the page's origin plus `/mcp`, and a link "How to connect your MCP
  client" to `/docs/how-to/mcp/` (the docs site the gateway serves, opens in
  a new tab with `rel="noopener"`). The dialog holds no commands: the
  per-client commands (Claude Code, Codex, GitHub Copilot CLI, Gemini CLI)
  are on that docs page (docs/how-to/mcp.md), so there is one place to keep
  them right. The token's text field has `data-shot-hide`
  so the docs screenshot `mcp-token-dialog` shows no token.
  Closing the dialog forgets it. A warning chip next to the menu says `MCP
  token expires in N days` once 3 days are left or 15% of the token's
  lifetime (its expiry minus its creation time), whichever comes first (a
  7 day token from 3 days before the expiry, a 365 day token from 54 days
  before), and `MCP token expired` after the expiry.
- Unread counts: each tab's label counts the
  entries in it this browser has not shown yet, `Pending (2)`, and has no
  count when there are none. A tab shown for 4 seconds without interruption
  marks all its current entries as seen. Switching to another tab, leaving
  the page, hiding the browser tab or a change of the tab's unread entries
  starts the 4 seconds over, so switching away before that marks nothing. The
  seen ids are kept per tab in `localStorage` (key
  `bwtidy.seenQueueEntries`, only the ids still in the tab), per account
  (the key gets `:` and the account's server and email appended in the
  gateway mode, because each account's ids start from 1), so they survive
  a reload, and without storage everything counts as unread again on the next
  load. An entry that leaves a tab does not count there any more and is
  unread in the tab it moved to. The backend knows nothing of what was read.
  While the Queue page is open it polls the applied, rejected and failed
  lists every 10 seconds too, as background requests, so the counts follow
  the auto-commit.
- The tool note is invisible to MCP in every tool: not listed, `get_item`
  says not found, and a proposal naming it is refused as an unknown item.

## UI edits and the auto-commit

The backend side (`POST /api/queue`, `GET`/`PUT /api/queue/auto-commit`,
`POST /api/queue/commit`, when a commit runs) belongs to MCP and the queue.
What the web UI does with it:

- Every UI write goes to `POST /api/queue` with the shapes of `POST
  /api/operations` (source `ui`). A 409 or 422 shows its reason, the merge
  choices work against the 409. Nothing in the UI waits for a
  write, so dialogs are never greyed out and there is no progress bar for a
  save.
- While `ui` entries are pending or a commit runs, the app asks for `GET
  /api/queue?status=pending` and `GET /api/queue/auto-commit` every second,
  else every 10 seconds. An entry that leaves the pending list stays in the
  grid overlay until the commit ended (a status with a new `commitCount` and
  `committing` false) and the grid reloaded, so a cell never flips back
  to its old value for the time of the commit. Then the grid, the templates
  and the change log reload.
- The app bar's page buttons (Vault, Types, Queue and Change log) show
  which page is open: the button of the current page has a 3 px bar under it
  at the bottom edge of the app bar, in the bar's text color, with rounded top
  corners, and its label at full opacity while the other buttons' labels are
  at 80% (full on hover). The bar reads on the usual, the MCP and the dark app
  bar. The current button does nothing when clicked: no ripple, no hover
  background, the default cursor. It is not disabled, so it keeps its label
  color and a screen reader announces it as the current page
  (`aria-current="page"`). No button is current on Settings and Statistics,
  which open from the More actions menu. A button keeps its size when it
  becomes current.
- The app bar shows a Commit now button (`POST /api/queue/commit`) next to
  the Queue button while `ui` entries are pending, and a small spinner while
  a commit or an approval runs. The spinner's tooltip and accessible name say
  what it does, from the status's `phase`: "Syncing with the server", "Taking
  a backup", "Writing 3 of 5 changes" (the change under way, "Writing the
  change" for a single one), "Reloading the vault", else "Saving changes".
  The Queue page asks for the status when it starts an approval, so the
  spinner mostly shows at once and not only at the next 10 second poll.
  There is no separate chip for the unsaved edits, it would repeat the badge.
  The Queue button's tooltip
  explains the badge number, updating every second: a line `N unsaved edits.`
  followed by the countdown to `nextCommitAt` (`Saved automatically in 7 s.`)
  when `ui` entries are pending, a line `M MCP proposals to review.` when
  other entries are pending, and `Nothing pending.` when there are none. The
  button's accessible name says the same counts (`Queue, 2 unsaved edits, 1
  MCP proposals to review`, or `Queue, nothing pending`).
- Failed `ui` entries this browser has not reported yet (their ids are kept
  in `localStorage` per account, as each account's ids start from 1) show
  in a snackbar on every page: each entry's items and its reason. Checked
  after every commit and once when the app starts (a commit at the session
  end may have failed while no tab was open). For a
  revisionDate conflict the rows and cells get the conflict marks (see
  Grid), comparing the rows before the commit with the reloaded ones. The
  cells of other failed entries get a light red tint until the snackbar is
  closed. Dismissing or retrying a failed entry on the Queue page takes back
  what that entry added: its line in the snackbar (closed when none is left), its
  tinted cells and its conflict marks. The marks of other failed entries stay,
  also on the same item.
- The Queue page has an Auto-commit box with the two numbers (seconds 1 to
  3600, changes 1 to 1000) and Save (`PUT /api/queue/auto-commit`).
- The Log out dialog shows "Saving your changes" while its request runs when
  `ui` entries are pending, with the Bitwarden box checked or not (the backend
  commits them before it ends the session).

## Development and tests

Development and tests never touch anyone's vault on a Bitwarden server or
the host's Bitwarden CLI state: they run against a dev stack with a seeded
synthetic vault and never run the backend on the host, as the developer
guide (`dev/README.md` in the repository) describes with the suites, the
screenshots and the docs build.
