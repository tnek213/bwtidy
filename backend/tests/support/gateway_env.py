"""The test environment of the gateway: the settings, the fake workers, the app and the helpers
the tests of its modules share."""

import sys
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bwtidy.gateway.accounts import AccountKey
from bwtidy.gateway.app import create_gateway_app
from bwtidy.gateway.settings import GatewaySettings
from bwtidy.mcp.auth import McpToken
from bwtidy.mcp_token import UNAUTHORIZED, utc_now
from bwtidy.protocol import ACCOUNT_COOKIE as COOKIE
from bwtidy.protocol import SUBDIRS

ALLOWED = "https://vaultwarden"
EXITING = "exiting"
## the fake worker's one line on its stdout, so worker.log shows it started
STARTED = "fake worker started"
## the fake worker appends every request but its health polls to this file in its directory
SEEN = "seen"
EMAIL = "user@example.test"
## the clicks that start a worker: the start of a Bitwarden login and the unlock
LOGIN = "/api/bw-login"
## serves /api/health and echoes what it gets on /api/session, exits on SIGTERM. With a file
## EXITING in its directory its health answers 503 once (the file goes) and it exits a tenth of
## a second later. Every other request is written to the file SEEN.
FAKE_WORKER = f"""#!{sys.executable}
import json, os, signal, sys, threading
from http.server import BaseHTTPRequestHandler
from socketserver import UnixStreamServer

path = os.environ["BWTIDY_SOCKET"]
signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))


class Handler(BaseHTTPRequestHandler):
    ## buffered, so an answer's head and body leave in one write: the gateway's health check
    ## hangs up once it has the head, and a body written after that fails with a broken pipe
    ## whose traceback lands in worker.log (uvicorn, the real worker, drops it silently)
    wbufsize = -1

    def seen(self):
        if self.path != "/api/health":
            with open("{SEEN}", "a") as file:
                file.write(self.command + " " + self.path + "\\n")

    def do_GET(self):
        self.seen()
        if self.path == "/api/health" and os.path.exists("{EXITING}"):
            os.unlink("{EXITING}")
            threading.Timer(0.1, os._exit, [0]).start()
            self.answer({{"status": "exiting"}}, status=503)
        elif self.path == "/api/health":
            self.answer({{"status": "ok", "version": "fake"}})
        elif self.path.startswith("/api/session"):
            self.answer(
                {{
                    "pid": os.getpid(),
                    "cookie": self.headers.get("Cookie"),
                    "dropped": self.headers.get("X-Drop"),
                    "agent": self.headers.get("User-Agent"),
                    "path": self.path,
                    "env": dict(os.environ),
                }},
                extra=[("Set-Cookie", "bwtidy_session=fake; Path=/"), ("X-Worker", "1")],
            )
        elif self.path == "/api/cookies":
            cookies = [
                ("Set-Cookie", "bwtidy_account=x; Path=/"),
                ("Set-Cookie", "bwtidy_session=y; Path=/"),
            ]
            self.answer({{}}, extra=cookies)
        else:
            self.answer({{"detail": "Not Found"}}, status=404)

    def do_POST(self):
        self.seen()
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode()
        self.answer(
            {{
                "pid": os.getpid(),
                "body": body,
                "path": self.path,
                "email": os.environ["BWTIDY_ACCOUNT_EMAIL"],
                "authorization": self.headers.get("Authorization"),
                "cookie": self.headers.get("Cookie"),
                "host": self.headers.get("Host"),
            }}
        )

    def answer(self, data, status=200, extra=()):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def address_string(self):
        return "unix"

    def log_message(self, *args):
        pass


if os.path.exists(path):
    os.unlink(path)
print("{STARTED}", flush=True)
UnixStreamServer(path, Handler).serve_forever()
"""


def write_script(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


class GatewayEnv:
    def __init__(self, client: TestClient, app: Any, root: Path) -> None:
        self.client = client
        self.app = app
        self.root = root

    @property
    def gateway(self):
        return self.app.state.gateway

    def request(self, method: str, path: str, cookies: dict[str, str], **kwargs: Any) -> Any:
        """A request with exactly these cookies: the client's jar never carries one over."""
        headers = dict(kwargs.pop("headers", {}))
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        response = self.client.request(method, path, headers=headers, **kwargs)
        self.client.cookies.clear()
        return response

    def open(
        self,
        server: str = ALLOWED,
        email: str = EMAIL,
        create: bool = True,
        cookies: dict[str, str] | None = None,
    ):
        body = {"server": server, "email": email, "create": create}
        return self.request("POST", "/gateway/account", cookies or {}, json=body)

    def cookie(self, response) -> str:
        return response.cookies[COOKIE]

    def click(self, token: str, path: str = LOGIN) -> Any:
        """A click that starts the account's worker when none runs: Log in, or Unlock."""
        return self.request("POST", path, {COOKIE: token}, json={})

    def started(
        self,
        server: str = ALLOWED,
        email: str = EMAIL,
        create: bool = True,
        cookies: dict[str, str] | None = None,
    ) -> str:
        """Open the account and press Log in, so its worker runs. Answers the routing cookie."""
        opened = self.open(server, email, create, cookies)
        assert opened.status_code == 200, opened.text
        token = self.cookie(opened)
        clicked = self.click(token)
        assert clicked.status_code == 200, clicked.text
        return token

    def session(self, token: str | None) -> Any:
        return self.request("GET", "/api/session", {COOKIE: token} if token else {})

    def account(self, token: str | None) -> Any:
        return self.request("GET", "/gateway/account", {COOKIE: token} if token else {}).json()

    def directory(self, host_dir: str = "vaultwarden", email: str = EMAIL) -> Path:
        return self.root / host_dir / email

    def directory_gone(self) -> bool:
        return not self.directory().exists()

    def worker_pids(self) -> list[int]:
        return [w.pid for w in self.gateway.workers._workers.values()]

    def seen(self, host_dir: str = "vaultwarden") -> list[str]:
        """The requests the account's fake worker got, its health polls aside."""
        path = self.directory(host_dir) / SEEN
        return path.read_text().splitlines() if path.exists() else []


def settings(tmp_path: Path, **extra: Any) -> GatewaySettings:
    worker = write_script(tmp_path / "worker", FAKE_WORKER)
    options: dict[str, Any] = {
        "accounts_dir": tmp_path / "a",
        "allowed_servers": frozenset({ALLOWED}),
        "switch_uid": False,
        "worker_bin": worker,
        "start_timeout": 15.0,
        **extra,
    }
    return GatewaySettings(**options)


def make_env(tmp_path: Path, **extra: Any) -> Iterator[GatewayEnv]:
    app = create_gateway_app(settings(tmp_path, **extra))
    with TestClient(app) as client:
        yield GatewayEnv(client, app, tmp_path / "a")


PUBLIC = "https://vault.example.test"


UNLOCK = "/api/session"


## a path the keep-alive fake worker never answers
DROP = "/api/drop"


## what the fake resolver answers, a missing host does not resolve
## public addresses come from ranges no company owns that ipaddress counts as global:
## 192.88.99.0/24 (the retired 6to4 relay anycast block, RFC 7526) and 2001:20::/28 (ORCHIDv2,
## RFC 7343), the documentation ranges are not global
ADDRESSES = {
    "vault.example.test": ["192.88.99.10", "2001:20::10"],
    "mixed.example.test": ["192.88.99.11", "192.168.1.11"],
    "private.example.test": ["10.0.0.1"],
    "localhost": ["127.0.0.1"],
    "10.0.0.1": ["10.0.0.1"],
    "nat64.example.test": ["64:ff9b::a00:1"],
    "nat64-local.example.test": ["64:ff9b:1::1"],
    "compat.example.test": ["::10.0.0.1"],
    "compat-loopback.example.test": ["::127.0.0.1"],
    "translated.example.test": ["::ffff:0:a00:1"],
    "mapped.example.test": ["::ffff:10.0.0.1"],
    "sixtofour.example.test": ["2002:a00:1::"],
    "teredo.example.test": ["2001::1"],
    "unspecified.example.test": ["0.0.0.0"],
    "multicast.example.test": ["224.0.0.1"],
    "multicast6.example.test": ["ff02::1"],
    "mapped-public.example.test": ["::ffff:192.88.99.10"],
}


## keeps a connection open after an answer (HTTP/1.1) but closes it unanswered when another
## request comes on it: uvicorn does that when its keep-alive timeout ends just as the request
## arrives. DROP is never answered. Every request but its health polls is written to SEEN.
KEEP_ALIVE_WORKER = f"""#!{sys.executable}
import json, os, signal, sys
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingUnixStreamServer

path = os.environ["BWTIDY_SOCKET"]
signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.answered = False

    def do_GET(self):
        if self.answered or self.path == "{DROP}":
            self.close_connection = True
            return
        if self.path != "/api/health":
            with open("{SEEN}", "a") as file:
                file.write(self.command + " " + self.path + "\\n")
        body = json.dumps({{"status": "ok"}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.answered = True

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.do_GET()

    def address_string(self):
        return "unix"

    def log_message(self, *args):
        pass


if os.path.exists(path):
    os.unlink(path)
ThreadingUnixStreamServer.daemon_threads = True
ThreadingUnixStreamServer(path, Handler).serve_forever()
"""


def wait_for(done, what: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not done():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.05)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def mcp_token(
    env: GatewayEnv, email: str = EMAIL, days: int | None = 90, made: timedelta = timedelta()
) -> str:
    """A token for the account, made the way its worker makes one, `made` ago."""
    token = McpToken(env.directory(email=email) / "app", now=lambda: utc_now() - made)
    return token.create(days)[0]


def assert_unauthorized(response: Any) -> None:
    assert response.status_code == 401
    assert response.text == UNAUTHORIZED
    assert response.headers["www-authenticate"] == "Bearer"


def account(email: str) -> AccountKey:
    return AccountKey(server=ALLOWED, email=email, host_dir="vaultwarden")


def backups_env(tmp_path: Path, **extra: Any) -> Iterator[GatewayEnv]:
    yield from make_env(tmp_path, backups_root=tmp_path / "b", **extra)


def backups_of(tmp_path: Path, email: str = EMAIL) -> Path:
    return tmp_path / "b" / "vaultwarden" / email


def existing_account(tmp_path: Path, backups: dict[str, bytes] | None = None) -> Path:
    """An account directory past its first unlock, with these backups in app/backups."""
    path = tmp_path / "a" / "vaultwarden" / EMAIL
    for name in SUBDIRS:
        (path / name).mkdir(parents=True)
    if backups is not None:
        (path / "app" / "backups").mkdir()
        for name, data in backups.items():
            (path / "app" / "backups" / name).write_bytes(data)
    return path
