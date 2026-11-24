"""The Bitwarden CLI login from the web UI (docs/spec.md, Session and access).

POST /api/bw-login starts it and answers a login token, which every later call carries in the
header X-Bwtidy-Login. These routes never answer 401, the frontend reads that as "locked".
POST /api/bw-logout is the exception: it needs the session.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel

from bwtidy.api.app_state import app_state
from bwtidy.api.deps import (
    RequestHeader,
    SessionState,
    logout_response,
    require_request_header,
    secure_cookies,
    start_session,
)
from bwtidy.bw import (
    BwLogin,
    LoginRefused,
    NoLogin,
    NotFinished,
    NotRunning,
    WrongAccount,
)
from bwtidy.protocol import LOGIN_HEADER, SESSION_COOKIE

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


class StartRequest(BaseModel):
    server: str
    email: str
    password: str


class InputRequest(BaseModel):
    text: str | None = None
    password: bool = False


async def current_login(
    request: Request, x_bwtidy_login: Annotated[str | None, Header(alias=LOGIN_HEADER)] = None
) -> BwLogin:
    """The running login, 404 unless the header carries its token."""
    bw_login = app_state(request).bw_login
    await bw_login.expire_if_due()
    try:
        bw_login.check(x_bwtidy_login)
    except NoLogin:
        raise HTTPException(status_code=404, detail="No such login") from None
    return bw_login


Current = Annotated[BwLogin, Depends(current_login)]


@router.post("/bw-login")
async def start(request: Request, body: StartRequest) -> dict[str, str]:
    try:
        token = await app_state(request).bw_login.start(body.server, body.email, body.password)
    except WrongAccount as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except LoginRefused as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return {"loginId": token}


@router.get("/bw-login/output")
async def output(bw_login: Current, after: int = 0) -> dict:
    return bw_login.output(after)


@router.post("/bw-login/input", status_code=204)
async def write_input(body: InputRequest, bw_login: Current) -> Response:
    try:
        if body.password:
            bw_login.write_password()
        else:
            bw_login.write(body.text or "")
    except NotRunning:
        raise HTTPException(status_code=409, detail="The Bitwarden CLI is not running") from None
    return Response(status_code=204)


@router.delete("/bw-login", status_code=204)
async def cancel(bw_login: Current) -> Response:
    await bw_login.cancel()
    return Response(status_code=204)


@router.post("/bw-login/finish", response_model_exclude_none=True)
async def finish(request: Request, response: Response, bw_login: Current) -> SessionState:
    """Unlock with the kept password, exactly like POST /api/session, then forget it.

    bw serve cannot run while the CLI is logged out, so it is started again first when it is
    not answering (the supervisor's retry may be up to 30 seconds away).
    """
    try:
        password = bw_login.password_for_finish()
    except NotFinished:
        raise HTTPException(
            status_code=409, detail="The Bitwarden login has not succeeded"
        ) from None
    state = app_state(request)
    bw_serve = state.bw_serve
    try:
        if bw_serve is not None and not bw_serve.ready:
            await bw_serve.stop()
            await bw_serve.start()
        return await start_session(
            state.session,
            password,
            response,
            bw_login,
            state.mcp_token,
            secure_cookies(request),
        )
    finally:
        bw_login.forget()


@router.post("/bw-logout", status_code=204)
async def bw_logout(
    request: Request,
    x_bwtidy_request: RequestHeader = None,
) -> Response:
    """End the session like DELETE /api/session, then log the CLI out (BwLogin.log_out).

    It needs the session cookie (401 without it) and REQUEST_HEADER (403 without it): only the
    logged in browser, or MCP's log_out_of_bitwarden, logs the CLI out.
    """
    require_request_header(x_bwtidy_request)
    state = app_state(request)
    if not state.session.is_valid(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status_code=401, detail="Not logged in")
    await state.session.logout()
    code = await state.bw_login.log_out()
    if code != 0:
        raise HTTPException(status_code=502, detail=f"bw logout exited with code {code}")
    return logout_response(secure_cookies(request))
