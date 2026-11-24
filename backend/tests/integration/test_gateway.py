"""The gateway against the real bw and Vaultwarden, as root (docs/spec.md, Gateway).

Two accounts log in through the gateway, each worker runs as its own uid, and nothing of one
account is reachable from the other. The backups are under a root of their own
(BWTIDY_BACKUPS_ROOT). Runs as root only, since the gateway switches uids:
scripts/integration --root tests/integration/test_gateway.py. Without root the tests skip.
"""

import asyncio
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from support.app import purge_tool_notes
from support.cli import EMAIL, PASSWORD, SERVER, BwCli

from bwtidy.gateway.app import LOCKED
from bwtidy.gateway.workers import STOP_TIMEOUT
from bwtidy.login_state import LOGGED_IN, LOGGED_OUT
from bwtidy.mcp_token import UNAUTHORIZED
from bwtidy.protocol import ACCOUNT_COOKIE as COOKIE
from bwtidy.protocol import (
    LOGIN_HEADER,
    LOGIN_STATE_FILE,
    PENDING_MARKER,
    REQUEST_HEADER,
    SESSION_COOKIE,
    SUBDIRS,
)

pytestmark = pytest.mark.integration

RESTORE_EMAIL = "restore@bwtidy.test"
NOBODY_EMAIL = "nobody@bwtidy.test"
TIMEOUT = 120.0
## short, so the worker of a failed login exits soon (the idle check runs every 10 seconds)
IDLE_EXIT = "20"
UID_BASE = 70000


class Browser:
    """One browser profile: its routing cookie and its worker's session cookie."""

    def __init__(self, url: str, email: str) -> None:
        self.url = url
        self.email = email
        self.cookies: dict[str, str] = {}

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        response = httpx.request(
            method, self.url + path, headers=headers, timeout=TIMEOUT, **kwargs
        )
        for name in (COOKIE, SESSION_COOKIE):
            if name in response.cookies:
                self.cookies[name] = response.cookies[name]
        return response

    def open(self, create: bool) -> httpx.Response:
        body = {"server": SERVER, "email": self.email, "create": create}
        return self.request("POST", "/gateway/account", json=body)

    def account(self) -> dict[str, Any]:
        return self.request("GET", "/gateway/account").json()

    def login(self, password: str) -> dict[str, Any]:
        """Run bw login through the worker and answer the console's final state."""
        body = {"server": SERVER, "email": self.email, "password": password}
        response = self.request("POST", "/api/bw-login", json=body)
        assert response.status_code == 200, response.text
        self.login_headers = {LOGIN_HEADER: response.json()["loginId"]}
        deadline = time.monotonic() + TIMEOUT
        while True:
            out = self.request("GET", "/api/bw-login/output?after=0", headers=self.login_headers)
            assert out.status_code == 200, out.text
            if not out.json()["running"]:
                return out.json()
            assert time.monotonic() < deadline, "bw login hangs"
            time.sleep(0.5)

    def finish(self) -> httpx.Response:
        return self.request("POST", "/api/bw-login/finish", headers=self.login_headers)


class GatewayProcess:
    def __init__(
        self, url: str, accounts_dir: Path, backups_root: Path, proc: subprocess.Popen[bytes]
    ) -> None:
        self.url = url
        self.accounts_dir = accounts_dir
        self.backups_root = backups_root
        self.proc = proc
        self.browsers: dict[str, Browser] = {}

    def browser(self, email: str) -> Browser:
        return self.browsers.setdefault(email, Browser(self.url, email))

    def directory(self, email: str) -> Path:
        return self.accounts_dir / "vaultwarden" / email

    def backups(self, email: str) -> Path:
        return self.backups_root / "vaultwarden" / email


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(done, what: str, timeout: float = TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while not done():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.5)


def health(url: str) -> bool:
    try:
        return httpx.get(url + "/api/health", timeout=5).status_code == 200
    except httpx.TransportError:
        return False


@contextlib.contextmanager
def running_gateway(base: Path) -> Generator[GatewayProcess]:
    """The real gateway on a free port with its directories in base, its output in gateway.log.

    The dev stack's Vaultwarden is allowed as a server.
    """
    accounts_dir = base / "accounts"
    backups_root = base / "backups"
    port = free_port()
    env = {
        **os.environ,
        "BWTIDY_HOST": "127.0.0.1",
        "BWTIDY_PORT": str(port),
        "BWTIDY_ACCOUNTS_DIR": str(accounts_dir),
        "BWTIDY_BACKUPS_ROOT": str(backups_root),
        "BWTIDY_ALLOWED_SERVERS": SERVER,
        "BWTIDY_UID_BASE": str(UID_BASE),
        "BWTIDY_IDLE_EXIT": IDLE_EXIT,
    }
    entry = Path(sys.executable).parent / "bwtidy-gateway"
    assert entry.exists()
    url = f"http://127.0.0.1:{port}"
    with (base / "gateway.log").open("ab") as log:
        proc = subprocess.Popen(
            [str(entry)], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log
        )
    try:
        wait_for(lambda: health(url) or proc.poll() is not None, "the gateway's health")
        assert proc.poll() is None, "the gateway exited at its start"
        yield GatewayProcess(url, accounts_dir, backups_root, proc)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(TIMEOUT)


def short_base() -> Path:
    """Short paths (a unix socket path has a limit of about 100 bytes), and the workers' uids
    must be able to enter the parents of their directories, which pytest's tmp dirs forbid.
    """
    base = Path(tempfile.mkdtemp(prefix="gw.", dir="/tmp"))
    base.chmod(0o711)
    return base


def skip_unless_root() -> None:
    if os.geteuid() != 0:
        pytest.skip(
            "runs as root only: scripts/integration --root tests/integration/test_gateway.py"
        )


@pytest.fixture(scope="module")
def gateway(bw_cli: BwCli) -> Iterator[GatewayProcess]:
    """The real gateway, for the tests that log accounts in."""
    skip_unless_root()
    purge_tool_notes(bw_cli)
    base = short_base()
    try:
        with running_gateway(base) as gateway:
            yield gateway
    finally:
        shutil.rmtree(base, ignore_errors=True)
        purge_tool_notes(bw_cli)


def status_line(pid: int, field: str) -> list[str]:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith(f"{field}:"):
            return line.split(":", 1)[1].split()
    raise AssertionError(f"no {field} in the status of {pid}")


def worker_pids(gateway: GatewayProcess, email: str) -> list[int]:
    """The account's bw serve (its command line names the socket) and the worker, its parent.

    Root in the container has no CAP_SYS_PTRACE, so another uid's environ and cwd in /proc
    cannot be read, the command line and the status can.
    """
    wanted = f"unix://{gateway.directory(email) / 'run' / 'bw.sock'}".encode()
    pids = set()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmdline = (proc / "cmdline").read_bytes()
        except OSError:
            continue
        if wanted in cmdline:
            ## a bw serve that exits right after its command line was read has no status left
            try:
                parent = int(status_line(int(proc.name), "PPid")[0])
            except OSError:
                continue
            pids.update((int(proc.name), parent))
    return sorted(pids)


def test_two_accounts_log_in_through_the_gateway(gateway: GatewayProcess):
    for email in (EMAIL, RESTORE_EMAIL):
        browser = gateway.browser(email)
        missing = browser.open(create=False)
        assert missing.status_code == 404, missing.text
        assert missing.json() == {"exists": False}
        assert browser.cookies == {}

        created = browser.open(create=True)
        assert created.status_code == 200, created.text
        assert created.json() == {
            "account": {"server": SERVER, "email": email},
            "running": False,
            "exists": False,
            "pending": False,
            "loggedIn": False,
        }
        assert COOKIE in browser.cookies
        path = gateway.directory(email)
        ## nothing starts before the first Log in, it makes the directory
        assert not path.exists()
        assert worker_pids(gateway, email) == []
        assert browser.request("GET", "/api/session").status_code == 401

        assert browser.login(PASSWORD)["exitCode"] == 0
        assert (path / PENDING_MARKER).exists()
        finished = browser.finish()
        assert finished.status_code == 200, finished.text
        assert finished.json()["state"] == "unlocked"
        assert finished.json()["bwLogin"] == {
            "loggedIn": True,
            "server": SERVER,
            "email": email,
            "accountFixed": True,
        }
        assert SESSION_COOKIE in browser.cookies
        assert not (path / PENDING_MARKER).exists()
        assert browser.request("GET", "/api/session").json()["state"] == "unlocked"
        assert browser.account() == {
            "account": {"server": SERVER, "email": email},
            "running": True,
            "exists": True,
            "pending": False,
            "loggedIn": True,
        }


def test_each_account_has_its_own_uid_directory_and_processes(gateway: GatewayProcess):
    assert gateway.accounts_dir.stat().st_mode & 0o777 == 0o711
    assert gateway.backups_root.stat().st_mode & 0o777 == 0o711
    ## the background backup after the unlock, written by the worker as its own uid
    backups = gateway.backups(EMAIL)
    wait_for(lambda: any(backups.glob("*.zip.age")), "the background backup")
    for backup in backups.glob("*.zip.age"):
        assert backup.stat().st_uid == gateway.directory(EMAIL).stat().st_uid
    assert not (gateway.directory(EMAIL) / "app" / "backups").exists()
    uids = {}
    for email in (EMAIL, RESTORE_EMAIL):
        path = gateway.directory(email)
        uid = path.stat().st_uid
        assert uid > UID_BASE
        assert path.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_gid == uid
        for name in SUBDIRS:
            sub = path / name
            assert sub.stat().st_uid == uid
            assert sub.stat().st_mode & 0o777 == 0o700
        assert (path / "run" / "app.sock").is_socket()
        assert (path / "run" / "bw.sock").is_socket()
        assert (path / "app" / "bwtidy.sqlite").stat().st_uid == uid
        ## the worker's note for the gateway, as its own uid
        state_file = path / "app" / LOGIN_STATE_FILE
        assert state_file.stat().st_uid == uid
        assert state_file.stat().st_mode & 0o777 == 0o600
        assert state_file.read_bytes() == LOGGED_IN
        assert (path / "worker.log").stat().st_uid == uid
        backups = gateway.backups(email)
        assert backups.stat().st_uid == uid
        assert backups.stat().st_mode & 0o777 == 0o700
        assert backups.parent.stat().st_mode & 0o777 == 0o711

        pids = worker_pids(gateway, email)
        ## the worker and its bw serve
        assert len(pids) == 2, pids
        for pid in pids:
            assert status_line(pid, "Uid") == [str(uid)] * 4, f"pid {pid}"
            assert status_line(pid, "Gid") == [str(uid)] * 4, f"pid {pid}"
            assert status_line(pid, "Groups") == [], f"pid {pid}"
        uids[email] = uid
    assert len(set(uids.values())) == 2


## run as one account's uid against the other's directory: every access must be denied
PEEK = """
import json, os, socket, sys
other, own, other_backups = sys.argv[1], sys.argv[2], sys.argv[3]
results = {}

def attempt(name, action):
    try:
        action()
        results[name] = "allowed"
    except PermissionError:
        results[name] = "denied"

def connect(path):
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(path)

attempt("listdir", lambda: os.listdir(other))
attempt("sqlite", lambda: open(os.path.join(other, "app", "bwtidy.sqlite"), "rb"))
attempt("bw state", lambda: os.listdir(os.path.join(other, "bw")))
attempt("worker log", lambda: open(os.path.join(other, "worker.log"), "rb"))
attempt("bw.sock", lambda: connect(os.path.join(other, "run", "bw.sock")))
attempt("app.sock", lambda: connect(os.path.join(other, "run", "app.sock")))
attempt("backups", lambda: os.listdir(other_backups))
attempt("own bw.sock", lambda: connect(os.path.join(own, "run", "bw.sock")))
print(json.dumps(results))
"""


def test_one_account_cannot_reach_the_other(gateway: GatewayProcess):
    a, b = gateway.directory(EMAIL), gateway.directory(RESTORE_EMAIL)
    uid_a = a.stat().st_uid
    result = subprocess.run(
        [sys.executable, "-I", "-c", PEEK, str(b), str(a), str(gateway.backups(RESTORE_EMAIL))],
        user=uid_a,
        group=uid_a,
        extra_groups=[],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    results = json.loads(result.stdout)
    ## the probe can reach its own account, so a denial means isolation and not a broken probe
    assert results.pop("own bw.sock") == "allowed"
    assert results == dict.fromkeys(results, "denied"), results

    ## A's routing cookie with B's session cookie reaches A's worker, which knows no such session
    browser_a, browser_b = gateway.browser(EMAIL), gateway.browser(RESTORE_EMAIL)
    mixed = {COOKIE: browser_a.cookies[COOKIE], SESSION_COOKIE: browser_b.cookies[SESSION_COOKIE]}
    header = "; ".join(f"{k}={v}" for k, v in mixed.items())
    response = httpx.get(gateway.url + "/api/grid", headers={"Cookie": header}, timeout=TIMEOUT)
    assert response.status_code == 401
    ## each browser's own pair still works
    assert browser_a.request("GET", "/api/grid").status_code == 200
    assert browser_b.request("GET", "/api/grid").status_code == 200


def test_a_failed_login_leaves_no_directory_behind(gateway: GatewayProcess):
    browser = gateway.browser(NOBODY_EMAIL)
    assert browser.open(create=True).status_code == 200
    path = gateway.directory(NOBODY_EMAIL)
    assert not path.exists()
    assert browser.login("not-the-password")["exitCode"] != 0
    assert (path / PENDING_MARKER).exists()
    uid = path.stat().st_uid
    assert uid > UID_BASE
    assert (path / "app" / LOGIN_STATE_FILE).read_bytes() == LOGGED_OUT
    ## the worker exits after the idle time without a session, and its pending directory goes
    wait_for(lambda: not path.exists(), "the pending directory to go")
    wait_for(lambda: not gateway.backups(NOBODY_EMAIL).exists(), "its backups directory to go")
    wait_for(lambda: worker_pids(gateway, NOBODY_EMAIL) == [], "the worker's processes to go")
    assert browser.account() == {
        "account": {"server": SERVER, "email": NOBODY_EMAIL},
        "running": False,
        "exists": False,
        "pending": False,
        "loggedIn": False,
    }
    assert browser.open(create=False).status_code == 404
    ## the other accounts are untouched
    assert gateway.browser(EMAIL).request("GET", "/api/session").json()["state"] == "unlocked"


def list_items(url: str, token: str) -> dict[str, Any]:
    """list_items over streamable HTTP through the gateway, like an MCP client does."""

    async def run() -> dict[str, Any]:
        http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        transport = streamable_http_client(f"{url}/mcp", http_client=http)
        async with http, Client(transport) as client:
            result = await client.call_tool("list_items", {})
        texts = [c.text for c in result.content if isinstance(c, TextContent)]
        ## the tool's own error text names no value, so it is safe to show on a failure
        assert not result.is_error, texts
        return json.loads("".join(texts))

    return asyncio.run(run())


def item_ids(browser: Browser) -> set[str]:
    """The ids of the account's items as its grid shows them, the tool note left out."""
    grid = browser.request("GET", "/api/grid").json()
    return {row["id"] for row in grid["rows"] if row["id"] != grid["toolNoteId"]}


def test_each_mcp_token_reaches_only_its_own_account(gateway: GatewayProcess):
    tokens = {}
    for email in (EMAIL, RESTORE_EMAIL):
        browser = gateway.browser(email)
        created = browser.request("POST", "/api/mcp/token")
        assert created.status_code == 201, created.text
        tokens[email] = created.json()["token"]
        enabled = browser.request("POST", "/api/mcp", json={"enabled": True})
        assert enabled.json() == {"mcpEnabled": True}
    assert tokens[EMAIL] != tokens[RESTORE_EMAIL]

    seen = {}
    for email, token in tokens.items():
        assert gateway.browser(email).request("POST", "/api/session/heartbeat").status_code == 204
        answer = list_items(gateway.url, token)
        seen[email] = {item["id"] for item in answer["items"]}
        assert seen[email] == item_ids(gateway.browser(email)), email
    ## the dev vault has items and the restore account's is empty, so the two are told apart
    assert seen[EMAIL]
    assert not seen[EMAIL] & seen[RESTORE_EMAIL]

    unknown = httpx.post(
        gateway.url + "/mcp",
        headers={"Authorization": "Bearer not-a-token"},
        json={},
        timeout=TIMEOUT,
    )
    assert unknown.status_code == 401
    assert unknown.text == UNAUTHORIZED

    ## after its account logged out, a token finds no worker, the other account goes on
    browser = gateway.browser(RESTORE_EMAIL)
    assert browser.request("DELETE", "/api/session").status_code == 204
    wait_for(lambda: browser.account()["running"] is False, "the worker to exit")

    def mcp_answer() -> httpx.Response:
        return httpx.post(
            gateway.url + "/mcp",
            headers={"Authorization": f"Bearer {tokens[RESTORE_EMAIL]}"},
            json={},
            timeout=TIMEOUT,
        )

    ## running turns false once its health stops answering, the gateway forgets the worker only
    ## once its process exited: first the worker itself answers (400 to this empty body), then
    ## the proxy 502 once its socket is gone, then the gateway's 503
    wait_for(lambda: mcp_answer().status_code == 503, "the worker's process to go")
    locked = mcp_answer()
    assert locked.status_code == 503
    assert locked.text == LOCKED
    assert gateway.browser(EMAIL).request("POST", "/api/session/heartbeat").status_code == 204
    assert {item["id"] for item in list_items(gateway.url, tokens[EMAIL])["items"]} == seen[EMAIL]


def test_a_logout_ends_the_worker(gateway: GatewayProcess):
    browser = gateway.browser(EMAIL)
    path = gateway.directory(EMAIL)
    pids = worker_pids(gateway, EMAIL)
    assert pids
    response = browser.request("DELETE", "/api/session")
    assert response.status_code == 204, response.text
    wait_for(lambda: browser.account()["running"] is False, "the worker to exit")
    ## worker_pids finds the worker only through its bw serve, which the worker stops in its
    ## shutdown and then runs on for a moment, so wait until each process itself is reaped
    wait_for(
        lambda: not any(Path(f"/proc/{pid}").exists() for pid in pids),
        "the worker's processes to be reaped",
    )
    assert worker_pids(gateway, EMAIL) == []
    assert browser.request("GET", "/api/session").status_code == 401
    ## the account stays: its directory is past its first unlock
    assert path.is_dir()
    assert not (path / PENDING_MARKER).exists()
    assert not (path / "run" / "app.sock").exists()
    ## opening it again starts nothing, the gateway knows the CLI is still logged in
    again = browser.open(create=False)
    assert again.status_code == 200, again.text
    assert again.json()["running"] is False
    assert again.json()["loggedIn"] is True
    assert worker_pids(gateway, EMAIL) == []
    ## the unlock starts the next worker and unlocks there
    started = time.monotonic()
    unlocked = browser.request("POST", "/api/session", json={"password": PASSWORD})
    took = time.monotonic() - started
    assert unlocked.status_code == 200, unlocked.text
    assert unlocked.json()["state"] == "unlocked"
    assert len(worker_pids(gateway, EMAIL)) == 2
    print(f"the unlock with the worker's start took {took:.1f} s")
    assert took < 15, f"the unlock with the worker's start took {took:.1f} s"
    assert browser.request("DELETE", "/api/session").status_code == 204


def test_delete_my_account_removes_its_directories_as_root(gateway: GatewayProcess):
    """Delete my account (docs/spec.md, Deleting data): the worker writes the marker as its
    own uid and exits, the gateway as root removes the account directory and its directory
    under the backups root, backups in it, and leaves the other account alone."""
    browser = gateway.browser(RESTORE_EMAIL)
    path = gateway.directory(RESTORE_EMAIL)
    backups = gateway.backups(RESTORE_EMAIL)
    if browser.request("GET", "/api/session").json().get("state") != "unlocked":
        unlocked = browser.request("POST", "/api/session", json={"password": PASSWORD})
        assert unlocked.status_code == 200, unlocked.text
    ## the background backup after the unlock, so the backups directory is not empty
    wait_for(lambda: any(backups.glob("*.zip.age")), "the background backup")
    headers = {REQUEST_HEADER: "1"}
    deleted = browser.request(
        "POST", "/api/account/delete", json={"pending": "drop"}, headers=headers
    )
    assert deleted.status_code == 204, deleted.text
    wait_for(lambda: not path.exists(), "the account directory to go")
    wait_for(lambda: not backups.exists(), "its backups directory to go")
    wait_for(lambda: worker_pids(gateway, RESTORE_EMAIL) == [], "the worker's processes to go")
    assert browser.open(create=False).status_code == 404
    ## the other account is untouched
    assert gateway.directory(EMAIL).is_dir()
    assert any(gateway.backups(EMAIL).glob("*.zip.age"))


def uid_pids(uid: int) -> list[int]:
    """Every process that runs as the uid."""
    pids = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if status_line(int(proc.name), "Uid")[0] == str(uid):
                pids.append(int(proc.name))
        except (OSError, AssertionError):
            continue
    return sorted(pids)


def test_a_worker_the_gateway_has_to_kill_takes_its_bw_serve_with_it(bw_cli: BwCli):
    """The kill goes to the worker's process group (docs/spec.md, Gateway): no bw serve with the
    unlocked vault lives on as the account's uid.

    The worker is frozen with SIGSTOP, so its SIGTERM at the gateway's shutdown does nothing
    and the gateway reaches its kill deadline. A gateway of its own, since this one ends.
    """
    skip_unless_root()
    base = short_base()
    try:
        with running_gateway(base) as gateway:
            browser = gateway.browser(EMAIL)
            assert browser.open(create=True).status_code == 200
            assert browser.login(PASSWORD)["exitCode"] == 0
            finished = browser.finish()
            assert finished.status_code == 200, finished.text
            assert finished.json()["state"] == "unlocked"
            uid = gateway.directory(EMAIL).stat().st_uid
            pids = worker_pids(gateway, EMAIL)
            ## the worker (the gateway's child) and its bw serve
            assert len(pids) == 2, pids
            [worker] = [pid for pid in pids if status_line(pid, "PPid") == [str(gateway.proc.pid)]]
            assert uid_pids(uid) == pids
            os.kill(worker, signal.SIGSTOP)
            started = time.monotonic()
            gateway.proc.terminate()
            gateway.proc.wait(TIMEOUT + 60)
            took = time.monotonic() - started
            print(f"the gateway's stop with a frozen worker took {took:.1f} s")
            ## the kill came at the deadline, not before it
            assert took >= STOP_TIMEOUT, f"the gateway stopped after {took:.1f} s"
        ## the container's init reaps what the kill left
        wait_for(lambda: uid_pids(uid) == [], "the account's processes to go", timeout=10)
        assert worker_pids(gateway, EMAIL) == []
    finally:
        shutil.rmtree(base, ignore_errors=True)
        purge_tool_notes(bw_cli)
