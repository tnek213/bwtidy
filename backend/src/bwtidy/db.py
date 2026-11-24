"""SQLite under the data directory: the change log and the review queue. Never a secret.

A change log row (docs/spec.md, Writes) holds the time, the source (ui or mcp), the operation
kind, the ids of the affected items, the ids of the items the operation created, the
coordinates it wrote (`login.username`, `fields.<name>`), the id of the folder a folder kind
renamed, deleted or restored, the ids of the attachment a rename replaced and of its copy, the
result and the backup taken before the operation.
Coordinates go in, though a custom field's name is vault content (docs/spec.md, Writes).
Values and error messages never go in, because either could carry a value.

A queue row (docs/spec.md, MCP and the queue) holds an operation as JSON, its summary, its
source and status, and once decided the result and the diff that was shown. The queue checks
that neither carries a secret value before it stores them: a UI edit of a secret column has
its value replaced by an age ciphertext (value_encrypted says so), and edits_tool_note marks
bwtidy's own writes to the tool note (a section save of any section). An agent's entry keeps
the summary its propose call answered as an age ciphertext (agent_summary), since a silently
refused one can hold the secret the agent guessed. refused_secret marks a silent refusal and
agent_decided_at is when the agent sees it rejected: the first decision on another entry after
it (set_queue_status).
A failed entry that the user dismisses is deleted, its ciphertext with it. A failed entry's
ciphertext is deleted at the worker's start too (value_gone says so), so it cannot be retried.

A settings row holds one key and value: the id of the tool note (docs/spec.md, The tool note),
the auto-commit settings, the age identity for queued hidden values, itself encrypted with
the master password, when the MCP token was last used (mcp/auth.py) and how the UI shows
dates and times. Never a password or a plain value.

A backup_fingerprints row holds a backup's file name and the fingerprint of the vault it
holds (docs/spec.md, Backups), removed when the retention deletes the backup.

The file holds its schema version (docs/spec.md, Schema versions). The schema has one version,
SCHEMA_VERSION: a new file gets it, a worker opens only a file at it.
"""

import json
import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

## the one schema, version 1. A file holds its version in SQLite's PRAGMA user_version
## (docs/spec.md, Schema versions). The last line of change_log is spelled the way SQLite
## records a column added with ALTER TABLE, so every file's sqlite_master holds the same text.
SCHEMA = (
    """
    CREATE TABLE change_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        source TEXT NOT NULL,
        operation_kind TEXT NOT NULL,
        item_ids TEXT NOT NULL,
        result TEXT NOT NULL,
        backup TEXT NOT NULL,
        created_item_ids TEXT NOT NULL DEFAULT '[]',
        columns TEXT NOT NULL DEFAULT '[]',
        restored_backup TEXT,
        restored_change_id INTEGER,
        folder_id TEXT
    , attachment_ids TEXT)
    """,
    """
    CREATE TABLE queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        source TEXT NOT NULL,
        summary TEXT NOT NULL,
        operation_json TEXT NOT NULL,
        status TEXT NOT NULL,
        decided_at TEXT,
        result TEXT,
        diff_json TEXT,
        value_encrypted INTEGER NOT NULL DEFAULT 0,
        edits_tool_note INTEGER NOT NULL DEFAULT 0,
        value_gone INTEGER NOT NULL DEFAULT 0,
        agent_summary TEXT,
        refused_secret INTEGER NOT NULL DEFAULT 0,
        agent_decided_at TEXT
    )
    """,
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE backup_fingerprints (
        name TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL
    )
    """,
    "CREATE INDEX change_log_at ON change_log (at)",
)
## the version a new file gets, and the only one a worker opens
SCHEMA_VERSION = 1


class SchemaError(Exception):
    """The file's schema version is not the one this code opens."""


def user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _objects(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """The file's tables and indexes by type and name, SQLite's own left out."""
    rows = conn.execute(
        "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    )
    return [(row[0], row[1]) for row in rows]


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Generator[None]:
    """One write transaction, rolled back on any error. DDL in it is rolled back too."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        ## some errors (a full disk) end the transaction themselves
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def create_schema(conn: sqlite3.Connection) -> None:
    """The schema on an empty file, in one transaction, so it is never half made."""
    with _transaction(conn):
        if user_version(conn) != 0 or _objects(conn):
            raise SchemaError("The file is no longer empty.")
        for statement in SCHEMA:
            conn.execute(statement)
        conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")


def open_current(conn: sqlite3.Connection, path: Path) -> None:
    """A new file gets the schema, any file not at SCHEMA_VERSION is refused.

    Nothing in a refused file changes.
    """
    version = user_version(conn)
    if version == SCHEMA_VERSION:
        return
    if version == 0 and not _objects(conn):
        create_schema(conn)
        return
    raise SchemaError(
        f"{path} has schema version {version}, this bwtidy opens only version {SCHEMA_VERSION}."
    )


## the id of the bwtidy note, so a note renamed elsewhere is still found
TOOL_NOTE_ID = "tool_note_id"
## auto-commit of queued UI edits (docs/spec.md, MCP and the queue)
AUTO_COMMIT_AFTER_SECONDS = "auto_commit_after_seconds"
AUTO_COMMIT_MAX_ENTRIES = "auto_commit_max_entries"
## the age identity queued hidden values are encrypted to, stored encrypted with the master
## password (docs/spec.md, MCP and the queue, Hidden values)
HIDDEN_IDENTITY = "hidden_identity"
## how the UI shows dates and times (docs/spec.md, Settings)
DATE_FORMAT = "date_format"
HIDE_TOOL_NOTE = "hide_tool_note"
## the N of the grid's Rare fields column (docs/spec.md, Grid)
RARE_FIELDS_MAX = "rare_fields_max"

QUEUE_COLUMNS = (
    "id, created_at, source, summary, operation_json, status, decided_at, result, diff_json, "
    "value_encrypted, edits_tool_note, value_gone, agent_summary, refused_secret, "
    "agent_decided_at"
)


CHANGE_LOG_COLUMNS = (
    "id, at, source, operation_kind, item_ids, result, backup, created_item_ids, columns, "
    "restored_backup, restored_change_id, folder_id, attachment_ids"
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _stored_instant(value: datetime, *, round_up: bool) -> str:
    """value in the form the at column holds, whole seconds in UTC, so the two compare as text.

    A naive value is taken as UTC. round_up moves a fraction of a second to the next second,
    so a lower bound never lets in a row of the second before it.
    """
    instant = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    whole = instant.replace(microsecond=0)
    if round_up and instant.microsecond:
        whole += timedelta(seconds=1)
    return whole.isoformat(timespec="seconds")


@dataclass(frozen=True)
class QueueStatus:
    """One decision to record (the arguments of Database.set_queue_status)."""

    entry_id: int
    status: str
    result: str | None = None
    diff_json: str | None = None


@dataclass(frozen=True)
class NewQueueEntry:
    """One queue row to store (the arguments of Database.add_queue_entry)."""

    source: str
    summary: str
    operation_json: str
    status: str = "pending"
    result: str | None = None
    diff_json: str | None = None
    value_encrypted: bool = False
    edits_tool_note: bool = False
    agent_summary: str | None = None
    refused_secret: bool = False


@dataclass(frozen=True)
class ChangeLogFilter:
    """Which change log rows GET /api/change-log answers, every part combined with AND.

    An empty part does not filter. A row matches columns when it wrote any of them, and
    item_ids when its items or the items it created hold any of them.
    """

    since: datetime | None = None
    until: datetime | None = None
    sources: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    item_ids: tuple[str, ...] = ()
    result: str | None = None

    def where(self) -> tuple[str, list[Any]]:
        """The WHERE clause, empty when nothing filters, and its parameters."""
        parts: list[str] = []
        params: list[Any] = []
        if self.since is not None:
            parts.append("at >= ?")
            params.append(_stored_instant(self.since, round_up=True))
        if self.until is not None:
            parts.append("at <= ?")
            params.append(_stored_instant(self.until, round_up=False))
        for column, values in (("source", self.sources), ("operation_kind", self.kinds)):
            if values:
                parts.append(f"{column} IN ({_marks(values)})")
                params.extend(values)
        if self.columns:
            parts.append(_any_of("columns", self.columns))
            params.extend(self.columns)
        if self.item_ids:
            either = [_any_of(column, self.item_ids) for column in ("item_ids", "created_item_ids")]
            parts.append(f"({' OR '.join(either)})")
            params.extend([*self.item_ids, *self.item_ids])
        if self.result is not None:
            parts.append("result = ?")
            params.append(self.result)
        return (f"WHERE {' AND '.join(parts)}" if parts else ""), params


def _marks(values: tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)


def _any_of(json_column: str, values: tuple[str, ...]) -> str:
    """Whether the JSON list in json_column holds any of values."""
    return f"EXISTS (SELECT 1 FROM json_each({json_column}) WHERE value IN ({_marks(values)}))"


def _change_log_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "at": row["at"],
        "source": row["source"],
        "operationKind": row["operation_kind"],
        "itemIds": json.loads(row["item_ids"]),
        "createdItemIds": json.loads(row["created_item_ids"]),
        "columns": json.loads(row["columns"]),
        "result": row["result"],
        "backup": row["backup"],
        "restoredBackup": row["restored_backup"],
        "restoredChangeId": row["restored_change_id"],
        "folderId": row["folder_id"],
        "attachmentIds": json.loads(row["attachment_ids"]) if row["attachment_ids"] else None,
    }


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        """Connect, a new file gets the current schema. Safe to call twice.

        SchemaError for a file at another version than SCHEMA_VERSION, which stays as it is.
        """
        if self._conn is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            open_current(conn, self.path)
        except BaseException:
            conn.close()
            raise
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _connection(self) -> sqlite3.Connection:
        self.open()
        assert self._conn is not None
        return self._conn

    def record(
        self,
        source: str,
        operation_kind: str,
        item_ids: list[str],
        result: str,
        backup: str,
        created_item_ids: list[str] | None = None,
        columns: list[str] | None = None,
        restored_backup: str | None = None,
        restored_change_id: int | None = None,
        folder_id: str | None = None,
        attachment_ids: dict[str, str | None] | None = None,
    ) -> int:
        conn = self._connection()
        with conn:
            cursor = conn.execute(
                "INSERT INTO change_log "
                "(at, source, operation_kind, item_ids, result, backup, created_item_ids, "
                "columns, restored_backup, restored_change_id, folder_id, attachment_ids) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _now(),
                    source,
                    operation_kind,
                    json.dumps(item_ids),
                    result,
                    backup,
                    json.dumps(created_item_ids or []),
                    json.dumps(columns or []),
                    restored_backup,
                    restored_change_id,
                    folder_id,
                    None if attachment_ids is None else json.dumps(attachment_ids),
                ),
            )
        return cursor.lastrowid or 0

    def latest(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.matching(ChangeLogFilter(), limit)[0]

    def matching(
        self, rows_filter: ChangeLogFilter, limit: int = 200
    ) -> tuple[list[dict[str, Any]], bool]:
        """The newest change log rows that match, at most limit, and whether more matched."""
        where, params = rows_filter.where()
        rows = self._connection().execute(
            f"SELECT {CHANGE_LOG_COLUMNS} FROM change_log {where} ORDER BY id DESC LIMIT ?",
            (*params, limit + 1),
        )
        entries = [_change_log_entry(row) for row in rows]
        return entries[:limit], len(entries) > limit

    def restores(self) -> list[dict[str, Any]]:
        """Every restore row that was written and names its backup, in id order, from the
        whole table (the restore marks of GET /api/change-log, see restore.restored_by)."""
        rows = self._connection().execute(
            f"SELECT {CHANGE_LOG_COLUMNS} FROM change_log WHERE operation_kind = 'restoreItems' "
            "AND result = 'ok' AND restored_backup IS NOT NULL ORDER BY id"
        )
        return [_change_log_entry(row) for row in rows]

    def entry(self, entry_id: int) -> dict[str, Any] | None:
        """One change log row by id, None when there is none."""
        row = (
            self._connection()
            .execute(f"SELECT {CHANGE_LOG_COLUMNS} FROM change_log WHERE id = ?", (entry_id,))
            .fetchone()
        )
        return None if row is None else _change_log_entry(row)

    def add_queue_entry(
        self,
        source: str,
        summary: str,
        operation_json: str,
        *,
        status: str = "pending",
        result: str | None = None,
        diff_json: str | None = None,
        value_encrypted: bool = False,
        edits_tool_note: bool = False,
        agent_summary: str | None = None,
        refused_secret: bool = False,
    ) -> int:
        """Store an entry, pending or already decided (decided now) in the same INSERT."""
        row = NewQueueEntry(
            source,
            summary,
            operation_json,
            status=status,
            result=result,
            diff_json=diff_json,
            value_encrypted=value_encrypted,
            edits_tool_note=edits_tool_note,
            agent_summary=agent_summary,
            refused_secret=refused_secret,
        )
        return self.add_queue_entries([row])[0]

    def add_queue_entries(self, rows: list[NewQueueEntry]) -> list[int]:
        """Store every entry in one transaction, so a failure stores none. Returns the ids in
        the order of rows."""
        now = _now()
        conn = self._connection()
        ids = []
        with conn:
            for row in rows:
                cursor = conn.execute(
                    "INSERT INTO queue (created_at, source, summary, operation_json, status, "
                    "decided_at, result, diff_json, value_encrypted, edits_tool_note, "
                    "agent_summary, refused_secret) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        now,
                        row.source,
                        row.summary,
                        row.operation_json,
                        row.status,
                        None if row.status == "pending" else now,
                        row.result,
                        row.diff_json,
                        int(row.value_encrypted),
                        int(row.edits_tool_note),
                        row.agent_summary,
                        int(row.refused_secret),
                    ),
                )
                ids.append(cursor.lastrowid or 0)
        return ids

    def queue_entries(
        self,
        statuses: tuple[str, ...] | None = None,
        ids: list[int] | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """Queue rows in id order, narrowed by statuses, ids and source."""
        where: list[str] = []
        params: list[Any] = []
        if statuses is not None:
            where.append(f"status IN ({', '.join('?' for _ in statuses)})")
            params.extend(statuses)
        if source is not None:
            where.append("source = ?")
            params.append(source)
        if ids is not None:
            where.append(f"id IN ({', '.join('?' for _ in ids)})")
            params.extend(ids)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        rows = self._connection().execute(
            f"SELECT {QUEUE_COLUMNS} FROM queue{clause} ORDER BY id", params
        )
        return [dict(row) for row in rows]

    def get_setting(self, key: str) -> str | None:
        row = self._connection().execute("SELECT value FROM settings WHERE key = ?", (key,))
        found = row.fetchone()
        return found["value"] if found else None

    def set_setting(self, key: str, value: str) -> None:
        conn = self._connection()
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def delete_setting(self, key: str) -> None:
        conn = self._connection()
        with conn:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    def set_backup_fingerprint(self, name: str, fingerprint: str) -> None:
        conn = self._connection()
        with conn:
            conn.execute(
                "INSERT INTO backup_fingerprints (name, fingerprint) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET fingerprint = excluded.fingerprint",
                (name, fingerprint),
            )

    def backup_fingerprint(self, name: str) -> str | None:
        """The stored fingerprint of a backup, None when none is stored for that name."""
        row = self._connection().execute(
            "SELECT fingerprint FROM backup_fingerprints WHERE name = ?", (name,)
        )
        found = row.fetchone()
        return found["fingerprint"] if found else None

    def forget_backups(self, names: list[str]) -> None:
        if not names:
            return
        conn = self._connection()
        with conn:
            conn.execute(
                f"DELETE FROM backup_fingerprints WHERE name IN ({', '.join('?' for _ in names)})",
                names,
            )

    def delete_queue_entries(self, ids: list[int], status: str) -> list[int]:
        """Delete the entries with these ids and this status. Returns the deleted ids in order.

        secure_delete overwrites the freed pages, so the stored age ciphertext of a hidden
        value does not linger in the file.
        """
        if not ids:
            return []
        conn = self._connection()
        marks = ", ".join("?" for _ in ids)
        with conn:
            conn.execute("PRAGMA secure_delete = ON")
            rows = conn.execute(
                f"DELETE FROM queue WHERE status = ? AND id IN ({marks}) RETURNING id",
                [status, *ids],
            ).fetchall()
        return sorted(row["id"] for row in rows)

    def delete_queue_entries_except(self, keep_ids: list[int]) -> int:
        """Delete every queue entry but the ones with these ids, the secure delete of
        delete_queue_entries so a stored age ciphertext does not linger. Returns the count."""
        conn = self._connection()
        with conn:
            conn.execute("PRAGMA secure_delete = ON")
            if keep_ids:
                marks = ", ".join("?" for _ in keep_ids)
                cursor = conn.execute(f"DELETE FROM queue WHERE id NOT IN ({marks})", keep_ids)
            else:
                cursor = conn.execute("DELETE FROM queue")
        return cursor.rowcount

    def clear_change_log(self) -> int:
        """Delete every change log row (never a secret, so no secure delete). Returns the count."""
        conn = self._connection()
        with conn:
            cursor = conn.execute("DELETE FROM change_log")
        return cursor.rowcount

    def clear_backup_fingerprints(self) -> int:
        """Delete every backup fingerprint row. Returns the count."""
        conn = self._connection()
        with conn:
            cursor = conn.execute("DELETE FROM backup_fingerprints")
        return cursor.rowcount

    def delete_failed_before(self, cutoff: str) -> list[int]:
        """Delete the failed entries decided before the cutoff (an ISO time), same secure delete.

        Returns the deleted ids in order. decided_at has one fixed format, so the text compares.
        """
        conn = self._connection()
        with conn:
            conn.execute("PRAGMA secure_delete = ON")
            rows = conn.execute(
                "DELETE FROM queue WHERE status = 'failed' AND decided_at < ? RETURNING id",
                (cutoff,),
            ).fetchall()
        return sorted(row["id"] for row in rows)

    def set_queue_status(
        self,
        entry_id: int,
        status: str,
        result: str | None = None,
        diff_json: str | None = None,
    ) -> None:
        """Record a decision, as set_queue_statuses does."""
        self.set_queue_statuses([QueueStatus(entry_id, status, result, diff_json)])

    def set_queue_statuses(self, rows: list[QueueStatus]) -> None:
        """Record every decision in one transaction, so a failure records none. A row's result
        and diff_json are left as they are when None.

        A decision shows the agent every silent refusal decided before this second as rejected
        from now on, like one the user rejected (docs/spec.md, MCP and the queue, Silent refusal).
        """
        now = _now()
        conn = self._connection()
        with conn:
            conn.executemany(
                "UPDATE queue SET status = ?, decided_at = ?, "
                "result = COALESCE(?, result), diff_json = COALESCE(?, diff_json) WHERE id = ?",
                [
                    (
                        row.status,
                        None if row.status == "pending" else now,
                        row.result,
                        row.diff_json,
                        row.entry_id,
                    )
                    for row in rows
                ],
            )
            if any(row.status != "pending" for row in rows):
                conn.execute(
                    "UPDATE queue SET agent_decided_at = ? WHERE refused_secret = 1 "
                    "AND agent_decided_at IS NULL AND decided_at < ?",
                    (now, now),
                )

    def reset_queue_entries(self, operations: dict[int, str]) -> None:
        """Put failed entries back to pending for a retry in one transaction, so a failure
        resets none: their result, diff and decided time cleared and each operation as given
        (refreshed for an overwrite)."""
        conn = self._connection()
        with conn:
            conn.executemany(
                "UPDATE queue SET status = 'pending', decided_at = NULL, result = NULL, "
                "diff_json = NULL, operation_json = ? WHERE id = ? AND status = 'failed'",
                [(operation, entry_id) for entry_id, operation in operations.items()],
            )

    def forget_queue_values(self, operations: dict[int, str]) -> None:
        """Replace the operations of failed entries holding a hidden value's ciphertext by the
        given ones (the value replaced by a marker) and mark their value gone.

        secure_delete overwrites the freed space, so the ciphertext does not linger in the
        file, as in delete_queue_entries.
        """
        if not operations:
            return
        conn = self._connection()
        with conn:
            conn.execute("PRAGMA secure_delete = ON")
            conn.executemany(
                "UPDATE queue SET operation_json = ?, value_encrypted = 0, value_gone = 1 "
                "WHERE id = ? AND status = 'failed' AND value_encrypted = 1",
                [(operation, entry_id) for entry_id, operation in operations.items()],
            )
