"""The gateway's proxy (docs/spec.md, Gateway): which worker a request reaches and what it passes
on either way."""

import json
import logging
import re
from pathlib import Path

import pytest

from bwtidy.gateway.proxy import is_mcp_path, is_worker_path, without_cookie
from bwtidy.mcp_token import UNAUTHORIZED
from bwtidy.protocol import ACCOUNT_COOKIE as COOKIE
from bwtidy.protocol import WORKER_GONE
from tests.support.gateway_env import (
    ADDRESSES,
    ALLOWED,
    DROP,
    EMAIL,
    KEEP_ALIVE_WORKER,
    LOGIN,
    PUBLIC,
    GatewayEnv,
    bearer,
    make_env,
    mcp_token,
    write_script,
)
from tests.support.quiet import assert_absent


def test_the_routing_cookie_reaches_the_right_worker(env: GatewayEnv):
    user = env.started(email="user@example.test")
    peer = env.started(email="peer@example.test")
    assert user != peer
    user_pid = env.session(user).json()["pid"]
    peer_pid = env.session(peer).json()["pid"]
    assert user_pid != peer_pid
    assert sorted(env.worker_pids()) == sorted([user_pid, peer_pid])
    assert env.session(user).json()["env"]["BWTIDY_ACCOUNT_EMAIL"] == "user@example.test"
    assert env.session(peer).json()["env"]["BWTIDY_ACCOUNT_EMAIL"] == "peer@example.test"
    state = env.account(peer)
    assert state == {
        "account": {"server": ALLOWED, "email": "peer@example.test"},
        "running": True,
        "exists": True,
        "pending": True,
        "loggedIn": False,
    }


def test_without_a_route_the_api_answers_401(env: GatewayEnv):
    assert env.session(None).status_code == 401
    assert env.session(None).json() == {"detail": "No account"}
    assert env.session("unknown").status_code == 401
    assert env.request("POST", "/api/session", {}, json={}).status_code == 401
    assert env.request("POST", "/mcp", {}, json={}).text == UNAUTHORIZED
    ## the gateway's own routes do not need one
    assert env.client.get("/api/health").status_code == 200
    assert env.client.get("/gateway/account").status_code == 200


def test_the_routing_cookie_is_taken_out_of_the_forwarded_header(env: GatewayEnv):
    token = env.started()
    response = env.request(
        "GET",
        "/api/session?x=1",
        {COOKIE: token, "bwtidy_session": "s", "other": "o"},
        headers={"Connection": "keep-alive, X-Drop", "X-Drop": "1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert_absent(token, body["cookie"], "the cookie header")
    assert "bwtidy_session=s" in body["cookie"]
    assert "other=o" in body["cookie"]
    assert body["dropped"] is None
    assert body["agent"] == "testclient"
    assert body["path"] == "/api/session?x=1"
    ## the worker's headers reach the browser, hop-by-hop ones aside
    assert response.headers["x-worker"] == "1"
    assert response.cookies["bwtidy_session"] == "fake"
    ## a routing cookie alone leaves no Cookie header behind
    assert env.session(token).json()["cookie"] is None


def test_another_browsers_routing_cookie_carries_no_session_to_the_worker(env: GatewayEnv):
    """Anyone who knows the server and email gets a routing cookie for the account, but the
    worker sees no session cookie from them, so it neither ends the session for a wrong
    password nor logs the CLI out (tests/unit/test_worker.py checks the worker's side)."""
    owner = env.started()
    other = env.cookie(env.open(create=False))
    assert other != owner
    for path in ("/api/session", "/api/bw-logout"):
        own = env.request("POST", path, {COOKIE: owner, "bwtidy_session": "s"}, json={})
        assert own.json()["cookie"] == "bwtidy_session=s"
        response = env.request("POST", path, {COOKIE: other}, json={})
        assert response.json()["pid"] == own.json()["pid"]
        assert response.json()["cookie"] is None


def test_a_request_body_is_streamed_to_the_worker(env: GatewayEnv):
    token = env.cookie(env.open())
    response = env.request("POST", "/api/bw-login", {COOKIE: token}, json={"password": "x"})
    assert response.status_code == 200
    assert json.loads(response.json()["body"]) == {"password": "x"}


def test_a_session_request_is_logged_when_it_starts_and_when_it_answers(
    env: GatewayEnv, caplog: pytest.LogCaptureFixture
):
    token = env.started()
    label = f"vaultwarden/{EMAIL}"
    with caplog.at_level(logging.INFO, logger="bwtidy"):
        env.request("POST", "/api/session", {COOKIE: token}, json={"password": "not-in-a-log"})
        env.request("POST", "/api/session/heartbeat", {COOKIE: token})
        env.session(token)
    assert [record.getMessage() for record in caplog.records] == [
        f"POST /api/session for {label} starts",
        f"POST /api/session for {label} answered 200",
    ]
    assert_absent("not-in-a-log", caplog.text, "the log")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("bwtidy_account=abc", ""),
        ("a=1; bwtidy_account=abc; b=2", "a=1; b=2"),
        ("bwtidy_account=abc; b=2", "b=2"),
        ("bwtidy_account_x=1; b=2", "bwtidy_account_x=1; b=2"),
    ],
)
def test_without_cookie(header: str, expected: str):
    assert without_cookie(header, COOKIE) == expected


def test_a_request_the_worker_drops_is_502_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    keep_alive = write_script(tmp_path / "keep-alive", KEEP_ALIVE_WORKER)
    for env in make_env(tmp_path, worker_bin=keep_alive):
        token = env.started()
        pid = env.worker_pids()[0]
        with caplog.at_level(logging.WARNING, logger="bwtidy"):
            response = env.request("GET", DROP, {COOKIE: token})
        assert response.status_code == 502
        assert response.json() == {"detail": WORKER_GONE}
        [message] = [record.getMessage() for record in caplog.records]
        assert re.fullmatch(
            rf"The worker for vaultwarden/{re.escape(EMAIL)} \(pid {pid}, started [0-9.]+ s ago, "
            r"running\) did not answer on a new connection: RemoteProtocolError .+",
            message,
        ), message


def test_mcp_is_routed_by_the_token(env: GatewayEnv):
    user_cookie = env.started(email="user@example.test")
    env.started(email="peer@example.test")
    user, peer = mcp_token(env, "user@example.test"), mcp_token(env, "peer@example.test")
    for email, token in (("user@example.test", user), ("peer@example.test", peer)):
        response = env.request("POST", "/mcp", {}, headers=bearer(token), json={"x": 1})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["email"] == email
        assert body["path"] == "/mcp"
        assert json.loads(body["body"]) == {"x": 1}
        ## the token goes on unchanged, the worker checks it again
        assert body["authorization"] == f"Bearer {token}"
        assert body["host"] == "testserver"
    ## the token decides, a browser's routing cookie does not, and it is not passed on
    response = env.request("POST", "/mcp", {COOKIE: user_cookie}, headers=bearer(peer), json={})
    assert response.json()["email"] == "peer@example.test"
    assert response.json()["cookie"] is None
    ## the bearer scheme is matched case-insensitively, like the worker does
    lower = env.request("POST", "/mcp", {}, headers={"Authorization": f"bearer {user}"}, json={})
    assert lower.json()["email"] == "user@example.test"


def test_the_server_is_checked_again_before_bw_login(
    env: GatewayEnv, monkeypatch: pytest.MonkeyPatch
):
    host = "vault.example.test"
    token = env.cookie(env.open(server=PUBLIC))
    ## the name now resolves elsewhere, like a DNS rebinding server would make it
    monkeypatch.setitem(ADDRESSES, host, ["10.0.0.1"])
    response = env.request("POST", "/api/bw-login", {COOKIE: token}, json={"password": "x"})
    assert response.status_code == 422
    assert response.json()["detail"] == f"{PUBLIC} does not resolve to a public address."
    ## checked before anything starts: no directory, no worker
    assert not env.directory(host).exists()
    assert env.worker_pids() == []
    monkeypatch.setitem(ADDRESSES, host, ["192.88.99.10"])
    assert env.click(token).status_code == 200
    assert env.seen(host) == ["POST /api/bw-login"]
    ## a running worker's login is checked too
    monkeypatch.setitem(ADDRESSES, host, ["10.0.0.1"])
    response = env.request("POST", "/api/bw-login", {COOKIE: token}, json={"password": "x"})
    assert response.status_code == 422
    assert env.seen(host) == ["POST /api/bw-login"]
    ## only the start of the login is checked, not its console
    output = env.request("GET", "/api/bw-login/output?after=0", {COOKIE: token})
    assert output.status_code == 404
    assert env.seen(host)[-1] == "GET /api/bw-login/output?after=0"
    monkeypatch.setitem(ADDRESSES, host, ["192.88.99.10"])
    response = env.request("POST", "/api/bw-login", {COOKIE: token}, json={"password": "x"})
    assert response.status_code == 200
    assert env.seen(host)[-1] == "POST /api/bw-login"


def test_an_allowed_server_skips_the_check_before_bw_login(env: GatewayEnv):
    ## vaultwarden does not resolve in the fake resolver
    token = env.cookie(env.open())
    response = env.request("POST", "/api/bw-login", {COOKIE: token}, json={})
    assert response.status_code == 200
    assert env.seen() == ["POST /api/bw-login"]


def test_a_workers_routing_cookie_never_reaches_the_browser(env: GatewayEnv):
    token = env.started()
    response = env.request("GET", "/api/cookies", {COOKIE: token})
    assert response.status_code == 200
    assert response.headers.get_list("set-cookie") == ["bwtidy_session=y; Path=/"]


def test_health_with_a_trailing_slash_is_the_gateways_too(tmp_path: Path):
    for env in make_env(tmp_path, version="261030 abc1234"):
        token = env.started()
        response = env.request("GET", "/api/health/", {COOKIE: token})
        assert response.json() == {"status": "ok", "version": "261030 abc1234"}
        assert env.seen() == [f"POST {LOGIN}"]


@pytest.mark.parametrize("path", ["/docs", "/docs/", "/docs/api/health", "/docs/mcp"])
def test_no_docs_path_goes_to_a_worker(path: str):
    assert not is_worker_path(path)
    assert not is_mcp_path(path)
