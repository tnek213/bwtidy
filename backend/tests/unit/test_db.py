"""The SQLite tables: a new file gets the one schema, a file at another version is refused."""

import sqlite3
from pathlib import Path

import pytest

from bwtidy.db import (
    CHANGE_LOG_COLUMNS,
    QUEUE_COLUMNS,
    SCHEMA_VERSION,
    Database,
    QueueStatus,
    SchemaError,
)
from bwtidy.protocol import DB_FILE

SETTINGS_COLUMNS = "key, value"
BACKUP_FINGERPRINTS_COLUMNS = "name, fingerprint"

## sqlite_master of a new file, the SQL text included, so any change to the tables is seen here
ONE_SCHEMA = [
    ("index", "change_log_at", "change_log", "CREATE INDEX change_log_at ON change_log (at)"),
    ("index", "sqlite_autoindex_backup_fingerprints_1", "backup_fingerprints", None),
    ("index", "sqlite_autoindex_settings_1", "settings", None),
    (
        "table",
        "backup_fingerprints",
        "backup_fingerprints",
        "CREATE TABLE backup_fingerprints (\n"
        "        name TEXT PRIMARY KEY,\n"
        "        fingerprint TEXT NOT NULL\n"
        "    )",
    ),
    (
        "table",
        "change_log",
        "change_log",
        "CREATE TABLE change_log (\n"
        "        id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "        at TEXT NOT NULL,\n"
        "        source TEXT NOT NULL,\n"
        "        operation_kind TEXT NOT NULL,\n"
        "        item_ids TEXT NOT NULL,\n"
        "        result TEXT NOT NULL,\n"
        "        backup TEXT NOT NULL,\n"
        "        created_item_ids TEXT NOT NULL DEFAULT '[]',\n"
        "        columns TEXT NOT NULL DEFAULT '[]',\n"
        "        restored_backup TEXT,\n"
        "        restored_change_id INTEGER,\n"
        "        folder_id TEXT\n"
        "    , attachment_ids TEXT)",
    ),
    (
        "table",
        "queue",
        "queue",
        "CREATE TABLE queue (\n"
        "        id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "        created_at TEXT NOT NULL,\n"
        "        source TEXT NOT NULL,\n"
        "        summary TEXT NOT NULL,\n"
        "        operation_json TEXT NOT NULL,\n"
        "        status TEXT NOT NULL,\n"
        "        decided_at TEXT,\n"
        "        result TEXT,\n"
        "        diff_json TEXT,\n"
        "        value_encrypted INTEGER NOT NULL DEFAULT 0,\n"
        "        edits_tool_note INTEGER NOT NULL DEFAULT 0,\n"
        "        value_gone INTEGER NOT NULL DEFAULT 0,\n"
        "        agent_summary TEXT,\n"
        "        refused_secret INTEGER NOT NULL DEFAULT 0,\n"
        "        agent_decided_at TEXT\n"
        "    )",
    ),
    (
        "table",
        "settings",
        "settings",
        "CREATE TABLE settings (\n"
        "        key TEXT PRIMARY KEY,\n"
        "        value TEXT NOT NULL\n"
        "    )",
    ),
    ("table", "sqlite_sequence", "sqlite_sequence", "CREATE TABLE sqlite_sequence(name,seq)"),
]


def sqlite_master(path: Path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        )
        return [tuple(row) for row in rows]
    finally:
        conn.close()


def user_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def set_version(path: Path, version: int) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"PRAGMA user_version = {version}")
    finally:
        conn.close()


def new_file(path: Path) -> Path:
    database = Database(path)
    database.open()
    database.close()
    return path


def test_a_new_file_has_the_one_schema(tmp_path: Path):
    path = new_file(tmp_path / DB_FILE)
    assert sqlite_master(path) == ONE_SCHEMA
    assert user_version(path) == SCHEMA_VERSION == 1


def test_an_empty_file_gets_the_schema(tmp_path: Path):
    path = tmp_path / DB_FILE
    path.touch()
    assert user_version(new_file(path)) == SCHEMA_VERSION


def test_a_file_at_another_version_is_refused_and_left_unchanged(tmp_path: Path):
    path = new_file(tmp_path / DB_FILE)
    set_version(path, SCHEMA_VERSION + 1)
    before = path.read_bytes()
    refused = f"{DB_FILE} has schema version {SCHEMA_VERSION + 1}, this bwtidy opens only version 1"
    with pytest.raises(SchemaError, match=refused):
        Database(path).open()
    assert path.read_bytes() == before


def test_a_file_with_tables_but_no_version_is_refused_and_left_unchanged(tmp_path: Path):
    path = tmp_path / DB_FILE
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("CREATE TABLE notes (text TEXT)")
    conn.close()
    before = path.read_bytes()
    with pytest.raises(SchemaError, match="has schema version 0, this bwtidy opens only version 1"):
        Database(path).open()
    assert path.read_bytes() == before


def column_names(path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def schema_of(path: Path) -> dict[str, list[str]]:
    return {
        table: column_names(path, table)
        for table in ("change_log", "queue", "settings", "backup_fingerprints")
    }


def test_a_new_file_gets_the_four_tables_and_opening_again_changes_nothing(tmp_path: Path):
    path = tmp_path / "bwtidy.sqlite"
    db = Database(path)
    db.open()
    schema = schema_of(path)
    assert schema == {
        "change_log": CHANGE_LOG_COLUMNS.split(", "),
        "queue": QUEUE_COLUMNS.split(", "),
        "settings": SETTINGS_COLUMNS.split(", "),
        "backup_fingerprints": BACKUP_FINGERPRINTS_COLUMNS.split(", "),
    }

    new_id = db.record(
        "ui", "convertItem", ["old-2"], "ok", "c.zip.age", ["new-2"], ["type", "fields.Pin"]
    )
    stored = db.entry(new_id)
    assert stored is not None
    assert stored == {
        "id": new_id,
        "at": stored["at"],
        "source": "ui",
        "operationKind": "convertItem",
        "itemIds": ["old-2"],
        "createdItemIds": ["new-2"],
        "columns": ["type", "fields.Pin"],
        "result": "ok",
        "backup": "c.zip.age",
        "restoredBackup": None,
        "restoredChangeId": None,
        "folderId": None,
        "attachmentIds": None,
    }
    assert db.entry(999) is None
    assert db.latest()[0]["id"] == new_id
    assert db.latest()[0]["columns"] == ["type", "fields.Pin"]

    restore_id = db.record(
        "ui",
        "restoreItems",
        ["old-2"],
        "ok",
        "d.zip.age",
        restored_backup="c.zip.age",
        restored_change_id=new_id,
    )
    restored = db.entry(restore_id)
    assert restored is not None
    assert restored["restoredBackup"] == "c.zip.age"
    assert restored["restoredChangeId"] == new_id

    folder_row = db.record(
        "ui", "renameFolder", [], "ok", "e.zip.age", [], ["folder"], folder_id="f1"
    )
    renamed = db.entry(folder_row)
    assert renamed is not None
    assert renamed["folderId"] == "f1"

    attachment_row = db.record(
        "mcp",
        "renameAttachment",
        ["item-1"],
        "ok",
        "f.zip.age",
        columns=["attachments"],
        attachment_ids={"before": "a1", "after": "a2"},
    )
    attachment_entry = db.entry(attachment_row)
    assert attachment_entry is not None
    assert attachment_entry["attachmentIds"] == {"before": "a1", "after": "a2"}

    queue_id = db.add_queue_entry(
        "mcp", "a summary", "{}", value_encrypted=True, edits_tool_note=True
    )
    [entry] = db.queue_entries()
    assert entry["id"] == queue_id
    assert entry["status"] == "pending"
    assert entry["value_encrypted"] == 1
    assert entry["edits_tool_note"] == 1

    db.set_setting("some_key", "some value")
    assert db.get_setting("some_key") == "some value"

    db.set_backup_fingerprint("b.zip.age", "fingerprint-1")
    assert db.backup_fingerprint("b.zip.age") == "fingerprint-1"
    assert db.backup_fingerprint("other.zip.age") is None

    db.close()
    again = Database(path)
    again.open()
    assert schema_of(path) == schema
    assert [row["id"] for row in again.latest()] == [
        attachment_row,
        folder_row,
        restore_id,
        new_id,
    ]
    assert again.get_setting("some_key") == "some value"
    again.close()


def test_record_stores_the_columns_and_latest_returns_them(tmp_path: Path):
    db = Database(tmp_path / "bwtidy.sqlite")
    db.record("ui", "trashItem", ["a"], "ok", "a.zip.age")
    db.record("mcp", "mergeColumns", ["b"], "failed", "b.zip.age", None, ["fields.PIN", "notes"])
    assert [row["columns"] for row in db.latest()] == [["fields.PIN", "notes"], []]
    db.close()


def refuse_updates_of(path: Path, entry_id: int) -> None:
    """A trigger that makes every UPDATE of this queue row fail."""
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            f"CREATE TRIGGER refuse BEFORE UPDATE ON queue WHEN OLD.id = {entry_id} "
            "BEGIN SELECT RAISE(ABORT, 'refused'); END"
        )
    conn.close()


def queue_rows(db: Database, *ids: int) -> list[tuple]:
    return [
        (row["status"], row["result"], row["diff_json"], row["operation_json"])
        for row in db.queue_entries(ids=list(ids))
    ]


def test_set_queue_statuses_records_every_row_or_none(tmp_path: Path):
    path = tmp_path / "bwtidy.sqlite"
    db = Database(path)
    first, second = (db.add_queue_entry("ui", "edit", "{}") for _ in range(2))
    db.set_queue_statuses(
        [QueueStatus(first, "approved", diff_json="[1]"), QueueStatus(second, "failed", '{"e": 1}')]
    )
    assert queue_rows(db, first, second) == [
        ("approved", None, "[1]", "{}"),
        ("failed", '{"e": 1}', None, "{}"),
    ]
    assert all(row["decided_at"] for row in db.queue_entries(ids=[first, second]))
    refuse_updates_of(path, second)
    with pytest.raises(sqlite3.IntegrityError, match="refused"):
        db.set_queue_statuses([QueueStatus(first, "pending"), QueueStatus(second, "pending")])
    assert queue_rows(db, first) == [("approved", None, "[1]", "{}")]
    db.close()


def test_reset_queue_entries_resets_every_row_or_none(tmp_path: Path):
    path = tmp_path / "bwtidy.sqlite"
    db = Database(path)
    first, second = (db.add_queue_entry("ui", "edit", "{}") for _ in range(2))
    for entry_id in (first, second):
        db.set_queue_status(entry_id, "failed", '{"e": 1}', "[1]")
    db.reset_queue_entries({first: '{"a": 1}', second: '{"b": 2}'})
    assert queue_rows(db, first, second) == [
        ("pending", None, None, '{"a": 1}'),
        ("pending", None, None, '{"b": 2}'),
    ]
    assert [row["decided_at"] for row in db.queue_entries(ids=[first, second])] == [None, None]
    for entry_id in (first, second):
        db.set_queue_status(entry_id, "failed", '{"e": 1}', "[1]")
    refuse_updates_of(path, second)
    with pytest.raises(sqlite3.IntegrityError, match="refused"):
        db.reset_queue_entries({first: '{"c": 3}', second: '{"d": 4}'})
    assert queue_rows(db, first) == [("failed", '{"e": 1}', "[1]", '{"a": 1}')]
    db.close()
