"""The usage stats file (docs/spec.md, Usage stats): its migrations, its writes and its retention.

One SQLite file for the whole gateway, outside every account directory, in WAL mode. Its schema
version is in PRAGMA user_version and moves up through MIGRATIONS, one numbered step at a time.
A new step is one more tuple at the end of the list, never a change to an earlier one.
"""

import os
import sqlite3
import stat
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bwtidy.protocol import ACCOUNTS_DIR, STATS_FILE

## an event older than this is counted into daily and deleted, a capacity sample deleted
KEEP_DAYS = 200
## a time in the file: UTC, to the second, so the text sorts like the time
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

type Migration = tuple[str, ...]

## step N brings a file from version N - 1 to N, each step in one transaction
MIGRATIONS: list[Migration] = [
    (
        """CREATE TABLE events (
            at TEXT NOT NULL,
            account TEXT NOT NULL,
            channel TEXT NOT NULL,
            operation TEXT NOT NULL,
            ok INTEGER NOT NULL,
            status INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL,
            version TEXT NOT NULL
        )""",
        "CREATE INDEX events_at ON events (at)",
        """CREATE TABLE errors (
            fingerprint TEXT PRIMARY KEY,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            count INTEGER NOT NULL,
            exception_type TEXT NOT NULL,
            frames TEXT NOT NULL,
            channel TEXT NOT NULL,
            operation TEXT NOT NULL,
            version TEXT NOT NULL
        )""",
        """CREATE TABLE capacity (
            at TEXT NOT NULL,
            active INTEGER NOT NULL,
            "limit" INTEGER NOT NULL,
            waiting INTEGER NOT NULL,
            extra TEXT
        )""",
        "CREATE INDEX capacity_at ON capacity (at)",
        """CREATE TABLE daily (
            day TEXT NOT NULL,
            channel TEXT NOT NULL,
            operation TEXT NOT NULL,
            ok INTEGER NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (day, channel, operation, ok)
        )""",
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "INSERT INTO settings (key, value) VALUES ('salt', lower(hex(randomblob(32))))",
    ),
]


class StatsError(Exception):
    """The stats file cannot be used: a newer version, not a regular file, another owner."""


@dataclass(frozen=True)
class Event:
    at: str
    account: str
    channel: str
    operation: str
    ok: bool
    status: int
    duration_ms: int
    version: str


@dataclass(frozen=True)
class ErrorSeen:
    """One exception a worker reported, stored under its fingerprint."""

    at: str
    fingerprint: str
    exception_type: str
    ## JSON, a list of [path, function, line]
    frames: str
    channel: str
    operation: str
    version: str


@dataclass(frozen=True)
class Sample:
    at: str
    active: int
    limit: int
    waiting: int
    ## JSON of a wrapper's own numbers, None without
    extra: str | None


def stamp(when: datetime) -> str:
    return when.astimezone(UTC).strftime(TIME_FORMAT)


def stats_path(environ: Mapping[str, str] = os.environ) -> Path:
    """The stats file the gateway with this environment uses: BWTIDY_STATS_DB, else stats.db
    in the parent of BWTIDY_ACCOUNTS_DIR."""
    named = environ.get("BWTIDY_STATS_DB", "").strip()
    if named:
        return Path(named).absolute()
    accounts = environ.get("BWTIDY_ACCOUNTS_DIR", "").strip() or ACCOUNTS_DIR
    return Path(accounts).absolute().parent / STATS_FILE


def user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def check_file(path: Path) -> None:
    """Make the file when it is missing (mode 0600), and refuse one that is not a regular file
    of the gateway's own uid, a link included."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError as error:
        reason = error.strerror or type(error).__name__
        raise StatsError(f"The stats file {path} cannot be opened: {reason}") from None
    try:
        info = os.fstat(fd)
    finally:
        os.close(fd)
    if not stat.S_ISREG(info.st_mode):
        raise StatsError(f"The stats file {path} is not a regular file")
    if info.st_uid != os.geteuid():
        raise StatsError(f"The stats file {path} is not owned by the gateway's uid")


def open_stats(path: Path, migrations: Sequence[Migration] = MIGRATIONS) -> sqlite3.Connection:
    """The stats file, made or migrated to the newest version, StatsError when it cannot be.

    A file at a newer version than the code knows is closed again unchanged.
    """
    check_file(path)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    try:
        found = user_version(conn)
        if found > len(migrations):
            raise StatsError(
                f"The stats file {path} is at version {found}, "
                f"this bwtidy knows versions up to {len(migrations)}"
            )
        os.chmod(path, 0o600)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        migrate(conn, found, migrations)
    except BaseException:
        conn.close()
        raise
    return conn


def migrate(conn: sqlite3.Connection, found: int, migrations: Sequence[Migration]) -> None:
    """Every step above the file's version, each in one transaction with its version."""
    for version in range(found + 1, len(migrations) + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in migrations[version - 1]:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def read_stats(path: Path) -> sqlite3.Connection:
    """The stats file for reading, StatsError unless it is at the newest version."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        found = user_version(conn)
        if found != len(MIGRATIONS):
            raise StatsError(
                f"The stats file {path} is at version {found}, this bwtidy reads version "
                f"{len(MIGRATIONS)} (the gateway migrates it at its start)"
            )
    except BaseException:
        conn.close()
        raise
    return conn


def salt(conn: sqlite3.Connection) -> bytes:
    row = conn.execute("SELECT value FROM settings WHERE key = 'salt'").fetchone()
    return bytes.fromhex(row[0])


def write(
    conn: sqlite3.Connection,
    events: Iterable[Event],
    errors: Iterable[ErrorSeen],
    samples: Iterable[Sample],
) -> None:
    """One batch in one transaction. An error seen again counts up and moves its last_seen."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany(
            "INSERT INTO events (at, account, channel, operation, ok, status, duration_ms, version)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    e.at,
                    e.account,
                    e.channel,
                    e.operation,
                    int(e.ok),
                    e.status,
                    e.duration_ms,
                    e.version,
                )
                for e in events
            ],
        )
        conn.executemany(
            "INSERT INTO errors (fingerprint, first_seen, last_seen, count, exception_type, frames,"
            " channel, operation, version) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)"
            " ON CONFLICT (fingerprint) DO UPDATE SET last_seen = excluded.last_seen,"
            " count = count + 1, channel = excluded.channel, operation = excluded.operation,"
            " version = excluded.version",
            [
                (
                    e.fingerprint,
                    e.at,
                    e.at,
                    e.exception_type,
                    e.frames,
                    e.channel,
                    e.operation,
                    e.version,
                )
                for e in errors
            ],
        )
        conn.executemany(
            'INSERT INTO capacity (at, active, "limit", waiting, extra) VALUES (?, ?, ?, ?, ?)',
            [(s.at, s.active, s.limit, s.waiting, s.extra) for s in samples],
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def roll_up(conn: sqlite3.Connection, now: datetime, keep_days: int = KEEP_DAYS) -> None:
    """Count the events of the days before the last keep_days into daily and delete them, and
    delete the capacity samples of those days. Whole days, so a day is either in daily or in
    the events, never in both."""
    cutoff = (now.astimezone(UTC) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO daily (day, channel, operation, ok, count)"
            " SELECT substr(at, 1, 10), channel, operation, ok, count(*) FROM events"
            " WHERE at < ? GROUP BY substr(at, 1, 10), channel, operation, ok"
            " ON CONFLICT (day, channel, operation, ok) DO UPDATE SET"
            " count = count + excluded.count",
            (cutoff,),
        )
        conn.execute("DELETE FROM events WHERE at < ?", (cutoff,))
        conn.execute("DELETE FROM capacity WHERE at < ?", (cutoff,))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
