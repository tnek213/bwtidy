"""The MCP toggle and the MCP token: a logged in user sets them up (docs/spec.md)."""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, StrictInt

from bwtidy.api.app_state import app_state
from bwtidy.api.deps import Session
from bwtidy.mcp import DEFAULT_DAYS, MAX_DAYS
from bwtidy.mcp_token import iso

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

NEEDS_TOKEN = "Create an MCP token first: MCP token menu, Create."
TOKEN_EXPIRED = "The MCP token has expired. Replace it first: MCP token menu, Replace."


class McpToggle(BaseModel):
    enabled: bool


class McpTokenRequest(BaseModel):
    ## null makes a token without an expiry, a missing days the default
    days: StrictInt | None = Field(default=DEFAULT_DAYS, ge=1, le=MAX_DAYS)


class NewMcpToken(BaseModel):
    token: str
    createdAt: str
    expiresAt: str | None


class McpTokenInfo(BaseModel):
    """What the UI shows of the token: never the token or its hash."""

    createdAt: str
    expiresAt: str | None
    lastUsedAt: str | None


def iso_or_none(moment: datetime | None) -> str | None:
    return None if moment is None else iso(moment)


@router.post("/mcp")
async def toggle_mcp(body: McpToggle, request: Request, session: Session) -> dict[str, bool]:
    """Enable or disable MCP for the running session. Every login starts with it disabled.

    Enabling needs a token that exists and has not expired, else 409. Disabling always works.
    """
    if body.enabled:
        token = app_state(request).mcp_token
        record = token.record()
        if record is None:
            raise HTTPException(status_code=409, detail=NEEDS_TOKEN)
        if record.expired(token.now()):
            raise HTTPException(status_code=409, detail=TOKEN_EXPIRED)
    session.mcp_enabled = body.enabled
    log.info("MCP %s", "enabled" if body.enabled else "disabled")
    return {"mcpEnabled": session.mcp_enabled}


@router.get("/mcp/token")
async def get_mcp_token(request: Request, session: Session) -> McpTokenInfo:
    """When the token was made, when it expires and when it was last used. 404 without one."""
    token = app_state(request).mcp_token
    record = token.record()
    if record is None:
        raise HTTPException(status_code=404, detail="No MCP token")
    return McpTokenInfo(
        createdAt=iso(record.created_at),
        expiresAt=iso_or_none(record.expires_at),
        lastUsedAt=iso_or_none(token.last_used()),
    )


@router.post("/mcp/token", status_code=201)
async def create_mcp_token(
    request: Request, session: Session, body: McpTokenRequest | None = None
) -> NewMcpToken:
    """Make a new MCP token, which replaces the old one. This is the only answer that holds it."""
    token = app_state(request).mcp_token
    days = DEFAULT_DAYS if body is None else body.days
    created, record = token.create(days)
    if days is None:
        log.info("MCP token created without an expiry")
    else:
        log.info("MCP token created, it expires in %d days", days)
    return NewMcpToken(
        token=created, createdAt=iso(record.created_at), expiresAt=iso_or_none(record.expires_at)
    )


@router.delete("/mcp/token", status_code=204)
async def remove_mcp_token(request: Request, session: Session) -> Response:
    app_state(request).mcp_token.remove()
    log.info("MCP token removed")
    return Response(status_code=204)
