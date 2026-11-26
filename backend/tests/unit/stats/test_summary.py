"""The summary of the usage stats over raw and rolled-up days, which never names an account id,
a frame or a fingerprint."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bwtidy.stats import db
from bwtidy.stats.summary import errors, summary

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
ACCOUNTS = ("a1b2c3d4e5f60718", "f0e1d2c3b4a59687")
FINGERPRINT = "9" * 64
FRAMES = json.dumps([["api/grid.py", "grid", 40], ["items/coordinates.py", "is_secret", 12]])


def event(days: float, account: str, channel: str, operation: str, ok: bool) -> db.Event:
    at = db.stamp(NOW - timedelta(days=days))
    return db.Event(at, account, channel, operation, ok, 200 if ok else 500, 5, "1.0.0")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    conn = db.open_stats(tmp_path / "stats.db")
    one, two = ACCOUNTS
    db.write(
        conn,
        [
            event(300, one, "ui", "GET /api/grid", True),
            event(300, one, "ui", "GET /api/grid", False),
            event(2, one, "ui", "GET /api/grid", True),
            event(1, two, "ui", "GET /api/grid", True),
            event(1, two, "ui", "PUT /api/items/{item_id}", False),
            event(1, two, "mcp", "list_items", True),
            event(20, two, "mcp", "list_items", True),
        ],
        [
            db.ErrorSeen(
                db.stamp(NOW - timedelta(days=1)),
                FINGERPRINT,
                "KeyError",
                FRAMES,
                "ui",
                "PUT /api/items/{item_id}",
                "1.0.0",
            )
        ],
        [
            db.Sample(db.stamp(NOW - timedelta(hours=2)), 3, 8, 1, '{"sessions":2}'),
            db.Sample(db.stamp(NOW - timedelta(hours=2) + timedelta(minutes=1)), 5, 8, 2, None),
            db.Sample(db.stamp(NOW - timedelta(hours=1)), 1, 8, 0, '{"sessions":1}'),
        ],
    )
    db.roll_up(conn, NOW)
    return conn


def test_the_summary_of_the_raw_days(conn: sqlite3.Connection):
    found = summary(conn, 14, NOW)
    assert found["totals"] == {
        "accounts": 2,
        "sessions": 3,
        "ui_operations": 3,
        "mcp_operations": 1,
        "ok": 3,
        "failed": 1,
    }
    assert found["operations"] == [
        {"channel": "mcp", "operation": "list_items", "ok": 1, "failed": 0},
        {"channel": "ui", "operation": "GET /api/grid", "ok": 2, "failed": 0},
        {"channel": "ui", "operation": "PUT /api/items/{item_id}", "ok": 0, "failed": 1},
    ]
    assert found["errors"] == [
        {"channel": "ui", "operation": "PUT /api/items/{item_id}", "type": "KeyError", "count": 1}
    ]
    hour = (NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H")
    assert found["capacity"][0] == {
        "hour": f"{hour}:00:00Z",
        "active": 5,
        "limit": 8,
        "turned_away": 3,
    }
    assert len(found["capacity"]) == 2


def test_the_summary_reads_daily_for_the_rolled_up_days(conn: sqlite3.Connection):
    assert conn.execute("SELECT count(*) FROM events").fetchone() == (5,)
    found = summary(conn, 365, NOW)
    grid = next(op for op in found["operations"] if op["operation"] == "GET /api/grid")
    assert (grid["ok"], grid["failed"]) == (3, 1)
    assert found["totals"]["ok"] == 5
    assert found["totals"]["failed"] == 2
    assert found["totals"]["accounts"] == 2


def test_the_summary_never_names_an_account_a_frame_or_a_fingerprint(conn: sqlite3.Connection):
    text = json.dumps(summary(conn, 365, NOW))
    for hidden in (*ACCOUNTS, FINGERPRINT, "api/grid.py", "is_secret"):
        assert hidden not in text


def test_without_any_wrapper_numbers_there_are_no_sessions(tmp_path: Path):
    conn = db.open_stats(tmp_path / "stats.db")
    found = summary(conn, 14, NOW)
    assert found["totals"]["sessions"] is None
    assert found["operations"] == []


def test_the_errors_add_the_frames(conn: sqlite3.Connection):
    (error,) = errors(conn, 14, NOW)
    assert error["type"] == "KeyError"
    assert error["frames"][-1] == ["items/coordinates.py", "is_secret", 12]
    assert "fingerprint" not in error
    assert errors(conn, 0, NOW) == []
