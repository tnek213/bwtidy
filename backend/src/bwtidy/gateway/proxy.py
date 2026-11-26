"""The proxy from the browser or the MCP client to the account's worker.

A browser request is routed by its routing cookie, an MCP request by its bearer token. The
routing cookie never reaches a worker and never comes back from one, the hop-by-hop headers
and the gateway's own server headers are dropped on the way.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.types import ASGIApp, Receive, Scope, Send

from bwtidy.gateway.accounts import AccountError, AccountKey, LimitError, check_server
from bwtidy.gateway.workers import Unanswered, Worker, WorkerStartError
from bwtidy.mcp_token import bearer
from bwtidy.protocol import ACCOUNT_COOKIE, STATS_ERROR_HEADER, WORKER_GONE

if TYPE_CHECKING:
    from bwtidy.gateway.app import Gateway

log = logging.getLogger(__name__)

## what a worker serves to a browser, everything else is the gateway's own or a static file
WORKER_PREFIXES = ("/api/",)
## routed by its bearer token instead of the routing cookie, an MCP client has no cookie
MCP_PATH = "/mcp"
## the gateway answers these itself, a worker never sees them
HEALTH_PATHS = frozenset({"/api/health", "/api/health/"})
## where a worker's bw login starts: bw resolves the server's name itself, so it is checked again
BW_LOGIN_PATH = "/api/bw-login"
## what starts or ends a session: logged when it starts, uvicorn's line comes only with the answer
SESSION_PATH = "/api/session"
## the clicks that start the account's worker when none runs: the unlock and the Bitwarden login
START_PATHS = frozenset({SESSION_PATH, BW_LOGIN_PATH})
NO_ACCOUNT = "No account"
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
## the gateway's uvicorn adds these itself
SERVER_HEADERS = frozenset({"date", "server"})
## the worker's error report for the usage stats never reaches the browser (gateway/stats.py)
ERROR_HEADER = STATS_ERROR_HEADER.lower()
## what the proxy notes in the request's scope for the usage stats: the worker it went to, and
## the worker's error report taken out of the answer
REACHED = "bwtidy.reached"
ERROR_REPORT = "bwtidy.error_report"


class ProxyMiddleware:
    """Sends a worker's paths to the browser's worker over its socket, streamed both ways."""

    def __init__(self, app: ASGIApp, gateway: Gateway) -> None:
        self.app = app
        self.gateway = gateway

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        request = Request(scope, receive)
        if is_mcp_path(path):
            found = self.gateway.mcp_worker(bearer(scope))
        elif is_worker_path(path):
            found = await self.browser_worker(scope["method"], path, request)
        else:
            await self.app(scope, receive, send)
            return
        ## the request goes on unchanged, an MCP request's token is checked again by the worker
        if isinstance(found, Response):
            response = found
        else:
            scope[REACHED] = found
            response = await proxy(request, found)
        await response(scope, receive, send)

    async def browser_worker(self, method: str, path: str, request: Request) -> Response | Worker:
        """The worker of the browser's routing cookie, else the answer to give.

        Only an unlock or the start of a Bitwarden login starts a worker that does not run.
        """
        key = self.gateway.route(request.cookies.get(ACCOUNT_COOKIE))
        if key is None:
            return no_account()
        if method != "POST" or path not in START_PATHS:
            return self.gateway.workers.get(key) or no_account()
        if path == BW_LOGIN_PATH:
            refusal = await self.check_login_server(key)
            if refusal is not None:
                return refusal
        try:
            worker = await self.gateway.open(key, create=path == BW_LOGIN_PATH)
        except AccountError as error:
            return JSONResponse({"detail": str(error)}, status_code=422)
        except LimitError as error:
            return JSONResponse({"detail": str(error)}, status_code=429)
        except WorkerStartError as error:
            return JSONResponse({"detail": str(error)}, status_code=503)
        ## an unlock of an account without a directory: there is nothing to unlock
        return worker or no_account()

    async def check_login_server(self, key: AccountKey) -> Response | None:
        """The server's addresses once more right before bw login, which resolves it itself."""
        try:
            await check_server(key, self.gateway.settings.allowed_servers)
        except AccountError as error:
            log.warning("Refused a Bitwarden login for %s: %s", key.label, error)
            return JSONResponse({"detail": str(error)}, status_code=422)
        return None


def no_account() -> Response:
    return JSONResponse({"detail": NO_ACCOUNT}, status_code=401)


def is_worker_path(path: str) -> bool:
    if path in HEALTH_PATHS:
        return False
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in WORKER_PREFIXES)


def is_mcp_path(path: str) -> bool:
    return path == MCP_PATH or path.startswith(MCP_PATH + "/")


def forwarded_headers(request: Request) -> list[tuple[str, str]]:
    """The request's headers without the hop-by-hop ones and without the routing cookie."""
    dropped = set(HOP_BY_HOP)
    dropped.update(
        name.strip().lower() for name in request.headers.get("connection", "").split(",")
    )
    headers = []
    for name, value in request.headers.items():
        if name in dropped:
            continue
        if name == "cookie":
            value = without_cookie(value, ACCOUNT_COOKIE)
            if not value:
                continue
        headers.append((name, value))
    return headers


def without_cookie(header: str, name: str) -> str:
    kept = [part for part in header.split(";") if not part.strip().startswith(f"{name}=")]
    return "; ".join(part.strip() for part in kept if part.strip())


async def proxy(request: Request, worker: Worker) -> Response:
    raw_path = request.scope.get("raw_path") or request.scope["path"].encode()
    url = raw_path.decode()
    if request.scope.get("query_string"):
        url += "?" + request.scope["query_string"].decode()
    has_body = "content-length" in request.headers or "transfer-encoding" in request.headers
    session_request = request.method in ("POST", "DELETE") and request.url.path == SESSION_PATH
    if session_request:
        log.info("%s %s for %s starts", request.method, SESSION_PATH, worker.key.label)
    try:
        ## no read timeout: a login or a backup can take long, and MCP streams
        answer = await worker.send(
            request.method,
            url,
            headers=forwarded_headers(request),
            content=request.stream() if has_body else None,
        )
    except Unanswered as unanswered:
        ## httpx's own text names the OS error (a refused or reset connection), never a body
        error = unanswered.error
        log.warning(
            "The worker for %s (%s) did not answer on a %s connection: %s %s",
            worker.key.label,
            worker.describe(),
            "new" if unanswered.new_connection else "reused",
            type(error).__name__,
            str(error) or "(no text)",
        )
        return JSONResponse({"detail": WORKER_GONE}, status_code=502)
    if session_request:
        log.info(
            "%s %s for %s answered %d",
            request.method,
            SESSION_PATH,
            worker.key.label,
            answer.status_code,
        )
    response = StreamingResponse(
        answer.aiter_raw(), status_code=answer.status_code, background=BackgroundTask(answer.aclose)
    )
    response.raw_headers = [
        (name.encode("latin-1"), value.encode("latin-1"))
        for name, value in answer.headers.multi_items()
        if passed_back(name.lower(), value)
    ]
    report = answer.headers.get(ERROR_HEADER)
    if report is not None:
        request.scope[ERROR_REPORT] = report
    return response


def passed_back(name: str, value: str) -> bool:
    """Whether a worker's response header reaches the browser: never the routing cookie, never
    the error report of the usage stats."""
    if name in HOP_BY_HOP or name in SERVER_HEADERS or name == ERROR_HEADER:
        return False
    return name != "set-cookie" or value.split("=", 1)[0].strip() != ACCOUNT_COOKIE
