"""Deleting the account's data (docs/spec.md, Deleting data) through POST /api/account/delete-data:
what the wipe deletes, the pending entries and backups it keeps, and a write that runs."""

import asyncio
import json
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bwtidy.db import DATE_FORMAT
from bwtidy.protocol import REQUEST_HEADER
from tests.support.app_env import AppEnv
from tests.support.queue_helpers import listing, pending_ui_edit


@pytest.fixture
def env(tmp_path: Path) -> Iterator[AppEnv]:
    with AppEnv(tmp_path) as env:
        env.unlock()
        env.client.headers[REQUEST_HEADER] = "1"
        yield env


def add_failed_entry(env: AppEnv) -> int:
    """A decided (failed) queue entry, the kind delete-data always removes."""
    return env.writes.db.add_queue_entry(
        "mcp",
        "Set login.username of 1 item",
        json.dumps(
            {"kind": "setLeaf", "itemId": "login-1", "column": "login.username", "value": "x"}
        ),
        status="failed",
        result=json.dumps({"error": "writeFailed", "detail": "boom", "backup": "none"}),
    )


def test_delete_data_removes_backups_change_log_and_decided_entries(env: AppEnv):
    env.post(env.set_leaf("login-1", "login.username", "written@x.test"))
    assert env.backup_files()
    assert env.change_log()
    failed = add_failed_entry(env)
    env.writes.db.set_setting(DATE_FORMAT, "iso")
    token, _ = env.app.state.bw.mcp_token.create(90)
    assert env.app.state.bw.mcp_token.exists()

    response = env.client.post("/api/account/delete-data", json={"pending": None})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["backups"] >= 1
    assert body["changeLog"] >= 1
    assert body["queue"] >= 1
    assert body["keptBackups"] == []

    assert env.backup_files() == []
    assert env.change_log() == []
    assert env.writes.db.backup_fingerprint(env.login_backup) is None
    assert env.writes.db.queue_entries(ids=[failed]) == []
    ## the settings and the MCP token stay
    assert env.writes.db.get_setting(DATE_FORMAT) == "iso"
    assert env.app.state.bw.mcp_token.exists()


def test_drop_deletes_the_pending_entries_too(env: AppEnv):
    pending = pending_ui_edit(env)
    response = env.client.post("/api/account/delete-data", json={"pending": "drop"})
    assert response.status_code == 200, response.text
    assert env.writes.db.queue_entries(ids=[pending]) == []
    assert listing(env) == []
    ## the queued value was never written to the vault
    assert env.item("login-1")["login"]["username"] != "queued@x.test"


def test_a_pending_restore_proposal_keeps_its_backup(env: AppEnv):
    kept = env.login_backup
    env.writes.db.add_queue_entry(
        "mcp",
        "Restore a change",
        json.dumps({"kind": "restoreItems", "backup": kept, "changeLogId": 1}),
        status="pending",
    )
    response = env.client.post("/api/account/delete-data", json={"pending": "write"})
    assert response.status_code == 200, response.text
    assert response.json()["keptBackups"] == [kept]
    ## the backup the restore reads stays, the proposal stays pending
    assert env.backup_files() == [kept]
    assert [row["source"] for row in listing(env, "pending")] == ["mcp"]


def test_a_direct_write_in_progress_is_409_and_deletes_nothing(env: AppEnv):
    before = env.backup_files()
    assert before
    logged = env.change_log()
    gate = asyncio.Event()
    env.bw.put_gate = gate
    portal = env.client.portal
    assert portal is not None
    with ThreadPoolExecutor(max_workers=1) as pool:
        write = pool.submit(
            env.client.post,
            "/api/operations",
            json={"operations": [env.set_leaf("login-1", "login.username", "direct@x.test")]},
        )
        try:
            deadline = time.monotonic() + 10
            while not env.bw.put_waiting:
                assert time.monotonic() < deadline, "the write never reached its PUT"
                time.sleep(0.01)
            assert env.writes.running
            response = env.client.post("/api/account/delete-data", json={"pending": None})
            assert response.status_code == 409, response.text
            assert response.json() == {"detail": "a write is running, try again"}
            ## nothing was deleted while the write was paused
            assert env.backup_files() == before
            assert env.change_log() == logged
        finally:
            portal.call(gate.set)
        assert write.result(10).status_code == 200
    env.settle()
    assert env.item("login-1")["login"]["username"] == "direct@x.test"
    assert not env.writes.running
    ## the next deletion goes through
    assert env.client.post("/api/account/delete-data", json={"pending": None}).status_code == 200
