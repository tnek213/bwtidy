"""The one login session: unlocks bw serve, ends on timeout, logout or a new login.

The master password and the session key live only in this object, in RAM.
"""

import asyncio
import hmac
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bwtidy import passphrase
from bwtidy.backups import BackupError, ReencryptResult, reencrypt_all
from bwtidy.bw import BwClient, BwError, BwUnavailable
from bwtidy.config import HEARTBEAT_TIMEOUT, IDLE_TIMEOUT
from bwtidy.vault import (
    Formats,
    HiddenValues,
    ToolNoteId,
    VaultCache,
    backup_fields_hidden,
    backup_passwords,
    ensure_tool_note,
    set_backup_passwords,
)

log = logging.getLogger(__name__)

CHECK_INTERVAL = 10.0
## how long a session's end waits for POST /lock: it goes on without its answer (the worker's
## exit stops bw serve), but the next login waits for that lock before it unlocks
LOCK_LIMIT = 10.0
## how much longer a login waits for a lock that gave no answer within LOCK_LIMIT before it
## gives up: bw serve still runs it and would lock the new unlock again (docs/spec.md, Session
## and access)
LATE_LOCK_LIMIT = 20.0
## how long a session's end waits for the background backup's running step before it cancels
## the backup, which stops its bw
BACKUP_STOP_LIMIT = 30.0
## wrong passwords in a row against a running session before the waits start
FREE_TRIES = 3
FIRST_WAIT = 1.0
MAX_WAIT = 60.0


class WrongPassword(Exception):
    """bw serve refused to unlock with the given master password."""


class WorkerExits(Exception):
    """The worker is on its way out and starts no new session (docs/spec.md, Worker mode)."""


class TooManyTries(Exception):
    """Too many wrong passwords against the running session, the next try waits."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"Try again in {retry_after:.0f} s")
        self.retry_after = retry_after


@dataclass(frozen=True)
class SessionHooks:
    """What the layers above do at a session's start and end, passed by main.create_app."""

    ## once a session is unlocked and its cache loaded: the background backup, the login state,
    ## the worker's pending marker
    on_unlocked: Callable[[], None]
    ## while the vault is still unlocked, right before a session ends: the commit of the queued
    ## UI edits, whose failure is only logged
    before_end: Callable[[], Awaitable[None]]
    ## given BACKUP_STOP_LIMIT: stops a running background backup and drops the decrypted one
    on_ended: Callable[[float], Awaitable[None]]
    ## once a session ended and bw serve is locked, but not when a new login replaces the
    ## session: a worker exits there (docs/spec.md, Worker mode)
    after_end: Callable[[], None]


@dataclass(frozen=True)
class _Verifier:
    """The running session's password as a salted scrypt hash, to check a new login against."""

    salt: bytes
    digest: bytes


class SessionManager:
    def __init__(
        self,
        client: BwClient,
        cache: VaultCache,
        tool_note_id: ToolNoteId,
        hooks: SessionHooks,
        backup_dir: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        hidden: HiddenValues | None = None,
        formats: Formats | None = None,
        heartbeat_timeout: float = HEARTBEAT_TIMEOUT,
        idle_timeout: float = IDLE_TIMEOUT,
    ) -> None:
        self.client = client
        self.cache = cache
        self.tool_note_id = tool_note_id
        self.hooks = hooks
        self.backup_dir = backup_dir
        self.hidden = hidden
        ## what was computed from the vault's values, which goes with the session like the cache
        self.formats = formats
        self._clock = clock
        self._heartbeat_timeout = heartbeat_timeout
        self._idle_timeout = idle_timeout
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._password: str | None = None
        self._session_key: str | None = None
        self._verifier: _Verifier | None = None
        ## one check at a time, so parallel guesses cannot pass the wait together
        self._check_lock = asyncio.Lock()
        self._wrong_tries = 0
        self._retry_at: float | None = None
        self._last_heartbeat = 0.0
        self._last_activity = 0.0
        self.mcp_enabled = False
        ## what the login did to the backups when the master password had changed, else None
        self.backup_reencryption: ReencryptResult | None = None
        ## a worker: its first session that ends for good is its last, and it takes no new one
        ## from the moment that end starts
        self.one_session = False
        self.closed = False
        ## the last POST /lock, also once its wait ended: bw serve may still run it
        self._locking: asyncio.Task[bool] | None = None

    @property
    def password(self) -> str | None:
        return self._password

    @property
    def session_key(self) -> str | None:
        return self._session_key

    @property
    def active(self) -> bool:
        return self._token is not None

    @property
    def busy(self) -> bool:
        """A login, logout or timeout check runs right now (an unlock can take a while)."""
        return self._lock.locked()

    @property
    def unlocked(self) -> bool:
        """A session runs and has not timed out yet (the watcher ends it a little later)."""
        return self._token is not None and not self._expired(self._clock())

    async def login(self, password: str) -> str:
        """Unlock bw serve and start a new session, replacing a running one.

        While a session runs, the password must be that session's one (docs/spec.md, Session and
        access): else WrongPassword, and the running session stays untouched. Raises
        WrongPassword when bw serve refuses the unlock, TooManyTries while the next check has to
        wait, WorkerExits on a worker on its way out, ToolNoteError when the tool note cannot
        take the backup passwords, BwError when anything else fails.
        """
        log.info("Login: started")
        checked: _Verifier | None = None
        while True:
            verifier = self._verifier
            ## the slow hash runs outside the lock, so it holds up no timeout check or logout
            if verifier is not None and verifier is not checked:
                await self._check_password(password, verifier)
                checked = verifier
            async with self._lock:
                if self.closed:
                    log.info("Login: refused, the worker exits")
                    raise WorkerExits()
                ## another login replaced the session during the check
                if self._verifier is not None and self._verifier is not checked:
                    continue
                return await self._start(password)

    async def _check_password(self, password: str, verifier: _Verifier) -> None:
        async with self._check_lock:
            if self._retry_at is not None:
                wait = self._retry_at - self._clock()
                if wait > 0:
                    log.info("Login: refused, too many wrong passwords")
                    raise TooManyTries(wait)
            digest = await asyncio.to_thread(passphrase.hash_password, password, verifier.salt)
            if hmac.compare_digest(digest, verifier.digest):
                self._wrong_tries = 0
                self._retry_at = None
                return
            self._wrong_tries += 1
            if self._wrong_tries >= FREE_TRIES:
                doublings = min(self._wrong_tries - FREE_TRIES, 6)
                self._retry_at = self._clock() + min(FIRST_WAIT * 2**doublings, MAX_WAIT)
            log.info("Login: refused, not the running session's password")
            raise WrongPassword("not the running session's password")

    async def _start(self, password: str) -> str:
        ## locking first makes sure the unlock below really checks the password
        if self._token is not None:
            await self._end("a new login", replaced=True)
        else:
            await self._lock_bw_serve()
        await self._wait_for_late_lock()
        log.info("Login: bw serve locked")
        try:
            session_key = await self.client.unlock(password)
        except BwUnavailable:
            raise
        except BwError as error:
            raise WrongPassword(str(error)) from None
        log.info("Login: unlocked")
        salt = secrets.token_bytes(16)
        ## the hash runs in its thread while the steps below wait for bw serve
        hashing = asyncio.ensure_future(asyncio.to_thread(passphrase.hash_password, password, salt))
        try:
            await self.client.sync()
            log.info("Login: synced")
            await self.cache.load()
            log.info("Login: cache loaded")
            await self._check_unlocked()
            note = await ensure_tool_note(self.client, self.cache, self.tool_note_id, password)
            log.info("Login: note ensured")
            reencryption = await self._reencrypt(note, password)
            log.info("Login: backups checked")
            digest = await hashing
        except BaseException:
            hashing.cancel()
            await self._lock_bw_serve()
            self.cache.clear()
            if self.formats is not None:
                self.formats.clear()
            if self.hidden is not None:
                self.hidden.clear()
            raise
        now = self._clock()
        self._token = secrets.token_urlsafe(32)
        self._password = password
        self._session_key = session_key
        self._verifier = _Verifier(salt, digest)
        self._wrong_tries = 0
        self._retry_at = None
        self._last_heartbeat = now
        self._last_activity = now
        self.mcp_enabled = False
        self.backup_reencryption = reencryption
        log.info("Vault unlocked, session started")
        ## the hooks are side effects of an unlock that already happened
        try:
            self.hooks.on_unlocked()
        except Exception as error:
            log.warning("Login: the on_unlocked hook failed (%s)", type(error).__name__)
        return self._token

    async def _wait_for_late_lock(self) -> None:
        """Wait until a POST /lock that gave no answer within LOCK_LIMIT has ended.

        bw serve still runs such a lock (it waits for a CLI lock file that a bw left behind)
        and clears the vault's key when it ends, also after an unlock that
        came in the meantime. Raises BwUnavailable when it has not ended after LATE_LOCK_LIMIT,
        so nothing is unlocked while it can still land.
        """
        locking = self._locking
        if locking is None or locking.done():
            return
        log.info("Login: waiting for the lock of bw serve that gave no answer")
        loop = asyncio.get_running_loop()
        started = loop.time()
        done, _ = await asyncio.wait({locking}, timeout=LATE_LOCK_LIMIT)
        if not done:
            log.warning(
                "Login: refused, the lock of bw serve gave no answer within %.0f s more",
                LATE_LOCK_LIMIT,
            )
            raise BwUnavailable("bw serve has not finished locking the vault, try again")
        log.info("Login: the lock of bw serve ended, waited %.1f s more", loop.time() - started)

    async def _check_unlocked(self) -> None:
        """Fail the login unless bw serve reports the vault unlocked once the cache is loaded.

        A lock that lands during the login leaves bw serve locked behind an unlock that
        answered: the cache load then reads nothing it can decrypt.
        """
        status = await self.client.status()
        if status.get("status") != "unlocked":
            log.warning("Login: unlock check failed, bw serve does not report the vault unlocked")
            raise BwError("bw serve locked the vault again during the login, try again")
        log.info("Login: unlock checked")

    async def _reencrypt(self, note: dict[str, Any], password: str) -> ReencryptResult | None:
        """Bring the hidden identity and the backups to the master password.

        The note lists every master password the backups may still be encrypted with, newest
        first. A new master password is put first and saved before anything is re-encrypted,
        so a run cut off at any point leaves every password still needed in the list. The list
        becomes the master password alone only as the very last step, once the identity and
        every backup open with it (or a backup was found unreadable with all of them). A field
        of the list that is not hidden has the list written again, which makes it hidden.
        """
        known = backup_passwords(note)
        older = [listed for listed in known if listed != password]
        if known != [password, *older] or not backup_fields_hidden(note):
            await set_backup_passwords(self.client, self.cache, note["id"], [password, *older])
        identity_stored = True
        if self.hidden is not None:
            identity_stored = await self.hidden.unlock(password, older)
        if known == [password]:
            return None
        if self.backup_dir is None:
            log.warning("The master password changed, but no backup directory is configured")
            return None
        ## with no older password known, every backup not in the new one gets reported unreadable
        try:
            result = await asyncio.to_thread(reencrypt_all, self.backup_dir, older, password)
        except BackupError as error:
            log.warning("Re-encrypting the backups stopped, the older passwords stay: %s", error)
            return None
        if older and identity_stored:
            await set_backup_passwords(self.client, self.cache, note["id"], [password])
            log.info("The backups need no older master password any more")
        return result

    def is_valid(self, token: str | None) -> bool:
        if token is None or self._token is None:
            return False
        if not secrets.compare_digest(token.encode(), self._token.encode()):
            return False
        return not self._expired(self._clock())

    def heartbeat(self, token: str | None) -> bool:
        if not self.is_valid(token):
            return False
        self._last_heartbeat = self._clock()
        return True

    def touch(self, token: str | None) -> None:
        if self.is_valid(token):
            self._last_activity = self._clock()

    async def logout(self) -> None:
        async with self._lock:
            await self._end("logout")

    async def check_timeouts(self) -> None:
        async with self._lock:
            if self._token is not None and self._expired(self._clock()):
                await self._end("timeout")

    async def restore_after_restart(self) -> None:
        """Unlock a restarted bw serve again so a running session survives the restart."""
        async with self._lock:
            if self._password is None:
                return
            try:
                self._session_key = await self.client.unlock(self._password)
            except BwError:
                log.warning("Could not unlock the restarted bw serve, ending the session")
                await self._end("bw serve restarted")

    def _expired(self, now: float) -> bool:
        return (
            now - self._last_heartbeat >= self._heartbeat_timeout
            or now - self._last_activity >= self._idle_timeout
        )

    async def _end(self, reason: str, replaced: bool = False) -> None:
        """End the running session: the work before the end, then the wipe and the lock.

        Every step logs a line (docs/spec.md, Worker mode), so a session's end that stops
        somewhere shows where.
        """
        if self._token is None:
            return
        log.info("Session end: started, %s", reason)
        if self.one_session and not replaced:
            self.closed = True
        try:
            await self.hooks.before_end()
        except Exception:
            log.exception("The work before the session's end failed")
        await self.hooks.on_ended(BACKUP_STOP_LIMIT)
        self._token = None
        self._password = None
        self._session_key = None
        self._verifier = None
        self.mcp_enabled = False
        self.backup_reencryption = None
        self.cache.clear()
        if self.formats is not None:
            self.formats.clear()
        if self.hidden is not None:
            self.hidden.clear()
        if await self._lock_bw_serve():
            log.info("Session end: bw serve locked")
        log.info("Session ended")
        if not replaced:
            self.hooks.after_end()

    async def _lock_bw_serve(self) -> bool:
        """Lock bw serve, waiting up to LOCK_LIMIT. Whether it answered that it locked.

        A lock without an answer by then keeps running (see _wait_for_late_lock), and while it
        runs it stands for the next lock too, so no second one queues up behind it.
        """
        if self._locking is None or self._locking.done():
            self._locking = asyncio.ensure_future(self._send_lock())
        try:
            async with asyncio.timeout(LOCK_LIMIT):
                return await asyncio.shield(self._locking)
        except TimeoutError:
            log.warning("Locking bw serve gave no answer within %.0f s, going on", LOCK_LIMIT)
            return False

    async def _send_lock(self) -> bool:
        try:
            await self.client.lock()
        except BwError as error:
            log.warning("Locking bw serve failed: %s", error)
            return False
        return True
