"""The usage stats summed up (docs/spec.md, Usage stats): what a page or `bwtidy stats` shows.

summary() never answers an account id, a frame or a fingerprint. The days whose events were
rolled up count from daily, which has no accounts. errors() adds the frames, for the operator
who runs the command where the gateway runs.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from bwtidy.stats.db import stamp
from bwtidy.stats.parse import MCP, UI


def since(days: int, now: datetime | None) -> str:
    return stamp((now or datetime.now(UTC)) - timedelta(days=days))


def summary(conn: sqlite3.Connection, days: int, now: datetime | None = None) -> dict[str, Any]:
    """The totals, each operation's ok and failed counts per channel, the errors by operation
    and type, and the capacity per hour, of the last `days` days."""
    start = since(days, now)
    counts: dict[tuple[str, str], dict[str, int]] = {}
    rows = conn.execute(
        "SELECT channel, operation, ok, count(*) FROM events WHERE at >= ?"
        " GROUP BY channel, operation, ok"
        " UNION ALL SELECT channel, operation, ok, sum(count) FROM daily WHERE day >= ?"
        " GROUP BY channel, operation, ok",
        (start, start[:10]),
    ).fetchall()
    for channel, operation, ok, count in rows:
        entry = counts.setdefault((channel, operation), {"ok": 0, "failed": 0})
        entry["ok" if ok else "failed"] += count
    order = sorted(
        counts, key=lambda key: (key[0], -(counts[key]["ok"] + counts[key]["failed"]), key[1])
    )
    accounts = conn.execute(
        "SELECT count(DISTINCT account) FROM events WHERE at >= ?", (start,)
    ).fetchone()[0]
    sessions = conn.execute(
        "SELECT sum(json_extract(extra, '$.sessions')) FROM capacity WHERE at >= ?"
        " AND json_valid(extra) AND json_type(extra, '$.sessions') = 'integer'",
        (start,),
    ).fetchone()[0]

    def total(kind: str, channel: str | None = None) -> int:
        return sum(
            entry[kind] for key, entry in counts.items() if channel is None or key[0] == channel
        )

    errors = conn.execute(
        "SELECT channel, operation, exception_type, sum(count) AS total FROM errors"
        " WHERE last_seen >= ? GROUP BY channel, operation, exception_type"
        " ORDER BY total DESC, channel, operation, exception_type",
        (start,),
    ).fetchall()
    capacity = conn.execute(
        'SELECT substr(at, 1, 13), max(active), max("limit"), sum(waiting) FROM capacity'
        " WHERE at >= ? GROUP BY substr(at, 1, 13) ORDER BY 1",
        (start,),
    ).fetchall()
    return {
        "days": days,
        "since": start,
        "totals": {
            "accounts": accounts,
            "sessions": sessions,
            "ui_operations": total("ok", UI) + total("failed", UI),
            "mcp_operations": total("ok", MCP) + total("failed", MCP),
            "ok": total("ok"),
            "failed": total("failed"),
        },
        "operations": [
            {"channel": channel, "operation": operation, **counts[channel, operation]}
            for channel, operation in order
        ],
        "errors": [
            {"channel": channel, "operation": operation, "type": kind, "count": count}
            for channel, operation, kind, count in errors
        ],
        "capacity": [
            {"hour": f"{hour}:00:00Z", "active": active, "limit": limit, "turned_away": waiting}
            for hour, active, limit, waiting in capacity
        ],
    }


def errors(
    conn: sqlite3.Connection, days: int, now: datetime | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """The errors last seen in the last `days` days, the most frequent first, with their frames
    (innermost last), without their fingerprints."""
    rows = conn.execute(
        "SELECT exception_type, channel, operation, count, first_seen, last_seen, frames"
        " FROM errors WHERE last_seen >= ? ORDER BY count DESC, last_seen DESC LIMIT ?",
        (since(days, now), limit),
    ).fetchall()
    return [
        {
            "type": kind,
            "channel": channel,
            "operation": operation,
            "count": count,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "frames": json.loads(frames),
        }
        for kind, channel, operation, count, first_seen, last_seen, frames in rows
    ]
