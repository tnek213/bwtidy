# Configuration

Every setting is a variable in `.env` next to `compose.yaml`, which Compose
reads by itself. `example.env` lists the ones you may set, with a comment
each. An empty value means the default.

A time in seconds is a positive number such as `90` or `0.5`. Every other
number the gateway reads is a whole number of at least 1, and a port is at
most 65535. A value that a setting cannot take stops the gateway at its start
with a message that names the variable and what it takes.

## Compose and the build

`compose.yaml` and the image build read these from `.env`.

| Variable | Default | What it does |
| --- | --- | --- |
| `BWTIDY_IMAGE` | | The image to run, without the tag. `compose.yaml` refuses to start without it, `example.env` sets the published image `docker.io/tnek213/bwtidy`. A build from a checkout (see [Run bwtidy](../how-to/run.md)) names its image `bwtidy:local` instead, but the variable must still be set. |
| `BWTIDY_VERSION` | `latest` | The image tag. A build from a checkout ignores it. |
| `BWTIDY_HOST_PORT` | `8765` | The port on the host's 127.0.0.1 that the web page is published on. |
| `BWTIDY_BACKUPS` | `bwtidy-backups` | Where the backups are kept: the volume, or a host directory such as another disk, a network share or a directory your backup tool reads. Its file system must let root chown. |
| `BWTIDY_COMMIT` | | The commit the image is built from, given to the build. `/api/health` and the About page show it after the version. |

## Gateway

The gateway is the process the container starts. It serves the page and
starts one worker per open account (see
[How bwtidy fits together](../explanation/overview.md)).

### Set in `.env`

| Variable | Default | What it does |
| --- | --- | --- |
| `BWTIDY_SECURE_COOKIES` | `0` | `1` when the page is served over https, so the cookies are marked Secure. |
| `BWTIDY_MCP_HOSTS` | | Host names (comma separated) that `/mcp` accepts besides 127.0.0.1, localhost and ::1. Set it behind a reverse proxy. |
| `BWTIDY_ALLOWED_SERVERS` | | Servers (comma separated URLs) that may resolve to a private address, like a Vaultwarden on your own network. |
| `BWTIDY_MAX_PENDING` | `2` | Most new accounts that wait for their first login at once. |
| `BWTIDY_MAX_WORKERS` | `8` | Most accounts open at once, one worker each. |
| `BWTIDY_UID_BASE` | `70000` | The account workers' uids start above it: the first account gets 70001, each new one the next. |
| `BWTIDY_HEARTBEAT_TIMEOUT` | `120` | Seconds without a heartbeat (all tabs closed) before the session ends, at least 60. |
| `BWTIDY_SESSION_IDLE_TIMEOUT` | `7200` | Seconds without activity before the session ends. |
| `BWTIDY_BACKUP_KEEP_NEWEST` | `40` | A backup past the newest this many is deleted. |
| `BWTIDY_BACKUP_KEEP_DAYS` | `90` | A backup older than this many days is deleted. |
| `BWTIDY_BACKUP_KEEP_ALWAYS` | `5` | The newest this many backups are kept whatever their age. |
| `BWTIDY_FAILED_KEEP_HOURS` | `30` | Hours after which a failed queue entry is deleted. |
| `BWTIDY_STATS` | `0` | `1` turns on the [usage stats](#usage-stats), which stay on this machine. |
| `BWTIDY_STATS_DB` | `/data/stats.db` | The usage stats file, by default `stats.db` next to the accounts directory. It must be outside the accounts directory and the backups. |

The gateway checks the six from `BWTIDY_HEARTBEAT_TIMEOUT` to
`BWTIDY_FAILED_KEEP_HOURS` at its start and passes them on to every worker,
which is where they take effect. It passes `BWTIDY_MCP_HOSTS` on too.

### Set by the image

The image and `compose.yaml` set these, leave them as they are.

| Variable | Default | What it does |
| --- | --- | --- |
| `BWTIDY_HOST` | `127.0.0.1` | The address the gateway listens on, `0.0.0.0` in the image. |
| `BWTIDY_PORT` | `8000` | The port the gateway listens on in the container. |
| `BWTIDY_ACCOUNTS_DIR` | `/data/accounts` | Where the account directories are. |
| `BWTIDY_BACKUPS_ROOT` | | Where the backups are, `/backups` in `compose.yaml`. Unset, each account keeps them in its own `app/backups`. |
| `BWTIDY_STATIC_DIR` | | The built web page, `/app/static` in the image. Unset, only the API is served. |
| `BWTIDY_DOCS_DIR` | | The built documentation site served at `/docs/`, `/app/site` in the image. Unset, there is none. |
| `BWTIDY_IDLE_EXIT` | `600` | Seconds a worker waits for its first session before it exits. The gateway checks it at its start and passes it on to every worker. |
| `BWTIDY_GATEWAY_SWITCH_UID` | `1` | `0` keeps every worker on the gateway's own uid, for the unit tests only. |

## Worker

The gateway starts each account's worker with an environment built from
scratch: the variables it passes on (above), and these. You never set them.

| Variable | Default | What it does |
| --- | --- | --- |
| `BWTIDY_ACCOUNT_SERVER` | | The account's server. Set together with `BWTIDY_ACCOUNT_EMAIL`, they fix the one account the worker serves. |
| `BWTIDY_ACCOUNT_EMAIL` | | The account's email. |
| `BWTIDY_DATA_DIR` | `/data/app` | The account's `app` directory: the SQLite file, the MCP token's hash. |
| `BWTIDY_BACKUPS_DIR` | `BWTIDY_DATA_DIR/backups` | The account's backup directory, under `BWTIDY_BACKUPS_ROOT` when that is set. |
| `BWTIDY_SOCKET` | | The Unix socket the worker answers on, in place of a port. |
| `BWTIDY_BW_SOCKET` | | The Unix socket of the worker's `bw serve`, in place of a port. |
| `BWTIDY_EXIT_AFTER_SESSION` | `0` | `1`: the worker serves one session and exits when it ends, or after `BWTIDY_IDLE_EXIT` without one. |
| `BWTIDY_PENDING_MARKER` | | The file that marks a new account. The first unlock deletes it, which keeps the account. |
| `BWTIDY_SECURE_COOKIES` | `0` | The gateway's own value. |
| `BWTIDY_STATS_REPORT` | `0` | `1` when the gateway's usage stats are on: the worker tells the gateway its route templates and tool names, and an unhandled error's type and code locations. |

Besides these, the gateway gives a worker `PATH` and `NODE_EXTRA_CA_CERTS`
(for the Bitwarden CLI) when it has them, and sets `HOME` and
`BITWARDENCLI_APPDATA_DIR` to directories of the account. A worker reads
the variables it is given and six more that the gateway does not pass on,
so each of those has its default there:

- `BWTIDY_HOST` and `BWTIDY_PORT` (`127.0.0.1` and `8000`) are the address a
  worker listens on only when it has no `BWTIDY_SOCKET`, which a gateway's
  worker always has.
- `BWTIDY_STATIC_DIR` is unset, so a worker serves no web page. The gateway
  does.
- `BWTIDY_COMMIT` is unset, so a worker's `/api/health` shows the package
  version without a commit.
- `BWTIDY_BW_BIN` (`bw`, the Bitwarden CLI to run, found on `PATH`) and
  `BWTIDY_BW_SERVE_PORT` (`8087`, the port in the Host header that `bw serve`
  checks, since the worker reaches it on `BWTIDY_BW_SOCKET`).

For `bw login` a worker puts the master password in `BWTIDY_LOGIN_PASSWORD`,
in the environment of that one `bw` process only.

The dev stack's variables are in the developer guide, `dev/dev-vault.md` in
the repository.

## Usage stats

bwtidy keeps no usage stats unless you set `BWTIDY_STATS=1`. Then the gateway
counts what is used and what fails, in its own SQLite file
(`BWTIDY_STATS_DB`), and bwtidy never sends it anywhere. With the stats off
there is no file and nothing is counted.

What the file keeps is only what comes from bwtidy's code:

- For each request that reaches an account's worker: its time, an opaque id
  of the account (never its email), web UI or MCP, the route it took (like
  `PUT /api/items/{item_id}`, never the item's id) or the MCP tool or method
  it called (never its arguments), whether it succeeded, its status and how
  long it took.
- For an error the worker did not expect: its type and where in bwtidy's code
  it happened, never its message.
- Every minute: how many accounts are open, how many may be, and how many
  were turned away.

It never keeps item names, values, folder or field names, what you searched
for, request bodies, error messages, IP addresses or browsers. After 200 days
the requests are kept only as counts per day.

`compose.yaml` mounts only the accounts and the backups, and its root file
system is read-only, so the file needs a volume of its own. Set in `.env`:

```sh
BWTIDY_STATS=1
BWTIDY_STATS_DB=/stats/stats.db
```

and mount the volume in a `compose.override.yaml` next to `compose.yaml`,
which Compose reads by itself:

```yaml
services:
  bwtidy:
    volumes:
      - bwtidy-stats:/stats

volumes:
  bwtidy-stats:
```

Without a place it can write the file, the gateway does not start.

`bwtidy stats` prints the last 14 days: the accounts, the UI and MCP
operations with how many succeeded and failed, the busiest hour and the most
frequent errors with where they happened. Run it where the gateway runs:

```sh
docker compose exec bwtidy bwtidy stats
docker compose exec bwtidy bwtidy stats --days 60 --json
```

`--days N` picks the days, `--json` prints the summary and the errors as JSON.
Without a stats file it says that the stats are off.

## Sessions and limits

Each Unlock or Log in starts a worker for up to 10 minutes, and anyone who
reaches the page can try one. Beyond `BWTIDY_MAX_PENDING` or
`BWTIDY_MAX_WORKERS` the unlock or the login says so. Only the logged in
browser, or an MCP client with the account's token, ends a running session.
What a new login needs while a session runs is in
[Logging in](../explanation/login.md#the-session).

## Files and volumes

The paths in the container:

```text
/data/accounts/                   the volume bwtidy-accounts
└── HOST/
    └── EMAIL/                    one account's data, only its uid can read it (mode 0700)
        ├── app/
        │   ├── bwtidy.sqlite     the queue, the change log and the settings
        │   └── mcp-token.sha256  the hash of the MCP token
        ├── bw/                   the Bitwarden CLI's login and state
        ├── home/                 the worker's home directory
        ├── run/                  the sockets of the worker and of its bw serve
        └── worker.log            the account worker's log
/backups/                         the volume bwtidy-backups, or the directory BWTIDY_BACKUPS names
└── HOST/
    └── EMAIL/
        └── TIMESTAMP.zip.age     a backup, named by its time in UTC
```

`bwtidy.sqlite` is not encrypted: a secret value is in it only
age-encrypted, but names, notes and URIs are plain in the queue's diffs, and
the change log names the fields each write changed.

Decrypt a backup with `age -d -o backup.zip FILE`, the passphrase is the
master password. Every backup holds `RESTORE.md`, the steps that bring a lost
vault back from it: see [Restore a vault from a backup](../restore.md).
