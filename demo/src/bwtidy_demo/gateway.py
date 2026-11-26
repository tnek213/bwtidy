"""The demo's gateway (bwtidy-demo-gateway): bwtidy's gateway with no login forms (dev/demo.md).

It wraps `create_gateway_app` with a router of its own and the worker exit hook, and adds an
outer middleware that runs before the gateway's proxy:

- `GET /demo` is the start page. `POST /demo/start` takes a ready vault from the controller,
  logs its worker in server side and answers 303 to / with both cookies. `POST /demo/end` and
  `DELETE /gateway/account` end the demo.
- Every route that takes a server, an email or a password from the browser answers 403, and
  `POST /api/mcp/token` asks for a token of at most one day.
- `/` and `/index.html` are the frontend with the demo banner, or 303 to /demo without the
  routing cookie of a live demo.
- When a worker exits, its account directory with its backups goes and the controller deletes
  the Vaultwarden user. The same happens at the start for every directory left from before, and
  a demo older than DEMO_SESSION_MAX is stopped whatever its activity.

The vault's password never leaves this process: no page, no log line, no cookie holds it.
"""

import asyncio
import contextlib
import html
import json
import logging
import re
import string
import time
from collections.abc import AsyncGenerator, AsyncIterator, Coroutine, Iterable
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from importlib import resources
from pathlib import Path
from typing import Any

import httpx2 as httpx
import uvicorn
from bwtidy.config import IDLE_TIMEOUT, Policy, SettingsError, env_seconds, env_text
from bwtidy.gateway.accounts import (
    TEMPORARY_PREFIX,
    AccountError,
    AccountKey,
    LimitError,
    account_key,
    normalize_server,
)
from bwtidy.gateway.app import REQUEST_DRAIN, Gateway, create_gateway_app
from bwtidy.gateway.settings import GatewaySettings
from bwtidy.gateway.workers import Worker, WorkerStartError
from bwtidy.logs import setup_logging, uvicorn_log_config
from bwtidy.protocol import ACCOUNT_COOKIE, LOGIN_HEADER, SESSION_COOKIE
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bwtidy_demo import init
from bwtidy_demo.controller import DOMAIN, SERVER

log = logging.getLogger(__name__)

START_PAGE = "/demo"
START_PATH = "/demo/start"
END_PATH = "/demo/end"
ACCOUNT_PATH = "/gateway/account"
MCP_TOKEN_PATH = "/api/mcp/token"
## a demo ends after this many seconds, whatever its activity (DEMO_SESSION_MAX)
SESSION_MAX = 2700.0
## how often the hard maximum is checked
MAX_POLL = 10.0
## the server side login: how often its output is polled, how long it may take in all and how
## long one request to the worker may take
LOGIN_POLL = 0.25
LOGIN_TIMEOUT = 120.0
STEP_TIMEOUT = 60.0
## a release the controller answers 502 (Vaultwarden refused) or never answers is tried again,
## waiting RELEASE_WAIT, then twice as long each time
RELEASE_TRIES = 5
RELEASE_WAIT = 1.0
MCP_TOKEN_DAYS = 1
## the body of POST /api/mcp/token is a few bytes, anything far larger is refused
TOKEN_BODY_LIMIT = 4096

REFUSED = "The demo logs you in itself. Start a new demo at /demo."
CROSS_SITE = "Start the demo from its own page at /demo."
TOO_LARGE = "The request body is too large."
## the start page's notes, picked by the query's note: never a text from the request
NOTES = {
    "free": "No demo vault is free right now. Try again in a minute.",
    "full": "The demo is full right now. Try again in a few minutes.",
    "failed": "The demo could not start. Try again in a minute.",
}
## the paths refused with every method, and those refused for POST only (POST
## /api/session/heartbeat and DELETE /api/session keep working)
REFUSED_ANY = ("/api/bw-login", "/api/bw-logout", "/api/account/delete")
REFUSED_POST = frozenset({ACCOUNT_PATH, "/api/session"})
## the fetch metadata of a start from the demo's own page or typed in the address bar
SAME_SITE = frozenset({"same-origin", "none"})
BODY = re.compile(r"<body\b[^>]*>", re.IGNORECASE)
## where a docs page's content starts, the theme's article, else the page's body
DOCS_CONTENT = re.compile(r"<article\b[^>]*md-content__inner[^>]*>", re.IGNORECASE)
DOCS_PATH = "/docs"
MCP_PAGES = ("/docs/tutorial/mcp", "/docs/tutorial/mcp/index.html")


class LoginFailed(Exception):
    """The server side login did not succeed. The text names the step, never a value."""


@dataclass(frozen=True)
class Vault:
    server: str
    email: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class DemoSettings:
    controller_url: str
    controller_token: str = field(repr=False)
    ## the demo's Vaultwarden, normalized: the only server BWTIDY_ALLOWED_SERVERS may hold
    server: str = SERVER
    session_max: float = SESSION_MAX
    max_poll: float = MAX_POLL
    ## the workers' idle timeout, for the start page's text
    idle_timeout: float = IDLE_TIMEOUT
    ## the public host name (DEMO_HOST, which compose gives bwtidy as BWTIDY_MCP_HOSTS), for
    ## the MCP address in the docs. Empty uses the host of the request.
    public_host: str = ""

    @classmethod
    def from_env(cls) -> "DemoSettings":
        url = env_text("DEMO_CONTROLLER_URL")
        if url is None:
            raise SettingsError(
                "DEMO_CONTROLLER_URL must name the controller, such as http://controller:8100"
            )
        directory = Path(env_text("DEMO_SECRETS") or init.SECRETS_DIR)
        try:
            token = (directory / init.CONTROLLER_TOKEN).read_text().strip()
        except OSError as error:
            raise SettingsError(
                f"DEMO_SECRETS: {init.CONTROLLER_TOKEN} in {directory} cannot be read "
                f"({error.strerror or type(error).__name__})"
            ) from None
        if not token:
            raise SettingsError(f"DEMO_SECRETS: {init.CONTROLLER_TOKEN} in {directory} is empty")
        try:
            server = normalize_server(env_text("DEMO_SERVER") or SERVER)[0]
        except AccountError as error:
            raise SettingsError(f"DEMO_SERVER: {error}") from None
        return cls(
            controller_url=url.rstrip("/"),
            controller_token=token,
            server=server,
            session_max=env_seconds("DEMO_SESSION_MAX", SESSION_MAX),
            idle_timeout=Policy.from_env().idle_timeout,
            public_host=(env_text("BWTIDY_MCP_HOSTS") or "").split(",")[0].strip(),
        )


def read_index(gateway: GatewaySettings, settings: DemoSettings) -> str:
    """The frontend's index.html, after the checks of the settings the demo depends on."""
    if gateway.allowed_servers != {settings.server}:
        raise SettingsError(f"BWTIDY_ALLOWED_SERVERS must hold only {settings.server}")
    if gateway.static_dir is None:
        raise SettingsError("BWTIDY_STATIC_DIR must name the built frontend")
    path = gateway.static_dir / "index.html"
    try:
        index = path.read_text()
    except OSError as error:
        raise SettingsError(
            f"BWTIDY_STATIC_DIR: {path} cannot be read ({error.strerror or type(error).__name__})"
        ) from None
    if BODY.search(index) is None:
        raise SettingsError(f"BWTIDY_STATIC_DIR: {path} has no <body>")
    return index


def template(name: str) -> str:
    return resources.files("bwtidy_demo").joinpath("templates", name).read_text()


def with_banner(index: str) -> str:
    """index.html with the banner right after its <body>."""
    banner = template("banner.html")
    return BODY.sub(lambda match: match.group(0) + "\n" + banner, index, count=1)


def with_docs_note(page: str, note: str) -> str:
    """A docs page with the note at the top of its content."""
    for pattern in (DOCS_CONTENT, BODY):
        if pattern.search(page):
            return pattern.sub(lambda match: match.group(0) + "\n" + note, page, count=1)
    return page


def duration(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes >= 120 and minutes % 60 == 0:
        return f"{minutes // 60} hours"
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def canonical(path: str) -> str:
    """The path with its empty and dot segments resolved and no trailing slash.

    The proxy forwards the raw path, and httpx resolves its dot segments, so /api/./bw-login
    reaches the worker as /api/bw-login: every check here compares this form.
    """
    parts: list[str] = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/" + "/".join(parts)


def refused(method: str, path: str) -> bool:
    """Whether a request takes a server, an email or a password, or logs bw in or out."""
    if method == "POST" and path in REFUSED_POST:
        return True
    return any(path == prefix or path.startswith(prefix + "/") for prefix in REFUSED_ANY)


def capped_days(body: bytes) -> bytes:
    """The body of POST /api/mcp/token asking for at most MCP_TOKEN_DAYS.

    A missing days, null (no expiry) or more is rewritten to MCP_TOKEN_DAYS. A body that is no
    JSON object goes on as it is, the worker refuses it. An object is written anew, so the
    worker parses exactly what was checked.
    """
    try:
        data = json.loads(body) if body.strip() else {}
    except ValueError:
        return body
    if not isinstance(data, dict):
        return body
    days = data.get("days")
    if days is None or (type(days) is int and days > MCP_TOKEN_DAYS):
        data["days"] = MCP_TOKEN_DAYS
    return json.dumps(data).encode()


class BodyTooLarge(Exception):
    pass


class Disconnected(Exception):
    pass


async def read_body(receive: Receive, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise Disconnected()
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            raise BodyTooLarge()
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def with_body(scope: Scope, receive: Receive, body: bytes) -> tuple[Scope, Receive]:
    """The request again with this JSON body in place of the one read."""
    dropped = {b"content-length", b"transfer-encoding", b"content-type"}
    headers = [(name, value) for name, value in scope["headers"] if name.lower() not in dropped]
    headers += [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return {**scope, "headers": headers}, replay


async def once(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def call(
    worker: Worker,
    method: str,
    url: str,
    headers: Iterable[tuple[str, str]] = (),
    body: dict[str, Any] | None = None,
) -> tuple[int, httpx.Headers, Any]:
    """One request to the worker over its socket, its answer read and closed."""
    sent = list(headers)
    content = None
    if body is not None:
        data = json.dumps(body).encode()
        sent += [("content-type", "application/json"), ("content-length", str(len(data)))]
        content = once(data)
    answer = await worker.send(method, url, headers=sent, content=content, timeout=STEP_TIMEOUT)
    try:
        raw = await answer.aread()
    finally:
        await answer.aclose()
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    return answer.status_code, answer.headers, parsed


def session_cookie(headers: httpx.Headers) -> str:
    for header in headers.get_list("set-cookie"):
        cookie: SimpleCookie = SimpleCookie()
        try:
            cookie.load(header)
        except CookieError:
            continue
        if SESSION_COOKIE in cookie:
            return cookie[SESSION_COOKIE].value
    raise LoginFailed("POST /api/bw-login/finish set no session cookie")


async def log_in(worker: Worker, vault: Vault) -> str:
    """Log the worker's bw in and unlock it, the way the login page does. The session cookie.

    dev/demo.md, Start flow: POST /api/bw-login, its output polled until bw exits, then POST
    /api/bw-login/finish.
    """
    login = {"server": vault.server, "email": vault.email, "password": vault.password}
    status, _, data = await call(worker, "POST", "/api/bw-login", body=login)
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("loginId"), str):
        raise LoginFailed(f"POST /api/bw-login answered {status}")
    header = [(LOGIN_HEADER, data["loginId"])]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LOGIN_TIMEOUT
    after = 0
    while True:
        url = f"/api/bw-login/output?after={after}"
        status, _, data = await call(worker, "GET", url, header)
        if status != 200 or not isinstance(data, dict):
            raise LoginFailed(f"GET /api/bw-login/output answered {status}")
        after = int(data.get("next") or 0)
        if not data.get("running"):
            break
        if loop.time() > deadline:
            raise LoginFailed("bw login did not end in time")
        await asyncio.sleep(LOGIN_POLL)
    if data.get("exitCode") != 0:
        raise LoginFailed(f"bw login exited with {data.get('exitCode')}")
    status, headers, _ = await call(worker, "POST", "/api/bw-login/finish", header)
    if status != 200:
        raise LoginFailed(f"POST /api/bw-login/finish answered {status}")
    return session_cookie(headers)


class Demo:
    """The demo's state beside the gateway's: the controller, the starts and the cleanup."""

    gateway: Gateway

    def __init__(
        self,
        settings: DemoSettings,
        gateway_settings: GatewaySettings,
        index: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.allowed = gateway_settings.allowed_servers
        self.secure = gateway_settings.secure_cookies
        self.page = with_banner(index)
        self.start_page = string.Template(template("start.html"))
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        ## the monotonic start of every live demo, for the hard maximum
        self.started: dict[AccountKey, float] = {}
        ## the emails released or being released, so each user is released once
        self._released: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncGenerator[None]:
        """The controller's client, the start sweep and the hard maximum around the app."""
        async with httpx.AsyncClient(
            base_url=self.settings.controller_url,
            headers={"Authorization": f"Bearer {self.settings.controller_token}"},
            transport=self._transport,
            timeout=30.0,
        ) as client:
            self._client = client
            try:
                yield
            finally:
                for task in list(self._tasks):
                    task.cancel()
                await asyncio.gather(*self._tasks, return_exceptions=True)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("The demo's controller client is not open")
        return self._client

    def background(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def take(self) -> Vault | None:
        """A ready vault from the controller, None when none is ready."""
        response = await self.client.post("/take")
        if response.status_code == 409:
            return None
        response.raise_for_status()
        data = response.json()
        return Vault(server=data["server"], email=data["email"], password=data["password"])

    async def release(self, email: str) -> None:
        """Have the controller delete the Vaultwarden user, once per email."""
        if email in self._released:
            return
        if not email.endswith("@" + DOMAIN):
            log.warning("Left the user %s alone, it is not a demo user", email)
            return
        self._released.add(email)
        wait = RELEASE_WAIT
        for attempt in range(1, RELEASE_TRIES + 1):
            try:
                response = await self.client.post("/release", json={"email": email})
            except httpx.TransportError as error:
                reason = type(error).__name__
            else:
                if response.status_code == 204:
                    log.info("Released the demo user %s", email)
                    return
                if response.status_code != 502:
                    log.error(
                        "The controller refused to release %s: %d", email, response.status_code
                    )
                    self._released.discard(email)
                    return
                reason = "Vaultwarden refused the deletion"
            if attempt < RELEASE_TRIES:
                log.warning("Releasing %s failed (%s), trying again in %g s", email, reason, wait)
                await asyncio.sleep(wait)
                wait *= 2
        log.error("Releasing %s failed %d times, the user stays", email, RELEASE_TRIES)
        self._released.discard(email)

    def delete_account(self, host_dir: str, email: str) -> None:
        """Delete the account directory with its backups, the way Gateway.delete_on_request
        does. The caller holds the account's lock and knows no worker runs in it."""
        accounts = self.gateway.accounts
        path = accounts.root / host_dir / email
        try:
            if path.is_symlink() or path.exists():
                accounts.delete(path, force=True)
                log.info("Deleted the demo account %s/%s", host_dir, email)
            elif accounts.backups is not None:
                accounts.backups.remove(host_dir, email, force=True)
        except (AccountError, OSError) as error:
            log.error("Deleting the demo account %s/%s failed: %s", host_dir, email, error)

    async def end_account(self, key: AccountKey) -> None:
        """The cleanup of a demo: its account directory, its routing cookies and its user.

        Idempotent: once the directory is gone and the user released, it does nothing.
        """
        self.started.pop(key, None)
        gateway = self.gateway
        async with gateway.lock(key):
            if gateway.workers.at(key.path(gateway.accounts.root)) is not None:
                log.warning("A worker runs in the demo account %s again, it stays", key.label)
                return
            self.delete_account(key.host_dir, key.email)
            gateway.forget_key(key)
        await self.release(key.email)

    async def cleanup(self, worker: Worker) -> None:
        """The gateway's on_worker_exit hook."""
        await self.end_account(worker.key)

    async def end(self, token: str | None) -> None:
        """End the demo of this routing cookie: stop its worker, whose exit hook cleans up."""
        gateway = self.gateway
        key = gateway.route(token)
        gateway.forget(token)
        if key is None:
            return
        worker = gateway.workers.get(key)
        if worker is not None:
            log.info("Ending the demo %s", key.email)
            await gateway.workers.stop(worker)
        await self.end_account(key)

    async def abandon(self, email: str, key: AccountKey | None, worker: Worker | None) -> None:
        """Leave nothing of a start that failed: no worker, no directory, no user."""
        if worker is not None:
            await self.gateway.workers.stop(worker)
        if key is not None:
            await self.end_account(key)
        else:
            await self.release(email)

    async def start(self, request: Request) -> Response:
        if request.headers.get("sec-fetch-site", "none") not in SAME_SITE:
            return PlainTextResponse(CROSS_SITE, 403)
        await self.end(request.cookies.get(ACCOUNT_COOKIE))
        try:
            vault = await self.take()
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            log.error("The controller handed out no vault: %s", type(error).__name__)
            return noted("failed")
        if vault is None:
            log.info("A demo start found no ready vault")
            return noted("free")
        key: AccountKey | None = None
        worker: Worker | None = None
        token = session = ""
        started = False
        try:
            if normalize_server(vault.server)[0] != self.settings.server:
                raise AccountError("The vault is not on the demo's server.")
            key = await account_key(vault.server, vault.email, self.allowed)
            worker = await self.gateway.open(key, create=True)
            if worker is None:
                raise WorkerStartError("The worker did not start.")
            session = await log_in(worker, vault)
            token = self.gateway.issue(key, None)
            self.started[key] = time.monotonic()
            started = True
        except LimitError:
            log.info("A demo start for %s reached a cap", vault.email)
            return noted("full")
        except Exception as error:
            ## the step of a failed login, else the type only: no answer of the worker is logged
            reason = str(error) if isinstance(error, LoginFailed) else type(error).__name__
            log.error("The demo start for %s failed: %s", vault.email, reason)
            return noted("failed")
        finally:
            if not started:
                await self.abandon(vault.email, key, worker)
        log.info("Started the demo %s", vault.email)
        response = RedirectResponse("/", status_code=303)
        for name, value in ((ACCOUNT_COOKIE, token), (SESSION_COOKIE, session)):
            response.set_cookie(
                name, value, httponly=True, samesite="strict", path="/", secure=self.secure
            )
        return response

    def ended(self, response: Response) -> Response:
        for name in (ACCOUNT_COOKIE, SESSION_COOKIE):
            response.delete_cookie(
                name, httponly=True, samesite="strict", path="/", secure=self.secure
            )
        return response

    async def stop_expired(self) -> None:
        """Stop every demo older than DEMO_SESSION_MAX. Its exit hook cleans up."""
        now = time.monotonic()
        for key, started in list(self.started.items()):
            if now - started < self.settings.session_max:
                continue
            self.started.pop(key, None)
            log.info("The demo %s reached its maximum time, ending it", key.email)
            worker = self.gateway.workers.get(key)
            if worker is None:
                self.background(self.end_account(key))
            else:
                self.background(self.gateway.workers.stop(worker))

    async def enforce_max(self) -> None:
        while True:
            await asyncio.sleep(self.settings.max_poll)
            await self.stop_expired()

    async def sweep(self) -> None:
        """Delete every account left from before and release its user: all are demo accounts."""
        accounts = self.gateway.accounts
        found: list[tuple[str, str]] = []
        for path in accounts.temporary_directories():
            accounts.delete_temporary(path)
            found.append((path.parent.name, path.name.removeprefix(TEMPORARY_PREFIX)))
        found += [(path.parent.name, path.name) for path in accounts.account_directories()]
        if accounts.backups is not None and accounts.backups.root.is_dir():
            for host in sorted(accounts.backups.root.iterdir()):
                if host.is_dir() and not host.is_symlink():
                    found += [(host.name, path.name) for path in sorted(host.iterdir())]
        for host_dir, email in dict.fromkeys(found):
            self.delete_account(host_dir, email)
        if found:
            log.info("Deleted %d demo accounts left from before", len(dict.fromkeys(found)))
        for email in dict.fromkeys(email for _, email in found):
            await self.release(email)

    def docs_note(self, path: str, host: str) -> str:
        """The note for a docs page, with the MCP address on the MCP tutorial."""
        mcp = ""
        if path in MCP_PAGES:
            name = (self.settings.public_host if self.secure else "") or host
            address = f"{'https' if self.secure else 'http'}://{name}/mcp"
            mcp = string.Template(template("docs_mcp.html")).substitute(
                address=html.escape(address), days=MCP_TOKEN_DAYS
            )
        return string.Template(template("docs_note.html")).substitute(
            idle=duration(self.settings.idle_timeout),
            total=duration(self.settings.session_max),
            mcp=mcp,
        )

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.get(START_PAGE, include_in_schema=False)
        def start_page(note: str | None = None) -> Response:
            text = NOTES.get(note or "")
            return HTMLResponse(
                self.start_page.substitute(
                    note=f'<p class="note" role="status">{text}</p>' if text else "",
                    idle=duration(self.settings.idle_timeout),
                    total=duration(self.settings.session_max),
                ),
                headers={"Cache-Control": "no-store"},
            )

        @router.post(START_PATH, include_in_schema=False)
        async def start(request: Request) -> Response:
            return await self.start(request)

        @router.post(END_PATH, include_in_schema=False)
        async def end(request: Request) -> Response:
            await self.end(request.cookies.get(ACCOUNT_COOKIE))
            return self.ended(RedirectResponse(START_PAGE, status_code=303))

        @router.get("/", include_in_schema=False)
        @router.get("/index.html", include_in_schema=False)
        def index(request: Request) -> Response:
            if self.gateway.route(request.cookies.get(ACCOUNT_COOKIE)) is None:
                return RedirectResponse(START_PAGE, status_code=303)
            return HTMLResponse(self.page, headers={"Cache-Control": "no-store"})

        return router


def noted(note: str) -> Response:
    return RedirectResponse(f"{START_PAGE}?note={note}", status_code=303)


class DemoMiddleware:
    """Runs before the gateway's proxy: the refused routes, End demo and the MCP token cap."""

    def __init__(self, app: ASGIApp, demo: Demo) -> None:
        self.app = app
        self.demo = demo

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope["method"]
        path = canonical(scope["path"])
        if refused(method, path):
            ## repr: the path comes from the request and may hold a line break
            log.info("Refused %s %r", method, path[:200])
            await PlainTextResponse(REFUSED, 403)(scope, receive, send)
        elif method == "DELETE" and path == ACCOUNT_PATH:
            await self.demo.end(Request(scope).cookies.get(ACCOUNT_COOKIE))
            await self.demo.ended(Response(status_code=204))(scope, receive, send)
        elif method == "POST" and path == MCP_TOKEN_PATH:
            try:
                body = await read_body(receive, TOKEN_BODY_LIMIT)
            except Disconnected:
                return
            except BodyTooLarge:
                await PlainTextResponse(TOO_LARGE, 413)(scope, receive, send)
                return
            scope, receive = with_body(scope, receive, capped_days(body))
            await self.app(scope, receive, send)
        elif method == "GET" and (path == DOCS_PATH or path.startswith(DOCS_PATH + "/")):
            await self.docs(scope, receive, send, path)
        else:
            await self.app(scope, receive, send)

    async def docs(self, scope: Scope, receive: Receive, send: Send, path: str) -> None:
        """A docs page answered by the site, with the demo's note added to its HTML."""
        ## a conditional request would get a 304 for the page without the note
        scope = {
            **scope,
            "headers": [
                (name, value)
                for name, value in scope["headers"]
                if name not in (b"if-none-match", b"if-modified-since", b"range")
            ],
        }
        host = Request(scope).headers.get("host", "localhost")
        held: list[Message] = []
        page = False

        async def intercept(message: Message) -> None:
            nonlocal page
            if message["type"] == "http.response.start":
                headers = Headers(raw=message["headers"])
                page = message["status"] == 200 and headers.get("content-type", "").startswith(
                    "text/html"
                )
                if not page:
                    await send(message)
                    return
            elif not page:
                await send(message)
                return
            held.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                start = held[0]
                body = b"".join(m.get("body", b"") for m in held[1:])
                text = with_docs_note(
                    body.decode("utf-8", "replace"), self.demo.docs_note(path, host)
                ).encode()
                headers = MutableHeaders(raw=list(start["headers"]))
                for name in ("etag", "last-modified", "content-length"):
                    del headers[name]
                headers["content-length"] = str(len(text))
                headers["cache-control"] = "no-store"
                await send({**start, "headers": headers.raw})
                await send({"type": "http.response.body", "body": text})

        await self.app(scope, receive, intercept)


def create_demo_app(
    gateway_settings: GatewaySettings,
    settings: DemoSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """The gateway wrapped by the demo. transport replaces the controller's in tests."""
    demo = Demo(settings, gateway_settings, read_index(gateway_settings, settings), transport)
    app = create_gateway_app(gateway_settings, routers=[demo.router()], on_worker_exit=demo.cleanup)
    demo.gateway = app.state.gateway
    app.state.demo = demo
    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with demo.running():
            await demo.sweep()
            async with inner(app):
                enforcer = asyncio.create_task(demo.enforce_max())
                try:
                    yield
                finally:
                    enforcer.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await enforcer

    app.router.lifespan_context = lifespan
    ## added last, so it is the outermost and runs before the gateway's proxy
    app.add_middleware(DemoMiddleware, demo=demo)
    return app


def run() -> None:
    setup_logging()
    logging.getLogger("bwtidy_demo").setLevel(logging.INFO)
    try:
        gateway_settings = GatewaySettings.from_env()
        app = create_demo_app(gateway_settings, DemoSettings.from_env())
    except SettingsError as error:
        raise SystemExit(str(error)) from None
    uvicorn.run(
        app,
        host=gateway_settings.host,
        port=gateway_settings.port,
        log_config=uvicorn_log_config(),
        timeout_graceful_shutdown=REQUEST_DRAIN,
    )
