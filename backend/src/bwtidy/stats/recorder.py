"""The gateway's recorder of the usage stats (docs/spec.md, Usage stats).

The request path only hands a `Seen` to `Recorder.seen`, an append in RAM. Every FLUSH_EVERY
seconds `run` turns what was seen into rows and writes them in one transaction in a thread,
takes a capacity sample every SAMPLE_EVERY seconds and rolls old events up once a day. A
failing write is logged in one line and its rows are dropped, a request never waits for it.
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bwtidy.stats import db
from bwtidy.stats.parse import MCP, OTHER, Names, error_report, mcp_calls, outcomes

log = logging.getLogger(__name__)

FLUSH_EVERY = 5.0
SAMPLE_EVERY = 60.0
RETENTION_EVERY = 24 * 3600.0
## what is kept in RAM between two flushes at most, more is dropped
MAX_PENDING = 10_000
## an account's opaque id: this many hex characters of the HMAC of its key
ACCOUNT_ID_LENGTH = 16


@dataclass(frozen=True)
class Capacity:
    """The workers that run or start, the most that may, those turned away since the last."""

    active: int
    limit: int
    turned_away: int


@dataclass(frozen=True)
class Seen:
    """One request that reached a worker, as the gateway saw it, in RAM until the next flush.

    The path and the bodies are what passed through the proxy: they become an operation and an
    outcome at the flush and are never written.
    """

    at: str
    account: str
    channel: str
    method: str
    path: str
    status: int
    duration_ms: int
    request_body: bytes | None = None
    content_type: str = ""
    response_body: bytes | None = None
    error_header: str | None = None


def utc_now() -> datetime:
    return datetime.now(UTC)


def http_ok(status: int) -> bool:
    return 200 <= status < 400


class Recorder:
    def __init__(
        self,
        path: Path,
        version: str,
        capacity: Callable[[], Capacity],
        extra: Callable[[], Any] | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.path = path
        self.version = version
        ## learned from the first worker that answers STATS_NAMES_PATH (gateway/stats.py)
        self.names: Names | None = None
        self._capacity = capacity
        self._extra = extra
        self._now = now
        self._conn: sqlite3.Connection | None = None
        self._salt = b""
        self._seen: list[Seen] = []
        self._samples: list[db.Sample] = []
        ## one writer at a time on the connection: the flush thread, the roll-up, the close
        self._lock = threading.Lock()
        ## so a failure that repeats is logged once, until a write succeeds again
        self._failing = False
        self._dropped = False

    def open(self) -> None:
        """Open or migrate the file and roll up the old events, StatsError when the file cannot
        be used (logged here with the file, its version and the newest one known)."""
        try:
            conn = db.open_stats(self.path)
        except db.StatsError as error:
            log.error("%s", error)
            raise
        except sqlite3.Error as error:
            log.error("The stats file %s cannot be opened: %s", self.path, error)
            raise db.StatsError(f"The stats file {self.path} cannot be opened") from None
        self._conn = conn
        self._salt = db.salt(conn)
        self.roll_up()

    def account_id(self, key: str) -> str:
        """An opaque id of the account: the same account always gets the same, its key never
        shows."""
        digest = hmac.new(self._salt, key.encode(), hashlib.sha256).hexdigest()
        return digest[:ACCOUNT_ID_LENGTH]

    def seen(self, item: Seen) -> None:
        if len(self._seen) >= MAX_PENDING:
            if not self._dropped:
                log.warning("The usage stats dropped requests, more came than they can write")
                self._dropped = True
            return
        self._seen.append(item)

    def sample(self) -> None:
        """A capacity sample, with the wrapper's own numbers in extra when it gives them."""
        try:
            capacity = self._capacity()
            extra = None
            if self._extra is not None:
                given = self._extra()
                extra = None if given is None else json.dumps(given, separators=(",", ":"))
        except Exception as error:
            log.warning("The usage stats' capacity sample failed: %s", type(error).__name__)
            return
        self._samples.append(
            db.Sample(
                db.stamp(self._now()),
                capacity.active,
                capacity.limit,
                capacity.turned_away,
                extra,
            )
        )

    def take(self) -> tuple[list[Seen], list[db.Sample]]:
        """What was seen and sampled since the last take, in the event loop's thread."""
        seen, self._seen = self._seen, []
        samples, self._samples = self._samples, []
        self._dropped = False
        return seen, samples

    def flush(self) -> None:
        """Write what was seen and sampled, from the event loop's thread."""
        self.write(*self.take())

    def write(self, seen: list[Seen], samples: list[db.Sample]) -> None:
        """Turn the requests into rows and write them in one transaction. Any failure is logged
        in one line, by its type and SQLite's text only, and drops the rows."""
        if not seen and not samples:
            return
        try:
            events, errors = self.rows(seen)
            with self._lock:
                if self._conn is None:
                    return
                db.write(self._conn, events, errors, samples)
        except Exception as error:
            if not self._failing:
                text = str(error) if isinstance(error, sqlite3.Error) else ""
                log.warning("Writing the usage stats failed: %s %s", type(error).__name__, text)
            self._failing = True
            return
        self._failing = False

    def rows(self, seen: list[Seen]) -> tuple[list[db.Event], list[db.ErrorSeen]]:
        events: list[db.Event] = []
        errors: list[db.ErrorSeen] = []
        names = self.names
        for item in seen:
            if item.channel == MCP:
                calls = mcp_calls(names, item.method, item.request_body)
                answered = outcomes(item.content_type, item.response_body)
                results = [
                    (call.operation, http_ok(item.status) and answered.get(call.id or "", True))
                    for call in calls
                ]
            else:
                operation = names.ui_operation(item.method, item.path) if names else OTHER
                results = [(operation, http_ok(item.status))]
            for operation, ok in results:
                events.append(
                    db.Event(
                        item.at,
                        item.account,
                        item.channel,
                        operation,
                        ok,
                        item.status,
                        item.duration_ms,
                        self.version,
                    )
                )
            report = error_report(item.error_header) if item.status == 500 else None
            if report is not None:
                frames = json.dumps([list(frame) for frame in report.frames])
                fingerprint = hashlib.sha256(
                    json.dumps([report.exception_type, frames]).encode()
                ).hexdigest()
                errors.append(
                    db.ErrorSeen(
                        item.at,
                        fingerprint,
                        report.exception_type,
                        frames,
                        item.channel,
                        results[0][0],
                        self.version,
                    )
                )
        return events, errors

    def roll_up(self) -> None:
        try:
            with self._lock:
                if self._conn is not None:
                    db.roll_up(self._conn, self._now())
        except sqlite3.Error as error:
            log.warning("Rolling up the usage stats failed: %s", error)

    async def run(self) -> None:
        """Flush every FLUSH_EVERY seconds, sample and roll up when due, until cancelled."""
        clock = time.monotonic
        next_sample = clock() + SAMPLE_EVERY
        next_roll_up = clock() + RETENTION_EVERY
        while True:
            await asyncio.sleep(FLUSH_EVERY)
            if clock() >= next_sample:
                self.sample()
                next_sample += SAMPLE_EVERY
            await asyncio.to_thread(self.write, *self.take())
            if clock() >= next_roll_up:
                await asyncio.to_thread(self.roll_up)
                next_roll_up += RETENTION_EVERY

    async def stop(self, runner: "asyncio.Task[None] | None") -> None:
        """Stop the loop, write what is left and close the file."""
        if runner is not None:
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner
        await asyncio.to_thread(self.write, *self.take())
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
