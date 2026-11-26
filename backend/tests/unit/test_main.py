"""The app create_app builds (docs/spec.md, Worker mode): its health, the frontend beside the API,
the Secure cookie, bw serve on a unix socket and the stop."""

import asyncio
import copy
import re
import socket
import sqlite3
import sys
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from pathlib import Path

import httpx2 as httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from bwtidy import main
from bwtidy.bw.client import BwClient
from bwtidy.bw.serve import BwServeProcess
from bwtidy.config import Settings, version_text
from bwtidy.main import bw_serve_client, create_app
from bwtidy.protocol import DB_FILE
from bwtidy.protocol import SESSION_COOKIE as COOKIE
from bwtidy.version import PRE_RELEASES, semver
from tests.support.app_env import AppEnv
from tests.support.fake_bw import FakeBwServe
from tests.support.hooks import nothing_async
from tests.support.login_env import BOUND, IDLE_EXIT, make_env, queue_an_edit, stop_answering
from tests.support.sample_vault import LOGIN


@pytest.mark.parametrize("secure", [True, False])
def test_the_session_cookie_is_secure_only_when_asked(tmp_path: Path, secure: bool):
    for env in make_env(tmp_path, secure_cookies=secure):
        cookie = env.login().headers["set-cookie"]
        assert cookie.startswith(f"{COOKIE}=")
        assert ("; secure" in cookie.lower()) is secure
        assert ("; secure" in env.client.delete("/api/session").headers["set-cookie"].lower()) is (
            secure
        )


FAKE_SOCKET_BW = f"""#!{sys.executable}
import json, os, sys
from http.server import BaseHTTPRequestHandler
from socketserver import UnixStreamServer

args = sys.argv[1:]
assert args[0] == "serve"
hostname = args[args.index("--hostname") + 1]
assert hostname.startswith("unix:///")
path = hostname[len("unix://"):]


## like bw serve 2026.10.0, which allows only hosts like 127.0.0.1:PORT
ALLOWED = "127.0.0.1:" + args[args.index("--port") + 1]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers["Host"] != ALLOWED:
            self.send_response(403)
            self.end_headers()
            return
        body = json.dumps({{"success": True, "data": {{"template": {{"status": "locked"}}}}}})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def address_string(self):
        return "unix"

    def log_message(self, *args):
        pass


## like node's server.listen, binding fails while a socket file is in the way
UnixStreamServer(path, Handler).serve_forever()
"""


def test_bw_serve_listens_on_the_socket_and_a_stale_one_is_removed(tmp_path: Path):
    bw_bin = tmp_path / "bw"
    bw_bin.write_text(FAKE_SOCKET_BW)
    bw_bin.chmod(0o755)
    path = tmp_path / "bw.sock"
    ## what a killed bw serve leaves behind: the socket file without a listener
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(path))
    stale.close()

    settings = Settings(
        host="127.0.0.1",
        port=8000,
        data_dir=tmp_path / "data",
        static_dir=None,
        bw_bin=str(bw_bin),
        bw_serve_port=8087,
        backup_dir=tmp_path / "data" / "backups",
        bw_socket=path,
    )

    async def scenario() -> None:
        http = bw_serve_client(settings)
        process = BwServeProcess(
            str(bw_bin),
            8087,
            BwClient(http),
            on_restart=nothing_async,
            ready_timeout=15,
            poll_interval=0.05,
            socket=path,
        )
        await process.start()
        assert process.ready
        assert (await BwClient(http).status())["status"] == "locked"
        await process.stop()
        await http.aclose()

    asyncio.run(scenario())


def test_without_a_bw_socket_the_client_uses_the_port():
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        data_dir=Path("/nonexistent"),
        static_dir=None,
        bw_bin="bw",
        bw_serve_port=8087,
        backup_dir=Path("/nonexistent/backups"),
    )
    client = bw_serve_client(settings)
    assert client.base_url == httpx.URL("http://127.0.0.1:8087")
    asyncio.run(client.aclose())


@pytest.mark.usefixtures("short_limits")
def test_a_stop_gives_up_on_a_commit_that_gets_no_answer(tmp_path: Path):
    """A SIGTERM's lifespan end: the commit under way is cancelled, its entry stays pending."""
    environments = make_env(tmp_path, exit_after_session=True, idle_exit=IDLE_EXIT)
    env = next(environments)
    env.bw.items = [copy.deepcopy(LOGIN)]
    assert env.login().status_code == 200
    entry_id = queue_an_edit(env)
    answering = stop_answering(env)
    env.call(env.app.state.bw.auto_commit.start_commit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        ## the TestClient's exit runs the lifespan's end
        stop = pool.submit(next, environments, None)
        try:
            assert stop.result(BOUND) is None
        finally:
            if not stop.done():
                env.call(answering.set)
    assert env.exits == [True]
    with sqlite3.connect(tmp_path / "data" / DB_FILE) as db:
        [(status,)] = db.execute("SELECT status FROM queue WHERE id = ?", (entry_id,)).fetchall()
    assert status == "pending"


def test_an_exit_cancels_the_requests_still_running_after_the_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """uvicorn waits for every running request before the lifespan's end, without a limit."""
    monkeypatch.setattr(main, "SHUTDOWN_GRACE", 1, raising=False)
    path = tmp_path / "app.sock"
    env = AppEnv(tmp_path / "data", bw=FakeBwServe(), fake_backups=False, socket=path)
    app = env.app
    started = threading.Event()

    @app.get("/api/never")
    async def never() -> None:
        started.set()
        await asyncio.Event().wait()

    server = uvicorn.Server(main.server_config(app, env.settings))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + BOUND
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    client = socket.socket(socket.AF_UNIX)
    try:
        client.connect(str(path))
        client.sendall(b"GET /api/never HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        assert started.wait(BOUND)
        server.should_exit = True
        thread.join(BOUND)
        assert not thread.is_alive()
    finally:
        client.close()
        thread.join(BOUND)


PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def package_version() -> str:
    return tomllib.loads(PYPROJECT.read_text())["project"]["version"]


SEMVER = re.compile(r"\d+\.\d+\.\d+(-(alpha|beta|rc)\.\d+)?")


def shown_version() -> str:
    return semver(version("bwtidy"))


def test_the_installed_metadata_holds_the_version_of_pyproject():
    assert version("bwtidy") == package_version()


def test_the_shown_version_is_semver_with_the_pre_release_of_pyproject():
    assert SEMVER.fullmatch(shown_version())
    pre_release = re.search(r"(a|b|rc)\d+$", package_version())
    if pre_release:
        assert f"-{PRE_RELEASES[pre_release.group(1)]}." in shown_version()


def test_health_reports_the_version_and_the_commit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BWTIDY_COMMIT", "261028 abc1234")
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": f"{shown_version()} (261028 abc1234)"}


@pytest.mark.parametrize("commit", [None, "", "  "])
def test_health_reports_the_version_alone_without_a_commit(
    monkeypatch: pytest.MonkeyPatch, commit: str | None
):
    if commit is None:
        monkeypatch.delenv("BWTIDY_COMMIT", raising=False)
    else:
        monkeypatch.setenv("BWTIDY_COMMIT", commit)
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.json() == {"status": "ok", "version": shown_version()}


def test_version_text_puts_the_commit_in_parentheses():
    assert version_text("261111 7db8830-dirty") == f"{shown_version()} (261111 7db8830-dirty)"
    assert version_text() == shown_version()


def app(static_dir: Path | None) -> AppEnv:
    return AppEnv(Path("/nonexistent"), fake_backups=False, static_dir=static_dir)


def test_serves_frontend_and_api_side_by_side(tmp_path: Path):
    (tmp_path / "index.html").write_text("<h1>bwtidy</h1>")
    client = TestClient(app(tmp_path).app)
    assert "bwtidy" in client.get("/").text
    assert client.get("/api/health").json()["status"] == "ok"


def test_without_static_dir_only_api_exists():
    client = TestClient(app(None).app)
    assert client.get("/").status_code == 404
