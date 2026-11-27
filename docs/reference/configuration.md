# Configuration

You set every setting as a variable in `.env` next to `compose.yaml`, and
Compose reads that file by itself. `example.env` lists the variables you may
set with a comment for each. An empty value means the default where the
tables below give one.

A time in seconds is a positive number such as `90` or `0.5`. Every other
number the gateway reads is a whole number of at least 1, and a port is at
most 65535. The gateway checks every value when it starts. A value that a
setting cannot take stops it with a message that names the variable and what
it takes.

The variables reach the gateway and its workers along the path below.

```graphviz alt="Compose reads .env and hands the gateway the variables set there along with the backups root. The image sets the gateway's addresses and paths. The gateway gives the worker of each open account the variables it passes on and the ones it sets for the account, and the worker has its own defaults for the rest."
digraph {
  rankdir=LR
  nodesep=0.3
  ranksep=0.5
  node [fontname="sans-serif" fontsize=13 shape=box style="rounded,filled" fillcolor="#ffffff" color="#5c6bc0" margin="0.12,0.06"]
  edge [fontname="sans-serif" fontsize=12 color="#455a64"]
  env [label=".env" fillcolor="#e8eaf6"]
  compose [label="compose.yaml"]
  image [label="The image"]
  gateway [label="Gateway" fillcolor="#f1f8e9" color="#558b2f"]
  worker [label="Worker\none per open account" fillcolor="#f1f8e9" color="#558b2f"]
  defaults [label="The worker's\nown defaults" style="rounded,filled,dashed"]
  env -> compose [label="read by\nCompose"]
  compose -> gateway [label="Set in .env\nand the backups root"]
  image -> gateway [label="Set by\nthe image"]
  gateway -> worker [label="passed on and\nset for the account"]
  defaults -> worker [style=dashed]
  {rank=same; gateway; defaults}
}
```

## Compose and the build

`compose.yaml` and the image build read these variables from `.env`.

| Variable | Default | What it does |
| --- | --- | --- |
| `BWTIDY_IMAGE` | | The image to run without its tag. `compose.yaml` refuses to start without it. `example.env` sets the published image `docker.io/tnek213/bwtidy`. A build from a checkout (see [Build the image yourself](../how-to/run.md#build-the-image-yourself)) names its image `bwtidy:local` instead, but the variable must still be set. |
| `BWTIDY_VERSION` | `latest` | The image tag. A build from a checkout ignores it. |
| `BWTIDY_HOST_PORT` | `8765` | The port on the host's 127.0.0.1 where the web page is published. |
| `BWTIDY_BACKUPS` | `bwtidy-backups` | Where the backups are kept. It is the volume or a host directory such as another disk, a network share or a directory your backup tool reads. Its file system must let root chown. |
| `BWTIDY_COMMIT` | | The commit the image is built from, which the build is given. `/api/health` and the About page show it after the version. |
| `BWTIDY_KROKI_URL` | `https://kroki.io` | The Kroki server that renders the diagrams of the docs in a build from a checkout. |

## Gateway

The gateway is the process that the container starts. It serves the page
and starts one worker for each open account (see
[How bwtidy fits together](../explanation/overview.md)).

### Set in `.env`

| Variable | Default | What it does |
| --- | --- | --- |
| `BWTIDY_SECURE_COOKIES` | `0` | `1` when the page is served over https, so the cookies are marked Secure. |
| `BWTIDY_MCP_HOSTS` | | Host names (comma separated) that `/mcp` accepts besides 127.0.0.1, localhost and ::1. Set it behind a reverse proxy. |
| `BWTIDY_ALLOWED_SERVERS` | | Servers (comma separated URLs) that skip the check for a public address, like a Vaultwarden on your own network. |
| `BWTIDY_MAX_PENDING` | `2` | The most new accounts that wait for their first login at once. |
| `BWTIDY_MAX_WORKERS` | `8` | The most accounts open at once, with one worker each. |
| `BWTIDY_UID_BASE` | `70000` | The uids of the account workers start above it. With the default the first account gets 70001 and each new account the next uid. |
| `BWTIDY_HEARTBEAT_TIMEOUT` | `120` | Seconds without a heartbeat (all tabs closed) before the session ends. It is at least 60. |
| `BWTIDY_SESSION_IDLE_TIMEOUT` | `7200` | Seconds without activity before the session ends. |
| `BWTIDY_BACKUP_KEEP_NEWEST` | `40` | A backup past the newest this many is deleted. |
| `BWTIDY_BACKUP_KEEP_DAYS` | `90` | A backup older than this many days is deleted. |
| `BWTIDY_BACKUP_KEEP_ALWAYS` | `5` | The newest this many backups are kept whatever their age. |
| `BWTIDY_FAILED_KEEP_HOURS` | `30` | Hours after which a failed queue entry is deleted. |
| `BWTIDY_STATS` | `0` | `1` turns on the [usage stats](#usage-stats), which stay on this machine. |
| `BWTIDY_STATS_DB` | `/stats/stats.db` | The usage stats file, on the volume `bwtidy-stats` that `compose.yaml` mounts at `/stats`. It must be outside the accounts directory and the backups. |

The gateway checks the six from `BWTIDY_HEARTBEAT_TIMEOUT` to
`BWTIDY_FAILED_KEEP_HOURS` when it starts and passes them on to every
worker. They take effect there. The retention never deletes a backup that is
still in use. The gateway passes `BWTIDY_MCP_HOSTS` on as well.

### Set by the image

The image and `compose.yaml` set some of these and the rest keep their
defaults. Leave them all as they are.

| Variable | Default | What it does |
| --- | --- | --- |
| `BWTIDY_HOST` | `127.0.0.1` | The address the gateway listens on. The image sets `0.0.0.0`. |
| `BWTIDY_PORT` | `8000` | The port the gateway listens on in the container. |
| `BWTIDY_ACCOUNTS_DIR` | `/data/accounts` | Where the account directories are. |
| `BWTIDY_BACKUPS_ROOT` | | Where the backups are. `compose.yaml` sets `/backups`. When it is unset each account keeps its backups in its own `app/backups`. |
| `BWTIDY_STATIC_DIR` | | The built web page, `/app/static` in the image. When it is unset no web page is served. |
| `BWTIDY_DOCS_DIR` | | The built documentation site served at `/docs/`, `/app/site` in the image. When it is unset there is none. |
| `BWTIDY_IDLE_EXIT` | `600` | Seconds a worker waits for its first session before it exits. The gateway checks it when it starts and passes it on to every worker. |
| `BWTIDY_GATEWAY_SWITCH_UID` | `1` | `0` keeps every worker on the gateway's own uid. It is for the unit tests only. |

## Worker

The gateway starts the worker of each account with an environment that it
builds from scratch. It holds the variables the gateway passes on (above)
and the ones below. You never set them.

| Variable | Default | What it does |
| --- | --- | --- |
| `BWTIDY_ACCOUNT_SERVER` | | The account's server. Together with `BWTIDY_ACCOUNT_EMAIL` it fixes the one account the worker serves. The two are set together or not at all. |
| `BWTIDY_ACCOUNT_EMAIL` | | The account's email. |
| `BWTIDY_DATA_DIR` | `/data/app` | The account's `app` directory, which holds the SQLite file and the hash of the MCP token. |
| `BWTIDY_BACKUPS_DIR` | `BWTIDY_DATA_DIR/backups` | The account's backup directory. It is under `BWTIDY_BACKUPS_ROOT` when that is set. |
| `BWTIDY_SOCKET` | | The Unix socket the worker answers on in place of a port. |
| `BWTIDY_BW_SOCKET` | | The Unix socket of the worker's `bw serve` in place of a port. |
| `BWTIDY_EXIT_AFTER_SESSION` | `0` | With `1` the worker serves one session and exits when it ends. It then also exits when no session starts within `BWTIDY_IDLE_EXIT`. |
| `BWTIDY_PENDING_MARKER` | | The file that marks a new account. The first unlock deletes it and so keeps the account. |
| `BWTIDY_SECURE_COOKIES` | `0` | The gateway's own value. |
| `BWTIDY_STATS_REPORT` | `0` | `1` when the gateway's usage stats are on. The worker then tells the gateway its route templates and tool names as well as the type and code locations of an unhandled error. |

The gateway also gives a worker its own `PATH` (or the system default) and
its `NODE_EXTRA_CA_CERTS` (for the Bitwarden CLI) when it has one. It sets
`HOME` and `BITWARDENCLI_APPDATA_DIR` to directories of the account.

A worker reads six more variables that the gateway does not pass on. Each of
them keeps its default in a worker:

- `BWTIDY_HOST` and `BWTIDY_PORT` (`127.0.0.1` and `8000`) are the address a
  worker listens on only when it has no `BWTIDY_SOCKET`. A worker of the
  gateway always has one.
- `BWTIDY_STATIC_DIR` is unset, so a worker serves no web page. The gateway
  does.
- `BWTIDY_COMMIT` is unset, so the `/api/health` of a worker shows the
  package version without a commit.
- `BWTIDY_BW_BIN` (`bw`) is the Bitwarden CLI to run, found on `PATH`.
- `BWTIDY_BW_SERVE_PORT` (`8087`) is only the port in the Host header that
  `bw serve` checks, since the worker reaches it on `BWTIDY_BW_SOCKET`.

For `bw login` a worker puts the master password in `BWTIDY_LOGIN_PASSWORD`.
Only the environment of that one `bw` process holds it.

The developer guide lists the variables of the dev stack in
`dev/dev-vault.md` of the repository.

## Usage stats

bwtidy keeps no usage stats unless you set `BWTIDY_STATS=1`. The gateway then
counts what is used and what fails in a SQLite file of its own
(`BWTIDY_STATS_DB`). bwtidy never sends that file anywhere. With the stats
off there is no file and nothing is counted.

What the file keeps comes only from bwtidy's code:

- A request that reaches an account's worker is kept with its time and an
  opaque id of the account (never its email). It also has the channel (web
  UI or MCP) and the route it took (such as `PUT /api/items/{item_id}`
  without the item's id). An MCP request has the tool or method it called in
  place of the route but never its arguments. Whether it succeeded is kept
  too, with its status and how long it took.
- An error the worker did not expect is kept as its type and the places in
  bwtidy's code where it happened. Its message is never kept.
- A sample every minute holds how many accounts are open and how many may be
  open at once. It also holds how many were turned away since the sample
  before.

The file never keeps any of these:

- item names and values
- folder and field names
- what you searched for and the bodies of requests
- error messages
- IP addresses and browsers

After 200 days the requests are kept only as counts per day.

`compose.yaml` keeps the file at `/stats/stats.db` on the volume
`bwtidy-stats`, because the container's root file system is read-only. So
`BWTIDY_STATS=1` in `.env` is all it takes.

A path of your own in `BWTIDY_STATS_DB` needs a mount that takes writes,
added in a `compose.override.yaml` next to `compose.yaml`. Without one the
gateway does not start. Outside `compose.yaml` an unset `BWTIDY_STATS_DB`
means `stats.db` in the parent of the accounts directory.

`bwtidy stats` sums up the last 14 days. It prints the accounts and the UI
and MCP operations with how many of each succeeded and failed. It adds the
busiest hour and the most frequent errors with the places where they
happened. Run it where the gateway runs.

```sh
docker compose exec bwtidy bwtidy stats
docker compose exec bwtidy bwtidy stats --days 60 --json
```

`--days N` picks another number of days and `--json` prints the summary and
the errors as JSON. Without a stats file the command says that the stats are
off.

## Sessions and limits

Each Unlock or Log in starts the account's worker when it is not running.
Anyone who reaches the page can try one. The worker exits again when no
session starts within 10 minutes (`BWTIDY_IDLE_EXIT`) and no Bitwarden login
runs. A login for a new account beyond `BWTIDY_MAX_PENDING` says so. So does
an unlock or a login beyond `BWTIDY_MAX_WORKERS`.

Only the logged in browser or an MCP client with the account's token can ask
to end a running session.
[Logging in](../explanation/login.md#the-session) says what else ends one and
what a new login needs while a session runs.

## Files and volumes

These are the paths in the container.

```text
/data/accounts/                   the volume bwtidy-accounts
└── HOST/
    └── EMAIL/                    one account's data, owned by its uid (mode 0700)
        ├── app/
        │   ├── bwtidy.sqlite     the queue, the Change log and the settings
        │   └── mcp-token.sha256  the hash of the MCP token
        ├── bw/                   the Bitwarden CLI's login and state
        ├── home/                 the worker's home directory
        ├── run/                  the sockets of the worker and of its bw serve
        └── worker.log            the account worker's log
/backups/                         the volume bwtidy-backups, or the directory BWTIDY_BACKUPS names
└── HOST/
    └── EMAIL/
        └── TIMESTAMP.zip.age     a backup, named by its time in UTC
/stats/                           the volume bwtidy-stats, empty unless BWTIDY_STATS=1
└── stats.db                      the usage stats
```

`bwtidy.sqlite` itself is not encrypted. A secret value is in it only
age-encrypted. Names, notes and URIs are plain text in the queue's diffs. The
Change log names the fields that each write changed.

Decrypt a backup with `age -d -o backup.zip FILE`. Its passphrase is the
master password bwtidy last logged in with, and
[step 1 of the restore](../restore.md#1-decrypt-the-backup-on-a-tmpfs) says
when it is an earlier one. Every backup holds `RESTORE.md` with the steps
that bring a lost vault back from it. The same steps are on the page
[Restore a vault from a backup](../restore.md).
