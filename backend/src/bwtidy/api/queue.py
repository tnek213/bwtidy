"""Queue routes (docs/spec.md, MCP and the queue): list, approve, reject, dismiss, retry, queue UI
edits.

POST /api/queue queues the UI's operations, which the auto-commit then commits on its own.
GET and PUT /api/queue/auto-commit read and set when, POST /api/queue/commit commits now.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from bwtidy.api.app_state import app_state
from bwtidy.api.deps import Session
from bwtidy.operations import parse_operations
from bwtidy.queue.autocommit import (
    MAX_AFTER_SECONDS,
    MAX_MAX_ENTRIES,
    MIN_AFTER_SECONDS,
    MIN_MAX_ENTRIES,
)
from bwtidy.queue.commit import RetryRefused
from bwtidy.queue.entries import QueueError
from bwtidy.queue.state import STATUSES

router = APIRouter(prefix="/api/queue")


class Decision(BaseModel):
    ids: list[int]


class Retry(BaseModel):
    ids: list[int]
    overwrite: bool = False


class AutoCommitSettings(BaseModel):
    afterSeconds: int = Field(ge=MIN_AFTER_SECONDS, le=MAX_AFTER_SECONDS)
    maxEntries: int = Field(ge=MIN_MAX_ENTRIES, le=MAX_MAX_ENTRIES)


@router.post("", status_code=201)
async def enqueue_ui(request: Request, body: dict[str, Any], session: Session) -> dict[str, Any]:
    """Queue the UI's operations, one entry each, checked against the vault in RAM at once.

    409 with the conflicts (or merge conflicts) and 422 for an operation that is not allowed
    queue nothing.
    """
    ops = parse_operations(body.get("operations"))
    return {"ids": app_state(request).queue.enqueue_ui(ops)}


@router.get("")
async def list_queue(
    request: Request, session: Session, status: str | None = None
) -> list[dict[str, Any]]:
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=422, detail=f"status is one of {', '.join(STATUSES)}")
    return app_state(request).queue.entries(status)


@router.post("/approve")
async def approve(request: Request, body: Decision, session: Session) -> dict[str, Any]:
    """Apply the entries as one operation. 200 tells per entry what became of it."""
    try:
        return await app_state(request).queue.approve(body.ids)
    except QueueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.post("/commit")
async def commit_now(request: Request, session: Session) -> dict[str, Any]:
    """Commit every pending UI entry now, once a running commit has finished."""
    return await app_state(request).auto_commit.commit_pending()


@router.get("/auto-commit")
async def auto_commit_status(request: Request, session: Session) -> dict[str, Any]:
    return app_state(request).auto_commit.status()


@router.put("/auto-commit")
async def set_auto_commit(
    request: Request, body: AutoCommitSettings, session: Session
) -> dict[str, Any]:
    auto_commit = app_state(request).auto_commit
    auto_commit.configure(body.afterSeconds, body.maxEntries)
    return auto_commit.status()


@router.post("/reject")
async def reject(request: Request, body: Decision, session: Session) -> dict[str, Any]:
    try:
        return await app_state(request).queue.reject(body.ids)
    except QueueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.post("/dismiss")
async def dismiss(request: Request, body: Decision, session: Session) -> dict[str, Any]:
    """Delete failed entries. Ids that are not failed are left alone and listed as notFailed."""
    return await app_state(request).queue.dismiss(body.ids)


@router.post("/retry")
async def retry(request: Request, body: Retry, session: Session) -> dict[str, Any]:
    """Put failed entries back to pending and apply them at once, answered like approve. 409 for
    an entry that cannot be retried, a conflict only with overwrite."""
    try:
        return await app_state(request).queue.retry(body.ids, body.overwrite)
    except QueueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except RetryRefused as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
