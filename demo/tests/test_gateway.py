import asyncio
import contextlib
import json
import logging
import shutil
import sys
import tempfile
import time
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx2 as httpx
import pytest
from bwtidy.config import SettingsError
from bwtidy.gateway import workers as workers_module
from bwtidy.gateway.settings import GatewaySettings
from bwtidy.protocol import ACCOUNT_COOKIE, SESSION_COOKIE
from fastapi.testclient import TestClient

from bwtidy_demo import gateway as demo_gateway
from bwtidy_demo.gateway import NOTES, REFUSED, DemoSettings, create_demo_app, read_index

SERVER = "https://vaultwarden"
TOKEN = "controller-token-1"
LOGIN_ID = "login-1"
## the fake worker's bw login fails for this password
WRONG = "wrong-password"
INDEX = """<!doctype html>
<html>
<head><title>bwtidy</title></head>
<body>
<div id="root"></div>
</body>
</html>
"""
DOCS_PAGE = """<!doctype html>
<html><body><main>
<article class="md-content__inner md-typeset"><h1>Tutorial</h1></article>
</main></body></html>
"""
## the fake worker: its health, the bw-login sequence of dev/demo.md (the output answers
## running once, then exits with 1 for WRONG and 0 for any other password), and an echo of every
## other request. Every request but the health polls is appended to the file seen.
FAKE_WORKER = f"""#!{sys.executable}
import json, os, signal, sys
from http.server import BaseHTTPRequestHandler
from socketserver import UnixStreamServer

signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))
state = {{"password": None, "polls": 0}}


class Handler(BaseHTTPRequestHandler):
    wbufsize = -1

    def read(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length).decode()

    def handle_any(self):
        body = self.read() if self.command in ("POST", "PUT", "DELETE") else ""
        if self.path == "/api/health":
            return self.answer({{"status": "ok"}})
        with open("seen", "a") as file:
            file.write(json.dumps([self.command, self.path, body]) + "\\n")
        login = self.headers.get("X-Bwtidy-Login") == "{LOGIN_ID}"
        if self.command == "POST" and self.path == "/api/bw-login":
            state["password"] = json.loads(body)["password"]
            return self.answer({{"loginId": "{LOGIN_ID}"}})
        if self.path.startswith("/api/bw-login/output") and login:
            state["polls"] += 1
            if state["polls"] == 1:
                return self.answer({{"text": "Logging in", "next": 10, "running": True,
                                     "exitCode": None}})
            code = 1 if state["password"] == "{WRONG}" else 0
            return self.answer({{"text": "", "next": 10, "running": False, "exitCode": code}})
        if self.command == "POST" and self.path == "/api/bw-login/finish" and login:
            os.unlink(os.environ["BWTIDY_PENDING_MARKER"])
            cookie = "{SESSION_COOKIE}=session-" + str(os.getpid()) + "; HttpOnly; Path=/"
            return self.answer({{"state": "unlocked"}}, [("Set-Cookie", cookie)])
        self.answer({{"method": self.command, "path": self.path, "body": body,
                      "cookie": self.headers.get("Cookie")}})

    do_GET = do_POST = do_PUT = do_DELETE = handle_any

    def answer(self, data, extra=()):
        body = json.dumps(data).encode()
        self.send_response(200)
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


path = os.environ["BWTIDY_SOCKET"]
if os.path.exists(path):
    os.unlink(path)
UnixStreamServer(path, Handler).serve_forever()
"""


def email(word: str) -> str:
    return f"{word}-paper-cloud-river@demo.bwtidy.test"


class FakeController:
    """The controller's API: the vaults in ready are handed out in turn. The first
    fail_releases releases answer 502."""

    def __init__(self, ready: list[str] | None = None) -> None:
        self.ready = list(ready if ready is not None else [email(w) for w in WORDS])
        self.passwords: dict[str, str] = {}
        self.taken: list[str] = []
        self.release_calls: list[str] = []
        self.released: list[str] = []
        self.fail_releases = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if request.method == "POST" and request.url.path == "/take":
            if not self.ready:
                return httpx.Response(409, text="No demo vault is ready.")
            taken = self.ready.pop(0)
            self.taken.append(taken)
            password = self.passwords.get(taken, f"password-of-{taken.split('@')[0]}")
            return httpx.Response(
                200, json={"server": SERVER, "email": taken, "password": password}
            )
        if request.method == "POST" and request.url.path == "/release":
            released = json.loads(request.content)["email"]
            self.release_calls.append(released)
            if self.fail_releases:
                self.fail_releases -= 1
                return httpx.Response(502, text="Vaultwarden did not delete the user.")
            self.released.append(released)
            return httpx.Response(204)
        return httpx.Response(404)


WORDS = ["amber", "birch", "cedar", "dune", "elm"]


class Env:
    def __init__(self, client: TestClient, controller: FakeController, root: Path) -> None:
        self.client = client
        self.app: Any = client.app
        self.controller = controller
        self.root = root

    @property
    def demo(self):
        return self.app.state.demo

    @property
    def gateway(self):
        return self.app.state.gateway

    def request(
        self, method: str, path: str, cookies: dict[str, str] | None = None, **kwargs: Any
    ) -> httpx.Response:
        """A request with exactly these cookies: the client's jar never carries one over."""
        headers = dict(kwargs.pop("headers", {}))
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        response = self.client.request(
            method, path, headers=headers, follow_redirects=False, **kwargs
        )
        self.client.cookies.clear()
        return response

    def start(self) -> dict[str, str]:
        """Start a demo, answer its two cookies."""
        response = self.request("POST", "/demo/start")
        assert response.status_code == 303, response.text
        assert response.headers["location"] == "/"
        return {
            ACCOUNT_COOKIE: response.cookies[ACCOUNT_COOKIE],
            SESSION_COOKIE: response.cookies[SESSION_COOKIE],
        }

    def directory(self, address: str) -> Path:
        return self.root / "a" / "vaultwarden" / address

    def seen(self, address: str) -> list[list[str]]:
        path = self.directory(address) / "seen"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def running(self) -> list[Any]:
        return [w for w in self.gateway.workers._workers.values() if w.running]

    def call(self, function, *args: Any) -> Any:
        """Run an async function of the app in its event loop."""
        assert self.client.portal is not None
        return self.client.portal.call(function, *args)


def wait_for(done, what: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not done():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.02)


def set_cookies(response: httpx.Response) -> dict[str, str]:
    """Every Set-Cookie of the answer by its cookie's name, lowercased."""
    found = {}
    for header in response.headers.get_list("set-cookie"):
        found[header.split("=", 1)[0]] = header.lower()
    return found


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workers_module, "HEALTH_POLL", 0.01)
    monkeypatch.setattr(demo_gateway, "LOGIN_POLL", 0.01)
    monkeypatch.setattr(demo_gateway, "RELEASE_WAIT", 0.0)


def run_root(tmp_path: Path) -> Path:
    """The workers' sockets of the test whose tmp_path this is, in /tmp and never under
    tmp_path: TMPDIR can be deep, and a Unix socket path holds at most 107 bytes."""
    return Path("/tmp") / f"bwtidy-demo-run-{tmp_path.name}"


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="dg"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        shutil.rmtree(run_root(path), ignore_errors=True)


def gateway_settings(tmp_path: Path, **extra: Any) -> GatewaySettings:
    worker = tmp_path / "worker"
    worker.write_text(FAKE_WORKER)
    worker.chmod(0o755)
    static = tmp_path / "static"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text(INDEX)
    docs = tmp_path / "docs"
    (docs / "tutorial" / "mcp").mkdir(parents=True, exist_ok=True)
    (docs / "tutorial" / "first-cleanup").mkdir(parents=True, exist_ok=True)
    for name in ("tutorial/mcp", "tutorial/first-cleanup"):
        (docs / name / "index.html").write_text(DOCS_PAGE)
    (docs / "style.css").write_text("body { color: red }")
    options: dict[str, Any] = {
        "docs_dir": docs,
        "accounts_dir": tmp_path / "a",
        "allowed_servers": frozenset({SERVER}),
        "switch_uid": False,
        "worker_bin": worker,
        "start_timeout": 15.0,
        "static_dir": static,
        "run_dir": run_root(tmp_path),
        **extra,
    }
    return GatewaySettings(**options)


def demo_settings(**extra: Any) -> DemoSettings:
    return DemoSettings(controller_url="http://controller", controller_token=TOKEN, **extra)


@contextlib.contextmanager
def open_env(
    tmp_path: Path,
    controller: FakeController | None = None,
    demo: dict[str, Any] | None = None,
    **extra: Any,
) -> Generator[Env]:
    controller = controller or FakeController()
    app = create_demo_app(
        gateway_settings(tmp_path, **extra),
        demo_settings(**(demo or {})),
        transport=httpx.MockTransport(controller.handle),
    )
    with TestClient(app) as client:
        yield Env(client, controller, tmp_path)


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    with open_env(tmp_path) as opened:
        yield opened


def raw(env: Env, method: str, raw_path: str, cookies: dict[str, str], body: bytes = b"{}"):
    """A request with this raw path as it is: an HTTP client would resolve its dot segments."""

    async def run() -> tuple[int, bytes]:
        path, _, query = raw_path.partition("?")
        cookie = "; ".join(f"{k}={v}" for k, v in cookies.items())
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": unquote(path),
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": [
                (b"host", b"testserver"),
                (b"cookie", cookie.encode()),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        sent = False
        messages: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await env.app(scope, receive, send)
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        bodies = [m.get("body", b"") for m in messages if m["type"] == "http.response.body"]
        return status, b"".join(bodies)

    return env.call(run)


## every route of dev/demo.md, Refused routes, and the bw-login routes the browser never needs
REFUSED_ROUTES = [
    ("POST", "/gateway/account"),
    ("POST", "/api/bw-login"),
    ("POST", "/api/bw-login/input"),
    ("POST", "/api/bw-login/finish"),
    ("GET", "/api/bw-login/output"),
    ("DELETE", "/api/bw-login"),
    ("POST", "/api/session"),
    ("POST", "/api/bw-logout"),
    ("POST", "/api/account/delete"),
]
## the same routes in the spellings that reach a worker's route all the same
REFUSED_SPELLINGS = [
    ("POST", "/api/./bw-login"),
    ("POST", "/api/x/../bw-login"),
    ("POST", "/api//bw-login"),
    ("POST", "/api/bw-login/"),
    ("POST", "/api/bw%2Dlogin"),
    ("POST", "/api/bw-login%2Ffinish"),
    ("POST", "/api/./session"),
    ("POST", "/api/session/"),
    ("POST", "/api/./bw-logout"),
    ("POST", "/api/account/./delete"),
    ("POST", "/gateway/./account"),
    ("POST", "/gateway/account/"),
]
LOGIN_SEQUENCE = [
    ["POST", "/api/bw-login"],
    ["GET", "/api/bw-login/output?after=0"],
    ["GET", "/api/bw-login/output?after=10"],
    ["POST", "/api/bw-login/finish"],
]


def test_every_refused_route_answers_403_and_never_reaches_the_worker(env: Env):
    cookies = env.start()
    address = env.controller.taken[0]
    before = env.seen(address)
    body = {"server": SERVER, "email": "someone@example.test", "password": "a real one"}
    for method, path in REFUSED_ROUTES:
        response = env.request(method, path, cookies, json=body)
        assert response.status_code == 403, (method, path)
        assert response.text == REFUSED
    for method, path in REFUSED_SPELLINGS:
        status, content = raw(env, method, path, cookies, json.dumps(body).encode())
        assert status == 403, (method, path)
        assert content.decode() == REFUSED
    assert env.seen(address) == before


def test_the_routes_the_app_needs_still_reach_the_worker(env: Env):
    cookies = env.start()
    address = env.controller.taken[0]
    for method, path in [
        ("GET", "/api/session"),
        ("POST", "/api/session/heartbeat"),
        ("DELETE", "/api/session"),
        ("POST", "/api/account/delete-data"),
    ]:
        response = env.request(method, path, cookies)
        assert response.status_code == 200, (method, path)
        assert response.json()["path"] == path
    assert [line[:2] for line in env.seen(address)][len(LOGIN_SEQUENCE) :] == [
        ["GET", "/api/session"],
        ["POST", "/api/session/heartbeat"],
        ["DELETE", "/api/session"],
        ["POST", "/api/account/delete-data"],
    ]


@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_the_app_without_a_live_demo_goes_to_the_start_page(env: Env, path: str):
    for cookies in [None, {ACCOUNT_COOKIE: "an-old-one"}]:
        response = env.request("GET", path, cookies)
        assert response.status_code == 303
        assert response.headers["location"] == "/demo"


def test_the_start_page_and_the_index_ask_robots_to_stay_away(env: Env):
    meta = '<meta name="robots" content="noindex, nofollow" />'
    assert meta in env.request("GET", "/demo").text
    page = env.request("GET", "/", env.start()).text
    assert page.index("<head>") < page.index(meta) < page.index("</head>")


def test_the_start_page_has_one_button_and_no_field(env: Env):
    response = env.request("GET", "/demo")
    assert response.status_code == 200
    page = response.text
    assert '<form method="post" action="/demo/start">' in page
    assert "Start demo" in page
    assert "<input" not in page
    assert "deleted when the session ends" in page
    assert "Never enter your own details" in page
    assert "after 45 minutes at most" in page
    assert all(note not in page for note in NOTES.values())


def test_a_note_comes_from_its_fixed_text_only(env: Env):
    assert NOTES["free"] in env.request("GET", "/demo?note=free").text
    page = env.request("GET", "/demo?note=%3Cscript%3E").text
    assert "<script>" not in page
    assert 'class="note"' not in page


def test_start_logs_the_vault_in_server_side_and_hands_out_both_cookies(
    env: Env, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO)
    response = env.request("POST", "/demo/start")
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    address = env.controller.taken[0]
    password = f"password-of-{address.split('@')[0]}"
    cookies = set_cookies(response)
    assert set(cookies) == {ACCOUNT_COOKIE, SESSION_COOKIE}
    for header in cookies.values():
        assert "httponly" in header
        assert "samesite=strict" in header
        assert "path=/" in header
    seen = env.seen(address)
    assert [line[:2] for line in seen] == LOGIN_SEQUENCE
    assert json.loads(seen[0][2]) == {"server": SERVER, "email": address, "password": password}
    assert not (env.directory(address) / ".pending").exists()
    assert password not in response.text
    assert all(password not in value for value in response.headers.values())
    assert password not in caplog.text
    session = env.request(
        "GET",
        "/api/session",
        {
            ACCOUNT_COOKIE: response.cookies[ACCOUNT_COOKIE],
            SESSION_COOKIE: response.cookies[SESSION_COOKIE],
        },
    )
    assert session.json()["cookie"] == f"{SESSION_COOKIE}={response.cookies[SESSION_COOKIE]}"


def test_a_start_without_a_ready_vault_shows_the_busy_note(tmp_path: Path):
    with open_env(tmp_path, FakeController(ready=[])) as env:
        response = env.request("POST", "/demo/start")
        assert response.status_code == 303
        assert response.headers["location"] == "/demo?note=free"
        assert NOTES["free"] in env.request("GET", response.headers["location"]).text
        assert ACCOUNT_COOKIE not in response.cookies


def test_a_start_over_the_worker_cap_shows_the_full_note_and_releases_its_vault(tmp_path: Path):
    with open_env(tmp_path, max_workers=1) as env:
        env.start()
        response = env.request("POST", "/demo/start")
        assert response.headers["location"] == "/demo?note=full"
        second = env.controller.taken[1]
        assert env.controller.released == [second]
        assert not env.directory(second).exists()
        assert len(env.running()) == 1


def test_a_failed_login_leaves_nothing_behind(tmp_path: Path):
    controller = FakeController()
    controller.passwords[controller.ready[0]] = WRONG
    with open_env(tmp_path, controller) as env:
        response = env.request("POST", "/demo/start")
        assert response.headers["location"] == "/demo?note=failed"
        address = controller.taken[0]
        assert controller.released == [address]
        assert not env.directory(address).exists()
        assert env.running() == []


def test_a_cross_site_start_takes_no_vault(env: Env):
    response = env.request("POST", "/demo/start", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert env.controller.taken == []


def test_a_second_start_ends_the_first_demo(env: Env):
    first = env.start()
    response = env.request("POST", "/demo/start", first)
    assert response.status_code == 303
    assert env.controller.released == [env.controller.taken[0]]
    assert not env.directory(env.controller.taken[0]).exists()
    assert len(env.running()) == 1


def test_end_demo_deletes_the_account_and_releases_it_once(env: Env):
    cookies = env.start()
    address = env.controller.taken[0]
    response = env.request("POST", "/demo/end", cookies)
    assert response.status_code == 303
    assert response.headers["location"] == "/demo"
    for header in set_cookies(response).values():
        assert "max-age=0" in header
    assert not env.directory(address).exists()
    assert env.controller.release_calls == [address]
    assert env.running() == []
    assert env.request("GET", "/", cookies).headers["location"] == "/demo"


def test_switch_account_ends_the_demo(env: Env):
    cookies = env.start()
    address = env.controller.taken[0]
    response = env.request("DELETE", "/gateway/account", cookies)
    assert response.status_code == 204
    assert "max-age=0" in set_cookies(response)[ACCOUNT_COOKIE]
    assert not env.directory(address).exists()
    assert env.controller.release_calls == [address]


def test_the_end_of_the_session_deletes_the_account(env: Env):
    env.start()
    address = env.controller.taken[0]
    worker = env.running()[0]
    worker.proc.terminate()
    wait_for(lambda: env.controller.released == [address], "the release")
    assert not env.directory(address).exists()


def test_a_release_vaultwarden_refused_is_tried_again(env: Env):
    env.controller.fail_releases = 2
    cookies = env.start()
    address = env.controller.taken[0]
    env.request("POST", "/demo/end", cookies)
    assert env.controller.release_calls == [address] * 3
    assert env.controller.released == [address]


def test_the_hard_maximum_ends_a_demo_and_cleanup_runs_once(tmp_path: Path):
    with open_env(tmp_path, demo={"session_max": 0.3, "max_poll": 0.05}) as env:
        cookies = env.start()
        address = env.controller.taken[0]
        worker = env.running()[0]
        ## the session ends on its own just as the hard maximum stops the worker
        time.sleep(0.25)
        worker.proc.terminate()
        wait_for(lambda: env.controller.released == [address], "the release")
        wait_for(lambda: env.running() == [], "the worker's exit")
        env.call(env.demo.cleanup, worker)
        env.call(env.demo.end_account, worker.key)
        assert env.controller.release_calls == [address]
        assert not env.directory(address).exists()
        assert env.request("GET", "/", cookies).headers["location"] == "/demo"


def test_the_hard_maximum_stops_an_active_demo(tmp_path: Path):
    with open_env(tmp_path, demo={"session_max": 0.3, "max_poll": 0.05}) as env:
        cookies = env.start()
        address = env.controller.taken[0]
        while env.controller.released == []:
            env.request("POST", "/api/session/heartbeat", cookies)
            time.sleep(0.05)
        assert env.controller.release_calls == [address]
        wait_for(lambda: not env.directory(address).exists(), "the deletion")


def test_the_start_deletes_every_account_left_from_before(tmp_path: Path):
    old = email("old")
    stranger = "someone@example.test"
    for address in (old, stranger):
        for sub in ("app", "bw", "run", "home"):
            (tmp_path / "a" / "vaultwarden" / address / sub).mkdir(parents=True)
    unfinished = tmp_path / "a" / "vaultwarden" / f".new-{email('new')}"
    unfinished.mkdir()
    orphan = tmp_path / "b" / "vaultwarden" / email("orphan")
    orphan.mkdir(parents=True)
    (orphan / "2026-11-20T10-00-00Z.json.gpg").write_text("x")
    with open_env(tmp_path, backups_root=tmp_path / "b") as env:
        assert not (tmp_path / "a" / "vaultwarden" / old).exists()
        assert not (tmp_path / "a" / "vaultwarden" / stranger).exists()
        assert not unfinished.exists()
        assert not orphan.exists()
        assert sorted(env.controller.released) == sorted([email("new"), old, email("orphan")])


def test_the_backups_go_with_the_account(tmp_path: Path):
    with open_env(tmp_path, backups_root=tmp_path / "b") as env:
        cookies = env.start()
        address = env.controller.taken[0]
        backups = tmp_path / "b" / "vaultwarden" / address
        assert backups.is_dir()
        (backups / "2026-11-26T10-00-00Z.json.gpg").write_text("x")
        env.request("POST", "/demo/end", cookies)
        assert not backups.exists()
        assert not env.directory(address).exists()


@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_the_app_carries_the_banner(env: Env, path: str):
    cookies = env.start()
    response = env.request("GET", path, cookies)
    assert response.status_code == 200
    page = response.text
    banner = page.index('id="demo-banner"')
    assert page.index("<body>") < banner < page.index('<div id="root">')
    assert "your vault is deleted when the session ends" in page
    assert '<form method="post" action="/demo/end">' in page
    assert "height: calc(100vh - var(--demo-banner)) !important" in page


@pytest.mark.parametrize(
    ("sent", "passed"),
    [
        ({"days": 90}, {"days": 1}),
        ({"days": None}, {"days": 1}),
        ({}, {"days": 1}),
        (None, {"days": 1}),
        ({"days": 1}, {"days": 1}),
        ({"days": 0}, {"days": 0}),
        ({"days": "7"}, {"days": "7"}),
    ],
)
def test_an_mcp_token_lives_one_day_at_most(env: Env, sent: Any, passed: Any):
    cookies = env.start()
    kwargs = {} if sent is None else {"json": sent}
    response = env.request("POST", "/api/mcp/token", cookies, **kwargs)
    assert json.loads(response.json()["body"]) == passed


def test_the_mcp_token_cap_holds_for_every_spelling_and_a_large_body(env: Env):
    cookies = env.start()
    status, content = raw(env, "POST", "/api/mcp/./token", cookies, b'{"days": 365}')
    assert status == 200
    assert json.loads(json.loads(content)["body"]) == {"days": 1}
    status, content = raw(env, "POST", "/api/mcp/token/", cookies, b'{"days": null}')
    assert json.loads(json.loads(content)["body"]) == {"days": 1}
    address = env.controller.taken[0]
    before = env.seen(address)
    large = json.dumps({"days": 1, "pad": "x" * 10000}).encode()
    status, _ = raw(env, "POST", "/api/mcp/token", cookies, large)
    assert status == 413
    assert env.seen(address) == before


def test_the_settings_the_demo_depends_on(tmp_path: Path):
    settings = demo_settings()
    good = gateway_settings(tmp_path)
    assert "<body>" in read_index(good, settings)
    bad = [
        gateway_settings(tmp_path, allowed_servers=frozenset()),
        gateway_settings(tmp_path, allowed_servers=frozenset({SERVER, "https://vault.example"})),
        gateway_settings(tmp_path, static_dir=None),
        gateway_settings(tmp_path, static_dir=tmp_path / "missing"),
    ]
    for gateway in bad:
        with pytest.raises(SettingsError):
            read_index(gateway, settings)
    (tmp_path / "static" / "index.html").write_text("<html></html>")
    with pytest.raises(SettingsError, match="no <body>"):
        read_index(good, settings)


def test_an_index_without_a_head_is_refused(tmp_path: Path):
    settings = demo_settings()
    good = gateway_settings(tmp_path)
    (tmp_path / "static" / "index.html").write_text("<html><body></body></html>")
    with pytest.raises(SettingsError, match="no <head>"):
        read_index(good, settings)


def test_the_demo_settings_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("DEMO_CONTROLLER_URL", "DEMO_SESSION_MAX", "DEMO_SERVER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEMO_SECRETS", str(tmp_path))
    with pytest.raises(SettingsError, match="DEMO_CONTROLLER_URL"):
        DemoSettings.from_env()
    monkeypatch.setenv("DEMO_CONTROLLER_URL", "http://controller:8100/")
    with pytest.raises(SettingsError, match="controller-token"):
        DemoSettings.from_env()
    (tmp_path / "controller-token").write_text(TOKEN + "\n")
    settings = DemoSettings.from_env()
    assert settings.controller_url == "http://controller:8100"
    assert settings.controller_token == TOKEN
    assert settings.server == SERVER
    assert settings.session_max == 2700
    assert TOKEN not in repr(settings)
    monkeypatch.setenv("DEMO_SESSION_MAX", "nope")
    with pytest.raises(SettingsError, match="DEMO_SESSION_MAX"):
        DemoSettings.from_env()


def test_a_docs_page_has_the_demo_note_at_the_top_of_its_content(env: Env):
    response = env.request("GET", "/docs/tutorial/first-cleanup/")
    assert response.status_code == 200
    page = response.text
    assert "data-demo-note" in page
    assert page.index("data-demo-note") < page.index("<h1>")
    assert page.index('md-content__inner md-typeset">') < page.index("data-demo-note")
    assert "deleted when the session ends (after 45 minutes at most, or " in page
    assert 'href="/demo"' in page
    assert "/mcp" not in page
    assert response.headers["content-length"] == str(len(response.content))
    assert response.headers["cache-control"] == "no-store"


def test_a_conditional_request_for_a_docs_page_still_gets_the_note(env: Env):
    first = env.request("GET", "/docs/tutorial/first-cleanup/")
    again = env.request(
        "GET", "/docs/tutorial/first-cleanup/", headers={"If-None-Match": "x", "Range": "bytes=0-5"}
    )
    assert first.status_code == again.status_code == 200
    assert "data-demo-note" in again.text


def test_the_mcp_page_names_the_plain_address_from_the_request_host(env: Env):
    page = env.request("GET", "/docs/tutorial/mcp/", headers={"Host": "localhost:8770"}).text
    assert "<code>http://localhost:8770/mcp</code>" in page
    assert "A token lives at most 1 day." in page


def test_the_mcp_page_names_the_public_host_when_cookies_are_secure(tmp_path: Path):
    with open_env(tmp_path, demo={"public_host": "demo.example.org"}, secure_cookies=True) as env:
        page = env.request("GET", "/docs/tutorial/mcp/", headers={"Host": "other"}).text
        assert "<code>https://demo.example.org/mcp</code>" in page
        assert "other" not in page
        assert "/mcp" not in env.request("GET", "/docs/tutorial/first-cleanup/").text


def test_a_host_in_the_mcp_address_is_escaped(env: Env):
    page = env.request("GET", "/docs/tutorial/mcp/", headers={"Host": "a<b>"}).text
    assert "a<b>" not in page


def test_a_page_outside_the_docs_and_a_docs_asset_are_untouched(env: Env):
    assert env.request("GET", "/docs/style.css").text == "body { color: red }"
    assert "data-demo-note" not in env.request("GET", "/demo").text
    assert "data-demo-note" not in env.request("GET", "/docs/../index.html").text
