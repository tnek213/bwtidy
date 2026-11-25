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

Containers on one network can reach each other's ports, so the controller
and Vaultwarden's admin page also check a token (see Secrets): the network
keeps the outside out, the tokens keep a worker or a bug in `demo` from
anything but what it is meant to call.

## Start flow

1. `GET /demo` serves the start page from the wrapper, on the same origin as
   bwtidy. Its Start demo button is a plain HTML form that POSTs to
   `/demo/start`: both cookies are SameSite=Strict, so the browser must get
   them in an answer to a same-site navigation, never a cross-site one.
2. `POST /demo/start` asks the controller for a ready vault: its server
   (`https://vaultwarden`), its email and its master password. The password
   never leaves the `demo` process: no page, no log, no cookie.
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

A start that fails (no ready vault, a cap reached, a failed login) answers
the start page again with a short text ("No demo vault is free right now.
Try again in a minute.") and leaves no account behind: the worker is
stopped, its exit hook deletes the directory and the Vaultwarden user.

`GET /` and `GET /index.html` without a routing cookie of a live demo
account answer 303 to `/demo`, so a visitor whose demo ended lands on the
start page, never on the account form.

## Refused routes

An outer middleware of the wrapper (`app.add_middleware` after
`create_gateway_app`, so it runs before the gateway's proxy) answers these
from the browser with 403 and a plain text such as `The demo logs you in
itself. Start a new demo at /demo.`:

- `POST /gateway/account`: only `/demo/start` routes a browser.
- `POST /api/bw-login`, `POST /api/bw-login/input` and `POST
  /api/bw-login/finish`: only the wrapper logs in, in process, never
  through the proxy.
- `POST /api/session` (the unlock): the visitor has no password to type,
  and a working unlock form would be a credential field.
- `POST /api/bw-logout`.
- `POST /api/account/delete` (Delete my account, `api/account.py`): the end
  of the demo deletes the account anyway.

`DELETE /gateway/account` (Switch account, and the end of Delete all data)
becomes End demo: the wrapper stops the account's worker
(`gateway.workers.stop`), which runs the cleanup below, and answers 204 with
the routing cookie cleared, as the gateway would.

`POST /api/mcp/token` is capped: the wrapper reads its small JSON body and
passes it on with `days` 1 when it asks for more or for no expiry (`null`).
The answer's `expiresAt` shows the real expiry. MCP itself works for the
visitor's vault only, as in bwtidy: the token names the account.

## Banner

The wrapper's router serves `/` and `/index.html` itself (a router wins over
the static files). It reads `settings.static_dir/index.html` once at the
start and inserts a fixed banner right after `<body>`: "Demo: your vault is
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
   so it can take it.
2. Tells the controller to delete the Vaultwarden user.

The hard maximum: the wrapper notes each demo's start and stops its worker
(`gateway.workers.stop`) after `DEMO_SESSION_MAX`, whatever the activity.
The cleanup then runs through the hook like any other end.

At its start the wrapper deletes every account directory left from before
(they are all demo accounts) and asks the controller to delete their users.
A gateway restart never leaves a vault behind.

## Controller

The controller keeps `DEMO_POOL_READY` vaults ready, hands one out per
start and deletes the users of ended demos.

- A new vault: the controller draws an id, four words from `bw generate
  --passphrase --words 4 --separator -`, and makes the email
  `ID@demo.bwtidy.test` and a random master password. It registers the
  account through Vaultwarden's two-step registration
  (`/identity/accounts/register/send-verification-email`, which answers the
  token itself without SMTP, then `/identity/accounts/register/finish`),
  with the client-side crypto a Bitwarden client does: the master key from
  the password with the server's KDF, the master password hash, the
  encrypted user key and the key pair. It then logs in with bw in a
  directory of its own, seeds the vault with `bw import bitwardenjson` of
  `dev/fixture-personal.json`, logs out and deletes that directory.
- The ledger: a file on a persistent volume holds every id ever drawn. A
  drawn id already in it is drawn again, so an id is never reused, not even
  after a restart that emptied Vaultwarden.
- Handing out: a ready vault goes to exactly one start and leaves the pool,
  then the controller makes a new one in the background.
- Deleting a user: through Vaultwarden's admin API. `POST /admin` with the
  token as a form field sets the `VW_ADMIN` cookie, which the controller
  keeps and reuses until it is refused: the admin login allows a burst of 3
  per 300 seconds. `GET /admin/users/by-mail/EMAIL` finds the user's UUID,
  `POST /admin/users/UUID/delete` (with `Content-Type: application/json`)
  deletes it. A user that is not there counts as deleted.
- Its HTTP API listens on `back` only and needs `Authorization: Bearer
  TOKEN`, the controller token from the secrets volume, on every request.
  It never answers a password but to the start that takes the vault.

## Secrets

`init` writes into the `secrets` volume at every start of the stack:

- a random Vaultwarden admin token (Vaultwarden reads it with
  `ADMIN_TOKEN_FILE`, the controller reads the same file),
- a random controller API token (the controller and the wrapper read it),
- the TLS key and the self-signed certificate for the name `vaultwarden`
  (Vaultwarden reads both, the workers and the controller trust the
  certificate with `NODE_EXTRA_CA_CERTS`, bw refuses plain http).

No secret ever leaves the stack, and none comes from outside: nothing is
configured by hand and nothing is shown. The tokens are readable by root
only. The certificate is readable by everyone, as the workers run as the
accounts' uids. The edge mounts no secret.

Vaultwarden's data is temporary: a restart starts it empty, and the wrapper
deletes the account directories at its start to match. The id ledger is the
one persistent volume.

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

The defaults, adjusted after measuring:

| Limit                          | Default    | Setting                                                    |
| ------------------------------ | ---------- | ---------------------------------------------------------- |
| vaults ready in the pool       | 2          | `DEMO_POOL_READY` (controller)                             |
| visitors at once               | 6          | `BWTIDY_MAX_WORKERS=6`                                     |
| starts in flight               | 2          | `BWTIDY_MAX_PENDING=2` (a start's directory is pending until its unlock) |
| idle session end               | 15 minutes | `BWTIDY_SESSION_IDLE_TIMEOUT=900` (seconds)               |
| closed tab                     | 2 minutes  | `BWTIDY_HEARTBEAT_TIMEOUT`, bwtidy's default 120          |
| a session at most              | 45 minutes | `DEMO_SESSION_MAX=2700` (seconds, the wrapper)            |
| MCP token lifetime             | 1 day      | the wrapper's cap on `POST /api/mcp/token`                 |
| `POST /demo/start`             | per address | the edge's `limit_req` zone for it                        |
| `/mcp`                         | per address | the edge's `limit_req` zone for it                        |

A start refused by a cap (429 from the gateway) or by the edge's rate limit
gets the start page with "The demo is full right now. Try again in a few
minutes."

## Run it

To be written with `compose.demo.yaml`.

## On the maintainer's host

To be written with the deployment: bound to 127.0.0.1 and reached through
an SSH tunnel, no reverse proxy and no DNS.

## On a VPS

To be written: a 4 GB x86 VPS, documented but not deployed.
