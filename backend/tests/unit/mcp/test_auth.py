"""The MCP token: its file with the hash, the expiry, the creation time and the last use, and
the check in front of /mcp that refuses a request without the right token."""

import hashlib
import json
import logging
import sqlite3
import stat
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bwtidy.db import Database
from bwtidy.mcp.auth import LAST_USED_SETTING, McpToken
from bwtidy.mcp.texts import DISABLED
from bwtidy.mcp_token import EXPIRED, Check, parse_token_file, token_digest
from bwtidy.protocol import DB_FILE, TOKEN_FILE
from tests.support.app_env import AppEnv
from tests.support.mcp_helpers import enable, mcp_env
from tests.support.mcp_http import (
    authorized,
    create_token,
    http_call,
    raw_post,
    refused,
    token_clock,
)

START = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def test_a_token_expires_after_its_days(tmp_path: Path):
    clock = Clock()
    mcp = McpToken(tmp_path, now=clock)
    token, record = mcp.create(30)
    assert (record.created_at, record.expires_at) == (START, START + timedelta(days=30))
    lines = (tmp_path / TOKEN_FILE).read_text().splitlines()
    assert lines == [token_digest(token).decode(), "2026-12-08T12:00:00+00:00", START.isoformat()]
    assert stat.S_IMODE((tmp_path / TOKEN_FILE).stat().st_mode) == 0o600
    clock.now = START + timedelta(days=30, seconds=-1)
    assert mcp.check(token) is Check.ACCEPTED
    clock.now = START + timedelta(days=30)
    assert mcp.check(token) is Check.EXPIRED
    ## a wrong token is unknown, whether the right one expired or not
    assert mcp.check(token + "x") is Check.UNKNOWN
    assert mcp.check("") is Check.UNKNOWN


def test_a_token_without_days_never_expires(tmp_path: Path):
    clock = Clock()
    mcp = McpToken(tmp_path, now=clock)
    token, record = mcp.create(None)
    assert record.expires_at is None
    assert (tmp_path / TOKEN_FILE).read_text().splitlines()[1] == "never"
    clock.now = START + timedelta(days=100 * 365)
    assert mcp.check(token) is Check.ACCEPTED


def test_a_file_of_one_line_and_a_file_of_two_lines_hold_no_token(tmp_path: Path):
    digest = token_digest("old")
    for content in (digest + b"\n", digest + b"\nnever\n"):
        assert parse_token_file(content) is None, content
        (tmp_path / TOKEN_FILE).write_bytes(content)
        assert McpToken(tmp_path).check("old") is Check.UNKNOWN


def test_a_file_that_cannot_be_read_holds_no_token(tmp_path: Path):
    digest = token_digest("t")
    for content in (b"", b"\n", digest + b"\nsoon\n", digest + b"\n2026-12-08T12:00:00\n", b"\xff"):
        assert parse_token_file(content) is None, content
    ## a creation time that cannot be read makes it no token too
    content = digest + b"\nnever\nyesterday\n"
    assert parse_token_file(content) is None
    (tmp_path / TOKEN_FILE).write_bytes(content)
    assert McpToken(tmp_path).check("t") is Check.UNKNOWN


def test_the_last_use_is_kept_once_a_minute_and_survives_a_restart(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    clock = Clock()
    mcp = McpToken(tmp_path, db, now=clock)
    mcp.create()
    assert mcp.last_used() is None
    mcp.note_used()
    clock.now += timedelta(seconds=30)
    mcp.note_used()
    assert mcp.last_used() == START
    assert db.get_setting(LAST_USED_SETTING) == START.isoformat()
    clock.now = START + timedelta(minutes=1)
    mcp.note_used()
    assert McpToken(tmp_path, db, now=clock).last_used() == clock.now
    mcp.remove()
    assert mcp.last_used() is None
    assert db.get_setting(LAST_USED_SETTING) is None
    db.close()


@pytest.fixture
def env(tmp_path: Path) -> Iterator[AppEnv]:
    yield from mcp_env(tmp_path)


def test_without_a_token_file_every_request_is_refused(env: AppEnv):
    enable(env)
    assert env.client.delete("/api/mcp/token").status_code == 204
    assert not (env.data_dir / TOKEN_FILE).exists()
    assert refused(raw_post(env, {}))
    assert refused(raw_post(env, {"Authorization": "Bearer "}))
    assert refused(raw_post(env, {"Authorization": "Bearer anything"}))
    assert refused(env.client.get("/mcp"))


def test_a_missing_or_wrong_token_is_refused(env: AppEnv, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG)
    enable(env)
    token = create_token(env)
    assert refused(raw_post(env, {}))
    assert refused(raw_post(env, {"Authorization": "Bearer wrong"}))
    assert refused(raw_post(env, {"Authorization": f"Basic {token}"}))
    assert refused(raw_post(env, {"Authorization": token}))
    assert refused(raw_post(env, {"Authorization": f"Bearer {token}x"}))
    assert raw_post(env, {"Authorization": f"bearer {token}"}).status_code == 200
    assert refused(raw_post(env, {}))
    assert raw_post(env, {}).headers["WWW-Authenticate"] == "Bearer"
    ## before the SDK: no host check, no session, no tool runs
    assert refused(raw_post(env, {"Host": "attacker.example"}))
    found = token in caplog.text
    assert not found, "the token is in the log"


def test_the_right_token_reaches_the_tools_and_the_toggle_still_decides(env: AppEnv):
    token = create_token(env)
    assert http_call(env, token) == DISABLED
    enable(env)
    assert "folders" in json.loads(http_call(env, token))
    enable(env, False)
    assert http_call(env, token) == DISABLED


def test_a_new_token_replaces_the_old_one(env: AppEnv):
    enable(env)
    old = create_token(env)
    new = create_token(env)
    assert old != new
    assert refused(raw_post(env, {"Authorization": f"Bearer {old}"}))
    assert raw_post(env, {"Authorization": f"Bearer {new}"}).status_code == 200


def test_remove_refuses_every_request_again(env: AppEnv):
    enable(env)
    token = create_token(env)
    assert env.client.get("/api/session").json()["mcpToken"] is True
    assert env.client.delete("/api/mcp/token").status_code == 204
    assert not (env.data_dir / TOKEN_FILE).exists()
    assert env.client.get("/api/session").json()["mcpToken"] is False
    assert refused(raw_post(env, {"Authorization": f"Bearer {token}"}))
    ## removing what is not there is fine
    assert env.client.delete("/api/mcp/token").status_code == 204


def test_the_hash_file_holds_only_the_hash_and_the_times(env: AppEnv):
    token = create_token(env)
    path = env.data_dir / TOKEN_FILE
    stored = path.read_text()
    digest, expires, created = stored.splitlines()
    assert digest == hashlib.sha256(token.encode()).hexdigest()
    assert datetime.fromisoformat(expires) - datetime.fromisoformat(created) == timedelta(days=90)
    assert stored.endswith("\n")
    found = token in stored
    assert not found, "the token itself is stored"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    ## no temporary file left next to it
    assert [p.name for p in env.data_dir.iterdir() if TOKEN_FILE in p.name] == [TOKEN_FILE]


def test_the_token_routes_need_a_session_and_answer_the_token_once(env: AppEnv):
    assert env.client.get("/api/session").json()["mcpToken"] is False
    token = create_token(env)
    session = env.client.get("/api/session").json()
    assert session["mcpToken"] is True
    found = token in json.dumps(session)
    assert not found, "the session answer holds the token"
    env.client.cookies.clear()
    assert env.client.get("/api/session").json()["mcpToken"] is False
    assert env.client.post("/api/mcp/token").status_code == 401
    assert env.client.delete("/api/mcp/token").status_code == 401
    assert (env.data_dir / TOKEN_FILE).exists()


def test_a_token_lives_the_days_chosen_and_then_gets_its_own_401(env: AppEnv):
    enable(env)
    clock = token_clock(env)
    response = env.client.post("/api/mcp/token", json={"days": 7})
    assert response.status_code == 201
    answer = response.json()
    assert set(answer) == {"token", "createdAt", "expiresAt"}
    created = datetime.fromisoformat(answer["createdAt"])
    assert created == clock.now
    assert datetime.fromisoformat(answer["expiresAt"]) == created + timedelta(days=7)
    token = answer["token"]
    clock.now += timedelta(days=7, seconds=-1)
    assert authorized(env, token) == 200
    clock.now += timedelta(seconds=1)
    response = raw_post(env, {"Authorization": f"Bearer {token}"})
    assert (response.status_code, response.text) == (401, EXPIRED)
    assert response.headers["WWW-Authenticate"] == "Bearer"
    ## a wrong token is still unknown, not expired
    assert refused(raw_post(env, {"Authorization": f"Bearer {token}x"}))
    ## the session still has a token, the UI offers to replace it
    assert env.client.get("/api/session").json()["mcpToken"] is True


def test_a_token_without_an_expiry_works_for_good(env: AppEnv):
    enable(env)
    clock = token_clock(env)
    response = env.client.post("/api/mcp/token", json={"days": None})
    assert response.status_code == 201
    assert response.json()["expiresAt"] is None
    clock.now += timedelta(days=4000)
    assert authorized(env, response.json()["token"]) == 200
    assert env.client.get("/api/mcp/token").json()["expiresAt"] is None


def test_the_days_are_checked(env: AppEnv):
    for days in (0, 366, -1, 1.5, "7", True):
        response = env.client.post("/api/mcp/token", json={"days": days})
        assert response.status_code == 422, days
    assert not (env.data_dir / TOKEN_FILE).exists()
    for days in (1, 365):
        assert env.client.post("/api/mcp/token", json={"days": days}).status_code == 201
    ## no body and an empty one take the default of 90 days
    for body in (None, {}):
        answer = env.client.post("/api/mcp/token", json=body).json()
        span = datetime.fromisoformat(answer["expiresAt"]) - datetime.fromisoformat(
            answer["createdAt"]
        )
        assert span == timedelta(days=90)


def test_get_token_answers_the_times_and_never_the_token(env: AppEnv):
    assert env.client.get("/api/mcp/token").status_code == 404
    clock = token_clock(env)
    created = env.client.post("/api/mcp/token", json={"days": 30}).json()
    info = env.client.get("/api/mcp/token").json()
    assert info == {
        "createdAt": created["createdAt"],
        "expiresAt": created["expiresAt"],
        "lastUsedAt": None,
    }
    digest = hashlib.sha256(created["token"].encode()).hexdigest()
    found = created["token"] in json.dumps(info) or digest in json.dumps(info)
    assert not found, "the answer holds the token or its hash"
    clock.now += timedelta(hours=1)
    assert authorized(env, created["token"]) == 200
    assert env.client.get("/api/mcp/token").json()["lastUsedAt"] == clock.now.isoformat()
    env.client.cookies.clear()
    assert env.client.get("/api/mcp/token").status_code == 401


def test_the_last_use_is_recorded_at_most_once_a_minute(env: AppEnv):
    clock = token_clock(env)
    token = create_token(env)
    first = clock.now
    assert authorized(env, token) == 200
    clock.now += timedelta(seconds=59)
    assert authorized(env, token) == 200
    assert env.client.get("/api/mcp/token").json()["lastUsedAt"] == first.isoformat()
    clock.now += timedelta(seconds=1)
    assert authorized(env, token) == 200
    assert env.client.get("/api/mcp/token").json()["lastUsedAt"] == clock.now.isoformat()
    ## a refused request is no use
    clock.now += timedelta(minutes=5)
    assert refused(raw_post(env, {"Authorization": "Bearer wrong"}))
    assert env.client.get("/api/mcp/token").json()["lastUsedAt"] != clock.now.isoformat()
    ## kept in the SQLite settings, so a new worker knows it
    stored = sqlite_setting(env, LAST_USED_SETTING)
    assert stored == (clock.now - timedelta(minutes=5)).isoformat()
    ## a new token has not been used yet
    create_token(env)
    assert env.client.get("/api/mcp/token").json()["lastUsedAt"] is None
    assert sqlite_setting(env, LAST_USED_SETTING) is None


def sqlite_setting(env: AppEnv, key: str) -> str | None:
    connection = sqlite3.connect(env.data_dir / DB_FILE)
    try:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    finally:
        connection.close()
    return row[0] if row else None
