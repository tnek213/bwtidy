"""The gateway's app (docs/spec.md, Gateway): its routes, the documentation site at /docs/, the
routing cookies and what becomes of an account directory as its worker comes and goes."""

import asyncio
import os
import signal
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bwtidy.config import SettingsError
from bwtidy.gateway.app import LOCKED, NO_ROUTE, create_gateway_app
from bwtidy.gateway.settings import GatewaySettings
from bwtidy.mcp_token import EXPIRED
from bwtidy.protocol import ACCOUNT_COOKIE as COOKIE
from bwtidy.protocol import DELETE_MARKER, PENDING_MARKER
from tests.support.gateway_env import (
    ALLOWED,
    EMAIL,
    EXITING,
    LOGIN,
    UNLOCK,
    GatewayEnv,
    account,
    assert_unauthorized,
    backups_env,
    backups_of,
    bearer,
    make_env,
    mcp_token,
    settings,
    wait_for,
)
from tests.support.quiet import assert_absent


def test_the_gateway_refuses_to_run_without_root_unless_told(tmp_path: Path):
    with pytest.raises(SettingsError):
        create_gateway_app(settings(tmp_path, switch_uid=True))


def test_health_is_answered_by_the_gateway_itself(tmp_path: Path):
    for env in make_env(tmp_path, version="261030 abc1234"):
        assert env.client.get("/api/health").json() == {"status": "ok", "version": "261030 abc1234"}


def test_a_missing_account_is_404_without_create(env: GatewayEnv):
    response = env.open(create=False)
    assert response.status_code == 404
    assert response.json() == {"exists": False}
    assert COOKIE not in response.cookies
    assert not env.directory().exists()
    assert env.account(None) == {**NO_ROUTE, "firstRun": True}


def test_opening_an_account_starts_no_worker(env: GatewayEnv):
    """Only a click on Log in or Unlock starts one (docs/spec.md, Gateway)."""
    response = env.open()
    assert response.status_code == 200, response.text
    token = env.cookie(response)
    ## a new account gets its directory only at its first Bitwarden login
    assert not env.directory().exists()
    assert env.worker_pids() == []
    assert env.account(token) == {
        "account": {"server": ALLOWED, "email": EMAIL},
        "running": False,
        "exists": False,
        "pending": False,
        "loggedIn": False,
        "firstRun": True,
    }
    ## nothing else under /api/ starts a worker, and an unlock has no account to unlock yet
    for method, path in (
        ("GET", "/api/session"),
        ("POST", "/api/session/heartbeat"),
        ("POST", "/api/bw-login/finish"),
        ("GET", LOGIN),
        ("POST", UNLOCK),
    ):
        refused = env.request(method, path, {COOKIE: token}, json={})
        assert refused.status_code == 401, (method, path)
        assert refused.json() == {"detail": "No account"}
    assert env.worker_pids() == []
    assert not env.directory().exists()


def test_a_pending_directory_goes_when_its_worker_exits(env: GatewayEnv):
    token = env.started()
    [pid] = env.worker_pids()
    os.kill(pid, signal.SIGKILL)
    wait_for(lambda: not env.directory().exists(), "the pending directory to go")
    assert env.worker_pids() == []
    assert env.session(token).status_code == 401
    state = env.account(token)
    assert state == {
        "account": {"server": ALLOWED, "email": EMAIL},
        "running": False,
        "exists": False,
        "pending": False,
        "loggedIn": False,
        "firstRun": True,
    }
    assert env.open(create=False).status_code == 404
    ## the next Log in of the page makes the directory again
    assert env.click(token).status_code == 200
    assert (env.directory() / PENDING_MARKER).exists()


def test_a_replacement_keeps_the_pending_directory_its_predecessor_leaves(env: GatewayEnv):
    env.started()
    gateway = env.gateway
    key = account(EMAIL)
    worker = gateway.workers.get(key)

    async def race() -> None:
        ## an open that got the lock before the old worker's clean up did
        async with gateway.lock(key):
            os.kill(worker.pid, signal.SIGKILL)
            await worker.proc.wait()
            await asyncio.sleep(0.05)
            assert gateway.workers.get(key) is None
            path, uid = gateway.start_slot(key, False)
            await gateway.workers.start(key, path, uid)
        await worker.gone.wait()

    assert env.client.portal is not None
    env.client.portal.call(race)
    assert env.directory().is_dir()
    assert gateway.workers.get(key).running


def test_a_directory_past_its_first_unlock_stays_and_starts_again(env: GatewayEnv):
    token = env.started()
    (env.directory() / PENDING_MARKER).unlink()
    [pid] = env.worker_pids()
    os.kill(pid, signal.SIGKILL)
    wait_for(lambda: env.worker_pids() == [], "the worker to exit")
    assert env.directory().is_dir()
    assert env.account(token)["running"] is False
    ## the existing directory is kept, no marker comes back, the unlock starts a new worker
    response = env.open(create=False)
    assert response.status_code == 200
    assert response.json()["exists"] is True
    assert env.worker_pids() == []
    assert env.click(env.cookie(response), UNLOCK).status_code == 200
    assert not (env.directory() / PENDING_MARKER).exists()
    assert env.session(env.cookie(response)).json()["pid"] != pid
    ## the old cookie names the account, so it reaches the new worker too
    assert env.session(token).json()["pid"] != pid


def test_a_worker_exit_with_a_delete_marker_removes_the_account_and_its_backups(tmp_path: Path):
    for env in backups_env(tmp_path):
        token = env.started()
        path = env.directory()
        ## past its first unlock, so only the marker could delete it
        (path / PENDING_MARKER).unlink()
        backups = backups_of(tmp_path)
        (backups / "20261101T120000Z.zip.age").write_bytes(b"backup")
        (path / "app" / DELETE_MARKER).write_bytes(b"")
        [pid] = env.worker_pids()
        os.kill(pid, signal.SIGKILL)
        wait_for(lambda path=path: not path.exists(), "the account directory to go")
        ## the backups directory goes too, even with a backup in it
        assert not backups.exists()
        ## the routing cookie no longer reaches the account
        assert env.account(token) == {**NO_ROUTE, "firstRun": True}


def test_a_worker_exit_without_a_marker_deletes_nothing(env: GatewayEnv):
    env.started()
    path = env.directory()
    (path / PENDING_MARKER).unlink()
    [pid] = env.worker_pids()
    os.kill(pid, signal.SIGKILL)
    wait_for(lambda: env.worker_pids() == [], "the worker to exit")
    assert path.is_dir()


def test_two_clicks_at_once_start_one_worker(env: GatewayEnv):
    tokens = [env.cookie(env.open()) for _ in range(2)]
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(env.click, tokens))
    assert [r.status_code for r in responses] == [200, 200]
    assert len(env.worker_pids()) == 1
    pids = {r.json()["pid"] for r in responses}
    assert len(pids) == 1


def test_a_new_login_replaces_the_browsers_routing_cookie(env: GatewayEnv):
    first = env.started(email="user@example.test")
    second = env.started(email="peer@example.test", cookies={COOKIE: first})
    assert env.session(first).status_code == 401
    assert env.session(second).json()["env"]["BWTIDY_ACCOUNT_EMAIL"] == "peer@example.test"


def test_delete_forgets_the_routing_cookie_and_keeps_the_worker(env: GatewayEnv):
    token = env.started()
    (env.directory() / PENDING_MARKER).unlink()
    response = env.request("DELETE", "/gateway/account", {COOKIE: token})
    assert response.status_code == 204
    assert f"{COOKIE}=" in response.headers["set-cookie"]
    assert env.session(token).status_code == 401
    assert len(env.worker_pids()) == 1
    assert env.directory().is_dir()


def test_delete_of_a_pending_account_stops_its_worker_and_drops_the_directory(env: GatewayEnv):
    token = env.started()
    response = env.request("DELETE", "/gateway/account", {COOKIE: token})
    assert response.status_code == 204
    ## gone by the time the answer comes, so the form offers to create it again at once
    assert env.worker_pids() == []
    assert env.directory_gone()
    assert env.open(create=False).status_code == 404


def test_a_worker_on_its_way_out_is_waited_for_and_replaced(env: GatewayEnv):
    token = env.started()
    (env.directory() / PENDING_MARKER).unlink()
    [pid] = env.worker_pids()
    (env.directory() / EXITING).touch()
    response = env.click(token, UNLOCK)
    assert response.status_code == 200
    [new_pid] = env.worker_pids()
    assert new_pid != pid
    assert response.json()["pid"] == new_pid
    assert env.session(token).json()["pid"] == new_pid


def test_a_worker_on_its_way_out_does_not_count_as_running(env: GatewayEnv):
    token = env.started()
    (env.directory() / PENDING_MARKER).unlink()
    (env.directory() / EXITING).touch()
    assert env.account(token)["running"] is False


def test_the_routing_cookie_is_secure_only_when_asked(tmp_path: Path):
    for env in make_env(tmp_path, secure_cookies=True):
        header = env.open().headers["set-cookie"].lower()
        assert header.startswith(f"{COOKIE}=")
        assert "; secure" in header and "; httponly" in header and "samesite=strict" in header
        assert env.session(env.started()).json()["env"]["BWTIDY_SECURE_COOKIES"] == "1"


def test_mcp_without_a_known_token_is_401(env: GatewayEnv):
    ## no account has a token yet
    assert_unauthorized(env.request("POST", "/mcp", {}, headers=bearer("x"), json={}))
    cookie = env.started()
    token = mcp_token(env)
    assert_unauthorized(env.request("POST", "/mcp", {}, json={}))
    assert_unauthorized(env.request("POST", "/mcp", {}, headers=bearer(token + "x"), json={}))
    assert_unauthorized(env.request("POST", "/mcp", {}, headers=bearer(""), json={}))
    assert_unauthorized(
        env.request("POST", "/mcp", {}, headers={"Authorization": f"Basic {token}"}, json={})
    )
    ## a routing cookie is no way into MCP
    assert_unauthorized(env.request("POST", "/mcp", {COOKIE: cookie}, json={}))
    assert_unauthorized(env.request("GET", "/mcp", {COOKIE: cookie}))
    ## a replaced token stops routing at once
    new = mcp_token(env)
    assert_unauthorized(env.request("POST", "/mcp", {}, headers=bearer(token), json={}))
    assert env.request("POST", "/mcp", {}, headers=bearer(new), json={}).status_code == 200


def test_mcp_with_an_expired_token_is_401_with_its_own_text(env: GatewayEnv):
    env.started(email="user@example.test")
    env.started(email="peer@example.test")
    user = mcp_token(env, "user@example.test", days=1, made=timedelta(days=1, seconds=1))
    response = env.request("POST", "/mcp", {}, headers=bearer(user), json={})
    assert response.status_code == 401
    assert response.text == EXPIRED
    assert response.headers["www-authenticate"] == "Bearer"
    named = "user" in response.text
    assert not named, "the answer names the account"
    ## a token about to expire still works, and one without an expiry too
    peer = mcp_token(env, "peer@example.test", days=1, made=timedelta(hours=23, minutes=59))
    response = env.request("POST", "/mcp", {}, headers=bearer(peer), json={})
    assert response.json()["email"] == "peer@example.test"
    user = mcp_token(env, "user@example.test", days=None, made=timedelta(days=4000))
    response = env.request("POST", "/mcp", {}, headers=bearer(user), json={})
    assert response.json()["email"] == "user@example.test"


def test_mcp_for_an_account_whose_worker_is_not_running_is_503(env: GatewayEnv):
    env.started()
    token = mcp_token(env)
    ## past its first unlock, so the directory stays when the worker exits
    (env.directory() / PENDING_MARKER).unlink()
    [pid] = env.worker_pids()
    os.kill(pid, signal.SIGKILL)
    wait_for(lambda: env.worker_pids() == [], "the worker to exit")
    response = env.request("POST", "/mcp", {}, headers=bearer(token), json={})
    assert response.status_code == 503
    assert response.text == LOCKED
    ## opening the account starts no worker, its next unlock does
    token_of_browser = env.cookie(env.open(create=False))
    assert env.request("POST", "/mcp", {}, headers=bearer(token), json={}).status_code == 503
    assert env.click(token_of_browser, UNLOCK).status_code == 200
    ## the account's next worker gets the token's requests again
    assert env.request("POST", "/mcp", {}, headers=bearer(token), json={}).status_code == 200


def test_the_third_pending_login_is_refused_until_one_is_gone(env: GatewayEnv):
    emails = ["a@example.test", "b@example.test", "c@example.test"]
    first = env.started(email=emails[0])
    env.started(email=emails[1])
    ## the form still opens a new account, its first Log in makes the directory and is refused
    third = env.cookie(env.open(email=emails[2]))
    response = env.click(third)
    assert response.status_code == 429
    assert response.json()["detail"] == (
        "Too many new accounts wait for their first login. Try again in a few minutes."
    )
    assert not env.directory(email=emails[2]).exists()
    assert len(env.worker_pids()) == 2
    ## an existing pending account still opens, only a new directory counts against the cap
    second = env.cookie(env.open(email=emails[1], create=False))
    assert env.click(second, UNLOCK).status_code == 200
    env.request("DELETE", "/gateway/account", {COOKIE: first})
    assert env.click(third).status_code == 200


def test_a_refused_account_body_is_never_echoed(env: GatewayEnv):
    email = "someone-for-the-echo-test@example.test"
    response = env.request("POST", "/gateway/account", {}, json={"email": email})
    assert response.status_code == 422
    echoed = email in response.text
    assert not echoed
    assert response.json()["detail"][0]["loc"] == ["body", "server"]


@pytest.mark.parametrize("inside", ["a/b", ".", "a"])
def test_a_backups_root_that_overlaps_the_accounts_is_refused(tmp_path: Path, inside: str):
    with pytest.raises(SettingsError, match="BWTIDY_BACKUPS_ROOT"):
        create_gateway_app(settings(tmp_path, backups_root=tmp_path / inside))


FRONTEND = "<title>the frontend</title>"


DOCS_HOME = "<title>the docs</title>"


DOCS_PAGE = "<title>a docs page</title>"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def client(tmp_path: Path, **extra: Any) -> Iterator[TestClient]:
    static = tmp_path / "static"
    write(static / "index.html", FRONTEND)
    settings = GatewaySettings(
        accounts_dir=tmp_path / "accounts", switch_uid=False, static_dir=static, **extra
    )
    with TestClient(create_gateway_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def with_docs(tmp_path: Path) -> Iterator[TestClient]:
    docs = tmp_path / "site"
    write(docs / "index.html", DOCS_HOME)
    write(docs / "a" / "index.html", DOCS_PAGE)
    yield from client(tmp_path, docs_dir=docs)


def test_the_docs_are_served_without_a_cookie(with_docs: TestClient):
    assert COOKIE not in with_docs.cookies
    home = with_docs.get("/docs/")
    assert home.status_code == 200
    assert home.text == DOCS_HOME
    page = with_docs.get("/docs/a/")
    assert page.status_code == 200
    assert page.text == DOCS_PAGE


def test_docs_without_the_slash_redirects(with_docs: TestClient):
    response = with_docs.get("/docs", follow_redirects=False)
    assert response.status_code in (307, 308)
    assert response.headers["location"] == "/docs/"
    assert with_docs.get("/docs").text == DOCS_HOME


def test_a_missing_docs_page_is_not_the_frontend(with_docs: TestClient):
    response = with_docs.get("/docs/nope/")
    assert response.status_code == 404
    assert_absent(FRONTEND, response.text, "the response")


def test_the_frontend_still_answers_at_the_root(with_docs: TestClient):
    assert with_docs.get("/").text == FRONTEND


def test_without_a_docs_dir_there_are_no_docs(tmp_path: Path):
    for test_client in client(tmp_path):
        assert_absent(DOCS_HOME, test_client.get("/docs/").text, "the response")
        assert test_client.get("/").text == FRONTEND
