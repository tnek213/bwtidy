"""The worker's MCP token: its file, its last use and the ASGI check in front of /mcp.

The token's record and its checks, which the gateway reads too, are in mcp_token.py.
"""

import hmac
import logging
import os
import secrets
import sqlite3
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from starlette.types import ASGIApp, Receive, Scope, Send

from bwtidy.mcp_token import (
    Check,
    TokenRecord,
    bearer,
    iso,
    parse_time,
    parse_token_file,
    token_digest,
    token_refusal,
    utc_now,
)
from bwtidy.protocol import TOKEN_FILE

log = logging.getLogger(__name__)

DEFAULT_DAYS = 90
MAX_DAYS = 365
## the settings key of the last accepted MCP request's time, written at most once a minute
LAST_USED_SETTING = "mcp_token_last_used"
LAST_USED_EVERY = timedelta(minutes=1)


class Settings(Protocol):
    """Where the last use is kept, the worker's SQLite settings (db.Database)."""

    def get_setting(self, key: str) -> str | None: ...

    def set_setting(self, key: str, value: str) -> None: ...

    def delete_setting(self, key: str) -> None: ...


class McpToken:
    def __init__(
        self,
        data_dir: Path,
        settings: Settings | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.path = data_dir / TOKEN_FILE
        self.settings = settings
        self.now = now
        self._last_used: datetime | None = None
        self._last_used_loaded = False

    def exists(self) -> bool:
        return self.path.is_file()

    def record(self) -> TokenRecord | None:
        try:
            return parse_token_file(self.path.read_bytes())
        except OSError:
            return None

    def create(self, days: int | None = DEFAULT_DAYS) -> tuple[str, TokenRecord]:
        """Make a new token, replacing the old one, and answer it. Only its hash is written.

        days None makes a token that works until it is removed.
        """
        token = secrets.token_urlsafe(32)
        created = self.now().astimezone(UTC).replace(microsecond=0)
        expires = None if days is None else created + timedelta(days=days)
        record = TokenRecord(digest=token_digest(token), expires_at=expires, created_at=created)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ## mkstemp creates the file with mode 0600, and os.replace swaps it in atomically
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{TOKEN_FILE}.")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(record.file_content())
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, self.path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
        self._forget_last_used()
        return token, record

    def remove(self) -> None:
        self.path.unlink(missing_ok=True)
        self._forget_last_used()

    def check(self, token: str) -> Check:
        """The hash first, compared in constant time, then the expiry."""
        if not token:
            return Check.UNKNOWN
        record = self.record()
        if record is None or not hmac.compare_digest(token_digest(token), record.digest):
            return Check.UNKNOWN
        if record.expired(self.now()):
            return Check.EXPIRED
        return Check.ACCEPTED

    def last_used(self) -> datetime | None:
        if not self._last_used_loaded:
            self._last_used_loaded = True
            stored = self._read_setting()
            self._last_used = parse_time(stored) if stored else None
        return self._last_used

    def note_used(self) -> None:
        """Keep the time of an accepted request, in RAM and in the settings, once a minute."""
        now = self.now()
        last = self.last_used()
        if last is not None and now - last < LAST_USED_EVERY:
            return
        self._last_used = now.astimezone(UTC).replace(microsecond=0)
        if self.settings is None:
            return
        try:
            self.settings.set_setting(LAST_USED_SETTING, iso(self._last_used))
        except (OSError, sqlite3.Error) as error:
            log.warning("Could not store when the MCP token was last used: %s", error)

    def _read_setting(self) -> str | None:
        if self.settings is None:
            return None
        try:
            return self.settings.get_setting(LAST_USED_SETTING)
        except (OSError, sqlite3.Error) as error:
            log.warning("Could not read when the MCP token was last used: %s", error)
            return None

    def _forget_last_used(self) -> None:
        self._last_used = None
        self._last_used_loaded = True
        if self.settings is None:
            return
        try:
            self.settings.delete_setting(LAST_USED_SETTING)
        except (OSError, sqlite3.Error) as error:
            log.warning("Could not forget when the MCP token was last used: %s", error)


class RequireMcpToken:
    """Answer 401 to an MCP request without the right token, before the MCP SDK sees it."""

    def __init__(self, app: ASGIApp, token: McpToken) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            check = self.token.check(bearer(scope))
            if check is not Check.ACCEPTED:
                if check is Check.EXPIRED:
                    log.info("Refused an MCP request with an expired token")
                else:
                    log.info("Refused an MCP request without a valid token")
                await token_refusal(check)(scope, receive, send)
                return
            self.token.note_used()
        await self.app(scope, receive, send)
