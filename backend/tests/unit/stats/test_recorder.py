"""The recorder: rows from what was seen, outcomes by status when a body was not read, the
capacity samples, and a failure that never reaches the caller."""

import asyncio
import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bwtidy.stats.parse import MCP, UI, Names
from bwtidy.stats.recorder import MAX_PENDING, Capacity, Recorder, Seen

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
NAMES = Names.from_report(
    {
        "routes": [{"path": "/api/items/{item_id}", "methods": ["PUT"]}],
        "tools": ["list_items"],
        "methods": ["tools/call"],
    }
)


def recorder(tmp_path: Path, **options) -> Recorder:
    made = Recorder(
        tmp_path / "stats.db",
        "1.0.0",
        capacity=options.pop("capacity", lambda: Capacity(2, 8, 1)),
        now=lambda: NOW,
        **options,
    )
    made.open()
    return made


def read(tmp_path: Path, query: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(tmp_path / "stats.db") as conn:
        return conn.execute(query).fetchall()


def seen(channel: str = UI, **fields: Any) -> Seen:
    values: dict[str, Any] = {
        "at": "2026-11-26T12:00:00Z",
        "account": "a1",
        "channel": channel,
        "method": "PUT",
        "path": "/api/items/x1",
        "status": 200,
        "duration_ms": 4,
        **fields,
    }
    return Seen(**values)


def call(id: int) -> bytes:
    message = {"jsonrpc": "2.0", "id": id, "method": "tools/call", "params": {"name": "list_items"}}
    return json.dumps(message).encode()


def test_without_the_names_a_ui_request_counts_as_other(tmp_path: Path):
    stats = recorder(tmp_path)
    events, _ = stats.rows([seen()])
    assert [e.operation for e in events] == ["other"]
    stats.names = NAMES
    events, _ = stats.rows([seen(), seen(status=404)])
    assert [(e.operation, e.ok) for e in events] == [
        ("PUT /api/items/{item_id}", True),
        ("PUT /api/items/{item_id}", False),
    ]


def test_an_mcp_answer_not_read_counts_by_its_status(tmp_path: Path):
    stats = recorder(tmp_path)
    stats.names = NAMES
    mcp = {"channel": MCP, "method": "POST", "path": "/mcp", "request_body": call(1)}
    events, _ = stats.rows(
        [seen(**mcp, response_body=None), seen(**mcp, response_body=None, status=502)]
    )
    assert [(e.operation, e.ok, e.status) for e in events] == [
        ("list_items", True, 200),
        ("list_items", False, 502),
    ]


def test_an_error_report_needs_a_500(tmp_path: Path):
    stats = recorder(tmp_path)
    header = json.dumps({"type": "KeyError", "frames": []})
    _, errors = stats.rows([seen(status=500, error_header=header), seen(error_header=header)])
    assert [e.exception_type for e in errors] == ["KeyError"]


def test_the_same_account_gets_the_same_id_and_another_another(tmp_path: Path):
    stats = recorder(tmp_path)
    first = stats.account_id("https://vault.example user@example.test")
    assert first == stats.account_id("https://vault.example user@example.test")
    assert first != stats.account_id("https://vault.example other@example.test")
    assert len(first) == 16
    assert int(first, 16) >= 0


def test_a_sample_keeps_the_extra_and_survives_a_failing_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    def broken() -> dict[str, int]:
        raise RuntimeError("secret")

    stats = recorder(tmp_path, extra=lambda: {"sessions": 2})
    stats.sample()
    stats.flush()
    failing = recorder(tmp_path, extra=broken)
    failing.sample()
    failing.flush()
    rows = read(tmp_path, 'SELECT at, active, "limit", waiting, extra FROM capacity')
    assert rows == [("2026-11-26T12:00:00Z", 2, 8, 1, '{"sessions":2}')]
    assert "secret" not in caplog.text


def test_more_than_the_pending_limit_is_dropped_with_one_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    stats = recorder(tmp_path)
    caplog.set_level(logging.WARNING)
    for _ in range(MAX_PENDING + 5):
        stats.seen(seen())
    seen_now, _ = stats.take()
    assert len(seen_now) == MAX_PENDING
    assert caplog.text.count("dropped") == 1


def test_stop_writes_what_is_left_and_closes(tmp_path: Path):
    stats = recorder(tmp_path)
    stats.seen(seen())
    asyncio.run(stats.stop(None))
    assert read(tmp_path, "SELECT count(*) FROM events") == [(1,)]
