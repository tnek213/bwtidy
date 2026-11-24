"""FastAPI app factory and the `bwtidy` entry point."""

import asyncio
import contextlib
import functools
import logging
import sqlite3
import sys
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

import httpx2 as httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from bwtidy.api import account as account_api
from bwtidy.api import backups as backups_api
from bwtidy.api import bwlogin as bwlogin_api
from bwtidy.api import datatypes as datatypes_api
from bwtidy.api import errors as errors_api
from bwtidy.api import generator as generator_api
from bwtidy.api import grid as grid_api
from bwtidy.api import mcp as mcp_api
from bwtidy.api import operations as operations_api
from bwtidy.api import queue as queue_api
from bwtidy.api import session as session_api
from bwtidy.api import settings as settings_api
from bwtidy.api import statistics as statistics_api
from bwtidy.api import templates as templates_api
from bwtidy.api.app_state import AppState
from bwtidy.backups import BackupKeeper
from bwtidy.bw import BwClient, BwLogin, BwServeProcess
from bwtidy.config import Settings
from bwtidy.db import Database
from bwtidy.login_state import LoginStateFile
from bwtidy.logs import setup_logging, uvicorn_log_config
from bwtidy.mcp import McpToken, RequireMcpToken, create_mcp_server, streamable_http_security
from bwtidy.protocol import DB_FILE
from bwtidy.queue.autocommit import AutoCommit
from bwtidy.queue.review import ReviewQueue
from bwtidy.queue.to_fill import ToFill
from bwtidy.queue.wipe import backup_in_use
from bwtidy.session import CHECK_INTERVAL, SessionHooks, SessionManager
from bwtidy.validation import never_echo_a_refused_body
from bwtidy.vault import Formats, HiddenValues, ToolNoteId, VaultCache, refresh_formats
from bwtidy.worker import WorkerLifetime, stop_by_signal
from bwtidy.writes import WritePath

log = logging.getLogger(__name__)

## uvicorn's exit code when the server could not start
STARTUP_FAILURE = 3

## unlock runs the KDF and a full sync can take long on a slow server
BW_SERVE_TIMEOUT = 120.0
## how long an exit or a SIGTERM waits for the requests still running before it cancels them
## and goes on with the lifespan's end (uvicorn waits for ever without it)
SHUTDOWN_GRACE = 30


def create_app(
    settings: Settings | None = None,
    *,
    start_bw_serve: bool = True,
    http: httpx.AsyncClient | None = None,
    clock: Callable[[], float] = time.monotonic,
    request_exit: Callable[[], None] = stop_by_signal,
) -> FastAPI:
    settings = settings or Settings.from_env()
    http = http or bw_serve_client(settings)
    client = BwClient(http)
    db = Database(settings.data_dir / DB_FILE)
    tool_note_id = ToolNoteId(db)
    formats = Formats(tool_note_id)
    backup_dir = settings.backup_dir
    hidden = HiddenValues(db)

    ## the hooks call objects built further down, which all exist before the first login
    def after_load() -> None:
        tool_note_id.keep(cache)
        to_fill.after_load()
        refresh_formats(cache, formats)

    def on_unlocked() -> None:
        keeper.start_background_backup()
        bw_login.note(True)
        worker.unlocked()

    cache = VaultCache(client, on_load=after_load)
    session = SessionManager(
        client,
        cache,
        tool_note_id,
        SessionHooks(
            on_unlocked=on_unlocked,
            before_end=lambda: auto_commit.commit_before_session_ends(),
            on_ended=lambda limit: keeper.end_session(limit),
            after_end=lambda: worker.session_ended(),
        ),
        backup_dir=backup_dir,
        clock=clock,
        hidden=hidden,
        formats=formats,
        heartbeat_timeout=settings.policy.heartbeat_timeout,
        idle_timeout=settings.policy.idle_timeout,
    )
    keeper = BackupKeeper(
        session,
        cache,
        backup_dir,
        settings.bw_bin,
        db,
        queue_in_use=functools.partial(backup_in_use, db),
        retention=settings.policy.retention,
        version=settings.version,
    )
    writes = WritePath(session, cache, keeper, db, remembered=tool_note_id, clock=clock)
    queue = ReviewQueue(
        db,
        cache,
        writes,
        keeper,
        hidden,
        on_ui_entry=lambda entry_id: auto_commit.note_queued(entry_id),
        on_written=lambda op, created: to_fill.note_written(op, created),
        failed_keep_hours=settings.policy.failed_keep_hours,
    )
    to_fill = ToFill(cache, queue)
    auto_commit = AutoCommit(queue, db, session, clock=clock)
    mcp_token = McpToken(settings.data_dir, db)
    bw_serve = (
        BwServeProcess(
            settings.bw_bin,
            settings.bw_serve_port,
            client,
            on_restart=session.restore_after_restart,
            socket=settings.bw_socket,
        )
        if start_bw_serve
        else None
    )
    bw_login = BwLogin(
        settings.bw_bin,
        client,
        clock=clock,
        bw_serve=bw_serve,
        account=settings.account,
        login_state=LoginStateFile(settings.data_dir),
    )
    mcp = create_mcp_server(session, queue, bw_login, formats)
    ## stateless: a backend restart leaves no MCP session behind that the agent would have to drop
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=streamable_http_security(settings.mcp_hosts),
    )
    worker = WorkerLifetime(
        session,
        bw_login,
        exit_after_session=settings.exit_after_session,
        idle_exit=settings.idle_exit,
        pending_marker=settings.pending_marker,
        clock=clock,
        request_exit=request_exit,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        account_api.drop_stale_delete_marker(settings.data_dir)
        ## a data directory that cannot be written fails the first write instead of the start,
        ## a file at another schema version fails the start (SchemaError)
        try:
            db.open()
            ## no commit runs yet, and an account has one worker
            queue.fail_interrupted()
            ## a failed entry's hidden value does not outlive its worker
            queue.forget_failed_values()
        except (OSError, sqlite3.Error) as error:
            log.warning("Could not open the change log at startup: %s", error)
        if bw_serve is not None:
            await bw_serve.start()
        watcher = asyncio.create_task(watch_timeouts(session, bw_login, worker))
        committer = asyncio.create_task(auto_commit.run())
        expirer = asyncio.create_task(queue.run_failed_expiry())
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            ## an MCP end_session or log_out_of_bitwarden that made the worker exit
            await mcp.settle()
            for task in (committer, expirer, watcher):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await auto_commit.settle()
            await bw_login.close()
            ## a stop waits for no backup step, so it stays within the gateway's kill
            keeper.cancel_background_backup()
            await session.logout()
            if bw_serve is not None:
                await bw_serve.stop()
            await http.aclose()
            db.close()

    app = FastAPI(title="bwtidy", lifespan=lifespan)
    never_echo_a_refused_body(app)
    errors_api.answer_domain_errors(app)
    app.state.bw = AppState(
        settings=settings,
        db=db,
        cache=cache,
        session=session,
        bw_login=bw_login,
        bw_serve=bw_serve,
        writes=writes,
        backups=keeper,
        queue=queue,
        to_fill=to_fill,
        auto_commit=auto_commit,
        mcp=mcp,
        worker=worker,
        mcp_token=mcp_token,
        formats=formats,
    )

    @app.get("/api/health", response_model=None)
    def health() -> dict[str, str] | JSONResponse:
        ## the gateway reads a 503 as a worker on its way out and waits for its exit
        if worker.exiting:
            return JSONResponse({"status": "exiting", "version": settings.version}, 503)
        return {"status": "ok", "version": settings.version}

    app.include_router(session_api.router)
    app.include_router(bwlogin_api.router)
    app.include_router(grid_api.router)
    app.include_router(backups_api.router)
    app.include_router(operations_api.router)
    app.include_router(templates_api.router)
    app.include_router(generator_api.router)
    app.include_router(datatypes_api.router)
    app.include_router(statistics_api.router)
    app.include_router(queue_api.router)
    app.include_router(mcp_api.router)
    app.include_router(settings_api.router)
    app.include_router(account_api.router)
    ## the SDK's own /mcp route behind the token, without its Starlette wrapper, whose lifespan
    ## runs above
    for route in mcp_app.routes:
        assert isinstance(route, Route)
        app.router.routes.append(Route(route.path, RequireMcpToken(route.endpoint, mcp_token)))

    ## mounted last so every /api route above wins over the static files
    if settings.static_dir is not None:
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")

    return app


def bw_serve_client(settings: Settings) -> httpx.AsyncClient:
    """The client for bw serve: its unix socket in worker mode, else its port on 127.0.0.1.

    The base URL stays the same on the socket: bw serve answers 403 unless the Host header is
    one it allows, like 127.0.0.1:PORT.
    """
    transport = None
    if settings.bw_socket is not None:
        transport = httpx.AsyncHTTPTransport(uds=str(settings.bw_socket))
    return httpx.AsyncClient(
        transport=transport,
        base_url=f"http://127.0.0.1:{settings.bw_serve_port}",
        timeout=BW_SERVE_TIMEOUT,
    )


async def watch_timeouts(
    session: SessionManager, bw_login: BwLogin, worker: WorkerLifetime | None = None
) -> None:
    """End the session and drop a Bitwarden login once their timeouts pass."""
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            await session.check_timeouts()
            await bw_login.expire_if_due()
            if worker is not None:
                worker.check_idle()
        except Exception:
            log.exception("Checking the timeouts failed")


def server_config(app: FastAPI, settings: Settings) -> uvicorn.Config:
    """uvicorn's settings: the unix socket or the port, our log format, the shutdown's limit."""
    where: dict[str, Any] = (
        {"uds": str(settings.socket)}
        if settings.socket is not None
        else {"host": settings.host, "port": settings.port}
    )
    return uvicorn.Config(
        app,
        log_config=uvicorn_log_config(),
        timeout_graceful_shutdown=SHUTDOWN_GRACE,
        **where,
    )


def run() -> None:
    setup_logging()
    settings = Settings.from_env()
    server: uvicorn.Server | None = None

    def request_exit() -> None:
        ## the same clean shutdown as Ctrl+C: the lifespan locks and stops bw serve
        if server is not None:
            server.should_exit = True

    app = create_app(settings, request_exit=request_exit)
    server = uvicorn.Server(server_config(app, settings))
    ## uvicorn.run() hides its server, so its ending is done here: the socket file goes, and a
    ## failed start is exit code 3
    try:
        server.run()
    finally:
        if settings.socket is not None:
            settings.socket.unlink(missing_ok=True)
    if not server.started:
        sys.exit(STARTUP_FAILURE)
