"""The usage stats file (docs/spec.md, Schema versions and Usage stats): its migrations, the
refusal of a newer file and the retention."""

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bwtidy.stats import db

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
TABLES = ["capacity", "daily", "errors", "events", "settings"]


def tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    return [name for (name,) in rows]


def event(at: datetime, operation: str = "GET /api/grid", ok: bool = True) -> db.Event:
    return db.Event(db.stamp(at), "abc", "ui", operation, ok, 200 if ok else 500, 3, "1.0.0")


def sample(at: datetime) -> db.Sample:
    return db.Sample(db.stamp(at), 1, 8, 0, None)


def test_a_new_file_gets_step_one_and_version_one(tmp_path: Path):
    path = tmp_path / "stats.db"
    conn = db.open_stats(path)
    assert db.user_version(conn) == 1 == len(db.MIGRATIONS)
    assert tables(conn) == TABLES
    assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert len(db.salt(conn)) == 32
    conn.close()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_uid == os.geteuid()


def test_a_file_at_version_one_is_left_alone(tmp_path: Path):
    path = tmp_path / "stats.db"
    conn = db.open_stats(path)
    salt = db.salt(conn)
    db.write(conn, [event(NOW)], [], [])
    conn.close()
    again = db.open_stats(path)
    assert db.user_version(again) == 1
    assert db.salt(again) == salt
    assert again.execute("SELECT count(*) FROM events").fetchone() == (1,)


def test_a_new_step_migrates_a_file_at_version_one(tmp_path: Path):
    path = tmp_path / "stats.db"
    conn = db.open_stats(path)
    db.write(conn, [event(NOW)], [], [])
    conn.close()
    step_two = ("ALTER TABLE events ADD COLUMN region TEXT", "CREATE TABLE notes (text TEXT)")
    migrated = db.open_stats(path, [*db.MIGRATIONS, step_two])
    assert db.user_version(migrated) == 2
    assert "notes" in tables(migrated)
    assert migrated.execute("SELECT operation, region FROM events").fetchall() == [
        ("GET /api/grid", None)
    ]


def test_a_failing_step_leaves_the_version_before_it(tmp_path: Path):
    path = tmp_path / "stats.db"
    db.open_stats(path).close()
    broken = ("CREATE TABLE fine (x)", "CREATE TABLE events (x)")
    with pytest.raises(sqlite3.OperationalError):
        db.open_stats(path, [*db.MIGRATIONS, broken])
    conn = db.open_stats(path)
    assert db.user_version(conn) == 1
    assert "fine" not in tables(conn)


def test_a_newer_file_fails_the_open_and_stays_unchanged(tmp_path: Path):
    path = tmp_path / "stats.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE future (x)")
        conn.execute("PRAGMA user_version = 7")
    path.chmod(0o644)
    before = path.read_bytes()
    with pytest.raises(db.StatsError, match=f"{path} is at version 7, .* up to 1"):
        db.open_stats(path)
    assert path.read_bytes() == before
    assert path.stat().st_mode & 0o777 == 0o644


def test_a_link_is_refused(tmp_path: Path):
    target = tmp_path / "elsewhere.db"
    db.open_stats(target).close()
    link = tmp_path / "stats.db"
    link.symlink_to(target)
    with pytest.raises(db.StatsError, match="cannot be opened"):
        db.open_stats(link)


def test_reading_needs_the_newest_version(tmp_path: Path):
    path = tmp_path / "stats.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 9")
    with pytest.raises(db.StatsError, match="at version 9"):
        db.read_stats(path)


def test_the_retention_rolls_old_events_into_daily(tmp_path: Path):
    conn = db.open_stats(tmp_path / "stats.db")
    old = NOW - timedelta(days=201)
    db.write(
        conn,
        [event(old), event(old, ok=False), event(old + timedelta(hours=1)), event(NOW)],
        [],
        [sample(old), sample(NOW)],
    )
    db.roll_up(conn, NOW)
    assert conn.execute("SELECT at FROM events").fetchall() == [(db.stamp(NOW),)]
    assert conn.execute("SELECT at FROM capacity").fetchall() == [(db.stamp(NOW),)]
    day = old.strftime("%Y-%m-%d")
    assert conn.execute("SELECT * FROM daily ORDER BY ok").fetchall() == [
        (day, "ui", "GET /api/grid", 0, 1),
        (day, "ui", "GET /api/grid", 1, 2),
    ]
    db.write(conn, [event(old)], [], [])
    db.roll_up(conn, NOW)
    assert conn.execute("SELECT count FROM daily WHERE ok = 1").fetchall() == [(3,)]


def test_the_retention_keeps_the_last_200_days_and_every_error(tmp_path: Path):
    conn = db.open_stats(tmp_path / "stats.db")
    kept = NOW - timedelta(days=199)
    old = db.stamp(NOW - timedelta(days=400))
    error = db.ErrorSeen(old, "f" * 64, "KeyError", "[]", "ui", "GET /api/grid", "1.0.0")
    db.write(conn, [event(kept)], [error], [sample(kept)])
    db.roll_up(conn, NOW)
    assert conn.execute("SELECT count(*) FROM events").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM capacity").fetchone() == (1,)
    assert conn.execute("SELECT count, last_seen FROM errors").fetchall() == [(1, old)]


def test_an_error_seen_again_counts_up(tmp_path: Path):
    conn = db.open_stats(tmp_path / "stats.db")
    first, later = db.stamp(NOW - timedelta(days=1)), db.stamp(NOW)
    seen = [
        db.ErrorSeen(at, "f" * 64, "KeyError", "[]", "ui", "GET /api/grid", "1.0.0")
        for at in (first, later)
    ]
    db.write(conn, [], seen[:1], [])
    db.write(conn, [], seen[1:], [])
    assert conn.execute("SELECT first_seen, last_seen, count FROM errors").fetchall() == [
        (first, later, 2)
    ]


def test_the_path_follows_the_environment(tmp_path: Path):
    assert db.stats_path({}) == Path("/data/stats.db")
    assert db.stats_path({"BWTIDY_ACCOUNTS_DIR": str(tmp_path / "a")}) == tmp_path / "stats.db"
    named = {"BWTIDY_STATS_DB": str(tmp_path / "s.db"), "BWTIDY_ACCOUNTS_DIR": "/x/a"}
    assert db.stats_path(named) == tmp_path / "s.db"
