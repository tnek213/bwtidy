"""The login session's routes: unlock, its state, the heartbeat and the log out."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from bwtidy.api.app_state import app_state
from bwtidy.api.deps import (
    SessionState,
    logout_response,
    secure_cookies,
    start_session,
    state_of,
)
from bwtidy.protocol import SESSION_COOKIE

router = APIRouter(prefix="/api/session")


class LoginRequest(BaseModel):
    password: str


@router.post("", response_model_exclude_none=True)
async def login(request: Request, body: LoginRequest, response: Response) -> SessionState:
    state = app_state(request)
    return await start_session(
        state.session,
        body.password,
        response,
        state.bw_login,
        state.mcp_token,
        secure_cookies(request),
    )


@router.get("", response_model_exclude_none=True)
async def get_state(request: Request) -> SessionState:
    state = app_state(request)
    token = request.cookies.get(SESSION_COOKIE)
    state.session.touch(token)
    return await state_of(state.session, token, state.bw_login, state.mcp_token)


@router.post("/heartbeat", status_code=204)
async def heartbeat(request: Request) -> Response:
    if not app_state(request).session.heartbeat(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status_code=401, detail="Not logged in")
    return Response(status_code=204)


@router.delete("", status_code=204)
async def logout(request: Request) -> Response:
    session = app_state(request).session
    if session.is_valid(request.cookies.get(SESSION_COOKIE)):
        await session.logout()
    return logout_response(secure_cookies(request))
