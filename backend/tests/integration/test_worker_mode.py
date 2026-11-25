"""Worker mode against the real bw and Vaultwarden (docs/spec.md, Worker mode).

The worker gets a CLI state of its own (BITWARDENCLI_APPDATA_DIR in a temporary directory),
so it logs in as another device and leaves the app container's login alone.
"""

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from support.app import purge_tool_notes
from support.cli import EMAIL, PASSWORD, SERVER, BwCli

from bwtidy.config import Settings
from bwtidy.main import create_app
from bwtidy.protocol import LOGIN_HEADER
from bwtidy.protocol import SESSION_COOKIE as COOKIE

pytestmark = pytest.mark.integration

TIMEOUT = 120.0


class Account:
    """One account's directory, laid out the way the gateway makes it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.app = root / "app"
        self.bw = root / "bw"
        self.run = root / "run"
        self.marker = root / "pending"
        for path in (self.app, self.bw, self.run):
            path.mkdir(parents=True, exist_ok=True)
        root.chmod(0o700)

    @property
    def app_socket(self) -> Path:
        return self.run / "app.sock"

    @property
    def bw_socket(self) -> Path:
        return self.run / "bw.sock"

    def cli(self) -> BwCli:
        return BwCli(appdata=str(self.bw))


@pytest.fixture(scope="module")
def account(bw_cli: BwCli, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Account]:
    """The tool note a login makes is deleted for good, so test_seed.py still sees the seed."""
    purge_tool_notes(bw_cli)
    ## short, a unix socket path has a limit of about 100 bytes
    account = Account(tmp_path_factory.mktemp("acct"))
    try:
        yield account
    finally:
        purge_tool_notes(bw_cli)


def wait_for(done, what: str, timeout: float = TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while not done():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.5)


def bw_serve_processes(socket: Path) -> list[int]:
    """The pids of processes whose command line names the socket."""
    pids = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmdline = (proc / "cmdline").read_bytes()
        except OSError:
            continue
        if str(socket).encode() in cmdline and int(proc.name) != os.getpid():
            pids.append(int(proc.name))
    return pids


def test_a_login_and_an_unlock_through_bw_serve_on_its_socket(
    account: Account, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("BITWARDENCLI_APPDATA_DIR", str(account.bw))
    account.marker.touch()
    settings = replace(
        Settings.from_env(),
        data_dir=account.app,
        backup_dir=account.app / "backups",
        static_dir=None,
        bw_socket=account.bw_socket,
        account_server=SERVER,
        account_email=EMAIL,
        pending_marker=account.marker,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        state = client.get("/api/session").json()["bwLogin"]
        assert state == {"loggedIn": False, "server": SERVER, "email": EMAIL, "accountFixed": True}
        other = {"server": SERVER, "email": "someone@bwtidy.test", "password": PASSWORD}
        assert client.post("/api/bw-login", json=other).status_code == 422

        response = client.post(
            "/api/bw-login", json={"server": SERVER, "email": EMAIL, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        headers = {LOGIN_HEADER: response.json()["loginId"]}
        deadline = time.monotonic() + TIMEOUT
        while True:
            out = client.get("/api/bw-login/output?after=0", headers=headers).json()
            if not out["running"]:
                break
            assert time.monotonic() < deadline, "bw login hangs"
            time.sleep(0.5)
        assert out["exitCode"] == 0
        assert account.marker.exists()

        response = client.post("/api/bw-login/finish", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "unlocked"
        assert not account.marker.exists()

        assert account.bw_socket.is_socket()
        transport = httpx.HTTPTransport(uds=str(account.bw_socket))
        base_url = f"http://127.0.0.1:{settings.bw_serve_port}"
        with httpx.Client(transport=transport, base_url=base_url) as bw_serve:
            status = bw_serve.get("/status").json()["data"]["template"]
        assert status["status"] == "unlocked"
        assert status["userEmail"] == EMAIL

        assert client.delete("/api/session").status_code == 204
        assert client.portal is not None
        client.portal.call(app.state.bw.backups.wait_for_background_backup)


def start_worker(account: Account, log: Path, **extra: str) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "BITWARDENCLI_APPDATA_DIR": str(account.bw),
        "BWTIDY_DATA_DIR": str(account.app),
        "BWTIDY_SOCKET": str(account.app_socket),
        "BWTIDY_BW_SOCKET": str(account.bw_socket),
        "BWTIDY_ACCOUNT_SERVER": SERVER,
        "BWTIDY_ACCOUNT_EMAIL": EMAIL,
        "BWTIDY_EXIT_AFTER_SESSION": "1",
        **extra,
    }
    ## the entry point the image runs, from the venv uv run put first on the path
    entry = Path(sys.executable).parent / "bwtidy"
    assert entry.exists()
    with log.open("ab") as out:
        return subprocess.Popen(
            [str(entry)], env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=out
        )


def worker_client(account: Account) -> httpx.Client:
    transport = httpx.HTTPTransport(uds=str(account.app_socket))
    return httpx.Client(transport=transport, base_url="http://worker", timeout=TIMEOUT)


def health(client: httpx.Client) -> bool:
    try:
        return client.get("/api/health").status_code == 200
    except httpx.TransportError:
        return False


def stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.terminate()
        proc.wait(30)


def test_the_worker_answers_on_its_socket_and_exits_after_the_session(
    account: Account, tmp_path: Path
):
    account.cli().ensure_logged_in()
    proc = start_worker(account, tmp_path / "worker.log")
    try:
        with worker_client(account) as client:
            wait_for(lambda: health(client) or proc.poll() is not None, "the worker's health")
            assert proc.poll() is None, "the worker exited at its start"
            assert client.get("/api/health").json()["status"] == "ok"
            assert bw_serve_processes(account.bw_socket)

            response = client.post("/api/session", json={"password": PASSWORD})
            assert response.status_code == 200, response.text
            client.cookies.set(COOKIE, response.cookies[COOKIE])
            assert client.get("/api/session").json()["state"] == "unlocked"
            assert client.delete("/api/session").status_code == 204

        assert proc.wait(TIMEOUT) == 0
        assert not account.app_socket.exists()
        assert bw_serve_processes(account.bw_socket) == []
    finally:
        stop(proc)


def test_the_worker_exits_when_no_session_starts(account: Account, tmp_path: Path):
    account.cli().ensure_logged_in()
    proc = start_worker(account, tmp_path / "worker.log", BWTIDY_IDLE_EXIT="1")
    try:
        ## the idle time is checked every 10 seconds
        assert proc.wait(TIMEOUT) == 0
        assert not account.app_socket.exists()
        assert bw_serve_processes(account.bw_socket) == []
    finally:
        stop(proc)
