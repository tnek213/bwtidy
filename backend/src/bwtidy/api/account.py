"""Deleting the account's data, or the whole account (docs/spec.md, Settings, Deleting data).

POST /api/account/delete-data removes the backups, the change log, the backup fingerprints and
the decided queue entries of the logged in account. POST /api/account/delete does the same with
everything gone, then writes a marker the gateway acts on, logs the CLI out and ends the
session, so the worker exits and the gateway deletes the account directory.

Both need the session and REQUEST_HEADER like POST /api/bw-logout. Both answer 409 with the
pending counts on the first try while any queue entry is pending, so the browser can ask
whether to write the pending UI edits first or drop them.
"""

import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bwtidy.api.app_state import AppState, app_state
from bwtidy.api.deps import (
    RequestHeader,
    Session,
    logout_response,
    require_request_header,
    secure_cookies,
)
from bwtidy.backups import BackupError
from bwtidy.bw import BwError
from bwtidy.protocol import DELETE_MARKER
from bwtidy.queue.wipe import WriteRunning

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/account")


class DeleteRequest(BaseModel):
    ## None on the first try: a 409 then names the pending entries. write commits the pending UI
    ## edits first (like Commit now), drop deletes them too.
    pending: Literal["write", "drop"] | None = None


def write_delete_marker(data_dir: Path) -> None:
    """Write app/delete-requested as a small regular file, mode 0600, for the gateway to find.

    The gateway deletes the account directory when the worker exits and this marker is there
    (docs/spec.md, Gateway). O_NOFOLLOW so the worker never writes it through a symlink.
    """
    path = data_dir / DELETE_MARKER
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def drop_stale_delete_marker(data_dir: Path) -> None:
    """Delete a marker a worker before this one left, at the worker's start.

    A new worker can start in the directory before the gateway saw the old one exit (the
    account opened again at once). The gateway then keeps the directory, and the marker must
    not delete it at this worker's own exit.
    """
    try:
        (data_dir / DELETE_MARKER).unlink()
    except FileNotFoundError:
        return
    except OSError as error:
        log.warning("Could not delete the delete marker an earlier worker left: %s", error)
        return
    log.info("Deleted the delete marker an earlier worker left, the account stays")


async def wipe_data(request: Request, pending: str | None, *, keep_pending_mcp: bool) -> Response:
    """The shared steps of both endpoints: answer 409 while entries are pending and no choice
    was made, write the pending UI edits first on write, then delete the account's data.

    Answers the counts deleted (200), or 409 with the pending counts, a failed commit or a write
    that is running. The caller decides what else happens after a 200.
    """
    state = app_state(request)
    queue = state.queue
    pending_ui = queue.pending_ui_ids()
    pending_mcp = queue.pending_mcp_ids()
    if pending is None and (pending_ui or pending_mcp):
        return JSONResponse(
            {"pending": len(pending_ui), "mcpPending": len(pending_mcp)}, status_code=409
        )
    if pending == "write" and pending_ui:
        failure = await commit_pending_first(state)
        if failure is not None:
            return failure
    try:
        result = await queue.wipe_account_data(keep_pending_mcp=keep_pending_mcp)
    except WriteRunning as error:
        return JSONResponse({"detail": str(error)}, status_code=409)
    return JSONResponse(result)


async def commit_pending_first(state: AppState) -> Response | None:
    """Commit the pending UI edits like Commit now, None when they are all written.

    A commit that fails or leaves an entry behind deletes nothing: 409 with what went wrong, so
    the browser can show it and let the user try again or drop the edits instead.
    """
    try:
        outcome = await state.auto_commit.commit_pending()
    except (BackupError, BwError) as error:
        return JSONResponse(
            {"detail": f"The pending edits could not be written: {error}"}, status_code=409
        )
    if outcome["failed"] or outcome["pending"]:
        return JSONResponse(
            {
                "detail": "The pending edits were not all written, nothing was deleted.",
                "failed": outcome["failed"],
                "stillPending": outcome["pending"],
            },
            status_code=409,
        )
    return None


@router.post("/delete-data")
async def delete_data(
    request: Request,
    body: DeleteRequest,
    session: Session,
    x_bwtidy_request: RequestHeader = None,
) -> Response:
    """Delete the account's backups, change log and decided queue entries, the account kept."""
    require_request_header(x_bwtidy_request)
    return await wipe_data(request, body.pending, keep_pending_mcp=body.pending == "write")


@router.post("/delete")
async def delete_account(
    request: Request,
    body: DeleteRequest,
    session: Session,
    x_bwtidy_request: RequestHeader = None,
) -> Response:
    """Delete all of the account's data, then end the session so the gateway removes the account.

    The pending MCP proposals go too, since the account goes. After the data is gone the marker
    is written, the CLI is logged out and the session ends the way POST /api/bw-logout does, so
    the worker exits and the gateway deletes the account directory and its backups directory.
    """
    require_request_header(x_bwtidy_request)
    answer = await wipe_data(request, body.pending, keep_pending_mcp=False)
    if answer.status_code != 200:
        return answer
    state = app_state(request)
    write_delete_marker(state.settings.data_dir)
    await state.session.logout()
    code = await state.bw_login.log_out()
    if code != 0:
        raise HTTPException(status_code=502, detail=f"bw logout exited with code {code}")
    return logout_response(secure_cookies(request))
