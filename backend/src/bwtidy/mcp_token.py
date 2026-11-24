"""The bearer token in front of /mcp: only its sha256 is kept, in the data directory.

See MCP and the queue in docs/spec.md. The token itself is answered once when it is made and
never stored, logged or answered again. The file holds the hash on its first line, the expiry
(a UTC ISO timestamp or `never`) on the second and the creation time on the third. A file of
any other shape is no token. This module is the record and its checks, which the gateway and
the worker share. The worker's token file and its check of a request are in mcp/auth.py.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import Scope

UNAUTHORIZED = "bwtidy MCP needs a token. Create one in the web UI."
EXPIRED = "bwtidy MCP token expired. Create a new one in the web UI."
NEVER = "never"


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def parse_time(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def token_digest(token: str) -> bytes:
    """The sha256 hex of the token, as the token file holds it on its first line."""
    return hashlib.sha256(token.encode()).hexdigest().encode()


@dataclass(frozen=True)
class TokenRecord:
    """What the token file holds. expires_at None never expires."""

    digest: bytes
    expires_at: datetime | None
    created_at: datetime

    def expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at

    def file_content(self) -> bytes:
        expiry = NEVER if self.expires_at is None else iso(self.expires_at)
        return b"%s\n%s\n%s\n" % (self.digest, expiry.encode(), iso(self.created_at).encode())


def parse_token_file(content: bytes) -> TokenRecord | None:
    """The record of a token file, None unless it holds a hash, an expiry and a creation time."""
    try:
        lines = content.decode("ascii").splitlines()
    except UnicodeDecodeError:
        return None
    if len(lines) != 3:
        return None
    digest = lines[0].strip().encode()
    if not digest:
        return None
    expiry = lines[1].strip()
    expires_at = None
    if expiry != NEVER:
        expires_at = parse_time(expiry)
        ## an expiry that cannot be read is no token to trust, rather than one without a limit
        if expires_at is None:
            return None
    created_at = parse_time(lines[2].strip())
    if created_at is None:
        return None
    return TokenRecord(digest=digest, expires_at=expires_at, created_at=created_at)


class Check(Enum):
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"
    EXPIRED = "expired"


def token_refusal(check: Check) -> PlainTextResponse:
    """The 401 for an unknown or an expired token, the same for the worker and the gateway."""
    text = EXPIRED if check is Check.EXPIRED else UNAUTHORIZED
    return PlainTextResponse(text, status_code=401, headers={"WWW-Authenticate": "Bearer"})


def bearer(scope: Scope) -> str:
    """The token of an `Authorization: Bearer TOKEN` header, else an empty string."""
    scheme, _, token = Headers(scope=scope).get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""
