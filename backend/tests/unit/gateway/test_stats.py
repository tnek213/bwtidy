"""The usage stats next to the proxy (docs/spec.md, Usage stats): what one request that reaches a
worker leaves in stats.db, and that nothing a person typed ever does."""

import hashlib
import json
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from bwtidy.protocol import ACCOUNT_COOKIE as COOKIE
from bwtidy.protocol import STATS_ERROR_HEADER
from bwtidy.stats import db as stats_db
from tests.support.gateway_env import EMAIL, GatewayEnv, bearer, make_env, mcp_token
from tests.support.stats_env import FAILING_TOOL, MARKER, StatsEnv, make_stats_env


@pytest.fixture
def stats(tmp_path: Path) -> Iterator[StatsEnv]:
    yield from make_stats_env(tmp_path)


def rpc(method: str, id: object = 1, **params: object) -> dict[str, object]:
    message: dict[str, object] = {"jsonrpc": "2.0", "method": method, "params": params}
    if id is not None:
        message["id"] = id
    return message


def call(tool: str, id: object = 1, **arguments: object) -> dict[str, object]:
    return rpc("tools/call", id, name=tool, arguments=arguments)


def post_mcp(env: GatewayEnv, token: str, body: object):
    return env.client.post("/mcp", json=body, headers=bearer(token))


def events(env: StatsEnv) -> list[tuple[str, str, int, int]]:
    return env.rows("SELECT channel, operation, ok, status FROM events ORDER BY rowid")


def test_a_ui_request_counts_by_its_route_template(stats: StatsEnv):
    token = stats.started()
    answer = stats.request("PUT", f"/api/items/{MARKER}-id", {COOKIE: token}, json={})
    assert answer.status_code == 200
    stats.request("GET", "/api/grid?search=x", {COOKIE: token})
    stats.request("GET", "/api/nowhere", {COOKIE: token})
    stats.flush()
    assert events(stats) == [
        ("ui", "POST /api/bw-login", 1, 200),
        ("ui", "PUT /api/items/{item_id}", 1, 200),
        ("ui", "GET /api/grid", 1, 200),
        ("ui", "other", 1, 200),
    ]
    (account,) = {row[0] for row in stats.rows("SELECT account FROM events")}
    assert len(account) == 16
    assert EMAIL not in account


def test_requests_the_gateway_answers_itself_count_nothing(stats: StatsEnv):
    stats.client.get("/api/health")
    stats.open()
    stats.session(None)
    stats.recorder.flush()
    assert stats.rows("SELECT count(*) FROM events") == [(0,)]


def test_mcp_counts_each_call_by_its_tool_or_method_and_never_its_arguments(stats: StatsEnv):
    stats.started()
    token = mcp_token(stats)
    answer = post_mcp(stats, token, call("list_items", search=MARKER))
    assert answer.status_code == 200
    assert answer.json()["result"]["content"][0]["text"] == f"{MARKER} item"
    batch = [
        rpc("initialize", 1),
        call(FAILING_TOOL, 2, new_name=MARKER),
        call(f"{MARKER}_tool", 3),
        rpc(f"{MARKER}/method", 4),
        rpc("notifications/initialized", None),
    ]
    assert post_mcp(stats, token, batch).status_code == 200
    stats.flush()
    assert events(stats)[1:] == [
        ("mcp", "list_items", 1, 200),
        ("mcp", "initialize", 1, 200),
        ("mcp", FAILING_TOOL, 0, 200),
        ("mcp", "other", 1, 200),
        ("mcp", "other", 1, 200),
        ("mcp", "notifications/initialized", 1, 200),
    ]


def test_an_mcp_body_reaches_the_worker_unchanged(stats: StatsEnv):
    stats.started()
    token = mcp_token(stats)
    body = json.dumps(call("list_items", search=MARKER * 100_000)).encode()
    answer = stats.client.post(
        "/mcp", content=body, headers={**bearer(token), "Content-Type": "application/json"}
    )
    assert answer.status_code == 200
    assert answer.json()["result"]["received"] == hashlib.sha256(body).hexdigest()
    stats.flush()
    assert events(stats)[1:] == [("mcp", "list_items", 1, 200)]


def test_a_worker_error_is_kept_by_type_and_its_header_never_reaches_the_browser(
    stats: StatsEnv,
):
    token = stats.started()
    answer = stats.request("GET", "/api/fail", {COOKIE: token})
    assert answer.status_code == 500
    assert STATS_ERROR_HEADER.lower() not in answer.headers
    stats.request("GET", "/api/fail", {COOKIE: token})
    stats.flush()
    assert events(stats)[1:] == [("ui", "GET /api/fail", 0, 500)] * 2
    ((kind, frames, count, channel, operation),) = stats.rows(
        "SELECT exception_type, frames, count, channel, operation FROM errors"
    )
    assert (kind, json.loads(frames), count) == ("KeyError", [], 2)
    assert (channel, operation) == ("ui", "GET /api/fail")


def test_the_error_header_is_stripped_with_the_stats_off_too(tmp_path: Path):
    for env in make_stats_env(tmp_path, stats=False):
        token = env.started()
        answer = env.request("GET", "/api/fail", {COOKIE: token})
        assert answer.status_code == 500
        assert STATS_ERROR_HEADER.lower() not in answer.headers


def test_no_marker_reaches_the_stats_file(stats: StatsEnv):
    token = stats.started()
    cookies = {COOKIE: token}
    stats.request("PUT", f"/api/items/{MARKER}", cookies, json={"name": f"{MARKER} item"})
    stats.request("GET", f"/api/grid?search={MARKER}", cookies)
    stats.request("GET", f"/api/{MARKER}/folder", cookies)
    stats.request("GET", "/api/fail", cookies)
    mcp = mcp_token(stats)
    post_mcp(stats, mcp, call("list_items", MARKER, search=MARKER))
    post_mcp(stats, mcp, [call(FAILING_TOOL, 7, new_name=MARKER), rpc(MARKER, f"{MARKER}-id")])
    post_mcp(stats, mcp, call(MARKER, 8))
    stats.client.post("/mcp", content=f"{MARKER} not json".encode(), headers=bearer(mcp))
    stats.flush()
    dump = stats.dump()
    assert stats.rows("SELECT count(*) FROM errors") == [(1,)]
    assert MARKER not in dump
    assert EMAIL not in dump
    assert "vaultwarden" not in dump


def test_a_failing_stats_write_never_fails_a_request(
    stats: StatsEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    def broken(*_args: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(stats_db, "write", broken)
    token = stats.started()
    caplog.set_level(logging.WARNING, logger="bwtidy.stats.recorder")
    assert stats.request("GET", "/api/grid", {COOKIE: token}).status_code == 200
    stats.flush()
    assert stats.request("GET", "/api/grid", {COOKIE: token}).status_code == 200
    stats.flush()
    lines = [r.getMessage() for r in caplog.records if r.name == "bwtidy.stats.recorder"]
    assert lines == ["Writing the usage stats failed: OperationalError disk I/O error"]


def test_a_wrapper_labels_the_accounts_and_adds_its_numbers(tmp_path: Path):
    def label(key) -> str:
        return "visitor-1" if key.email == EMAIL else "someone"

    for env in make_stats_env(tmp_path, stats_account=label, stats_extra=lambda: {"sessions": 3}):
        env.started()
        env.recorder.sample()
        env.flush()
        assert env.rows("SELECT DISTINCT account FROM events") == [("visitor-1",)]
        assert env.rows('SELECT active, "limit", waiting, extra FROM capacity') == [
            (1, 8, 0, '{"sessions":3}')
        ]


def test_a_capacity_sample_counts_the_starts_turned_away(tmp_path: Path):
    for env in make_stats_env(tmp_path, max_workers=1):
        env.started()
        refused = env.open(email="other@example.test")
        answer = env.click(env.cookie(refused))
        assert answer.status_code == 429
        env.recorder.sample()
        env.recorder.sample()
        env.recorder.flush()
        assert env.rows('SELECT active, "limit", waiting FROM capacity') == [
            (1, 1, 1),
            (1, 1, 0),
        ]


def test_with_the_stats_off_nothing_is_recorded(tmp_path: Path):
    for env in make_env(tmp_path):
        assert env.gateway.recorder is None
        token = env.started()
        answer = env.session(token)
        assert "BWTIDY_STATS_REPORT" not in answer.json()["env"]
    assert not (tmp_path / stats_db.STATS_FILE).exists()
    assert list(tmp_path.glob("*.db*")) == []


def test_the_stats_file_is_made_at_the_start_mode_0600(stats: StatsEnv, tmp_path: Path):
    assert stats.stats_db == tmp_path / "stats.db"
    assert stats.stats_db.stat().st_mode & 0o777 == 0o600


def test_a_stats_file_inside_the_accounts_refuses_the_start(tmp_path: Path):
    with pytest.raises(ValueError, match="BWTIDY_STATS_DB must be outside BWTIDY_ACCOUNTS_DIR"):
        next(make_stats_env(tmp_path, stats_db=tmp_path / "a" / "stats.db"))


def test_a_newer_stats_file_stops_the_start_unchanged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    path = tmp_path / "stats.db"
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {len(stats_db.MIGRATIONS) + 1}")
    before = path.read_bytes()
    with pytest.raises(stats_db.StatsError):
        next(make_stats_env(tmp_path))
    assert path.read_bytes() == before
    assert f"{path} is at version {len(stats_db.MIGRATIONS) + 1}" in caplog.text
