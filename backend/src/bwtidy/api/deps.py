"""What the route modules share: the session dependency and its cookie, the request header
check, the session's state as the login routes answer it and the tool note."""

import logging
import math
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel

from bwtidy.api.app_state import app_state
from bwtidy.backups import ReencryptResult
from bwtidy.bw import BwError, BwLogin, BwLoginState, BwUnavailable
from bwtidy.mcp import McpToken
from bwtidy.protocol import BACKGROUND_HEADER, REQUEST_HEADER, SESSION_COOKIE, WORKER_GONE
from bwtidy.session import SessionManager, TooManyTries, WorkerExits, WrongPassword
from bwtidy.vault import ToolNoteError, find_tool_note

log = logging.getLogger(__name__)


class BackupReencryption(BaseModel):
    done: int
    failed: list[str]

    @classmethod
    def of(cls, result: ReencryptResult) -> "BackupReencryption":
        return cls(done=result.done, failed=result.failed)


class BwLoginInfo(BaseModel):
    loggedIn: bool
    server: str
    email: str
    ## only in worker mode, where the server and email cannot be changed (see exclude_none)
    accountFixed: bool | None = None

    @classmethod
    def of(cls, state: BwLoginState) -> "BwLoginInfo":
        return cls(
            loggedIn=state.logged_in,
            server=state.server,
            email=state.email,
            accountFixed=True if state.account_fixed else None,
        )


class SessionState(BaseModel):
    state: str
    mcpEnabled: bool
    ## whether an MCP token exists, false while locked like mcpEnabled
    mcpToken: bool
    bwLogin: BwLoginInfo
    ## present only when this login re-encrypted the backups (see the routes' exclude_none)
    backupReencryption: BackupReencryption | None = None


def secure_cookies(request: Request) -> bool:
    """Whether the session cookie carries Secure (a worker behind https)."""
    return app_state(request).settings.secure_cookies


async def require_session(request: Request) -> SessionManager:
    """Reject the request with 401 unless it carries the current session cookie.

    Counts the request as user activity, unless it carries BACKGROUND_HEADER.
    """
    session = app_state(request).session
    token = request.cookies.get(SESSION_COOKIE)
    if not session.is_valid(token):
        raise HTTPException(status_code=401, detail="Not logged in")
    if request.headers.get(BACKGROUND_HEADER) != "1":
        session.touch(token)
    return session


Session = Annotated[SessionManager, Depends(require_session)]

RequestHeader = Annotated[str | None, Header(alias=REQUEST_HEADER)]


def require_request_header(value: str | None) -> None:
    """403 without REQUEST_HEADER: a page on another site cannot send it without a preflight."""
    if value != "1":
        raise HTTPException(status_code=403, detail=f"{REQUEST_HEADER}: 1 is missing")


def require_tool_note(session: SessionManager) -> dict[str, Any]:
    note = find_tool_note(session.cache, session.tool_note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="The bwtidy note is missing, log in again")
    return note


async def state_of(
    session: SessionManager, token: str | None, bw_login: BwLogin, mcp_token: McpToken
) -> SessionState:
    info = BwLoginInfo.of(await bw_login.state())
    if session.is_valid(token):
        result = session.backup_reencryption
        return SessionState(
            state="unlocked",
            mcpEnabled=session.mcp_enabled,
            mcpToken=mcp_token.exists(),
            bwLogin=info,
            backupReencryption=None if result is None else BackupReencryption.of(result),
        )
    return SessionState(state="locked", mcpEnabled=False, mcpToken=False, bwLogin=info)


async def start_session(
    session: SessionManager,
    password: str,
    response: Response,
    bw_login: BwLogin,
    mcp_token: McpToken,
    secure: bool = False,
) -> SessionState:
    """Unlock the vault, set the session cookie and answer the new state.

    Shared by POST /api/session and the finish of a Bitwarden CLI login, so both answer the
    same errors: 401 for a wrong password, 429 with Retry-After while the next try against a
    running session has to wait, 503 without bw serve, 409 with its reason for a tool note that
    cannot take the backup passwords, 502 for anything else after the unlock, and 502 with the
    gateway's own text from a worker on its way out, so the frontend starts the next worker and
    repeats the login.
    """
    try:
        token = await session.login(password)
    except WrongPassword:
        raise HTTPException(status_code=401, detail="Wrong master password") from None
    except TooManyTries as error:
        seconds = max(1, math.ceil(error.retry_after))
        raise HTTPException(
            status_code=429,
            detail=f"Too many wrong passwords. Try again in {seconds} s.",
            headers={"Retry-After": str(seconds)},
        ) from None
    except WorkerExits:
        raise HTTPException(status_code=502, detail=WORKER_GONE) from None
    except BwUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    except ToolNoteError as error:
        log.warning("Login failed after unlock: %s", error)
        raise HTTPException(status_code=409, detail=str(error)) from None
    except BwError as error:
        log.warning("Login failed after unlock: %s", error)
        raise HTTPException(status_code=502, detail=str(error)) from None
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="strict", path="/", secure=secure
    )
    return await state_of(session, token, bw_login, mcp_token)


def logout_response(secure: bool = False) -> Response:
    response = Response(status_code=204)
    response.delete_cookie(
        SESSION_COOKIE, httponly=True, samesite="strict", path="/", secure=secure
    )
    return response
