# The demo

The public demo of bwtidy: a visitor clicks Start demo and gets a vault of
their own, seeded with the dev fixture, for one session. This page is the
design every part of the demo follows. When a part of the demo changes what
this page says, the page changes in the same commit.

The demo is its own image, built `FROM` the normal bwtidy image, adding the
Python project in the top-level folder `demo/` (package `bwtidy_demo`). The
normal image contains no demo code: the Dockerfile copies everything under
`backend/src` into it, so the demo code lives outside `backend/`. bwtidy
itself knows nothing of the demo. The demo wraps the gateway through the two
parameters of `create_gateway_app` (docs/spec.md, Gateway, Extra routes and
the exit hook): `routers` for its own pages and `on_worker_exit` to delete a
visitor's account when its worker exits.

## Components and networks

`compose.demo.yaml` runs five containers:

| Container     | Image                       | What it does                                                         |
| ------------- | --------------------------- | -------------------------------------------------------------------- |
| `init`        | the demo image              | writes the stack's secrets into the `secrets` volume, then exits      |
| `vaultwarden` | `vaultwarden/server:1.37.4` | the demo's own server, HTTPS with the self-signed certificate of `init` |
| `demo`        | the demo image              | bwtidy's gateway wrapped by `bwtidy_demo`, and the workers it starts  |
| `controller`  | the demo image              | the pool of ready vaults, and the deletion of Vaultwarden users      |
| `edge`        | nginx                       | the only published port, rate limits, proxies to `demo`              |

The version of Vaultwarden is the dev stack's (`compose.dev.yaml`), so the
demo runs against the server the tests run against. The demo image has bw
and Python already (it is the bwtidy image), so the controller is the same
image with another command.

Three networks:

- `back` (`internal: true`): `vaultwarden`, `demo` and `controller`.
- `front` (`internal: true`): `edge` and `demo`.
- `public` (a normal network): `edge` only.

`init` needs no network (`network_mode: none`). An internal Docker network
has no route out of the host, so only the edge can reach anything outside
the stack, and only the edge can be reached from outside. Who reaches what:

- `edge` reaches `demo` on its gateway port, nothing else. It is not on
  `back`, so it can reach neither Vaultwarden nor the controller.
- `demo` reaches `vaultwarden` (each worker's bw) and the controller's HTTP
  API.
- `controller` reaches `vaultwarden`: the registration, `bw import` and the
  admin API.
- `vaultwarden` reaches nothing: it answers on `back` and sends no mail, no
  push and no icon requests (see Vaultwarden settings).

Every container has a read-only file system, drops all capabilities (the
`demo` container keeps the six that `compose.yaml` gives bwtidy, the edge the
four nginx needs to bind port 80 and start its workers) and sets
`no-new-privileges`. What must be written is a tmpfs or a volume.

`depends_on` orders the start and, with `restart: true`, what compose
restarts when it makes a container anew: a new `vaultwarden` container is
empty, so `controller` and `demo` restart with it, and nginx looks the name
`demo` up once, so `edge` restarts with a new `demo` container. That holds
for what compose does (`up` after a changed setting). A container that
crashes restarts alone (`restart: unless-stopped`).

Containers on one network can reach each other's ports, so the controller
and Vaultwarden's admin page also check a token (see Secrets): the network
keeps the outside out, the tokens keep a worker or a bug in `demo` from
anything but what it is meant to call.

## Start flow

1. `GET /demo` serves the start page from the wrapper (the template
   `bwtidy_demo/templates/start.html`), on the same origin as bwtidy: what the
   demo is, that the vault is deleted when the session ends (with the idle
   timeout and `DEMO_SESSION_MAX` in minutes), never to enter one's own
   details, and one Start demo button. It has no input field. The button is a
   plain HTML form that POSTs to `/demo/start`: both cookies are
   SameSite=Strict, so the browser must get them in an answer to a same-site
   navigation, never a cross-site one. A start whose `Sec-Fetch-Site` is
   neither `same-origin` nor `none` answers 403 and takes no vault, so another
   site cannot use up the pool.
2. `POST /demo/start` first ends the demo of the browser's routing cookie,
   if it has a live one, as End demo does. It then asks the controller for a
   ready vault (`POST /take` with the controller token): its server
   (`https://vaultwarden`), its email and its master password. The password
   never leaves the `demo` process: no page, no log, no cookie. A server
   other than `DEMO_SERVER` fails the start.
3. Then, in the gateway's process (`app.state.gateway`):
   1. `key = await account_key(server, email, settings.allowed_servers)`.
      `BWTIDY_ALLOWED_SERVERS` is `https://vaultwarden`, the only server.
   2. `worker = await gateway.open(key, create=True)`.
   3. `worker.send` `POST /api/bw-login` with `{server, email, password}`
      as JSON (its `content` is an async iterable of bytes). The answer's
      `loginId` goes into the header `X-Bwtidy-Login` of the next requests.
   4. Poll `GET /api/bw-login/output?after=N` with that header, N the
      answer's `next`, until `running` is false. Anything but `exitCode` 0
      fails the start.
   5. `POST /api/bw-login/finish` with the header unlocks the vault. Take
      the `bwtidy_session` cookie from its `Set-Cookie`.
   6. `token = gateway.issue(key, None)` is the `bwtidy_account` routing
      cookie.

   Every answer of `worker.send` is read and closed, so no socket stays
   open.
4. Answer 303 to `/` with both cookies: HttpOnly, SameSite=Strict, Path=/,
   Secure per `BWTIDY_SECURE_COOKIES`.
5. The frontend opens the grid as it does after any login, unchanged.

A start that fails answers 303 to the start page with a note, and leaves no
account behind: the worker is stopped, its exit hook deletes the directory
and the Vaultwarden user (a start that got no worker releases the vault
itself). The query names one of three fixed notes, the page never shows a
text from the request:

| Query          | When                                   | The note                                                 |
| -------------- | -------------------------------------- | -------------------------------------------------------- |
| `?note=free`   | the controller has no ready vault (409) | "No demo vault is free right now. Try again in a minute." |
| `?note=full`   | a cap of the gateway (`LimitError`)    | "The demo is full right now. Try again in a few minutes." |
| `?note=failed` | anything else: the controller, the worker, the login | "The demo could not start. Try again in a minute." |

Below the button the start page shows the demo's usage (see Usage stats).

`GET /` and `GET /index.html` without a routing cookie of a live demo
account answer 303 to `/demo`, so a visitor whose demo ended lands on the
start page, never on the account form.

## Refused routes

An outer middleware of the wrapper (`app.add_middleware` after
`create_gateway_app`, so it runs before the gateway's proxy) answers these
from the browser with 403 and the plain text `The demo logs you in itself.
Start a new demo at /demo.`:

- `POST /gateway/account`: only `/demo/start` routes a browser.
- Every method on `/api/bw-login` and below it (`POST /api/bw-login`, `POST
  /api/bw-login/input`, `POST /api/bw-login/finish`, the output and the
  cancel): only the wrapper logs in, in process, never through the proxy.
- `POST /api/session` (the unlock): the visitor has no password to type,
  and a working unlock form would be a credential field.
- `POST /api/bw-logout` (every method).
- `POST /api/account/delete` (Delete my account, `api/account.py`, every
  method): the end of the demo deletes the account anyway. `POST
  /api/account/delete-data` stays.

`POST /api/session/heartbeat` and `DELETE /api/session` (Lock) stay: Lock
ends the session, and with it the demo.

The middleware compares the path with its empty and dot segments resolved
and without a trailing slash: the proxy forwards the raw path and httpx
resolves its dot segments, so `/api/./bw-login` would reach the worker as
`/api/bw-login`. The percent-encoded spellings are decoded by the server
before the middleware sees them.

`DELETE /gateway/account` (Switch account, and the end of Delete all data)
becomes End demo: the wrapper stops the account's worker
(`gateway.workers.stop`), which runs the cleanup below, and answers 204 with
the routing cookie and the session cookie cleared, as the gateway would.

`POST /api/mcp/token` is capped by rewriting, never refused: the wrapper
reads its small JSON body (more than 4 KiB answers 413) and passes it on
with `days` 1 when it asks for more, for no expiry (`null`), or leaves
`days` out (bwtidy's default is 90 days). An empty body becomes `{"days":
1}`. A JSON object is written anew, so the worker parses exactly what was
checked. Anything else (not an object, `days` 0 or not a number) goes on as
it is, and the worker refuses it. The answer's `expiresAt` shows the real
expiry. MCP itself works for the visitor's vault only, as in bwtidy: the
token names the account.

## Banner

The wrapper's router serves `/` and `/index.html` itself (a router wins over
the static files), with `Cache-Control: no-store`. It reads
`settings.static_dir/index.html` once at the start (a missing file or one
without `<body>` fails the start) and inserts a fixed banner
(`bwtidy_demo/templates/banner.html`) right after `<body>`: "Demo: your vault is
deleted when the session ends. Never enter your own details." with an End
demo button (a form that POSTs to `/demo/end`, which does what `DELETE
/gateway/account` does and answers 303 to `/demo`).

The frontend's shell (the outer `Box` of `App.tsx`) is `100vh` high, and a
`vh` height ignores its parent, so shrinking `#root` alone would cut off the
bottom of the grid. The inserted style gives the banner a fixed height
(`--demo-banner`), puts it in normal flow above `#root` and sets `#root >
div` to `height: calc(100vh - var(--demo-banner)) !important` (the
`!important` wins over the class MUI generates), so the whole app fits
below the banner without a second scroll bar. A demo e2e test checks that
the grid's last row and the page's bottom edge are visible. The banner is
outside React's root, so it stays through every page of the app.

## Docs note

The general docs stay free of demo text. The demo image serves the same
prebuilt site at `/docs/` (`BWTIDY_DOCS_DIR`), and the wrapper's middleware
adds the demo's note to the HTML of every `GET /docs/...` answer with status
200 (`bwtidy_demo/templates/docs_note.html`), right after the opening tag of
the theme's `md-content__inner` article (right after `<body>` if a page has
none). It says that every visitor gets a fresh sample vault, deleted when the
session ends (`DEMO_SESSION_MAX` at most, the idle timeout idle), and that
where a tutorial says to log in the reader uses Start demo. Its inline style
copies the colors of the site's own banner (`overrides/main.html`). On
`tutorial/mcp/` it also names the MCP address and that a token lives at most
1 day: `https://DEMO_HOST/mcp` when `DEMO_SECURE_COOKIES` is on (the host from
`BWTIDY_MCP_HOSTS`, which compose fills from `DEMO_HOST`), else
`http://` and the request's own `Host`, which is `localhost:PORT` as the
visitor reached it. Assets and other types stay untouched, and the answer is
`no-store` with conditional headers dropped, so the note never goes stale.

This is done at request time, not in `demo/Dockerfile`, because the MCP
address depends on the host and the port of a running stack, which no build
knows. One mechanism then serves every page, and it needs only a stable anchor
(the article class or `<body>`) in the site's HTML, not a copy of the site that
a build step must keep in step with the theme.

## End and cleanup

A demo ends when its session ends: the idle or heartbeat timeout, Lock, End
demo, MCP's `end_session` or `log_out_of_bitwarden`, or the hard maximum.
The worker then exits (`BWTIDY_EXIT_AFTER_SESSION`), and the gateway awaits
`on_worker_exit` after its own cleanup. The wrapper's hook:

1. Under `gateway.lock(key)`, and only while no worker runs in the
   directory (`gateway.workers.at(path)` is None), deletes the account the
   way `Gateway.delete_on_request` does: `gateway.accounts.delete(path,
   force=True)`, which also removes its backups directory, then
   `gateway.forget_key(key)`. The hook runs outside the gateway's own lock,
   so it can take it. A backups directory whose account directory is gone
   already goes too.
2. Tells the controller to delete the Vaultwarden user (`POST /release`). A
   502 (Vaultwarden refused) or no answer is tried again after 1, 2, 4 and 8
   seconds, then given up with an error in the log. Any other refusal is
   logged and not tried again.

The cleanup is idempotent: it releases each email once, however often the
hook, End demo, the hard maximum or a failed start call it, and deleting a
directory that is gone does nothing. End demo awaits it, so its answer comes
once the account is gone.

The hard maximum: the wrapper notes each demo's start once its login
succeeded, checks every 10 seconds and stops the worker
(`gateway.workers.stop`) of a demo older than `DEMO_SESSION_MAX`, whatever
the activity. The cleanup then runs through the hook like any other end.

At its start, before the gateway's own start, the wrapper deletes every
account directory left from before (they are all demo accounts), the ones a
create left under their temporary name and the backups directories without
an account, and asks the controller to delete the users of those in
`demo.bwtidy.test`. When the gateway stops, it stops every worker and their
hooks clean up. A gateway restart never leaves a vault behind.

### The wrapper's settings

The wrapper (`bwtidy_demo.gateway`, `bwtidy-demo-gateway`) reads the
gateway's own settings (`GatewaySettings.from_env`) and these. A missing or
unusable one stops it at the start with a message naming it:

| Setting                  | Default               | What                                                                 |
| ------------------------ | --------------------- | -------------------------------------------------------------------- |
| `DEMO_CONTROLLER_URL`    | none, required        | the controller's API, such as `http://controller:8100`             |
| `DEMO_SECRETS`           | `/secrets`            | the secrets volume, its `controller-token` must be readable and not empty |
| `DEMO_SERVER`            | `https://vaultwarden` | the demo's Vaultwarden. `BWTIDY_ALLOWED_SERVERS` must hold it and nothing else |
| `DEMO_SESSION_MAX`       | `2700`                | the hard maximum of a demo, in seconds                               |

`BWTIDY_STATIC_DIR` is required too: the banner is inserted into its
`index.html`. The secrets volume's variable is `DEMO_SECRETS`, the name
`init` and the controller read.

## Controller

The controller (`bwtidy_demo.controller`, `bwtidy-demo-controller`) keeps
`DEMO_POOL_READY` vaults ready, hands one out per start and deletes the
users of ended demos. It never holds more than `DEMO_MAX` vaults ready plus
handed out, and builds one vault at a time: each costs a bw process and
Vaultwarden's KDF, and its requests count against Vaultwarden's rate limit
for unauthenticated requests. Vaultwarden's data is temporary, so the pool
starts empty and fills at every start.

- A new vault: the controller draws an id, four words from `bw generate
  --passphrase --words 4 --separator -`, and makes the email
  `ID@demo.bwtidy.test` and a random master password. It registers the
  account (`bwtidy_demo.vaultwarden`) through Vaultwarden's two-step registration
  (`/identity/accounts/register/send-verification-email`, which answers the
  token itself without SMTP, then `/identity/accounts/register/finish`),
  with the client-side crypto a Bitwarden client does: the master key from
  the password with the server's KDF, the master password hash, the
  encrypted user key and the key pair. It then seeds the vault with bw in a
  fresh `BITWARDENCLI_APPDATA_DIR` of its own, trusting the stack's CA
  through `NODE_EXTRA_CA_CERTS`: `bw config server`, `bw login
  --passwordenv`, `bw unlock --raw`, `bw import bitwardenjson` of the
  fixture (`dev/fixture-personal.json`, its path in `DEMO_FIXTURE`) and `bw
  sync`, and deletes that directory. The password and the session key go to
  bw in its environment, never in its arguments, and bw's output is never
  logged.
- A vault that fails to register or seed is deleted through the admin API,
  and its id stays in the ledger. The next build waits a backoff (10
  seconds, doubled after each failure up to 5 minutes), so a broken step
  neither loops fast nor leaves a user behind.
- The ledger: a file on a persistent volume (`DEMO_LEDGER`, by default
  `/ledger/ids`) holds every id ever drawn, one per line. A drawn id already
  in it is drawn again, so an id is never reused, not even after a restart
  that emptied Vaultwarden. A new id is appended and synced to disk before
  it is used. bw writes noise on stderr without a server, so only its
  stdout counts, and an answer that is not four or more lowercase words is
  drawn again.
- Handing out: a ready vault goes to exactly one start and leaves the pool,
  then the controller makes a new one in the background.
- Deleting a user: through Vaultwarden's admin API. `POST /admin` with the
  token as a form field sets the `VW_ADMIN` cookie, which the controller
  keeps and reuses until it is refused: the admin login allows a burst of 3
  per 300 seconds. `GET /admin/users/by-mail/EMAIL` finds the user's UUID,
  `POST /admin/users/UUID/delete` (with `Content-Type: application/json`)
  deletes it. A user that is not there counts as deleted.
- Its HTTP API listens on `back` only, port 8100, and needs
  `Authorization: Bearer TOKEN`, the controller token from the secrets
  volume, on every request (compared in constant time, an unknown path gets
  401 too, and there are no docs pages). It never answers a password but to
  the start that takes the vault. No log line holds a password or a token:
  the logs name ids and counts.

| Route           | Body      | Answer                                                                         |
| --------------- | --------- | ------------------------------------------------------------------------------ |
| `POST /take`    |           | `{server, email, password}` of a ready vault, now taken. 409 with a text when none is ready |
| `POST /release` | `{email}` | 204 once the user is deleted and forgotten. 400 for an email outside `demo.bwtidy.test`. 502 when Vaultwarden refused: the vault stays taken and the caller releases it again |
| `GET /status`   |           | `{ready, taken, building, failed, max}`, counts only, `failed` since the start, `max` is `DEMO_MAX` |

A release of a user the controller does not know (one from before a
restart) deletes it all the same, which is what the wrapper's cleanup at its
start needs.

Its settings: `DEMO_POOL_READY`, `DEMO_MAX`, `DEMO_FIXTURE` (required),
`DEMO_LEDGER`, `DEMO_SECRETS` (by default `/secrets`) and `DEMO_SERVER` (by
default `https://vaultwarden`).

## Secrets

`init` (`bwtidy_demo.init`, `bwtidy-demo-init`) writes into the `secrets`
volume (`DEMO_SECRETS`, by default `/secrets`), each file in one rename:

| File                      | Mode | What                                                              |
| ------------------------- | ---- | ----------------------------------------------------------------- |
| `vaultwarden-admin-token` | 0600 | Vaultwarden reads it with `ADMIN_TOKEN_FILE`, the controller too   |
| `controller-token`        | 0600 | the controller's API token, the controller and the wrapper read it |
| `vaultwarden.key`         | 0600 | the server's TLS key, Vaultwarden reads it                        |
| `vaultwarden.crt`         | 0644 | the server certificate for the name `vaultwarden`, then the CA    |
| `ca.crt`                  | 0644 | the self-signed CA, trusted by the workers and the controller (`NODE_EXTRA_CA_CERTS`, bw refuses plain http) |

The CA's key is never written: it signs the server certificate and is gone,
so nothing can sign another certificate the stack trusts. Both certificates
carry the extensions a strict verifier asks for (Python's
`VERIFY_X509_STRICT`, on by default, and Node). The directory is 0755.

A volume that holds all five files already is left as it is. compose runs
`init` again at every `up`, also an `up` that only changes a setting and
leaves Vaultwarden running, and Vaultwarden reads its certificate and its
admin token only when it starts: new secrets under a running Vaultwarden
would cut the controller and the workers off from it. A volume that lacks a
file (the first start, or a write that was cut off: the server certificate
is written last) gets all five anew. For new secrets, stop the stack and
remove the volume (`docker volume rm PROJECT_secrets`, `PROJECT` being
`bwtidy-demo` unless `-p` names another), never `down --volumes`, which
removes the ledger and the usage stats too.

No secret ever leaves the stack, and none comes from outside: nothing is
configured by hand and nothing is shown. The tokens are readable by root
only. The certificate is readable by everyone, as the workers run as the
accounts' uids. The edge mounts no secret.

Vaultwarden's data is temporary (a tmpfs of `DEMO_VAULTWARDEN_DATA_SIZE`,
256 MB by default): a restart starts it empty. The account directories are a
tmpfs too (`DEMO_ACCOUNTS_SIZE`, 512 MB by default), and the wrapper deletes
what it finds at its start. Both sizes are all the visitors together can
fill. The id ledger, the secrets and the usage stats are the three volumes
that stay.

A restart of the controller alone forgets its ready vaults while Vaultwarden
keeps their users, unused, until Vaultwarden restarts. Restart the stack
(`up --force-recreate`), never the controller by itself.

## Vaultwarden settings

| Setting                        | Value                         | Why                                                   |
| ------------------------------ | ----------------------------- | ----------------------------------------------------- |
| `SIGNUPS_ALLOWED`              | `true`                        | the controller registers, it is never reachable from outside |
| SMTP                           | none                          | the registration answers its token without mail       |
| `WEB_VAULT_ENABLED`            | `false`                       | nobody uses it                                        |
| `SENDS_ALLOWED`                | `false`                       | nothing to share                                      |
| `EMERGENCY_ACCESS_ALLOWED`     | `false`                       |                                                       |
| `INVITATIONS_ALLOWED`          | `false`                       |                                                       |
| `ORG_CREATION_USERS`           | `none`                        | the fixture has no organizations                      |
| `PASSWORD_HINTS_ALLOWED`       | `false`                       |                                                       |
| `SHOW_PASSWORD_HINT`           | `false`                       |                                                       |
| `ICON_SERVICE`                 | `internal`                    | no request leaves the stack                           |
| `DISABLE_ICON_DOWNLOAD`        | `true`                        | no request leaves the stack                           |
| `PUSH_ENABLED`                 | `false`                       | no request leaves the stack                           |
| `USER_ATTACHMENT_LIMIT`        | small (in KB)                 | a visitor cannot fill the disk                        |
| `USER_SEND_LIMIT`              | small (in KB)                 | the same, should sends ever be on                     |
| `ADMIN_TOKEN_FILE`             | the token in the secrets volume | the controller's user deletion                      |
| `DOMAIN`                       | `https://vaultwarden`         | the name the workers use                              |
| `ROCKET_PORT`, `ROCKET_TLS`    | 443, the certificate and key in the secrets volume | bw refuses plain http               |
| `LOGIN_RATELIMIT_SECONDS`, `LOGIN_RATELIMIT_MAX_BURST` | 1, 1000 | every login comes from one address, the `demo` container |
| `UNAUTHENTICATED_RATELIMIT_SECONDS`, `UNAUTHENTICATED_RATELIMIT_MAX_BURST` | 1, 1000 | the registrations and bw's prelogin count here, all from two addresses |
| `ADMIN_RATELIMIT_MAX_BURST`    | 30                            | a controller that restarts a few times still logs in  |

Vaultwarden's limits count per address, and all its requests come from the
`demo` and `controller` containers, so its defaults (a burst of 10 logins,
then one per minute) would stop the pool and the starts for everyone at
once. The edge limits each visitor's address instead (see Limits and
timeouts).

Never save settings in Vaultwarden's admin page: it writes
`data/config.json`, which overrides the environment.

## Two guarantees against a real account

A visitor must never type the details of their real Bitwarden account into
the demo. Two independent guarantees hold, so either alone is enough:

1. No credential fields and refused login routes. The demo logs the visitor
   in itself, the start page has one button, and every route that takes a
   server, an email or a password from the browser answers 403 (see Refused
   routes). A visitor whose demo ended is sent to the start page.
2. No internet route from bwtidy. The `demo` container is on internal
   networks only, and `BWTIDY_ALLOWED_SERVERS` holds only
   `https://vaultwarden`. Even a bug that let a login through could not
   reach a real server.

## Limits and timeouts

The defaults, kept after measuring (Memory):

| Limit                          | Default    | Setting                                                    |
| ------------------------------ | ---------- | ---------------------------------------------------------- |
| vaults ready in the pool       | 2          | `DEMO_POOL_READY` (controller)                             |
| vaults ready plus handed out   | 6          | `DEMO_MAX` (controller)                                    |
| visitors at once               | 6          | `BWTIDY_MAX_WORKERS=6`                                     |
| starts in flight               | 2          | `BWTIDY_MAX_PENDING=2` (a start's directory is pending until its unlock) |
| idle session end               | 15 minutes | `BWTIDY_SESSION_IDLE_TIMEOUT=900` (seconds)               |
| closed tab                     | 2 minutes  | `BWTIDY_HEARTBEAT_TIMEOUT`, bwtidy's default 120          |
| a session at most              | 45 minutes | `DEMO_SESSION_MAX=2700` (seconds, the wrapper)            |
| MCP token lifetime             | 1 day      | the wrapper's rewrite of `POST /api/mcp/token`             |
| `POST /demo/start`             | per address: a burst of 5, then 6 a minute | `DEMO_START_BURST`, `DEMO_START_RATE` (the edge) |
| `/mcp`                         | per address: a burst of 40, then 10 a second | `DEMO_MCP_BURST`, `DEMO_MCP_RATE` (the edge) |
| a request body                 | 1 MB, 1 kB for a start | the edge's `client_max_body_size`               |

Every service also has a memory and a process limit, set in
`compose.demo.yaml` (`mem_limit`, with `memswap_limit` equal to it so a
container cannot swap past its limit, and `pids_limit`). A public demo shares
its host with other services, so a visitor cannot be allowed to take all of
its memory or start processes until it slows. The defaults rest on the
visitor limits above and the measures of Memory, each with about 25 percent
headroom, rounded up. The arithmetic stands in a comment above each limit in
the compose file.

| Service     | Memory (`DEMO_MEM_...`)    | Processes (`DEMO_PIDS_...`) | Why |
| ----------- | -------------------------- | --------------------------- | --- |
| init        | `DEMO_MEM_INIT` 128m       | `DEMO_PIDS_INIT` 64         | a Python process that writes a few files and exits |
| vaultwarden | `DEMO_MEM_VAULTWARDEN` 384m | `DEMO_PIDS_VAULTWARDEN` 64 | 25 MiB peak plus a full data tmpfs (256m), 42 processes |
| controller  | `DEMO_MEM_CONTROLLER` 320m | `DEMO_PIDS_CONTROLLER` 32   | the 226 MiB peak while it builds a vault, 16 processes |
| demo        | `DEMO_MEM_DEMO` 2700m      | `DEMO_PIDS_DEMO` 128        | 6 visitors, 2 starts in flight and a full accounts tmpfs (512m), 101 processes |
| edge        | `DEMO_MEM_EDGE` 32m        | `DEMO_PIDS_EDGE` 32         | 13 MiB peak, 14 processes |

The Memory budget says which limit covers which row: the visitors, the
gateway, the starts in flight and the accounts tmpfs are the demo's, the
Vaultwarden row and the data tmpfs are Vaultwarden's, the controller's peak is
the controller's and the idle row is each service's own.

An operator who raises `DEMO_MAX` raises the demo's limits too, per added
visitor: 275 MiB in `DEMO_MEM_DEMO` (220 MiB, plus 25 percent), 20 in
`DEMO_PIDS_DEMO` (about 15 measured, plus 25 percent) and 5 MiB in
`DEMO_MEM_VAULTWARDEN`. One who raises a tmpfs size adds 1.25 MiB per MiB to the
limit of the service that owns it: `DEMO_ACCOUNTS_SIZE` to `DEMO_MEM_DEMO`,
`DEMO_VAULTWARDEN_DATA_SIZE` to `DEMO_MEM_VAULTWARDEN`. The pages of a tmpfs
count against the memory of the container that writes them.

A container that hits a limit fails in a way that is easy to miss, so look for
it when the demo misbehaves. Out of memory: the kernel kills a process or the
container, and `docker inspect -f '{{.State.OOMKilled}}' CONTAINER` says
`true` for the container itself. Out of processes: forks fail with errors
such as "Resource temporarily unavailable" in the container's log, and
`docker exec CONTAINER cat /sys/fs/cgroup/pids.current` is at the limit.
Raise the matching variable of the table, never past what the Memory budget
allows for the host.

A start refused by a cap (429 from the gateway) or by the edge's rate limit
gets the start page with "The demo is full right now. Try again in a few
minutes." Any other request past a limit of the edge gets 429.

`DEMO_MAX` sets both the controller's limit and `BWTIDY_MAX_WORKERS`, so a
vault that is handed out always finds a free worker.

## The edge

nginx, configured by `demo/edge/default.conf.template`: the image fills in
the rates, the bursts and the port from the container's environment when it
starts. `demo/edge/15-trusted-proxies.sh` runs before that and writes the
real-ip config (Behind a reverse proxy). It
proxies everything to `demo:8000` with the `Host` header the browser sent
(the cookies, the redirects and MCP's host check go by it), and applies the
limits above with `limit_req`, keyed by the client's address. `/mcp` is not
buffered in either direction and may stay open for an hour, as MCP over
streamable HTTP answers with a stream of events. bwtidy uses no WebSocket.
The edge keeps no access log (`access_log off`): the usage stats count what
the demo needs to know without an address or a path of anyone's. Its error log
stays. The template is mounted from the checkout, so a change to it takes a
`docker compose restart edge`.

The edge asks crawlers and bots to stay away: `GET /robots.txt` answers
`User-agent: *` and `Disallow: /`, every answer carries `X-Robots-Tag: noindex,
nofollow, noarchive` (error pages too), and the start page and the frontend
index hold a robots meta tag with `noindex, nofollow`. It is only a request,
not all bots listen. A crawler that follows the rules neither indexes the demo
nor inflates its stats, and one that only follows links never takes a vault,
as a start needs a POST.

Behind a reverse proxy the edge sees the proxy's address for every visitor.
Name the proxy in `DEMO_TRUSTED_PROXIES` and the limits count the visitor
(Behind a reverse proxy).

## Behind a reverse proxy

Settings for an edge that sits behind a proxy with TLS:

- `DEMO_TRUSTED_PROXIES`: a space separated list of addresses or CIDRs,
  empty by default. For each entry the edge gets a `set_real_ip_from`, plus
  `real_ip_header X-Forwarded-For` and `real_ip_recursive on`, so the limits
  count the visitor the proxy names. The entry is the address the edge sees
  the proxy from, not the visitor's. `15-trusted-proxies.sh` writes
  `/etc/nginx/conf.d/00-real-ip.conf` when the container starts. With the
  list empty it writes nothing and the edge never believes
  `X-Forwarded-For`, so a visitor cannot pick the address the limits count.
  The proxy must set the header itself (Caddy's `reverse_proxy` and nginx's
  `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for` do).
- `DEMO_EDGE_PORT`: the port nginx listens on inside the edge, `80` by
  default. The published port stays `DEMO_BIND:DEMO_PORT` and maps to it. Set
  it when something else already uses 80 in the edge's network namespace
  (a sidecar), or when the proxy expects another port.
- `DEMO_SECURE_COOKIES=1` and `DEMO_HOST` set to the public host name or
  address, as in On a VPS.

The sidecar pattern: a VPN or tunnel container carries the edge's network,
so the stack publishes no port and the proxy reaches the edge through the
tunnel. An override file for `compose.demo.yaml`:

```yaml
services:
  sidecar:
    # the VPN client image of your choice, with its own settings
    image: busybox:1.37
    command: ["sleep", "infinity"]
    networks:
      - front
      - public

  edge:
    network_mode: service:sidecar
    ports: !reset []
    networks: !reset {}
    depends_on:
      sidecar:
        condition: service_started
```

The sidecar joins `front` (where the demo is) and `public` (the route out for
the tunnel), and the edge shares its network namespace, so nginx reaches
`demo:8000` through the sidecar's networks. Run both files:

```sh
docker compose -f compose.demo.yaml -f sidecar.yaml up -d
```

Set `DEMO_EDGE_PORT` to the port the tunnel forwards to, and
`DEMO_TRUSTED_PROXIES` to the proxy's address as the edge sees it (the tunnel
address of the proxy). `demo/tests/files/sidecar-override.yaml` is this
override with the stand-in, and a test checks that `docker compose config`
accepts it.

## Testing

The demo's tests are in `demo/tests`, `scripts/ci` runs them:

```sh
cd demo && uv run --locked pytest -q
```

`test_init.py` checks the files of `init`, their modes, that a second run
keeps a full set and writes a set that lacks a file anew, and that a strict
TLS client trusting the CA accepts the
server certificate for `vaultwarden` only. `test_controller.py` runs the
pool and the API with fakes for Vaultwarden and bw injected (the pool's
counts and limits, the ledger across a restart, failed builds, the token on
every route, `/status` and the log lines without a secret), and `BwCli`
against a fake `bw` script (the arguments, the environment and the
temporary directory).

`test_edge.py` renders the edge's config as the container does (an empty `DEMO_TRUSTED_PROXIES` writes no real-ip config, a list writes one `set_real_ip_from` each, `DEMO_EDGE_PORT` sets the listen port) and checks that the sidecar override is valid compose.

`test_gateway.py` runs the wrapper with the gateway's fake worker approach:
a small script that answers the health, the bw-login sequence and an echo of
every other request, and writes each request it gets to a file in its
account directory. The controller is a mock transport. It checks that every
refused route, in every spelling (dot segments sent as a raw ASGI request,
since an HTTP client resolves them), answers 403 and reaches no worker,
that the routes the app needs still do, the redirects to `/demo`, the start
page, the start (both cookies, the login sequence, no password in a page, a
header or the log), the three notes, End demo and Switch account, the
release once and its retries, the hard maximum, the start sweep, the banner
and the MCP token rewrite.

`test_stats.py` runs the wrapper the same way with the usage stats on: the
events carry the vault id, a sample holds the demo's numbers and the
controller's counts, a turned away visitor counts, `stats.json` holds no
vault id or password and the database is read at most once a minute, a
failing read fails no visitor, the start page renders its numbers and tables
in the HTML, the static files are served and nothing else, and the vendored
uPlot files match the sums in `VENDORED.md`.

`bwtidy_demo.vaultwarden` registers and deletes the users. Its module
docstring says where in Vaultwarden's and the Bitwarden clients' source each
field and each step of the crypto comes from. Its tests check the crypto
with fixed inputs and the HTTP calls against a mock transport. One test
registers a real user on the dev stack's Vaultwarden (it has
`SIGNUPS_ALLOWED`) and logs bw in with it in the app container, in a state
directory of its own. It skips without `BWTIDY_DEV_STACK=1`, like the
backend's integration tests:

```sh
scripts/dev-vault up
cd demo && BWTIDY_DEV_STACK=1 uv run --locked pytest tests/test_vaultwarden.py -q
```

`BWTIDY_STACK` picks another stack. The test leaves its user behind (the dev
stack has no admin token), `scripts/dev-vault down` or `reset` removes it.
The admin API's user deletion is tested against the mock only.

### The stack test

`scripts/demo-stack test` proves the two guarantees on running containers.
It builds the normal image from the worktree, the demo image on top of it,
starts the stack on a free port of 127.0.0.1 (in a compose project and with
images named after the worktree, so several worktrees never share one) and
runs `demo/tests/stack/test_demo_stack.py` against it. `scripts/ci` never
runs it: the file skips without `DEMO_STACK=1`.

```sh
scripts/demo-stack test            # up, then the tests, about three minutes
scripts/demo-stack test -k edge    # pytest arguments pass through
scripts/demo-stack compose logs demo
scripts/demo-stack down            # stop it, remove its volumes and images
```

Through the edge, with a client that keeps cookies like a browser tab, it
checks:

- The networks: `back` and `front` are internal, only the edge is on
  `public` and only the edge publishes a port. From inside the `demo` and
  `controller` containers (also as a worker's uid) a TCP connection to
  Bitwarden's servers by name, to 1.1.1.1 and to 8.8.8.8 fails, while the
  one to `vaultwarden` succeeds. Vaultwarden reaches nothing outside
  either, and the edge reaches `demo` but neither Vaultwarden nor the
  controller, by name or by address.
- A visitor: `/` sends to `/demo`, the start page has no input field, the
  start answers both cookies, the session is unlocked on
  `https://vaultwarden` with a four word id, the grid and MCP's
  `list_items` (with a token capped to one day) list the fixture's items.
- The usage stats: `/demo/stats.json` shows the vault start, the grid and
  `list_items` without the vault's id, and a dump of the stats database holds
  no password of the fixture and nothing shaped like a vault's password.
- Every refused route answers 403 with the wrapper's text, for a stranger
  and for a visitor, with a real server in the body and in other spellings
  of the path (sent raw). Afterwards the accounts root holds the one
  directory of the one visitor, and no log names a real server.
- A second visitor gets another id, both are in the ledger. End demo and
  Switch account delete the account directory and the Vaultwarden user (asked
  through the admin API from the controller container), the ended demo's
  cookies and MCP token open nothing, and the pool refills.
- The edge: a body of 2 MB gets 413, a burst on `/mcp` gets 429, a burst of
  starts gets the "full" note and takes no vault.
- `DEMO_MAX=1`: the test starts the stack again with one vault, a second
  start while the first runs gets the "free" note, and the end of the first
  makes room.

A failure of a refused route, of a connection that must fail or of the
cleanup is a reason to stop and find out, never to weaken the test.

## Run it

On a host with Docker, from a checkout:

```sh
docker compose -f compose.demo.yaml up -d --build
```

The demo then answers on `http://127.0.0.1:8770`. Every setting has a
default. A `.env` beside the compose file or the environment changes it:

| Setting                 | Default                          | What                                                        |
| ----------------------- | -------------------------------- | ----------------------------------------------------------- |
| `BWTIDY_IMAGE`          | `docker.io/tnek213/bwtidy:main`  | the normal image the demo image is built on                 |
| `DEMO_IMAGE`            | `bwtidy-demo:local`              | the name the built demo image gets                          |
| `DEMO_BUILD_CONTEXT`    | `.`                              | the build context: the checkout, or a git URL               |
| `DEMO_BIND`, `DEMO_PORT` | `127.0.0.1`, `8770`             | where the edge is published                                 |
| `DEMO_EDGE_PORT`        | `80`                             | the port nginx listens on inside the edge, the published port maps to it |
| `DEMO_TRUSTED_PROXIES`  | empty                            | reverse proxies whose `X-Forwarded-For` is believed (Behind a reverse proxy) |
| `DEMO_HOST`             | empty                            | the public host name, for MCP's host check (`BWTIDY_MCP_HOSTS`) |
| `DEMO_SECURE_COOKIES`   | empty                            | `1` behind HTTPS (`BWTIDY_SECURE_COOKIES`)                  |
| `DEMO_POOL_READY`, `DEMO_MAX` | 2, 6                       | the pool and the visitors at once                           |
| `DEMO_MAX_PENDING`      | 2                                | starts in flight (`BWTIDY_MAX_PENDING`)                     |
| `DEMO_SESSION_IDLE`, `DEMO_SESSION_MAX` | 900, 2700        | seconds, the idle end and the hard maximum                  |
| `DEMO_START_RATE`, `DEMO_START_BURST`, `DEMO_MCP_RATE`, `DEMO_MCP_BURST` | `6r/m`, 5, `10r/s`, 40 | the edge's limits per address |
| `DEMO_VAULTWARDEN_DATA_SIZE`, `DEMO_ACCOUNTS_SIZE` | `256m`, `512m` | the two tmpfs the visitors fill           |

The server (`https://vaultwarden`) is no setting: the compose file names it,
and the wrapper refuses to start with any other.

The image: `demo/Dockerfile` starts `FROM` the image `BWTIDY_IMAGE` names and
installs `bwtidy_demo` into bwtidy's own venv with uv (the gateway starts
each worker with the `bwtidy` script beside its own Python, so a second venv
would not do): first the packages of `demo/uv.lock` at their locked
versions and hashes, which the base already holds unless the two lock files
drifted, then the project alone, both without resolving anything. The
bwtidy in the demo image is the base's, never the checkout's sources. The
default base follows `main`. A deployment pins a short-commit tag or a
version, so it runs a bwtidy somebody chose:

```sh
BWTIDY_IMAGE=docker.io/tnek213/bwtidy:abc1234 docker compose -f compose.demo.yaml up -d --build
```

Without a checkout, the host needs `compose.demo.yaml` and
`demo/edge/default.conf.template` and `demo/edge/15-trusted-proxies.sh` (the
edge mounts them) at their paths, and
`DEMO_BUILD_CONTEXT` names the repo for the build, a branch or commit after
`#`:

```sh
DEMO_BUILD_CONTEXT=https://github.com/tnek213/bwtidy.git#main docker compose -f compose.demo.yaml up -d --build
```

`docker compose build` needs buildx 0.17 or later. With an older one, build
with plain docker and start without a build, as `scripts/demo-stack` does:

```sh
docker build -f demo/Dockerfile --build-arg BWTIDY_IMAGE=docker.io/tnek213/bwtidy:abc1234 -t bwtidy-demo:local .
docker compose -f compose.demo.yaml up -d --no-build
```

To build on the checkout's own bwtidy instead of a published image, build
the normal image first (`docker build --target app -t bwtidy:demo-base .`)
and set `BWTIDY_IMAGE=bwtidy:demo-base`.

After a changed setting, `up -d` again makes the containers it concerns
anew. `docker compose -f compose.demo.yaml down` stops the stack and keeps
the ledger and the secrets. `down --volumes` removes them and the usage stats, and with the
ledger the promise that an id is never used twice, so use it for a test
stack only.

The controller's counts, from the host:

```sh
docker compose -f compose.demo.yaml exec controller python3 -c "
import urllib.request
token = open('/secrets/controller-token').read().strip()
request = urllib.request.Request('http://127.0.0.1:8100/status', headers={'Authorization': 'Bearer ' + token})
print(urllib.request.urlopen(request).read().decode())"
```

## Usage stats

The demo runs with bwtidy's opt-in usage stats on (docs/spec.md, Usage
stats): `compose.demo.yaml` sets `BWTIDY_STATS=1` and keeps the file in a
volume of its own, `BWTIDY_STATS_DB=/stats/stats.db` on the `stats` volume,
since the root file system is read-only and the accounts are a tmpfs. The
stats are public by design: anyone can see whether the demo is used, which
operations work and when it was full.

What the demo adds to bwtidy's stats (`bwtidy_demo/stats.py`):

- The account of an event is the vault's four-word id
  (`create_gateway_app(stats_account=...)`), not bwtidy's opaque id. The id
  names a vault that is gone once its demo ended.
- Each capacity sample's `extra` (`stats_extra`) holds what the demo saw since
  the last sample: `sessions` (the demo vaults started), `turned_away` (starts
  that got the "free" or the "full" note from the wrapper, a start the edge
  limits never reaches it), `wait_ms_total` and `wait_ms_max` (how long the
  start page made a visitor wait for their vault, from the click until the
  vault was logged in), and the controller's `ready`, `building` and `max`
  (`DEMO_MAX`). The wrapper asks the controller's `GET /status` every 20
  seconds and leaves the three out while it does not answer.

The public panel is the Usage section under the start button, made in the
HTML itself, so it works without JavaScript:

- The plain numbers of the last 200 days and of today (from midnight UTC):
  the demo vaults used, the UI operations, the MCP calls and the share that
  succeeded. Below them the visitors turned away and how long a start took.
- A chart of the active demo sessions per hour over the last 14 days, with
  the capacity (`DEMO_MAX`) as a dashed line and a marker in each hour when a
  visitor was turned away. uPlot draws it from `/demo/stats.json`, and a table
  of the same hours follows it for screen readers, collapsed by default.
- A table of the operations by channel (Web UI, MCP) with their succeeded and
  failed counts, and the errors: per operation the exception's type and how
  often, never its frames.
- The line "The demo counts anonymous usage (sessions, operations, errors)
  and shows it here. It stores no addresses and nothing you type."

`GET /demo/stats.json` answers what the panel shows: `summary(conn, 200)` of
`bwtidy.stats.summary` plus the key `demo` with the demo's totals (`vaults_started`,
`turned_away`, the waits), `today` and the `hours` of the chart. Both read one
summary that the wrapper keeps for 60 seconds, so reloads never reach the
database more than once a minute. Neither holds a vault id, a visitor's own
timeline, a frame or a fingerprint. A read that fails shows "The usage numbers
cannot be shown right now." on the start page and answers 503, a start still
works. With `BWTIDY_STATS` off the page has no Usage section and the route
answers 404.

uPlot (MIT) is vendored in `bwtidy_demo/static/uplot/`, the two files of its
npm package as they are, with `VENDORED.md` naming the version, the source and
the sums. The wrapper serves them and `usage.js` under `/demo/static/` (only
those three names), so the page loads nothing from another host. The page
keeps the start page's colors and font.

The full stats, with the top errors and their frames, are for the operator
where the demo runs:

```sh
docker compose -f compose.demo.yaml exec demo bwtidy stats --days 30
docker compose -f compose.demo.yaml exec demo bwtidy stats --json
```

## Memory

Measured on 2026-11-26 with `scripts/demo-stack up` (bwtidy 0.1.0a1 at
v0.1.0-alpha.1-30-gd3c16ef, bw 2026.10.0, Vaultwarden 1.37.4), a visitor
being `POST /demo/start` through the edge followed by `GET /api/session`.
`docker stats --no-stream`, in MiB, with the pool full (2 vaults ready):

| Visitors | edge | demo | controller | vaultwarden | total |
| -------- | ---- | ---- | ---------- | ----------- | ----- |
| 0        | 10   | 42   | 44         | 15          | 111   |
| 1        | 10   | 261  | 44         | 18          | 333   |
| 2        | 11   | 486  | 44         | 19          | 560   |
| 3        | 11   | 704  | 44         | 20          | 779   |

Processes, `pids.current` in each container's cgroup, the peak of samples
taken while 6 visitors started (2026-11-26, the same versions, a heartbeat
sent for each so none ended): edge 14, demo 101 (about 15 per visitor), controller
16, vaultwarden 42. The demo's peak memory was 1,311 MiB with the 6 visitors
and the controller's 226 MiB.

Each visitor costs about 220 MiB in the demo container and 2 to 3 MiB in
Vaultwarden. The split of one visitor in the demo container (RSS, from
`/proc`, since the image has no `ps`):

| Process                          | RSS      |
| -------------------------------- | -------- |
| `bw serve` (node), per visitor   | 205-215 MB |
| the worker (`bwtidy`), per visitor | 91 MB  |
| the gateway, once                | 62-65 MB |

The two RSS figures add up to more than the 220 MiB the container's cgroup
counts, because the processes share the pages of the Python and node
binaries. The controller is at 44 MiB when it idles and peaks at about 210
MiB while it builds a vault (it runs bw for the login and the import), so
allow for one such peak. A start takes about 13 s and a vault build about 12
s on the machine of the measurement.

The two tmpfs that visitors can fill count as memory once full: up to 512
MiB for the accounts (`DEMO_ACCOUNTS_SIZE`) and 256 MiB for Vaultwarden
(`DEMO_VAULTWARDEN_DATA_SIZE`). A fixture vault is far below that.

The budget for a 4 GB x86 VPS, with about 1 GB kept for the OS and peaks:

| Part                                     | MiB       |
| ---------------------------------------- | --------- |
| the four containers with no visitor      | 111       |
| 6 visitors at 220 MiB plus 3 in Vaultwarden | 1,340  |
| the controller's peak while it builds    | 170 more  |
| two starts in flight (`BWTIDY_MAX_PENDING`), about 100 each for bw's login | 200 |
| tmpfs full (both)                        | 768       |
| total in the worst case                  | about 2,600 |

That is under the 3 GB that remain after the 1 GB, so the default stays at
`DEMO_MAX=6` and `BWTIDY_MAX_WORKERS=6`, and `DEMO_POOL_READY=2` (a ready
vault is only a user in Vaultwarden, no process, so the pool costs almost
nothing). 8 visitors would add 440 MiB and reach the limit with full tmpfs,
so a bigger machine is what raises `DEMO_MAX`, by about 220 MiB each.

## On the maintainer's host

For a host where only the maintainer may reach the demo (the demo is not
public there). Keep the default `DEMO_BIND=127.0.0.1`, so the edge listens
on loopback of the host and nothing else can connect:

```sh
git clone https://github.com/tnek213/bwtidy.git
cd bwtidy
BWTIDY_IMAGE=docker.io/tnek213/bwtidy:abc1234 docker compose -f compose.demo.yaml up -d --build
```

From a laptop, an SSH tunnel brings the port over, and the demo is at
`http://localhost:8770`:

```sh
ssh -L 8770:127.0.0.1:8770 HOST
```

The cookies then travel over http on loopback, so leave
`DEMO_SECURE_COOKIES` empty.

Why a tunnel and not a name that only the Tailnet resolves behind a reverse
proxy: a name is only hidden, not closed. The proxy listens on its address
and answers whatever name a request carries, so anyone who finds the address
(or guesses the name, which certificate transparency logs and DNS scans
often reveal) gets the demo. A port on loopback has no address anyone else
can reach, and the tunnel is authenticated by SSH.

## On a VPS

For a public demo. What it needs: a 4 GB x86 (amd64) machine (the budget in
Memory), Docker with Compose, a name in DNS that points at it and ports 80
and 443 open. The demo stack stays bound to loopback and a reverse proxy in
front of it holds the certificate for the public name.

1. Put these in a `.env` beside `compose.demo.yaml`, with the public name:

   ```sh
   BWTIDY_IMAGE=docker.io/tnek213/bwtidy:abc1234
   DEMO_HOST=demo.example.org
   DEMO_SECURE_COOKIES=1
   ```

   `DEMO_SECURE_COOKIES=1` sets the Secure flag on the cookies, which only
   works behind HTTPS. `DEMO_HOST` lets MCP's host check accept the name.
   `BWTIDY_IMAGE` pins the normal image to a short-commit tag, so the demo
   runs a bwtidy somebody chose.

2. Build from a pinned commit with a remote git context, so the VPS needs no
   checkout of the sources (only `compose.demo.yaml` and
   the two files of `demo/edge` at their paths):

   ```sh
   DEMO_BUILD_CONTEXT=https://github.com/tnek213/bwtidy.git#COMMIT docker compose -f compose.demo.yaml up -d --build
   ```

   With a buildx older than 0.17, build with plain docker as in Run it.

3. A reverse proxy for the public name, which sets no limits of its own
   (the edge has them). Caddy gets the certificate by itself:

   ```
   demo.example.org {
       reverse_proxy 127.0.0.1:8770
   }
   ```

   The same with nginx and a certificate from certbot:

   ```
   server {
       listen 443 ssl;
       server_name demo.example.org;
       ssl_certificate     /etc/letsencrypt/live/demo.example.org/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/demo.example.org/privkey.pem;
       location / {
           proxy_pass http://127.0.0.1:8770;
           proxy_set_header Host $host;
           proxy_buffering off;
           proxy_read_timeout 300s;
       }
   }
   ```

   The edge limits go by the address of the peer. Behind the proxy that is
   the proxy for every visitor, unless the edge trusts the proxy's
   `X-Forwarded-For` (Behind a reverse proxy).

Updating: pull the new commit or set a new `COMMIT` and `BWTIDY_IMAGE`, and
run the `up -d --build` again. Every container that changed is made anew,
and every demo running ends with it (Vaultwarden starts empty). The ledger
volume stays, so no id is used twice.

Logs: `docker compose -f compose.demo.yaml logs -f demo controller edge`
(each log is capped at 3 files of 10 MB). The controller's counts are in the
last command of Run it: ready, taken, building and failed. Failed above 0 for
long is a vault that cannot be built, and the controller log says why.

What to watch:

- Memory: `docker stats --no-stream`, about 110 MiB with no visitor and 220
  MiB for each. Taken at the limit of 6 with the machine near its memory is a
  reason to lower `DEMO_MAX`.
- The ledger volume: it grows by one line (an id) for every vault ever made,
  so `docker system df -v` and the size of `ledger` show it. It stays small
  (tens of bytes a vault) for years, and it is never cleared.
- The tmpfs: a demo container whose `/data/accounts` is full fails starts.
  `docker compose -f compose.demo.yaml exec demo df -h /data/accounts`.

## What a visitor can and cannot do

For a review of the demo's safety. The tests of the stack
(`scripts/demo-stack test`) check every line of it.

A visitor can:

- Start one demo at a time, which logs bw in server side to a fresh vault of
  its own on the demo's Vaultwarden, seeded with the dev fixture (made-up
  data). No field of the demo asks for a credential.
- Use everything bwtidy does on that vault: sort, edit, delete, restore,
  commit and undo, with the backups of the account.
- Create an MCP token (valid at most 1 day) and use `/mcp` for that vault
  only. The token opens nothing else.
- End the demo (End demo, Lock, MCP's `end_session`). Idle for 15 minutes,
  a closed tab for 2 minutes and a total of 45 minutes end it too. Its
  account directory, backups and Vaultwarden user are then deleted.

A visitor cannot:

- Log in to any other server or account: `BWTIDY_ALLOWED_SERVERS` holds only
  the demo's Vaultwarden, the routes that create or change a login or an
  account are refused (Refused routes), and no container but the edge has a
  route out of the host.
- See or reach another visitor's vault: each has its own account directory,
  its own uid and cookies, and its own Vaultwarden user.
- Reach Vaultwarden's admin page, the controller or the demo gateway
  directly: only the edge is published, and it forwards to the gateway alone.
- Get a vault the same id twice: the ledger keeps every id ever used.
- Fill the machine: the tmpfs have sizes, request bodies are 1 MB, the
  edge rate limits starts and MCP, and `DEMO_MAX` caps the visitors.
- Keep a demo beyond its maximum, or a token beyond 1 day.
