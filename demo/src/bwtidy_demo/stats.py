"""The demo's usage stats (dev/demo.md, Usage stats): what it adds to bwtidy's, and the panel.

bwtidy's gateway records them (docs/spec.md, Usage stats). The demo labels each account's events
with its four-word vault id, and adds to every capacity sample what only the demo knows: the
vaults started and the visitors turned away since the last sample, how long the starts took,
and the controller's ready and building vaults and DEMO_MAX.

The panel under the start page and GET /demo/stats.json show aggregates only: no vault id, no
visitor's own timeline, no frames and no fingerprints. Both read one summary, made at most once
per CACHE_SECONDS, so reloads never reach the database more often.
"""

import asyncio
import html
import logging
import math
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bwtidy.gateway.accounts import AccountKey
from bwtidy.stats.db import read_stats, stamp
from bwtidy.stats.parse import MCP, UI
from bwtidy.stats.summary import since, summary

log = logging.getLogger(__name__)

## what the summary covers: the 200 days bwtidy keeps every event of
DAYS = 200
## what the chart shows, an hour per point
CHART_DAYS = 14
CACHE_SECONDS = 60.0
## how often the controller's counts are asked for, so a sample is never far behind them
STATUS_EVERY = 20.0
CHANNELS = {UI: "Web UI", MCP: "MCP"}
## the files GET /demo/static/NAME serves, from the package's static folder
STATIC = {
    "uplot/uPlot.iife.min.js": "text/javascript; charset=utf-8",
    "uplot/uPlot.min.css": "text/css; charset=utf-8",
    "usage.js": "text/javascript; charset=utf-8",
}
LINE = (
    "The demo counts anonymous usage (sessions, operations, errors) and shows it here. "
    "It stores no addresses and nothing you type."
)
UNAVAILABLE = "The usage numbers cannot be shown right now."
## the controller's counts a sample keeps, by their name in GET /status
STATUS_FIELDS = ("ready", "building", "max")


def vault_id(key: AccountKey) -> str:
    """The label of a demo account's events: its four-word vault id, the email's local part."""
    return key.email.partition("@")[0]


class Counters:
    """What the demo saw since the last capacity sample, in RAM. extra() takes it."""

    def __init__(self) -> None:
        self.sessions = 0
        self.turned_away = 0
        self.wait_ms_total = 0
        self.wait_ms_max = 0
        ## the controller's last counts, None while it does not answer
        self.status: dict[str, int] | None = None

    def started(self, seconds: float) -> None:
        """A visitor got a vault, after waiting this long on the start page."""
        waited = max(0, round(seconds * 1000))
        self.sessions += 1
        self.wait_ms_total += waited
        self.wait_ms_max = max(self.wait_ms_max, waited)

    def refused(self) -> None:
        """A start found no free vault or no room, the visitor was turned away."""
        self.turned_away += 1

    def extra(self) -> dict[str, int]:
        """The sample's extra (create_gateway_app's stats_extra), the counts set back to 0."""
        found = {
            "sessions": self.sessions,
            "turned_away": self.turned_away,
            "wait_ms_total": self.wait_ms_total,
            "wait_ms_max": self.wait_ms_max,
            **(self.status or {}),
        }
        self.sessions = self.turned_away = self.wait_ms_total = self.wait_ms_max = 0
        return found


def status_counts(data: Any) -> dict[str, int] | None:
    """The fields of the controller's GET /status a sample keeps, None for another answer."""
    if not isinstance(data, dict):
        return None
    found = {name: data.get(name) for name in STATUS_FIELDS}
    if not all(type(value) is int for value in found.values()):
        return None
    return found


def number(name: str) -> str:
    """SQL for an integer of the sample's extra, NULL for anything else."""
    return (
        f"CASE WHEN json_valid(extra) AND json_type(extra, '$.{name}') = 'integer'"
        f" THEN json_extract(extra, '$.{name}') END"
    )


def extras(conn: sqlite3.Connection, start: str) -> dict[str, Any]:
    """The demo's own totals of the samples since start."""
    vaults, away, waited, longest = conn.execute(
        f"SELECT coalesce(sum({number('sessions')}), 0), coalesce(sum({number('turned_away')}), 0),"
        f" coalesce(sum({number('wait_ms_total')}), 0), max({number('wait_ms_max')})"
        " FROM capacity WHERE at >= ?",
        (start,),
    ).fetchone()
    return {
        "vaults_started": vaults,
        "turned_away": away,
        "wait_seconds_average": round(waited / vaults / 1000, 1) if vaults else None,
        "wait_seconds_longest": round(longest / 1000, 1) if longest else None,
    }


def today(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    """The numbers since midnight UTC."""
    start = stamp(now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0))
    counts = {(UI, 1): 0, (UI, 0): 0, (MCP, 1): 0, (MCP, 0): 0}
    for channel, ok, count in conn.execute(
        "SELECT channel, ok, count(*) FROM events WHERE at >= ? GROUP BY channel, ok", (start,)
    ):
        counts[channel, ok] = counts.get((channel, ok), 0) + count
    found = extras(conn, start)
    return {
        "since": start,
        "vaults_started": found["vaults_started"],
        "turned_away": found["turned_away"],
        "ui_operations": counts[UI, 1] + counts[UI, 0],
        "mcp_operations": counts[MCP, 1] + counts[MCP, 0],
        "ok": sum(count for (_, ok), count in counts.items() if ok),
        "failed": sum(count for (_, ok), count in counts.items() if not ok),
    }


def hours(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    """Each hour of the last CHART_DAYS with a sample: the most sessions at once, the most
    there may be (DEMO_MAX, else the gateway's cap) and the visitors turned away."""
    rows = conn.execute(
        f'SELECT substr(at, 1, 13), max(active), max("limit"), max({number("max")}),'
        f" coalesce(sum({number('turned_away')}), 0) FROM capacity WHERE at >= ?"
        " GROUP BY substr(at, 1, 13) ORDER BY 1",
        (since(CHART_DAYS, now),),
    ).fetchall()
    return [
        {
            "hour": f"{hour}:00:00Z",
            "active": active,
            "capacity": demo_max if demo_max is not None else limit,
            "turned_away": away,
        }
        for hour, active, limit, demo_max, away in rows
    ]


def read_usage(path: Path, now: datetime) -> dict[str, Any]:
    """What /demo/stats.json answers and the panel shows: bwtidy's summary of the last DAYS days
    and the demo's own numbers. It never holds a vault id."""
    conn = read_stats(path)
    try:
        found = summary(conn, DAYS, now)
        return {
            **found,
            "demo": {
                **extras(conn, found["since"]),
                "today": today(conn, now),
                "chart_days": CHART_DAYS,
                "hours": hours(conn, now),
            },
        }
    finally:
        conn.close()


def utc_now() -> datetime:
    return datetime.now(UTC)


class UsageCache:
    """The usage of read_usage, read again at most once per CACHE_SECONDS. A read that fails
    is None for as long, so a broken file is not tried on every reload either."""

    def __init__(
        self,
        path: Path,
        ttl: float = CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.path = path
        self.ttl = ttl
        self._clock = clock
        self._now = now
        self._value: dict[str, Any] | None = None
        self._at: float | None = None
        self._lock = asyncio.Lock()
        self._failing = False

    def _fresh(self) -> bool:
        return self._at is not None and self._clock() - self._at < self.ttl

    async def get(self) -> dict[str, Any] | None:
        if self._fresh():
            return self._value
        async with self._lock:
            if self._fresh():
                return self._value
            try:
                value = await asyncio.to_thread(read_usage, self.path, self._now())
                self._failing = False
            except Exception as error:
                if not self._failing:
                    text = str(error) if isinstance(error, sqlite3.Error) else ""
                    log.warning("Reading the usage stats failed: %s %s", type(error).__name__, text)
                self._failing = True
                value = None
            self._value, self._at = value, self._clock()
            return value


def count(value: int | None) -> str:
    return f"{value or 0:,}"


def share(ok: int, failed: int) -> str:
    done = ok + failed
    if not done:
        return "none yet"
    ## rounded down, so a single failure never shows as 100%
    return f"{math.floor(1000 * ok / done) / 10:g}%"


def seconds(value: float | None) -> str:
    return "none yet" if value is None else f"{value:g} s"


def tile(value: str, label: str, today_text: str) -> str:
    return (
        f'<div class="tile"><span class="value">{value}</span>'
        f'<span class="label">{label}</span>'
        f'<span class="today">{today_text}</span></div>'
    )


def table(caption: str, head: list[str], rows: list[list[str]], numeric: set[int]) -> str:
    def cell(tag: str, index: int, text: str) -> str:
        kind = ' class="num"' if index in numeric else ""
        return f"<{tag}{kind}>{text}</{tag}>"

    header = "".join(cell("th", i, html.escape(text)) for i, text in enumerate(head))
    body = "".join(
        "<tr>" + "".join(cell("td", i, text) for i, text in enumerate(row)) + "</tr>"
        for row in rows
    )
    return (
        f'<table><caption class="sr-only">{html.escape(caption)}</caption>'
        f"<thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"
    )


def operation_label(operation: str) -> str:
    return f"<code>{html.escape(operation)}</code>"


def render(usage: dict[str, Any] | None) -> str:
    """The Usage section of the start page, numbers and tables in the HTML itself. The chart is
    drawn over it by usage.js, a page without JavaScript loses nothing but the picture."""
    if usage is None:
        return (
            '<section class="card usage" aria-labelledby="usage-title">'
            f'<h2 id="usage-title">Usage</h2><p>{LINE}</p><p class="note">{UNAVAILABLE}</p>'
            "</section>"
        )
    totals = usage["totals"]
    demo = usage["demo"]
    day = demo["today"]
    tiles = "".join(
        [
            tile(
                count(demo["vaults_started"]),
                "demo vaults used",
                f"{count(day['vaults_started'])} today",
            ),
            tile(
                count(totals["ui_operations"]),
                "UI operations",
                f"{count(day['ui_operations'])} today",
            ),
            tile(
                count(totals["mcp_operations"]),
                "MCP calls",
                f"{count(day['mcp_operations'])} today",
            ),
            tile(
                share(totals["ok"], totals["failed"]),
                "succeeded",
                f"{share(day['ok'], day['failed'])} today",
            ),
        ]
    )
    away = demo["turned_away"]
    facts = [
        f"In the last {usage['days']} days, today counted from midnight UTC.",
        f"Turned away because the demo was full: {count(away)}"
        f" ({count(day['turned_away'])} today).",
    ]
    if demo["wait_seconds_average"] is not None:
        facts.append(
            f"Starting a demo took {seconds(demo['wait_seconds_average'])} on average"
            f" and {seconds(demo['wait_seconds_longest'])} at most."
        )
    chart_rows = [
        [
            html.escape(hour["hour"][:13].replace("T", " ") + ":00"),
            count(hour["active"]),
            count(hour["capacity"]),
            count(hour["turned_away"]),
        ]
        for hour in demo["hours"]
    ]
    chart_table = (
        table(
            "Sessions per hour",
            ["Hour (UTC)", "Active sessions", "Capacity", "Turned away"],
            chart_rows,
            {1, 2, 3},
        )
        if chart_rows
        else "<p>No hours sampled yet.</p>"
    )
    operations = [
        [
            html.escape(CHANNELS.get(op["channel"], op["channel"])),
            operation_label(op["operation"]),
            count(op["ok"]),
            count(op["failed"]),
        ]
        for op in usage["operations"]
    ]
    operations_table = (
        table(
            "Operations by channel",
            ["Channel", "Operation", "Succeeded", "Failed"],
            operations,
            {2, 3},
        )
        if operations
        else "<p>No operations yet.</p>"
    )
    errors = [
        [
            html.escape(CHANNELS.get(error["channel"], error["channel"])),
            operation_label(error["operation"]),
            f"<code>{html.escape(error['type'])}</code>",
            count(error["count"]),
        ]
        for error in usage["errors"]
    ]
    errors_table = (
        table("Errors by operation", ["Channel", "Operation", "Error", "Times"], errors, {3})
        if errors
        else "<p>No errors so far.</p>"
    )
    days = demo["chart_days"]
    return f"""<section class="card usage" aria-labelledby="usage-title">
  <h2 id="usage-title">Usage</h2>
  <p>{LINE}</p>
  <div class="tiles">{tiles}</div>
  <p class="facts">{" ".join(facts)}</p>
  <h3>Sessions per hour, last {days} days</h3>
  <div id="usage-chart" class="chart" data-src="/demo/stats.json" aria-hidden="true" hidden></div>
  <details>
    <summary>The chart as a table</summary>
    {chart_table}
  </details>
  <h3>Operations</h3>
  {operations_table}
  <h3>Errors</h3>
  <p>Per operation, the type of an error bwtidy did not expect and how often it happened.</p>
  {errors_table}
</section>
<script src="/demo/static/uplot/uPlot.iife.min.js" defer></script>
<script src="/demo/static/usage.js" defer></script>"""


def head() -> str:
    """What the start page's head gets with the stats on: uPlot's stylesheet."""
    return '<link rel="stylesheet" href="/demo/static/uplot/uPlot.min.css" />'
