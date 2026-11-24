"""POST /api/account/delete-data and /api/account/delete (docs/spec.md, Settings, Deleting data).

The doubles are the ones of every unit test (AppEnv in tests/support/app_env.py and
tests/conftest.py): a fake backup, a fake bw serve, a fast scrypt stand-in.
"""

import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from bwtidy.backups.files import BackupError
from bwtidy.protocol import DELETE_MARKER, REQUEST_HEADER
from tests.support.app_env import AppEnv
from tests.support.queue_helpers import listing, pending_ui_edit


@pytest.fixture
def env(tmp_path: Path) -> Iterator[AppEnv]:
    with AppEnv(tmp_path) as env:
        env.unlock()
        env.client.headers[REQUEST_HEADER] = "1"
        yield env


def test_a_pending_entry_is_409_with_the_counts_when_no_choice_was_made(env: AppEnv):
    pending_ui_edit(env)
    response = env.client.post("/api/account/delete-data", json={"pending": None})
    assert response.status_code == 409
    assert response.json() == {"pending": 1, "mcpPending": 0}
    ## nothing was deleted
    assert env.backup_files()
    assert listing(env, "pending")


def test_write_commits_the_pending_edits_first(env: AppEnv):
    pending_ui_edit(env)
    response = env.client.post("/api/account/delete-data", json={"pending": "write"})
    assert response.status_code == 200, response.text
    ## the edit was written before everything went
    assert env.item("login-1")["login"]["username"] == "queued@x.test"
    assert listing(env) == []
    assert env.change_log() == []
    assert env.backup_files() == []


def test_a_failed_commit_deletes_nothing(env: AppEnv):
    before = env.backup_files()
    pending = pending_ui_edit(env)
    ## a change elsewhere, so the login's backup cannot be reused and a fresh one must be taken
    env.item("card-1")["revisionDate"] = "2026-10-26T16:00:00.000Z"
    env.backup_error = BackupError("personal export", "bw exited with 1")
    response = env.client.post("/api/account/delete-data", json={"pending": "write"})
    assert response.status_code == 409, response.text
    assert "could not be written" in response.json()["detail"]
    ## nothing was deleted and the edit is still pending
    assert env.backup_files() == before
    assert env.item("login-1")["login"]["username"] != "queued@x.test"
    assert [row["id"] for row in listing(env, "pending")] == [pending]


def test_delete_writes_the_marker_and_ends_the_session(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    logged_out: list[bool] = []

    async def fake_log_out() -> int:
        logged_out.append(True)
        return 0

    monkeypatch.setattr(env.app.state.bw.bw_login, "log_out", fake_log_out)
    response = env.client.post("/api/account/delete", json={"pending": None})
    assert response.status_code == 204, response.text
    assert "bwtidy_session=" in response.headers["set-cookie"]
    marker = env.data_dir / DELETE_MARKER
    assert marker.is_file()
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert logged_out == [True]
    ## the session ended, so the worker exits
    assert env.client.get("/api/session").json()["state"] == "locked"
    assert env.backup_files() == []


def test_the_endpoints_need_a_session(env: AppEnv):
    env.client.cookies.clear()
    for path in ("/api/account/delete-data", "/api/account/delete"):
        assert env.client.post(path, json={"pending": None}).status_code == 401


def test_the_endpoints_need_the_request_header(env: AppEnv):
    del env.client.headers[REQUEST_HEADER]
    for path in ("/api/account/delete-data", "/api/account/delete"):
        assert env.client.post(path, json={"pending": None}).status_code == 403
    ## nothing was deleted
    assert env.backup_files()
    assert not (env.data_dir / DELETE_MARKER).exists()


def test_a_worker_drops_a_marker_an_earlier_worker_left_at_its_start(tmp_path: Path):
    ## a worker started before the gateway saw the old one exit keeps the account
    env = AppEnv(tmp_path, fake_backups=False)
    marker = env.data_dir / DELETE_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(b"")
    with env:
        assert not marker.exists()
