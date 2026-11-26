"""The demo stack end to end (compose.demo.yaml), through its edge: scripts/demo-stack test.

The safety guarantee of the demo is tested here on the real containers: a visitor gets a vault
without typing anything, every route that would take a server, an email or a password answers
403, and neither the demo nor the controller (nor Vaultwarden) can open a connection out of
the stack. Then that the end of a demo deletes the account directory and the Vaultwarden user,
that the pool refills, and that the edge and DEMO_MAX limit the starts.

The tests run in the order of the file and share the stack: the two visitors are module
fixtures, the last test starts the stack again with DEMO_MAX=1 and puts the defaults back.
A failure here is never a reason to weaken a test (dev/demo.md, Two guarantees).
"""

import asyncio
import http.client
import json
import os
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx2 as httpx
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from bwtidy_demo.controller import DOMAIN, ID
from bwtidy_demo.gateway import NOTES, REFUSED

pytestmark = pytest.mark.skipif(
    os.environ.get("DEMO_STACK") != "1",
    reason="needs the demo stack: scripts/demo-stack test",
)

ROOT = Path(__file__).resolve().parents[3]
DEMO_STACK = ROOT / "scripts" / "demo-stack"
URL = os.environ.get("DEMO_STACK_URL", "")
PROJECT = os.environ.get("DEMO_STACK_PROJECT", "")
ACCOUNTS = "/data/accounts"
## a start logs bw in, a new vault registers and imports: both take seconds, never minutes
REQUEST_TIMEOUT = 120.0
POOL_TIMEOUT = 240.0
## real public servers, by name and by address: none may be reachable from a bwtidy container
OUTSIDE = [
    "vault.bitwarden.com:443",
    "identity.bitwarden.com:443",
    "api.bitwarden.com:443",
    "vault.bitwarden.eu:443",
    "1.1.1.1:443",
    "8.8.8.8:53",
]
REAL_SERVER = "https://vault.bitwarden.com"
REAL_LOGIN = {"server": REAL_SERVER, "email": "x@example.com", "password": "not-a-password"}

## runs in a container: what a TCP connection to each argument (HOST:PORT) comes to
PROBE = """
import json, socket, sys
found = {}
for target in sys.argv[1:]:
    host, _, port = target.rpartition(":")
    try:
        socket.create_connection((host, int(port)), 5).close()
        found[target] = "connected"
    except socket.gaierror:
        found[target] = "no name"
    except OSError:
        found[target] = "no route"
print(json.dumps(found))
"""

## runs in the controller: the pool's counts, asked the way the demo gateway asks
STATUS = """
import urllib.request
token = open("/secrets/controller-token").read().strip()
request = urllib.request.Request(
    "http://127.0.0.1:8100/status", headers={"Authorization": "Bearer " + token}
)
print(urllib.request.urlopen(request, timeout=10).read().decode())
"""

## runs in the controller: whether Vaultwarden has a user for each argument, by its admin API
USERS = """
import asyncio, json, sys
from pathlib import Path
from bwtidy_demo import init, vaultwarden

async def main():
    secrets = Path("/secrets")
    token = (secrets / init.ADMIN_TOKEN).read_text().strip()
    ca = secrets / init.CA_CERT
    async with vaultwarden.AdminSession("https://vaultwarden", token, ca_file=ca) as admin:
        found = {email: await admin.user_id(email) is not None for email in sys.argv[1:]}
    print(json.dumps(found))

asyncio.run(main())
"""


def stack(*args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
    """scripts/demo-stack ARGS, what it printed."""
    result = subprocess.run(
        [str(DEMO_STACK), *args], capture_output=True, text=True, env=env, timeout=600, check=False
    )
    if check and result.returncode != 0:
        raise AssertionError(f"demo-stack {' '.join(args[:3])} failed:\n{result.stderr[-2000:]}")
    return result.stdout


def in_container(service: str, *command: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(DEMO_STACK), "compose", "exec", "-T", service, *command],
        capture_output=True,
        text=True,
        timeout=120,
        check=check,
    )


def python_in(service: str, code: str, *args: str) -> str:
    return in_container(service, "python3", "-c", code, *args).stdout


def docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def container(service: str) -> str:
    return stack("compose", "ps", "--all", "--quiet", service).strip()


def status() -> dict[str, int]:
    return json.loads(python_in("controller", STATUS))


def wait_status(wanted: Callable[[dict[str, int]], bool], timeout: float = POOL_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        counts = status()
        if wanted(counts):
            return counts
        assert time.monotonic() < deadline, f"the pool did not get there: {counts}"
        time.sleep(1)


def users(*emails: str) -> dict[str, bool]:
    return json.loads(python_in("controller", USERS, *emails))


def account_dirs() -> list[str]:
    """Every directory two levels below the accounts root, as HOST/EMAIL."""
    listing = in_container("demo", "find", ACCOUNTS, "-mindepth", "2", "-maxdepth", "2").stdout
    return sorted(line.removeprefix(ACCOUNTS + "/") for line in listing.splitlines())


def hosts() -> list[str]:
    return sorted(in_container("demo", "ls", ACCOUNTS).stdout.split())


def browser() -> httpx.Client:
    """A client that keeps cookies and follows no redirect, like one tab of a browser."""
    return httpx.Client(base_url=URL, follow_redirects=False, timeout=REQUEST_TIMEOUT)


@dataclass(eq=False)
class Visitor:
    client: httpx.Client
    email: str

    @property
    def id(self) -> str:
        return self.email.partition("@")[0]

    def end(self) -> httpx.Response:
        if self in LIVE:
            LIVE.remove(self)
        return self.client.post("/demo/end")


## the visitors with a demo running. A session ends two minutes after its tab's last heartbeat,
## so every test starts with one from each, as their open tabs would send.
LIVE: list[Visitor] = []


@pytest.fixture(autouse=True)
def heartbeats() -> None:
    for visitor in LIVE:
        assert visitor.client.post("/api/session/heartbeat").status_code == 204


def start(client: httpx.Client) -> Visitor:
    wait_status(lambda counts: counts["ready"] >= 1)
    response = client.post("/demo/start")
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/"
    visitor = Visitor(client, client.get("/api/session").json()["bwLogin"]["email"])
    LIVE.append(visitor)
    return visitor


@pytest.fixture(scope="module")
def first() -> Iterator[Visitor]:
    with browser() as client:
        visitor = start(client)
        yield visitor
        visitor.end()


@pytest.fixture(scope="module")
def second(first: Visitor) -> Iterator[Visitor]:
    with browser() as client:
        visitor = start(client)
        yield visitor
        visitor.end()


def fixture_names() -> set[str]:
    fixture = json.loads((ROOT / "dev" / "fixture-personal.json").read_text())
    return {item["name"] for item in fixture["items"]}


def raw(method: str, path: str, headers: dict[str, str], body: bytes = b"") -> tuple[int, str]:
    """A request with the path as it is written: an HTTP client would resolve its dot segments."""
    address = urlsplit(URL)
    connection = http.client.HTTPConnection(address.hostname or "", address.port, timeout=30)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read().decode()
    finally:
        connection.close()


## --- the networks


def test_only_the_edge_is_on_a_network_with_a_route_out():
    internal = {
        name: docker("network", "inspect", f"{PROJECT}_{name}", "--format", "{{.Internal}}")
        for name in ("back", "front", "public")
    }
    assert internal == {"back": "true", "front": "true", "public": "false"}
    networks = {
        service: sorted(
            json.loads(
                docker(
                    "inspect", container(service), "--format", "{{json .NetworkSettings.Networks}}"
                )
            )
        )
        for service in ("vaultwarden", "controller", "demo", "edge")
    }
    assert networks == {
        "vaultwarden": [f"{PROJECT}_back"],
        "controller": [f"{PROJECT}_back"],
        "demo": [f"{PROJECT}_back", f"{PROJECT}_front"],
        "edge": [f"{PROJECT}_front", f"{PROJECT}_public"],
    }
    assert docker("inspect", container("init"), "--format", "{{.HostConfig.NetworkMode}}") == "none"


def test_only_the_edge_publishes_a_port():
    published = {
        service: docker(
            "inspect", container(service), "--format", "{{json .HostConfig.PortBindings}}"
        )
        for service in ("vaultwarden", "controller", "demo", "edge")
    }
    assert published.pop("edge") != "{}"
    assert set(published.values()) == {"{}"}


@pytest.mark.parametrize("service", ["demo", "controller"])
def test_a_bwtidy_container_reaches_vaultwarden_and_nothing_outside(service: str):
    found = json.loads(python_in(service, PROBE, "vaultwarden:443", *OUTSIDE))
    ## the probe itself works: the one server of the stack answers
    assert found.pop("vaultwarden:443") == "connected"
    assert "connected" not in found.values(), found
    assert set(found) == set(OUTSIDE)


def test_a_worker_uid_reaches_nothing_outside_either(first: Visitor):
    ## the workers run as their account's uid, in the demo container's network
    owner = in_container("demo", "stat", "-c", "%u", f"{ACCOUNTS}/vaultwarden/{first.email}")
    uid = owner.stdout.strip()
    assert int(uid) >= 70000
    as_worker = ["setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups"]
    probe = in_container("demo", *as_worker, "python3", "-c", PROBE, "vaultwarden:443", *OUTSIDE)
    found = json.loads(probe.stdout)
    assert found.pop("vaultwarden:443") == "connected"
    assert "connected" not in found.values(), found


def test_vaultwarden_reaches_nothing_outside():
    alive = ["curl", "--silent", "--insecure", "--max-time", "5", "https://127.0.0.1/alive"]
    assert in_container("vaultwarden", *alive).returncode == 0
    for target in ("https://1.1.1.1", "https://vault.bitwarden.com", "https://api.github.com"):
        out = ["curl", "--silent", "--max-time", "5", "--output", "/dev/null", target]
        assert in_container("vaultwarden", *out, check=False).returncode != 0, target


def test_the_edge_reaches_the_demo_but_neither_vaultwarden_nor_the_controller():
    def reaches(host: str, port: int) -> bool:
        command = ["nc", "-z", "-w", "3", host, str(port)]
        return in_container("edge", *command, check=False).returncode == 0

    def address(service: str) -> str:
        template = f'{{{{(index .NetworkSettings.Networks "{PROJECT}_back").IPAddress}}}}'
        return docker("inspect", container(service), "--format", template)

    assert reaches("demo", 8000)
    assert not reaches("vaultwarden", 443)
    assert not reaches("controller", 8100)
    ## nor by address, without the name
    assert not reaches(address("vaultwarden"), 443)
    assert not reaches(address("controller"), 8100)


## --- a visitor's demo


def test_the_front_page_sends_a_new_visitor_to_the_start_page():
    with browser() as client:
        for path in ("/", "/index.html"):
            response = client.get(path)
            assert response.status_code == 303
            assert response.headers["location"] == "/demo"
        page = client.get("/demo")
    assert page.status_code == 200
    assert "Start demo" in page.text
    assert 'action="/demo/start"' in page.text
    ## no credential field anywhere: the page has a button and nothing to type into
    assert "<input" not in page.text.lower()
    assert "<textarea" not in page.text.lower()


def test_a_start_logs_the_visitor_in_with_both_cookies(first: Visitor):
    assert set(first.client.cookies) == {"bwtidy_account", "bwtidy_session"}
    session = first.client.get("/api/session").json()
    assert session["state"] == "unlocked"
    assert session["bwLogin"]["server"] == "https://vaultwarden"
    assert first.email.endswith("@" + DOMAIN)
    assert ID.fullmatch(first.id), first.id
    assert len(first.id.split("-")) >= 4
    page = first.client.get("/")
    assert page.status_code == 200
    assert "Never enter your own details" in page.text
    assert account_dirs() == [f"vaultwarden/{first.email}"]


def test_the_grid_lists_the_seeded_items(first: Visitor):
    grid = first.client.get("/api/grid")
    assert grid.status_code == 200
    names = {row["cells"]["name"] for row in grid.json()["rows"]}
    assert fixture_names() <= names


def mcp_items(token: str) -> list[dict]:
    async def run() -> list[dict]:
        client = httpx.AsyncClient(
            base_url=URL, headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT
        )
        async with client, Client(streamable_http_client(f"{URL}/mcp", http_client=client)) as mcp:
            result = await mcp.call_tool("list_items", {})
        assert not result.is_error
        block = result.content[0]
        assert isinstance(block, TextContent)
        return json.loads(block.text)["items"]

    return asyncio.run(run())


@pytest.fixture(scope="module")
def mcp_token(first: Visitor) -> str:
    asked = datetime.now(UTC)
    ## the visitor asks for 30 days and gets one
    created = first.client.post("/api/mcp/token", json={"days": 30})
    assert created.status_code == 201
    expires = datetime.fromisoformat(created.json()["expiresAt"])
    assert expires - asked <= timedelta(days=1, minutes=1)
    assert first.client.post("/api/mcp", json={"enabled": True}).status_code == 200
    return created.json()["token"]


def test_mcp_lists_the_items_of_the_visitors_vault(mcp_token: str):
    names = {item["name"] for item in mcp_items(mcp_token)}
    assert fixture_names() <= names


def test_mcp_without_the_token_is_refused():
    headers = {"Accept": "application/json, text/event-stream"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    with browser() as client:
        assert client.post("/mcp", headers=headers, json=body).status_code == 401
        wrong = {**headers, "Authorization": "Bearer not-a-token"}
        assert client.post("/mcp", headers=wrong, json=body).status_code == 401


## --- no way in with a real account

REFUSED_REQUESTS = [
    ("POST", "/gateway/account", {"server": REAL_SERVER, "email": "x@example.com", "create": True}),
    ("POST", "/gateway/account", {"server": REAL_SERVER, "email": "x@example.com"}),
    ("POST", "/api/bw-login", REAL_LOGIN),
    ("POST", "/api/bw-login", {**REAL_LOGIN, "server": "https://vault.bitwarden.eu"}),
    ("GET", "/api/bw-login/output", None),
    ("POST", "/api/bw-login/input", {"text": "x"}),
    ("POST", "/api/bw-login/finish", {}),
    ("DELETE", "/api/bw-login", None),
    ("POST", "/api/session", {"password": "not-a-password"}),
    ("POST", "/api/bw-logout", None),
    ("POST", "/api/account/delete", {"password": "not-a-password"}),
]
## the same routes spelled so that only a resolved path matches them
REFUSED_SPELLINGS = [
    ("POST", "/gateway/account/"),
    ("POST", "//gateway//account"),
    ("POST", "/gateway/./account"),
    ("POST", "/x/../gateway/account"),
    ("POST", "/gateway/%61ccount"),
    ("POST", "/api/bw-login/"),
    ("POST", "/api//bw-login"),
    ("POST", "/api/./bw-login"),
    ("POST", "/api/x/../bw-login"),
    ("POST", "/api/%62w-login"),
    ("POST", "/api/bw-login/x/../finish"),
    ("POST", "/api/./session"),
    ("POST", "/api/session/"),
    ("POST", "/api/account/./delete"),
    ("POST", "/api/bw-logout/"),
]


def refused_ids(value: object) -> str | None:
    return None if isinstance(value, dict) or value is None else str(value)


@pytest.mark.parametrize(("method", "path", "body"), REFUSED_REQUESTS, ids=refused_ids)
def test_a_route_that_takes_a_login_answers_403(first: Visitor, method: str, path: str, body):
    with browser() as stranger:
        for client in (stranger, first.client):
            response = client.request(method, path, json=body)
            assert response.status_code == 403, f"{method} {path}: {response.text[:200]}"
            assert response.text == REFUSED
            assert "set-cookie" not in response.headers


@pytest.mark.parametrize(("method", "path"), REFUSED_SPELLINGS)
def test_another_spelling_of_a_refused_route_is_refused_too(first: Visitor, method: str, path: str):
    cookies = "; ".join(f"{name}={value}" for name, value in first.client.cookies.items())
    body = json.dumps(REAL_LOGIN).encode()
    for headers in ({}, {"Cookie": cookies}):
        sent = {"Content-Type": "application/json", "Host": urlsplit(URL).netloc, **headers}
        code, text = raw(method, path, sent, body)
        ## 403 from the wrapper, or the edge refusing a path it cannot make sense of: never a worker
        assert code in (400, 403), f"{method} {path}: {code} {text[:200]}"
        if code == 403:
            assert text == REFUSED


def test_the_refused_requests_reached_no_worker_and_made_no_account(first: Visitor):
    for path in ("/gateway/account", "/api/bw-login"):
        assert first.client.post(path, json=REAL_LOGIN).status_code == 403
    ## a login that went through would have made a directory for its server
    assert hosts() == ["vaultwarden"]
    assert account_dirs() == [f"vaultwarden/{first.email}"]
    session = first.client.get("/api/session").json()
    assert session["state"] == "unlocked"
    assert session["bwLogin"] == {
        "loggedIn": True,
        "server": "https://vaultwarden",
        "email": first.email,
        "accountFixed": True,
    }
    logs = stack("compose", "logs", "--no-log-prefix", "demo")
    assert "Refused POST '/gateway/account'" in logs
    assert "Refused POST '/api/bw-login'" in logs
    assert "bitwarden.com" not in logs
    assert "bitwarden.eu" not in logs
    ## Vaultwarden's start banner names Bitwarden, a request to a real server would name its host
    assert ".bitwarden." not in stack("compose", "logs", "--no-log-prefix", "vaultwarden")


## --- the second visitor, the end and the cleanup


def test_a_second_visitor_gets_a_vault_of_their_own(first: Visitor, second: Visitor):
    assert second.email != first.email
    assert ID.fullmatch(second.id), second.id
    assert account_dirs() == sorted(f"vaultwarden/{v.email}" for v in (first, second))
    ledger = in_container("controller", "cat", "/ledger/ids").stdout.split()
    assert {first.id, second.id} <= set(ledger)
    assert len(ledger) == len(set(ledger))
    assert users(first.email, second.email) == {first.email: True, second.email: True}
    assert wait_status(lambda counts: counts["building"] == 0)["taken"] == 2


def test_the_end_deletes_the_account_and_the_vaultwarden_user(
    first: Visitor, second: Visitor, mcp_token: str
):
    old = dict(first.client.cookies)
    response = first.end()
    assert response.status_code == 303
    assert response.headers["location"] == "/demo"
    assert set(first.client.cookies) == set()
    assert account_dirs() == [f"vaultwarden/{second.email}"]
    assert users(first.email, second.email) == {first.email: False, second.email: True}
    ## the cookies and the MCP token of the ended demo open nothing
    with httpx.Client(base_url=URL, cookies=old, timeout=REQUEST_TIMEOUT) as stale:
        assert stale.get("/").status_code == 303
        assert stale.get("/api/grid").status_code != 200
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {mcp_token}",
        }
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        assert stale.post("/mcp", headers=headers, json=body).status_code == 401
    ## the other visitor is untouched
    assert second.client.get("/api/session").json()["state"] == "unlocked"
    assert account_dirs() == [f"vaultwarden/{second.email}"]


def test_the_pool_refills_after_the_end(second: Visitor):
    counts = wait_status(lambda counts: counts["ready"] == 2 and counts["building"] == 0)
    assert counts == {"ready": 2, "taken": 1, "building": 0, "failed": 0}


def test_switch_account_ends_the_demo_too(second: Visitor):
    LIVE.remove(second)
    assert second.client.delete("/gateway/account").status_code == 204
    assert account_dirs() == []
    assert users(second.email) == {second.email: False}
    counts = wait_status(lambda counts: counts["taken"] == 0 and counts["building"] == 0)
    assert counts == {"ready": 2, "taken": 0, "building": 0, "failed": 0}


## --- the limits


def test_the_edge_refuses_a_large_body():
    with browser() as client:
        response = client.post("/api/session/heartbeat", content=b"x" * (2 * 1024 * 1024))
    assert response.status_code == 413


def test_the_edge_limits_mcp_requests_per_address():
    headers = {"Accept": "application/json, text/event-stream"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    with browser() as client:
        codes = [client.post("/mcp", headers=headers, json=body).status_code for _ in range(300)]
    assert set(codes) == {401, 429}, set(codes)
    ## the burst passes first
    assert codes[0] == 401


def test_the_edge_limits_starts_per_address_and_takes_no_vault():
    before = wait_status(lambda counts: counts["building"] == 0)
    accounts = account_dirs()
    ## a start from another site is refused by the wrapper and takes no vault, the edge counts it
    headers = {"Sec-Fetch-Site": "cross-site"}
    with browser() as client:
        answers = [client.post("/demo/start", headers=headers) for _ in range(20)]
        limited = [answer for answer in answers if answer.status_code == 303]
        assert limited, [answer.status_code for answer in answers]
        assert {answer.headers["location"] for answer in limited} == {"/demo?note=full"}
        assert {answer.status_code for answer in answers} <= {303, 403}
        page = client.get("/demo?note=full")
    assert NOTES["full"] in page.text
    assert status() == before
    assert account_dirs() == accounts


## --- DEMO_MAX


@pytest.fixture
def one_vault() -> Iterator[None]:
    """The stack started again with room for one visitor, the defaults back afterwards."""
    defaults = {k: v for k, v in os.environ.items() if k not in ("DEMO_MAX", "DEMO_POOL_READY")}
    stack("up", "--no-build", env={**defaults, "DEMO_MAX": "1", "DEMO_POOL_READY": "1"})
    try:
        yield
    finally:
        stack("up", "--no-build", env=defaults)


def test_a_start_past_demo_max_gets_the_busy_note(one_vault: None):
    with browser() as one, browser() as two:
        visitor = start(one)
        assert wait_status(lambda counts: counts["taken"] == 1) == {
            "ready": 0,
            "taken": 1,
            "building": 0,
            "failed": 0,
        }
        refused = two.post("/demo/start")
        assert refused.status_code == 303
        assert refused.headers["location"] == "/demo?note=free"
        assert set(two.cookies) == set()
        assert NOTES["free"] in two.get("/demo?note=free").text
        assert two.get("/").headers["location"] == "/demo"
        ## the running demo is untouched, and no second account was made
        assert visitor.client.get("/api/session").json()["state"] == "unlocked"
        assert account_dirs() == [f"vaultwarden/{visitor.email}"]
        assert visitor.end().status_code == 303
        ## its end makes room again
        later = start(two)
        assert later.email != visitor.email
        assert later.end().status_code == 303
    assert account_dirs() == []
