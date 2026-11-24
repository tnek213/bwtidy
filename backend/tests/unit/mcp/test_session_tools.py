"""The session tools: end_session and log_out_of_bitwarden answer first, then end the session."""

from collections.abc import Iterator
from pathlib import Path

import pytest

import bwtidy.bw.login as bwlogin
from tests.support.app_env import AppEnv
from tests.support.mcp_helpers import call, disabled, enable, mcp_env, settle, text


@pytest.fixture
def env(tmp_path: Path) -> Iterator[AppEnv]:
    yield from mcp_env(tmp_path)


def test_end_session_answers_first_and_then_ends_the_session(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    enable(env)
    session = env.app.state.bw.session
    events: list[str] = []
    logout = session.logout

    async def recording_logout() -> None:
        events.append("end")
        await logout()

    monkeypatch.setattr(session, "logout", recording_logout)
    result = call(env, "end_session")
    events.append("answer")
    assert not result.is_error
    assert text(result) == "The bwtidy session ends now."
    settle(env)
    assert events == ["answer", "end"]
    assert env.client.get("/api/session").json()["state"] == "locked"
    assert not env.bw.unlocked
    assert disabled(call(env, "list_folders"))


def test_log_out_of_bitwarden_ends_the_session_and_then_logs_the_cli_out(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    enable(env)
    session = env.app.state.bw.session
    events: list[str] = []

    async def fake_logout(bw_bin: str) -> int:
        events.append("cli logout while active" if session.active else "cli logout")
        return 0

    monkeypatch.setattr(bwlogin, "run_logout", fake_logout)
    result = call(env, "log_out_of_bitwarden")
    events.append("answer")
    assert not result.is_error
    assert "the Bitwarden CLI logs out" in text(result)
    settle(env)
    assert events == ["answer", "cli logout"]
    assert env.client.get("/api/session").json()["state"] == "locked"
    assert disabled(call(env, "log_out_of_bitwarden"))
    settle(env)
    assert events == ["answer", "cli logout"]
