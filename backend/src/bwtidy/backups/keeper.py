"""The backups of one account while a session runs (docs/spec.md, Backups).

The backup of a write is the newest one when its fingerprint equals the vault's (see
files.fingerprint), else a fresh one. After every write and every unlock a backup of the
vault as it is now is taken in the background, so the next write usually finds one ready.
The write path (writes/path.py) asks the keeper for the backup of a write and starts the
background backup after it, the backup routes take and delete backups through it. The
retention runs here after each backup, and like Delete it keeps the backups still in use.
"""

import asyncio
import contextlib
import functools
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol

from bwtidy.backups.collect import BackupSession, BackupStopped, create_backup
from bwtidy.backups.files import (
    BackupError,
    BackupInfo,
    FingerprintStore,
    apply_retention,
    fingerprint,
    list_backups,
    prune,
)
from bwtidy.backups.reader import BackupReader
from bwtidy.bw import BwError
from bwtidy.config import DEFAULT_RETENTION, Retention
from bwtidy.db import Database
from bwtidy.protocol import backup_time
from bwtidy.vault import VaultCache

log = logging.getLogger(__name__)


## how long deleting all the backups waits for a running background backup before it cancels it
WIPE_BACKUP_STOP_LIMIT = 30.0


class BackupInUse(Exception):
    """The backup cannot be deleted: a write may still rely on it. Nothing was deleted."""


class Backup(Protocol):
    """create_backup's shape, so a test can put a fake in its place."""

    def __call__(
        self,
        session: BackupSession,
        directory: Path,
        bw_bin: str,
        *,
        store: FingerprintStore,
        stop: Callable[[], bool] | None = None,
    ) -> Awaitable[BackupInfo]: ...


class BackupKeeper:
    """The backups of one account while a session runs: the newest one with its fingerprint,
    the backup lock, the background backup and the decrypted backups in RAM (reader).

    Every backup, for a write, by hand or in the background, goes through take_backup under
    one backup lock, so two never run at once and a write that arrives during a background
    backup waits for it and then reuses it. queue_in_use (queue/wipe.py backup_in_use) says
    why a backup must stay for the queue.
    """

    def __init__(
        self,
        session: BackupSession,
        cache: VaultCache,
        backup_dir: Path,
        bw_bin: str,
        db: Database,
        *,
        queue_in_use: Callable[[str], str | None],
        backup: Backup | None = None,
        retention: Retention = DEFAULT_RETENTION,
        version: str = "unknown",
    ) -> None:
        self.session = session
        self.retention = retention
        self.cache = cache
        self.backup_dir = backup_dir
        self.bw_bin = bw_bin
        self.db = db
        ## looked up at init, so a test can patch the module's create_backup before create_app
        self.backup: Backup = backup or functools.partial(create_backup, version=version)
        self.reader = BackupReader(backup_dir, lambda: session.password)
        self._queue_in_use = queue_in_use
        self._write_backup: str | None = None
        self._lock = asyncio.Lock()
        self._background: asyncio.Task[None] | None = None
        ## set to stop the running background backup before its next step
        self._background_stop = asyncio.Event()

    async def end_session(self, limit: float) -> None:
        """Drop what a session left in RAM: a running background backup, a decrypted backup.

        The background backup stops before its next step, and the bw it runs is waited for up
        to limit seconds, never cut off (docs/spec.md, Backups). Only then is it cancelled.
        """
        await self.stop_background_backup(limit)
        self.reader.clear()

    def vault_fingerprint(self) -> str:
        """The fingerprint of the vault as the cache holds it."""
        return fingerprint(
            self.cache.items, self.cache.folders, self.cache.collections, self.cache.organizations
        )

    def newest_backup(self) -> BackupInfo | None:
        """The newest backup in the directory with its stored fingerprint, None without one."""
        backups = list_backups(self.backup_dir)
        if not backups:
            return None
        newest = backups[0]
        stored = self.db.backup_fingerprint(newest.name)
        return BackupInfo(newest.name, newest.size, newest.time, stored)

    async def backup_for_write(self) -> tuple[BackupInfo, bool]:
        """The backup a write may rely on: the newest one when it holds the vault as the
        cache does now (the second value is True), else a fresh one. It stays in use until
        release_write_backup.

        The vault's fingerprint is taken before the lock, from the cache the write was checked
        against. A background backup that runs holds the lock, so it is waited for and then
        compared, which is what makes it reusable.
        """
        current = self.vault_fingerprint()
        async with self._lock:
            newest = self.newest_backup()
            if newest is not None and newest.fingerprint == current:
                log.info("Backup %s holds the vault as it is, reusing it", newest.name)
                self._write_backup = newest.name
                return newest, True
            info = await self._create_backup()
            self._write_backup = info.name
            return info, False

    def release_write_backup(self) -> None:
        """The write that backup_for_write gave its backup to has ended, the backup may go."""
        self._write_backup = None

    async def take_backup(self) -> BackupInfo:
        """A fresh backup of the vault now, once no other backup runs."""
        async with self._lock:
            return await self._create_backup()

    def in_use(self, name: str) -> str | None:
        """Why the backup must stay, None when it may go: it is the newest one (the next
        write may reuse it), the write under way was taken with it (backup_for_write), or the
        queue still needs it (queue_in_use). Delete and the retention both ask this, under the
        backup lock."""
        backups = list_backups(self.backup_dir)
        if backups and backups[0].name == name:
            return f"Backup {name} is the newest one, the next write may reuse it."
        if name == self._write_backup:
            return f"Backup {name} is what the write under way was taken with."
        return self._queue_in_use(name)

    async def delete_backup(self, name: str) -> None:
        """Delete the backup and its fingerprint, under the backup lock so a backup that is
        being written cannot make it the newest one halfway.

        Raises KeyError for a name the directory does not hold, BackupInUse with the reason
        of in_use for a backup still in use. The decrypted copy in RAM goes with the file.
        """
        async with self._lock:
            if name not in {b.name for b in list_backups(self.backup_dir)}:
                raise KeyError(name)
            reason = self.in_use(name)
            if reason is not None:
                raise BackupInUse(reason)
            (self.backup_dir / name).unlink()
            self.db.forget_backups([name])
            self.reader.forget(name)
            log.info("Backup %s deleted", name)

    async def delete_all_backups(
        self, limit: float = WIPE_BACKUP_STOP_LIMIT
    ) -> tuple[int, list[str]]:
        """Delete every backup file, the newest one too, except one a queued restore still
        needs (queue_in_use), for the account data deletion (docs/spec.md, Deleting data).

        The background backup is stopped first, so none is written while the files go, then the
        deletion runs under the backup lock. The decrypted copies in RAM and any leftover
        temporary file go too. Returns the count deleted and the names kept.
        """
        await self.stop_background_backup(limit)
        async with self._lock:
            deleted: list[str] = []
            kept: list[str] = []
            for info in list_backups(self.backup_dir):
                if self._queue_in_use(info.name) is not None:
                    kept.append(info.name)
                    continue
                (self.backup_dir / info.name).unlink(missing_ok=True)
                self.reader.forget(info.name)
                deleted.append(info.name)
            self._remove_leftover_files(kept)
            self.db.forget_backups(deleted)
            log.info("Deleted %d backups, kept %d still in use", len(deleted), len(kept))
            return len(deleted), sorted(kept)

    def _remove_leftover_files(self, kept: list[str]) -> None:
        """Remove a temporary .part file a cut-off backup left behind, the kept backups aside."""
        if not self.backup_dir.is_dir():
            return
        keep = set(kept)
        for path in self.backup_dir.iterdir():
            if path.name in keep or not path.is_file():
                continue
            if backup_time(path.name) is None and not path.name.endswith(".part"):
                continue
            with contextlib.suppress(OSError):
                path.unlink()

    async def _create_backup(self, stop: Callable[[], bool] | None = None) -> BackupInfo:
        info = await self.backup(
            self.session, self.backup_dir, self.bw_bin, store=self.db, stop=stop
        )
        self._apply_retention(info.time)
        return info

    def _apply_retention(self, now: datetime) -> None:
        """Delete the old backups the retention names, except those in use, with their
        fingerprints and decrypted copies. A failure is only logged, the backup stands.

        now is the new backup's time, so the retention counts from the backup just taken.
        """
        names = [b.name for b in list_backups(self.backup_dir)]
        keep = frozenset(
            n
            for n in apply_retention(names, now, retention=self.retention)
            if self.in_use(n) is not None
        )
        if keep:
            log.info("Retention keeps backups still in use: %s", ", ".join(sorted(keep)))
        try:
            deleted = prune(self.backup_dir, now, keep, self.retention)
        except OSError as error:
            log.warning("Deleting old backups failed: %s", error.strerror or error)
            return
        if not deleted:
            return
        self.db.forget_backups(deleted)
        for name in deleted:
            self.reader.forget(name)
        log.info("Retention deleted %d old backups: %s", len(deleted), ", ".join(sorted(deleted)))

    def start_background_backup(self) -> None:
        """Back up the vault as the cache holds it now, in the background, unless the newest
        backup holds it already or a background backup still runs.

        Called after a write and after an unlock. The task reads the session's password and
        key when it runs and is stopped when the session ends (end_session).
        """
        if self._background is not None and not self._background.done():
            log.info("A background backup still runs, not starting another")
            return
        if not self.cache.loaded:
            return
        newest = self.newest_backup()
        if newest is not None and newest.fingerprint == self.vault_fingerprint():
            log.info("Backup %s holds the vault as it is, no background backup", newest.name)
            return
        self._background_stop = asyncio.Event()
        self._background = asyncio.create_task(
            self._run_background_backup(self._background_stop.is_set)
        )

    async def _run_background_backup(self, stop: Callable[[], bool]) -> None:
        try:
            async with self._lock:
                info = await self._create_backup(stop)
        except BackupStopped as error:
            log.info("Background backup stopped between its steps, before the %s", error.step)
            return
        except (BackupError, BwError) as error:
            log.warning("Background backup failed: %s", error)
            return
        except Exception:
            log.exception("Background backup failed")
            return
        log.info("Background backup %s ready", info.name)

    def cancel_background_backup(self) -> None:
        """Cancel a running background backup at once, which stops the bw it runs (SIGTERM)."""
        if self._background is not None and not self._background.done():
            self._background.cancel()
            log.info("Background backup cancelled")

    async def stop_background_backup(self, limit: float) -> None:
        """Stop a running background backup before its next step and wait for it.

        Cancelling it would stop the bw it runs, and a bw stopped in its write can leave the
        CLI's lock file behind, which holds up bw serve's next lock for 10 s. After limit
        seconds it is cancelled after all. A backup that a stop of the worker cancelled already
        is not waited for.
        """
        task = self._background
        if task is None or task.done() or task.cancelling():
            return
        self._background_stop.set()
        log.info("Session end: stopping the background backup before its next step")
        loop = asyncio.get_running_loop()
        started = loop.time()
        done, _ = await asyncio.wait({task}, timeout=limit)
        if done:
            log.info("Session end: background backup ended, waited %.1f s", loop.time() - started)
            return
        log.warning(
            "Session end: the background backup did not end within %.0f s, cancelling it", limit
        )
        self.cancel_background_backup()

    async def wait_for_background_backup(self) -> None:
        """Wait until no background backup runs. Its failure or cancel is not raised."""
        task = self._background
        if task is None or task.done():
            return
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            ## the task was cancelled (the session ended), the caller was not
            if not task.cancelled():
                raise
