"""Auto-commit of queued UI edits (docs/spec.md, MCP and the queue).

Every UI edit is a pending queue entry with source ui. This commits all of them as one approve
(one backup, one call of the write path) once the oldest is afterSeconds old or maxEntries are
pending, both kept in the settings table. Only while the vault is unlocked, one commit at a
time, and never an agent's entry: those wait for the user. Entries queued while a commit runs wait
for the next one. The time an entry was queued lives in RAM only, so after a restart every
pending UI entry counts as due at once.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from bwtidy.backups import BackupError
from bwtidy.bw import BwError
from bwtidy.db import AUTO_COMMIT_AFTER_SECONDS, AUTO_COMMIT_MAX_ENTRIES, Database
from bwtidy.queue.review import ReviewQueue
from bwtidy.writes import WRITING

log = logging.getLogger(__name__)

DEFAULT_AFTER_SECONDS = 180
DEFAULT_MAX_ENTRIES = 20
MIN_AFTER_SECONDS, MAX_AFTER_SECONDS = 1, 3600
MIN_MAX_ENTRIES, MAX_MAX_ENTRIES = 1, 1000
## how often the task looks for due entries
TICK = 1.0
## how long a commit that could not run (no backup, no vault) waits before the next try
RETRY_DELAY = 30.0
## how long the session's end and a stop wait for a commit before they cancel it: each bw call
## may wait 120 s for an answer, and the worker's exit waits for the session's end
END_COMMIT_LIMIT = 60.0
## how long cancelled commits get to end (a cancel can come during a reload, which a second
## cancel cuts)
CANCEL_WAIT = 10.0

EMPTY_OUTCOME: dict[str, Any] = {"applied": [], "failed": [], "pending": [], "backup": None}


class Unlockable(Protocol):
    @property
    def unlocked(self) -> bool: ...


class AutoCommit:
    def __init__(
        self,
        queue: ReviewQueue,
        db: Database,
        session: Unlockable,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.queue = queue
        self.db = db
        self.session = session
        self._clock = clock
        self._lock = asyncio.Lock()
        self.committing = False
        self._queued_at: dict[int, float] = {}
        self._not_before = 0.0
        ## what runs on its own (the checks of run, a request's commit like the templates save),
        ## kept so it is not collected and so a stop can wait for it
        self.background: set[asyncio.Task[Any]] = set()
        ## set when a stop gave up on the commits, so the session's end after it commits nothing
        self._gave_up = False

    def note_queued(self, entry_id: int) -> None:
        self._queued_at[entry_id] = self._clock()

    def _setting(self, key: str, default: int, low: int, high: int) -> int:
        raw = self.db.get_setting(key)
        try:
            value = int(raw) if raw is not None else default
        except ValueError:
            return default
        return value if low <= value <= high else default

    @property
    def after_seconds(self) -> int:
        return self._setting(
            AUTO_COMMIT_AFTER_SECONDS, DEFAULT_AFTER_SECONDS, MIN_AFTER_SECONDS, MAX_AFTER_SECONDS
        )

    @property
    def max_entries(self) -> int:
        return self._setting(
            AUTO_COMMIT_MAX_ENTRIES, DEFAULT_MAX_ENTRIES, MIN_MAX_ENTRIES, MAX_MAX_ENTRIES
        )

    def configure(self, after_seconds: int, max_entries: int) -> None:
        """Save both settings. The caller has checked the ranges."""
        self.db.set_setting(AUTO_COMMIT_AFTER_SECONDS, str(after_seconds))
        self.db.set_setting(AUTO_COMMIT_MAX_ENTRIES, str(max_entries))

    def _due_in(self, ids: list[int]) -> float | None:
        """Seconds until the pending entries are committed, None when nothing is pending."""
        if not ids:
            return None
        if len(ids) >= self.max_entries:
            return 0.0
        now = self._clock()
        ## an entry queued before a restart has no time here and counts as old
        oldest = min(self._queued_at.get(entry_id, float("-inf")) for entry_id in ids)
        return max(0.0, oldest + self.after_seconds - now)

    def status(self) -> dict[str, Any]:
        ids = self.queue.pending_ui_ids()
        due_in = self._due_in(ids)
        next_at = None
        if due_in is not None:
            when = datetime.now(UTC) + timedelta(seconds=due_in)
            next_at = when.isoformat(timespec="seconds")
        progress = self.queue.progress
        answer: dict[str, Any] = {
            "afterSeconds": self.after_seconds,
            "maxEntries": self.max_entries,
            "pending": len(ids),
            ## an approve of the Queue page counts too, so the app bar shows it
            "committing": self.committing or self.queue.approving,
            "nextCommitAt": next_at,
            "commitCount": self.queue.commits,
            "phase": None if progress is None else progress.phase,
        }
        if progress is not None and progress.phase == WRITING:
            answer["done"] = progress.done
            answer["total"] = progress.total
        return answer

    async def run(self) -> None:
        """Commit the due UI entries, forever. A cancel never reaches a running commit."""
        while True:
            await asyncio.sleep(TICK)
            await asyncio.shield(self._in_background(self._check()))

    async def _check(self) -> None:
        try:
            await self.commit_if_due()
        except Exception:
            log.exception("The auto-commit failed")

    def _in_background(self, work: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(work)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

    async def settle(self) -> None:
        """Wait for the commits that run on their own, up to END_COMMIT_LIMIT, then cancel them.

        A stop calls this after cancelling run. When it gives up, bw did not answer, so the
        session's end that follows commits nothing and the entries stay pending.
        """
        if not self.background:
            return
        _, running = await asyncio.wait(set(self.background), timeout=END_COMMIT_LIMIT)
        if running:
            log.warning(
                "Stop: the commits did not finish within %.0f s, cancelling them", END_COMMIT_LIMIT
            )
            self._gave_up = True
            await self._cancel_background()

    async def _cancel_background(self) -> None:
        """Cancel the running commits and wait CANCEL_WAIT at most for them to end.

        Each cancel cuts the commit's current step (approve puts the entries not written back
        to pending), and it is repeated while a commit goes on to another step.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CANCEL_WAIT
        running = set(self.background)
        while running and loop.time() < deadline:
            for task in running:
                task.cancel()
            _, running = await asyncio.wait(running, timeout=min(1.0, deadline - loop.time()))
        if running:
            log.warning("%d cancelled commits did not end, going on without them", len(running))

    async def commit_if_due(self) -> dict[str, Any] | None:
        """One check: commit when the pending UI entries are due and the vault is unlocked."""
        if not self.session.unlocked or self._clock() < self._not_before:
            return None
        if self._due_in(self.queue.pending_ui_ids()) != 0.0:
            return None
        try:
            return await self.commit_pending()
        except (BackupError, BwError) as error:
            log.warning("The auto-commit could not run, the entries stay pending: %s", error)
            self._not_before = self._clock() + RETRY_DELAY
            return None

    async def commit_pending(self) -> dict[str, Any]:
        """Commit every pending UI entry now, after a running commit has finished.

        Answers like ReviewQueue.approve, with empty lists when nothing is pending. Raises
        BackupError and BwError like approve (the entries are pending again then).
        """
        async with self._lock:
            ids = self.queue.pending_ui_ids()
            if not ids:
                return dict(EMPTY_OUTCOME)
            self.committing = True
            try:
                outcome = await self.queue.approve(ids)
            finally:
                self.committing = False
            for entry_id in ids:
                if entry_id not in outcome["pending"]:
                    self._queued_at.pop(entry_id, None)
            log.info(
                "Auto-commit: %d applied, %d failed, %d pending",
                len(outcome["applied"]),
                len(outcome["failed"]),
                len(outcome["pending"]),
            )
            return outcome

    def start_commit(self) -> None:
        """Commit in the background, so the request that queued an entry answers at once."""
        self._in_background(self.commit_quietly())

    async def commit_quietly(self) -> dict[str, Any] | None:
        """A commit whose failure only gets logged, None then: the entries wait for the next."""
        try:
            outcome = await self.commit_pending()
        except (BackupError, BwError) as error:
            log.warning("The commit could not run, the entries stay pending: %s", error)
            return None
        if outcome["failed"]:
            log.warning("%d queued edits failed at the commit", len(outcome["failed"]))
        return outcome

    async def commit_before_session_ends(self) -> None:
        """The session-end commit, given up after END_COMMIT_LIMIT. Whatever stays pending
        counts as old from now on, so the next unlock commits it at once (the same as after a
        restart).

        A cancel of the caller never reaches the commit: it runs in a task of its own, which
        settle waits for like for a commit of run.
        """
        outcome = None
        if self._gave_up:
            log.info("Session end: no commit, the stop gave up on bw, the entries stay pending")
        else:
            task = self._in_background(self.commit_quietly())
            done, _ = await asyncio.wait({task}, timeout=END_COMMIT_LIMIT)
            if task not in done:
                log.warning(
                    "Session end: commit given up after %.0f s, the entries stay pending",
                    END_COMMIT_LIMIT,
                )
                await self._cancel_background()
            elif task.cancelled():
                ## a stop's settle gave up on it
                log.warning("Session end: commit cancelled, the entries stay pending")
            else:
                outcome = task.result()
                if outcome is None:
                    log.info("Session end: commit could not run, the entries stay pending")
        if outcome is not None:
            log.info(
                "Session end: commit done, %d applied, %d failed, %d pending",
                len(outcome["applied"]),
                len(outcome["failed"]),
                len(outcome["pending"]),
            )
        if outcome is None or outcome["pending"]:
            self._queued_at.clear()
            self._not_before = 0.0
