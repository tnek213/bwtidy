# bwtidy specification

This document is written mostly for the AI agents that work on the code. You
can look up a fact or the reason behind a rule in it, but it is not meant to
be read from top to bottom. To learn bwtidy, start with [the user
docs](index.md).

bwtidy is a self-hosted web UI and MCP server that keeps a Bitwarden or
Vaultwarden vault consistent. It shows the whole vault as one wide grid and
normalizes field names across items. It applies templates too, and it lets an
AI agent propose reorganizations without ever seeing a secret value.

This document holds the rules and reasons that the user docs do not state.
They are invariants, contracts and decisions.

An invariant says where a secret value may never go, how a write is protected
or how accounts are kept apart. A contract fixes a stored format or the
answers of the gateway, the API and the MCP tools. A decision comes with its
reason so that nobody "fixes" it back.

Every rule is in the present tense. What a user sees and does is in the user
docs, and a section here links to that page instead of repeating it. These
are the pages:

- [The web UI](reference/ui.md) has every page, menu and button.
- [Configuration](reference/configuration.md) has the variables and the files.
- [MCP tools](reference/mcp-tools.md) has the MCP tools as an agent's client
  lists them.
- The explanation pages say why bwtidy works the way it does for a user,
  starting from [How bwtidy fits together](explanation/overview.md).
- [architecture.md](architecture.md) says where the code lives.

## The vault it is made for

bwtidy is made for a personal vault of a few hundred items. Most of them are
logins and secure notes whose custom field names have grown inconsistent. The
same thing is spelled several ways across items, and some logins are stored
as secure notes. The same kind of item comes in several shapes with different
field names. An organization shares some of the items with other people
through collections.

The problem is inconsistency rather than size. Normalizing is the key
feature, and the whole vault fits in RAM.

## Architecture

The parts are one container with a gateway, plus a worker and a `bw serve`
for each account. [How bwtidy fits together](explanation/overview.md) and
[architecture.md](architecture.md) describe them. These are the decisions
behind them:

- The Bitwarden CLI (`@bitwarden/cli`) is pinned to one exact version in
  `docker/bitwarden-cli/package.json`. Its lock file fixes every package the
  CLI brings along.
- The image ships the CLI's open source build, built from Bitwarden's source
  at the commit that `docker/bitwarden-cli/source.json` pins. That is the
  tag `cli-v` plus the pinned version. Bitwarden's npm package is their
  commercial build, which bundles code under the Bitwarden License. A
  build that bundles a module under `bitwarden_license/` fails.
- bwtidy talks to `bw serve` instead of running one `bw` call per operation,
  because every CLI call pays for a Node startup. The login and the backups
  still run the CLI (see Backups).
- The vault is loaded in one listing, since a few hundred items need no
  paging.
- `bw serve` has no authentication of its own, so it is never published. Only
  the web port is published. It listens on the host's 127.0.0.1 to keep the
  unlocked vault away from other local programs. This is not hacker proof
  because whoever is in the host's `docker` group can reach it, and that is
  accepted.
- The image adds to its base (`python:3.13-slim-trixie`) only what runs. That
  is the backend's venv and Node.js with the CLI's installed packages, along
  with the static files and the docs site. npm and uv stay in the build stages
  with the sources (`dev/release.md`). Every package in the image is code that
  a scanner reports on and an attacker could use.

## Worker mode

Several Bitwarden accounts use one bwtidy URL, and each is fully separated
from the others (see [How accounts are kept apart](explanation/isolation.md)).
There are only a few accounts, so isolation and simple code matter more than
scale.

- Only a click on Unlock or Log in starts a worker (see Gateway). A worker is
  a Python process of about 100 MB plus its `bw serve` of about 190 MB.
  Opening the page or filling in the account form needs no credential, so
  neither starts one.
- There is no shared SQLite. The directory tree under `accounts/` is the
  account list.
- The account's `app/bwtidy.sqlite` is not encrypted. A secret value is in it
  only as an age ciphertext (see Secret values). But the queue's diffs hold
  the plain values, and the change log holds the coordinates that each write
  changed with their custom field names (see Writes). The account directory
  (mode 0700) keeps the file from other uids. What it holds is documented
  rather than encrypted.
- A browser profile uses one account at a time. All tabs of the profile share
  the account, so the other tabs reload when it changes (see The account
  form). MCP takes a bearer token per account.
- A deployed bwtidy runs behind a reverse proxy that terminates TLS. It is
  reachable only over a private network such as a VPN.
- A deploy keeps every account's data (see Schema versions).

The backend becomes a worker through the settings below.
[Configuration](reference/configuration.md#worker) has the full list with
their defaults. Without them the backend runs as a single-user app with `bw
serve` on `127.0.0.1:8087`. Only the integration tests start it that way, in
the image's dev target as uid 1000. `compose.yaml`, `scripts/dev-app` and the
end-to-end tests run the gateway. These are the settings:

- `BWTIDY_SOCKET` makes the app listen on this unix socket. A stale socket
  file is replaced, and the app removes the file when it exits.
- `BWTIDY_BW_SOCKET` makes `bw serve` listen on this unix socket (`--hostname
  unix://PATH`). A socket file left by a killed `bw serve` is removed before
  every start because the new one cannot listen otherwise. The reason for the
  socket is that no process can reach another account's unlocked `bw serve`
  over TCP, since the socket's directory belongs to the account's uid.
  `--port` is still passed and the requests still carry `Host:
  127.0.0.1:PORT`. `bw serve` answers 403 to any Host outside
  `localhost:PORT`, `127.0.0.1:PORT` and `[::1]:PORT` even on a socket (PORT
  defaults to 8087).
- `BWTIDY_ACCOUNT_SERVER` and `BWTIDY_ACCOUNT_EMAIL` fix the worker's account.
  They come together, and the start fails with only one of them. `POST
  /api/bw-login` with another server or email answers 422 `This bwtidy worker
  belongs to EMAIL on SERVER.` The server is compared without a trailing slash
  and case, and the email is lowercased. `GET /api/session`'s `bwLogin` then
  carries the fixed server and email (never the browser's) and `accountFixed:
  true`. Without these settings `accountFixed` is left out.
- `BWTIDY_EXIT_AFTER_SESSION=1` makes the worker exit right after its session
  ended. It also exits when no session started within `BWTIDY_IDLE_EXIT`
  seconds of its start (default 600) while no Bitwarden login runs. A login
  counts until it finishes or is dropped. The exit is a clean shutdown with
  exit code 0, so nothing of the vault stays in RAM between sessions. A new
  login that replaces a running session does not make the worker exit.
    - The exit counts as settled from the moment the session's end starts or
      the idle time has passed. The end can take long since it commits the
      pending edits. From then on `/api/health` answers 503 `{"status":
      "exiting"}`, so the gateway never hands a browser a worker on its way out.
    - A login that reaches the exiting worker anyway starts no session. It
      answers 502 with the gateway's own text `The account's worker does not
      answer`. The frontend reads that like a 502 of the gateway. It opens the
      account again and repeats the login, and the gateway waits for the old
      worker to go before it starts the next. Without this a new page's unlock
      during the old page's log out would succeed on the exiting worker, whose
      exit would then drop the session.
- `BWTIDY_BACKUPS_DIR` puts the backups in this directory instead of
  `backups` in `BWTIDY_DATA_DIR`. Everything that touches the backups uses it.
  That is the backups themselves and their retention, as well as the
  re-encryption and the backup routes. The fingerprints in SQLite name a
  backup by its file name only.
- `BWTIDY_PENDING_MARKER` names a file that the worker deletes after its first
  successful unlock. It does so once the cache, the tool note and the
  backups' password are done. The gateway creates the marker with a new
  account's directory. It keeps that directory only when `bw login` and the
  unlock succeed, so the marker tells a new account that never logged in from
  a real one.

Every backend writes `bw-login-state` in `BWTIDY_DATA_DIR`. The file holds one
line (`logged in` or `logged out`) with mode 0600. It is swapped in atomically
and only when it differs from what this process wrote last.

The file notes what `GET /api/session` and the start of a Bitwarden login find,
and nothing when `bw status` gave no answer. It also notes a `bw login` or `bw
logout` that exits 0 and every successful unlock. The reason is that the
gateway shows the unlock or the Bitwarden login before a worker runs. It never
asks a worker that does not run. This file, the MCP token hash and the delete
marker are all the gateway reads from an account.

### Stopping a `bw` process

A `bw` process always gets SIGTERM first and SIGKILL only when it is still
there 5 seconds later. This holds for every `bw` the backend starts. That is
the commands of a backup as well as `bw serve`, `bw login` and `bw logout`.

The reason is that every `bw` process shares one CLI state in `bw/data.json`.
A process rewrites the whole file (truncate, then write) under the lock
directory `data.json.lock`. A SIGKILL between the truncate and the write
leaves an empty file, which means a logged out CLI. A running `bw serve` then
does not answer its next `/unlock` at all. A SIGKILL also always leaves the
lock behind. That costs the other `bw` processes about 10 seconds until they
call it stale. On SIGTERM node finishes its write and the lock goes with it.

Even on SIGTERM a dying `bw export` can leave the lock behind, and `bw
serve`'s `POST /lock` then waits those 10 seconds. Were the session's end to
give up on the lock and a new login to unlock, the lock would land a moment
later. The login would then find the vault locked or answer 200 on a locked
`bw serve`. So a session's end never stops a `bw` it does not have to stop:

- A session's end stops the background backup between its steps. The backup
  checks a flag before each call to `bw serve` or the CLI. The end waits up to
  30 seconds (`BACKUP_STOP_LIMIT`) for the `bw` that runs and only then
  cancels it. Then comes `POST /lock`. A worker's stop (SIGTERM) cancels the
  backup at once, so the 30 seconds never add to a stop.
- A `POST /lock` that gave no answer within its 10 seconds is not dropped,
  because `bw serve` still runs it. The next login waits for it up to 20
  seconds more (`LATE_LOCK_LIMIT`) before it unlocks. After that the login
  sends no unlock and answers 503 `bw serve has not finished locking the
  vault, try again`. While the late lock runs it stands for the next lock too.
  Waiting is chosen over a restart of `bw serve` because the lock lands within
  a second after the 10. A restart costs its stop and its start, plus the wait
  until it answers.
- After the cache load the login asks `bw serve` for its status. It fails with
  502 `bw serve locked the vault again during the login, try again` unless the
  status is `unlocked`, so a login never answers 200 on a locked `bw serve`.

### Bounded stops

The exit and a SIGTERM end within a bounded time, also when `bw serve` or the
Bitwarden server does not answer. Each call to `bw serve` waits up to 120
seconds. With `bw serve` frozen, an end that waited the full 120 seconds per
call would exit the worker minutes late.

- The session's end gives up on its commit after 60 seconds. The entries not
  written stay pending, and the one being written fails as interrupted. The
  end gives up on a running background backup after 30 seconds and on the
  lock of `bw serve` after 10. It goes on either way, and an exiting worker's
  stop of `bw serve` takes the vault out of RAM.
- On a stop uvicorn waits 30 seconds for the requests still running and then
  cancels them. A commit that one of them runs is cut off (see MCP and the
  queue). The clean shutdown then waits 60 seconds for the commits under way.
  It cancels those that are not done, and a cancelled commit gets 10 seconds
  to end. Then the shutdown ends the session (without a new commit after such
  a cancel) and stops `bw serve`. So a stop usually ends before the gateway
  kills the worker 120 seconds after its SIGTERM.

### Logs

Every line of the gateway and the workers starts with its time. Our own lines
are logged from INFO up and other libraries' lines from WARNING up, because
INFO of a library can name what passes through it (like the URLs of httpx).

The login and the session's end log each step by name with a refusal's or an
end's reason, but never a value. Examples are `Login: started` and `Session
end: bw serve locked`. Uvicorn writes its request line only when the answer
starts. So the gateway logs when a `POST` or `DELETE /api/session` starts and
what status it got. A frozen login or end then shows where it stopped.

## Gateway

The gateway (`backend/src/bwtidy/gateway/` with the entry point
`bwtidy-gateway`) is the one process that listens. It starts the workers and
proxies to them. Unless the usage stats are on it never parses, logs or
stores anything that passes through the proxy. With them on it reads and
keeps only what Usage stats names. It logs account keys and uids as well as
worker starts and exits. It also logs the session requests (see Logs) and a
request that a worker did not answer.

The gateway runs as root (`USER 0`) because it starts each worker as the
account's uid. `compose.yaml` drops every capability but six. It keeps
`CHOWN`, `DAC_OVERRIDE` and `FOWNER` for the account files. It keeps `SETUID`
and `SETGID` to start a worker as its uid and `KILL` to stop it. It also sets
`no-new-privileges` and makes the root file system read-only. The volumes of
the accounts, the backups and the usage stats and a tmpfs on `/tmp` are the
only places that take writes.

The gateway's settings are in
[Configuration](reference/configuration.md#gateway). These are the reasons
behind some of them:

- `BWTIDY_UID_BASE` defaults to 70000 because a host's own service uids often
  take the range from 10000 up.
- `BWTIDY_GATEWAY_SWITCH_UID=0` exists only for the unit tests, which cannot
  switch uids. Otherwise the gateway refuses to start unless it is root.
- The session timeouts and the backup retention are passed on to every worker
  along with the failed entries' age limit. They are policy that a deployment
  may have, so the environment sets them and the policy reaches each worker.
  The worker stop time and the `bw` time limits stay fixed, and so do the
  display limits and the datatype pattern limits. A value that cannot be used
  stops the gateway and the app at the start with an error that names the
  variable.
- Every number setting of the gateway, and `BWTIDY_IDLE_EXIT`, is checked at
  the start against the values that Configuration names. Reason: a typo in
  `.env` stops the gateway with an error that names the variable, not with a
  traceback or a limit under which no account can open.
- `BWTIDY_IDLE_EXIT` is passed on so that a test can shorten it.
  `BWTIDY_MCP_HOSTS` is passed on so that the public host name passes the
  worker's DNS rebinding check (see MCP and the queue).
  `BWTIDY_BW_BIN` is passed on so that an operator can run a Bitwarden CLI of
  their own, mounted into the container, at their own risk. When it is unset
  the worker runs the image's `bw`.

### Account key

An account key is a server and an email.

- The server is a lowercased https URL with an empty path or `/`. It has no
  user info, no query and no fragment. So it reads `https://host` with
  `:PORT` added for a port other than 443. The directory name is the host
  with `_PORT` added for such a port.
- Unless the server is in `BWTIDY_ALLOWED_SERVERS`, every address its host
  resolves to must be global (`ipaddress` `is_global`) and not multicast. The
  address must also lie outside the ranges below. `ipaddress` counts some of
  them as global although they lead to an IPv4 address, private ones too.
    - `64:ff9b::/96` and `64:ff9b:1::/48` (NAT64)
    - `::/96` (IPv4 compatible)
    - `::ffff:0:0:0/96` (IPv4 translated)
    - `2002::/16` (6to4)
    - `2001::/32` (Teredo)

  Otherwise the answer is 422 `SERVER does not resolve to a public address.`,
  and a host that does not resolve gets the same answer. The reason is that
  bwtidy takes any https server but nothing on the private network or in the
  container's network.
- The check runs again right before `bw login`. A `POST /api/bw-login`
  (exactly that path) routed to such a worker is checked first. On a refusal
  the gateway answers the same 422 and never forwards the request. A known
  limit is that `bw` resolves the name itself afterwards, so a DNS rebinding
  server can still win the race. The second check only narrows that window.
  The rest of the login (`/output`, `/input` and `/finish`) is not checked
  again.
- The email is lowercased and at most 254 characters long. It must match
  `^[a-z0-9_%+-][a-z0-9._%+-]*@[a-z0-9.-]+[.][a-z]{2,}$` and hold no `..`, or
  the answer is 422. The local part does not start with a dot because a
  directory name with a dot in front is one of the gateway's temporary ones.
- The account directory is `ACCOUNTS_DIR/HOST/EMAIL`. A path that resolves
  outside the accounts directory or through a symlink is refused (422).

### Account directory

An account directory holds these files and directories.

```text
ACCOUNTS_DIR/HOST/EMAIL/
├── app/                 BWTIDY_DATA_DIR
│   ├── bwtidy.sqlite
│   ├── backups/         only without a backups root
│   ├── mcp-token.sha256
│   ├── bw-login-state
│   └── delete-requested once Delete my account ran
├── bw/                  BITWARDENCLI_APPDATA_DIR
├── run/
│   ├── app.sock
│   └── bw.sock
├── home/                HOME
├── worker.log           the worker's stdout and stderr, appended
└── .pending             until the first unlock
```

- The gateway makes the directory as root at the account's first Bitwarden
  login, not when the account form opens the account. The account's uid and
  gid own everything in it with mode 0700 (the gid is the uid). The accounts
  directory and the host directories have mode 0711. So a worker can enter
  them but cannot list the other accounts.
- The directory is made under the temporary name `.new-EMAIL`. It is renamed
  into place once its uid owns everything in it, so an interrupted creation
  leaves no account directory. A name with a dot in front never is an
  account, and the gateway deletes a leftover at its start.
- A new account gets a uid one above the highest owner among all account
  directories and all directories under the backups root. Pending account
  directories count too. A directory under the backups root can outlive its
  account directory, so its uid is never handed to another account. The uid
  is at least `BWTIDY_UID_BASE + 1`. The uids have no passwd entries.
- The gateway refuses an account directory owned by root, by a uid at or
  below `BWTIDY_UID_BASE` or by the owner of another account directory. It
  answers 422 `The account directory is not usable.` and starts no worker.
  The reason goes in its log. At its start it logs every such directory.
  Whatever the base, no worker starts as uid 0 or below (503 `The worker's
  uid is not usable.`).

### Backups root

With `BWTIDY_BACKUPS_ROOT=ROOT` an account's backups live in `ROOT/HOST/EMAIL`
instead of its `app/backups`. That path has the same parts and checks as the
account directory. So `ROOT` can be any mount apart from the account data.
`compose.yaml` sets it to `/backups`, which is its own volume.

- Before each worker start the gateway makes the directory as root under the
  account's lock. `ROOT` (made at the gateway's start) and the host directory
  get mode 0711. The account's directory gets mode 0700 and is owned by the
  account's uid and gid, also when it was there already. An account made anew
  gets a new uid and the old one's files are given to it. The gateway hands
  the path to the worker as `BWTIDY_BACKUPS_DIR`.
- The directory is never reached through a symlink. When the host directory
  or the account's directory is a symlink or not a directory, the worker is
  not started. The answer is 503 `The worker could not start.` with the
  reason in the gateway's log. `ROOT` itself may be a symlink because the
  deployment chose it. The account's uid owns its directories and could point
  a symlink at another account's backups, which the gateway reads and writes
  as root.
- A pending account directory that is deleted takes its directory under the
  root with it, unless that directory holds backups. Those can be from before
  the account directory was deleted by hand, and the root is meant to outlive
  it.
- `ROOT` and `BWTIDY_ACCOUNTS_DIR` must not be one inside the other (with
  symlinks resolved), or the gateway stops at its start.

### Routes of the gateway itself

- `GET /api/health` (and `/api/health/`) answers `{"status": "ok", "version":
  ...}` from the gateway and never from a worker.
- `GET /gateway/account` answers what the page needs before a worker runs,
  for the browser's routing cookie. The answer is `{account: {server, email},
  running, exists, pending, loggedIn, firstRun}`.
    - `running` says that the account's worker runs and its `/api/health`
      answers 200.
    - `exists` says that the account directory exists, and `pending` that it
      has `.pending`.
    - `loggedIn` is the line of `app/bw-login-state` (at most 64 bytes). The
      file is read with the rules of the MCP token file (see MCP below).
      Without a file to trust the account counts as logged out.
    - `firstRun` is true when no account directory has both its SQLite file
      and no `.pending`. The SQLite file must be a regular file and not itself
      a symlink, and only its existence counts. `firstRun` is the same for
      every browser (see The account form).
    - Without a routing cookie `account` is null and the rest is false, apart
      from `firstRun`. `account` stays after the worker exited.
- `POST /gateway/account {server, email, create}` validates and sets the
  routing cookie and answers like the `GET`. It starts no worker and makes no
  directory. A missing directory answers 404 `{"exists": false}` unless
  `create` is true. Then the cookie is set for the account without a
  directory, which its first Bitwarden login makes. A server, an email or a
  directory that the gateway refuses gets 422. The browser's old routing
  cookie is forgotten.
- `DELETE /gateway/account` (204) forgets the routing cookie, while the
  worker keeps its own session rules. Only the worker of an account that
  still has `.pending` is stopped (SIGTERM). The answer then waits until the
  worker exited and its directory is gone. So the account form can offer to
  create the account again at once.

### Workers

- Workers start on a click. `POST /api/session` (the unlock) and `POST
  /api/bw-login` with a routing cookie are the only requests that start the
  account's worker when none runs. It is exactly these two. Opening the
  account must not start a worker, which takes about 300 MB for up to 10
  minutes without any credential.
- The gateway starts the worker under a lock per account key. It waits until
  the worker's `/api/health` answers and then passes the request on
  unchanged. A running worker whose `/api/health` does not answer 200 is on
  its way out. The gateway waits up to 30 seconds outside the lock for it to
  go and then starts a new one. When the old worker is still there after
  that the answer is 503.
- The start of a Bitwarden login of an account without a directory first
  makes the directory with `.pending`, after the server's address check. An
  unlock of an account without a directory answers 401 `{"detail": "No
  account"}`. The answer is 503 `{"detail": ...}` when the worker cannot be
  started (`The worker could not start.`), exits or does not answer in time.
- Anyone who reaches the gateway can click Log in or Unlock without the
  master password, and each click starts a worker for up to 10 minutes. So
  the starts have caps:
    - When `BWTIDY_MAX_PENDING` directories with `.pending` exist, a login that
      would make a new one answers 429 `Too many new accounts wait for their
      first login. Try again in a few minutes.` An existing pending account
      still starts its worker.
    - When `BWTIDY_MAX_WORKERS` workers run or are starting, the start of one
      more answers 429 `Too many accounts are open at once. Try again later.`
      This is checked before a new directory is made, so a refused login leaves
      nothing behind.
    - A worker counts from the moment its start begins until its start fails or
      it exits. Each check and the reservation it guards happen without an
      await in between, so concurrent clicks cannot pass a cap together.
- A worker starts as root with the `bwtidy` entry point next to the gateway's
  own Python. It drops to the account's uid and gid with `extra_groups=[]`,
  umask 0o077 and a new session. Its environment is built from scratch, and
  [Configuration](reference/configuration.md#worker) lists it. Its cwd is the
  account directory.
- The gateway opens `worker.log` without following a link and without
  blocking. It refuses to start the worker (503) when that is not a regular
  file owned by the account's uid. A missing one is made with mode 0600.
- The gateway waits up to 60 seconds for `/api/health` on `run/app.sock`.
  Otherwise it kills the worker (its whole process group) and answers 503.
  One worker per account runs at a time.
- Proxied requests have no timeout, and only the gateway's shutdown cuts them
  off. Reaching a worker needs a routing cookie or an MCP token. Logins,
  backups and MCP streams are long on purpose.

When a worker exits the gateway forgets it.

- With the delete marker `app/delete-requested` in its directory (see Deleting
  data) the gateway deletes the account directory. It also deletes the
  account's directory under the backups root even with backups in it. It
  forgets the browser's routing to the account as well. The marker is trusted
  only the way the MCP token file is, so a worker can ask the gateway to
  delete nothing but its own account. A new worker already running in the
  directory keeps it. A worker deletes a marker it finds at its start, so the
  account stays when it was opened again at once.
- A directory that still has `.pending` is deleted because its login never
  succeeded, unless a new worker already runs in it. A worker that cannot be
  spawned leaves no `.pending` directory either. At its start the gateway
  deletes every account directory that has `.pending`.
- A directory is only ever deleted directly at `ACCOUNTS_DIR/HOST/EMAIL` and
  never through a symlink.

At its shutdown the gateway waits up to 10 seconds for the requests in flight
and cuts off the rest (an MCP stream stays open for good). Then every worker
gets SIGTERM and is killed when it still runs 120 seconds later.

- The kill goes to the worker's whole process group. So its `bw serve` with
  the unlocked vault in RAM never outlives it as the account's uid, and
  neither does a running CLI command. The SIGTERM goes to the worker alone,
  whose clean shutdown needs `bw serve`.
- `compose.yaml` gives docker's stop 150 seconds (`stop_grace_period`). That
  is more than those 10 and 120 seconds plus the 5 of `bw serve`, because a
  docker kill would hit every `bw` at once (see Stopping a `bw` process). The
  120 seconds are meant for a commit with a fresh backup, for a background
  backup that the session's end waits for and for that commit.

### Routing cookie and proxy

The gateway decides where a request goes by its path and then by its routing
cookie or its token. The sections below give the details.

```graphviz alt="The gateway answers a health check, its own account routes, the routers a wrapper passes in, the documentation site and the static files itself. A request to /mcp or below goes to the worker of the account whose unexpired token it carries when that worker runs, or else gets a 401 or a 503. A request under /api/ needs a routing cookie that names an account, or it gets 401 No account. An unlock or a Bitwarden login first makes sure the account's worker runs and answers, starting or replacing it, and may be refused with 401, 422, 429 or 503. Any other request goes to the running worker, or gets 401 No account when none runs."
digraph {
  nodesep=0.35
  ranksep=0.35
  fontname="sans-serif"
  node [fontname="sans-serif" fontsize=13 shape=box style="rounded,filled" fillcolor="#ffffff" color="#5c6bc0" margin="0.12,0.06"]
  edge [fontname="sans-serif" fontsize=12 color="#455a64"]
  request [label="A request" fillcolor="#e8eaf6"]
  path [label="Which path?"]
  own [label="The gateway answers:\nhealth, account routes,\nextra routers if any,\nthe docs site, the static files" fillcolor="#e8eaf6"]
  token [label="Does the bearer token\nmatch an unexpired token?"]
  mcprun [label="Does that account's\nworker run?"]
  cookie [label="Does the routing cookie\nname an account?"]
  click [label="Is it POST /api/session\nor POST /api/bw-login?"]
  start [label="Make sure the worker runs:\nstart it or replace one\non its way out"]
  running [label="Does its worker run?"]
  worker [label="Proxied to the worker" fillcolor="#f1f8e9" color="#558b2f"]
  node [fillcolor="#fff3e0" color="#e65100"]
  mcp401 [label="401"]
  mcp503 [label="503 locked"]
  noaccount [label="401 No account"]
  refused [label="401, 422, 429 or 503"]
  request -> path
  path -> own [label=" /api/health, /gateway/,\n /docs and the rest"]
  path -> token [label=" /mcp and below"]
  path -> cookie [label=" /api/"]
  token -> mcp401 [label=" no"]
  token -> mcprun [label=" yes"]
  mcprun -> mcp503 [label=" no"]
  mcprun -> worker [label=" yes"]
  cookie -> noaccount [label=" no"]
  cookie -> click [label=" yes"]
  click -> start [label=" yes"]
  click -> running [label=" no"]
  start -> refused [label=" refused"]
  start -> worker [label=" it answers"]
  running -> worker [label=" yes"]
  running -> noaccount [label=" no"]
}
```

- The routing cookie `bwtidy_account` holds 32 random bytes and is HttpOnly
  with SameSite=Strict and Path=/. It has no Max-Age and is Secure with
  `BWTIDY_SECURE_COOKIES=1`. The gateway maps it in RAM to the account key.
  A gateway restart forgets the map, and the frontend then opens the account
  again. The cookie names the account rather than the worker, so it reaches
  the account's next worker too.
- Every request under `/api/` (except `/api/health`) with a routing cookie of
  a running worker goes to that worker over its socket. It is streamed both
  ways with the hop-by-hop headers dropped. The `bwtidy_account` cookie is
  taken out of the Cookie header. The gateway uses httpx's
  `AsyncHTTPTransport(uds=...)` directly because a client would keep a cookie
  jar. That jar would carry one browser's session cookie into another
  browser's requests.
- Each request gets a connection of its own. The worker's uvicorn closes a
  connection that idled 5 seconds, and a request sent on a kept connection
  just as the worker closes it is lost. A new connection on a unix socket
  costs next to nothing.
- A request that the worker does not answer gets 502 `{"detail": "The
  account's worker does not answer"}`. The gateway then logs the worker's pid
  and age, whether its process still runs and whether the connection was new.
  It logs httpx's error too, but never the path or a body.
- Every `Set-Cookie` of the worker's answer that sets `bwtidy_account` is
  dropped, so a worker cannot change the browser's routing cookie.
- Those paths answer 401 `{"detail": "No account"}` without a routing cookie.
  They answer the same without a running worker for anything but the two
  clicks above. The frontend then opens the account again.
- Every other path belongs to the gateway. Its own routes come first and the
  routes passed in `routers` next (see Extra routes and the exit hook). Then
  come the documentation site and the static files (mounted last). The
  workers get no static directory, so the frontend always comes from the
  gateway.

### The documentation site

The gateway serves the site in `BWTIDY_DOCS_DIR` at `/docs/` to anyone,
without a routing cookie or a login. `/docs` redirects to `/docs/`. A missing
page is a 404 of the site and never the frontend, and no request under
`/docs` reaches a worker. FastAPI's own API pages (`/docs` and `/redoc`) are
off. [architecture.md](architecture.md#the-container) says how the site is
built. These make up its look:

- a content column wider than the theme's
- running text of at most 72 characters a line
- pictures with a thin border that open larger on a click
- a warm ivory and deep teal palette of its own for each color scheme, its
  text and links at WCAG 2.2 AA contrast
- code in IBM Plex Mono, which the site carries itself
- a footer that credits Zensical and the Diátaxis framework
- nothing loaded from a CDN

The site sends another site nothing about the host it is served from, as the
address of a self-hosted bwtidy stays private. No page requests anything from
another host. The header's version and star count are written at build time
rather than fetched from GitHub. The referrer policy `same-origin` keeps the
`Referer` off every link to another site.

### Extra routes and the exit hook

`create_gateway_app` also takes `routers` and `on_worker_exit`. With them an
outside package that wraps the gateway (such as the demo) can add pages of
its own and learn when a worker exits. bwtidy itself knows nothing of such a
package. `bwtidy-gateway` passes neither, so the gateway then behaves as
described above.

- `routers` are FastAPI `APIRouter`s, included in their order after the
  gateway's own routes. They come before the documentation site and the
  static files. So a router's path wins over a static file of the same path
  (`/` or `/index.html`) but not over a route of the gateway. Paths the proxy
  takes (under `/api/` and `/mcp`) never reach a router because the proxy
  runs first.
- `on_worker_exit` is an async callable awaited once with every `Worker` that
  exited. It runs after the gateway's own cleanup (the delete marker and the
  `.pending` directory) and outside the account's lock, so it may take the
  lock itself. It also runs when that cleanup failed. The gateway logs an
  exception in it as `The worker exit hook failed for ACCOUNT: TYPE` with the
  exception's type only, as its text could hold a value. The exception never
  propagates. The gateway keeps serving and still forgets the worker.

### MCP

An MCP client has no routing cookie, so a request to `/mcp` (or below it) is
routed by its `Authorization: Bearer TOKEN` alone.

- The gateway hashes the token with sha256. It looks for the account
  directory whose `app/mcp-token.sha256` holds that hash on its first line,
  compared with `hmac.compare_digest`. The files are read as root on every
  request because there are few of them.
- A token file counts only when the file itself is a regular file and not a
  symlink, and the account's uid owns it. The owner check is why a worker
  cannot point the gateway at another account's hash. The same rules hold for
  `app/bw-login-state` and `app/delete-requested`, which are the only other
  files the gateway reads from an account directory. When two accounts hold
  the same hash, neither is used.
- No token or no match gets 401 with the worker's own text `bwtidy MCP needs
  a token. Create one in the web UI.` and `WWW-Authenticate: Bearer`.
- A match whose token expired gets 401 `bwtidy MCP token expired. Create a
  new one in the web UI.` The expiry is the file's second line. The answer
  never names the account, whether or not its worker runs. The hash is
  compared first and the time after it. A file whose expiry cannot be read
  holds no token.
- A match whose worker is not running gets 503 `bwtidy is locked for this
  account. Log in in the web UI.` as plain text. An MCP request never starts a
  worker.
- A match with a running worker is proxied unchanged with the `Authorization`
  header. The worker checks the token again.

## Usage stats

The usage stats are off unless `BWTIDY_STATS=1`.
[Configuration](reference/configuration.md#usage-stats) has the variables and
what is kept as well as the command that shows it. The reason is that a
public demo needs to know whether it is used and what fails. It also needs to
know when it was full, and an operator may want the same of their own
install. With the stats
off nothing changes. There is no file and no table and no work per request.

The gateway records the stats and a worker never does. Each worker runs as
its account's uid and cannot write a file the gateway owns. Only the gateway
sees the requests of every account and the workers' cap.
`GatewaySettings.stats` and `GatewaySettings.stats_db` hold the two
variables. Nothing leaves the machine because bwtidy never sends the stats
anywhere.

Only what comes from the code is kept. That is the route templates and the
MCP tool and method names as well as the exception types and code locations.
Nothing that a person typed or a vault holds is kept, and none of these is
ever in the file:

- item names, values and ids
- folder and field names
- search text and request bodies
- JSON-RPC ids and arguments
- exception messages and arguments
- IP addresses and user agents

A unit test sends markers in all of these and finds none in the file.

- An event is one request that reaches a worker. It holds the UTC time and
  the account's id as well as the channel (`ui` or `mcp`) and the operation.
  It also holds ok with the HTTP status and the duration in milliseconds
  along with the gateway's version. A request the gateway answers itself is
  no event. Examples are `/api/health` and the account form as well as a
  refused MCP token and a 401 without a routing cookie.
- A UI operation is `METHOD TEMPLATE` with the template of the first worker
  route that takes the request (`PUT /api/items/{item_id}`). It never holds
  the path. A path that no route takes is `other`.
- An MCP `POST /mcp` counts each JSON-RPC message of its body, and a batch
  counts each call. A `tools/call` counts by its `params.name` and any other
  message by its method. Each counts so only when the worker registers that
  tool or its MCP SDK knows that method, and else it is `other`. `GET /mcp`
  and `DELETE /mcp` count as themselves. A body that is no JSON or is over 2
  MiB is one `other`.
- ok means a status of 2xx or 3xx. For an MCP call with an id it also means
  an answer with a result that is no tool error (`isError`) and no `error`
  object. An answer over 2 MiB is not read, so its calls count by the status
  alone. The bodies go on unchanged and a streamed answer keeps streaming,
  because the gateway reads a copy.
- The account id is the first 16 hex characters of the HMAC-SHA256 of the
  account key (server and email). The HMAC's key is a random salt that the
  file makes at its first open. So the same account counts as one without its
  email. `create_gateway_app(stats_account=...)` lets a wrapper of the gateway
  (the demo) store its own label instead.
- An exception that a worker did not handle reaches the gateway only as a 500.
  A worker that the gateway started with the stats on (it sets
  `BWTIDY_STATS_REPORT=1`) adds the header `X-Bwtidy-Stats-Error` to that 500.
  The header holds the exception's type and its frames inside the bwtidy
  package. A frame is its path relative to the package with the function and
  the line. The header never holds the exception's text, arguments or locals
  and never a log line.
- The gateway strips that header from every answer whether the stats are on
  or off. It keeps an error only when the header holds nothing else. The
  error is kept under a fingerprint, the sha256 of the type and the frames.
  With it go its first and last time and its count as well as the channel
  and operation of its event. A 500 that a route answers itself (a failed
  backup or a write that failed midway) is a failed event without an error.
- The gateway asks a worker once for `GET /stats/names`, a path it never
  proxies. The answer holds the templates of the worker's `/api/` routes and
  its tool names as well as its MCP SDK's client methods. Every worker runs
  the same code, so the first one that answers serves all. Until then every
  operation is `other`.
- Every 60 seconds the gateway takes a capacity sample of the workers that
  run or start and of `BWTIDY_MAX_WORKERS`. The sample also counts the starts
  refused with 429 `Too many accounts are open at once. Try again later.`
  since the sample before. `create_gateway_app(stats_extra=...)` gives a
  wrapper's own numbers as JSON in the sample's `extra`. A wrapper that counts
  sessions gives the sessions started since the last sample as `sessions`.
  The summary adds them up.
- A request only notes itself in RAM. At most every 5 seconds the notes
  become rows, written in one transaction in a thread. A write that fails
  drops its rows and logs one line until a write succeeds again. That line
  holds the error's type and SQLite's text but no request data. A request
  never waits for a write or fails with it. Past 10000 notes between two
  writes the rest are dropped.
- At the gateway's start and then every day the events of the days before
  the last 200 are counted into `daily` and deleted. A row of `daily` counts
  the events of one day with the same channel, operation and ok. The capacity
  samples of those days are deleted too. Only whole days roll up, so a day is
  either in the events or in `daily`. `daily` and `errors` are kept for good.
  An error keeps its count and its last time.
- The file is `BWTIDY_STATS_DB` or else `stats.db` in the parent of
  `BWTIDY_ACCOUNTS_DIR` (`/data/stats.db`). `compose.yaml` sets
  `/stats/stats.db` on the volume `bwtidy-stats`, because its root file system
  is read-only and `/data` lies on it. The gateway makes it in WAL mode
  with mode 0600 and its own uid as the owner. It must lie outside the
  accounts directory and the backups root, whose directories the account uids
  own. A link stops the start, and so does a file that is not regular or that
  another uid owns.
- The summary (`summary` in `stats/summary.py`) is the one plain dict that a
  page or `bwtidy stats` shows. It holds the totals of the accounts and of
  the UI and MCP operations with how many were ok and failed. The totals
  include the sessions when a wrapper gives them. Each operation's ok and
  failed per channel follow and then the errors by operation and type. The
  capacity per hour comes last. For the days whose events were rolled up the
  summary reads `daily`, which counts no accounts. It never answers an
  account id or a frame or a fingerprint. The command adds the top errors
  with their frames for the operator where the gateway runs.

## Schema versions

- Each account's `app/bwtidy.sqlite` holds its schema version in SQLite's
  `PRAGMA user_version`. The schema has one version, which is 1. It is
  `SCHEMA` in `db.py`. Its tables are `change_log` and `queue` along with
  `settings` and `backup_fingerprints`. `change_log` has the index
  `change_log_at` on `change_log (at)`, which the Change log's filters sort
  and filter by.
- A new file (version 0 without tables) gets the whole schema in one
  transaction and is set to version 1.
- A worker opens only a file at version 1. Any other version fails its start
  with a log line that names the file and its version as well as version 1.
  Nothing in such a file changes. The browser sees that the worker could not
  start, as it does for any other reason.
- The gateway's `stats.db` (see Usage stats) has versions of its own in
  `PRAGMA user_version`. `MIGRATIONS` in `stats/db.py` moves them up. It is
  an ordered list of numbered steps and each step is a tuple of SQL
  statements. Opening the file runs every step above its version, each in one
  transaction that also sets its version. A file at a newer version than the
  code knows stops the gateway's start. A log line then names the file and
  its version as well as the newest version the code knows. Nothing in the
  file changes.
- Version 1 of `stats.db` (step 1) makes the tables `events` and `capacity`
  with their indexes `events_at` and `capacity_at`. It also makes `errors`
  and `daily` as well as `settings`, which holds the salt. The reason for the
  steps is that the rolled-up days and the errors are kept for good. So a
  release that keeps a new number migrates the file instead of starting it
  over. The next step is one more tuple in the list.

## Session and access

[Logging in](explanation/login.md) and [The web
UI](reference/ui.md#login-pages) show what a user sees of the login, the
session and its end.

- `POST /api/session` takes the master password and unlocks `bw serve`
  (`POST /unlock`). The password and the session key stay in RAM only. They
  never reach the disk or the logs. The login pages' password field keeps its
  name and its `current-password` autocomplete, so password managers fill it.
  Only these pages invite them (see Grid, Password managers).
- Once the vault is unlocked and the session started, the unlock's side
  effects follow. They are the background backup, the CLI's login state and
  the deletion of `.pending`. They never fail the unlock. A failure is logged
  with its type and the login still answers with the cookie.
- The session key is `data.raw` of the unlock response, and backups pass it to
  the CLI. Every unlock (`bw serve` or `bw unlock`) and every `POST /lock`
  invalidate the keys handed out earlier. So nothing else may unlock or lock
  `bw` in the container while a session runs.
- The session is a cookie `bwtidy_session` that holds 32 random bytes. It is
  HttpOnly with SameSite=Strict and Path=/. It has no Max-Age and is Secure
  with `BWTIDY_SECURE_COOKIES=1`. One session runs at a time. A new login
  replaces the old one, but only with the running session's master password:
    - While a session runs, `POST /api/session` (and `POST
      /api/bw-login/finish`) first checks the password against a hash of the
      running session's password. The hash is scrypt with N 2^17, r 8 and p 1
      and a random 16 byte salt made at the login. It runs in a thread outside
      the session's lock and is compared with `hmac.compare_digest`. A wrong
      password answers 401 `Wrong master password` and changes nothing. There
      is no commit and no lock. The session and its cookie stay. Without a
      running session the unlock of `bw serve` checks the password.
    - After 3 wrong passwords in a row against a running session each further
      try is refused with 429 `Too many wrong passwords. Try again in N s.` and
      `Retry-After: N`. That holds for the right password too until N seconds
      after the last wrong one. N starts at 1 and doubles up to 60. A right
      password and every new session start the count again. The count is per
      worker and not per address because the gateway and the proxy in front of
      it hide the address. Checks run one at a time, so parallel guesses cannot
      pass the wait together.
    - The reason is that anyone who reaches the gateway and knows an account's
      server and email gets a routing cookie for it. Only the logged in browser
      (with its session cookie) and an MCP client with the account's token may
      end a session or log the CLI out. The hash is slow so that the check is no
      fast oracle for guessing the master password. It is a hash of its own
      because every unlock of `bw serve` invalidates the running session's key.
    - Two things are accepted. A refusal with a session running takes the
      hash's time and one without takes the unlock time of `bw serve`, so the
      timing says whether a session runs. And when the master password changed
      on the server during a session, a login with the new one is refused until
      the session ends.
- Every open tab sends `POST /api/session/heartbeat` every 30 seconds. The
  session ends when none came for `BWTIDY_HEARTBEAT_TIMEOUT` seconds. The
  default is 120, and less than 60 is refused as it must stay above the 30.
  Closing the tabs should log the user out. Basic auth cannot do that because
  browsers keep its credentials until they quit.
- `BWTIDY_SESSION_IDLE_TIMEOUT` seconds (default 7200) without user activity
  end the session too. Heartbeats do not count as activity. Nor do requests
  that the frontend makes on a timer, which carry the header
  `X-Bwtidy-Background: 1`. Every other API request counts.
- A session ends on a timeout, a logout or a new login with the right
  password. It also ends when the process exits and on MCP's `end_session` or
  `log_out_of_bitwarden`. Ending a session takes these steps:
    - It commits the pending `ui` entries (see MCP and the queue).
    - It stops a running background backup before its next step.
    - It locks `bw serve`.
    - It wipes the password and the session key from RAM.
    - It disables MCP.

  A worker with `BWTIDY_EXIT_AFTER_SESSION=1` then exits, except when a new
  login replaced the session. The end never waits long on `bw` (see Worker
  mode, Bounded stops).
- MCP works only while a logged in tab is open, on purpose.
- When `bw serve` exits the backend starts it again and unlocks it with the
  password from RAM, so a running session survives. If that unlock fails the
  session ends.
- The CLI must be logged in before anything can be unlocked. `bw serve` does
  not even start while it is not. So `GET /api/session` always carries
  `bwLogin` with `loggedIn`, `server` and `email`. The frontend shows a
  Bitwarden login instead of the unlock while `loggedIn` is false.
    - `loggedIn` comes from the status of `bw serve` when it answers and knows
      a server. Otherwise it comes from `bw status`.
    - `server` and `email` are what the CLI reports. Otherwise they are
      `https://vault.bitwarden.com` and an empty email.
    - While the account's worker does not run the page asks no worker.
      `loggedIn` is then the gateway's, and the server and email are the
      account's.
- bwtidy saves no server or email. The Bitwarden login form prefills them
  from the browser's localStorage. It takes the last values typed
  (`bwtidy.accountForm`) or else the account in use (`bwtidy.account`), and
  otherwise what the session sends. A worker's fixed account always comes
  from the session. Log in stays disabled while either is empty, because `bw
  login` would wait for an email. The master password of the form is used for
  both the CLI login and the unlock.
- `POST /api/bw-login` takes the `server`, the `email` and the `password`. It
  runs `bw config server SERVER` when the server differs from the CLI's. Then
  it runs `bw login EMAIL --passwordenv BWTIDY_LOGIN_PASSWORD`. Both run in a
  pseudo-terminal so that the CLI asks its questions the way it does at a
  terminal. On the first login from a new device bitwarden.com mails a
  one-time code, for example. The password reaches the CLI only through the
  child's environment and stays in RAM for this one login.
- The answer is a `loginId` that every later call carries in the header
  `X-Bwtidy-Login`. It is 409 while a login runs or when the CLI is logged in
  already, and 404 for an unknown token. None of these routes answers 401
  because the frontend reads that as "locked".
- `bw serve` is stopped (with its restarts) before `bw config server`. It is
  started again on a cancel and when the CLI has exited with any exit code. A
  `bw serve` that starts while `bw login` runs reads the logged out state and
  writes it back. That drops the fresh login about one time in three.
- `GET /api/bw-login/output?after=N` feeds the console. It answers the `text`
  from offset N on along with `next`, `running` and `exitCode`. The CLI's
  prompts redraw their line for every typed character. That is applied before
  a line is shown, so each prompt appears once with its answer.
- The master password and the session key are replaced by eight bullets
  before anything is stored. The key is in the `BW_SESSION="..."` lines and
  after `--session`. Nothing that the CLI prints and nothing typed into it is
  ever logged.
- `POST /api/bw-login/input` with `text` writes that line to the CLI. With
  `password: true` it writes the kept password instead and masks its echo. It
  answers 409 when nothing runs. `DELETE /api/bw-login` kills the CLI and
  forgets the password and the token.
- `POST /api/bw-login/finish` works only after exit code 0. It starts `bw
  serve` again when that is not answering. Then it unlocks exactly like `POST
  /api/session` and forgets the password and the token. Both are also dropped
  when the CLI exits with another code (once that exit was reported) and ten
  minutes after the start.

```mermaid alt="The browser posts the server, the email and the master password to the worker. The worker stops bw serve, runs bw config server when the server differs and then bw login in a pseudo-terminal, and answers with a login id that later calls carry. The browser reads the console output from an offset and sends the lines the user types until the CLI exits. The worker then starts bw serve again. After exit code 0 the browser posts the finish, and the worker starts bw serve when it does not answer, unlocks it and answers with the session cookie."
sequenceDiagram
  participant B as Browser
  participant W as Worker
  participant S as bw serve
  participant C as bw CLI
  B->>W: POST /api/bw-login
  W->>S: stop
  W->>C: bw config server (when it differs), bw login
  W-->>B: loginId
  Note over B,W: Every later call carries X-Bwtidy-Login
  loop until the CLI exits
    B->>W: GET /api/bw-login/output?after=N
    W-->>B: text, next, running, exitCode
    B->>W: POST /api/bw-login/input
    W->>C: the typed line or the kept password
  end
  W->>S: start again
  B->>W: POST /api/bw-login/finish (after exit code 0)
  W->>S: start when it does not answer, then unlock
  W-->>B: session cookie
```

- `POST /api/bw-logout` needs the session cookie. Without a valid one it
  answers 401 `Not logged in` and nothing is logged out. It also needs the
  header `X-Bwtidy-Request: 1` and answers 403 without it. A page on another
  site cannot send a custom header without a CORS preflight, which bwtidy
  never answers.
- The logout ends the session like `DELETE /api/session` and stops `bw
  serve`. A `bw serve` that sees the CLI log out stays up but hangs on the
  next unlock. Then the logout runs `bw logout` and starts `bw serve` again,
  which keeps retrying until the next login. MCP's `log_out_of_bitwarden` runs
  the same steps.
- Only the logged in browser or the MCP token logs the CLI out. When the
  session ended behind the page, `POST /api/bw-logout` answers 401 or a
  worker on its way out answers 502 even after the retry. Then the CLI stays
  logged in and the page asks for the unlock first. The choice sits inside
  the one Log out dialog. So there is no second button or menu entry for
  logging out of Bitwarden.

### The account form

Behind the gateway the page first needs the browser's account (see Gateway).

- Before anything else the frontend asks `GET /gateway/account`. With no
  account it shows the account form, whose Continue posts `/gateway/account`.
  A 404 `{exists: false}` offers to create the account with a second post
  that has `create: true`. A 422 shows its reason under the form. A backend
  without the gateway answers 404 to the `GET`, and the frontend goes
  straight to the session.
- Neither the form nor the page starts a worker. With an account the page
  shows the Bitwarden login or the unlock. It goes by `GET /api/session` while
  the account's worker runs (`running`) and by the gateway's `loggedIn`
  otherwise.
- The first-run notice ("Before you start", see [Login
  pages](reference/ui.md#login-pages)) shows while `firstRun` is true. It is
  an info note with the role `note` rather than an alert, as nothing went
  wrong. It does not block the form. The page asks the gateway again whenever
  a login page shows.
- Nothing stores the first run, since the gateway derives it from the account
  directories. An account counts once it has its SQLite file and no
  `.pending`. The file alone is not enough. The worker makes it at its start
  before the login, so a failed first login leaves one in its pending
  directory. This way a new user meets the beta caution before the first
  login and an existing one never sees it again. No new stored data is needed
  for it. The notice's two URLs are in `frontend/src/about/links.ts`.
- After a failed unlock or login the page asks the gateway again, since the
  click may have started the worker. An unlock that failed for a reason other
  than the password asks for the session again. So a CLI that turned out to
  be logged out shows the Bitwarden login.
- The account in use is remembered in localStorage. The gateway may not know
  the browser's account because it restarted or the browser was closed. Then
  the app posts `/gateway/account` with the remembered account by itself and
  shows the unlock, so a returning user types only the master password. A 404
  then forgets the account and shows the form.
- The worker exits with its session. So after a Log out the app opens the
  account again before it shows the unlock or the Bitwarden login. A 401 `No
  account` from the gateway on any request opens it again too. Three requests
  are tried once more after such a reopen when they found no worker (401 `No
  account` or 502 from a worker on its way out). They are the unlock, the
  start of a Bitwarden login and the log out of Bitwarden. Nothing starts a
  worker for the log out of Bitwarden.
- Switch account ends the session with `DELETE /api/session`, which commits
  the unsaved edits first. It then calls `DELETE /gateway/account`, forgets
  the remembered account and shows the account form. A reload then stays on
  the form.
- The app bar shows the build commit after the title (see
  [About](reference/ui.md#about)). The build is then in every screenshot,
  where the Version line of the About page alone is easy to miss. It uses the
  bar's own text colour at a low opacity rather than a fixed colour. So it
  follows the color mode and MCP mode.
- The other tabs of the browser profile follow. All tabs share the routing
  cookie, so a tab that kept the old account would send its ids to the new
  account's worker. A queue approval of entry N would approve the other
  account's entry N, and a templates save would replace the other account's
  templates.
- So a tab reloads when another tab changes the remembered account. It also
  reloads when another tab logs out, since Log out writes the time under
  `bwtidy.loggedOut` for the others. Accounts are compared by server and
  email, and a write of the same account reloads nothing.
- A tab that finds its worker gone opens its account again only while the
  remembered account is still its own. Otherwise it reloads. So it cannot
  route the shared cookie back to the old account in the middle of another
  tab's switch.

## Coordinates

Every value in the grid has a coordinate, which is also its column name.

- Built-in leaves use their JSON path. These are all of them.

  | Group | Coordinates |
  | --- | --- |
  | Every item | `name` `notes` |
  | Login | `login.username` `login.password` `login.totp` |
  | Card | `card.cardholderName` `card.brand` `card.number` `card.expMonth` `card.expYear` `card.code` |
  | Identity | `identity.<key>` with the key `title` `firstName` `middleName` `lastName` `address1` `address2` `address3` `city` `state` `postalCode` `country` `company` `email` `phone` `ssn` `username` `passportNumber` or `licenseNumber` |
  | SSH key | `sshKey.privateKey` `sshKey.publicKey` `sshKey.keyFingerprint` |

- `login.uris` is one multi-value column. It holds the list of `uri` strings
  and keeps each one's `match` setting. The grid and the search see the
  strings only, and so does every operation but `setUris`. MCP shows each URI
  with its match. Bitwarden's `match` takes the values below. MCP and the
  queue's diffs show a match by its word.

  | `match` | Bitwarden's setting | Its word |
  | --- | --- | --- |
  | 0 | domain | `domain` |
  | 1 | host | `host` |
  | 2 | startsWith | `startsWith` |
  | 3 | exact | `exact` |
  | 4 | regularExpression | `regex` |
  | 5 | never | `never` |
  | null | the client's default | null |

  The client's default is a setting of the browser extension. It is not in
  the vault and bwtidy never tries to read it.
- The organizational columns are `type`, `folder` (the folder name) and
  `organization`. `organization` holds the org name and is empty for a
  personal item. `collections` is a
  multi-value column of names. `favorite` and `reprompt` are organizational
  too.
- A custom field is `fields.<name>` with the name verbatim. Spaces, dots and
  trailing spaces are kept. Field names may contain dots, so a coordinate is
  never parsed by splitting on dots.
- Internally a coordinate is structured, such as `{"kind": "path", "path":
  "login.username"}` or `{"kind": "field", "name": "BIOS Password"}`. When one
  item has the same field name twice, the second is `fields.<name>#2`. A
  field name that itself ends in `#<digits>` always carries its occurrence
  (`fields.key#2#1`). So the display string parses back to the same
  coordinate.
- The read-only info columns are `attachments` and `passkeys` as well as
  `revisionDate`. `attachments` holds the file names in the item's order. The
  search finds them, but they are not a value that a merge can move.
  `passkeys` is the count of `login.fido2Credentials`. A file name is not
  secret, yet logs and backup errors still name an attachment by its id only.
- Some parts of an item are never shown or written as leaves. They survive
  every write untouched (see Writes). The grid row carries each attachment's
  id with its name for a rename, but no cell shows it. These are the parts:
    - `id`
    - `object`
    - `creationDate`
    - `deletedDate`
    - `passwordHistory`
    - `login.passwordRevisionDate`
    - the content of `login.fido2Credentials`
    - the attachment metadata other than the file names, above all the url that
      holds a token

## Secret values

These are the secret coordinates:

- `login.password`
- `login.totp`
- `card.number`
- `card.code`
- `sshKey.privateKey`
- every custom field with `type` 1 (hidden)

[Secret values and backups](explanation/secrets.md) shows what a user sees of
them.

- A revealed value is fetched one cell at a time (`GET
  /api/items/{id}/secret?column=...`). It is answered with `Cache-Control:
  no-store` and never cached by the frontend. Its row may have left the view
  or changed item or column, or the row was hidden. An answer that arrives
  after that is dropped and never shown.
- A column is secret when any item holds a hidden field of that name. So a
  cell in it can be a text field on its own item. That value is no secret
  because it is plain in `GET /api/grid` and MCP reads it. The grid shows it
  plain with a mark, and the secret route answers it 404. The user sees a
  mask only on a value that MCP cannot read.
- MCP never returns a secret value, not even in an error message.
- No operation may move a secret value into a non-secret coordinate. That is
  `name` or `notes`, a text custom field or any built-in non-secret leaf. Such
  an operation is rejected with a clear error, because the agent could read
  the value afterwards. An agent's proposal is the exception. It is refused
  silently (see MCP and the queue) so that the refusal is no oracle.
- `name` and `notes` are not secret, and neither are `login.uris` and text
  custom fields. If a
  note holds a secret the user moves it to a hidden field.

### A plain value never equals a secret one

A non-secret value of an item never equals one of its secret values, in
either order. A plain value equal to a secret value of the item is refused.
So is a secret value equal to one of the item's plain values. Without the rule
two grid edits could make a login's username its password. That would leave a
plain copy in the grid, in MCP answers and in SQLite.

- The plain values are `name`, the folder name and `notes`. A non-secret
  built-in leaf, an entry of `login.uris` and a text custom field are plain
  values too.
- The type and the organization do not count, and neither do the collections
  and the info columns. Every value counts however short it is. Only an empty
  one does not.
- The rule holds across the entries of one commit. It also holds against the
  queue's pending entries of the item, whose hidden values are decrypted in
  RAM for the comparison only. It holds again at write time on the item as
  `bw serve` has it then, which also catches a generated value.
- The refusal names both columns and never a value.
- Collisions already in the vault stay. Only a write of one of the two equal
  values is refused.
- A folder's new name (from `renameFolder` or its restore) becomes the folder
  of every item in it, those in the trash too. So it is checked against the
  secret values of each of them.

### Where secret values never go

- Secret values never reach logs, the SQLite file or test output. A queued UI
  edit of a secret column is in SQLite only as an age ciphertext. So is a
  queued new item's secret value (see MCP and the queue, Hidden values).
- A refused request body is never echoed, since it can hold the master
  password or a one-time code. A validation error names the field and the
  problem only. It holds just its `type`, `loc` and `msg`. That holds for the
  FastAPI validation of the worker and of the gateway alike. Both leave out its
  `input` and `ctx`.
- The `msg` is pydantic's own only for the error types whose text never
  quotes the input. A tag error is rebuilt from the expected tags without the
  tag. Our own validators raise an error of type `bwtidy` whose text we wrote.
  Every other type gets one fixed text. The hand-parsed sections follow the
  same rule (the operations, the templates and the generator settings).

### Values nobody types

- A person who creates an item may type its secret values (`secretValues` of
  `createItem`, see Writes). MCP never gives one. A `login.password` or a
  hidden field can instead get a value from `bw`'s generator at write time.
  Nobody sees that value until the user reveals it in the grid. For MCP a
  secret is blank or generated and never a value it gives or can read.
- There are no placeholder values. The secret fields left empty go on the
  to-fill list (see The tool note), so nothing is forgotten.
- `generateLeaf` (see Writes) replaces a value with one from `bw`'s
  generator. The value goes from the answer of `bw serve` into the item and
  nowhere else. The queue and the change log show `(generated)`.
- An example of the generator (`POST /api/generator/example`) is no value of
  an item. It is answered to the dialog that asked and is never stored or
  logged. The frontend keeps it in component state only.

## Writes

[How edits are saved](explanation/edits.md) describes the steps of a write as
a user meets them. These are the rules behind them:

- Every write goes through one serialized write path in the backend, one at
  a time.
- Every UI write but two is a `ui` entry of the queue (see MCP and the
  queue). The two are the tool note's rename back and keeping its new name,
  which go straight to the write path (see The tool note). `POST
  /api/operations` is for scripts and tests.
- Every operation has a full backup that matches the vault as it is right
  before the write (see Backups). An operation may touch many items and gets
  one backup rather than one per item. Without a backup there is no write.
- An item is written by a `GET` of the full item and a `PUT` of the full
  object with only the touched leaves changed. Unknown fields must survive.
  They include passkeys, password history and attachments as well as fields
  that future Bitwarden versions add.
- Changing an item's type is never a `PUT` with a new `type`. Instead bwtidy
  creates a new item of the new type with the mapped values. It copies the
  folder, the organization and the collections as well as favorite and
  reprompt. It uploads the attachments again and then moves the old item to
  the trash. A type change in place is not documented to work, while this
  path works on every server.
- Deletes only move an item to the trash. A permanent delete is never
  offered. The one exception is the old attachment of a `renameAttachment`
  (below). It is deleted once its copy under the new name is listed, since
  Bitwarden has no trash for an attachment and the write's backup holds the
  file. An empty folder's `deleteFolder` deletes the folder too, as it holds
  no item.

### Conflicts

Other people and the user's other devices edit the vault too. So every
operation checks the items it writes several times before it changes them.

```mermaid alt="A batch syncs and checks the items it writes against what each operation was based on. Then it takes the backup, syncs again and checks again when the vault changed. Each operation syncs once more before its first item when the last sync is older than 5 seconds. Each item that has a revisionDate to check is fetched and checked right before its write. A change made elsewhere that a check finds fails that operation as a conflict, and so does a server that refuses the write as out of date. bwtidy's own earlier writes pass."
flowchart TD
  sync[Sync and reload the affected items] --> check[Check each revisionDate and shown value]
  check -- changed elsewhere --> conflict([Conflict])
  check --> backup[Take the backup]
  backup --> again[Sync and reload again]
  again -- vault changed --> recheck[Check again]
  recheck -- changed elsewhere --> conflict
  recheck --> op
  again -- vault the same --> op[Next operation]
  op -- last sync over 5 s ago --> late[Sync]
  late --> get
  op --> get[GET the next item and check it right before its write]
  get -- changed elsewhere --> conflict
  get --> put[Write the item]
  put -- server says out of date --> conflict
  put -- more items --> get
  put -- more operations --> op
  put -- all written --> done([Written])
```

- Before every operation bwtidy runs `POST /sync` and reloads the affected
  items. It aborts if an item's `revisionDate` differs from the one the
  operation was based on.
- Two `revisionDate`s are compared as instants and not as strings
  (`revision.py`). At most 1 ms apart is the same revision. A value that is
  no timestamp (none or empty) equals only itself.
    - The server keeps more digits than the 3 of the answers of `bw serve`. It
      is 7 on bitwarden.com and 6 on Vaultwarden. So one instant can reach
      bwtidy in two spellings (cut and rounded), and a merge into an item that
      nobody had changed would fail as a conflict. Every write gives an item a
      new `revisionDate`, and no two writes of one item are 1 ms apart.
    - bwtidy also keeps the spelling that `bw serve` answered first for each
      item's revision and answers with that one. It keeps it in RAM with ids
      and `revisionDate`s only. So the grid, MCP and a backup's fingerprint see
      one spelling per revision.
- A folder, favorite or collection change made elsewhere is a conflict too.
  The Bitwarden clients change an item's folder and favorite flag through a
  partial update without a new `revisionDate`. Vaultwarden does the same for
  its collections. So the `revisionDate` alone does not show the change. A
  `moveToFolder`, a `setLeaf` of `favorite` or a `setCollections` would then
  overwrite the other device's choice without a word.
- These operations therefore carry what the grid showed as optional fields:
    - `shownFolderIds` is `{itemId: folderId or null}` for the items of
      `itemIds` only.
    - `shownFavorite` is for a `setLeaf` of `favorite` only.
    - `shownCollectionIds` is compared as a set.

  The check is a conflict when the item now differs, unless bwtidy's own
  writes since then turned the shown value into the one now (see Own writes
  below).
- The conflict is the item with its expected and actual `revisionDate`
  (which may be the same) and its `column` (`folder`, `favorite` or
  `collections`). In the queue it fails only the entry that carried the shown
  value. Another entry of the same item is tried again.
- The grid row has `folderId` and `collectionIds` for this. For a
  `renameAttachment` it has `attachments` with one `{id, fileName}` per
  attachment (never the url or the size). The grid sends all three shown
  values. `propose_move_to_folder` and `propose_set_collections` send them as
  the agent's view held them, so a change made elsewhere before the user
  approves fails the entry. An operation without these fields (such as a
  script's) is checked by its `revisionDate`s only.
- After the write's backup bwtidy always syncs once more and reloads. It runs
  the check again when the vault is not the one the check saw. A fresh backup
  takes long, and a change that another client made in that time would be
  overwritten with no conflict. A change to other items passes.
- Right before each item is written its `revisionDate` in the answer of `bw
  serve` to the `GET` is checked again. So are the shown values. This holds
  for every kind that writes an existing item, `trashItem` and the steps of
  `restoreItems` included.
    - A sync inside the batch can bring in another client's change after the
      check. Such a sync comes after a create, after a restore's move or
      untrash or with a backup by hand.
    - A mismatch fails that operation as a conflict. The operations before it
      stay written and the ones after it are not applied.
    - An item without a `revisionDate` to check is not checked. That is a new
      item, the tool note of a section save or an item in the trash.
    - A `GET` may answer a `revisionDate` that one of bwtidy's own writes turned
      into a later one. Then it is asked again every 0.2 seconds for up to 5
      seconds. Right after its own `PUT`, `bw serve` answers a `GET` of that
      item for a moment as it was before the `PUT`. The batch's next write of
      the item would then conflict with its own earlier write. One that is
      still from before the own write after the wait fails as a conflict.
- The server refuses a write on a copy of the item older than its own, and
  bwtidy shows that as a conflict of the item. `bw` sends its copy's
  `revisionDate` as `lastKnownRevisionDate`. Both servers answer the refusal
  with a 400:
    - Vaultwarden refuses the edit when its stored date is newer by 2 seconds or
      more ("The client copy of this cipher is out of date").
    - The official server refuses it when the dates differ by more than a
      second ("The item cannot be saved because it is out of date").

  bwtidy knows the answer by those texts. It syncs and fails the operation as
  a conflict like a mismatch right before the write. Any other refusal fails
  the write with `bw`'s text. An integration test with a second CLI login as
  another device proves it against Vaultwarden.
- The server does not check every change. A partial update of the folder or
  favorite flag passes on a stale copy. So do the collections and the trash
  with its restore. On Vaultwarden an attachment passes too. Vaultwarden also
  lets an edit through less than 2 seconds after the item's last one. Whether
  `bw` sends the date on a move into an organization is not verified. For
  those cases only the checks above stand.
- An operation syncs before it reads the first item it writes when the
  batch's last sync (any of them) started more than 5 seconds ago. So the
  check right before the write sees what the server holds now. Every own
  write bumps the account's revision, so the next sync is always a full one.
  A sync before every operation would make a commit of many queued edits
  several times slower, hence the 5 seconds.
- A partial change made elsewhere after the last sync and before the write is
  not covered and is overwritten. The window is up to 5 seconds plus the
  operation's own writes. There is no check after the batch. By then the
  server holds bwtidy's values and the other client's change cannot be told
  from them.
- The sync is `POST /sync` of `bw serve` without `force`. `bw` first asks the
  server's revision date and skips the full sync when it is not newer than
  its last sync. That last sync time is the client's own clock, so a host
  clock ahead of the server's can skip a change. bwtidy does not force anyway.
  With `force` every sync is a full one, so a sync with nothing changed would
  cost about as much as one after an own write.
- Own writes are remembered. For each item it wrote the write path keeps the
  `revisionDate` before and after its write. The "after" comes from the
  answer of `bw serve` to that very `PUT`, and both stay in RAM until the
  session ends.
    - An operation's expected `revisionDate` may be a "before" of such a write
      (in any spelling of its instant). Then it is checked against the "after"
      instead, following the chain. So an edit made while a commit runs never
      conflicts with bwtidy's own commit, and neither does one right after it
      before the grid reloaded. Any other change still conflicts.
    - Nothing that bwtidy did not write itself ever goes into the chain.
    - The same writes remember the folder, the favorite flag and the
      collections that the write found on the item and the ones it left. A
      shown value holds when a run of them leads from it to the value now.

### The change log

- Every applied operation is logged in SQLite with these fields:
    - the time
    - the source, which is `ui` or `mcp` or else `mixed` for a batch with
      entries from both
    - the operation
    - the ids of the affected items and of the items it created
    - the coordinates it wrote
    - the result
    - the backup's file name

  Values and error messages are never logged, since either could carry a
  value.
- `createdItemIds` (the table column `created_item_ids`, a JSON list) holds
  the new item of a `convertItem` and of an `applyTemplate` across types. It
  also holds the new item of a `moveToPersonal` and of a `createItem`, and of
  a `restoreItems` that created an item anew. A restore of the row must find
  them.
- A row of a folder kind (`renameFolder`, `deleteFolder` or `restoreFolder`)
  has no item ids. It names its folder in `folderId` (the column `folder_id`,
  null on every other row). For a restore that created the folder again that
  is the new one.
- A `renameAttachment` row has `attachmentIds` (the column `attachment_ids`,
  null on every other row). It is `{"before": id, "after": id}`, with `after`
  null when the write failed before a copy was kept.
- A restore's row also has `restoredBackup` and `restoredChangeId` (see
  Restoring a change log row).
- `columns` (a JSON list) is named like the grid's columns. The read-only
  columns are left out. These are the columns each kind logs.

  | Kind | Its `columns` |
  | --- | --- |
  | `setLeaf`, `removeLeaf` and `generateLeaf` | the `column` |
  | `moveToFolder` and the folder kinds | `folder` |
  | `setCollections` | `collections` |
  | `moveToOrganization` and `moveToPersonal` | `organization` and `collections` |
  | `renameAttachment` | `attachments` |
  | `renameField` | both names |
  | `mergeColumns` | the sources and the target |
  | `mergeItems` | the target columns |
  | `setUris` | `login.uris` |
  | `convertItem` and `applyTemplate` | each mapped source and target, every coordinate the plan changes on the item as the write found it and `sshKey.publicKey` and `sshKey.keyFingerprint` when the write derived them |
  | `trashItem`, `createItem`, `restoreItems` and `setToolNoteSection` | none |

  Those four kinds log no column because they work on whole items.

- A custom field's name is vault content, and the log holds it anyway.
  Knowing what each change touched is worth that. The names are already plain
  in the grid, in MCP answers and in the queue's diffs.
- `GET /api/change-log` answers the newest 200 rows that match as a JSON list
  with the newest first. The header `X-Bwtidy-More` is `true` when more rows
  matched and `false` otherwise. The filter runs in SQL over the whole log.
  The optional query parameters are combined with AND:
    - `from` and `to` are UTC instants and inclusive. A fraction of a second of
      `from` counts as the next whole second.
    - `source` and `kind` match their fields and can be repeated.
    - `column` matches a row when its `columns` hold any of the given ones. It
      can be repeated.
    - `item` matches a row's `itemIds` or `createdItemIds`. It can be repeated.
    - `result` is `ok` or `failed`.

  An unknown
  `source` or `result` is a 422, and so is an instant that does not parse.

### Operations

`POST /api/operations` takes `{"operations": [...]}`. The whole list is one
operation in the sense above. It gets one sync and one check of every touched
item, then one backup and one more sync. Then come the writes one by one.

- When an item changed or vanished the answer is 409 with the conflicting
  items. Each has its expected and actual `revisionDate` and the `column` of
  a shown value. An operation that is not allowed gets 422, and a failed
  backup gets 500 that names the step. Nothing is written in those cases.
- A write may fail midway. Then the response says which operations were
  applied, which failed and which are pending. It names the backup too and
  holds `conflicts`, and the vault is reloaded. `conflicts` holds the item
  that the failed operation found changed right before its write or that the
  server refused as out of date. Otherwise it is empty.
- These are the kinds:
    - `setLeaf` and `removeLeaf`
    - `moveToFolder`, which creates a missing folder (an empty name means no
      folder)
    - `setCollections` for organization items only
    - `moveToOrganization` (`POST /move/{itemId}/{organizationId}`) for
      personal items only
    - `moveToPersonal`
    - `trashItem`
    - `convertItem`
    - `applyTemplate` (see Templates)
    - `renameField`, `mergeColumns` and `mergeItems` (see Normalizing)
    - `setUris`
    - `restoreItems` and `restoreFolder` (see Restoring a change log row)
    - `createItem`
    - `generateLeaf`
    - `renameFolder` and `deleteFolder`
    - `renameAttachment`
    - `setToolNoteSection` for bwtidy's own note only

  No other operation may touch the tool note.

#### `renameFolder` and `deleteFolder`

`renameFolder` (`folderId`, `shownName` and `newName`) renames a folder with
`PUT /object/folder/{id}`. `deleteFolder` (`folderId` and `shownName`)
deletes an empty one. In Bitwarden itself renaming a folder means moving
every item and deleting the old folder by hand. Neither operation writes an
item.

- A folder has no `revisionDate`, so `shownName` is its name as the client
  showed it. A folder that has another name now or is gone is a conflict
  (409). Its `conflicts` hold `{folderId, expected, actual}` with the names,
  and `actual` is null for a gone folder.
- The folder is checked like an item at `POST /api/queue`, in the write
  path's check and right before the write. In the queue the conflict fails
  only the entries of that folder. Retry with overwrite renames or deletes the
  folder under the name it has then.
- A rename is refused (422) for an empty name or one of spaces only, and for
  the folder's name now. It is also refused for a name that another folder
  has ignoring case (`another folder is named "X"`) and for a name equal to a
  secret value of an item in the folder.
- A delete is refused for a folder that holds an item (`the folder holds N
  items, move them out first`). That holds for an item in the trash or the
  archive too, since Bitwarden would take the item out of the folder as well.
  Both refusals are checked again at write time. A rename checks the secret
  values of the items in the trash and the archive in the same way, as they
  get the folder's name too.
- Each write's answer goes into the cache and into the batch's own view of
  the folders. `bw serve` lists a folder as it was before an own write for a
  moment. So a later operation of the batch sees the folders as written.
- The queue's diff is one row of the folder without an item id. Its
  `itemName` is the folder's name now and its column is `folder`. `before` is
  its name and `after` the new name or null. The summaries are `Rename the
  folder "OLD" to "NEW"` and `Delete the folder "NAME"`.

#### `setUris`

`setUris` takes `items` as a list of `{itemId, revisionDate, shownUris,
uris}`. Both `uris` and `shownUris` are lists of `{uri, match}` with `match`
0 to 5 or null. The operation replaces the whole URI list of each login with
`uris` in its order and with every match exactly as given. Only MCP proposes
it.

An agent can then see and fix the match settings. A login that autofills on
the wrong site or on none can be fixed without ever reading or writing a
secret.

- These are refused (422) at `POST /api/queue`, in the check and at write
  time alike:
    - an item that is not a login (`only a login has URIs`)
    - the tool note
    - no items or one named twice
    - a uri that is empty after trimming (`URI N is empty`)
    - a match other than 0 to 5 or null
    - a uri with match 4 that Python's `re` cannot compile (`URI N is not a
      valid regular expression`)

  Bitwarden evaluates such a uri as a JavaScript regular expression. So the
  check catches only what Python rejects too. The messages name the URI by
  its position and never by its text.
- A uri equal to a secret value of the item is refused like any plain value.
  That check comes after every other refusal, so an error never tells whether
  a uri equals a secret.
- `shownUris` is a shown value like `shownFolderIds`. When the item's list
  differs now the item is a conflict with `column` `login.uris`. Unlike a
  folder bwtidy's own writes since then do not make it hold. The whole new
  list would undo a grid edit of the URIs committed after the proposal. Every
  item is checked before the first one is written. Retry with overwrite takes
  `shownUris` from the vault as it is then.
- The queue's diff has one row per item in `login.uris`. Its `before` and
  `after` are the uri strings, and `uris` holds the lists of `{uri, match}`
  with each match as its word or null. The summary of a UI entry is `Set the
  URIs of "NAME"` (or `Set the URIs of N items`).

#### `renameAttachment`

`renameAttachment` gives an attachment a new file name. It takes `itemId`,
`revisionDate` and `attachmentId` along with `fileName` (the new name) and
`shownFileName` (the name the proposer saw). Both an agent and the user in the
grid queue it, since what MCP can do the UI can do too. The contents never
pass through MCP.

- A rename in place is not possible through `bw serve`. An item `PUT` with
  another `fileName` answers success and gives the item a new
  `revisionDate`, but it leaves the name as it was. `bw serve` has no other
  route for it, and bwtidy never calls the server's API around `bw serve`. So
  the write takes these steps:
    - `GET /object/attachment/{id}?itemid=` fetches the bytes into the write's
      RAM only. They are never logged or kept.
    - `POST /attachment?itemid=` uploads them under the new name.
    - A check makes sure that the answer lists exactly one new attachment with
      the new name and the old one's `size`.
    - `DELETE` removes the old one.

  The attachment gets a new id.
- When the upload or anything after it fails, the copy is deleted again and
  the entry fails. So the item keeps exactly one attachment.
    - While the delete of the old one has not started, the copy goes at once.
      After a failed upload that is the copy the item lists anyway, found by its
      name and an id it did not have.
    - After the delete started the copy goes only when the item still lists the
      old one, since a delete that failed late may have happened.
    - A copy that cannot be deleted again is logged by its id and both stay.
- On Vaultwarden neither the upload nor the delete changes the item's
  `revisionDate`. The own write is recorded like a `PUT`'s (see Own writes).
  It comes from the upload's answer and from a `GET` right after the delete.
  So an edit queued before the rename never conflicts with it.
- These are refused (422):
    - the tool note
    - an `attachmentId` the item does not have (`the item has no attachment
      with the id ID`)
    - an empty new name
    - a name with a slash or a backslash, because the backups file an
      attachment under `item-attachments/<itemId>/<fileName>`
    - the name it has now
    - the name of another attachment of the item (`the item has another
      attachment named "NAME"`)

  Names are compared ignoring case (`str.casefold`). So a download of both to
  a file system that ignores case never overwrites one with the other.
  Bitwarden itself allows two attachments of one name, but bwtidy does not.
  Changing only the case of the attachment's own name stays allowed.
- Two renames of two attachments of one item to the same name end with the
  first one written. Queued in one request the second is refused at once.
  Queued in two requests the second entry fails at the write.
- The file name is no secret, so the rule of equal values does not check it.
- The rename is a conflict when the item's `revisionDate` differs. It is also
  one when the attachment has another name than `shownFileName`, since a
  rename elsewhere leaves the `revisionDate` as it is. The conflict is
  `{itemId, expected, actual, column: "attachments"}`. It is checked at `POST
  /api/queue`, in the check and right before the write.
- Retry with overwrite takes the name the attachment has then. A second
  rename of the same attachment queued before the first is written fails
  because its attachment id is gone.
- The queue's diff is one row of the item in the column `attachments`. Its
  `before` holds the file names now and its `after` has the new one in place.
  The summary is `Rename attachment "OLD" of "ITEM" to "NEW"`.

#### `setToolNoteSection`

`setToolNoteSection` saves one section of the tool note's notes. It takes a
`section` and its `changes` with no `revisionDate`. The `section` is one of
the five below. Only bwtidy's own
code queues or writes it. `POST /api/queue`, `POST /api/operations` and MCP
refuse it like every other operation on the note.

- At write time the write path GETs the tool note as it is then. It applies
  the changes to that section and PUTs the note, or does nothing when the
  notes stay as they are. So it never works on a stale copy and never
  conflicts on the note's `revisionDate`. Several such entries in one commit
  apply one after the other.
- `changes` is a list of changes of one entry each, applied in order. Two
  tabs that save templates or datatypes would otherwise lose each other's
  change. A save of the whole section would undo another save built on the
  same base. These are the changes per section:
    - `generator` has `{"op": "setDefault", "options"}`, `{"op": "setItem",
      "itemId", "options"}` and `{"op": "removeItem", "itemId"}`.
    - `toFill` has `{"op": "addColumn", "itemId", "column", "since"}`, with
      `since` only for a new entry. It also has `{"op": "removeColumn",
      "itemId", "column"}`, and the entry goes with its last column.
    - `templates` are known by name because they have no id. `{"op":
      "addTemplate", "template"}` puts a template last in the list and is
      refused when the name is taken ignoring case. `{"op": "setTemplate",
      "template"}` replaces a template in its place. `{"op": "renameTemplate",
      "name", "to"}` renames one and `{"op": "removeTemplate", "name"}` removes
      one.
    - `{"op": "moveTemplate", "name", "index"}` moves a template. A larger index
      means the last place, since the first of several matches wins. A change
      that names a template which a save before it renamed or deleted fails its
      entry. It never writes the old template back.
    - `datatypes` are known by id. `{"op": "addDatatype", "datatype"}` carries
      the id given when it is queued. `{"op": "setDatatype", "datatype"}` fails
      its entry when the datatype is gone. `{"op": "removeDatatype", "id"}` does
      nothing when it is gone already. It is refused while a template of the
      note as it is then uses the datatype.
    - `note` has `{"op": "setName", "name"}` with a name that is not empty.
- Every item id is followed through the conversions that this session
  committed (`convertItem`, `moveToPersonal` and an `applyTemplate` that
  changes the type). So a change queued before a conversion and committed
  after it lands on the new item.
- Every other section that does not parse starts over empty, but the
  `datatypes` keep their broken entries. So that section still fails its
  check until a broken one is fixed or deleted.
- The `generator` write drops the settings of items that are gone. The trash
  counts as gone, but an item that a conversion made since the batch began
  does not.
- The changes are checked against their section. The section they leave is
  checked as a whole by its parser. Both checks run at these points:
    - when the changes are queued, on the note as the saves under way of every
      section leave it (422)
    - in the check before the backup, where such an entry fails and the rest
      goes on
    - at write time
- Saves of a section may be pending or being committed. Every read of that
  section then applies all of them over the note in queue order, the same way
  the writes will. Such reads are the routes that say `saving`, the base of
  the next change and the queue's diff.
- The answer of `bw serve` to the note's `PUT` replaces the note in RAM at
  once. So once an entry is `applied` every read shows the saved value, also
  before the reload after the batch. Otherwise a save built on that read would
  be refused for a name it held twice.
- The check when queued takes the saves of the other sections into account
  too. A datatype can go right after the save that removed the template that
  used it, while that save waits.
- Its change log row names the tool note in `itemIds`. So its Restore is
  refused like the note's.

#### `setLeaf` and field types

- The secret rule for a `setLeaf` works both ways (see Secret values). A value
  equal to one of the item's own secret leaves may not go into a non-secret
  column of that item. A value for a secret column may not equal a plain
  value of the item. Both are checked at these points:
    - at `POST /api/queue` on the item with the earlier operations of the
      request applied. Its pending `setLeaf` and `removeLeaf` entries are
      applied too. So are its pending `generateLeaf`, `setUris` and
      `renameField` entries.
    - in the write path's check with the earlier operations of the batch
      applied
    - at write time on the `GET` of the item

  The same rule refuses a `moveToFolder` whose folder name equals a secret
  value of a moved item. At write time it refuses a `generateLeaf` whose
  generated value equals a plain value of the item.
- Changing an existing custom field never changes its type. A new field gets
  the requested type, which is text by default.
- A hidden `fieldType` for a field that the item already has with another
  type is refused (422) and nothing is stored. The value would stay in the
  open as a text field, while a secret column's dialog promises secrecy. This
  is checked in batch order and again on the item that the write GETs. So a
  hidden value for a field that an earlier operation of the same commit
  creates as text fails its entry, and nothing of the value is written.
- A `setLeaf` into `fields.<name>#2` of an item without a value in
  `fields.<name>` is refused. The occurrence waits for the one before it.

#### SSH keys

An SSH key item (type 5) gets its `sshKey.publicKey` and
`sshKey.keyFingerprint` from its `sshKey.privateKey`. This happens whenever
bwtidy writes it with a private key and one of the two empty. That is on a
create or a conversion. It also happens on a restore that creates the item
anew and on every `PUT`.
`bw` refuses an SSH key without all three and fills in none itself. MCP never
gives a private key, so without this a secure note that holds a key could not
become an SSH key item.

- A value already there is kept, even one that does not match the key. The
  public key is the OpenSSH line without a comment. The fingerprint is
  `SHA256:` and the unpadded base64 of the SHA-256 of the public key blob.
- bwtidy reads Ed25519 and RSA keys as well as ECDSA keys on P-256, P-384 and
  P-521. They may be in OpenSSH form or in PEM as PKCS#1, SEC1 or PKCS#8.
  Windows line ends and spaces around the key are fine too.
- A key with a passphrase fails the operation with "the private key has a
  passphrase". Any other text fails it with "the private key cannot be read".
  Nothing of that item is written then. Neither text holds a character of the
  key, and neither the key nor the derived values are logged.
- The derived columns are not secret. The queue's diff of a `convertItem` or
  an `applyTemplate` shows them plain when the private key comes from the
  item itself. A queued hidden value is a ciphertext, so the diff of a grid
  edit or a new item shows the masked private key only. A key that cannot
  give them fails at the write and never in the diff.

#### `convertItem` and `moveToPersonal`

- `convertItem` maps `{target column: source column}`. A mapped custom field
  moves and mapped `notes` are cleared, while `name` stays. Unmapped custom
  fields come along as they are.
- An unmapped built-in leaf of the old type becomes a custom field named after
  its key, so `login.password` becomes `password`. It is hidden when the leaf
  is secret, and `login.uris` is joined with newlines.
- Passkeys and password history cannot be copied. They are lost with the old
  item, which is in the trash and the backup.
- Attachments are downloaded and uploaded again after a `POST /sync`, because
  `bw serve` knows a created item only after a sync. The old item is trashed
  last. If anything after the create fails the new item is trashed again.
- `moveToPersonal` (`itemId` and `revisionDate`) takes an organization item
  into the personal vault. Bitwarden has no call for it, so it works like
  `convertItem`. It creates the item anew with everything as it is except the
  organization and the collections. Then it copies the attachments and trashes
  the old item. Its losses are the same.
- Everyone who shares the item's collections loses the item. A personal item
  and the tool note are refused (422). Moving between organizations is out of
  scope.

#### `generateLeaf`

`generateLeaf` (`itemId`, `revisionDate` and `column`) replaces a value with
one from `bw`'s generator. The value is `login.password` of a login or a
hidden custom field, and a missing field is created hidden. At write time the
operation reads the `generator` section of the note as it is then. It
generates with the item's own setting or else with the default. Then it sets
the leaf so that `bw` keeps the old value in the item's password history.

The value is never logged, stored or answered. Any other column is refused
(422) with "a generated value goes only into login.password or a hidden
field". So are a field of another type and the tool note. MCP has no tool for
it.

#### `createItem`

`createItem` adds a new item. It is not the tool's main purpose, but it makes
the tool complete and agents can propose new items too. It takes these
parameters:

- `name`
- exactly one of `type` and `template`
- `folderName`
- `organizationName` and `collectionNames`
- `values` and `secretValues`
- `generatePassword` and `generateColumns`

The item is built in this order:

- the type's empty sub object, the name and the organization with its
  collections (at least one)
- a template's custom fields, created empty with their field types
- `values` (`{column: value}` as `setLeaf` takes them), where an unnamed
  custom field becomes text
- `secretValues` (`{column: text}` for secret columns only), where an unnamed
  custom field becomes hidden

The folder is found by name or created.

- `secretValues` is the one way a secret value comes in, and only a person
  sends it. From the queue it comes from a `ui` entry and is encrypted while
  it waits. MCP has no way to send it.
- With `generatePassword` (logins only) `login.password` is set at write time
  from `bw`'s generator. That is `GET /generate` with the default of the
  `generator` section. The value is never logged, stored or answered.
  `generateColumns` does the same for each column it lists, which is
  `login.password` of a login or a hidden field. A `login.password` in both
  generates once. Both parameters are accepted because a stored entry may
  hold `generatePassword` and agents know it.
- The write creates the item (`POST /object/item`) and syncs before it is
  used. The change log row has empty `itemIds` and the new id in
  `createdItemIds`, so Restore of the row moves the new item to the trash.
  Committed from the queue the item puts the secret columns it left empty on
  the to-fill list.
- These are refused (422) before anything is written, at `POST /api/queue`
  and in MCP as in the write path. Each message names the column and never
  the value.
    - a secret column in `values` (`COLUMN is secret: a new item never takes a
      secret value, fill it in the grid after it is created`)
    - a column in `secretValues` that is not secret, an empty one or `name`
    - a column that is also generated
    - a value in `secretValues` that equals the name, the folder or one of the
      `values` (a generated value that does is refused at write time)
    - `generatePassword` on another type than a login
    - a column in `generateColumns` that takes no generated value or is also in
      `values`
    - an unknown type or template, or both or neither of `type` and `template`
    - an empty name or `name` among the values
    - an unknown organization, an organization without collections or
      collections without one
    - a column that does not exist for the type
    - a personal secure note named `bwtidy` or one whose `notes` hold a `note`
      section with its own name, since either would be taken for the tool note

## The tool note

The tool note is one secure note named `bwtidy` (or the name the user kept).
It lives in the user's personal vault and never in an organization, where its
other members would see it. bwtidy creates it on the first login when it is
missing. [The bwtidy note](explanation/tool-note.md) shows what a user sees of
it.

- `notes` holds one JSON object `{"version": 1, ...}` with one key per
  section. The sections are `templates` (see Templates) and `datatypes` (see
  Datatypes) as well as `toFill`, `generator` and `note`. A key that bwtidy
  does not know is kept.
- Each section is saved alone with `setToolNoteSection` (see Writes), so a
  save of one never loses another. Empty notes read as no sections. Notes that
  are not such an object (because they were edited by hand) read as no
  templates. The next section save then starts them over from `{"version":
  1}`, and the backup of that write holds the old notes.
- bwtidy finds the note by its id. It remembers the id in the `settings`
  table (key `tool_note_id`) whenever it finds or creates the note. The lookup
  tries these in turn:
    - the remembered id, as long as it is a personal secure note whatever its
      name is now
    - the name `bwtidy`
    - a personal secure note whose `note` section holds its own name, found
      after a reinstall or by another bwtidy

  Every user of the note goes through that one lookup. A login creates a note
  only when none of them finds one. A rename in another app would otherwise
  make the templates vanish, and the next login would create a second empty
  note.
- The write path refuses every operation that touches the note, at `POST
  /api/queue` too. The answer is 422 `the bwtidy note is managed by bwtidy,
  edit the templates on the Types page`. The only exceptions are bwtidy's own
  writes:
    - a section save, which is a `ui` queue entry of `setToolNoteSection` that
      carries the permission with it so that the commit passes it on for that
      entry alone
    - Rename it back
    - Keep this name

  These say so explicitly in their call to the write path, which never judges
  by the column. The backup password updates write the note directly through
  `bw serve`.
- MCP never lists, reads or touches the note.
- The Vault grid can leave out its row (`hideToolNote`, see Settings). Only
  the Vault grid hides it. The rows then shown are the rows of the grid, so
  its counts and select all do not count the note. The normalize dialogs
  always leave it out.

### The `generator` section

The section is `{"default": OPTIONS, "items": {ITEM_ID: OPTIONS}}`, and its
parser is in `sections/generator.py`.

- OPTIONS are the options of Bitwarden's own generator. They are one of these
  two:
    - `{"type": "password", "length", "uppercase", "lowercase", "number",
      "special", "minNumber", "minSpecial", "ambiguous"}`, where `ambiguous`
      true avoids ambiguous characters like `bw generate --ambiguous`
    - `{"type": "passphrase", "words", "separator", "capitalize",
      "includeNumber"}`
- These are the limits:
    - a length of 5 to 128
    - 3 to 20 words
    - `minNumber` and `minSpecial` of 0 to 9
    - at least one character class
    - a separator of one character or none, stored as the character or `""`
      for none and sent to `bw` as `space` and `empty` where needed

  The minimums have to fit the length, since `bw` would silently make the
  password longer than asked. An item's own setting is often there for an app
  that cannot take long passwords.
- Without a section or a default the default is a password of 24 characters
  with all four classes. Settings that are not valid give the same default.
- An item's own setting moves to the new id with a conversion. It is written
  to the section as part of that write together with the item's entry of the
  to-fill list. Settings of items that are gone (the trash counts) are dropped
  the next time the section is saved.
- `GET /api/generator` answers the section with the default filled in. It has
  `saving` while a save is under way and `error` when the stored settings are
  not valid.
- `PUT /api/generator/default` and `PUT /api/generator/items/{item_id}` take
  OPTIONS. `DELETE /api/generator/items/{item_id}` puts the item back on the
  default. Each queues its one change as a `ui` entry of `setToolNoteSection`
  (with the summary "Save the generator settings") and starts a commit at
  once.
- These routes answer 202 with `entryId` and the section as the saves under
  way leave it. They answer 422 for options outside the limits and for the
  tool note, and 404 for an item not in the vault.
- `POST /api/generator/example` takes OPTIONS and answers `{"value": ...}`
  from `bw serve` (see Secret values). It answers 422 outside the limits and
  502 when `bw` fails.
- `GET /api/grid` gives a row `ownGenerator: true` when the item has its own
  setting, and leaves it out otherwise. It takes this from the view that the
  generator routes use, so a save shows at once.

### The `note` section

The section is `{"name": NAME}` with the note's expected name, a string that
is not empty. Its parser is in `sections/note.py`. Without the section or
without `name` the expected name is `bwtidy`. The same holds for a section that
does not parse. So a note written before the section existed keeps working
with the same `version`.

bwtidy finds the note by its id, so its name is only a label for the user. The
grid's warning about a rename should not come back for a name that the user
chose.

`GET /api/templates` answers the note's name now (`toolNoteName`) and its
expected name (`toolNoteExpectedName`). When they differ the grid warns (see
[Alerts](reference/ui.md#alerts)) and offers two choices:

- Rename it back sends `POST /api/tool-note/rename-back` with the note's
  `revisionDate`. That is a `setLeaf` of `name` through the write path, with
  the backup and the `revisionDate` check and 409 on a conflict.
- Keep this name sends `POST /api/tool-note/keep-name` with the name that the
  warning showed. That is a `setToolNoteSection` of `note` through the write
  path. It answers 422 without a name and 409 when the note is not named that
  any more.

Both answer `toolNoteName` and `toolNoteExpectedName` along with the new
`revisionDate` and the backup.

### The to-fill list

The section `toFill` holds the secret fields that the user still has to fill
in. It is a list of `{"itemId": ID, "columns": [COLUMN, ...], "since":
"YYYY-MM-DD"}` and its parser is `parse_to_fill` in `sections/to_fill.py`.

- The columns are secret columns only, given as grid column ids. An entry has
  at least one column and each column once.
- There is at most one entry per item. `since` is the day the entry was
  added.
- A section that does not parse reads as an empty list.

An agent's new item takes no secret value. Apply template adds missing hidden
fields empty. So secret fields are left for the user, and the list reminds
them.

- These add to the list:
    - a `createItem` or `applyTemplate` committed from the queue, both from
      `ui` entries and from `mcp` entries the user approved
    - a cell that the user marks
- A `createItem` from a template lists the template's required secret
  coordinates. An optional one may stay empty, and a field name pattern names
  no column.
- A `createItem` of a raw type lists `login.password` (unless
  `generatePassword`) for a login. It lists `card.number` and `card.code` for a
  card and `sshKey.privateKey` for an SSH key. It never lists `login.totp`
  unless a template lists it.
- An `applyTemplate` lists the template's required secret coordinates. When it
  converted the item they go under the new item's id.
- A write through `POST /api/operations` adds nothing. Adding never fails the
  write it follows, and a list that cannot be queued is only logged.
- After every load of the vault in RAM the list is pruned. These are dropped:
    - a column that has a value now, filled in bwtidy or in any Bitwarden app
    - a column that the item no longer has as a secret
    - an entry with no column left
    - an item that is gone or in the trash
    - the tool note

  Checking a value only asks whether it is empty. It never logs or copies the
  value.
- A conversion moves the item's entry to the new id with its `since`, as part
  of that write.
- Every change is a queued `setToolNoteSection` of `toFill`. It is a `ui`
  entry with the summary `Update the to-fill list`. Its changes are the
  difference between the list wanted and the list as the note holds it with
  every such entry still pending applied. A list that is already right queues
  nothing, so its own commit queues nothing again.
- What a write left empty and what the user marked stay in RAM until the tool
  note itself holds them. Every load merges them in again. So a `toFill` save
  that failed or could not be queued is queued again at the next load. An
  Unmark takes its column out of them too.
- Without this an Unmark between a write and its reload would come back. A
  failed save would also lose its columns for good. The limit is that a
  backend stop before the save is in the note loses what was only in RAM.
- `GET /api/grid` answers `toFill` as `{ITEM_ID: [COLUMN, ...]}` for the vault
  as it is now. That is the pruned list, even before it is written. The grid
  has a secret column for every listed cell, also when no item has a value in
  it yet.
- Each row has `emptySecrets` with the secret columns that the item has
  without a value. It also has `emptyTextFields` as well as `booleanFields`
  and `linkedFields` (with or without a value). Each is left out when empty.
  Field names and types are no secret.
- `POST /api/to-fill` `{itemId, column, mark}` adds the cell or takes it off.
  It answers `{"entryId": N or null, "toFill": {...}}`, with `entryId` null
  when the list stays as it is. It answers 422 for a column that is no secret
  column of the item and for a cell that has a value when marking. The tool
  note and an unknown item get 422 too.
- MCP never sees the list. `propose_create_item` only says that the secret
  fields the user fills in show up in the To fill in list.

### The backup passwords

The note lists every master password that the backups may still be encrypted
with, newest first. The hidden custom field `backup-password` holds the
newest. While a re-encryption is unfinished the hidden custom field
`older-backup-passwords` holds the older ones as a JSON list of strings. A
note with only `backup-password` reads as a list of one. A note with neither
reads as an empty list.

- Every write of the list makes both fields hidden whatever type they had. A
  field turned into a text field in Bitwarden would show the master password
  in the grid and to MCP. An unlock that finds one of them not hidden writes
  the list again for that reason alone.
- The write first reads the note fresh from `bw serve`. When the note is no
  personal secure note any more the write changes nothing. It fails the login
  with 409 `the bwtidy note is not a personal secure note any more, move it
  back to your personal vault`.
- A backup that decrypts with none of the passwords is left untouched and
  named in the UI after the login. No password and not even the length of the
  list is ever logged, shown or answered.

On login the backups are re-encrypted with the master password without
asking, when the list is not just the master password. When the master
password is not first in the list it is put first and the note is saved,
before anything is re-encrypted.

Then comes the identity for hidden values (see MCP and the queue) and every
backup that does not open with the master password. For each one the listed
older passwords are tried, newest first. It goes one file at a time and each
file is replaced atomically. So a run cut off at any point leaves every backup
readable with a password still in the list, and the next login finishes the
job.

Only as the very last step does the list become the master password alone.
That happens once the identity and every backup open with the master password
or a backup was found unreadable with all of them.

```mermaid alt="When the master password is not first in the list it is put first and the note is saved. Then the identity and the backups are re-encrypted one file at a time. Each is opened with a listed password and encrypted with the master password, and one that no listed password opens is left untouched. Only when that is done does the list shrink to the master password alone. A run cut off before that leaves the list as it was, and the next login goes on."
flowchart TD
  login([Login]) --> first[Put the master password first in the list when it is not, and save the note]
  first --> each[Open the identity or the next backup with a listed password and encrypt it with the master password]
  each -- no listed password opens it --> left[Leave it untouched and name it in the UI]
  left --> each
  each -- more files --> each
  each -- cut off --> next([The next login goes on with the list as it is])
  each -- all done --> shrink[The list becomes the master password alone]
```

The list and its order exist because of runs that are cut off. With only one
old password kept, a run from A to B cut off halfway would leave the field at
A. A later change to C would then try only A and C. The backups already in B
could not be read any more.

## Backups

Every write has a backup that matches the vault as it is right before the
write. When possible the backup is taken ahead of time in the background. A
backup takes several seconds, as it runs one `bw` process per export and per
attachment and then the encryption. That would make every edit slow if it
were taken inside the write. [Secret values and
backups](explanation/secrets.md#backups) says what a backup holds for a user.

```mermaid alt="After a write or an unlock a backup starts in the background unless the newest backup already matches the vault or a background backup runs. A write computes the vault's fingerprint, waits for a running backup and reuses the newest backup when its fingerprint matches. Otherwise the write takes a backup of its own before it writes."
flowchart TD
  after([After a write or an unlock]) --> same1{Does the newest backup match the vault?}
  same1 -- no --> bg[Take a backup in the background unless one runs]
  same1 -- yes --> none([Nothing to do])
  write([A write after its sync and checks]) --> wait[Wait for a running backup]
  wait --> same2{Does the newest backup match the vault?}
  same2 -- yes --> reuse[Reuse it]
  same2 -- no --> own[Take a backup right there]
  reuse --> go([Write])
  own --> go
```

- A backup carries a fingerprint. That is a sha256 hex over what the backup
  covers, computed from the listings it stores so that the order does not
  matter. These are its parts:
    - every item's `id` and `revisionDate`
    - every item's `folderId`, `favorite` and `organizationId`
    - every item's `collectionIds` and attachment ids
    - the same parts of every archived item that is not in the trash, on
      their own
    - the folders, collections and organizations as whole JSON

  An attachment's `url` carries a token that differs between two listings.
  The rest of an item is fixed by its `revisionDate`. Only the folder, the
  favorite flag and the collections change without a new one (see Writes).
  The archived items count apart from the others. So archiving an item
  changes the fingerprint even when its `revisionDate` stays. The trash does
  not count and neither do the archived items in it. The cache lists the
  archived items in and outside the trash for this, for the restores
  (Restoring a change log row) and for the folder checks (`renameFolder` and
  `deleteFolder`).
- The fingerprint is stored in SQLite (the table `backup_fingerprints` with
  `name` and `fingerprint`) and removed with its backup. The `revisionDate`s
  it hashes are spelled as bwtidy first saw each revision. So a backup stays
  reusable when `bw serve` answers another spelling later. After a restart
  the first spelling may be the other one, and then one backup more is taken.
- A backup starts in the background after a write once the vault is reloaded.
  It also starts after an unlock once the cache is loaded. It does not start
  when the newest backup's fingerprint already equals the vault's.
- One backup runs at a time for a write, by hand or in the background. The
  background backup holds the session's password and key only while it runs.
  Its failure is only logged, and the next write then takes its own backup.
- A write first does its sync, reload and checks. It then computes the
  vault's fingerprint from the cache and waits for a running background
  backup. It reuses the newest backup when that fingerprint equals the
  vault's, and otherwise it takes a backup right there. A backup without a
  stored fingerprint is never reused.
- The file is `BACKUPS/<UTC timestamp>.zip.age` with the timestamp format
  `YYYYMMDDTHHMMSSZ`. A file whose name has that shape but no valid instant
  (a month 13) is no backup, like any other file.
- The encryption is age with a passphrase (scrypt), which is the master
  password. The `age` CLI only reads a passphrase from a terminal, so the
  backend uses `pyrage` (`pyrage.passphrase.encrypt`). The files must decrypt
  with plain `age -d` at a terminal.
- The zip holds these files.

  ```text
  personal-export.zip                    bw export --format zip, whose data.json bw import takes
  org-<orgId>-export.json                bw export --organizationid <orgId> --format json, per org
  item-attachments/<itemId>/<fileName>   every attachment of every item, via bw get attachment
  raw/
  ├── items.json                         GET /list/object/items, the exact objects the app PUTs
  ├── trash.json                         bw list items --trash
  ├── archived.json                      GET /list/object/items?archived=true, then with trash=true
  ├── folders.json
  ├── collections.json
  └── organizations.json
  RESTORE.md                             docs/restore.md, the steps of a disaster restore
  ```

- `item-attachments` is the source of attachments. It holds every attachment
  of every item in the personal vault and the organizations alike. That
  covers the active items and those in the trash and the archive.
  They are fetched one by one with one `bw` process each, one after the other
  since they share one CLI state.
- bw 2026.10.0 leaves an archived item out of every listing unless it is
  asked for archived items (`--archived` or `archived=true`). The trash
  listing then holds only the archived items in the trash. So
  `raw/items.json` and `raw/trash.json` hold no archived item, and
  `raw/archived.json` holds every archived item with those in the trash.
  `bw export` leaves out only the trash, so the personal export holds an
  archived item with its `archivedDate`. An organization export holds none.
- A second attachment with the same file name on one item is stored as
  `<attachmentId>-<fileName>`. The id is put in front again while that name is
  taken.
- `bw export --format zip` files attachments under `attachments/<item
  name>/<file name>` with special characters replaced. So items of the same
  name collide (`NAME_1` and `a/b` with `a_b`), and one file overwrites the
  other. A failed download is skipped with only a log line. Nothing says
  which folder belongs to which item.
- `personal-export.zip` stays as `bw export` wrote it. `bw import` has no zip
  format, so a restore imports the `data.json` inside it (format
  `bitwardenjson`). Its `attachments` folders are never trusted or read.
- The exports hold nothing from the trash. `raw/trash.json`,
  `raw/archived.json` and `item-attachments` do.
- `RESTORE.md` is `docs/restore.md` as the image has it
  (`/app/docs/restore.md`). The `COMMIT` in its line "Written for bwtidy
  COMMIT." is replaced by the version as `/api/health` shows it. With the
  repository gone the backup itself says how to restore it.
- When the file cannot be read the backup is written without it, and the log
  says so. A backup never fails for its manual. The documentation site's copy
  fills in the same line with the version the site is built for.
- A completeness check runs before the zip is built. Every attachment listed
  on an item in `raw/items.json`, `raw/trash.json` and `raw/archived.json`
  must have its member.
  The member's length must fit the listed `size`.
- That size is the encrypted blob's and not the plain byte count. It is `49 +
  (length // 16 + 1) * 16` for one type byte, the IV and the HMAC plus the
  PKCS7 padded AES-CBC ciphertext. A failure raises `BackupError("attachments",
  ...)` that names the item id and the attachment id only. No `.zip.age` is
  written then.
- The CLI runs with `BW_SESSION` in its environment and never on the command
  line. It runs with `--nointeraction`, its stdin closed and a timeout.
  `--raw` writes the export to stdout, so no plain export ever touches the
  disk.
- The zip is built in RAM. The encrypted file is written to a temporary name
  in the same directory, fsynced and renamed. A failed step leaves no
  `.zip.age` behind, and the error names the step.
- The listings after the sync are not trusted at once. Right after a `POST
  /sync` that fetched something, `bw serve` answers `GET /list/object/items`
  with an empty list for a moment while it decrypts the items again. So the
  cache load and the backup ask again for a few seconds before they believe an
  empty list.
- The backup refuses to write a backup with no items, because after the first
  login the tool note always exists.
- An archived item that is not in the trash counts as an item in the next
  two checks. One in the trash counts like the trash, which they leave out.
- An item whose `organizationId` is not among the listed organizations makes
  the backup list them again with the same wait. The backup is refused when
  the organization is still missing, as it would get no export.
- A folder or collection that an item refers to but the listing lacks is
  asked again the same way. After that it is only logged as counts, since the
  raw items still carry the ids.
- `GET /api/backups` lists the backups with their name, size and time. `POST
  /api/backups` takes one by hand, and the UI has no button for it.
- `GET /api/backups/{name}` answers the encrypted file as a download
  (`application/octet-stream`). It does so only for a name the listing knows,
  so no path can be given. Anything else gets 404.
- `DELETE /api/backups/{name}` deletes one backup and its fingerprint under
  the backup lock. It drops the backup's decrypted copy from RAM too. These are
  refused with 409:
    - the newest backup, since the next write may reuse it
    - the backup that a running write selected
    - a backup that a pending or approved `restoreItems` or `restoreFolder`
      reads
    - a backup that a failed queue entry's result names (dismiss the entry
      first)

  The backups are the user's to prune.
- The retention runs after each new backup. It deletes a backup that is not
  among the newest `BWTIDY_BACKUP_KEEP_NEWEST` (40) or is older than
  `BWTIDY_BACKUP_KEEP_DAYS` (90) days. But it always keeps the newest
  `BWTIDY_BACKUP_KEEP_ALWAYS` (5), so 90 days without changes do not delete
  every backup. The three values are whole numbers of at least 1.
- A backup still in use (for the reasons Delete refuses) is kept until it is
  not, and it still counts among the 40. A queued restore would otherwise fail
  when the retention had deleted its backup. The fingerprints and decrypted
  copies go with the deleted backups. Copying the backups directory elsewhere
  is the deployment's concern.
- The last decrypted backup read is kept in RAM while the session lasts. The
  queue asks for a pending entry's diff on every listing, and an age decrypt
  pays scrypt's cost each time.
- There are two restore paths. The rollback per item is `restoreItems` below.
  The disaster restore imports the exports into an empty account by hand with
  the steps of [restore.md](restore.md).
- The disaster restore needs only age and the official Bitwarden CLI, along
  with a stock `python3` (3.8 or later with the standard library alone). It
  needs nothing of bwtidy. Whoever restores a lost vault has a plain machine,
  and Python reads more easily than a jq program.
- The import gives every item a new id. So the restore's program pairs each
  backup item with its new item by content. It uploads the attachments from
  `item-attachments` and skips one that the new item already has, so a second
  run adds only what is missing.
- Each organization export goes into the organization of the same name. With
  two organizations of one name the program stops before anything is
  uploaded. Matching only by whether an item is in an organization would let
  twins in two organizations get each other's attachments.

### Restoring a change log row

The main concern is data loss. `restoreItems` makes the rollback a button,
and the tests prove that a backup brings the data back.

- `restoreItems {backup, itemIds, revisionDates, changeLogId?}` takes the
  row's `itemIds` plus its `createdItemIds` as `itemIds`. `revisionDates`
  maps the ids that are active when the restore is planned to their
  `revisionDate`. `changeLogId` is the row the plan was made for, and it is
  absent when a client builds the operation by hand.
- The restore goes through the write path like every operation. It gets a
  backup of the vault as it is now (so a restore can itself be restored) and
  one change log row. Each item is checked again right before its first
  write.
- The move's answer goes into the chain of own writes. The PUT after an
  untrash is not checked again, since the untrash answers no `revisionDate`.
  The queue takes the restore as a `ui` entry, and MCP has no restore tool.
- The restore's own row records `restored_backup` (the operation's `backup`)
  and `restored_change_id` (its `changeLogId`). Both are null on every other
  row and are answered as `restoredBackup` and `restoredChangeId`. The row's
  own `backup` stays the new backup taken before the restore.
- A change that a later restore undid says so. A restore puts whole items
  back, so it undoes every change of those items made after its backup.
- A change X is a row with result `ok` that is not itself a restore. It is
  marked by the first later restore R (with a higher id and result `ok`) that
  put back an item X touched from a backup taken at or before X. So R's
  `restoredBackup` is X's own `backup` or an older one by the UTC time in the
  names. A deleted backup still counts that way.
- `GET /api/change-log` gives each row `restoredBy: {id, at, itemIds}` or
  null, looked up over the whole table. Only the first such restore counts,
  so nothing beyond the restore rows is stored.
- X's own backup is the bound rather than X's time because names have whole
  seconds. The background backup after X can be named in X's second and
  already hold X.
- The restore acts per item id against the decrypted backup. The table gives
  each case with the plan's text in quotes.

  | The item in the backup | The item now | What the restore does |
  | --- | --- | --- |
  | in `raw/items.json` | active in the same organization, or personal in both | It PUTs the backup's item with the current `revisionDate` ("put back as before"). The attachments stay as they are because no operation removes one. |
  | in `raw/items.json` | in the trash | It runs `POST /restore/item/{id}` and then the PUT ("take out of the trash and put back as before"). |
  | in `raw/items.json` | gone | It creates the item anew from the backup without `id`, `revisionDate`, `attachments`, `passwordHistory` and `fido2Credentials`. Then it uploads the attachments from the backup ("create anew (passkeys and password history are lost)"). The new id goes into the row's `createdItemIds`. |
  | in `raw/items.json` and in an organization | active and personal | It goes back with `POST /move` and then the PUT ("move back into the organization and put back as before"). |
  | in `raw/items.json` | active in another organization than in the backup | A PUT cannot undo the move. The item is created anew in its old place like `moveToPersonal` does with the attachments copied from the current item. The current one is trashed ("create anew in its old place and trash the current one"). |
  | in `raw/trash.json` | active | It moves the item to the trash ("move to the trash"). |
  | in `raw/trash.json` | not active | It leaves the item ("leave as it is"). |
  | in neither file, as the row's operation created it | active | It moves the item to the trash ("move to the trash"). |
  | in neither file | not active | It leaves the item ("leave as it is"). |

- An archived item counts as active and one in the trash as in the trash,
  whether it is archived or not. A restore keeps an archived item archived.
  The PUT and the untrash leave its `archivedDate` as it is, and the plan's
  text ends in "(it stays archived)".
    - An archived item has a `revisionDate` in `revisionDates` and is checked
      for a conflict like an active one.
    - It is refused (422) when its organization differs from the backup's. A
      PUT cannot move it, and the copy a restore would make is not archived.
      Taking it out of the archive first makes it an active item.
    - An archived item that the row created, or that the backup holds in the
      trash, is moved to the trash and stays archived there.

  `bw serve` lists an archived item only when it is asked for archived items
  (Backups). Without these listings a restore would take the item for gone and
  plan it as created anew.
- Whichever of these writes the item, its folder and collections go back by
  name when the vault no longer has their ids. The server refuses a
  `folderId` or a collection id that it does not have. Without this a restore
  after a deleted folder or collection would fail, half done in the case of
  the collection.
    - A folder that the vault lacks becomes the first folder of that name or is
      created. Without a name in the backup the item goes without a folder.
    - A collection that the organization lacks becomes the collection of that
      name there, or else it is left out.
    - An item whose collections are all gone keeps the ones it has now. One that
      has none (created anew or moved back in) fails with that reason.
- Whether an item that is not active is in the trash or gone comes from the
  trash listings (the archived items in the trash too) that the vault in RAM
  loads with the items. It is asked of `bw serve` again right before the item
  is brought back.
- A failed read alone never counts as gone, only the settled listings do.
  Right after a sync `bw serve` answers "Not found." for every object for a
  moment. A read that fails while the listings hold the item fails the
  restore. A failed read of an item in the trash would otherwise create a copy
  without its passkeys and password history. The real item would stay in the
  trash.
- The attachments of an item created anew come from the backup's
  `item-attachments/<itemId>/` by the id that the item had in the backup. Each
  is uploaded under its original `fileName`, so two items of the same name
  each get their own files.
- These are refused with 422 before anything is written, at `POST /api/queue`
  and in the write path alike:
    - the tool note among the ids, since it holds the backup password (the
      templates are restored by saving them on the Types page)
    - a backup that does not exist or does not decrypt with the session's
      password
    - an empty `itemIds`
    - an item that is active now but was not when the restore was planned, as
      its `revisionDate` was never checked
    - an archived item whose organization changed since the backup
    - an item to create anew whose attachment the backup lacks

  Creating the item without an attachment or with another item's attachment
  would be silent data loss.
- A `renameAttachment` row is restored through the queue by a
  `renameAttachment` of the copy it made (`attachmentIds.after`). It goes back
  to the name that its backup holds for the attachment it replaced. A
  `restoreItems` would `PUT` the whole item back. That leaves its attachments
  as they are and undoes every later change of the item instead.
- The plan's `items` holds the item with the action `renameAttachment` and the
  text `rename the attachment "NOW" back to "OLD"`. It is refused (422) like
  the rename. It is also refused when the item or the copy is gone (`the
  attachment ID the rename made is gone`) or the backup lacks the old
  attachment. Its own row is a `renameAttachment` row, and `restoredBy` does
  not mark the row it undid.
- `restoreFolder {backup, folderId, changeLogId?}` puts a folder back as the
  backup's `raw/folders.json` holds it:
    - A folder that exists gets that name again ("rename back to "NAME"").
    - A folder that is gone is created again with a new id ("create again as
      "NAME"").
    - Nothing happens to a folder that has the name already. Nothing happens to
      a gone one either when a folder of that name exists ("leave as it is").

  It is refused (422) for a backup that holds no folder with the id and for a
  rename back to a name that another folder has now. The rename back passes
  the secret rule like a `renameFolder`.
- The plan of a `restoreFolder` is made again on the folders of `bw serve`
  right before the write. Its row records `restoredBackup` and
  `restoredChangeId`. Its summary is `Restore the folder "NAME" from BACKUP`
  (or `Restore a deleted folder from BACKUP`).
- `GET /api/restore-plan?changeLogId=N` answers the plan before anything is
  queued. It holds the `backup` and the `summary` along with the `operation`
  to queue and the `items`. Each item has an `itemId` and `itemName` with its
  `action` and `text`.
- For a row with a `folderId` the operation is a `restoreFolder` and `items`
  is empty. Then `folder` says what the restore does. It has `folderId` and
  `name` with `action` and `text`. The `action` is `renameBack`, `createAgain`
  or `leave`.
- The plan answers 404 for an unknown row. It answers 422 with the reason when
  the restore would be refused.
- The queue entry's summary is `Restore N items from BACKUP`. Its diff has
  one row per item in the pseudo column `restore`, where `after` is the text.
  It also has the cell rows of the items it puts back and a `trash` row for
  the items it trashes. The grid shows the cell rows as queued values and
  skips the `restore` rows.

## Grid

[The web UI](reference/ui.md#vault) says what the Vault page shows and what
each control does. These are its parts:

- the [toolbar](reference/ui.md#toolbar) with its filters and chips
- the [cells](reference/ui.md#cells) and what their look tells
- the [cell menu](reference/ui.md#cell-menu)
- the [header menu](reference/ui.md#header-menu) with Manage folders
- the [selection bar](reference/ui.md#selection-bar)
- [New item](reference/ui.md#new-item)

[Show or hide columns](how-to/show-hide-columns.md) covers the column chooser
of the Columns button. [Rename an attachment](how-to/rename-attachment.md)
covers Rename attachments, and [Change log](reference/ui.md#change-log) covers
the Change log page. These are the rules behind them:

- The grid is AG Grid Community. Column groups, a pinned `name` column and
  hiding columns are all in its free tier.
- A column exists for every coordinate that some item has a value in.
  `favorite` and `reprompt` are always there, so any item can be made a
  favorite or get reprompt from the grid.
- A false value counts as empty for Hide empty columns and for a header's
  count, while a masked secret counts as present. The count in a header's
  tooltip is over every item of the vault and not over the shown rows.
- The search keeps the rows where every word is in some text cell, but never
  a secret cell. Search, filters and every write work on the vault values and
  never on the queued ones.
- The user edits in the grid directly, and every edit is one `POST
  /api/queue` (see UI edits and the auto-commit). An edit would feel slow if it
  waited for the sync and the backup and then for the write and the
  reload. A dialog closes as soon as it is sent and nothing waits for
  the answer.
- On a conflict (409) nothing is queued. The reloaded grid marks the rows of
  the items that changed elsewhere. It also marks the cells whose shown value
  differs from before the reload.
- Masked cells are never marked because their change cannot be seen. Nor is
  `revisionDate`, which always differs. The marks stay until the next
  accepted edit. The comparison is a pure module
  (`frontend/src/shared/conflicts.ts`) so that other writers can reuse it.
- `type` changes only through the convert mapping (see Templates).
- The dialogs offer all of the vault's organizations and collections, also
  those that no item is in yet. Otherwise the first item could never go there.
  `GET /api/grid` carries them as `organizations` with one `{name,
  collections}` per organization.
- `GET /api/grid` carries the folders as `folders` with one `{id, name, items,
  trashedItems}` per folder, sorted by name. An organization with no
  collections cannot take an item because a write needs at least one
  collection. So the dialogs offer it disabled.
- A field in a secret column that is not hidden keeps its type (see Writes,
  `setLeaf` and field types). Its dialog sends no `fieldType` and says
  that the value is stored as plain text that an agent can read. A boolean
  field gets a checkbox, and a linked field gets no dialog.
- Generate new value and Generator settings for this item are offered only on
  `login.password` of a login and on a hidden custom field. They are never
  offered on a text, boolean or linked field of the same column or on the
  tool note.
- A cell that cannot hold a value (`unavailableReason` in
  `frontend/src/grid/editing.ts`) takes no edit, and its tooltip says why.
  The read-only columns, the tool note and a cell with an agent's proposal
  are not such cells and keep their own message. This is judged on the vault
  values.
- The tool note's row is read-only with every cell menu action disabled and
  no checkbox (see The tool note).
- New item queues a `createItem`. A filled secret input goes in
  `secretValues` and only there. The values stay in the dialog's state. No query
  key, log or storage holds them.
- That a template's field must be filled and match its format is the dialog's
  block alone. `POST /api/queue`, MCP and grid edits are not blocked. MCP may
  also leave a template's fields blank. A refused format check blocks nothing.
  That happens when a datatype went away or broke.
- Move to trash on a selection queues one `trashItem` entry per item in one
  `POST /api/queue`. So the auto-commit writes them as one batch with one
  backup. Moving to the trash is the only action on a selection.
- The ticks clear whenever a search or filter changes which rows are shown.
  So a row that is no longer shown is never part of an action.
- The column `rareFields` is no field of the vault, as bwtidy computes it.
  Custom fields can go in it. So can the built-in slots of `login` and `card`
  as well as those of `identity` and `sshKey`. The other columns never go in.
- An item uses a custom field when it has the field, also with an empty
  value. It uses a built-in slot only when the slot holds a value, since every
  item of a type has all of its type's slots.
- A column goes in when between 1 and N items use it. N is `rareFieldsMax`
  (see Settings). The count is over every item but the tool note and not over
  the shown rows, so the set does not change with a filter.
- The rare fields cell shows the vault values rather than the queued ones. A
  secret in it sits behind the same reveal as a masked cell.
- The name cell's suffixes for columns out of view are entries of
  `NAME_SUFFIXES` in `frontend/src/grid/nameSuffix.tsx`. Each entry has the
  column id it stands for and a function that renders it for a row.

### Queued changes in the grid

- Queued changes show in the grid as if they were done. They come from the
  pending queue that the app polls anyway. When several pending entries change
  one cell the newest (highest id) is shown.
- A secret queued value stays masked and has no reveal, as it is not in the
  vault yet. A column that only exists in a queued change gets a column in its
  group. That column is secret when the queued value is masked. An entry with
  a `diffError` changes nothing in the grid.
- Cells with an agent's proposal are read-only until it is approved or
  rejected. Cells with the user's own unsaved edits stay editable, and the
  editor starts from the queued value.
- Several unsaved edits of one cell get no conflict note. A commit checks a
  later one against bwtidy's own write before it.

### The Change log page

- The page lists `GET /api/change-log` and `GET /api/backups` as one list. A
  backup is taken before the write that names it, so it sorts right under
  that write's row. With any filter only the backups that a shown change row
  names are listed.
- A row has Restore when its result is `ok` and its backup still exists and
  it names an item or a folder. The rows of the tool note are the exception.
  Restore asks `GET /api/restore-plan` and queues the plan's operation like
  every UI edit.
- The Items filter is sent as the ids of the items whose names match. Above
  100 items it does not filter and asks for more of the name, as the ids
  would not fit in a URL.
- The Diff report lists what the shown change rows did, but never a value.
- The Source, Result and Operation columns show labels for display only. The
  API and SQLite keep the raw values. The kind stays visible beside its
  label, because the kinds are the vocabulary of the API and the MCP tools.
  So the user and the agent see the same words.
- The labels are in `frontend/src/shared/operationLabels.ts` and
  `frontend/src/changelog/rows.ts`. The rows that the filters keep come from
  `useFilteredChangeLog` (`frontend/src/changelog/filters.ts`).

### Password managers

Password managers are invited only on the account form, the unlock and the
Bitwarden login. That holds for Bitwarden and 1Password as well as LastPass,
Dashlane and the browser's own manager. Inside the app every input edits
vault data. So every other input carries `autocomplete="off"` and the opt-out
attributes that the managers honor:

- `data-bwignore`
- `data-1p-ignore`
- `data-lpignore`
- `data-form-type="other"`

The other inputs are those on the pages and in the dialogs, the selects and
autocompletes and the grid's own cell editors. A cell editor that AG Grid
builds itself gets the attributes in the same moment its input enters the
page, before any other script runs.

A secret input is a text input masked with CSS until "Show what I type". The
CSS is `-webkit-text-security: disc`, which Chromium and Firefox both honor.
Secret inputs are the secret dialog, a hidden value in Add field and the
secret inputs of New item. They have no spell check and no autocapitalize or
autocorrect. So no manager sees a password field to fill or to offer saving.

## Light and dark mode

The choice (see [Color mode](reference/ui.md#color-mode)) is one setting per
browser that every account shares. Light and Dark are stored in the cookie
`bwtidy_color_mode` as `light` or `dark`, and any other value counts as no
cookie. System is no cookie. Choosing it deletes the cookie. The browser's
`prefers-color-scheme` then decides, also when it changes while the page is
open.

The page sets the cookie itself with `Path=/` and `SameSite=Lax` for one year,
plus `Secure` on https. So a small script in `index.html` can read it before
the first paint. The gateway passes it on to the worker with the other cookies
and nothing reads it there.

The login pages follow the same setting, and so does the grid. AG Grid gets
its color scheme, and the colors made for a white background have a dark
variant.

## Paragraph width

Paragraphs of running text are at most 75 characters wide (`75ch`) on every
page and in every dialog. Their lines are balanced, which means ragged right
with the breaks chosen for the whole paragraph. Tables, the grid and forms
keep their width.

Words are never hyphenated and the text is never justified. Balanced
ragged-right lines read better than `text-wrap: pretty`, hyphenation or
justified text. The breaks do not change the text, so copying a paragraph
gives the same words with the same spaces.

A dialog's description text gets the width but not the balancing. An alert
that holds running text follows the same rule and shrinks to its text. An
alert with a short message keeps its width.

## Settings

[Settings](reference/ui.md#settings) describes the Settings page. These are
what it stores:

- The date and time format is in the `settings` table (key `date_format`). So
  it belongs to the account and follows it to every browser. Its values are
  `system` and `iso` as well as `europe`, `europe-dots` and `usa`.
- Every date and time that the UI shows follows the format in the browser's
  time zone. Date fields are our own because the browser's date field shows
  its own order whatever the choice. The UI shows System until the choice has
  loaded and when it cannot be loaded.
- Hide bwtidy's own note is in the `settings` table (key `hide_tool_note`,
  stored as `1` or `0`). It is per account and off by default (see The tool
  note).
- The N of the Rare fields column is in the `settings` table (key
  `rare_fields_max`) with the number as text. It is per account. It is set in
  the column's header menu and is 0 (off) until it has loaded.
- The layout is one per browser that every account shares. It is in
  localStorage under `bwtidy.layout`, and any other or missing value is
  normal. Every read and write is wrapped. So no storage reads as normal, and
  a failed write still changes the page.
- Compact shrinks the theme's spacing to 5 px instead of 8 and makes the app
  bar's toolbar dense. Nothing else changes, and the grid keeps its row
  height. The layout is read before the first render.
- `GET /api/settings/display` answers `{"dateFormat": ..., "hideToolNote":
  ..., "rareFieldsMax": ...}`. A stored format that it does not know reads as
  `system`. A missing or unknown `hide_tool_note` reads as `false`. A missing
  or unknown `rare_fields_max` (no whole number from 0 to 99) reads as 0.
- `PUT /api/settings/display` takes any of the three and answers like the
  `GET`. It saves only what it got, so changing one never resets the others.
  These get 422:
    - an unknown format
    - a `hideToolNote` that is not a boolean
    - a `rareFieldsMax` that is not a whole number from 0 to 99, which must be a
      JSON integer and not a string or a boolean
    - a field set to null
    - a body with none of the fields
- The password generator's default lives in the tool note. Its routes are
  there too (see The tool note, The `generator` section).

### Deleting data

[Settings](reference/ui.md#settings) says what the two buttons delete and
keep. Both act on the logged in account alone.

- "Delete my data" removes every backup (the newest one too) and every change
  log row. It also removes the backup fingerprints and every queue entry that
  is no longer pending.
- "Delete my account" does all of that and then logs out of Bitwarden. The
  gateway then deletes the account directory and its directory under the
  backups root (see Gateway).
- With pending entries the caller chooses. Write first commits the pending UI
  entries like Commit now and then deletes. A failed commit deletes nothing.
  Drop deletes the pending entries too.
- Pending MCP proposals cannot be written without approval. With Write first
  they stay pending for Delete my data. A backup that a pending restore
  proposal still needs stays too. Delete my account always drops the pending
  MCP proposals, since the account goes.
- The deletion refuses with 409 `a write is running, try again` while a
  commit or a direct write runs. Otherwise it takes the queue's lock and then
  the write path's, in the order that a commit takes them in. It holds both
  while it deletes with the backup keeper stopped. So no write starts
  meanwhile and nothing is half deleted.
- `POST /api/account/delete-data` needs the session cookie (401 without it)
  and the header `X-Bwtidy-Request: 1` (403 without it), like `POST
  /api/bw-logout`. It takes `{"pending": "write" | "drop" | null}`. These are
  its answers:
    - 409 `{"pending": N, "mcpPending": M}` when entries are pending and
      `pending` is null
    - 409 with the failure when Write first could not write
    - 409 `{"detail": ...}` while a commit is running
    - otherwise 200 `{"backups": ..., "changeLog": ..., "queue": ...,
      "keptBackups": [...]}` with the counts deleted and the backups kept for a
      pending restore proposal
- `POST /api/account/delete` takes the same body and answers the same 409s.
  On success it deletes everything, with Write first the pending MCP
  proposals too. Then it writes the marker `app/delete-requested` and logs the
  CLI out. It ends the session the way `POST /api/bw-logout` does. So the
  worker exits and the gateway deletes the account.

## About

[About](reference/ui.md#about) and [More actions](reference/ui.md#more-actions)
describe the About page and the More actions menu.

- bwtidy follows Semantic Versioning 2.0.0 with MAJOR.MINOR.PATCH. Its
  pre-releases are `-alpha.N`, `-beta.N` and `-rc.N`. The version is
  `1.0.0-beta.1`, and everything a person sees uses that spelling. [Road to
  1.0](explanation/road-to-1.0.md) has the stages and what each one promises.
- `backend/pyproject.toml` holds the version in Python's spelling
  (`1.0.0b1`), which Python packaging requires. It is the only place where
  the version is written by hand. The backend reads it from the installed
  package metadata. It spells the version the semver way in
  `bwtidy/version.py` (`semver`), which is the one conversion.
- `frontend/package.json` repeats the version in semver's spelling, though
  nothing reads it. The lock files carry generated copies. `scripts/ci` fails
  when one of them differs from pyproject's. It also fails when the release
  notes (`docs/releases.md`) have no entry for the version or break their
  format (`dev/release.md`, The release notes), because every release tells
  its users what it brings in a form they can scan.
- The image knows the commit it was built from as the committer date (YYMMDD)
  and the short git hash with a space between, like `261028 f2f4299`. It
  comes from the build arg `BWTIDY_COMMIT`, which is empty without it since
  the build context has no `.git`. `scripts/dev-vault up` adds `-dirty` for
  uncommitted changes.
- `GET /api/health` answers `{"status": "ok", "version": "<version>
  (<commit>)"}`, or the version alone without a commit. So the user knows
  which build they test.
- The page links the source code because of section 13 of the AGPL. Whoever
  uses the running service can get its source. Every link to the source or
  the license points into the repo.
- Works for me is a link only and no button. bwtidy sends nothing anywhere
  (no telemetry), and the user decides what to post. Its URL is
  `WORKS_FOR_ME_URL` in `frontend/src/about/links.ts`.
- Every direct dependency has an entry under Thanks in
  `frontend/src/about/credits.ts` with the license that its package names.
  The direct dependencies are the `dependencies` of `frontend/package.json`
  and of `backend/pyproject.toml` and the packages bundled in the docs. The
  tools that bwtidy is built and tested with stand beside them.
- The table of every package comes from `frontend/src/about/thirdParty.json`.
  `scripts/third-party` writes it from the lock files and the packages
  bundled in the docs, and it is committed.
  These are the packages it holds:
    - from npm every package of `frontend/package-lock.json` that is not a dev
      package, with the project itself left out
    - from PyPI every package that `uv export --no-dev` names, with one for
      another platform (like `pywin32`) left out
    - the packages whose files the repo holds as they are, which no lock file
      names (`BUNDLED` in the script). That is IBM Plex Mono, the code font of
      the docs site, in `docs/assets/fonts/ibm-plex-mono/` with its license.
    - `zensical-ui` from the dependency group `docs`, the theme of the docs
      site that the image serves at `/docs/`
    - the npm packages that the theme's script bundles and each of its icon
      sets that the site draws
- An icon set comes with `zensical-ui` as its ecosystem and the version of
  `zensical-ui`. Its license comes from the script's table of the sets'
  license files. The site draws a set when the theme's stylesheets or scripts
  embed one of its icons or when `zensical.toml`, `overrides/` or a page names
  one. The rest of the group `docs` (the Zensical builder and `resvg-py`)
  only builds the site and is left out.
- A PyPI package's license comes from the installed metadata. That is
  `License-Expression` or else a License classifier that names one license.
  For a package whose metadata names none it comes from the script's
  overrides. A license that the script cannot tell is an error that names the
  package.
- Each entry is `{name, version, ecosystem, license, direct}`, sorted by name.
  `scripts/ci` fails when the file differs from what the script would write.
  The Bitwarden CLI and the packages it brings along are named beside the
  table. Bitwarden, Inc. licenses them under its own terms.

## Normalizing

[Header menu](reference/ui.md#header-menu) and [Cell
menu](reference/ui.md#cell-menu) say how a user renames and merges.

- Rename a custom field across every item that has it with `renameField`. It
  takes `from` and `to` along with the items. Every field of that name in an
  item is renamed, and its type and position are kept. An item that already
  has a field named `to` is a merge conflict (409 with `mergeConflicts`),
  because that is a merge.
- Merge columns with `mergeColumns`. It takes `sources` and `target` with the
  items. Its `resolutions` map an item id to the column to keep.
- Items with values in more than one of the merged columns are listed for a
  manual choice instead of being overwritten. Without a resolution the answer
  is 409 with `mergeConflicts` (the item id and the columns holding values)
  and nothing is written. The UI sends a merge without choices first. It asks
  per listed item and sends the merge again with the choices.
- A resolution is read only where it is needed. Values not kept are removed.
  A field that moves into a field the item lacks is renamed in place, so it
  keeps its type and position.
- A merge into a built-in slot is allowed when the item's type has that slot.
  An example is `fields.username` into `login.username`. For other types it is
  a type conversion (see Writes) and goes through the template mapping UI. The
  merge is then a 422 that says so.
- Near-duplicate hints group column names that are equal after case folding
  and trimming whitespace. `GET /api/near-duplicates` lists the groups of field
  columns with their `key` and `columns`.
- Merge item into item moves chosen leaves of item A into item B and then
  trashes A. It is `mergeItems` with the mapping `{target column: source
  column}`. The tool note is never A. Values that are not moved stay in A, so
  they are in the trash and the backup.
- `mergeItems` takes `append` with the target list columns (`login.uris`) that
  keep B's entries and get A's entries added instead of being replaced. B's
  URIs come first and then A's URIs that B lacks, each with its own match
  setting.
- A column in `append` that is not a list column or not in the mapping is
  a 422. The UI appends by default, while the MCP tool `propose_merge_items`
  always replaces.
- The secret rule applies, so no merge may move a secret into a non-secret
  coordinate. A new field made by a move gets the type of its source, so a
  hidden value stays hidden.
- `bw` itself adds `<name>: <value>` to the password history for every hidden
  field that an edit renames, changes or removes. It keeps at most 5 entries.
  So a rename or merge of hidden fields can push the oldest entries out of an
  item's history, and the backup still has them.

## Templates

[Templates](reference/ui.md#templates) describes the Types page and the
template editor, and [the template tutorial](tutorial/template.md) walks
through them.

- A template has a name and an item type, an optional description and a list
  of coordinates. A custom field coordinate also carries its field type (0 text,
  1 hidden or 2 boolean). A coordinate can be a field name pattern (`"kind":
  "fieldPattern"`) as below.
- A text coordinate can carry a `"datatype": ID` (see Datatypes). A template
  without datatypes is made, stored and scored as if datatypes did not exist.
- Any coordinate (`name` too) can carry `"optional": true`. The key is left
  out when false, just as `datatype` is left out when there is none. So a
  coordinate without the key is required.
- Templates are stored in the tool note as its `templates` section (see The
  tool note).

  ```json
  {"version": 1, "templates": [
    {"name": "Computer", "type": 2, "coordinates": [
      {"kind": "field", "name": "user", "fieldType": 0},
      {"kind": "field", "name": "user password", "fieldType": 1}
    ]}
  ]}
  ```

- The allowed coordinates are `name` and `notes` and the built-in leaves of
  the template's type. Custom fields and field name patterns are allowed too.
  Names are unique ignoring case and surrounding spaces. A coordinate appears
  once per template, and a pattern once by its text ignoring case.
- The description is free text of at most 500 characters. Otherwise the
  answer is 422 "A description holds at most 500 characters". It is stored as
  `"description": "..."` and left out when empty. `GET /api/templates` and
  MCP's `list_templates` answer it when it is set.
- Matching ignores the description, like the description of a datatype. So
  do the fill score, the order of several matches and the duplicate warning.
- Items carry no marker, so membership is duck typed. An item matches a
  template when it has the template's type and a non-empty value for every
  required coordinate. A required pattern needs that value in at least one
  field that fits it.

### Fill score

The fill score is computed per item and template to find near misses. The
item type counts as one more thing to match.

- `total` is the template's required coordinates plus 1. `filled` is the
  required coordinates the item has filled, plus 1 when its type matches
  (`typeMatches`). The score is filled/total.
- A full match has `filled` equal to `total`, so it still needs the type.
  `missing` lists the template's required coordinates that the item has no
  value for as grid column ids. It is empty for a full match. Column names are
  not secret, so MCP carries them too.
- Every required coordinate with a datatype adds a format term. It adds 1 to
  `total` and its format score to `filled`. The format score is the value's
  score or the lowest one over the URIs for `login.uris`.
- The format score is 0 for a missing value and for one longer than the value
  limit. A boolean field in the item scores 0 too.
- An optional coordinate with a datatype adds its term only when the item has
  a value for it. Each term is floored to two decimals, so a partial value
  never counts as a whole point.
- A full match still needs `filled` equal to `total`. So a value off its
  format makes the item a near miss, with the value not in `missing`. An
  unknown or broken datatype counts as no datatype.
- Among several full matches the one with the most required coordinates wins.
  After that comes the one with the most format terms and then the one with
  the most optional coordinates that the item has filled. The first listed
  wins the last tie.
- A near miss has at least 2 matched and is not a full match. The grid's
  `template` column shows the match or else the best near miss. The highest
  score wins, and on a tie a matching type comes first.

### Duplicates and optional coordinates

- Two templates with the same item type score alike for every item when their
  coordinates are the same as a set. Each coordinate then counts by its column
  and its datatype and by whether it is optional. So the one listed first
  takes every full match and the other never gets an item. The order of
  the coordinates and the field types do not change a score and are ignored.
- A pattern compares by its text ignoring case. So two templates that are
  alike but for the pattern are no duplicates. Nor is a pattern the same as a
  field coordinate of that very name.
- A duplicate is a warning and not a refusal. Each template that duplicates
  one listed before it gets one warning that names the first template of its
  kind. It reads `LATER: same type and fields as EARLIER, which is listed
  first and takes every match.` The `warnings` of `GET /api/templates` carry
  them. The editor shows the same warning for its draft before saving.
- Optional coordinates are for items that have a field only sometimes. An
  optional coordinate is not needed for a full match. Its presence adds
  nothing to `total` or `filled`, and it is never in `missing`. With a
  datatype and a value in the item it adds its format term like a required
  one.
- A template needs at least one required coordinate (422 "a template needs at
  least one required field"). So an item whose type alone matches never
  matches a template of only optional coordinates. A template with no
  coordinates at all is matched by its type alone. Fields outside the template
  stay ignored.
- For MCP a secret column's format term is its presence term, which is 1 when
  set and 0 when not. It has no `formats` entry either. So MCP learns nothing
  about a secret beyond its presence, and a pattern can never be an oracle on
  one.
- What counts as secret goes by the field's type in the item. When the item
  has no such field it goes by the template's `fieldType`, and for a pattern
  by each field's type in the item. So MCP can call an item a full match that
  the UI calls a near miss.
- The UI shows a hint for template design. The most copied values belong in
  the built-in login slots (`login.username`, `login.password` and
  `login.totp`). The browser extension copies those straight from the item
  list. Custom fields need the item opened, and autofill matches them by name
  against a form input's name or id.

### Field name patterns

A template coordinate can match custom fields by a name pattern. So a template
can say that fields named like `user_*` exist and fit a datatype. A pattern is
stored as its own kind as `{"kind": "fieldPattern", "pattern": "user_*",
"fieldType": 0}`, with `datatype` and `optional` as on any coordinate. A `*`
in the name of an ordinary field coordinate stays a literal character.

- The syntax is a glob where `*` matches any run of characters (also none)
  and everything else is literal. It is matched against the whole field name
  ignoring case and surrounding spaces. A pattern needs at least one `*` and
  some other character (422 "a pattern needs a * and some other character").
- A pattern's field type is text or hidden and never boolean, since a boolean
  has no format to check. Two patterns of one template whose text is the same
  ignoring case and surrounding spaces are refused like a coordinate listed
  twice. A pattern and a field coordinate of the very name `user_*` are two
  coordinates.
- A pattern looks at the item's custom fields that have a value. It leaves out
  those that an exact field coordinate of the template names, since they
  belong to that coordinate. A field that two patterns fit counts for the
  first one listed only.
- A required pattern needs at least one such field, while an optional one is
  satisfied by none. It counts as one coordinate in `total` and `filled`
  however many fields fit it. `missing` names it as `fields.user_*`.
- Every field that the pattern has is scored against the datatype. The
  pattern's format term is the lowest of them. Its `formats` entry is the
  field that scored lowest, under that field's own column (`fields.user_b`).
- A required pattern without a field adds a term of 0 under `fields.user_*`.
  An optional one without a field adds no term. The kept format results and
  the Statistics rows go by the real field's column.
- What counts as secret goes by each real field's type in the item. The
  pattern's `fieldType` is used only when New item creates a field for it,
  under the name the user types (one field per pattern).
- Fields outside the template stay ignored. So a pattern says that fields
  named like it exist and fit. It does not say that every custom field of the
  item follows it.

### Routes and `applyTemplate`

- `GET /api/templates` answers `{"templates": [...], "revisionDate": ...,
  "toolNoteId": ..., "toolNoteName": ..., "saving": bool}`. The
  `revisionDate`, the id and the current name are the tool note's. `GET
  /api/grid` carries `toolNoteId` too, so the grid knows the note's row by id
  and never by name.
- Notes that hold no valid templates read as an empty list plus `error` and
  match nothing. `warnings` is there only when there are any. It names each
  coordinate whose datatype is unknown or broken (a pattern as `the pattern
  fields.user_*`) and each duplicate.
- While a save is pending or committing the route answers the templates as
  those saves leave the stored ones, with `"saving": true`.
- `PUT /api/templates` with `{"changes": [...]}` changes single templates by
  name (see `setToolNoteSection` in Writes). It validates the changes on the
  templates as the saves under way leave them. An invalid template gets 422,
  and so do a name taken and one that no template has. Nothing is queued
  then.
- Otherwise the route queues a `setToolNoteSection` of `templates` with the
  summary `Save the templates`. It is a `ui` entry that is allowed to touch
  the note. The route starts a commit at once in the background and answers
  202 `{"entryId": N, "templates": [...]}`.
- A whole list (`{"templates": [...]}`) is a 422 that says the templates are
  saved as changes. A `revisionDate` in the body is accepted and ignored.
- The Types page sends an `addTemplate` for a new template and a
  `removeTemplate` for a delete. For an edit it sends a `setTemplate`, after a
  `renameTemplate` when the name changed. `moveTemplate` exists in the API
  only.
- `GET /api/template-matches` answers per item id with the tool note left
  out. Each answer is `{"match": name or null, "scores": [{"template",
  "score", "typeMatches", "filled", "total", "missing"}]}` with one score per
  template in list order.
- A score with format terms also has `formats` with one entry per coordinate
  that has a datatype. The entry is `{"column", "datatype" (its name),
  "score", "distance", "firstEdit", "expected", "tooLong"}`. The UI gets
  secret columns too with `firstEdit` and `expected`, but never a character of
  the value.
- `GET /api/grid` carries the vault's custom fields as `fields` (`name` and
  `fieldType`) for the editor's pick. That is every field name that some item
  has, also with an empty value. The tool note and linked fields are left out.
  Where items disagree hidden wins (the secret rule), and otherwise the most
  common type does.
- The operation `applyTemplate` takes `itemId`, `revisionDate` and
  `template` by name along with a `mapping` `{template column: source
  column}`. Every target must be a coordinate of the template.
    - With the same type the mapped values move and unmapped leaves stay. A
      custom field source is removed, a built-in source is cleared and `name`
      stays.
    - With another type it is a `convertItem` with the same mapping.
- Either way every template field that the item lacks is created empty with
  the template's field type. An optional one is created too, so the item shows
  the slot. A new target field gets the template's field type.
- A field name pattern creates no field, as there is no name to give it. It is
  no target of the mapping either. The secret rule applies, but a text value
  into a hidden template field is allowed.
- Committed from the queue the operation puts the template's required secret
  columns that the item has empty afterwards on the to-fill list. A required
  hidden pattern adds nothing there.
- Convert to template maps the item's leaves onto the template's coordinates.
  It leaves unmapped leaves as they are. A secret into a non-secret coordinate
  and one value in two coordinates are refused in the dialog. A coordinate
  mapped onto itself is left out of the mapping.
- The type guidance under the editor's type select is in
  `frontend/src/templates/typeGuidance.ts`. So the user picks types the same
  way every time.

## Datatypes

A datatype is a named pattern that the user creates, and no presets ship. It
is defined once and used by template coordinates.
[Datatypes](reference/ui.md#datatypes) describes the Datatypes tab and its
editor.

- A datatype is for a value of the right shape. It says how well a value
  matches its format but never that a value is right. A pattern checks shape
  rather than meaning, so the wording is always "matches format" and never
  "valid".
- bwtidy never writes anything about datatypes into items. Removing a datatype
  leaves every item as it was.
- A datatype applies to text coordinates. These are `name` and `notes` and the
  text built-in slots of the template's type. `login.uris` counts as well,
  where each URI is scored and the lowest counts. Custom fields of type text
  or hidden count too.
- A datatype never applies to a boolean field or a number. A template that
  puts a datatype on those is a 422 that names the coordinate.
- Datatypes are stored in the tool note as its `datatypes` section, which is a
  list. The parser is in `sections/datatypes.py`. An entry looks like this.

  ```json
  {"id": "1f0c9a3e", "name": "PIN", "pattern": "\\d{4,8}",
   "description": "", "ignoreCase": false,
   "examples": ["1234"], "counterExamples": ["12"]}
  ```

- `id` is 8 random hex characters made by bwtidy. Names are unique after case
  folding and trimming. Examples must score 1 and counter-examples below 1,
  with at most 50 of each.
- The checks run when the section is queued or written and when it is
  loaded, as the note is untrusted too. A datatype that fails on load is
  kept and marked broken. It scores nothing and counts as no datatype.
- A broken datatype goes only by being fixed or removed, since a save with one
  in it is refused. A section that is no list reads as none with the problem
  in `error`.
- `"wholeValue": false` is stored only for a datatype whose pattern may match
  part of a value. Without the key the pattern matches the whole value.
  Another value than true or false is refused, and `wholeValue: true` is
  never written.
- A template coordinate names a datatype by `"datatype": ID`. An unknown id
  counts as no datatype, and `GET /api/templates` names it in `warnings`. So a
  template survives a datatype that went away by another way than the API.

### Scoring

- Matching runs on our own automaton engine (`patterns.py`) and never on a
  backtracking regex engine. The score is an edit distance. d is the least
  number of inserted, deleted or substituted characters that turn the value
  into a full match. The score is `1 - d / max(len(value), shortest match
  length, 1)`.
- A full match scores exactly 1. A score in an answer is floored to two
  decimals. For example the value `12a4` against `\d{4}` needs one
  substitution, so d is 1 and the score is `1 - 1/4 = 0.75`.
- With `wholeValue` false the characters before and after the matched part
  are free. Then d is the least number of edits that turn some part of the
  value into a full match. len(value) in the score is the length of that part,
  so `\d{4}` matches `id 1234` fully.
- A `^` first in the pattern takes away the free characters before. An
  unescaped `$` last takes away those after. With `wholeValue` true both
  anchors are allowed and change nothing, and anywhere else they are a pattern
  error. The free characters cost no automaton states, so the limits and the
  bound on the work stay as they are.
- The editor has typing rules for the anchors. A `^` first and an unescaped
  `$` last tick Whole value and are taken out of the typed text. While the box
  is ticked either one alone goes too, as it is redundant. With the box
  unticked one alone stays and anchors only that side.
- `firstEdit` is a position in the value and `expected` holds text from the
  pattern only. So neither shows a character of the value.
- Results are computed after every load of the vault in RAM. That is also how
  a change of the datatypes or the templates arrives. The results are kept in
  memory only (`Formats.results`) by item id and column along with the
  datatype's pattern and flags.
- They are never keyed by a hash of a value and never logged. The next load
  drops them, and so does a logout.
- MCP lists only the datatypes that a non-secret column uses. It sees format
  results for non-secret columns only (see MCP and the queue). No MCP tool
  proposes a datatype or a corrected value. The editor warns that an agent can
  read the pattern and the examples, so they must be made-up values.
- In the grid a cell's datatype is the datatype of its column in the template
  that the row's `template` column shows. That is the row's match or its best
  near miss. A cell with a queued value has no format mark, as the result is
  about the vault's value.
- A secret column shows the numbers and positions only. A value off its format
  saves like any other. The text under an editor is a warning and never blocks
  (only New item blocks, see Grid). The value being typed is in no query
  cache.

### The routes

- `GET /api/datatypes` answers `{"datatypes": [...], "saving": bool}` and
  `error` when the section cannot be read. While a save is under way it lists
  what that save holds. Each datatype has these keys:
    - the stored keys
    - `wholeValue` always, as true or false
    - the pattern facts `states`, `maxRepeat` and `shortest` (null when
      broken)
    - `broken` with the reason or null
- `PUT /api/datatypes` with `{"changes": [...]}` changes single datatypes by
  id (see `setToolNoteSection` in Writes). It validates the changes on the
  datatypes as the saves under way leave them. A failure is a 422 and nothing
  is queued.
- Otherwise the route queues a `setToolNoteSection` of `datatypes` with the
  summary `Save the datatypes`. It starts a commit at once and answers 202
  `{"entryId": N, "datatypes": [...]}`.
- A new datatype comes without an `id` and gets one here, and an id in an
  `addDatatype` is a 422. Unknown keys such as the facts of a GET are left
  out.
- Deleting a datatype that a template uses is a 422 that names the templates.
  That holds for a template that is stored or in a save under way. The write
  checks it again on the note as it is then. A whole list (`{"datatypes":
  [...]}`) is a 422 that says the datatypes are saved as changes.
- `POST /api/datatypes/check` is for the editor's live preview. It takes
  `{"pattern", "ignoreCase", "wholeValue", "examples", "counterExamples",
  "samples"}`. `wholeValue` is true when left out. Each list holds at most 50
  values, or the answer is a 422.
- The check answers the facts and a result for every example, counter-example
  and sample. For a pattern that the engine does not take it answers
  `{"error": {"message", "column"}}`.
- A result is `{"distance", "score", "firstEdit", "expected", "repeatsUsed",
  "steps", "tooLong"}`. Too long gives score 0 and `tooLong` true with null
  for the rest.
- `POST /api/datatypes/{id}/check` with `{"value"}` is for the grid's live
  editing. It answers the result of one value against a stored datatype. An
  unknown id gets 404 and a broken one 422.
- What these two routes take can be a secret being typed. It is never stored
  or logged, and a refused body is never echoed. So they read the body
  themselves rather than through FastAPI's validation. The frontend keeps
  what is checked in the dialog's state and out of the query cache.

## Statistics

The Statistics page ([Statistics](reference/ui.md#statistics)) opens from the
Datatypes tab and not from More actions. It is a tool for tuning patterns. A
normal user only needs it once a complicated pattern hits a limit of the
engine (`patterns.py`).

- `statistics.py` keeps one collector per worker process on the vault cache.
  For every item, column and datatype that the scoring reached it keeps the
  last scoring. That is these facts:
    - the item name and the column
    - the datatype's name and its pattern facts (`states`, `maxRepeat` and
      `shortest`)
    - the length of the value after NFC
    - the distance
    - the most copies used per counted repetition
    - the steps and the time it took
    - whether it was too long
- A column with several values (`login.uris`) has one row. It holds the
  longest length, the distance that counts and the most copies. The steps
  and the time are summed. A column without a value has no row.
- Each datatype has counters since the login. They count the values scored
  and the values too long, where each URI counts as one value. They also keep
  the slowest value's time and the total time of the last reload.
- The counters start over when the datatype gets another pattern, and they go
  when it goes. A broken datatype scores nothing and is not listed.
- The statistics live in memory only. Every load of the vault scores all
  values again, so it replaces the rows. A logout empties it all.
- A worker exits with its session, so the numbers are per account and start
  empty with each login. Nothing of them is logged or reaches SQLite.
- The statistics never hold a value or a character of one, only lengths and
  counts. The checks behind the live previews do not count, as they score no
  item.
- MCP cannot reach the statistics. No tool reads them and no answer carries any
  of them. The length of a secret is known to this page only, which needs the
  session.
- `GET /api/statistics` answers `{"limits", "reloads", "datatypes", "rows",
  "rowCount"}`. These are the limits from `patterns.py` and the number of
  reloads in this session. Each datatype comes with its facts and counters.
  The rows are the slowest 500 first, and `rowCount` is the number kept.

## MCP and the queue

[What an agent can do](explanation/mcp.md) says what an agent sees and
proposes for a user. [MCP tools](reference/mcp-tools.md) has each tool's
parameters and the description that the agent reads. `scripts/mcp-reference`
writes that page from the tools the server registers.
[Queue](reference/ui.md#queue) describes the Queue page, and [Connect an MCP
client](how-to/mcp.md) covers the switch and the token.

### The server, the switch and the token

- The MCP server runs inside the backend process because it holds the
  unlocked session. It serves streamable HTTP at `/mcp` on the web port and is
  built with the official `mcp` Python SDK. It is stateless, so a backend
  restart leaves no MCP session for the agent to drop.
- Only requests addressed to 127.0.0.1, localhost or `::1` are served. That is
  the SDK's DNS rebinding protection, so a web page cannot reach the server
  through a name of its own. `BWTIDY_MCP_HOSTS` adds each NAME to the allowed
  hosts (`NAME` and `NAME:*`) and `https://NAME` to the allowed origins.
- The switch is `POST /api/mcp` with `{"enabled": bool}`, which needs the
  session and answers `{"mcpEnabled": bool}`. `GET /api/session` reports
  `mcpEnabled`. The switch is off after every login.
- Turning it on needs a token that exists and has not expired. Otherwise the
  answer is 409 with one of these:
    - `{"detail": "Create an MCP token first: MCP token menu, Create."}`
    - `{"detail": "The MCP token has expired. Replace it first: MCP token menu,
      Replace."}`

  Turning it off always works.
- While MCP is disabled or locked every tool returns the text "bwtidy MCP is
  disabled. A logged in user must enable it in the web UI." and nothing else.
  That holds even for an argument error. A timed out session counts as locked
  at once.
- Every request to `/mcp` needs the header `Authorization: Bearer TOKEN`. A
  request without it, with a wrong token or while no token exists gets 401
  "bwtidy MCP needs a token. Create one in the web UI." It also gets
  `WWW-Authenticate: Bearer`, and all of this happens before the MCP SDK sees
  the request.
- With the right token the switch still decides whether the tools work.
  Several accounts share one URL, and an agent must reach only the account
  that its token belongs to. The gateway routes by the token's hash (see
  Gateway).
- `POST /api/mcp/token` needs the session and makes a new token
  (`secrets.token_urlsafe(32)`). It takes `{"days": N}` with an integer from 1
  to 365 or `{"days": null}` for no expiry. Without `days` the default is 90.
- It answers 201 `{"token", "createdAt", "expiresAt" or null}`, which is the
  only time the token is answered. A new token replaces the old one, which
  stops working at once.
- Only the token's sha256 is stored. It goes into `mcp-token.sha256` in the
  data directory, written atomically with mode 0600. Each line ends in a
  newline. These are the lines:
    - the hex
    - the expiry, which is a UTC ISO timestamp or `never`
    - the creation time, since not every copy keeps the file's own time
- `DELETE /api/mcp/token` needs the session, removes the file and answers 204.
  `GET /api/mcp/token` answers `{"createdAt", "expiresAt", "lastUsedAt"}` and
  404 without a token. It never answers the token or its hash.
- `GET /api/session` reports `mcpToken`, which says whether the file exists
  (also for an expired token). It is false while locked. The check compares
  the hashes with `hmac.compare_digest`.
- From its expiry on a request with the token gets 401 "bwtidy MCP token
  expired. Create a new one in the web UI." The worker and the gateway both
  answer that, and neither names the account. Both compare the hash first in
  constant time and then the time.
- The worker keeps the time of the last accepted MCP request in RAM and in its
  SQLite settings (`mcp_token_last_used`). It updates it at most once a
  minute. A new token and Remove forget it.
- The expiry chip warns once 3 days are left or 15% of the token's lifetime,
  whichever comes first. The lifetime is the expiry minus the creation time.
- The token dialog holds no commands. The commands for each client are on
  `docs/how-to/mcp.md`, which the dialog links at `/docs/how-to/mcp/`. So
  there is one place to keep them right. The token's field has
  `data-shot-hide`, so the docs screenshot shows no token.

### The tools

- The tool note is invisible to MCP in every tool. It is not listed and
  `get_item` says not found. A proposal that names it is refused as an
  unknown item.
- An unexpected error reaches the agent and the log by its type name only,
  since its text could carry a value.
- `list_items` and `get_item` never answer a secret value. They answer only
  `secretColumns`, which are the secret coordinates that are set.
- The `text` filter matches a name, a file name or any non-secret value. It
  never matches a secret one, so the filter is no oracle. Of `login.uris` it
  matches the uri text only and never a match word.
- `attachments` is a list of `{"id", "fileName", "size"}`. The size is the one
  Bitwarden lists, which is the encrypted size. The list never holds the
  contents, the url or a key. No tool reads or answers an attachment's
  contents.
- `login.uris` in those answers is a list of `{"uri", "match"}`. `match` is
  the word for Bitwarden's setting (see Coordinates) or null for the client's
  default. The grid and every
  other answer keep the plain strings.
- The secret rule holds for formats too. The fill scores count a non-secret
  column's format with its score and carry its entry in `formats`. A secret
  column counts as set or not set only and carries nothing. The engine does
  not even run on it for MCP.
- So the answers are byte for byte the same whatever a secret value looks
  like, as long as it is set. No pattern is an oracle on a secret.
- `list_datatypes` lists every datatype that is not broken and that at least
  one non-secret template coordinate uses. Its `usedBy` holds all the
  coordinates that use it, secret ones too. A datatype that only secret
  coordinates use or that nothing uses is not listed.
- A field coordinate counts as secret when the template says hidden. It also
  counts as secret when any item of the vault holds a field of that name as
  hidden, which is the safer of the two. An example typed as a real value of
  a secret column would otherwise reach the agent.
- `list_templates` names a coordinate's datatype as `"datatype": NAME`, so the
  agent never sees a datatype's id. It names a secret coordinate's datatype
  too, although `list_datatypes` may not list it. The name says nothing about
  a value. Just as in the score a coordinate whose datatype is unknown or
  broken has none.
- `list_proposals` lets an agent see what the user rejected so that it does
  not propose it again. It answers the entries of source `mcp` only, newest
  first and at most 100. Each is `{"id", "summary", "status", "createdAt",
  "decidedAt", "reason"}`, and `summary` is the one the propose call answered.
- `reason` is null for most entries. For a failed entry it is one fixed word
  by its result's error.

  | The result's error | `reason` |
  | --- | --- |
  | a conflict | `conflict` |
  | a merge conflict | `merge conflict` |
  | a failed write | `write failed` |
  | a cut off commit | `interrupted` |
  | an operation that no longer fits the vault | `invalid` |
  | anything else | `failed` |

  `reason` is never a detail or a diff, and never an operation or a value. A
  detail can carry a value. A silent refusal reads as an entry that the user
  has not decided yet (below).
- `end_session` ends the session like `DELETE /api/session`.
  `log_out_of_bitwarden` ends it and logs the CLI out like `POST
  /api/bw-logout`. Like every tool they work only while a session runs and
  MCP is on.
- Each of these two answers a short text first and does the work 0.5 seconds
  after the answer (`BwtidyMcp.after_answer`). So the answer goes out before a
  worker starts its exit, and a clean stop waits for that work
  (`BwtidyMcp.settle`). The tools carry `destructive_hint`, and the
  instructions tell the agent to call them only when asked to.
- Propose tools never write. They add an entry to the queue and return its id
  and summary. They take item ids and column names but never values. The
  exceptions are free values such as a new item name or a folder name and
  URIs.
- A propose tool reads the items' current `revisionDate`s and shown values
  itself. An optional `reason` is appended to the summary.
- `propose_rename_folder` and `propose_delete_folder` name the folder as
  `list_folders` does and send its name as `shownName`. An unknown name or one
  that two folders have is an error.
- `propose_set_uris` and `propose_set_uri_match` propose a `setUris`. They
  send the item's URIs as the agent's view held them as `shownUris`.
- `propose_set_uri_match` gives the match to every URI that `uri_pattern`
  matches and leaves the others. The pattern is a shell glob over the whole
  URI with case ignored, and without one every URI matches. Items with no URI
  whose match it changes are left out, and none left is an error. It is one
  queue entry with every item.
- The summaries are `Set the URIs of "NAME" (N)` and `Set URI match MATCH on N
  URIs of M items`. An unknown match word, an empty uri or a regex that does
  not compile comes back at once and nothing is queued. A uri equal to a
  secret value of the item is a silent refusal.
- `propose_rename_attachment` sends the item's `revisionDate` and the
  attachment's name now as `shownFileName`. An attachment id that the item
  does not have is an error.
- The item type is one word in every tool: `login`, `note`, `card`,
  `identity` or `sshKey`, as the grid shows it and `list_items` answers it.
  The reason is that an agent copies the type of an item it listed into a
  proposal. `propose_create_item` and `propose_convert_item` also take
  `secureNote`, Bitwarden's own word, which an agent may know from `bw`.
- `propose_create_item` proposes a `createItem` with `generate_secrets` as its
  `generateColumns`. MCP never gives a secret value and has two choices per
  secret field:
    - Left blank it counts as not set in the fill scores and goes on the To fill
      in list.
    - Generated at write time it is a value that MCP can never read. The fill
      scores count it as set and nothing more.
- A type `sshKey` or a template of type 5 is refused at once. The private key
  is a secret that MCP never gives, so the item could only fail with "SSH key
  private key is required". The message points to `propose_convert_item` on an
  item that holds the key.
- `propose_convert_item` proposes a `convertItem` with a mapping `{target
  column: the item's column}`. With the mapping a secure note that holds a
  private key can become an SSH key item without the secret passing through
  MCP (`{"sshKey.privateKey": "notes"}`).
- The summary is `Convert "NAME" to TYPE` followed by the mapped columns as
  `(SOURCE to TARGET, ...)`, but never a value. What `plan_conversion` refuses
  reaches the agent as `refused: REASON` and nothing is queued.
- There is no propose tool for a datatype or a corrected value. Nor is there
  one for a generated value of an existing item or for a restore.

### The queue

An entry holds one operation of `POST /api/operations` (see Writes) with a
summary and its source (`ui` or `mcp`). Its status is one of these:

- `pending`
- `approved` while it is being applied
- `rejected`
- `applied`
- `failed`

The diagram shows how an entry moves between them.

```graphviz alt="A queued entry starts as pending, and a silent refusal starts as rejected. Reject moves a pending entry to rejected. An approval or a commit moves it to approved while its commit runs. Its own write moves it on to applied or failed. An entry that the commit did not reach goes back to pending after a failed backup, an unreachable vault or a cancel, and fails as interrupted after an unexpected error. Retry puts a failed entry back to pending and approves it at once. Dismiss or the age limit deletes a failed entry."
digraph {
  rankdir=LR
  nodesep=0.45
  ranksep=0.6
  fontname="sans-serif"
  node [fontname="sans-serif" fontsize=13 shape=box style="rounded,filled" fillcolor="#ffffff" color="#5c6bc0" margin="0.14,0.08"]
  edge [fontname="sans-serif" fontsize=12 color="#455a64"]
  queued [label="Queued" shape=plaintext style="" fillcolor=""]
  pending [label="pending" fillcolor="#e8eaf6"]
  approved [label="approved"]
  applied [label="applied" fillcolor="#f1f8e9" color="#558b2f"]
  failed [label="failed" fillcolor="#fff3e0" color="#e65100"]
  rejected [label="rejected"]
  deleted [label="Deleted" shape=plaintext style="" fillcolor=""]
  queued -> pending
  queued -> rejected [label="silent refusal"]
  pending -> rejected [label="Reject"]
  pending -> approved [label="approve or commit"]
  approved -> applied [label="its write is done"]
  approved -> failed [label="conflict, invalid,\nwrite failed or\ninterrupted"]
  approved -> pending [label="not reached: a failed backup,\nno vault or a cancel"]
  failed -> pending [label="Retry"]
  failed -> deleted [label="Dismiss or\nthe age limit"]
}
```

<!-- markdownlint-disable MD038 -->

- An entry stays `approved` only while its commit runs. It is `applied` as
  soon as its own write is done, before the rest of its batch is written.
- Agent proposals wait for the user, who approves or rejects them one by one
  or in batches. An approved batch is one operation with one backup and then
  the writes. Its entries are marked `approved` in one transaction once the
  diffs they keep are computed. So a failure there leaves every one of them
  `pending`. UI edits are committed on their own (see Auto-commit below).
- Enqueueing checks the operation against the vault in RAM at once. It checks
  the shape, the `revisionDate` and merge conflicts as well as everything the
  write path refuses. The write path checks it again at approval.
- Enqueueing refuses any operation that touches the tool note, except
  bwtidy's own section saves. From an agent it also refuses a `setLeaf` into a
  secret column and a `createItem` with `secretValues` ("the queue never takes
  a secret value"). The entry is stored in SQLite, which never holds a secret
  value. From source `ui` those two are queued encrypted (see Hidden values
  below).
- A `setLeaf`'s column is secret when it is so on the item in the vault. It
  is also secret when it is so on the item as the earlier queued entries of
  that item leave it. Those entries are its `setLeaf`s, `removeLeaf`s and
  `generateLeaf`s along with its `setUris` and `renameField`s.
- `POST /api/queue` with `{"operations": [...]}` queues them from source `ui`
  with one entry per operation in order. It answers 201 `{"ids": [...]}`. The
  entries of one request are stored in one transaction.
- They are checked exactly as the write path checks before writing but
  without a sync. A conflict gets 409 with `conflicts` or `mergeConflicts`,
  and an operation that is not allowed gets 422. Nothing is queued then. A
  `revisionDate` that bwtidy's own commit replaced counts as current (see
  Writes, Own writes).
- The summary names the kind, the items and the columns but never a value. A
  `createItem` is summarized as `Create login "NAME"` with the type's word.
  The types' words are login and secure note as well as card, identity and SSH
  key. From a
  template it is `Create "NAME" from template "TEMPLATE"`.
- The `createItem` summary ends in ` with generated values` with
  `generateColumns`. Otherwise it ends in ` with a generated password` with
  `generatePassword`.
- `GET /api/queue?status=...` lists the entries in id order with the
  operation, the result and a diff. The diff has one row per changed cell. A
  row has `itemId` and `itemName` with its `column`, `before` and `after`.
    - A secret value shows as `{"masked": true}` and a missing value as null.
    - A trashed item shows as the pseudo column `trash` that goes from false to
      true.
    - A `createItem` has one row per value it gives with `before` null and
      `itemId` null. When a password is generated it also has a
      `login.password` row that goes to `(generated)`.
- A pending entry's diff is computed against the vault now, and `diffError`
  says why when the operation no longer fits. A decided entry keeps the diff
  it was decided on. `valueGone` is true for a failed entry whose hidden value
  was deleted at a start.
- `POST /api/queue/approve` with `{"ids": [...]}` applies the pending entries
  as one operations call. An entry may be blamed for a conflict or a merge
  conflict, or for an operation that is no longer allowed. Then it fails with
  the reason, and the rest is applied without it with still one backup.
- When a write fails midway the entries before it are applied and the failed
  one fails. It gets the error `conflicts` and the item when it found its item
  changed right before its write. The ones after it go back to pending.
- A failed backup or an unreachable vault puts every entry back to pending.
  The answer lists the `applied`, `failed` and `pending` ids and the `backup`.
- `POST /api/queue/reject` with `{"ids": [...]}` rejects pending entries. Ids
  that are unknown or no longer pending are a 422, and nothing is decided.
- `POST /api/queue/dismiss` with `{"ids": [...]}` deletes the failed entries
  among them. The age ciphertext of a hidden value goes with the row through
  SQLite's `secure_delete`, so it does not linger in freed pages. The answer
  is `{"dismissed": [...], "notFailed": [...]}`. Nobody reads a record of a
  dismissed failure, so it is deleted rather than marked.
- `POST /api/queue/retry` with `{"ids": [...], "overwrite": false}` puts the
  failed entries back to pending in one transaction. It approves them at once
  as one batch with the same answer. A write failure (`writeFailed`) and an
  `invalid` entry are retried as they are.
- These are refused with 409, and nothing is retried then:
    - an entry that is not failed
    - one cut off during its commit (`interrupted`), since its write may be
      done
    - a merge conflict, which needs a choice per item (the merge is proposed
      again with one)
    - one whose value is gone after a restart
    - a conflict without `"overwrite": true`
    - a conflict whose item or folder is gone

  No ids or an unknown one are a 422.
- With overwrite a conflict is retried after a sync. Its `revisionDate`s and
  the values that it says the grid showed are taken from the vault as it is
  then. Nothing else of the operation changes. So it writes its own columns
  over the change made elsewhere, and the item's other values stay as they
  are now. The backup of that write holds the other value. These are the shown
  values:
    - `shownFolderIds`
    - `shownFavorite`
    - `shownCollectionIds`
    - `shownUris`
    - `shownFileName`
    - a folder's `shownName`
- A failed entry's hidden value does not outlive its worker. Every start first
  fails the entries left approved. Right after that each failed entry with an
  encrypted value gets the marker `(gone after a restart)` in place of the
  ciphertext (with `secure_delete`) and is marked `valueGone`. Such an entry
  can only be dismissed. A pending entry keeps its ciphertext, and the next
  unlock commits it.
- The worker deletes a failed entry that nobody dismisses once it is older
  than `BWTIDY_FAILED_KEEP_HOURS` hours. The default is 30 hours counted from
  its `decidedAt`, and the deletion works the same way as Dismiss. It runs when
  the worker starts and then every hour. Only failed entries go. A backup that
  a deleted failed entry's result named is no longer in use.
- Unread counts on the Queue page's tabs are the browser's alone. The seen ids
  are kept in `localStorage` under the key `bwtidy.seenQueueEntries`. Behind
  the gateway a `:` and the account's server and email follow, because each
  account's ids start from 1. A tab shown for 4 seconds without interruption
  marks its entries as seen. The backend knows nothing of what was read.

<!-- markdownlint-enable MD038 -->

### Auto-commit

- The backend commits all pending `ui` entries as one approve with one backup
  and one call of the write path. It does so when the oldest of them is at
  least `afterSeconds` old or their count reaches `maxEntries`. The defaults
  are 180 and 20.
- It commits only while the vault is unlocked and one commit at a time. It
  checks once a second. Entries queued during a commit wait for the next one.
  Entries from MCP are never committed automatically.
- A commit that cannot run (no backup or no vault) leaves the entries pending
  and is tried again 30 seconds later. A `ui` entry that fails at the commit
  gets status `failed` and the result that an approval gives. That is
  `conflicts` or `mergeConflicts`, or else `invalid` or `writeFailed`.
- `GET /api/queue/auto-commit` answers `{"afterSeconds", "maxEntries",
  "pending", "committing", "nextCommitAt", "commitCount", "phase"}`:
    - `pending` counts the pending `ui` entries.
    - `committing` is true for the whole commit (the reload included) and also
      for an approval.
    - `nextCommitAt` is an ISO time or null.
    - `commitCount` is the number of commits and approvals that ended since the
      worker started, but not one that left its entries pending. The frontend
      watches it to reload after one.
    - `phase` says what the write path does. Its values are `syncing` and
      `backingUp` as well as `writing` (with `done` and `total`) and
      `reloading`. It is null while no write path runs. A commit can take a
      while and the user sees where it is.
- `PUT /api/queue/auto-commit` with `{"afterSeconds", "maxEntries"}` saves
  them in the `settings` table. `afterSeconds` takes 1 to 3600 and
  `maxEntries` 1 to 1000, and other values get 422.
- `POST /api/queue/commit` commits every pending `ui` entry now, after a
  running commit has finished. It answers like an approval.
- Ending the session first commits the pending `ui` entries. That commit may
  not run, may fail or may be cancelled after 60 seconds. Then the entries
  stay pending (or failed), and the next unlock commits them at once. The same
  goes for pending `ui` entries found after a restart. Only RAM knows how long
  an entry has waited.
- A commit that is cut off keeps what it wrote, as each entry is `applied` as
  soon as its write is done. A commit is cut off when it is cancelled (a stop)
  or hit by an unexpected error. The entry whose write runs then fails as
  interrupted at once, and its write may or may not be done.
- The entries not started yet go back to `pending` after a cancel. After an
  unexpected error they fail as interrupted too, since `pending` would repeat
  that error on every tick of the auto-commit.
- An entry still `approved` at the next start was being committed when the
  process was killed. It gets status `failed` with the result `{"error":
  "interrupted", "detail": ...}` and is never retried. A write of the entry
  may be done, and a create could be made twice. Left `approved` for good it
  would show on no tab, and its ciphertext could not be dismissed.
- A clean stop of the backend waits for the commits under way before it ends
  the session (see Worker mode, Bounded stops).

### Hidden values

- On the first unlock the backend creates an age X25519 identity. It stores
  the identity in the `settings` table (key `hidden_identity`), encrypted with
  the master password like the backups (`pyrage.passphrase.encrypt`). Every
  unlock decrypts it into RAM, and it is wiped when the session ends.
- A secret `setLeaf` value from the UI is encrypted to that identity's
  recipient before it is stored. So is each of the `secretValues` of a
  `createItem` from the UI. The entry's operation then holds the armored age
  ciphertext in place of the value, and only the unlock pays the scrypt cost.
- Every API answer shows that value as `{"masked": true}` in the operation as
  in the diff. The value is decrypted only when the entry is written.
- A queued secret `setLeaf` whose field does not exist at the write creates
  the field hidden. One whose field an earlier entry of the commit creates as
  text fails (see Writes, `setLeaf` and field types).
- A login may re-encrypt the backups for a new master password. Then the
  identity is re-encrypted first (see The tool note, The backup passwords). An
  identity that opens with none of the listed passwords is replaced. The
  entries queued under the old one fail at the commit with the reason.

### Silent refusal

- Some MCP operations carry a value that the agent chose. They are a
  `setLeaf` and a `moveToFolder` as well as a `renameFolder` and a
  `setUris`. When that
  value equals one of the item's own secret values the operation is not
  refused with an error. That holds also for a secret value queued from the UI
  and not written yet.
- The value is a new name, a folder name or a uri. For a folder it is checked
  against the secret values of an item in it. Only four propose tools send a
  free value. They are `propose_rename_item` and `propose_move_to_folder`
  along with `propose_rename_folder` and `propose_set_uris`.
- Such an operation is stored in one INSERT as already `rejected`. The value
  is masked in the operation and the diff. The result is `{"error":
  "secretValue", "detail": ...}`. The agent's value and reason are not stored
  in plain. These are the summaries:
    - `Refused proposal for "<item name>": it would put a secret value of the
      item into <column>`
    - for several items `Refused proposal for N items: it would put a secret
      value of an item into folder`
    - for a folder `Refused proposal for the folder "NAME": it would put a
      secret value of an item in it into its name`
- The agent gets the entry id and the same summary that it would get for an
  accepted proposal. No answer, error or timing may depend on whether a value
  that the agent chose equals a secret. Otherwise a refusal would confirm a
  guessed password.
- The user sees the refused proposal in the Rejected tab. The same operation
  from the UI is still refused with the error.
- `list_proposals` shows the summary that the propose call answered, status
  pending and no decided time. That lasts until an entry that is not a silent
  refusal gets a decided time later than the refusal's. Such a decision is an
  approve or a reject, or an auto-commit or Commit now of any source.
- From then on the refusal is rejected with that decision's time as its
  decided time (`agent_decided_at`, set once). Until the user decides
  anything a refused proposal must read exactly like one that waits for them.
  Otherwise listing would confirm a guess. An entry that stayed pending for
  good would make the agent propose it again.
- The summary that the agent was answered can hold the guessed secret, such as
  a rename's new name or its reason. So every entry of source `mcp` keeps it
  as age ciphertext to the identity for hidden values. `list_proposals`
  decrypts each listed summary, refused or not. One queued under an identity
  that was replaced reads `(the summary cannot be read)`.

## UI edits and the auto-commit

[How edits are saved](explanation/edits.md) and [App
bar](reference/ui.md#app-bar) say what a user sees of the queue and the
auto-commit. MCP and the queue covers the backend side. This is what the web
UI does with it:

- Every UI write goes to `POST /api/queue` with source `ui`. A 409 or 422
  shows its reason, and the merge choices work against the 409. Nothing in the
  UI waits for a write. So dialogs are never greyed out and there is no
  progress bar for a save.
- While `ui` entries are pending or a commit runs the app asks for `GET
  /api/queue?status=pending` and `GET /api/queue/auto-commit` every second.
  Otherwise it asks every 10 seconds.
- An entry that leaves the pending list stays in the grid overlay until the
  commit ended and the grid reloaded. A status with a new `commitCount` and
  `committing` false tells that the commit ended. So a cell never flips back
  to its old value for the time of the commit. Then the grid, the templates
  and the change log reload.
- The spinner's text comes from the status's `phase`. There is no separate
  chip for the unsaved edits, as it would repeat the Queue button's badge.
- The current page's button in the app bar is not disabled. So it keeps its
  label color and a screen reader announces it as the current page
  (`aria-current="page"`). It does nothing when clicked and keeps its size. No
  button is current on Settings and Statistics.
- Failed `ui` entries that this browser has not reported yet show in a
  snackbar on every page. Their ids are kept in `localStorage` per account.
  The app checks for them after every commit and once when it starts, since a
  commit at the session's end may have failed while no tab was open.
- For a conflict the rows and cells get the conflict marks (see Grid). The
  cells of other failed entries get a tint until the snackbar is closed.
  Dismissing or retrying a failed entry on the Queue page takes back what that
  entry added, and the marks of other failed entries stay.

## Development and tests

Development and tests never touch anyone's vault on a Bitwarden server or the
host's Bitwarden CLI state. They run against a dev stack with a seeded
synthetic vault and never run the backend on the host. The developer guide
(`dev/README.md` in the repository) describes this along with the suites, the
screenshots and the docs build.
