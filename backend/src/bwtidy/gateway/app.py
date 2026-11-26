"""The gateway app: its routes under /gateway/, the account state machine, the static files.

`Gateway` holds the state behind the routes: the routing cookies, the per-account locks and the
workers. The static files are the built frontend at / and the documentation site at /docs/.
The proxy to the workers is `gateway/proxy.py`.
"""

import asyncio
import contextlib
import logging
import os
import secrets
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bwtidy.config import SettingsError
from bwtidy.gateway.accounts import AccountError, AccountKey, Accounts, LimitError, account_key
from bwtidy.gateway.backups import BackupsError
from bwtidy.gateway.proxy import ProxyMiddleware
from bwtidy.gateway.settings import GatewaySettings
from bwtidy.gateway.stats import AccountLabel, StatsMiddleware
from bwtidy.gateway.workers import EXIT_WAIT, Worker, Workers, WorkerStartError
from bwtidy.logs import setup_logging, uvicorn_log_config
from bwtidy.mcp_token import Check, token_refusal, utc_now
from bwtidy.protocol import ACCOUNT_COOKIE
from bwtidy.stats.recorder import Capacity, Recorder
from bwtidy.validation import never_echo_a_refused_body

log = logging.getLogger(__name__)

## the documentation site's prefix (docs_dir), served to anyone like the frontend
DOCS_PATH = "/docs"
TOO_MANY_PENDING = "Too many new accounts wait for their first login. Try again in a few minutes."
LOCKED = "bwtidy is locked for this account. Log in in the web UI."
## at a stop, how long requests in flight (an MCP stream stays open for good) may run before
## they are cut off and the workers get SIGTERM. A worker still finishes its own requests.
REQUEST_DRAIN = 10


class Gateway:
    """The state behind the routes: the routing cookies, the account locks, the workers."""

    def __init__(
        self, settings: GatewaySettings, stats_extra: Callable[[], Any] | None = None
    ) -> None:
        self.settings = settings
        self.accounts = Accounts(
            settings.accounts_dir,
            uid_base=settings.uid_base,
            switch_uid=settings.switch_uid,
            backups_root=settings.backups_root,
        )
        self.workers = Workers(settings, on_exit=self.worker_exited)
        ## the usage stats, None unless BWTIDY_STATS=1 (docs/spec.md, Usage stats)
        self.recorder = (
            Recorder(
                settings.stats_path, settings.version, capacity=self.capacity, extra=stats_extra
            )
            if settings.stats
            else None
        )
        self._routes: dict[str, AccountKey] = {}
        self._locks: dict[AccountKey, asyncio.Lock] = {}

    def capacity(self) -> Capacity:
        """The workers now, their cap and the starts it refused since the last sample."""
        return Capacity(
            self.workers.count(), self.settings.max_workers, self.workers.take_turned_away()
        )

    def lock(self, key: AccountKey) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def route(self, token: str | None) -> AccountKey | None:
        return self._routes.get(token) if token else None

    def mcp_worker(self, token: str) -> Response | Worker:
        """The running worker of the MCP token's account, else the answer to give."""
        found = self.accounts.with_mcp_token(token)
        if found is None:
            log.info("Refused an MCP request without a valid token")
            return token_refusal(Check.UNKNOWN)
        path, record = found
        if record.expired(utc_now()):
            log.info("Refused an MCP request with an expired token")
            return token_refusal(Check.EXPIRED)
        worker = self.workers.at(path)
        if worker is None:
            label = path.relative_to(self.accounts.root)
            log.info("Refused an MCP request for %s, its worker is not running", label)
            return PlainTextResponse(LOCKED, status_code=503)
        return worker

    def issue(self, key: AccountKey, old: str | None) -> str:
        """A new routing cookie for the account, the browser's old one forgotten."""
        self.forget(old)
        token = secrets.token_urlsafe(32)
        self._routes[token] = key
        return token

    def forget(self, token: str | None) -> None:
        if token:
            self._routes.pop(token, None)

    def forget_key(self, key: AccountKey) -> None:
        """Forget every routing cookie that points at the account, the way DELETE
        /gateway/account forgets one (docs/spec.md, Deleting data)."""
        for token in [token for token, routed in self._routes.items() if routed == key]:
            self._routes.pop(token, None)

    async def open(self, key: AccountKey, create: bool) -> Worker | None:
        """The account's running worker, started when needed. None when it does not exist.

        Only an unlock or a Bitwarden login calls it (browser_worker). create makes a missing
        directory. A worker that no longer answers its health (its session ended and it exits)
        is waited for, so the click always reaches a worker that stays.
        """
        while True:
            async with self.lock(key):
                worker = self.workers.get(key)
                if worker is None:
                    started = self.start_slot(key, create)
                    if started is None:
                        return None
                    path, uid = started
                    try:
                        await self.make_backups(key, uid)
                        return await self.workers.start(key, path, uid)
                    except BaseException:
                        self.workers.release(key)
                        self.drop_after_failed_start(key, path)
                        raise
                if await worker.answers():
                    return worker
            ## outside the lock: the cleanup after its exit takes the same lock
            if not await self.workers.wait_gone(worker, EXIT_WAIT):
                raise WorkerStartError("The account's worker does not answer.")

    async def state(self, key: AccountKey) -> dict[str, Any]:
        """What the page needs before a worker runs, read from the account directory.

        running is true only for a worker that answers its health: one on its way out gets
        replaced by the next click anyway.
        """
        worker = self.workers.get(key)
        running = worker is not None and await worker.answers()
        try:
            path = self.accounts.directory(key)
            exists = path.is_dir()
        except AccountError:
            exists = False
        pending = exists and self.accounts.is_pending(path)
        return {
            "account": {"server": key.server, "email": key.email},
            "running": running,
            "exists": exists,
            "pending": pending,
            "loggedIn": exists and self.accounts.logged_in(path),
            "firstRun": self.first_run(),
        }

    def no_route(self) -> dict[str, Any]:
        """The answer of GET /gateway/account without a routing cookie."""
        return {**NO_ROUTE, "firstRun": self.first_run()}

    def first_run(self) -> bool:
        """No account ever got past its first login, so the login pages show the notice for a
        first start (docs/spec.md, The account form). Derived from the account directories,
        nothing stores it.
        """
        return not self.accounts.any_logged_in()

    def start_slot(self, key: AccountKey, create: bool) -> tuple[Path, int] | None:
        """The account's directory and uid, with a worker slot reserved for it.

        Made when missing and create is set, else None. No await from the caps' checks to the
        reservation and the new directory, so concurrent opens cannot pass a cap together.
        """
        path = self.accounts.directory(key)
        exists = path.is_dir()
        if not exists and not create:
            return None
        if not exists and self.accounts.pending_count() >= self.settings.max_pending:
            log.warning("Refused to create %s, too many accounts are pending", key.label)
            raise LimitError(TOO_MANY_PENDING)
        ## the worker cap before the directory, so a refused create leaves nothing behind
        self.workers.reserve(key)
        try:
            if exists:
                return path, self.accounts.uid_of(path)
            return self.accounts.create(key)
        except BaseException:
            self.workers.release(key)
            raise

    async def make_backups(self, key: AccountKey, uid: int) -> None:
        """The account's backups directory before its worker starts, WorkerStartError if not."""
        try:
            await asyncio.to_thread(self.accounts.make_backups, key, uid)
        except BackupsError as error:
            log.warning("Refused to start the worker for %s: %s", key.label, error)
            raise WorkerStartError("The worker could not start.") from None
        except OSError as error:
            reason = error.strerror or type(error).__name__
            log.warning("The backups directory of %s could not be made: %s", key.label, reason)
            raise WorkerStartError("The worker could not start.") from None

    async def leave(self, token: str | None) -> None:
        """Forget the routing cookie. An account that never got past its first unlock goes."""
        key = self.route(token)
        self.forget(token)
        if key is None:
            return
        worker = self.workers.get(key)
        if worker is not None and self.accounts.is_pending(worker.path):
            log.info("The browser left the pending account %s, stopping its worker", key.label)
            await self.workers.stop(worker)

    def drop_if_pending(self, key: AccountKey, path: Path) -> None:
        """Delete a directory whose login never succeeded, unless a worker runs in it.

        The caller holds the account's lock.
        """
        ## A replacement may already use the directory, its own exit cleans up.
        if self.workers.at(path) is None and self.accounts.is_pending(path):
            self.accounts.delete(path)
            log.info("Deleted the pending account directory of %s", key.label)

    def drop_after_failed_start(self, key: AccountKey, path: Path) -> None:
        """drop_if_pending, its own failure logged so the start's error is the one raised."""
        try:
            self.drop_if_pending(key, path)
        except Exception:
            log.exception("Deleting the pending account directory of %s failed", key.label)

    def delete_on_request(self, key: AccountKey, path: Path) -> bool:
        """Delete the account the worker asked to delete, its backups directory with it even
        with backups in it. True when it did (docs/spec.md, Deleting data). The caller holds the
        account's lock.

        Only the worker's own marker, trusted the way the MCP token file is, makes the gateway
        delete a directory, and never through a symlink. A replacement worker already in the
        directory keeps it: its own exit cleans up.
        """
        if self.workers.at(path) is not None or not self.accounts.delete_requested(path):
            return False
        self.accounts.delete(path, force=True)
        self.forget_key(key)
        log.info("Deleted the account %s at its own request", key.label)
        return True

    async def worker_exited(self, worker: Worker) -> None:
        """An account the worker asked to delete goes, else a directory whose login never
        succeeded goes with its worker."""
        async with self.lock(worker.key):
            if not self.delete_on_request(worker.key, worker.path):
                self.drop_if_pending(worker.key, worker.path)


class AccountRequest(BaseModel):
    server: str
    email: str
    create: bool = False


def stats_outside_accounts(settings: GatewaySettings) -> None:
    """The stats file lies outside the accounts directory and the backups root, whose
    directories belong to the account uids."""
    folder = settings.stats_path.parent
    for name, root in (
        ("BWTIDY_ACCOUNTS_DIR", settings.accounts_dir),
        ("BWTIDY_BACKUPS_ROOT", settings.backups_root),
    ):
        if root is not None and folder.resolve().is_relative_to(root.resolve()):
            raise SettingsError(f"BWTIDY_STATS_DB must be outside {name}")


def overlap(first: Path, second: Path) -> bool:
    """Whether one directory is the other or inside it, symlinks resolved."""
    a, b = first.resolve(), second.resolve()
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


## the answer of GET /gateway/account without a routing cookie, firstRun aside (Gateway.no_route)
NO_ROUTE = {
    "account": None,
    "running": False,
    "exists": False,
    "pending": False,
    "loggedIn": False,
}


def create_gateway_app(
    settings: GatewaySettings | None = None,
    *,
    stats_account: AccountLabel | None = None,
    stats_extra: Callable[[], Any] | None = None,
) -> FastAPI:
    """The gateway's app. With the usage stats on, stats_account gives the label an account's
    events get instead of its opaque id, and stats_extra the JSON a capacity sample keeps in
    extra: both are for a wrapper around the gateway (docs/spec.md, Usage stats)."""
    settings = settings or GatewaySettings.from_env()
    if settings.switch_uid and os.geteuid() != 0:
        raise SettingsError(
            "The gateway runs as root, so each worker can run as its own uid "
            "(BWTIDY_GATEWAY_SWITCH_UID=0 turns that off for tests)"
        )
    if settings.backups_root is not None and overlap(settings.backups_root, settings.accounts_dir):
        raise SettingsError(
            "BWTIDY_BACKUPS_ROOT and BWTIDY_ACCOUNTS_DIR must not be one inside the other"
        )
    if settings.stats:
        stats_outside_accounts(settings)
    gateway = Gateway(settings, stats_extra)
    recorder = gateway.recorder

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        gateway.accounts.prepare()
        gateway.accounts.sweep_pending()
        ## logged now, so an operator sees a directory no login can use before anyone tries
        gateway.accounts.unusable_directories()
        ## a stats file it cannot use (a newer version among them) stops the start
        runner = None
        if recorder is not None:
            recorder.open()
            runner = asyncio.create_task(recorder.run())
        try:
            yield
        finally:
            await gateway.workers.stop_all()
            if recorder is not None:
                await recorder.stop(runner)

    ## FastAPI's own API pages are off: /docs is the documentation site
    app = FastAPI(title="bwtidy gateway", lifespan=lifespan, docs_url=None, redoc_url=None)
    never_echo_a_refused_body(app)
    app.state.gateway = gateway

    @app.get("/api/health")
    @app.get("/api/health/", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.version}

    @app.get("/gateway/account")
    async def account(request: Request) -> dict[str, Any]:
        key = gateway.route(request.cookies.get(ACCOUNT_COOKIE))
        return gateway.no_route() if key is None else await gateway.state(key)

    @app.post("/gateway/account")
    async def open_account(body: AccountRequest, request: Request) -> Response:
        """Route the browser to the account. No worker starts here, only an unlock or a login.

        A new account gets its directory at its first Bitwarden login (create allows that).
        """
        try:
            key = await account_key(body.server, body.email, settings.allowed_servers)
            exists = gateway.accounts.directory(key).is_dir()
        except AccountError as error:
            raise HTTPException(422, str(error)) from None
        if not exists and not body.create:
            return JSONResponse({"exists": False}, status_code=404)
        token = gateway.issue(key, request.cookies.get(ACCOUNT_COOKIE))
        response = JSONResponse(await gateway.state(key))
        response.set_cookie(
            ACCOUNT_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            path="/",
            secure=settings.secure_cookies,
        )
        return response

    @app.delete("/gateway/account", status_code=204)
    async def close_account(request: Request) -> Response:
        await gateway.leave(request.cookies.get(ACCOUNT_COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(
            ACCOUNT_COOKIE,
            httponly=True,
            samesite="strict",
            path="/",
            secure=settings.secure_cookies,
        )
        return response

    app.add_middleware(ProxyMiddleware, gateway=gateway)
    ## added after the proxy, so it wraps it
    if recorder is not None:
        app.add_middleware(StatsMiddleware, recorder=recorder, account=stats_account)

    ## the documentation site, before the frontend at / so that never answers its paths. It
    ## needs no cookie, and as it is no worker's path no request under /docs reaches a worker.
    if settings.docs_dir is not None:

        @app.get(DOCS_PATH, include_in_schema=False)
        def docs_slash() -> Response:
            return RedirectResponse(DOCS_PATH + "/")

        app.mount(DOCS_PATH, StaticFiles(directory=settings.docs_dir, html=True), name="docs")

    ## mounted last so every route above wins over the static files
    if settings.static_dir is not None:
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")

    return app


def run() -> None:
    setup_logging()
    settings = GatewaySettings.from_env()
    app = create_gateway_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_config=uvicorn_log_config(),
        timeout_graceful_shutdown=REQUEST_DRAIN,
    )
