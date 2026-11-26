"""What the gateway, the worker and the frontend agree on (docs/spec.md, Gateway).

The rule: a string that two of the three sides read lives here, so the gateway imports it from
this module and never from the worker's. The frontend has its own copy of the cookie names and
headers as literals.
"""

import re
from datetime import UTC, datetime

## the browser's routing cookie, set by the gateway: which account's worker a request goes to
ACCOUNT_COOKIE = "bwtidy_account"
## the session cookie, set by the worker at the unlock
SESSION_COOKIE = "bwtidy_session"

## a request the page sends on its own (a poll), which never counts as user activity
BACKGROUND_HEADER = "X-Bwtidy-Background"
## a cross-site request with this custom header needs a CORS preflight, never answered, so
## another site's page cannot post the account's deletes or POST /api/bw-logout
REQUEST_HEADER = "X-Bwtidy-Request"
## the login token that every call of a Bitwarden CLI login carries after POST /api/bw-login
LOGIN_HEADER = "X-Bwtidy-Login"
## "true" when more change log rows matched than the answer holds
MORE_HEADER = "X-Bwtidy-More"

## the detail of a 502 from the gateway (no worker answers) and from a worker on its way out,
## which the frontend reads as "start the account's worker again and repeat the request"
WORKER_GONE = "The account's worker does not answer"

## where the gateway keeps the account directories unless BWTIDY_ACCOUNTS_DIR names another
ACCOUNTS_DIR = "/data/accounts"

## the usage stats (docs/spec.md, Usage stats): the file in the parent of the accounts
## directory, the variable the gateway sets for a worker it starts with stats on, the worker's
## path (never proxied) that lists its route templates and tool names, and the header of a
## worker's 500 that carries the exception's type and frames, which the gateway always strips
STATS_FILE = "stats.db"
STATS_REPORT_ENV = "BWTIDY_STATS_REPORT"
STATS_NAMES_PATH = "/stats/names"
STATS_ERROR_HEADER = "X-Bwtidy-Stats-Error"

## the account directory: its subdirectories, the pending marker of an account whose first
## unlock has not happened and the worker's log
SUBDIRS = ("app", "bw", "run", "home")
PENDING_MARKER = ".pending"
WORKER_LOG = "worker.log"
## the files in app/: the SQLite file, the MCP token's hash, the CLI's login state and the file
## a worker writes to ask the gateway to delete the whole account directory when the worker
## exits (docs/spec.md, Settings, Deleting data, and Gateway)
DB_FILE = "bwtidy.sqlite"
TOKEN_FILE = "mcp-token.sha256"
LOGIN_STATE_FILE = "bw-login-state"
DELETE_MARKER = "delete-requested"

## a backup is <UTC timestamp>.zip.age
BACKUP_SUFFIX = ".zip.age"
BACKUP_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
BACKUP_NAME = re.compile(r"^(\d{8}T\d{6}Z)\.zip\.age$")


def backup_time(name: str) -> datetime | None:
    """The UTC time in a backup file name, None for any other file."""
    match = BACKUP_NAME.match(name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), BACKUP_TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def backup_name(when: datetime) -> str:
    return when.astimezone(UTC).strftime(BACKUP_TIME_FORMAT) + BACKUP_SUFFIX
