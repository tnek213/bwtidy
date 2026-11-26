# bwtidy specification

This document is written mostly for the AI agents that work on the code. You
can use it to look up a fact and the reason behind a rule, but it is not meant
to be read from top to bottom. To learn bwtidy, start with [the user
docs](index.md).

bwtidy is a self-hosted web UI and MCP server for keeping a Bitwarden or
Vaultwarden vault consistent. It shows the whole vault as a wide grid, normalizes field
names across items, applies templates, and lets an AI agent propose
reorganizations without ever seeing secret values.

This document holds the rules and reasons the user docs do not state:
invariants (where a secret value may never go, how a write is protected, how
accounts are kept apart), contracts (stored formats, what the gateway, the API
and the MCP tools answer) and decisions with their reasons, so nobody "fixes"
one back. Every rule is in the present tense. What a user sees and does is in
the user docs, and a section here links to the page instead of repeating it:

- Every page, menu and button: [The web UI](reference/ui.md).
- The variables and the files: [Configuration](reference/configuration.md).
- The MCP tools as an agent's client lists them:
  [MCP tools](reference/mcp-tools.md).
- Why it works the way it does, for a user: the explanation pages, from
  [How bwtidy fits together](explanation/overview.md).
- Where the code lives: [architecture.md](architecture.md).

## The vault it is made for

bwtidy is made for a personal vault of a few hundred items, mostly logins and
secure notes, whose custom field names have grown inconsistent: the same thing
spelled several ways across items, logins stored as secure notes, the same
kind of item in several shapes with different field names. An organization
shares some of the items with other people through collections. The problem
is inconsistency, not size: normalizing is the key feature, and the whole
vault fits in RAM.

## Architecture

The parts (one container, a gateway, a worker and a `bw serve` per account)
are in [How bwtidy fits together](explanation/overview.md) and
[architecture.md](architecture.md). The decisions behind them:

- The Bitwarden CLI (`@bitwarden/cli`) is pinned to 2026.10.0 in
  `docker/bitwarden-cli/`, whose lock file fixes every package it brings
  along.
- bwtidy talks to `bw serve` rather than running one `bw` call per operation,
  because every CLI call pays a Node startup. The login and the backups still
  run the CLI (see Backups).
- The vault is loaded in one listing: a few hundred items need no paging.
- `bw serve` has no authentication of its own, so it is never published. Only
  the web port is published, on the host's 127.0.0.1, which keeps the
  unlocked vault away from other local programs. It is not hacker proof
  (whoever is in the host's `docker` group can reach it), and that is
  accepted.
- The image adds to its base (`python:3.13-slim-trixie`) only what runs: the
  backend's venv, Node.js with the CLI's installed packages, the static files
  and the docs site. npm, uv and the sources stay in the build stages
  (`dev/release.md`), because every package in the image is code a scanner
  reports on and an attacker could use.

## Worker mode

Several Bitwarden accounts use one bwtidy URL, each fully separated from the
others (see [How accounts are kept apart](explanation/isolation.md)). There
are only a few accounts, so isolation and simple code matter more than scale.

- Only a click on Unlock or Log in starts a worker (see Gateway): a worker is
  a Python process of about 100 MB plus its `bw serve` of about 190 MB, and
  opening the page or filling in the account form needs no credential.
- No shared SQLite: the directory tree under `accounts/` is the account list.
- The account's `app/bwtidy.sqlite` is not encrypted. A secret value is in it
  only as an age ciphertext (see Secret values), but the queue's diffs hold
  the plain values and the change log the coordinates each write changed,
  custom field names included (see Writes). The account directory (mode 0700)
  keeps it from other uids. What it holds is documented rather than
  encrypted.
- One account per browser profile at a time. All tabs of the profile share
  the account, so the other tabs reload when it changes (see The account
  form). MCP takes a bearer token per account.
- A deployed bwtidy runs behind a reverse proxy that terminates TLS, reachable
  only over a private network such as a VPN.
- A deploy keeps every account's data (see Schema versions).

The backend becomes a worker through the settings below (the full list with
defaults is in [Configuration](reference/configuration.md#worker)). Without
them it runs as a single-user app (`bw serve` on `127.0.0.1:8087`), which only
the integration tests start, in the image's dev target as uid 1000.
`compose.yaml`, `scripts/dev-app` and the end-to-end tests run the gateway.

- `BWTIDY_SOCKET`: the app listens on this unix socket. A stale socket file
  is replaced, and the file is removed when the app exits.
- `BWTIDY_BW_SOCKET`: `bw serve` listens on the unix socket (`--hostname
  unix://PATH`). A socket file left by a killed `bw serve` is removed before
  every start, else the new one cannot listen. Reason: no process can reach
  another account's unlocked `bw serve` over TCP, since the socket's
  directory belongs to the account's uid. `--port` is still passed and the
  requests still carry `Host: 127.0.0.1:PORT`, because `bw serve` answers 403
  to any Host outside `localhost:PORT`, `127.0.0.1:PORT` and `[::1]:PORT`
  (PORT defaults to 8087), even on a socket.
- `BWTIDY_ACCOUNT_SERVER` and `BWTIDY_ACCOUNT_EMAIL` (both or none, the start
  fails with only one) fix the worker's account. `POST /api/bw-login` with
  another server (compared without a trailing slash and case) or email
  (lowercased) answers 422 `This bwtidy worker belongs to EMAIL on SERVER.`
  `GET /api/session`'s `bwLogin` then carries the fixed server and email
  (never the browser's) and `accountFixed: true`, which is left out
  otherwise.
- `BWTIDY_EXIT_AFTER_SESSION=1`: the worker exits right after its session
  ended, and when no session started within `BWTIDY_IDLE_EXIT` seconds
  (default 600) of its start while no Bitwarden login runs (a login counts
  until its finish or its drop). The exit is a clean shutdown with exit code
  0. Reason: nothing of the vault stays in RAM between sessions. A new login
  that replaces a running session does not exit.
  - The exit counts as settled from the moment the session's end starts (it
    can take long, it commits the pending edits) or the idle time passed.
    From then on `/api/health` answers 503 `{"status": "exiting"}`, so the
    gateway never hands a browser a worker on its way out, and a login that
    reaches the worker anyway starts no session: it answers 502 with the
    gateway's own text `The account's worker does not answer`. The frontend
    reads that like a gateway's 502: it opens the account again and repeats
    the login, and the gateway waits for the old worker to go and starts the
    next. Reason: otherwise a new page's unlock during the old page's log out
    would succeed on the exiting worker, whose exit then drops the session.
- `BWTIDY_BACKUPS_DIR`: the backups are in this directory instead of
  `backups` in `BWTIDY_DATA_DIR`. Everything that touches the backups uses it
  (the backups, the retention, the re-encryption, the backup routes). The
  fingerprints in SQLite name a backup by its file name only.
- `BWTIDY_PENDING_MARKER`: the worker deletes this file after its first
  successful unlock (once the cache, the tool note and the backups' password
  are done). The gateway creates it with a new account's directory, which is
  kept only when `bw login` and the unlock succeed, so the marker tells a new
  account that never logged in from a real one.

Every backend writes `bw-login-state` in `BWTIDY_DATA_DIR`: one line, `logged
in` or `logged out`, mode 0600, swapped in atomically, only when it differs
from what this process wrote last. It notes what `GET /api/session` and the
start of a Bitwarden login find (nothing when `bw status` gave no answer), a
`bw login` or `bw logout` that exits 0 and every successful unlock. Reason:
the gateway shows the unlock or the Bitwarden login before a worker runs, it
never asks a worker that does not run, and this file, the MCP token hash and
the delete marker are all it reads from an account.

### Stopping a `bw` process

Always SIGTERM first, SIGKILL only when it is still there 5 seconds later.
This holds for every `bw` the backend starts: the commands of a backup, `bw
serve`, `bw login` and `bw logout`. Reason: every `bw` process shares one CLI
state, `bw/data.json`, which it rewrites whole (truncate, then write) under
the lock directory `data.json.lock`. A SIGKILL between the truncate and the
write leaves an empty file, which means a logged out CLI (a running `bw
serve` then does not answer its next `/unlock` at all), and it always leaves
the lock behind, which costs the other `bw` processes about 10 seconds until
they call it stale. On SIGTERM node finishes its write and the lock goes with
it.

Even on SIGTERM a dying `bw export` can leave the lock behind, and `bw
serve`'s `POST /lock` then waits those 10 seconds. Were the session's end to
give up on the lock and a new login to unlock, the lock would land a moment
later and the login would find the vault locked, or answer 200 on a locked
`bw serve`. So a session's end never stops a `bw` it does not have to:

- A session's end stops the background backup between its steps: the backup
  checks a flag before each call to `bw serve` or the CLI. The `bw` that runs
  is waited for up to 30 seconds (`BACKUP_STOP_LIMIT`) and only then
  cancelled. Then comes `POST /lock`. A worker's stop (SIGTERM) cancels the
  backup at once, so the 30 seconds never add to a stop.
- A `POST /lock` that gave no answer within its 10 seconds is not dropped:
  `bw serve` still runs it. The next login waits for it before it unlocks, up
  to 20 seconds more (`LATE_LOCK_LIMIT`), then sends no unlock and answers
  503 `bw serve has not finished locking the vault, try again`. While it runs
  it stands for the next lock too. Waiting is chosen over a restart of `bw
  serve`: the lock lands within a second after the 10, while a restart costs
  its stop, its start and the wait until it answers.
- After the cache load the login asks `bw serve`'s status and fails with 502
  `bw serve locked the vault again during the login, try again` unless it
  reports `unlocked`, so a login never answers 200 on a locked `bw serve`.

### Bounded stops

The exit and a SIGTERM end within a bounded time, also when `bw serve` or the
Bitwarden server does not answer (each call to `bw serve` waits up to 120
seconds). Reason: with `bw serve` frozen, an end that waited the full 120
seconds per call would exit the worker minutes late.

- The session's end gives up on its commit after 60 seconds (the entries not
  written stay pending, the one being written fails as interrupted), on a
  running background backup after 30 and on the lock of `bw serve` after 10.
  It goes on either way, and an exiting worker's stop of `bw serve` takes the
  vault out of RAM.
- On a stop, uvicorn waits 30 seconds for the requests still running and
  cancels them (a commit one of them runs is cut off, see MCP and the queue).
  The clean shutdown then waits 60 seconds for the commits under way, cancels
  them when they are not done (a cancelled commit gets 10 seconds to end),
  ends the session (without a new commit after such a cancel) and stops `bw
  serve`. So a stop usually ends before the gateway kills the worker 120
  seconds after its SIGTERM.

### Logs

Every line of the gateway and the workers starts with its time, our own lines
from INFO up, other libraries' from WARNING up (INFO of a library can name
what passes through it, like the URLs of httpx). The login and the session's
end log each step by name (`Login: started`, `Session end: bw serve locked`
and so on, with a refusal's or an end's reason), never a value. Uvicorn
writes its request line only when the answer starts, so the gateway logs when
a `POST` or `DELETE /api/session` starts and what status it got. Reason: a
frozen login or end shows where it stopped.

## Gateway

The gateway (`backend/src/bwtidy/gateway/`, entry point `bwtidy-gateway`) is
the one process that listens. It starts the workers and proxies to them.
Unless the usage stats are on, it never parses, logs or stores anything that
passes through the proxy. With them on it reads and keeps only what Usage
stats names. It logs account keys, uids, worker starts and exits, the session
requests (see Logs) and a request a worker did not answer.

It runs as root (`USER 0`), which it needs to start each worker as the
account's uid. `compose.yaml` drops every capability but `CHOWN`,
`DAC_OVERRIDE`, `FOWNER`, `SETUID`, `SETGID` and `KILL`, sets
`no-new-privileges` and makes the root file system read-only (the two volumes
and a tmpfs on `/tmp` are the only places that take writes).

Its settings are in [Configuration](reference/configuration.md#gateway). The
reasons behind some:

- `BWTIDY_UID_BASE` defaults to 70000 because a host's own service uids often
  take the range from 10000 up.
- `BWTIDY_GATEWAY_SWITCH_UID=0` exists only for the unit tests, which cannot
  switch uids. Otherwise the gateway refuses to start unless it is root.
- The session timeouts, the backup retention and the failed entries' age
  limit are passed on to every worker, so a deployment's policy reaches each
  one. These are policy a deployment may have, so the environment sets them.
  The worker stop time, the `bw` time limits, the display limits and the
  datatype pattern limits stay fixed. A value that cannot be used stops the
  gateway and the app at the start with an error that names the variable.
- `BWTIDY_IDLE_EXIT` is passed on so a test can shorten it, and
  `BWTIDY_MCP_HOSTS` so the public host name passes the worker's DNS
  rebinding check (see MCP and the queue).

### Account key

A server and an email.

- The server is an https URL without user info, query or fragment, with an
  empty path or `/`, lowercased: `https://host`, plus `:PORT` for a port
  other than 443. The directory name is the host, plus `_PORT` for such a
  port.
- Unless the server is in `BWTIDY_ALLOWED_SERVERS`, every address the host
  resolves to must be global (`ipaddress` `is_global`), not multicast, and
  outside `64:ff9b::/96` and `64:ff9b:1::/48` (NAT64), `::/96` (IPv4
  compatible), `::ffff:0:0:0/96` (IPv4 translated), `2002::/16` (6to4) and
  `2001::/32` (Teredo), because `ipaddress` counts some of those as global
  but they lead to an IPv4 address, private ones too. Else 422 `SERVER does
  not resolve to a public address.`, also for a host that does not resolve.
  Reason: any https server, but nothing on the private network or in the
  container's network.
- The check runs again right before `bw login`: a `POST /api/bw-login`
  (exactly that path) routed to such a worker is checked first, and on a
  refusal the gateway answers the same 422 and never forwards it. Known
  limit: `bw` resolves the name itself afterwards, so a DNS rebinding server
  can still win the race. The second check only narrows that window. The rest
  of the login (`/output`, `/input`, `/finish`) is not checked again.
- The email is lowercased, at most 254 characters, matching
  `^[a-z0-9_%+-][a-z0-9._%+-]*@[a-z0-9.-]+[.][a-z]{2,}$` and without `..`,
  else 422. The local part does not start with a dot, as a directory name
  with a dot in front is one of the gateway's temporary ones.
- The account directory is `ACCOUNTS_DIR/HOST/EMAIL`. A path that resolves
  outside the accounts directory or through a symlink is refused (422).

### Account directory

`app/` (`BWTIDY_DATA_DIR`: SQLite, `backups/` without a backups root,
`mcp-token.sha256`, `bw-login-state` and, once Delete my account ran,
`delete-requested`), `bw/` (`BITWARDENCLI_APPDATA_DIR`), `run/` (`app.sock`,
`bw.sock`), `home/` (`HOME`), `worker.log` (the worker's stdout and stderr,
appended) and `.pending` until the first unlock.

- It is made at the account's first Bitwarden login, not when the account
  form opens it, by the gateway as root. Everything is owned by the account's
  uid and gid (the gid is the uid), mode 0700. The accounts directory and the
  host directories are mode 0711, so a worker can enter them but cannot list
  the other accounts.
- It is made under the temporary name `.new-EMAIL` and renamed into place
  once its uid owns everything in it, so an interrupted creation leaves no
  account directory. A name with a dot in front never is an account, and the
  gateway deletes a leftover at its start.
- uid: a new account gets one more than the highest owner among all account
  directories (pending ones too) and all directories under the backups root
  (one can outlive its account directory, so its uid is never handed to
  another account), at least `BWTIDY_UID_BASE + 1`. The uids have no passwd
  entries.
- The gateway refuses an account directory owned by root, by a uid at or
  below `BWTIDY_UID_BASE` or by the owner of another account directory (422
  `The account directory is not usable.`, no worker starts, the reason in its
  log), and logs every such directory at its start. Whatever the base, no
  worker starts as uid 0 or below (503 `The worker's uid is not usable.`).

### Backups root

With `BWTIDY_BACKUPS_ROOT=ROOT` an account's backups live in
`ROOT/HOST/EMAIL` (the same parts and checks as its account directory)
instead of its `app/backups`, so `ROOT` can be any mount apart from the
account data. `compose.yaml` sets it to `/backups`, its own volume.

- Before each worker start, under the account's lock, the gateway makes the
  directory as root: `ROOT` (at the gateway's start) and the host directory
  mode 0711, the account's mode 0700, owned by the account's uid and gid,
  also when it was there already (an account made anew gets a new uid, and
  the old one's files are given to it). It hands the path to the worker as
  `BWTIDY_BACKUPS_DIR`.
- Never through a symlink: when the host directory or the account's
  directory is a symlink or not a directory, the worker is not started (503
  `The worker could not start.`, the reason in the gateway's log). `ROOT`
  itself may be a symlink, the deployment chose it. Reason: the account's uid
  owns its directories and could point a symlink at another account's
  backups, which the gateway reads and writes as root.
- A pending account directory that is deleted takes its directory under the
  root with it, unless that holds backups: they can be from before its
  account directory was deleted by hand, and the root is meant to outlive it.
- `ROOT` and `BWTIDY_ACCOUNTS_DIR` must not be one inside the other (with
  symlinks resolved), else the gateway stops at its start.

### Routes of the gateway itself

- `GET /api/health` (and `/api/health/`) answers `{"status": "ok", "version":
  ...}` from the gateway, never from a worker.
- `GET /gateway/account`: what the page needs before a worker runs, for the
  browser's routing cookie: `{account: {server, email}, running, exists,
  pending, loggedIn, firstRun}`.
  - `running`: the account's worker runs and its `/api/health` answers 200.
  - `exists`: the account directory exists. `pending`: it has `.pending`.
  - `loggedIn`: the line of `app/bw-login-state`, read with the rules of the
    MCP token file (see MCP below), at most 64 bytes. Without a file to trust
    the account counts as logged out.
  - `firstRun`: no account directory has both its SQLite file (a regular file
    reached without a symlink, only its existence counts) and no `.pending`.
    It is the same for every browser (see The account form).
  - Without a routing cookie `account` is null and the rest false, `firstRun`
    aside. `account` stays after the worker exited.
- `POST /gateway/account {server, email, create}` validates and sets the
  routing cookie, answering like the `GET`. It starts no worker and makes no
  directory. A missing directory answers 404 `{"exists": false}` unless
  `create` is true, which sets the cookie for the account without a
  directory: its first Bitwarden login makes it. 422 for a server, an email
  or a directory it refuses. The browser's old routing cookie is forgotten.
- `DELETE /gateway/account` (204) forgets the routing cookie. The worker
  keeps its own session rules. Only the worker of an account that still has
  `.pending` is stopped (SIGTERM), and the answer waits until it exited and
  its directory is gone, so the account form can offer to create it again at
  once.

### Workers

- Workers start on a click: `POST /api/session` (the unlock) and `POST
  /api/bw-login`, exactly these, with a routing cookie are the only requests
  that start the account's worker when none runs. Reason: opening the account
  must not start a worker, about 300 MB for up to 10 minutes without any
  credential.
- Under a lock per account key the gateway starts it, waits until its
  `/api/health` answers, then passes the request on unchanged. A running
  worker whose `/api/health` does not answer 200 is waited for (outside the
  lock, up to 30 seconds, then 503) and a new one started.
- The start of a Bitwarden login of an account without a directory makes the
  directory with `.pending` first, after the server's address check. An
  unlock of an account without a directory answers 401 `{"detail": "No
  account"}`. 503 `{"detail": ...}` when the worker cannot be started (`The
  worker could not start.`), exits or does not answer in time.
- Caps: anyone who reaches the gateway can click Log in or Unlock without the
  master password, and each click starts a worker for up to 10 minutes. So:
  - When `BWTIDY_MAX_PENDING` directories with `.pending` exist, a login that
    would make a new one answers 429 `Too many new accounts wait for their
    first login. Try again in a few minutes.` An existing pending account
    still starts its worker.
  - When `BWTIDY_MAX_WORKERS` workers run or are starting, starting one more
    answers 429 `Too many accounts are open at once. Try again later.`,
    checked before a new directory is made, so a refused login leaves
    nothing behind.
  - A worker counts from the moment its start begins until its start fails or
    it exits. Each check and the reservation it guards happen without an
    await in between, so concurrent clicks cannot pass a cap together.
- A worker starts as root with the `bwtidy` entry point next to the gateway's
  own Python, dropping to the account's uid and gid (`extra_groups=[]`, umask
  0o077, a new session), with an environment built from scratch (the list is
  in [Configuration](reference/configuration.md#worker)). Cwd is the account
  directory.
- The gateway opens `worker.log` without following a link and without
  blocking, and refuses to start the worker (503) when it is not a regular
  file owned by the account's uid (a missing one is made, mode 0600).
- The gateway waits up to 60 seconds for `/api/health` on `run/app.sock`,
  else it kills the worker (its whole process group) and answers 503. One
  worker per account at a time.
- No timeout on proxied requests (only the gateway's shutdown cuts them off):
  reaching a worker needs a routing cookie or an MCP token, and logins,
  backups and MCP streams are long on purpose.

When a worker exits the gateway forgets it.

- With the delete marker `app/delete-requested` in its directory (see
  Deleting data), the gateway deletes the account directory and its directory
  under the backups root even with backups in it, and forgets the browser's
  routing to it. The marker is trusted only the way the MCP token file is, so
  a worker can ask the gateway to delete nothing but its own account. A new
  worker already running in the directory keeps it, and a worker deletes a
  marker it finds at its start, so the account stays when it was opened again
  at once.
- A directory that still has `.pending` is deleted (the login never
  succeeded), unless a new worker already runs in it. A worker that cannot be
  spawned leaves no `.pending` directory either. At its start the gateway
  deletes every account directory that has `.pending`.
- A directory is only ever deleted directly at `ACCOUNTS_DIR/HOST/EMAIL`,
  never through a symlink.

At the gateway's shutdown it waits up to 10 seconds for the requests in
flight (an MCP stream stays open for good) and cuts off the rest. Then every
worker gets SIGTERM and is killed when it still runs 120 seconds later.

- The kill goes to the worker's whole process group, so its `bw serve` (the
  unlocked vault in RAM) and a running CLI command never outlive it as the
  account's uid. The SIGTERM goes to the worker alone, whose clean shutdown
  needs `bw serve`.
- `compose.yaml` gives docker's stop 150 seconds (`stop_grace_period`): more
  than those 10 and 120 plus `bw serve`'s 5. Reason: a docker kill would hit
  every `bw` at once (see Stopping a `bw` process). The 120 seconds are meant
  for a commit with a fresh backup, a background backup the session end waits
  for, and that commit.

### Routing cookie and proxy

- The routing cookie `bwtidy_account`: 32 random bytes, HttpOnly,
  SameSite=Strict, Path=/, no Max-Age, Secure with `BWTIDY_SECURE_COOKIES=1`,
  mapped in RAM to the account key. A gateway restart forgets the map, and
  the frontend opens the account again. The cookie names the account, not the
  worker: it reaches the account's next worker too.
- Every request under `/api/` (except `/api/health`) with a routing cookie of
  a running worker goes to that worker over its socket, streamed both ways,
  with the hop-by-hop headers dropped and the `bwtidy_account` cookie taken
  out of the Cookie header. httpx's `AsyncHTTPTransport(uds=...)` is used
  directly: a client would keep a cookie jar and carry one browser's session
  cookie into another browser's requests.
- Each request gets a connection of its own. Reason: the worker's uvicorn
  closes a connection that idled 5 seconds, and a request sent on a kept
  connection just as the worker closes it is lost. A new connection on a unix
  socket costs next to nothing.
- A request the worker does not answer gets 502 `{"detail": "The account's
  worker does not answer"}`, and the gateway logs the worker's pid, its age,
  whether its process still runs, whether the connection was new and httpx's
  error, never the path or a body.
- Every `Set-Cookie` of the worker's answer that sets `bwtidy_account` is
  dropped: a worker cannot change the browser's routing cookie.
- Without a routing cookie, and without a running worker for anything but
  the two clicks above, those paths answer 401 `{"detail": "No account"}`, so
  the frontend opens the account again.
- Every other path is the gateway's: its own routes, then the routes passed
  in `routers` (see Extra routes and the exit hook), then the documentation
  site, then the static files (mounted last). The workers get no static
  directory, so the frontend always comes from the gateway.

### The documentation site

The gateway serves the site in `BWTIDY_DOCS_DIR` at `/docs/` to anyone,
without a routing cookie or a login. `/docs` redirects to `/docs/`, a missing
page is a 404 of the site, never the frontend, and no request under `/docs`
reaches a worker. FastAPI's own API pages (`/docs`, `/redoc`) are off. How
the site is built is in [architecture.md](architecture.md#the-container).
Its look: a content column wider than the theme's, running text at most 80
characters a line, pictures with a thin border that open larger on a click,
and nothing loaded from a CDN. The site sends another site nothing about the
host it is served from, as the address of a self-hosted bwtidy stays private:
no page requests anything from another host (the header's version and star
count are written at build time, not fetched from GitHub), and the referrer
policy `same-origin` keeps the `Referer` off every link to another site.

### Extra routes and the exit hook

`create_gateway_app` also takes `routers` and `on_worker_exit`, so an outside
package that wraps the gateway (a demo, for one) can add its own pages and
learn when a worker exits, without bwtidy knowing about it. `bwtidy-gateway`
passes neither, so the gateway behaves as described above.

- `routers`: FastAPI `APIRouter`s, included in their order after the
  gateway's own routes and before the documentation site and the static
  files, so a router's path wins over a static file of the same path (`/`,
  `/index.html`) but not over a route of the gateway. Paths the proxy takes
  (under `/api/` and `/mcp`) never reach a router: the proxy runs first.
- `on_worker_exit`: an async callable awaited once with every `Worker` that
  exited, after the gateway's own cleanup (the delete marker, the `.pending`
  directory) and outside the account's lock, so it may take the lock itself.
  It also runs when that cleanup failed. An exception in it is logged by the
  gateway as `The worker exit hook failed for ACCOUNT: TYPE` (the exception's
  type only, its text could hold a value) and never propagates: the gateway
  keeps serving and the worker is still forgotten.

### MCP

An MCP client has no routing cookie, so a request to `/mcp` (or below it) is
routed by its `Authorization: Bearer TOKEN` alone.

- The gateway hashes the token with sha256 and looks for the account
  directory whose `app/mcp-token.sha256` holds that hash on its first line
  (compared with `hmac.compare_digest`, the files read as root on every
  request, there are few).
- A token file counts only when it is a regular file owned by the account's
  uid and reached without a symlink, so a worker cannot point the gateway at
  another account's hash. The same rules hold for `app/bw-login-state` and
  `app/delete-requested`, the only other files the gateway reads from an
  account directory. When two accounts hold the same hash, neither is used.
- No token or no match: 401 with the worker's own text, `bwtidy MCP needs a
  token. Create one in the web UI.`, and `WWW-Authenticate: Bearer`.
- A match whose token expired (the file's second line): 401 `bwtidy MCP
  token expired. Create a new one in the web UI.`, never naming the account,
  whether or not its worker runs. The hash is compared first, then the time.
  A file whose expiry cannot be read holds no token.
- A match whose worker is not running: 503 `bwtidy is locked for this
  account. Log in in the web UI.` (plain text). An MCP request never starts a
  worker.
- A match with a running worker: the request is proxied unchanged, the
  `Authorization` header with it, and the worker checks the token again.

## Usage stats

Off unless `BWTIDY_STATS=1`. The variables, what is kept and the command that
shows it: [Configuration](reference/configuration.md#usage-stats). Reason: a
public demo needs to know whether it is used, what fails and when it was
full, and an operator may want the same of their own install. Off, nothing
changes: no file, no table and no work per request.

The gateway records them, never a worker: each worker runs as its account's
uid and cannot write a file the gateway owns, and only the gateway sees every
account's requests and the workers' cap. `GatewaySettings.stats` and
`GatewaySettings.stats_db` hold the two variables. Nothing leaves the machine,
bwtidy never sends the stats anywhere.

Only what comes from the code is kept: route templates, MCP tool and method
names, exception types and code locations. Never what a person typed or a
vault holds: no item names, values, ids, folder or field names, search text,
request bodies, JSON-RPC ids or arguments, exception messages or arguments,
IP addresses or user agents. A unit test sends markers in all of these and
finds none in the file.

- An event is one request that reaches a worker: its UTC time, the account's
  id, the channel (`ui` or `mcp`), the operation, ok, the HTTP status, the
  duration in milliseconds and the gateway's version. A request the gateway
  answers itself (`/api/health`, the account form, a refused MCP token, a
  401 without a routing cookie) is none.
- A UI operation is `METHOD TEMPLATE`, the template of the first worker route
  that takes the request (`PUT /api/items/{item_id}`), never the path. A path
  no route takes is `other`.
- An MCP `POST /mcp` counts each JSON-RPC message of its body, a batch each
  call: a `tools/call` by its `params.name`, any other message by its method,
  each only when the worker registers that tool or its MCP SDK knows that
  method, else `other`. `GET /mcp` and `DELETE /mcp` are themselves. A body
  that is no JSON or over 2 MiB is one `other`.
- ok: a status of 2xx or 3xx and, for an MCP call with an id, an answer with
  a result that is no tool error (`isError`) and no `error` object. An answer
  over 2 MiB is not read, its calls count by the status alone. The bodies go
  on unchanged and a streamed answer keeps streaming: the gateway reads a copy.
- The account id is the first 16 hex characters of HMAC-SHA256 of the account
  key (server and email) with a random salt that the file makes at its first
  open, so the same account counts as one without its email.
  `create_gateway_app(stats_account=...)` lets a wrapper of the gateway (the
  demo) store its own label instead.
- Errors: an exception a worker did not handle reaches the gateway only as a
  500. A worker the gateway started with the stats on (it sets
  `BWTIDY_STATS_REPORT=1`) adds to that 500 the header `X-Bwtidy-Stats-Error`:
  the exception's type and its frames inside the bwtidy package as (path
  relative to the package, function, line), never its text, its arguments, its
  locals or a log line. The gateway strips the header from every answer,
  stats on or off, keeps an error only when the header holds nothing else,
  and keeps it under the fingerprint sha256 of the type and the frames, with
  its first and last time, its count, and the channel and operation of its
  event. A 500 that a route answers itself (a failed backup, a write that
  failed midway) is a failed event without an error.
- The names: the gateway asks a worker once for `GET /stats/names`, a path it
  never proxies, which answers the templates of its `/api/` routes, its tool
  names and its MCP SDK's client methods. Every worker runs the same code, so
  the first one that answers serves all. Until then every operation is
  `other`.
- Capacity: every 60 seconds a sample of the workers that run or start,
  `BWTIDY_MAX_WORKERS`, and the starts refused with 429 `Too many accounts are
  open at once.` since the last sample. `create_gateway_app(stats_extra=...)`
  gives a wrapper's own numbers as JSON in the sample's `extra`. A wrapper that
  counts sessions gives `sessions`, the sessions started since the last
  sample, which the summary adds up.
- Writes: the request only notes itself in RAM. At most every 5 seconds the
  notes become rows, written in one transaction in a thread. A write that
  fails drops its rows and logs one line (the error's type and SQLite's text,
  no request data) until a write succeeds again. A request never waits for it
  or fails with it. Past 10000 notes between two writes the rest are dropped.
- Retention: at the gateway's start and every day the events of the days
  before the last 200 are counted into `daily` (day, channel, operation, ok,
  count) and deleted, and so are the capacity samples of those days. Whole
  days, so a day is either in the events or in `daily`. `daily` and `errors`
  are kept for good, an error with its count and its last time.
- The file: `BWTIDY_STATS_DB`, else `stats.db` in the parent of
  `BWTIDY_ACCOUNTS_DIR` (`/data/stats.db`). The gateway makes it mode 0600,
  owned by its own uid, in WAL mode. It must be outside the accounts
  directory and the backups root, whose directories the account uids own, and
  a link, a file that is not regular or one of another uid stops the start.
- The summary (`summary` in `stats/summary.py`) is the one plain dict a page
  or `bwtidy stats` shows: the totals (accounts, sessions when a wrapper gives
  them, UI and MCP operations, ok and failed), each operation's ok and failed
  per channel, the errors by operation and type, and the capacity per hour. It
  reads `daily` for the days whose events were rolled up, which count no
  accounts. It never answers an account id, a frame or a fingerprint. The
  command adds the top errors with their frames, for the operator where the
  gateway runs.

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
- The gateway's `stats.db` (see Usage stats) has versions of its own in
  `PRAGMA user_version`, moved up by `MIGRATIONS` in `stats/db.py`: an ordered
  list of numbered steps, each a tuple of SQL statements. Opening runs every
  step above the file's version, each in one transaction that also sets its
  version. A file at a newer version than the code knows stops the gateway's
  start with a log line that names the file, its version and the newest
  version known, and nothing in it changes. Version 1 (step 1): the tables
  `events` (with the index `events_at`), `errors`, `capacity` (with the index
  `capacity_at`), `daily` and `settings`, which holds the salt. Reason: the
  rolled-up days and the errors are kept for good, so a release that keeps a
  new number migrates the file instead of starting it over, and the next step
  is one more tuple in the list.

## Session and access

What a user sees of the login, the session and its end is in
[Logging in](explanation/login.md) and
[The web UI](reference/ui.md#login-pages).

- `POST /api/session` takes the master password and unlocks `bw serve`
  (`POST /unlock`). The password and the session key stay in RAM only, never
  on disk, never in logs. The login pages' password field keeps its name and
  `current-password` autocomplete, so password managers fill it. Only these
  pages invite them (see Grid, Password managers).
- Once the vault is unlocked and the session started, the unlock's side
  effects follow: the background backup, the CLI's login state and the
  deletion of `.pending`. They never fail the unlock: a failure is logged
  with its type and the login still answers the cookie.
- The session key is `data.raw` of the unlock response. Backups pass it to
  the CLI. Every unlock (`bw serve` or `bw unlock`) and `POST /lock`
  invalidate the keys handed out earlier, so nothing else may unlock or lock
  `bw` in the container while a session runs.
- The session is a cookie `bwtidy_session`: 32 random bytes, HttpOnly,
  SameSite=Strict, Path=/, no Max-Age (plus Secure with
  `BWTIDY_SECURE_COOKIES=1`). One session at a time. A new login replaces the
  old one, but only with the running session's master password:
  - While a session runs, `POST /api/session` (and `POST
    /api/bw-login/finish`) checks the password against a hash of the running
    session's one first: scrypt (N 2^17, r 8, p 1, a random 16 byte salt,
    made at the login), run in a thread outside the session's lock and
    compared with `hmac.compare_digest`. A wrong one answers 401 `Wrong
    master password` and changes nothing: no commit, no lock, the session and
    its cookie stay. Without a running session the unlock of `bw serve`
    checks it.
  - After 3 wrong passwords in a row against a running session, each further
    try (also with the right password) is refused with 429 `Too many wrong
    passwords. Try again in N s.` and `Retry-After: N` until N seconds after
    the last wrong one: 1, then doubling up to 60. A right password and
    every new session start the count again. The count is per worker, not
    per address: the gateway and the proxy in front of it hide the address.
    Checks run one at a time, so parallel guesses cannot pass the wait
    together.
  - Reason: anyone who reaches the gateway and knows an account's server and
    email gets a routing cookie for it. Only the logged in browser (its
    session cookie) and an MCP client with the account's token may end a
    session or log the CLI out. The hash is slow so the check is no fast
    oracle for guessing the master password, and it is a hash of its own
    because every unlock of `bw serve` invalidates the running session's key.
  - Accepted: a refusal with a session running takes the hash's time, one
    without takes `bw serve`'s unlock time, so the timing says whether a
    session runs. And when the master password changed on the server during
    a session, a login with the new one is refused until the session ends.
- Every open tab sends `POST /api/session/heartbeat` every 30 seconds.
  Without one for `BWTIDY_HEARTBEAT_TIMEOUT` seconds (default 120, less than
  60 is refused, as it must stay above the 30) the session ends. Reason:
  closing the tabs should log the user out. Basic auth cannot do that,
  browsers keep its credentials until they quit.
- `BWTIDY_SESSION_IDLE_TIMEOUT` seconds (default 7200) without user activity
  end the session too. Heartbeats do not count as activity, nor requests the
  frontend makes on a timer, which carry the header `X-Bwtidy-Background: 1`.
  Every other API request does.
- Ending a session (timeout, logout, a new login with the right password,
  process exit, MCP's `end_session` or `log_out_of_bitwarden`): commit the
  pending `ui` entries (see MCP and the queue), stop a running background
  backup before its next step, lock `bw serve`, wipe the password and the
  session key from RAM, disable MCP. A worker with
  `BWTIDY_EXIT_AFTER_SESSION=1` then exits, except when a new login replaced
  the session. The end never waits long on `bw` (see Worker mode, Bounded
  stops).
- MCP works only while a logged in tab is open, on purpose.
- When `bw serve` exits, the backend starts it again and unlocks it with the
  password from RAM, so a running session survives. If that unlock fails,
  the session ends.
- The CLI must be logged in before anything can be unlocked, and `bw serve`
  does not even start while it is not. So `GET /api/session` always carries
  `bwLogin` (`loggedIn`, `server`, `email`), and the frontend shows a
  Bitwarden login instead of the unlock while `loggedIn` is false.
  `loggedIn` comes from `bw serve`'s status when it answers and knows a
  server, else from `bw status`. `server` and `email` are what the CLI
  reports, else `https://vault.bitwarden.com` and an empty email. While the
  account's worker does not run, the page asks no worker: `loggedIn` is the
  gateway's and the server and email are the account's.
- bwtidy saves no server or email. The Bitwarden login form prefills them
  from the browser's localStorage: the last values typed
  (`bwtidy.accountForm`), else the account in use (`bwtidy.account`), else
  what the session sends. A worker's fixed account always comes from the
  session. Log in stays disabled while either is empty (`bw login` would
  wait for an email). The master password of the form is used for both the
  CLI login and the unlock.
- `POST /api/bw-login` (`server`, `email`, `password`) runs `bw config server
  SERVER` when the server differs from the CLI's, then `bw login EMAIL
  --passwordenv BWTIDY_LOGIN_PASSWORD`, both in a pseudo-terminal, so the
  CLI asks its questions the way it does at a terminal (bitwarden.com mails
  a one-time code on the first login from a new device). The password
  reaches the CLI only through the child's environment and stays in RAM for
  this one login. The answer is a `loginId` that every later call carries in
  the header `X-Bwtidy-Login`, 409 while a login runs or the CLI is logged
  in already, 404 for an unknown token. None of these routes answers 401,
  the frontend reads that as "locked".
- `bw serve` is stopped (with its restarts) before `bw config server` and
  started again when the CLI has exited, whatever its exit code, and on a
  cancel. Reason: a `bw serve` that starts while `bw login` runs reads the
  logged out state and writes it back, which drops the fresh login about one
  time in three.
- The console: `GET /api/bw-login/output?after=N` answers the `text` from
  offset N on, `next`, `running` and `exitCode`. The CLI's prompts redraw
  their line for every typed character, and that is applied before a line is
  shown, so each prompt appears once with its answer. The master password
  and the session key (the `BW_SESSION="..."` lines and after `--session`)
  are replaced by eight bullets before anything is stored. Nothing the CLI
  prints and nothing typed into it is ever logged. `POST
  /api/bw-login/input` with `text` writes that line to the CLI, with
  `password: true` the kept password instead (its echo masked), 409 when
  nothing runs. `DELETE /api/bw-login` kills the CLI and forgets the
  password and the token.
- `POST /api/bw-login/finish`, only after exit code 0: starts `bw serve`
  again when it is not answering, then unlocks exactly like `POST
  /api/session` and forgets the password and the token. Both are also
  dropped when the CLI exits with another code (once that exit was reported)
  and ten minutes after the start.
- `POST /api/bw-logout` needs the session cookie (401 `Not logged in`
  without a valid one, and nothing is logged out) and the header
  `X-Bwtidy-Request: 1` (403 without it): a page on another site cannot send
  a custom header without a CORS preflight, which bwtidy never answers. It
  ends the session like `DELETE /api/session`, stops `bw serve` (one that
  sees the CLI log out stays up but hangs on the next unlock), runs `bw
  logout` and starts `bw serve` again, which keeps retrying until the next
  login. MCP's `log_out_of_bitwarden` runs the same steps.
- Only the logged in browser or the MCP token logs the CLI out. So when the
  session ended behind the page (`POST /api/bw-logout` answers 401, or the
  502 of a worker on its way out, also after the retry), the CLI stays
  logged in and the page asks for the unlock first. The choice sits inside
  the one Log out dialog, so there is no second button or menu entry for
  logging out of Bitwarden.

### The account form

Behind the gateway the page first needs the browser's account (see Gateway).

- Before anything else the frontend asks `GET /gateway/account`. With no
  account it shows the account form, whose Continue posts `/gateway/account`.
  A 404 `{exists: false}` offers to create the account (a second post with
  `create: true`), a 422 shows its reason under the form. A backend without
  the gateway answers 404 to the `GET`, and the frontend goes straight to
  the session.
- Neither the form nor the page starts a worker. With an account the page
  shows the Bitwarden login or the unlock: from `GET /api/session` while the
  account's worker runs (`running`), else from the gateway's `loggedIn`.
- The first-run notice ("Before you start", see
  [Login pages](reference/ui.md#login-pages)) shows while `firstRun` is
  true. It is an info note (role `note`, not an alert, as nothing went wrong)
  and does not block the form. The page asks the gateway again whenever a
  login page shows. Nothing stores it: the gateway derives it from the
  account directories. An account counts once it has its SQLite file and no
  `.pending`. The file alone is not enough: the worker makes it at its
  start, before the login, so a failed first login leaves one in its pending
  directory. Reason: a new user meets the beta caution before the first
  login, an existing one never sees it again, and no new stored data is
  needed for it. The two URLs are in `frontend/src/about/links.ts`.
- After a failed unlock or login the page asks the gateway again (the click
  may have started the worker), and an unlock that failed for another reason
  than the password asks for the session again, so a CLI that turned out to
  be logged out shows the Bitwarden login.
- The account in use is remembered in localStorage. When the gateway does
  not know the browser's account (it restarted, the browser was closed) the
  app posts `/gateway/account` with it by itself and shows the unlock, so a
  returning user types only the master password. A 404 then forgets it and
  shows the form.
- The worker exits with its session, so after a Log out the app opens the
  account again before it shows the unlock or the Bitwarden login. A 401 `No
  account` from the gateway on any request opens it again too. The unlock,
  the start of a Bitwarden login and the log out of Bitwarden are tried once
  more after such a reopen when their request found no worker (401 `No
  account`, or 502 from a worker on its way out). Nothing starts a worker
  for the log out of Bitwarden.
- Switch account ends the session (`DELETE /api/session`, which commits the
  unsaved edits first), calls `DELETE /gateway/account`, forgets the
  remembered account and shows the account form. A reload then stays on the
  form.
- The app bar shows the build commit after the title (see
  [About](reference/ui.md#about)). Reason: the build is then in every
  screenshot, where the Version line of the About page alone is easy to
  miss. It uses the bar's own text colour at a low opacity, never a fixed
  colour, so it follows the color mode and MCP mode.
- The other tabs of the browser profile follow. All tabs share the routing
  cookie, so a tab that kept the old account would send its ids to the new
  account's worker (a queue approval of entry N approves the other account's
  entry N, a templates save replaces the other account's templates). So a
  tab reloads when another tab changes the remembered account and when
  another tab logs out (Log out writes the time under `bwtidy.loggedOut` for
  the others). Accounts are compared by server and email, and a write of the
  same account reloads nothing. A tab that finds its worker gone opens its
  account again only while the remembered account is still its own, else it
  reloads, so it cannot route the shared cookie back to the old account in
  the middle of another tab's switch.

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
  `setUris` see the strings only, MCP shows each URI with its match.
  Bitwarden's `match` is 0 domain, 1 host, 2 startsWith, 3 exact, 4
  regularExpression, 5 never, or null for the client's default, a setting of
  the browser extension that is not in the vault and that bwtidy never tries
  to read.
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
- Read-only info columns: `attachments` (the file names in the item's order,
  found by the search and not a value a merge can move), `passkeys` (count of
  `login.fido2Credentials`) and `revisionDate`. A file name is not secret.
  Logs and backup errors still name an attachment by its id only.
- Never shown or written as leaves: `id`, `object`, `creationDate`,
  `deletedDate`, `passwordHistory`, `login.passwordRevisionDate`,
  `login.fido2Credentials` content, and attachment metadata other than the
  file names (above all the url, which holds a token). The grid row carries
  each attachment's id with its name, for a rename, but no cell shows it.
  They survive every write untouched (see Writes).

## Secret values

Secret coordinates: `login.password`, `login.totp`, `card.number`,
`card.code`, `sshKey.privateKey`, and every custom field with `type` 1
(hidden). What a user sees of them is in
[Secret values and backups](explanation/secrets.md).

- A revealed value is fetched one cell at a time (`GET
  /api/items/{id}/secret?column=...`), answered with `Cache-Control:
  no-store`, and never cached by the frontend. One whose answer arrives after
  its row left the view, changed item or column, or was hidden is dropped and
  never shown.
- A column is secret when any item holds a hidden field of that name, so a
  cell in it can be a text field on its own item. That value is no secret:
  it is plain in `GET /api/grid` and MCP reads it, so the grid shows it plain
  with a mark, and the secret route answers it 404. Reason: the user sees a
  mask only on a value MCP cannot read.
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
  comparison only), and again at write time on the item as `bw serve` has it
  then, which also catches a generated value. The refusal names both columns
  and never a value. Collisions already in the vault stay: only a write of one
  of the two equal values is refused. A folder's new name (`renameFolder`, its
  restore) becomes the folder of every item in it, those in the trash too, so
  it is checked against the secret values of each of them. Reason: without the
  rule two grid edits could make a login's username its password, a plain copy
  in the grid, in MCP answers and in SQLite.
- `name`, `notes`, `login.uris` and text custom fields are not secret. If a
  note holds a secret, the user moves it to a hidden field.
- Secret values never reach logs, the SQLite file, or test output. A queued
  UI edit of a secret column or a queued new item's secret value is in SQLite
  only as an age ciphertext (see MCP and the queue, Hidden values).
- A refused request body is never echoed: it can hold the master password or a
  one-time code. A validation error names the field and the problem only
  (`type`, `loc` and `msg`, the worker's and the gateway's FastAPI validation
  alike, without its `input` and `ctx`). The `msg` is pydantic's own only for
  the error types whose text never quotes the input. A tag error is rebuilt
  from the expected tags without the tag, our own validators raise an error of
  type `bwtidy` whose text we wrote, and every other type gets one fixed text.
  The hand-parsed sections (operations, templates, generator settings) follow
  the same rule.
- A person creating an item may type its secret values (`secretValues`, see
  Writes, `createItem`), MCP never gives one. A `login.password` or a hidden
  field can get a value from `bw`'s generator at write time instead, which
  nobody sees until the user reveals it in the grid (for MCP: blank or
  generated, never a value it gives or can read). There are no placeholder
  values. The secret fields left empty go on the to-fill list (see The tool
  note), so nothing is forgotten.
- `generateLeaf` (see Writes) replaces a value with one from `bw`'s
  generator. The value goes from `bw serve`'s answer into the item and
  nowhere else. The queue and the change log show `(generated)`.
- An example of the generator (`POST /api/generator/example`) is no value of
  an item. It is answered to the dialog that asked and never stored or
  logged, and the frontend keeps it in component state only.

## Writes

The steps of a write as a user meets them are in
[How edits are saved](explanation/edits.md). The rules behind them:

- Every write goes through one serialized write path in the backend (one at a
  time).
- Every UI write is a `ui` entry of the queue (see MCP and the queue) but
  two: the tool note's rename back and keeping its new name go straight to
  the write path (see The tool note). `POST /api/operations` is for scripts
  and tests.
- Every operation (it may touch many items and gets one backup, not one per
  item) has a full backup that matches the vault as it is right before the
  write (see Backups). No backup, no write.
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
  attachment, and the write's backup holds the file. (An empty folder's
  `deleteFolder` deletes the folder, which holds no item.)

### Conflicts

- Before every operation: `POST /sync`, reload the affected items, and abort
  if an item's `revisionDate` differs from the one the operation was based on.
  Reason: other people and the user's other devices edit the vault too.
- Two `revisionDate`s are compared as instants, not as strings
  (`revision.py`): at most 1 ms apart is the same revision, and a value that
  is no timestamp (none, empty) equals only itself. Reason: the server keeps
  more digits than the 3 of `bw serve`'s answers (bitwarden.com 7,
  Vaultwarden 6), so one instant can reach bwtidy in two spellings, cut and
  rounded, and a merge into an item nobody had changed would fail as a
  conflict. Every write gives an item a new `revisionDate`, and no two writes
  of one item are 1 ms apart. bwtidy also keeps, for every item, the spelling
  `bw serve` answered first for a revision (in RAM, ids and `revisionDate`s
  only) and answers that one, so the grid, MCP and a backup's fingerprint see
  one spelling per revision.
- A folder, favorite or collection change made elsewhere is a conflict too.
  The Bitwarden clients change an item's folder and favorite flag through a
  partial update without a new `revisionDate`, and Vaultwarden its
  collections, so the `revisionDate` alone does not show it, and a
  `moveToFolder`, a `setLeaf` of `favorite` or a `setCollections` would
  overwrite the other device's choice without a word. So these operations
  carry what the grid showed, as optional fields: `shownFolderIds` (`{itemId:
  folderId or null}`, items of `itemIds` only), `shownFavorite` (a `setLeaf`
  of `favorite` only) and `shownCollectionIds` (compared as a set).
  - The check is a conflict when the item now differs, unless bwtidy's own
    writes since turned the shown value into the one now (see Own writes).
  - The conflict is the item with its expected and actual `revisionDate`
    (which may be the same) and `column` (`folder`, `favorite` or
    `collections`). In the queue it fails only the entry that carried the
    shown value, another entry of the same item is tried again.
  - The grid row has `folderId` and `collectionIds` for this, and
    `attachments`, one `{id, fileName}` per attachment (never the url or the
    size), for a `renameAttachment`. The grid sends all three shown values,
    and `propose_move_to_folder` and `propose_set_collections` send them as
    the agent's view held them, so a change made elsewhere before the user
    approves fails the entry. An operation without these fields (a script)
    is checked by its `revisionDate`s only.
- After the write's backup bwtidy always syncs once more and reloads, and
  runs the check again when the vault is not the one the check saw. Reason:
  a fresh backup takes long, and a change another client made in that time
  would be overwritten with no conflict. A change to other items passes.
- Right before each item is written, its `revisionDate` in `bw serve`'s
  answer to the `GET` is checked again, and so are the shown values, for
  every kind that writes an existing item, `trashItem` and the steps of
  `restoreItems` included. Reason: a sync inside the batch (after a create,
  a restore's move or untrash, or a backup by hand) can bring in another
  client's change after the check. A mismatch fails that operation as a
  conflict, the operations before it stay written and the ones after it are
  not applied. An item without a `revisionDate` to check (a new one, the tool
  note of a section save, one in the trash) is not checked.
  - A `GET` that answers a `revisionDate` one of bwtidy's own writes turned
    into a later one is asked again, every 0.2 seconds for up to 5 seconds.
    Reason: right after its own `PUT`, `bw serve` answers a `GET` of that
    item for a moment as it was before the `PUT`, and the batch's next write
    of the item would conflict with its own earlier write. One still from
    before the own write after the wait fails as a conflict.
- The server refuses a write on a copy of the item older than its own, and
  bwtidy shows that as a conflict of the item. `bw` sends its copy's
  `revisionDate` as `lastKnownRevisionDate`, and Vaultwarden refuses the edit
  when its stored one is newer by 2 seconds or more ("The client copy of this
  cipher is out of date"), the official server when they differ by more than
  a second ("The item cannot be saved because it is out of date"), both with
  a 400. bwtidy knows the answer by those texts, syncs, and fails the
  operation as a conflict like a mismatch right before the write. Any other
  refusal fails the write with `bw`'s text. An integration test with a
  second CLI login as another device proves it against Vaultwarden. The
  server does not check every change: a partial update of the folder or
  favorite flag, the collections, the trash and its restore, and on
  Vaultwarden an attachment, all pass on a stale copy, and Vaultwarden lets
  an edit less than 2 seconds after the item's last one through. Whether `bw`
  sends the date on a move into an organization is not verified. For those
  only the checks above stand.
- An operation syncs before it reads the first item it writes when the
  batch's last sync (any of them) started more than 5 seconds ago, so the
  check right before the write sees what the server holds now. Reason for
  the 5 seconds: every own write bumps the account's revision, so the next
  sync is always a full one, and a sync before every operation would make a
  commit of many queued edits several times slower. Not covered: a partial
  change made elsewhere after the last sync and before the write is
  overwritten, a window of up to 5 seconds plus the operation's own writes.
  There is no check after the batch: by then the server holds bwtidy's
  values and the other client's change cannot be told from it.
- The sync is `bw serve`'s `POST /sync` without `force`: `bw` first asks the
  server's revision date and skips the full sync when it is not newer than
  its last sync. That last sync time is the client's own clock, so a host
  clock ahead of the server's can skip a change. bwtidy does not force
  anyway: `force` always syncs fully, which costs every sync with nothing
  changed about as much as one after an own write.
- Own writes: the write path remembers, for each item it wrote, the
  `revisionDate` before and after its write (from `bw serve`'s answer to that
  very `PUT`, in RAM, cleared when the session ends). When an operation's
  expected `revisionDate` is a "before" of such a write (in any spelling of
  its instant), it is checked against the "after" instead, following the
  chain. So an edit made while a commit runs, or right after it before the
  grid reloaded, never conflicts with bwtidy's own commit. Any other change
  still conflicts. Nothing that bwtidy did not write itself ever goes into
  the chain. The same writes remember the folder, favorite flag and
  collections the write found on the item and the ones it left: a shown
  value holds when a run of them leads from it to the value now.

### The change log

- Every applied operation is logged in SQLite: time, source `ui`, `mcp` or
  `mixed` (a batch with entries from both), operation, affected item ids,
  the ids of the items it created, the coordinates it wrote, result, backup
  file name. Values and error messages are never logged (either could carry
  a value).
- `createdItemIds` (table column `created_item_ids`, a JSON list): the new
  item of a `convertItem`, of an `applyTemplate` across types, of a
  `moveToPersonal`, of a `createItem` and of a `restoreItems` that created an
  item anew. Reason: a restore of the row must find them.
- A row of a folder kind (`renameFolder`, `deleteFolder`, `restoreFolder`)
  has no item ids and names its folder in `folderId` (column `folder_id`,
  null on every other row), for a restore that created the folder again the
  new one. A `renameAttachment` row has `attachmentIds` (column
  `attachment_ids`, null on every other row): `{"before": id, "after": id}`,
  `after` null when the write failed before a copy was kept. A restore's row
  also has `restoredBackup` and `restoredChangeId` (see Restoring a change
  log row).
- `columns` (a JSON list), named like the grid's columns: the `column` of a
  `setLeaf`, `removeLeaf` and `generateLeaf`, `folder` for a `moveToFolder`
  and the folder kinds, `collections` for a `setCollections`, `organization`
  and `collections` for a `moveToOrganization` and a `moveToPersonal`,
  `attachments` for a `renameAttachment`, both names of a `renameField`, the
  sources and the target of a `mergeColumns`, the target columns of a
  `mergeItems`, `login.uris` for a `setUris`, and for a `convertItem` and an
  `applyTemplate` each mapped source and target plus every coordinate the
  plan changes on the item as the write found it, plus `sshKey.publicKey`
  and `sshKey.keyFingerprint` when the write derived them. The read-only
  columns are left out. A `trashItem`, `createItem`, `restoreItems` and
  `setToolNoteSection` work on whole items and log none. A custom field's
  name is vault content, and the log holds it anyway: knowing what each
  change touched is worth that, since the names are plain in the grid, in
  MCP answers and in the queue's diffs already.
- `GET /api/change-log` answers the newest 200 rows that match (newest
  first, a JSON list), with the header `X-Bwtidy-More: true` when more rows
  matched (`false` otherwise). Optional query parameters, combined with AND:
  `from` and `to` (UTC instants, inclusive, a fraction of a second of `from`
  counts as the next whole second), `source`, `kind`, `column` (a row matches
  when its `columns` hold any of them), `item` (its `itemIds` or
  `createdItemIds`), each of those four repeatable, and `result` (`ok` or
  `failed`). An unknown `source` or `result` or an instant that does not
  parse is a 422. The filter runs in SQL over the whole log.

### Operations

`POST /api/operations` takes `{"operations": [...]}`, the whole list being
one operation in the sense above: one sync, one check of every touched item,
one backup, one more sync, then the writes one by one.

- 409 with the conflicting items (expected and actual `revisionDate`, and the
  `column` of a shown value) when an item changed or vanished, 422 for an
  operation that is not allowed, 500 naming the step when the backup fails.
  Nothing is written in those cases.
- When a write fails midway, the response says which operations were
  applied, which failed and which are pending, plus the backup name and
  `conflicts` (the item the failed one found changed right before its write
  or the server refused as out of date, else empty), and the vault is
  reloaded.
- Kinds: `setLeaf`, `removeLeaf`, `moveToFolder` (a missing folder is
  created, an empty name means no folder), `setCollections` (organization
  items only), `moveToOrganization` (`POST /move/{itemId}/{organizationId}`,
  personal items only), `moveToPersonal`, `trashItem`, `convertItem`,
  `applyTemplate` (see Templates), `renameField`, `mergeColumns`,
  `mergeItems` (see Normalizing), `setUris`, `restoreItems` and
  `restoreFolder` (see Restoring a change log row), `createItem`,
  `generateLeaf`, `renameFolder`, `deleteFolder`, `renameAttachment` and
  `setToolNoteSection` (bwtidy's own only). No other operation may touch the
  tool note.

#### `renameFolder` and `deleteFolder`

`renameFolder` (`folderId`, `shownName`, `newName`) renames a folder (`PUT
/object/folder/{id}`), `deleteFolder` (`folderId`, `shownName`) deletes an
empty one. Reason: in Bitwarden itself, renaming a folder means moving every
item and deleting the old folder by hand. Neither writes an item.

- A folder has no `revisionDate`, so `shownName` is its name as the client
  showed it, and a folder that has another name now or is gone is a conflict
  (409, `conflicts` holding `{folderId, expected, actual}` with the names,
  `actual` null for a gone folder), checked like an item's: at `POST
  /api/queue`, in the write path's check and right before the write. In the
  queue it fails only the entries of that folder, and Retry with overwrite
  renames or deletes the folder under the name it has then.
- Refused (422) for a rename: an empty name (also spaces only), the folder's
  name now, a name another folder has ignoring case (`another folder is
  named "X"`) and a name equal to a secret value of an item in the folder.
  Refused for a delete: a folder that holds an item, active (`the folder
  holds N items, move them out first`) or in the trash (Bitwarden would take
  the item out of the folder too). Both are checked again at write time.
- Each write's answer goes into the cache and into the batch's own view of
  the folders, as `bw serve` lists a folder as it was before an own write
  for a moment, so a later operation of the batch sees the folders as
  written.
- The queue's diff is one row of the folder without an item id (`itemName`
  the folder's name now, column `folder`, `before` its name, `after` the new
  name or null), the summaries `Rename the folder "OLD" to "NEW"` and
  `Delete the folder "NAME"`.

#### `setUris`

`setUris` (`items`, a list of `{itemId, revisionDate, shownUris, uris}`, each
of `uris` and `shownUris` a list of `{uri, match}` with `match` 0 to 5 or
null) replaces the whole URI list of each login with `uris`, in its order
and with every match exactly as given. Only MCP proposes it. Reason: an
agent can then see and fix the match settings, so a login that autofills on
the wrong site or on none can be fixed without ever reading or writing a
secret.

- Refused (422) at `POST /api/queue`, in the check and at write time alike:
  an item that is not a login (`only a login has URIs`), the tool note, no
  items or one named twice, a uri empty after trimming (`URI N is empty`), a
  match other than 0 to 5 or null, and a uri with match 4 that Python's `re`
  cannot compile (`URI N is not a valid regular expression`, Bitwarden
  evaluates it as a JavaScript one, so the check catches only what Python
  rejects too). The messages name the URI by its position, never its text.
- A uri equal to a secret value of the item is refused like any plain value,
  checked after every other refusal, so an error never tells whether a uri
  equals a secret.
- `shownUris` is a shown value like `shownFolderIds`: when the item's list
  differs now, the item is a conflict with `column` `login.uris`. Unlike a
  folder, bwtidy's own writes since do not make it hold: the whole new list
  would undo a grid edit of the URIs committed after the proposal. Every
  item is checked before the first one is written. Retry with overwrite
  takes `shownUris` from the vault then.
- The queue's diff has one row per item in `login.uris`, `before` and
  `after` the uri strings, plus `uris` with the lists of `{uri, match}`,
  each match as its word or null. The summary of a UI entry is `Set the
  URIs of "NAME"` (`Set the URIs of N items`).

#### `renameAttachment`

`renameAttachment` (`itemId`, `revisionDate`, `attachmentId`, `fileName` the
new name, `shownFileName` the name the proposer saw) gives an attachment a
new file name. Both an agent and the user in the grid queue it: what MCP can
do, the UI can do too. The contents never pass through MCP.

- In place is not possible through `bw serve`: an item `PUT` with another
  `fileName` answers success, gives the item a new `revisionDate` and leaves
  the name as it was, and `bw serve` has no other route for it. bwtidy never
  calls the server's API around `bw serve`. So the write is: `GET
  /object/attachment/{id}?itemid=` (the bytes, in the write's RAM only,
  never logged or kept), `POST /attachment?itemid=` under the new name, a
  check that the answer lists exactly one new attachment with the new name
  and the old one's `size`, then `DELETE` of the old one. The attachment
  gets a new id.
- When the upload or anything after it fails, the copy is deleted again and
  the entry fails, so the item keeps exactly one: while the delete of the
  old one has not started, at once (after a failed upload the copy the item
  lists anyway, found by its name and an id it did not have), and after it
  started only when the item still lists the old one (a delete that failed
  late may have happened). A copy that cannot be deleted again is logged by
  its id and both stay.
- On Vaultwarden neither the upload nor the delete changes the item's
  `revisionDate`. The own write is recorded like a `PUT`'s (see Own writes)
  from the upload's answer and from a `GET` right after the delete, so an
  edit queued before the rename never conflicts with it.
- Refused (422): the tool note, an `attachmentId` the item does not have
  (`the item has no attachment with the id ID`), an empty new name, one with
  a slash or a backslash (the backups file an attachment under
  `item-attachments/<itemId>/<fileName>`), the name it has now, and the name
  of another attachment of the item (`the item has another attachment named
  "NAME"`). Names are compared ignoring case (`str.casefold`), so a download
  of both to a file system that ignores case never overwrites one with the
  other. Bitwarden itself allows two attachments of one name, bwtidy does
  not. Changing only the case of the attachment's own name stays allowed.
- Two renames of two attachments of one item to the same name end with the
  first written: queued in one request, the second is refused at once,
  queued in two, the second entry fails at the write.
- The file name is no secret, so the rule of equal values does not check it.
- A conflict when the item's `revisionDate` differs, and when the attachment
  has another name than `shownFileName` (a rename elsewhere leaves the
  `revisionDate` as it is): `{itemId, expected, actual, column:
  "attachments"}`, checked at `POST /api/queue`, in the check and right
  before the write. Retry with overwrite takes the name the attachment has
  then. A second rename of the same attachment queued before the first is
  written fails, its attachment id is gone.
- The queue's diff is one row of the item in the column `attachments`
  (`before` its file names now, `after` with the new one in place), the
  summary `Rename attachment "OLD" of "ITEM" to "NEW"`.

#### `setToolNoteSection`

`setToolNoteSection` (`section`, one of `templates`, `toFill`, `generator`,
`datatypes` and `note`, and `changes`, no `revisionDate`) saves one section
of the tool note's notes. Only bwtidy's own code queues or writes it: `POST
/api/queue`, `POST /api/operations` and MCP refuse it like every other
operation on the note.

- At write time the write path GETs the tool note as it is then, applies the
  changes to that section and PUTs the note (nothing when the notes stay as
  they are), so it never works on a stale copy and never conflicts on the
  note's `revisionDate`. Several such entries in one commit apply one after
  the other.
- `changes` is a list of changes of one entry each, applied in order.
  Reason: two tabs that save templates or datatypes would otherwise lose
  each other's change, and a save of the whole section would undo another
  save built on the same base.
  - `generator`: `{"op": "setDefault", "options"}`, `{"op": "setItem",
    "itemId", "options"}` and `{"op": "removeItem", "itemId"}`.
  - `toFill`: `{"op": "addColumn", "itemId", "column", "since"}` (`since`
    only for a new entry) and `{"op": "removeColumn", "itemId", "column"}`
    (the entry goes with its last column).
  - `templates`, known by name (they have no id): `{"op": "addTemplate",
    "template"}` (last in the list, refused when the name is taken ignoring
    case), `{"op": "setTemplate", "template"}` (replaced in its place),
    `{"op": "renameTemplate", "name", "to"}`, `{"op": "moveTemplate", "name",
    "index"}` (the last place for a larger index, since the first of several
    matches wins) and `{"op": "removeTemplate", "name"}`. A change that
    names a template a save before it renamed or deleted fails its entry, it
    never writes the old template back.
  - `datatypes`, known by id: `{"op": "addDatatype", "datatype"}` (the id
    given when it is queued), `{"op": "setDatatype", "datatype"}` (fails its
    entry when the datatype is gone) and `{"op": "removeDatatype", "id"}`
    (nothing when it is gone already, refused while a template of the note
    as it is then uses it).
  - `note`: `{"op": "setName", "name"}` (a name that is not empty).
- Every item id is followed through the conversions this session committed
  (`convertItem`, `moveToPersonal`, an `applyTemplate` that changes the
  type), so a change queued before a conversion and committed after it lands
  on the new item.
- A `generator`, `toFill`, `templates` or `note` section that does not parse
  starts over empty. The `datatypes` keep their broken entries, so the
  section still fails its check until a broken one is fixed or deleted. The
  `generator` write drops the settings of items that are gone (the trash
  counts, an item a conversion made since the batch began does not).
- The changes are checked against their section, and the section they leave
  by its parser as a whole: when they are queued, on the note as the saves
  under way of every section leave it (422), in the check before the backup
  (such an entry fails there and the rest goes on), and at write time.
- Every read of a section while saves of it are pending or being committed
  (the routes that say `saving`, the base of the next change, the queue's
  diff) applies all of them over the note in queue order, the same way the
  writes will. `bw serve`'s answer to the note's `PUT` replaces the note in
  RAM at once, so once an entry is `applied` every read shows the saved
  value, also before the reload after the batch. Otherwise a save built on
  that read would be refused for a name it held twice.
- The check when queued takes the saves of the other sections too: a
  datatype goes right after the save that removed the template using it,
  while that save waits.
- Its change log row names the tool note in `itemIds`, so its Restore is
  refused like the note's.

#### `setLeaf` and field types

- The secret rule for a `setLeaf`: a value equal to one of the item's own
  secret leaves may not go into a non-secret column of that item, and a
  value for a secret column may not equal a plain value of the item (see
  Secret values). Both are checked at `POST /api/queue` on the item with its
  pending `setLeaf`, `removeLeaf`, `generateLeaf`, `setUris` and
  `renameField` entries and the earlier operations of the request applied,
  in the write path's check with the earlier operations of the batch
  applied, and at write time on the `GET` of the item. The same rule refuses
  a `moveToFolder` whose folder name equals a secret value of a moved item,
  and at write time a `generateLeaf` whose generated value equals a plain
  value of the item.
- Changing an existing custom field never changes its type, a new field gets
  the requested type (text by default). A hidden `fieldType` for a field the
  item already has with another type is refused (422, nothing stored): the
  value would stay in the open as a text field, while a secret column's
  dialog promises secrecy. Checked in batch order and again on the item the
  write GETs, so a hidden value for a field that an earlier operation of the
  same commit creates as text fails its entry and nothing of the value is
  written.
- A `setLeaf` into `fields.<name>#2` of an item without a value in
  `fields.<name>` is refused: the occurrence waits for the one before it.

#### SSH keys

An SSH key item (type 5) gets its `sshKey.publicKey` and
`sshKey.keyFingerprint` from its `sshKey.privateKey` whenever bwtidy writes
it with a private key and one of the two empty (a create, a conversion, a
restore that creates the item anew, every `PUT`). Reason: `bw` refuses an
SSH key without all three and fills in none itself, and MCP never gives a
private key, so without this a secure note holding a key could not become
an SSH key item.

- A value already there is kept, even one that does not match the key. The
  public key is the OpenSSH line without a comment, the fingerprint
  `SHA256:` and the unpadded base64 of the SHA-256 of the public key blob.
- Read are Ed25519, RSA and ECDSA (P-256, P-384, P-521) keys in OpenSSH form
  or PEM (PKCS#1, SEC1 or PKCS#8), with Windows line ends and spaces around
  them too. A key with a passphrase fails the operation with "the private
  key has a passphrase", any other text with "the private key cannot be
  read", and nothing of that item is written. Neither text holds a character
  of the key, and neither the key nor the derived values are logged.
- The derived columns are not secret. The queue's diff of a `convertItem` or
  an `applyTemplate` shows them plain when the private key comes from the
  item itself. A queued hidden value is a ciphertext, so the diff of a grid
  edit or a new item shows the masked private key only. A key that cannot
  give them fails at the write, never in the diff.

#### `convertItem` and `moveToPersonal`

- `convertItem` maps `{target column: source column}`. A mapped custom field
  moves, mapped `notes` are cleared, `name` stays. Unmapped custom fields
  come along as they are. An unmapped built-in leaf of the old type becomes
  a custom field named after its key (`login.password` becomes `password`),
  hidden when the leaf is secret, `login.uris` joined with newlines.
  Passkeys and password history cannot be copied and are lost with the old
  item (it is in the trash and the backup). Attachments are downloaded and
  uploaded again after a `POST /sync`, because `bw serve` knows a created
  item only after a sync. The old item is trashed last. If anything after
  the create fails, the new item is trashed again.
- `moveToPersonal` (`itemId`, `revisionDate`) takes an organization item
  into the personal vault. Bitwarden has no call for it, so it works like
  `convertItem`: create the item anew with everything as it is except
  organization and collections, copy the attachments, then trash the old
  item, with the same losses. Everyone who shares the item's collections
  loses it. Refused (422) for a personal item and for the tool note. Moving
  between organizations is out of scope.

#### `generateLeaf`

`generateLeaf` (`itemId`, `revisionDate`, `column`) replaces the value of
`login.password` of a login or of a hidden custom field (a missing one is
created hidden) with a value from `bw`'s generator. At write time it reads
the `generator` section of the note as it is then, generates with the item's
own setting or else the default, and sets the leaf, so `bw` keeps the old
value in the item's password history. The value is never logged, stored or
answered. Refused (422) for any other column ("a generated value goes only
into login.password or a hidden field"), for a field of another type and
for the tool note. MCP has no tool for it.

#### `createItem`

`createItem` (`name`, exactly one of `type` and `template`, `folderName`,
`organizationName`, `collectionNames`, `values`, `secretValues`,
`generatePassword`, `generateColumns`) adds a new item. Not the tool's main
purpose, but it makes it complete, and agents can propose new items too.

- The item is built from the type's empty sub object, the name, the
  organization and its collections (at least one), then a template's custom
  fields (created empty with their field types), then `values` (`{column:
  value}` as `setLeaf` takes them, an unnamed custom field becomes text),
  then `secretValues` (`{column: text}`, secret columns only, an unnamed
  custom field becomes hidden). The folder is found by name or created.
- `secretValues` is the one way a secret value comes in, and only a person
  sends it: from the queue it comes from a `ui` entry, encrypted while it
  waits, and MCP has no way to send it.
- With `generatePassword` (logins only) `login.password` is set at write
  time from `bw`'s generator (`GET /generate`, with the default of the
  `generator` section), never logged, stored or answered. `generateColumns`
  does the same for each column it lists, `login.password` of a login or a
  hidden field. A `login.password` in both generates once. Both parameters
  are accepted: a stored entry may hold `generatePassword`, and agents know
  it.
- The write creates the item (`POST /object/item`) and syncs before it is
  used. The change log row has empty `itemIds` and the new id in
  `createdItemIds`, so Restore of the row moves the new item to the trash.
  Committed from the queue, it puts the secret columns it left empty on the
  to-fill list.
- Refused (422) before anything is written, at `POST /api/queue`, in MCP and
  in the write path alike, each message naming the column and never the
  value: a secret column in `values` ("COLUMN is secret: a new item never
  takes a secret value, fill it in the grid after it is created"), a column
  in `secretValues` that is not secret, an empty one, `name`, a column that
  is also generated, a value in `secretValues` that equals the name, the
  folder or one of the `values` (a generated value that does is refused at
  write time), `generatePassword` on another type than a login, a column in
  `generateColumns` that takes no generated value or is also in `values`, an
  unknown type or template, both or neither of `type` and `template`, an
  empty name, `name` among the values, an unknown organization, an
  organization without collections or collections without one, a column
  that does not exist for the type, and a personal secure note named
  `bwtidy` or one whose `notes` hold a `note` section with its own name
  (either would be taken for the tool note).

## The tool note

One secure note named `bwtidy` (or the name the user kept) in the user's
personal vault (never in an organization: its other members would see it),
created on first login when missing. What a user sees of it is in
[The bwtidy note](explanation/tool-note.md).

- `notes` holds one JSON object, `{"version": 1, ...}`, with one key per
  section: `templates` (see Templates), `datatypes` (see Datatypes), `toFill`,
  `generator` and `note`. A key bwtidy does not know is kept. Each section
  is saved alone with `setToolNoteSection` (see Writes), so a save of one
  never loses another. Empty notes read as no sections. Notes that are not
  such an object (edited by hand) read as no templates, and the next section
  save starts them over from `{"version": 1}` (the backup of that write
  holds them).
- Found by id: bwtidy remembers the note's id in the `settings` table (key
  `tool_note_id`) whenever it finds or creates the note. The lookup tries the
  remembered id first (a personal secure note, whatever its name now), then
  the name `bwtidy`, then a personal secure note whose `note` section holds
  its own name (found after a reinstall or by another bwtidy). Every user of
  the note uses that one lookup. A login creates a note only when none of
  them finds one. Reason: a rename in another app would otherwise make the
  templates vanish, and the next login would create a second, empty note.
- The write path refuses every operation that touches it (422 "the bwtidy
  note is managed by bwtidy, edit the templates on the Types page"), at
  `POST /api/queue` too. The only exceptions are bwtidy's own writes: a
  section save (a `ui` queue entry of `setToolNoteSection` that carries the
  permission with it, so the commit passes it on for that entry alone),
  Rename it back and Keep this name, which say so explicitly in their call
  to the write path (never judged by the column). The backup password
  updates write the note directly through `bw serve`.
- MCP never lists, reads or touches it.
- The Vault grid can leave out its row (`hideToolNote`, see Settings). Only
  the Vault grid hides it, and the rows then shown are the rows of the grid,
  so its counts and select all do not count the note. The normalize dialogs
  always leave it out.

### The `generator` section

`{"default": OPTIONS, "items": {ITEM_ID: OPTIONS}}`, with the parser in
`sections/generator.py`.

- OPTIONS is `{"type": "password", "length", "uppercase", "lowercase",
  "number", "special", "minNumber", "minSpecial", "ambiguous"}` or `{"type":
  "passphrase", "words", "separator", "capitalize", "includeNumber"}`, the
  options of Bitwarden's own generator (`ambiguous` true avoids ambiguous
  characters, like `bw generate --ambiguous`).
- Limits: length 5 to 128, words 3 to 20, `minNumber` and `minSpecial` 0 to
  9, at least one character class, a separator of one character or none
  (stored as the character, `""` for none, sent to `bw` as `space` and
  `empty` where needed). The minimums have to fit the length, since `bw`
  would silently make the password longer than asked, and an item's own
  setting is often there for an app that cannot take long passwords.
- Without a section, without a default or with settings that are not valid
  the default is a password of 24 with all four classes.
- An item's own setting moves to the new id with a conversion, written to
  the section as part of that write, together with its entry of the to-fill
  list. Settings of items that are gone (the trash counts) are dropped the
  next time the section is saved.
- `GET /api/generator` answers the section with the default filled in,
  `saving` while a save is under way, and `error` when the stored settings
  are not valid. `PUT /api/generator/default` and `PUT
  /api/generator/items/{item_id}` take OPTIONS, `DELETE
  /api/generator/items/{item_id}` puts the item back on the default. Each
  queues its one change as a `ui` entry of `setToolNoteSection` (summary
  "Save the generator settings") and starts a commit at once. They answer
  202 with the section as the saves under way leave it and `entryId`, 422
  for options outside the limits and for the tool note, 404 for an item not
  in the vault.
- `POST /api/generator/example` takes OPTIONS and answers `{"value": ...}`
  from `bw serve` (see Secret values), 422 outside the limits, 502 when `bw`
  fails.
- `GET /api/grid` gives a row `ownGenerator: true` when the item has its own
  setting (left out otherwise), from the view the generator routes use, so a
  save shows at once.

### The `note` section

`{"name": NAME}`, the note's expected name, a string that is not empty, with
the parser in `sections/note.py`. Without the section, without `name`, or
with a section that does not parse, the expected name is `bwtidy`, so a note
written before the section existed keeps working with the same `version`.
Reason: bwtidy finds the note by its id, so its name is only a label for the
user, and the grid's warning about a rename should not come back for a name
the user chose.

`GET /api/templates` answers the note's name now (`toolNoteName`) and its
expected name (`toolNoteExpectedName`). When they differ, the grid warns
(see [Alerts](reference/ui.md#alerts)):

- Rename it back: `POST /api/tool-note/rename-back` with the note's
  `revisionDate`, a `setLeaf` of `name` through the write path (backup,
  `revisionDate` check, 409 on a conflict).
- Keep this name: `POST /api/tool-note/keep-name` with the name the warning
  showed, a `setToolNoteSection` of `note` through the write path. 422
  without a name, 409 when the note is not named that any more.

Both answer `toolNoteName`, `toolNoteExpectedName`, the new `revisionDate`
and the backup.

### The to-fill list

The section `toFill`: the secret fields the user still has to fill in, a
list of `{"itemId": ID, "columns": [COLUMN, ...], "since": "YYYY-MM-DD"}`,
secret columns only (grid column ids), at least one column, each once, at
most one entry per item, `since` the day the entry was added (`parse_to_fill`
in `sections/to_fill.py`). A section that does not parse reads as an empty
list. Reason: an agent's new item takes no secret value and Apply template
adds missing hidden fields empty, so secret fields are left for the user,
and the list reminds them.

- What adds to it: a `createItem` or `applyTemplate` committed from the
  queue (`ui` entries, and `mcp` entries the user approved), and a cell the
  user marks. A `createItem` from a template lists the template's required
  secret coordinates (an optional one may stay empty, a field name pattern
  names no column), from a raw type `login.password` (unless
  `generatePassword`), `card.number` and `card.code`, or
  `sshKey.privateKey`, never `login.totp` unless a template lists it. An
  `applyTemplate` lists the template's required secret coordinates, under
  the new item's id when it converted the item. A write through `POST
  /api/operations` adds nothing. Adding never fails the write it follows: a
  list that cannot be queued is only logged.
- What removes from it: after every load of the vault in RAM the list is
  pruned. A column that has a value now is dropped (filled in bwtidy or in
  any Bitwarden app), so is a column the item does not have as a secret any
  more, and an entry with no column left, an item that is gone or in the
  trash, and the tool note. Checking a value only asks whether it is empty,
  it never logs or copies it.
- What moves in it: a conversion moves the item's entry to the new id with
  its `since`, as part of that write.
- Every change is a queued `setToolNoteSection` of `toFill` (a `ui` entry
  with the summary `Update the to-fill list`): the difference between the
  list as the note holds it with every such entry still pending applied,
  and the list wanted. A list that is already right queues nothing, so its
  own commit queues nothing again.
- What a write left empty and what the user marked stay in RAM until the
  tool note itself holds them, and every load merges them in again, so a
  `toFill` save that failed or could not be queued is queued again at the
  next load. An Unmark takes its column out of them too. Reason: otherwise
  an Unmark between a write and its reload would come back, and a failed
  save would lose its columns for good. Limit: a backend stop before the
  save is in the note loses what was only in RAM.
- `GET /api/grid` answers `toFill`, `{ITEM_ID: [COLUMN, ...]}` as the vault
  is now (the pruned list, even before it is written), and a secret column
  for every listed cell, also when no item has a value in it yet. Each row
  has `emptySecrets` (the secret columns the item has without a value),
  `emptyTextFields`, and `booleanFields` and `linkedFields` (with or without
  a value), each left out when empty. Field names and types are no secret.
- `POST /api/to-fill` `{itemId, column, mark}` adds the cell or takes it off
  and answers `{"entryId": N or null, "toFill": {...}}`, `entryId` null when
  the list stays as it is. 422 for a column that is no secret column of the
  item, a cell that has a value when marking, the tool note and an unknown
  item.
- MCP never sees the list. `propose_create_item` only says that the secret
  fields the user fills in show up in the To fill in list.

### The backup passwords

The note lists every master password the backups may still be encrypted
with, newest first: the hidden custom field `backup-password` holds the
newest, and while a re-encryption is unfinished the hidden custom field
`older-backup-passwords` holds the older ones as a JSON list of strings. A
note with only `backup-password` reads as a list of one, a note with neither
as an empty list.

- Every write of the list makes both fields hidden, whatever type they had
  (a field turned into a text field in Bitwarden would show the master
  password in the grid and to MCP). An unlock that finds one of them not
  hidden writes the list again for that reason alone.
- The write reads the note fresh from `bw serve` first and, when it is no
  personal secure note any more, writes nothing and fails the login with 409
  `the bwtidy note is not a personal secure note any more, move it back to
  your personal vault`.
- On login, when the list is not just the master password, the backups are
  re-encrypted with it, without asking:
  1. When the master password is not first in the list, put it first and
     save the note, before anything is re-encrypted.
  2. The identity for hidden values (see MCP and the queue), then every
     backup that does not open with the master password: each listed older
     password is tried, newest first. One file at a time, each replaced
     atomically, so a run cut off at any point leaves every backup readable
     with a password still in the list and the next login finishes the job.
  3. Only as the very last step, once the identity and every backup open
     with the master password or a backup was found unreadable with all of
     them, the list becomes the master password alone.
- A backup that decrypts with none of the passwords is left untouched and
  named in the UI after the login. No password and not the length of the
  list is ever logged, shown or answered.
- Reason for the list and the order: with one old password kept, a run from
  A to B cut off halfway would leave the field at A, and a later change to C
  would try only A and C, so the backups already in B could not be read any
  more.

## Backups

Every write has a backup that matches the vault as it is right before the
write, taken ahead of time in the background when possible. Reason: a backup
takes several seconds (one `bw` process per export and per attachment, then
the encryption), which would make every edit slow if it were taken inside
the write. What a backup holds, for a user, is in
[Secret values and backups](explanation/secrets.md#backups).

- A backup carries a fingerprint: a sha256 hex over what it covers, computed
  from the listings it stores, so the order does not matter: every item's
  `id`, `revisionDate`, `folderId`, `favorite`, `organizationId`,
  `collectionIds` and attachment ids, and the folders, collections and
  organizations as whole JSON. An attachment's `url` carries a token that
  differs between two listings, the rest of an item is fixed by its
  `revisionDate`, and the folder, favorite flag and collections change
  without a new one (see Writes). It is stored in SQLite (table
  `backup_fingerprints`, `name`, `fingerprint`), removed with its backup.
  The `revisionDate`s it hashes are spelled as bwtidy first saw each
  revision, so a backup stays reusable when `bw serve` answers another
  spelling later. After a restart the first spelling may be the other one,
  and then one backup more is taken.
- After a write (once the vault is reloaded) and after an unlock (once the
  cache is loaded) a backup starts in the background, unless the newest
  backup's fingerprint already equals the vault's. One backup runs at a
  time, whether for a write, by hand or in the background. The background
  backup holds the session's password and key only while it runs. Its
  failure is only logged, the next write then takes its own backup.
- A write, after its sync, reload and checks, computes the vault's
  fingerprint from the cache, waits for a running background backup, and
  reuses the newest backup when its fingerprint equals the vault's. Else it
  takes a backup right there. A backup without a stored fingerprint is never
  reused.
- File: `BACKUPS/<UTC timestamp>.zip.age`, timestamp format
  `YYYYMMDDTHHMMSSZ`. A file whose name has that shape but no valid instant
  (a month 13) is no backup, like any other file.
- Encryption: age with a passphrase (scrypt), the master password. The `age`
  CLI only reads a passphrase from a terminal, so the backend uses `pyrage`
  (`pyrage.passphrase.encrypt`). The files must decrypt with plain `age -d`
  at a terminal.
- Zip contents:

      personal-export.zip       bw export --format zip (the format bw import takes)
      org-<orgId>-export.json   bw export --organizationid <orgId> --format json, per org
      item-attachments/<itemId>/<fileName>   every attachment of every item, via bw get attachment
      raw/items.json            GET /list/object/items, the exact objects the app PUTs
      raw/trash.json            bw list items --trash
      raw/folders.json, raw/collections.json, raw/organizations.json
      RESTORE.md                docs/restore.md, the steps of a disaster restore

- `item-attachments` is the source of attachments: every attachment of every
  item, personal and organization alike, active and in the trash, fetched
  one by one (one `bw` process each, one after the other since they share
  one CLI state). A second attachment with the same file name on one item is
  stored as `<attachmentId>-<fileName>`, with the id put in front again
  while that name is taken. Reason: `bw export --format zip` files
  attachments under `attachments/<item name>/<file name>` with special
  characters replaced, so items of the same name collide (`NAME_1`, `a/b`
  and `a_b`), one file overwrites the other, a failed download is skipped
  with only a log line, and nothing says which folder belongs to which
  item. `personal-export.zip` stays as it is for `bw import`, but its
  `attachments` folders are never trusted or read.
- The exports hold nothing from the trash. `raw/trash.json` and
  `item-attachments` do.
- `RESTORE.md` is `docs/restore.md` as the image has it (`/app/docs/restore.md`),
  with `COMMIT` in its line "Written for bwtidy COMMIT." replaced by the
  version as `/api/health` shows it. Reason: with the repository gone, the
  backup itself says how to restore it. When the file cannot be read the
  backup is written without it and the log says so: a backup never fails for
  its manual. The documentation site's copy fills in the same line with the
  version the site is built for.
- Completeness check before the zip is built: every attachment listed on an
  item in `raw/items.json` and `raw/trash.json` has its member, and the
  member's length fits the listed `size`. That size is the encrypted blob's
  (`49 + (length // 16 + 1) * 16`: one type byte, the IV, the HMAC and the
  PKCS7 padded AES-CBC ciphertext), not the plain byte count. A failure
  raises `BackupError("attachments", ...)` naming the item id and the
  attachment id only, and no `.zip.age` is written.
- The CLI runs with `BW_SESSION` in its environment (never on the command
  line), `--nointeraction`, stdin closed and a timeout. `--raw` writes the
  export to stdout, so no plain export ever touches the disk.
- The zip is built in RAM and the encrypted file written to a temporary name
  in the same directory, fsynced and renamed. A failed step leaves no
  `.zip.age` behind, and the error names the step.
- The listings after the sync are not trusted at once. Right after a `POST
  /sync` that fetched something, `bw serve` answers `GET /list/object/items`
  with an empty list for a moment while it decrypts the items again, so the
  cache load and the backup ask an empty list again for a few seconds before
  they believe it. The backup refuses to write a backup with no items: after
  the first login the tool note always exists. An item whose
  `organizationId` is not among the listed organizations makes the backup
  list them again with the same wait, and it is refused when the
  organization is still missing (it would get no export). A folder or
  collection an item refers to but the listing lacks is asked again the same
  way, then only logged as counts: the raw items still carry the ids.
- `GET /api/backups` lists the backups (name, size, time), `POST
  /api/backups` takes one by hand (the UI has no button for it). `GET
  /api/backups/{name}` answers the encrypted file as a download
  (`application/octet-stream`), only for a name the listing knows, so no
  path can be given (404 for anything else).
- `DELETE /api/backups/{name}` deletes one backup and its fingerprint, and
  drops its decrypted copy from RAM, under the backup lock. Refused with
  409: the newest backup (the next write may reuse it), the backup a running
  write selected, a backup a pending or approved `restoreItems` or
  `restoreFolder` reads, and a backup a failed queue entry's result names
  (dismiss the entry first). The backups are the user's to prune.
- Retention, applied after each new backup: delete a backup that is not
  among the newest `BWTIDY_BACKUP_KEEP_NEWEST` (40) or is older than
  `BWTIDY_BACKUP_KEEP_DAYS` (90) days, but always keep the newest
  `BWTIDY_BACKUP_KEEP_ALWAYS` (5), whole numbers of at least 1. Reason for
  the last: 90 days without changes must not delete every backup. A backup
  still in use (the reasons Delete refuses) is kept until it is not, and
  still counts among the 40. Reason: a queued restore would otherwise fail
  when the retention had deleted its backup. The fingerprints and decrypted
  copies go with the deleted backups. Copying the backups directory
  elsewhere is the deployment's concern.
- A decrypted backup is kept in RAM while the session lasts (the last one
  read), because the queue asks for a pending entry's diff on every listing
  and an age decrypt pays scrypt's cost each time.
- Two restore paths: the per item rollback is `restoreItems` below. The
  disaster restore imports the exports into an empty account by hand, with
  the steps of [restore.md](restore.md). It needs only age, a stock
  `python3` (3.8 or later, the standard library alone) and the official
  Bitwarden CLI, nothing of bwtidy. Reason: whoever restores a lost vault
  has a plain machine, and Python reads more easily than a jq program. The
  import gives every item a new id, so its program pairs each backup item
  with its new item by content and uploads the attachments from
  `item-attachments`, skipping one the new item already has, so a second run
  adds only what is missing. Each organization export goes into the
  organization of the same name, and with two organizations of one name the
  program stops before anything is uploaded. Reason: matching only by
  whether an item is in an organization would let twins in two organizations
  get each other's attachments.

### Restoring a change log row

The main concern is data loss: `restoreItems` makes the rollback a button,
and the tests prove that a backup brings the data back.

- `restoreItems {backup, itemIds, revisionDates, changeLogId?}`: `itemIds`
  are the row's `itemIds` plus its `createdItemIds`, `revisionDates` maps the
  ids that are active when the restore is planned to their `revisionDate`.
  `changeLogId` is the row the plan was made for, absent when a client
  builds the operation by hand. It goes through the write path like every
  operation, with a backup of the vault as it is now (so a restore can
  itself be restored) and one change log row. Each item is checked again
  right before its first write. The move's answer goes into the chain of own
  writes, and the PUT after an untrash is not checked again (the untrash
  answers no `revisionDate`). The queue takes it as a `ui` entry. MCP has no
  restore tool.
- The restore's own row records `restored_backup` (the operation's `backup`)
  and `restored_change_id` (its `changeLogId`), both null on every other
  row, answered as `restoredBackup` and `restoredChangeId`. The row's own
  `backup` stays the new backup taken before the restore.
- A change a later restore undid says so. A restore puts whole items back,
  so it undoes every change of those items made after its backup. Change X
  (a row with result `ok` that is not itself a restore) is marked by the
  first later restore R (higher id, result `ok`) that put back an item X
  touched from a backup taken at or before X: R's `restoredBackup` is X's
  own `backup` or an older one, by the UTC time in the names (so a deleted
  backup still counts). `GET /api/change-log` gives each row `restoredBy:
  {id, at, itemIds}` or null, looked up over the whole table. Only the
  first such restore counts, so nothing beyond the restore rows is stored.
  X's own backup and not X's time is the bound because names have whole
  seconds: the background backup after X can be named in X's second and
  already holds X.
- What it does per item id, against the decrypted backup (the quoted text is
  the plan's):
  - in `raw/items.json` and active now: PUT the backup's item with the
    current `revisionDate` ("put back as before"). The attachments stay as
    they are, no operation removes one.
  - in `raw/items.json` and in the trash now: `POST /restore/item/{id}`,
    then the PUT ("take out of the trash and put back as before").
  - in `raw/items.json` and gone now: create it anew from the backup without
    `id`, `revisionDate`, `attachments`, `passwordHistory` and
    `fido2Credentials`, then upload its attachments from the backup ("create
    anew (passkeys and password history are lost)"). The new id goes into
    the row's `createdItemIds`.
  - in `raw/items.json` with another `organizationId` than now: a PUT cannot
    undo the move. An item that was personal is created anew in its old
    place like `moveToPersonal` does, with the attachments copied from the
    current item, and the current one is trashed ("create anew in its old
    place and trash the current one"). An item that was in an organization
    and is personal now goes back with `POST /move`, then the PUT ("move
    back into the organization and put back as before").
  - in `raw/trash.json`: move it to the trash when it is active now ("move
    to the trash"), else leave it.
  - in neither file (an item the row's operation created): move it to the
    trash when it is active now, else leave it ("leave as it is").
- Whichever of these writes the item, its folder and collections go back by
  name when the vault does not have their ids any more. A folder the vault
  lacks goes to the first folder of that name, or is created, and without a
  name in the backup the item goes without a folder. A collection the
  organization lacks goes to the collection of that name there, else it is
  left out. An item whose collections are all gone keeps the ones it has
  now, and one that has none (created anew or moved back in) fails with that
  reason. Reason: the server refuses a `folderId` or a collection id it does
  not have, so a restore after a deleted folder or collection would fail,
  half done in the collection case.
- Whether an item that is not active is in the trash or gone comes from the
  trash listing the vault in RAM loads with the items, asked of `bw serve`
  again right before the item is brought back. A failed read alone never
  counts as gone (right after a sync `bw serve` answers "Not found." for
  every object for a moment), only the settled listings do. A read that
  fails while the listings hold the item fails the restore. Reason: a
  failed read of an item in the trash would otherwise create a copy without
  its passkeys and password history, and leave the real item in the trash.
- Attachments of an item created anew come from the backup's
  `item-attachments/<itemId>/`, by the id the item had in the backup, each
  uploaded under its original `fileName`, so two items of the same name
  each get their own files.
- Refused with 422 before anything is written, at `POST /api/queue` and in
  the write path alike: the tool note among the ids (it holds the backup
  password, templates are restored by saving them on the Types page), a
  backup that does not exist or does not decrypt with the session's
  password, an empty `itemIds`, an item that is active now but was not when
  the restore was planned (its `revisionDate` was never checked), and an
  item to create anew whose attachment the backup lacks. Reason: creating
  the item without an attachment, or with another item's, would be silent
  data loss.
- A `renameAttachment` row is restored by a `renameAttachment` of the copy
  it made (`attachmentIds.after`) back to the name its backup holds for the
  attachment it replaced, through the queue. Reason: a `restoreItems` would
  `PUT` the whole item back, which leaves its attachments as they are and
  undoes every later change of the item instead. The plan's `items` holds
  the item with action `renameAttachment` and the text `rename the
  attachment "NOW" back to "OLD"`. Refused (422) like the rename, and when
  the item or the copy is gone (`the attachment ID the rename made is
  gone`) or the backup lacks the old attachment. Its own row is a
  `renameAttachment` row, and `restoredBy` does not mark the row it undid.
- `restoreFolder {backup, folderId, changeLogId?}` puts a folder back as the
  backup's `raw/folders.json` holds it: a folder that exists gets that name
  again ("rename back to "NAME""), one that is gone is created again
  ("create again as "NAME"", a new id), and nothing happens to a folder
  that has the name already, or to a gone one when a folder of that name
  exists ("leave as it is"). Refused (422): a backup that holds no folder
  with the id, and a rename back to a name another folder has now. The
  rename back passes the secret rule like a `renameFolder`. Its plan is
  made again on `bw serve`'s folders right before the write, and its row
  records `restoredBackup` and `restoredChangeId`. Its summary is `Restore
  the folder "NAME" from BACKUP` (`Restore a deleted folder from BACKUP`).
- `GET /api/restore-plan?changeLogId=N` answers the plan before anything is
  queued: `backup`, `summary`, the `operation` to queue, and `items`
  (`itemId`, `itemName`, `action`, `text`). For a row with a `folderId` the
  operation is a `restoreFolder`, `items` is empty and `folder` (`folderId`,
  `name`, `action` one of `renameBack`, `createAgain`, `leave`, `text`) says
  what it does. 404 for an unknown row, 422 with the reason when the restore
  would be refused.
- The queue entry's summary is `Restore N items from BACKUP`. Its diff has
  one row per item in the pseudo column `restore` (`after` is the text),
  plus the cell rows of the items it puts back and a `trash` row for the
  items it trashes. The grid shows the cell rows as queued values and skips
  the `restore` rows.

## Grid

What the Vault page shows and what each control does is in
[The web UI](reference/ui.md#vault): the [toolbar](reference/ui.md#toolbar)
with its filters and chips, the [cells](reference/ui.md#cells) and what
their look tells, the [cell menu](reference/ui.md#cell-menu), the
[header menu](reference/ui.md#header-menu) with Manage folders, the
[selection bar](reference/ui.md#selection-bar) and
[New item](reference/ui.md#new-item). The Columns button's column chooser is
in [Show or hide columns](how-to/show-hide-columns.md), Rename attachments
in [Rename an attachment](how-to/rename-attachment.md) and the Change log
page in [Change log](reference/ui.md#change-log). The rules behind them:

- AG Grid Community: column groups, a pinned `name` column and hiding
  columns are all in its free tier.
- A column exists for every coordinate some item has a value in. `favorite`
  and `reprompt` are always there, so any item can be made a favorite or get
  reprompt from the grid. A false value counts as empty for Hide empty
  columns and for a header's count, a masked secret as present. The count in
  a header's tooltip is over every item of the vault, not the shown rows.
- The search keeps the rows where every word is in some text cell, never a
  secret cell. Search, filters and every write work on the vault values,
  never the queued ones.
- The user edits in the grid directly, and every edit is one `POST
  /api/queue` (see UI edits and the auto-commit). Reason: an edit that waits
  for sync, backup, write and reload feels slow. A dialog closes as soon as
  it is sent and nothing waits for the answer.
- On a conflict (409) nothing is queued. The reloaded grid marks the rows of
  the items that changed elsewhere and the cells whose shown value differs
  from before the reload. Masked cells are never marked (their change cannot
  be seen), nor `revisionDate` (it always differs). The marks stay until the
  next accepted edit. The comparison is a pure module
  (`frontend/src/shared/conflicts.ts`) so other writers can reuse it.
- `type` changes only through the convert mapping (see Templates).
- The organizations and collections the dialogs offer are all of the
  vault's, also those no item is in yet (else the first item could never go
  there). `GET /api/grid` carries them as `organizations`, one `{name,
  collections}` per organization, and the folders as `folders`, one `{id,
  name, items, trashedItems}` per folder sorted by name. An organization
  with no collections cannot take an item (a write needs at least one
  collection), so the dialogs offer it disabled.
- A text, boolean or linked field in a secret column keeps its type (see
  Writes, `setLeaf` and field types): its dialog sends no `fieldType` and
  says that the value is stored as plain text that an agent can read. A
  boolean field gets a checkbox, a linked field no dialog.
- Generate new value and Generator settings for this item are offered on
  `login.password` of a login and on a hidden custom field only, never on a
  text, boolean or linked field of the same column or on the tool note.
- A cell that cannot hold a value (`unavailableReason` in
  `frontend/src/grid/editing.ts`) takes no edit and its tooltip says why.
  The read-only columns, the tool note and a cell with an agent's proposal
  are not such cells and keep their own message. This is judged on the vault
  values.
- The tool note's row is read-only with every cell menu action disabled and
  no checkbox (see The tool note).
- New item queues a `createItem`. A filled secret input goes in
  `secretValues` and only there, and the values stay in the dialog's state:
  no query key, log or storage holds them. That a template's field must be
  filled and on format is the dialog's block alone: `POST /api/queue`, MCP
  and grid edits are not blocked, and MCP may leave a template's fields
  blank. A refused format check (a datatype that went away or broke) blocks
  nothing.
- Selection: Move to trash queues one `trashItem` entry per item in one
  `POST /api/queue`, so the auto-commit writes them as one batch with one
  backup. The ticks clear whenever a search or filter changes which rows are
  shown, so a row not shown any more is never part of an action. Moving to
  the trash is the only action on a selection.
- Rare fields: the column `rareFields` is no field of the vault, bwtidy
  computes it. Custom fields and the built-in slots of `login`, `card`,
  `identity` and `sshKey` can go in, never the other columns. An item uses a
  custom field when it has the field, also with an empty value, and a
  built-in slot only when the slot holds a value, as every item of a type
  has all of its type's slots. A column goes in when between 1 and N items
  use it, counted over every item but the tool note, not the shown rows, so
  the set does not change with a filter. The cell shows the vault values,
  not the queued ones, a secret behind the same reveal as a masked cell. N
  is `rareFieldsMax` (see Settings).
- The name cell's suffixes for columns out of view are entries of
  `NAME_SUFFIXES` in `frontend/src/grid/nameSuffix.tsx`: the column id each
  stands for and a function that renders it for a row.

### Queued changes in the grid

- Queued changes show in the grid as if they were done, from the pending
  queue the app polls anyway. When several pending entries change one cell,
  the newest (highest id) is shown. A secret queued value stays masked and
  has no reveal, it is not in the vault yet. A column that only exists in a
  queued change gets a column in its group, secret when the queued value is
  masked. An entry with a `diffError` changes nothing in the grid.
- Cells with an agent's proposal are read-only until it is approved or
  rejected. Cells with the user's own unsaved edits stay editable, and the
  editor starts from the queued value. Several unsaved edits of one cell get
  no conflict note: a commit checks a later one against bwtidy's own write
  before it.

### The Change log page

- The page lists `GET /api/change-log` and `GET /api/backups` as one list. A
  backup is taken before the write that names it, so it sorts right under
  that write's row. With any filter only the backups that a shown change row
  names are listed.
- A row with result `ok` whose backup still exists and that names an item or
  a folder has Restore, except the rows of the tool note. Restore asks `GET
  /api/restore-plan` and queues the plan's operation like every UI edit.
- The Items filter is sent as the ids of the items whose names match. Above
  100 items it does not filter and asks for more of the name, as the ids
  would not fit in a URL.
- The Diff report lists what the shown change rows did, never a value.
- The Source, Result and Operation columns show labels, display only: the
  API and SQLite keep the raw values. The kind stays visible beside its
  label, because the kinds are the vocabulary of the API and the MCP tools,
  so the user and the agent see the same words. The labels are in
  `frontend/src/shared/operationLabels.ts` and
  `frontend/src/changelog/rows.ts`, the rows the filters keep in
  `useFilteredChangeLog` (`frontend/src/changelog/filters.ts`).

### Password managers

Password managers (Bitwarden, 1Password, LastPass, Dashlane, the browser's
own) are invited only on the account form, the unlock and the Bitwarden
login. Inside the app every input edits vault data, so every other input
(the pages, the dialogs, the selects and autocompletes, the grid's own cell
editors) carries `autocomplete="off"` and the opt-out attributes the
managers honor: `data-bwignore`, `data-1p-ignore`, `data-lpignore` and
`data-form-type="other"`. A cell editor that AG Grid builds itself gets them
in the same moment its input enters the page, before any other script runs.
A secret input (the secret dialog, a hidden value in Add field, the secret
inputs of New item) is a text input masked with CSS (`-webkit-text-security:
disc`, which Chromium and Firefox both honor) until "Show what I type", with
no spell check, autocapitalize or autocorrect: no manager sees a password
field to fill or to offer saving.

## Light and dark mode

The choice (see [Color mode](reference/ui.md#color-mode)) is one setting per
browser that every account shares. Light and Dark are stored in the cookie
`bwtidy_color_mode` (`light` or `dark`, any other value counts as no
cookie). System is no cookie: choosing it deletes the cookie and the
browser's `prefers-color-scheme` decides, also when it changes while the
page is open. The page sets the cookie itself (`Path=/`, one year,
`SameSite=Lax`, `Secure` on https), so a small script in `index.html` can
read it before the first paint. The gateway passes it on to the worker with
the other cookies and nothing reads it there. The login pages follow the
same setting, and so does the grid: AG Grid gets its color scheme, and the
colors made for a white background have a dark variant.

## Paragraph width

Paragraphs of running text, on every page and in every dialog, are at most 75
characters wide (`75ch`), with balanced lines (ragged right, the breaks
chosen for the whole paragraph). Tables, the grid and forms keep their
width. Words are never hyphenated and the text is never justified: balanced
ragged-right lines read better than `text-wrap: pretty`, hyphenation or
justified text. The breaks do not change the text: copying a paragraph gives
the same words with the same spaces. A dialog's description text gets the
width but not the balancing. An alert that holds running text follows the
same rule and shrinks to its text, while an alert with a short message keeps
its width.

## Settings

The Settings page is in [Settings](reference/ui.md#settings). What it
stores:

- The date and time format, in the `settings` table (key `date_format`), so
  it belongs to the account and follows it to every browser. One of
  `system`, `iso`, `europe`, `europe-dots` and `usa`. Every date and time
  the UI shows follows it, in the browser's time zone. Date fields are our
  own, because the browser's date field shows its own order whatever the
  choice. Until the choice has loaded, and when it cannot be loaded, the UI
  shows System.
- Hide bwtidy's own note, in the `settings` table (key `hide_tool_note`,
  stored as `1` or `0`), per account, off by default (see The tool note).
- The N of the Rare fields column, in the `settings` table (key
  `rare_fields_max`, the number as text), per account. It is set in the
  column's header menu and is 0 (off) until it has loaded.
- The layout, one per browser that every account shares, in localStorage
  under `bwtidy.layout` (any other or missing value is normal, every read
  and write wrapped so no storage reads as normal and a failed write still
  changes the page). Compact shrinks the theme's spacing to 5 px instead of
  8 and makes the app bar's toolbar dense. Nothing else changes, and the
  grid keeps its row height. It is read before the first render.
- `GET /api/settings/display` answers `{"dateFormat": ..., "hideToolNote":
  ..., "rareFieldsMax": ...}`. A stored format it does not know reads as
  `system`, a missing or unknown `hide_tool_note` as `false`, and a missing
  or unknown `rare_fields_max` (no whole number from 0 to 99) as 0.
- `PUT /api/settings/display` takes any of the three, saves only what it got
  (so changing one never resets the others) and answers like the `GET`. 422
  for an unknown format, a `hideToolNote` that is not a boolean, a
  `rareFieldsMax` that is not a whole number from 0 to 99 (a JSON integer,
  not a string or a boolean), a field set to null and a body with none of
  the fields.
- The password generator's default lives in the tool note (see The tool
  note, The `generator` section, also for its routes).

### Deleting data

What the two buttons delete and keep is in
[Settings](reference/ui.md#settings). Both act on the logged in account
alone.

- "Delete my data" removes every backup (the newest one too), every change
  log row, the backup fingerprints, and every queue entry that is not
  pending any more. "Delete my account" does all of that, then logs out of
  Bitwarden, and the gateway deletes the account directory and its directory
  under the backups root (see Gateway).
- With pending entries the caller chooses. Write first commits the pending
  UI entries like Commit now, then deletes, and a failed commit deletes
  nothing. Pending MCP proposals cannot be written without approval: with
  Write first they stay pending (for Delete my data), and a backup a pending
  restore proposal still needs stays. Drop deletes the pending entries too.
  Delete my account always drops the pending MCP proposals, since the
  account goes.
- The deletion refuses with 409 `a write is running, try again` while a
  commit or a direct write runs. Else it takes the queue's lock and then the
  write path's (the order a commit takes them in) and holds both while it
  deletes, with the backup keeper stopped, so no write starts meanwhile and
  nothing is half deleted.
- `POST /api/account/delete-data` needs the session cookie (401 without it)
  and the header `X-Bwtidy-Request: 1` (403 without it), like `POST
  /api/bw-logout`. It takes `{"pending": "write" | "drop" | null}`. It
  answers 409 `{"pending": N, "mcpPending": M}` when entries are pending and
  `pending` is null, 409 with the failure when Write first could not write,
  409 `{"detail": ...}` while a commit is running, else 200 `{"backups":
  ..., "changeLog": ..., "queue": ..., "keptBackups": [...]}` with the counts
  deleted and the backups kept for a pending restore proposal.
- `POST /api/account/delete` takes the same body and answers the same 409s.
  On success it deletes everything (the pending MCP proposals too with Write
  first), writes the marker `app/delete-requested`, logs the CLI out and
  ends the session the way `POST /api/bw-logout` does, so the worker exits
  and the gateway deletes the account.

## About

The About page and the More actions menu are in
[About](reference/ui.md#about) and
[More actions](reference/ui.md#more-actions).

- bwtidy follows Semantic Versioning 2.0.0: MAJOR.MINOR.PATCH, pre-releases
  as `-alpha.N`, `-beta.N` and `-rc.N`. The version is `1.0.0-beta.1`, and
  everything a person sees uses that spelling. The stages and what each
  promises are in [Road to 1.0](releases.md#road-to-10).
- `backend/pyproject.toml` holds the version in Python's spelling
  (`1.0.0b1`), which Python packaging requires, and it is the only place the
  version is written by hand. The backend reads it from the installed
  package metadata and spells it the semver way in `bwtidy/version.py`
  (`semver`), the one conversion. `frontend/package.json` repeats it in
  semver's spelling (nothing reads it), and the lock files carry generated
  copies. `scripts/ci` fails when one of them differs from pyproject's, and
  when the release notes (`docs/releases.md`) have no heading for the
  version. Reason: every release tells its users what it brings.
- The image knows the commit it was built from as the committer date
  (YYMMDD), a space and the short git hash, like `261028 f2f4299`: build arg
  `BWTIDY_COMMIT` (empty without it, since the build context has no `.git`),
  with `-dirty` from `scripts/dev-vault up` for uncommitted changes. `GET
  /api/health` answers `{"status": "ok", "version": "<version> (<commit>)"}`,
  or the version alone without a commit, so the user knows which build they
  test.
- The page links the source code because of section 13 of the AGPL: whoever
  uses the running service can get its source. Every link to the source or
  the license points into the repo.
- Works for me is a link only, no button: bwtidy sends nothing anywhere (no
  telemetry), the user decides what to post. Its URL is `WORKS_FOR_ME_URL`
  in `frontend/src/about/links.ts`.
- Thanks: every direct dependency (`dependencies` of `frontend/package.json`
  and of `backend/pyproject.toml`) has an entry in
  `frontend/src/about/credits.ts`, with the license its package names,
  beside the tools bwtidy is built and tested with.
- The table of every package comes from
  `frontend/src/about/thirdParty.json`, which `scripts/third-party` writes
  from the lock files and which is committed: npm, every package of
  `frontend/package-lock.json` that is not a dev package (the project itself
  left out), and PyPI, every package `uv export --no-dev` names (one for
  another platform, like `pywin32`, left out), its license from the
  installed metadata (`License-Expression`, else a License classifier that
  names one license) or from the script's overrides for a package whose
  metadata names none. A license it cannot tell is an error naming the
  package. Each entry is `{name, version, ecosystem, license, direct}`,
  sorted by name. `scripts/ci` fails when the file differs from what the
  script would write. The Bitwarden CLI and the packages it brings along are
  named beside the table, licensed by Bitwarden, Inc. under its own terms.

## Normalizing

How a user renames and merges is in
[Header menu](reference/ui.md#header-menu) and
[Cell menu](reference/ui.md#cell-menu).

- Rename a custom field across every item that has it (`renameField` with
  `from`, `to` and the items). Every field of that name in an item is renamed,
  type and position kept. An item that already has a field named `to` is a
  merge conflict (409 with `mergeConflicts`), because that is a merge.
- Merge columns (`mergeColumns` with `sources`, `target`, the items and
  `resolutions`, item id to the column to keep). Items with values in more
  than one of the merged columns are listed for a manual choice instead of
  being overwritten: without a resolution the answer is 409 with
  `mergeConflicts` (item id and the columns holding values) and nothing is
  written. The UI sends a merge without choices first, asks per listed item
  and sends it again with the choices. A resolution is read only where it is
  needed. Values not kept are removed. A field moving into a field the item
  lacks is renamed in place, keeping its type and position.
- Merge into a built-in slot (for example `fields.username` into
  `login.username`) is allowed when the item's type has that slot. For other
  types it is a type conversion (see Writes) and goes through the template
  mapping UI: the merge is a 422 that says so.
- Near-duplicate hints: column names equal after case folding and trimming
  whitespace are grouped. `GET /api/near-duplicates` lists the groups (`key`,
  `columns`) of field columns.
- Merge item into item: move chosen leaves of item A into item B, then trash
  A (`mergeItems`, mapping `{target column: source column}`). The tool note
  is never A. Values not moved stay in A, so they are in the trash and the
  backup.
- `mergeItems` takes `append`, the target list columns (`login.uris`) that keep
  B's entries and get A's entries added instead of being replaced: B's URIs
  first, then A's URIs that B lacks, each with its own match setting. A column
  in `append` that is not a list column or not in the mapping is a 422. The
  UI appends by default, the MCP tool `propose_merge_items` always replaces.
- The secret rule applies: no merge may move a secret into a non-secret
  coordinate. A new field made by a move gets the type of its source, so a
  hidden value stays hidden.
- `bw` itself adds `<name>: <value>` to the password history for every hidden
  field an edit renames, changes or removes, and keeps at most 5 entries. So a
  rename or merge of hidden fields can push the oldest entries out of an
  item's history (the backup still has them).

## Templates

The Types page and the template editor are in
[Templates](reference/ui.md#templates), with a walk through in
[the template tutorial](tutorial/template.md).

- A template: name, an optional description, item type, and a list of
  coordinates. A custom field coordinate also carries its field type (0 text,
  1 hidden, 2 boolean). A coordinate can be a field name pattern (`"kind":
  "fieldPattern"`, below). A text coordinate can carry a `"datatype": ID`
  (see Datatypes). A template without datatypes is made, stored and scored
  as if datatypes did not exist. Any coordinate, `name` too, can carry
  `"optional": true`, left out when false like `datatype` is left out when
  none, so a coordinate without the key is required.
- Stored in the tool note as its `templates` section (see The tool note):

      {"version": 1, "templates": [
        {"name": "Computer", "type": 2, "coordinates": [
          {"kind": "field", "name": "user", "fieldType": 0},
          {"kind": "field", "name": "user password", "fieldType": 1}
        ]}
      ]}

- Allowed coordinates: `name`, `notes`, the built-in leaves of the template's
  type, custom fields and field name patterns. Names are unique (ignoring case
  and surrounding spaces), a coordinate appears once per template, a pattern
  once by its text ignoring case.
- The description is free text of at most 500 characters (422 "A description
  holds at most 500 characters" otherwise), stored as `"description": "..."`
  and left out when empty. `GET /api/templates` and MCP's `list_templates`
  answer it when set. Matching, the fill score, the order of several matches
  and the duplicate warning ignore it, like the description of a datatype.
- No marker on items, membership is duck typed: an item matches a template
  when it has the template's type and a non-empty value for every required
  coordinate (for a required pattern, in at least one field that fits it).
- Fill score: per item and template, to find near misses. The item type counts
  as one more thing to match: `total` is the template's required coordinates
  plus 1, `filled` is the required coordinates the item has filled plus 1 when
  its type matches (`typeMatches`), the score is filled/total. A full match has
  filled equal to total, so it still needs the type. `missing` lists the
  template's required coordinates the item has no value for, as grid column
  ids, empty for a full match. Column names are not secret, so MCP carries
  them too.
- Format terms in the fill score: every required coordinate with a datatype
  adds 1 to `total` and its format score to `filled`: the value's score, the
  lowest over the URIs for `login.uris`, 0 for a missing value, for one longer
  than the value limit and for a boolean field in the item. An optional
  coordinate with a datatype adds its term only when the item has a value for
  it. Each term is floored to two decimals, so a partial value never counts
  as a whole point. A full match still needs `filled` equal to `total`, so a
  value off its format makes the item a near miss, with the value not in
  `missing`. An unknown or broken datatype counts as no datatype.
- Among several full matches the one with most required coordinates wins,
  then the one with most format terms, then the one with most optional
  coordinates the item has filled, then the first listed.
- A near miss has at least 2 matched and is not a full match. The grid's
  `template` column shows the match, else the best near miss: the highest
  score wins, a matching type first on a tie.
- Duplicate templates: two templates with the same item type and the same set
  of (coordinate column, datatype, optional) score alike for every item, so the
  one listed first takes every full match and the other never gets an item. The
  order of the coordinates and the field types do not change a score and are
  ignored. A pattern compares by its text ignoring case, so two templates
  alike but for the pattern are no duplicates, nor is a pattern the same as a
  field coordinate of that very name. A duplicate is a warning, not a
  refusal. Each template that duplicates one listed before it gets one
  warning, `LATER: same type and fields as EARLIER, which is listed first and
  takes every match.`, naming the first template of its kind. The `warnings`
  of `GET /api/templates` carry them, and the editor shows the same warning
  for its draft before saving.
- Optional coordinates: for items that have a field only sometimes. An
  optional coordinate is not needed for a full match, adds nothing to `total`
  or `filled` for its presence and is never in `missing`. With a datatype and
  a value in the item it adds its format term like a required one. A template
  needs at least one required coordinate (422 "a template needs at least one
  required field"), so an item whose type alone matches never matches a
  template of only optional coordinates. A template with no coordinates at
  all is matched by its type alone. Fields outside the template stay ignored.
- MCP and format terms: for MCP a secret column's format term is its presence
  term (1 when set, 0 when not) and it has no `formats` entry, so MCP learns
  nothing about a secret beyond its presence and a pattern can never be an
  oracle on one. What counts as secret goes by the field's type in the item,
  or by the template's `fieldType` when the item has no such field (for a
  pattern, by each field's type in the item). So MCP can call an item a full
  match that the UI calls a near miss.
- Template design hint shown in the UI: the most copied values belong in the
  built-in login slots (`login.username`, `login.password`, `login.totp`),
  which the browser extension copies straight from the item list. Custom
  fields need the item opened, and autofill matches them by name against form
  input name or id.

### Field name patterns

A template coordinate can match custom fields by a name pattern, so a
template can say that fields named like `user_*` exist and fit a datatype.
Stored as its own kind, `{"kind": "fieldPattern", "pattern": "user_*",
"fieldType": 0}`, with `datatype` and `optional` as on any coordinate. A `*`
in the name of an ordinary field coordinate stays a literal character.

- Syntax: a glob where `*` matches any run of characters (also none) and
  everything else is literal, matched against the whole field name,
  ignoring case and surrounding spaces. A pattern needs at least one `*` and
  some other character (422 "a pattern needs a * and some other character").
  Its field type is text or hidden, never boolean, since a boolean has no
  format to check. Two patterns of one template whose text is the same
  ignoring case and surrounding spaces are refused like a coordinate listed
  twice. A pattern and a field coordinate of the very name `user_*` are two
  coordinates.
- Which fields: the item's custom fields with a value, less those an exact
  field coordinate of the template names, which belong to that coordinate. A
  field that two patterns fit counts for the first one listed only.
- Presence: a required pattern needs at least one such field, an optional
  one is satisfied by none. It counts as one coordinate in `total` and
  `filled` however many fields fit it, and `missing` names it as
  `fields.user_*`.
- Format: every field it has is scored against the datatype and the
  pattern's format term is the lowest of them. Its `formats` entry is the
  field that scored lowest, under that field's own column (`fields.user_b`).
  A required pattern without a field adds a term of 0 under `fields.user_*`,
  an optional one without a field adds no term. The kept format results and
  the Statistics rows go by the real field's column.
- Secret: what counts as secret goes by each real field's type in the item.
  The pattern's `fieldType` is used only when New item creates a field for
  it, under the name the user types (one field per pattern).
- Limit: fields outside the template stay ignored, so a pattern says that
  fields named like it exist and fit, not that every custom field of the
  item follows it.

### Routes and `applyTemplate`

- `GET /api/templates` answers `{"templates": [...], "revisionDate": ...,
  "toolNoteId": ..., "toolNoteName": ..., "saving": bool}` with the tool
  note's `revisionDate`, id and current name. `GET /api/grid` carries
  `toolNoteId` too, so the grid knows the note's row by id, never by name.
  Notes that hold no valid templates read as an empty list plus `error`, and
  match nothing. `warnings`, only when there are any, names each coordinate
  whose datatype is unknown or broken (a pattern as `the pattern
  fields.user_*`) and each duplicate. While a save is pending or committing
  it answers the templates as those saves leave the stored ones, with
  `"saving": true`.
- `PUT /api/templates` with `{"changes": [...]}` changes single templates by
  name (see `setToolNoteSection` in Writes). It validates the changes on the
  templates as the saves under way leave them (422 for an invalid template,
  a name taken or one no template has, nothing queued then), queues a
  `setToolNoteSection` of `templates` with the summary `Save the templates`
  as a `ui` entry that is allowed to touch the note, starts a commit at once
  in the background and answers 202 `{"entryId": N, "templates": [...]}`. A
  whole list (`{"templates": [...]}`) is a 422 that says the templates are
  saved as changes. A `revisionDate` in the body is accepted and ignored.
- The Types page sends an `addTemplate` for a new template, a `setTemplate`
  after a `renameTemplate` when the name changed for an edit, and a
  `removeTemplate` for a delete. `moveTemplate` exists in the API only.
- `GET /api/template-matches` answers per item id (the tool note left out)
  `{"match": name or null, "scores": [{"template", "score", "typeMatches",
  "filled", "total", "missing"}]}`, one score per template in list order. A
  score with format terms also has `formats`: one `{"column", "datatype" (its
  name), "score", "distance", "firstEdit", "expected", "tooLong"}` per
  coordinate with a datatype. The UI gets secret columns too, with `firstEdit`
  and `expected`, never a character of the value.
- `GET /api/grid` carries the vault's custom fields as `fields` (`name`,
  `fieldType`), for the editor's pick: every field name some item has, also
  with an empty value, the tool note and linked fields left out. Where items
  disagree, hidden wins (the secret rule), else the most common type.
- The operation `applyTemplate` (`itemId`, `revisionDate`, `template` by name,
  `mapping` `{template column: source column}`): every target must be a
  coordinate of the template. Same type: the mapped values move (a custom
  field source is removed, a built-in source cleared, `name` stays), unmapped
  leaves stay. Another type: a `convertItem` with the same mapping. Either way
  every template field the item lacks is created empty with the template's
  field type, an optional one too so the item shows the slot, and a new
  target field gets the template's field type. A field name pattern creates
  no field (there is no name to give it) and is no target of the mapping. The
  secret rule applies, a text value into a hidden template field is allowed.
  Committed from the queue, it puts the template's required secret columns
  the item has empty afterwards on the to-fill list. A required hidden
  pattern adds nothing there.
- Convert to template maps the item's leaves onto the template's coordinates
  and leaves unmapped leaves as they are. A secret into a non-secret
  coordinate and one value in two coordinates are refused in the dialog, and
  a coordinate mapped onto itself is left out of the mapping.
- The type guidance under the editor's type select is in
  `frontend/src/templates/typeGuidance.ts`, so the user picks types the same
  way every time.

## Datatypes

A datatype is a named pattern the user creates (no shipped presets), defined
once and used by template coordinates. The Datatypes tab and its editor are
in [Datatypes](reference/ui.md#datatypes).

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
  part of a value. Without the key the pattern matches the whole value.
  Another value than true or false is refused, and `wholeValue: true` is
  never written.
- A template coordinate names a datatype by `"datatype": ID`. An unknown id
  counts as no datatype (`GET /api/templates` names it in `warnings`), so a
  template survives a datatype that went away by another way than the API.
- Matching runs on our own automaton engine (`patterns.py`), never on a
  backtracking regex engine. The score is an edit distance: d is the least number
  of inserted, deleted or substituted characters that turn the value into a full
  match, and score = 1 - d / max(len(value), shortest match length, 1). A full
  match is exactly 1. A score in an answer is floored to two decimals.
- With `wholeValue` false the characters before and after the matched part
  are free: d is the least number of edits that turn some part of the value
  into a full match, and len(value) in the score is the length of that part
  (`\d{4}` matches `id 1234` fully). A `^` first in the pattern takes away
  the free characters before, an unescaped `$` last those after. With
  `wholeValue` true both anchors are allowed and change nothing, and
  anywhere else they are a pattern error. The free characters cost no
  automaton states, so the limits and the bound on the work stay as they
  are.
- The editor's typing rules for the anchors: a `^` first and an unescaped
  `$` last tick Whole value and are taken out of the typed text. While the
  box is ticked either one alone goes too, as redundant. Unticked, one alone
  stays and anchors only that side.
- `firstEdit` is a position in the value and `expected` holds text from the
  pattern only, so neither shows a character of the value.
- Results are computed after every load of the vault in RAM, which is also
  how a change of the datatypes or the templates arrives. They are kept in
  memory only (`Formats.results`) by item id, column and the datatype's
  pattern and flags, never keyed by a hash of a value, never logged, dropped
  by the next load and by a logout.
- MCP lists only the datatypes that a non-secret column uses and sees format
  results for non-secret columns only (see MCP and the queue). No MCP tool
  proposes a datatype or a corrected value. The editor warns that an agent
  can read the pattern and the examples, so they must be made-up values.
- In the grid, a cell's datatype is the datatype of its column in the
  template the row's `template` column shows (its match or its best near
  miss). A cell with a queued value has no format mark, as the result is
  about the vault's value. A secret column shows the numbers and positions
  only. A value off its format saves like any other: the text under an
  editor is a warning and never blocks (only New item blocks, see Grid). The
  value being typed is in no query cache.

The routes:

- `GET /api/datatypes` answers `{"datatypes": [...], "saving": bool}`, each
  with the stored keys, `wholeValue` always (true or false), the pattern
  facts `states`, `maxRepeat` and `shortest` (null when broken) and `broken`
  (the reason or null), and `error` when the section cannot be read. While a
  save is under way it lists what that save holds.
- `PUT /api/datatypes` with `{"changes": [...]}` changes single datatypes by
  id (see `setToolNoteSection` in Writes): it validates the changes on the
  datatypes as the saves under way leave them (422, nothing queued), queues
  a `setToolNoteSection` of `datatypes` with the summary `Save the
  datatypes`, starts a commit at once and answers 202 `{"entryId": N,
  "datatypes": [...]}`. A new datatype comes without an `id` and gets one
  here (an id in an `addDatatype` is a 422). Unknown keys (the facts of a
  GET) are left out. Deleting one a template uses, stored or in a save under
  way, is a 422 naming the templates, and the write checks it again on the
  note as it is then. A whole list (`{"datatypes": [...]}`) is a 422 that
  says the datatypes are saved as changes.
- `POST /api/datatypes/check` with `{"pattern", "ignoreCase", "wholeValue",
  "examples", "counterExamples", "samples"}` (`wholeValue` true when left
  out, each list at most 50 values, a 422 otherwise), for the editor's live
  preview, answers the facts and a result for every example, counter-example
  and sample, or `{"error": {"message", "column"}}` for a pattern the engine
  does not take. A result is `{"distance", "score", "firstEdit", "expected",
  "repeatsUsed", "steps", "tooLong"}`. Too long gives score 0, `tooLong` true
  and null for the rest.
- `POST /api/datatypes/{id}/check` with `{"value"}` answers the result of one
  value against a stored datatype, for the grid's live editing (404 for an
  unknown id, 422 for a broken one).
- What these two take can be a secret being typed. It is never stored or
  logged, and a refused body is never echoed, so they read the body
  themselves rather than through FastAPI's validation. The frontend keeps
  what is checked in the dialog's state, out of the query cache.

## Statistics

The Statistics page ([Statistics](reference/ui.md#statistics)) opens from
the Datatypes tab, not from More actions: it is a tool for tuning patterns,
which a normal user only needs once a complicated pattern hits a limit of
the engine (`patterns.py`).

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
  `rowCount` as the number kept.

## MCP and the queue

What an agent sees and proposes, for a user, is in
[What an agent can do](explanation/mcp.md). Each tool's parameters and the
description the agent reads are in [MCP tools](reference/mcp-tools.md),
which `scripts/mcp-reference` writes from the tools the server registers.
The Queue page is in [Queue](reference/ui.md#queue), the switch and the
token in [Connect an MCP client](how-to/mcp.md).

### The server, the switch and the token

- The MCP server runs inside the backend process (it holds the unlocked
  session), streamable HTTP at `/mcp` on the web port, built with the
  official `mcp` Python SDK. It is stateless, so a backend restart leaves no
  MCP session for the agent to drop.
- Only requests addressed to 127.0.0.1, localhost or ::1 are served (the
  SDK's DNS rebinding protection), so a web page cannot reach it through a
  name of its own. `BWTIDY_MCP_HOSTS` adds each NAME to the allowed hosts
  (`NAME` and `NAME:*`) and `https://NAME` to the allowed origins.
- The switch: `POST /api/mcp` with `{"enabled": bool}` (session required)
  answers `{"mcpEnabled": bool}`, and `GET /api/session` reports
  `mcpEnabled`. It is off after every login. Turning it on needs a token
  that exists and has not expired: else 409 with `{"detail": "Create an MCP
  token first: MCP token menu, Create."}` or `{"detail": "The MCP token has
  expired. Replace it first: MCP token menu, Replace."}`. Turning it off
  always works.
- Disabled or locked (a timed out session counts as locked at once): every
  tool returns the text "bwtidy MCP is disabled. A logged in user must
  enable it in the web UI." and nothing else, not even an argument error.
- Every request to `/mcp` needs the header `Authorization: Bearer TOKEN`.
  Without it, with a wrong token or while no token exists, it is answered
  401 "bwtidy MCP needs a token. Create one in the web UI." (and
  `WWW-Authenticate: Bearer`) before the MCP SDK sees it. With the right
  token the switch still decides whether the tools work. Reason: several
  accounts share one URL, and an agent must reach only the account its token
  belongs to (the gateway routes by the token's hash, see Gateway).
- `POST /api/mcp/token` (session required) makes a new token
  (`secrets.token_urlsafe(32)`). It takes `{"days": N}` (an integer from 1
  to 365) or `{"days": null}` for no expiry, and without `days` the default
  of 90. It answers 201 `{"token", "createdAt", "expiresAt" or null}`, the
  only time the token is answered. Only its sha256 is stored, in
  `mcp-token.sha256` in the data directory, written atomically with mode
  0600: the hex on the first line, the expiry on the second (a UTC ISO
  timestamp, or `never`) and the creation time on the third (the file's own
  time is not kept by every copy), each line ending in a newline. A new
  token replaces the old one, which stops working at once.
- `DELETE /api/mcp/token` (session required) removes the file and answers
  204. `GET /api/mcp/token` answers `{"createdAt", "expiresAt",
  "lastUsedAt"}` and 404 without a token, never the token or its hash. `GET
  /api/session` reports `mcpToken` (whether the file exists, an expired
  token too), false while locked. The check compares the hashes with
  `hmac.compare_digest`.
- From its expiry on, a request with the token gets 401 "bwtidy MCP token
  expired. Create a new one in the web UI.", from the worker and from the
  gateway, never naming the account. Both compare the hash first, in
  constant time, then the time.
- Last use: the worker keeps the time of the last accepted MCP request in
  RAM and in its SQLite settings (`mcp_token_last_used`), updated at most
  once a minute. A new token and Remove forget it.
- The expiry chip warns once 3 days are left or 15% of the token's lifetime
  (its expiry minus its creation time), whichever comes first.
- The token dialog holds no commands: the per-client commands are on
  `docs/how-to/mcp.md`, which it links at `/docs/how-to/mcp/`, so there is
  one place to keep them right. The token's field has `data-shot-hide`, so
  the docs screenshot shows no token.

### The tools

- The tool note is invisible to MCP in every tool: not listed, `get_item`
  says not found, and a proposal naming it is refused as an unknown item.
- An unexpected error reaches the agent and the log by its type name only,
  its text could carry a value.
- `list_items` and `get_item` never answer a secret value, only
  `secretColumns`, the secret coordinates that are set. The `text` filter
  matches a name, a file name or any non-secret value, never a secret one,
  so the filter is no oracle, and of `login.uris` the uri text only, never a
  match word. `attachments` is a list of `{"id", "fileName", "size"}`, the
  size Bitwarden lists (the encrypted size), never the contents, the url or
  a key. No tool reads or answers an attachment's contents.
- `login.uris` in those answers is a list of `{"uri", "match"}`, `match` the
  word of Bitwarden's setting (`domain`, `host`, `startsWith`, `exact`,
  `regex`, `never`) or null for the client's default. The grid and every
  other answer keep the plain strings.
- The secret rule for formats: the fill scores count a non-secret column's
  format with its score and carry its entry in `formats`. A secret column
  counts as set or not set only and carries nothing, the engine does not
  even run on it for MCP. So the answers are byte for byte the same whatever
  a secret value looks like, as long as it is set, and no pattern is an
  oracle on a secret.
- `list_datatypes` lists every datatype that is not broken and that at
  least one non-secret template coordinate uses, with all the coordinates
  that use it (secret ones too) in `usedBy`. A datatype that only secret
  coordinates use, or none, is not listed. A field coordinate counts as
  secret when the template says hidden or when any item of the vault holds a
  field of that name as hidden, the safer of the two. Reason: an example
  typed as a real value of a secret column would reach the agent.
- `list_templates` names a coordinate's datatype as `"datatype": NAME`, so
  the agent never sees a datatype's id. It names a secret coordinate's
  datatype too, although `list_datatypes` may not list it: the name says
  nothing about a value. A coordinate whose datatype is unknown or broken
  has none, as in the score.
- `list_proposals` lets an agent see what the user rejected, so it does not
  propose it again. It answers the entries of source `mcp` only, newest
  first, at most 100, each `{"id", "summary", "status", "createdAt",
  "decidedAt", "reason"}`. `summary` is the one the propose call answered.
  `reason` is null, or for a failed entry one fixed word by its result's
  error: `conflict`, `merge conflict`, `write failed`, `interrupted`,
  `invalid`, and `failed` for anything else. Never a detail, a diff, an
  operation or a value, since a detail can carry one. A silent refusal reads
  as an entry the user has not decided yet (below).
- Session tools: `end_session` ends the session like `DELETE /api/session`,
  `log_out_of_bitwarden` ends it and logs the CLI out like `POST
  /api/bw-logout`. Like every tool they work only while a session runs and
  MCP is on. Each answers a short text first and does the work 0.5 seconds
  after the answer (`BwtidyMcp.after_answer`), so the answer goes out before
  a worker starts its exit, and a clean stop waits for that work
  (`BwtidyMcp.settle`). The tools carry `destructive_hint`, and the
  instructions tell the agent to call them only when asked to.
- Propose tools never write. They add an entry to the queue and return its
  id and summary. They take item ids and column names, never values (except
  a new item name, folder name, URIs and the like), and read the items'
  current `revisionDate`s and shown values themselves. An optional `reason`
  is appended to the summary.
- `propose_rename_folder` and `propose_delete_folder` name the folder as
  `list_folders` does and send its name as `shownName`. An unknown name, or
  one two folders have, is an error.
- `propose_set_uris` and `propose_set_uri_match` propose a `setUris` with
  the item's URIs as the agent's view held them as `shownUris`. The second
  gives every URI that `uri_pattern` matches (a shell glob over the whole
  URI, case ignored, every URI without one) the match and leaves the others.
  Items with no URI whose match it changes are left out, and none left is an
  error. It is one queue entry with every item. The summaries are `Set the
  URIs of "NAME" (N)` and `Set URI match MATCH on N URIs of M items`. An
  unknown match word, an empty uri or a regex that does not compile comes
  back at once and nothing is queued. A uri equal to a secret value of the
  item is a silent refusal.
- `propose_rename_attachment` sends the item's `revisionDate` and the
  attachment's name now as `shownFileName`. An attachment id the item does
  not have is an error.
- `propose_create_item` proposes a `createItem`, `generate_secrets` as its
  `generateColumns`. MCP never gives a secret value and has two choices per
  secret field: left blank (it counts as not set in the fill scores and goes
  on the To fill in list), or generated at write time, a value MCP can never
  read (the fill scores count it as set and nothing more). A type `sshKey`,
  or a template of type 5, is refused at once: the private key is a secret
  that MCP never gives, so the item could only fail with "SSH key private
  key is required". The message points to `propose_convert_item` on an item
  that holds the key.
- `propose_convert_item` proposes a `convertItem` with a mapping {target
  column: the item's column}. Reason for the mapping: a secure note that
  holds a private key can become an SSH key item without the secret passing
  through MCP (`{"sshKey.privateKey": "notes"}`). The summary is `Convert
  "NAME" to TYPE`, followed by the mapped columns as `(SOURCE to TARGET,
  ...)`, never a value. What `plan_conversion` refuses reaches the agent as
  `refused: REASON` and nothing is queued.
- There is no propose tool for a datatype, a corrected value, a generated
  value of an existing item or a restore.

### The queue

- An entry holds one operation of `POST /api/operations` (see Writes), a
  summary, its source (`ui` or `mcp`) and a status: `pending`, `approved`
  (being applied), `rejected`, `applied` or `failed`. An entry stays
  `approved` only while its commit runs, and it is `applied` as soon as its
  own write is done, before the rest of its batch is written.
- Agent proposals wait for the user, who approves or rejects them one by one
  or in batches. An approved batch is one operation: one backup, then the
  writes. Its entries are marked `approved` in one transaction once the
  diffs they keep are computed, so a failure there leaves every one of them
  `pending`. UI edits are committed on their own (Auto-commit below).
- Enqueueing checks the operation against the vault in RAM at once (shape,
  `revisionDate`, merge conflicts, everything the write path refuses), and
  the write path checks it again at approval.
- Refused at enqueue: any operation touching the tool note (except bwtidy's
  own section saves), and from an agent a `setLeaf` into a secret column and
  a `createItem` with `secretValues` ("the queue never takes a secret
  value"). Reason: the entry is stored in SQLite, which never holds a secret
  value. From source `ui` those two are queued encrypted (Hidden values
  below). A `setLeaf`'s column is secret when it is on the item in the vault
  or on the item as the queued `setLeaf`s, `removeLeaf`s, `generateLeaf`s,
  `setUris` and `renameField`s of that item before it leave it.
- `POST /api/queue` with `{"operations": [...]}` queues them from source
  `ui`, one entry per operation in order, and answers 201 `{"ids": [...]}`.
  They are checked exactly like the write path checks before writing but
  without a sync: 409 with `conflicts` or `mergeConflicts`, 422 for an
  operation that is not allowed, and nothing is queued then. The entries of
  one request are stored in one transaction. A `revisionDate` that bwtidy's
  own commit replaced counts as current (see Writes, Own writes).
- The summary names the kind, the items and the columns, never a value. A
  `createItem` is summarized `Create login "NAME"` (the type's word: login,
  secure note, card, identity, SSH key) or `Create "NAME" from template
  "TEMPLATE"`, plus ` with generated values` with `generateColumns`, else `
  with a generated password` with `generatePassword`.
- `GET /api/queue?status=...` lists the entries in id order with the
  operation, the result and a diff: one row per changed cell (`itemId`,
  `itemName`, `column`, `before`, `after`), secret values as `{"masked":
  true}`, a missing value as null, a trashed item as the pseudo column
  `trash` going from false to true. A `createItem` has one row per value it
  gives with `before` null and `itemId` null, plus a `login.password` row
  going to `(generated)` when a password is generated. A pending entry's
  diff is computed against the vault now (`diffError` says why when the
  operation does not fit any more), a decided entry keeps the diff it was
  decided on. `valueGone` is true for a failed entry whose hidden value was
  deleted at a start.
- `POST /api/queue/approve` with `{"ids": [...]}` applies the pending
  entries as one operations call. An entry blamed for a conflict, a merge
  conflict or an operation that is not allowed any more fails with the
  reason, and the rest is applied without it, still with one backup. When a
  write fails midway the entries before it are applied, the failed one fails
  (with error `conflicts` and the item when it found its item changed right
  before its write) and the ones after it go back to pending. A failed
  backup or an unreachable vault puts every entry back to pending. The
  answer lists the `applied`, `failed` and `pending` ids and the `backup`.
- `POST /api/queue/reject` with `{"ids": [...]}` rejects pending entries.
  Ids that are unknown or not pending any more are a 422 and nothing is
  decided.
- `POST /api/queue/dismiss` with `{"ids": [...]}` deletes the failed entries
  among them, the age ciphertext of a hidden value with the row (SQLite's
  `secure_delete`, so it does not linger in freed pages). The answer is
  `{"dismissed": [...], "notFailed": [...]}`. Reason: nobody reads a record
  of a dismissed failure, so it is deleted, not marked.
- `POST /api/queue/retry` with `{"ids": [...], "overwrite": false}` puts the
  failed entries back to pending in one transaction and approves them at
  once as one batch, with the same answer. Retried as they are: a write
  failure (`writeFailed`) and an `invalid` entry. Refused with 409, and
  nothing is retried: an entry that is not failed, one cut off during its
  commit (`interrupted`, its write may be done), a merge conflict (it needs
  a choice per item, so the merge is proposed again with one), one whose
  value is gone after a restart, a conflict without `"overwrite": true` and
  a conflict whose item or folder is gone. No ids or an unknown one are a
  422.
- With overwrite a conflict is retried after a sync: its `revisionDate`s and
  the values it says the grid showed (`shownFolderIds`, `shownFavorite`,
  `shownCollectionIds`, `shownUris`, `shownFileName` and a folder's
  `shownName`) are taken from the vault as it is then, and nothing else of
  the operation changes. So it writes its own columns over the change made
  elsewhere, and the item's other values stay as they are now. The backup of
  that write holds the other value.
- A failed entry's hidden value does not outlive its worker. At every start,
  right after the entries left approved fail, each failed entry with an
  encrypted value gets the marker `(gone after a restart)` in place of the
  ciphertext, with `secure_delete`, and is marked `valueGone`. Such an entry
  can only be dismissed. A pending entry keeps its ciphertext, the next
  unlock commits it.
- A failed entry nobody dismisses is deleted by the worker once it is older
  than `BWTIDY_FAILED_KEEP_HOURS` hours (default 30, counted from its
  `decidedAt`), the same way as Dismiss. It runs when the worker starts and
  then every hour. Only failed entries go. A backup that a deleted failed
  entry's result named is not in use any more.
- Unread counts on the Queue page's tabs are the browser's alone: the seen
  ids are kept in `localStorage` (key `bwtidy.seenQueueEntries`, plus `:`
  and the account's server and email behind the gateway, because each
  account's ids start from 1). A tab shown for 4 seconds without
  interruption marks its entries as seen. The backend knows nothing of what
  was read.

### Auto-commit

- The backend commits all pending `ui` entries as one approve (one backup,
  one call of the write path) when the oldest of them is at least
  `afterSeconds` old or their count reaches `maxEntries`. Defaults 180 and
  20. Only while the vault is unlocked, one commit at a time, checked once a
  second. Entries queued during a commit wait for the next one. Entries from
  MCP are never committed automatically.
- A commit that cannot run (no backup, no vault) leaves the entries pending
  and is tried again 30 seconds later. A `ui` entry that fails at the commit
  gets status `failed` and the result an approval gives (`conflicts`,
  `mergeConflicts`, `invalid`, `writeFailed`).
- `GET /api/queue/auto-commit` answers `{"afterSeconds", "maxEntries",
  "pending", "committing", "nextCommitAt", "commitCount", "phase"}`:
  `pending` counts the pending `ui` entries, `committing` is true for the
  whole commit (the reload included), also for an approval, `nextCommitAt`
  an ISO time or null, `commitCount` the number of commits and approvals
  that ended since the worker started (not one that left its entries
  pending), which the frontend watches to reload after one. `phase` says
  what the write path does: `syncing`, `backingUp`, `writing` (then also
  `done` and `total`) or `reloading`, null while no write path runs. Reason:
  a commit can take a while, and the user sees where it is.
- `PUT /api/queue/auto-commit` with `{"afterSeconds", "maxEntries"}` saves
  them in the `settings` table: afterSeconds 1 to 3600, maxEntries 1 to
  1000, else 422. `POST /api/queue/commit` commits every pending `ui` entry
  now, after a running commit has finished, and answers like an approval.
- Ending the session first commits the pending `ui` entries. If that commit
  cannot run, fails or is cancelled after 60 seconds, the entries stay
  pending (or failed) and the next unlock commits them at once, the same as
  pending `ui` entries found after a restart (how long an entry has waited
  is known in RAM only).
- A commit that is cut off keeps what it wrote: each entry is `applied` as
  soon as its write is done. The entry whose write runs when the commit is
  cancelled (a stop) or hit by an unexpected error fails as interrupted at
  once, its write may or may not be done. The entries not started yet go
  back to `pending` after a cancel. After an unexpected error they fail as
  interrupted too, since `pending` would repeat that error on every tick of
  the auto-commit.
- An entry still `approved` at the next start (the process was killed during
  its commit) gets status `failed` with the result `{"error": "interrupted",
  "detail": ...}` and is never retried. Reason: a write of the entry may be
  done, and a create could be made twice. Left `approved` for good it would
  show on no tab, and its ciphertext could not be dismissed.
- A clean stop of the backend waits for the commits under way before it
  ends the session (see Worker mode, Bounded stops).

### Hidden values

- On the first unlock the backend creates an age X25519 identity and stores
  it in the `settings` table (key `hidden_identity`) encrypted with the
  master password (`pyrage.passphrase.encrypt`, like the backups). Every
  unlock decrypts it into RAM, and it is wiped when the session ends.
- A secret `setLeaf` value from the UI and each of the `secretValues` of a
  `createItem` from the UI are encrypted to that identity's recipient before
  they are stored, so the entry's operation holds the armored age ciphertext
  in place of the value and only the unlock pays the scrypt cost. Every API
  answer shows that value as `{"masked": true}`, in the operation as in the
  diff. The value is decrypted only when the entry is written.
- A queued secret `setLeaf` whose field does not exist at the write creates
  it hidden, one whose field an earlier entry of the commit creates as text
  fails (see Writes, `setLeaf` and field types).
- When a login re-encrypts the backups for a new master password, the
  identity is re-encrypted first (see The tool note, The backup passwords).
  An identity that opens with none of the listed passwords is replaced, and
  the entries queued under the old one fail at the commit with the reason.

### Silent refusal

- An MCP `setLeaf`, `moveToFolder`, `renameFolder` or `setUris` whose value
  (a new name, a folder name, a uri) equals one of the item's own secret
  values (for a folder, of an item in it), also one queued from the UI and
  not written yet, is not refused with an error. Only `propose_rename_item`,
  `propose_move_to_folder`, `propose_rename_folder` and `propose_set_uris`
  send a free value.
- It is stored in one INSERT as already `rejected`, with the summary
  `Refused proposal for "<item name>": it would put a secret value of the
  item into <column>` (for several items `Refused proposal for N items: it
  would put a secret value of an item into folder`, for a folder `Refused
  proposal for the folder "NAME": it would put a secret value of an item in
  it into its name`), the value masked in the operation and the diff, and
  the result `{"error": "secretValue", "detail": ...}`. The agent's value
  and reason are not stored in plain.
- The agent gets the entry id and the same summary it would get for an
  accepted proposal. Reason: no answer, error or timing may depend on
  whether a value the agent chose equals a secret, else a refusal confirms a
  guessed password. The user sees the refused proposal in the Rejected tab.
  The same operation from the UI is still refused with the error.
- What `list_proposals` shows of it: the summary its propose call answered,
  status pending and no decided time, until an entry that is not a silent
  refusal gets a decided time later than the refusal's (an approve, a
  reject, an auto-commit or Commit now, of any source). From then on it is
  rejected, with that decision's time as its decided time
  (`agent_decided_at`, set once). Reason: until the user decides anything, a
  refused proposal must read exactly like one that waits for them, else
  listing confirms a guess, and an entry that stayed pending for good would
  make the agent propose it again.
- The summary the agent was answered can hold the guessed secret (a rename's
  new name, its reason), so every entry of source `mcp` keeps it as age
  ciphertext to the identity for hidden values, and `list_proposals`
  decrypts each listed summary, refused or not. One queued under an identity
  that was replaced reads `(the summary cannot be read)`.

## UI edits and the auto-commit

What a user sees of the queue and the auto-commit is in
[How edits are saved](explanation/edits.md) and
[App bar](reference/ui.md#app-bar). The backend side is in MCP and the
queue. What the web UI does with it:

- Every UI write goes to `POST /api/queue` (source `ui`). A 409 or 422 shows
  its reason, and the merge choices work against the 409. Nothing in the UI
  waits for a write, so dialogs are never greyed out and there is no
  progress bar for a save.
- While `ui` entries are pending or a commit runs, the app asks for `GET
  /api/queue?status=pending` and `GET /api/queue/auto-commit` every second,
  else every 10 seconds. An entry that leaves the pending list stays in the
  grid overlay until the commit ended (a status with a new `commitCount` and
  `committing` false) and the grid reloaded, so a cell never flips back to
  its old value for the time of the commit. Then the grid, the templates and
  the change log reload.
- The spinner's text comes from the status's `phase`. There is no separate
  chip for the unsaved edits, it would repeat the Queue button's badge.
- The current page's button in the app bar is not disabled, so it keeps its
  label color and a screen reader announces it as the current page
  (`aria-current="page"`). It does nothing when clicked and keeps its size.
  No button is current on Settings and Statistics.
- Failed `ui` entries this browser has not reported yet (their ids are kept
  in `localStorage` per account) show in a snackbar on every page, checked
  after every commit and once when the app starts (a commit at the session
  end may have failed while no tab was open). For a conflict the rows and
  cells get the conflict marks (see Grid), the cells of other failed entries
  a tint until the snackbar is closed. Dismissing or retrying a failed entry
  on the Queue page takes back what that entry added, and the marks of other
  failed entries stay.

## Development and tests

Development and tests never touch anyone's vault on a Bitwarden server or
the host's Bitwarden CLI state: they run against a dev stack with a seeded
synthetic vault and never run the backend on the host, as the developer
guide (`dev/README.md` in the repository) describes with the suites, the
screenshots and the docs build.
