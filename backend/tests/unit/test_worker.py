"""The worker's lifetime (docs/spec.md, Worker mode): the exit after a session, the idle exit and
the pending marker."""

import asyncio
import dataclasses
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from mcp import Client

import bwtidy.mcp.server as mcp_server
from bwtidy.config import HEARTBEAT_TIMEOUT
from bwtidy.protocol import LOGIN_HEADER, REQUEST_HEADER, WORKER_GONE
from bwtidy.protocol import SESSION_COOKIE as COOKIE
from tests.support.login_env import CODE, EMAIL, IDLE_EXIT, OTP_PROMPT, SERVER, CliEnv, make_env


@pytest.fixture
def env(tmp_path: Path) -> Iterator[CliEnv]:
    yield from make_env(tmp_path, exit_after_session=True, idle_exit=IDLE_EXIT)


@pytest.fixture
def plain(tmp_path: Path) -> Iterator[CliEnv]:
    """The single-user app: none of the worker settings set."""
    yield from make_env(tmp_path)


def test_the_worker_exits_after_a_logout(env: CliEnv):
    assert env.login().status_code == 200
    assert env.exits == []
    assert env.client.delete("/api/session").status_code == 204
    assert env.exits == [True]
    ## the queued edits were committed and bw serve locked before the exit was asked for
    assert not env.bw.unlocked


def test_health_answers_503_once_the_worker_exits(env: CliEnv):
    assert env.client.get("/api/health").status_code == 200
    assert env.login().status_code == 200
    assert env.client.delete("/api/session").status_code == 204
    response = env.client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["status"] == "exiting"


def test_a_login_during_the_session_end_is_sent_to_the_next_worker(env: CliEnv):
    """The old page's log out still runs when the new page opens the account and unlocks."""
    assert env.login().status_code == 200
    session = env.app.state.bw.session
    ending = threading.Event()
    gate = asyncio.Event()

    async def slow_end() -> None:
        ending.set()
        await gate.wait()

    session.hooks = dataclasses.replace(session.hooks, before_end=slow_end)
    with ThreadPoolExecutor(max_workers=2) as pool:
        logout = pool.submit(env.client.delete, "/api/session")
        assert ending.wait(15)
        try:
            ## the exit counts as decided already, so the gateway hands out no doomed worker
            health = env.client.get("/api/health")
            login = pool.submit(env.login)
            ## lets the login reach the session's lock, either order must end the same
            time.sleep(0.2)
        finally:
            env.call(gate.set)
        assert logout.result(15).status_code == 204
        answer = login.result(15)
    assert (health.status_code, health.json()["status"]) == (503, "exiting")
    assert answer.status_code == 502
    assert answer.json()["detail"] == WORKER_GONE
    assert not session.active
    assert env.exits == [True]


def test_a_login_after_the_idle_exit_is_decided_starts_no_session(env: CliEnv):
    env.clock.advance(IDLE_EXIT)
    env.worker.check_idle()
    answer = env.login()
    assert answer.status_code == 502
    assert answer.json()["detail"] == WORKER_GONE
    assert not env.app.state.bw.session.active


def test_the_worker_exits_after_a_session_timed_out(env: CliEnv):
    assert env.login().status_code == 200
    env.clock.advance(HEARTBEAT_TIMEOUT + 1)
    env.call(env.app.state.bw.session.check_timeouts)
    assert env.exits == [True]


def test_a_new_login_replacing_the_session_does_not_exit(env: CliEnv):
    assert env.login().status_code == 200
    assert env.login().status_code == 200
    assert env.exits == []


def test_without_the_session_cookie_a_browser_neither_ends_the_session_nor_logs_out(
    env: CliEnv, tmp_path: Path
):
    """A routing cookie is all anyone who knows the server and email gets from the gateway."""
    assert env.login().status_code == 200
    token = env.client.cookies[COOKIE]
    env.client.cookies.clear()
    assert env.login("wrong").status_code == 401
    response = env.client.post("/api/bw-logout", headers={REQUEST_HEADER: "1"})
    assert response.status_code == 401
    assert env.exits == []
    assert env.bw.unlocked
    argv_log = tmp_path / "argv.log"
    logged_out = argv_log.exists() and '"logout"' in argv_log.read_text()
    assert not logged_out
    env.client.cookies.set(COOKIE, token)
    assert env.client.get("/api/session").json()["state"] == "unlocked"
    assert env.client.get("/api/health").status_code == 200


def test_an_mcp_end_session_answers_and_then_the_worker_exits(
    env: CliEnv, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(mcp_server, "ANSWER_GRACE", 0.0)
    assert env.login().status_code == 200
    assert env.client.post("/api/mcp/token").status_code == 201
    assert env.client.post("/api/mcp", json={"enabled": True}).status_code == 200
    mcp = env.app.state.bw.mcp

    async def end() -> str:
        async with Client(mcp) as client:
            result = await client.call_tool("end_session", {})
        await mcp.settle()
        assert not result.is_error
        return "".join(getattr(c, "text", "") for c in result.content)

    assert env.client.portal is not None
    assert env.client.portal.call(end) == "The bwtidy session ends now."
    assert env.exits == [True]
    assert not env.bw.unlocked
    assert env.client.get("/api/health").status_code == 503


def test_the_session_end_runs_its_work_before_the_exit(env: CliEnv):
    order: list[str] = []
    session = env.app.state.bw.session

    async def before_end() -> None:
        order.append("before_end")

    session.hooks = dataclasses.replace(session.hooks, before_end=before_end)
    env.app.state.bw.worker.request_exit = lambda: order.append(
        "exit, bw locked" if not env.bw.unlocked else "exit, bw unlocked"
    )
    assert env.login().status_code == 200
    env.client.delete("/api/session")
    assert order == ["before_end", "exit, bw locked"]


def test_the_single_user_app_never_exits(plain: CliEnv):
    assert plain.login().status_code == 200
    plain.client.delete("/api/session")
    plain.clock.advance(IDLE_EXIT * 10)
    plain.worker.check_idle()
    assert plain.exits == []


def test_the_worker_exits_when_no_session_started_within_the_idle_time(env: CliEnv):
    env.clock.advance(IDLE_EXIT - 1)
    env.worker.check_idle()
    assert env.exits == []
    env.clock.advance(1)
    env.worker.check_idle()
    assert env.exits == [True]
    ## asked once only
    env.worker.check_idle()
    assert env.exits == [True]


def test_a_running_session_keeps_the_worker_past_the_idle_time(env: CliEnv):
    assert env.login().status_code == 200
    env.client.post("/api/session/heartbeat")
    env.clock.advance(IDLE_EXIT)
    env.client.post("/api/session/heartbeat")
    env.worker.check_idle()
    assert env.exits == []


def test_a_wrong_password_does_not_stop_the_idle_exit(env: CliEnv):
    assert env.login("wrong").status_code == 401
    assert env.exits == []
    env.clock.advance(IDLE_EXIT)
    env.worker.check_idle()
    assert env.exits == [True]


@pytest.mark.bw_cli
def test_a_running_bw_login_keeps_the_worker_past_the_idle_time(tmp_path: Path):
    for env in make_env(tmp_path, exit_after_session=True, idle_exit=IDLE_EXIT):
        env.bw.logged_in = False
        ## late enough that the idle time passes long before the login's own 10 minutes
        env.clock.advance(IDLE_EXIT - 1)
        assert env.start(server=SERVER, email=EMAIL).status_code == 200
        env.wait_for_text(OTP_PROMPT)
        env.clock.advance(1)
        env.worker.check_idle()
        assert env.exits == []
        ## after the CLI exited 0 the login waits for its finish, which still counts
        assert env.type_text(CODE).status_code == 204
        assert env.wait_for_exit()["exitCode"] == 0
        env.worker.check_idle()
        assert env.exits == []
        response = env.client.delete("/api/bw-login", headers={LOGIN_HEADER: env.token or ""})
        assert response.status_code == 204
        env.worker.check_idle()
        assert env.exits == [True]


def test_the_pending_marker_is_deleted_after_the_first_unlock_only(tmp_path: Path):
    marker = tmp_path / "pending"
    marker.touch()
    for env in make_env(tmp_path, pending_marker=marker):
        assert env.login("wrong").status_code == 401
        assert marker.exists()
        assert env.login().status_code == 200
        assert not marker.exists()
        marker.touch()
        assert env.login().status_code == 200
        assert marker.exists()


def test_a_missing_pending_marker_is_no_error(tmp_path: Path):
    for env in make_env(tmp_path, pending_marker=tmp_path / "pending"):
        assert env.login().status_code == 200
