"""The auto-commit of the UI's entries against the fake bw serve: on time or count, its status,
Commit now and the end of a session."""

import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import bwtidy.queue.autocommit as autocommit
from bwtidy import main
from bwtidy.protocol import DB_FILE
from tests.support.app_env import AppEnv
from tests.support.queue_helpers import (
    CHANGED,
    approve,
    auto_commit,
    commit_if_due,
    commit_now,
    enqueue_dict,
    enqueue_ui,
    quick_auto_commit,
    status,
    statuses,
    vault_unreachable,
)


def test_auto_commit_by_age(env: AppEnv):
    [entry_id] = enqueue_ui(env, env.set_leaf("login-1", "name", "Mail")).json()["ids"]
    before = status(env)
    assert before["pending"] == 1
    assert before["committing"] is False
    assert before["nextCommitAt"] is not None
    env.clock.advance(9)
    assert commit_if_due(env) is None
    assert statuses(env, entry_id) == ["pending"]
    env.clock.advance(1)
    outcome = commit_if_due(env)
    assert outcome is not None
    ## the login's backup held the vault as it was, and one of the vault after followed
    backup = env.login_backup
    assert len(env.backups) == 2
    assert outcome == {"applied": [entry_id], "failed": [], "pending": [], "backup": backup}
    assert env.item("login-1")["name"] == "Mail"
    assert statuses(env, entry_id) == ["applied"]
    assert [e["source"] for e in env.change_log()] == ["ui"]
    after = status(env)
    assert after["pending"] == 0
    assert after["nextCommitAt"] is None
    assert after["commitCount"] == before["commitCount"] + 1
    ## nothing pending, nothing happens
    assert commit_if_due(env) is None
    assert len(env.backups) == 2


def test_auto_commit_by_count_with_the_settings(env: AppEnv, tmp_path: Path):
    assert status(env) == {
        "afterSeconds": 10,
        "maxEntries": 20,
        "pending": 0,
        "committing": False,
        "nextCommitAt": None,
        "commitCount": 0,
        "phase": None,
    }
    response = env.client.put("/api/queue/auto-commit", json={"afterSeconds": 60, "maxEntries": 3})
    assert response.status_code == 200, response.text
    assert response.json()["afterSeconds"] == 60
    assert response.json()["maxEntries"] == 3
    for body in (
        {"afterSeconds": 0, "maxEntries": 3},
        {"afterSeconds": 3601, "maxEntries": 3},
        {"afterSeconds": 60, "maxEntries": 0},
        {"afterSeconds": 60, "maxEntries": 1001},
        {"afterSeconds": 60},
    ):
        assert env.client.put("/api/queue/auto-commit", json=body).status_code == 422
    assert status(env)["afterSeconds"] == 60
    ids = enqueue_ui(
        env,
        env.set_leaf("login-1", "name", "Mail"),
        env.set_leaf("note-3", "name", "Media"),
    ).json()["ids"]
    assert commit_if_due(env) is None
    ids += enqueue_ui(env, env.op("trashItem", "card-1")).json()["ids"]
    outcome = commit_if_due(env)
    assert outcome is not None
    assert outcome["applied"] == ids
    assert statuses(env, *ids) == ["applied"] * 3
    assert len(env.backups) == 2
    assert [i["id"] for i in env.bw.trashed] == ["card-1"]
    ## the settings survive a restart
    again = env.restarted()
    with again:
        again.unlock()
        assert status(again)["afterSeconds"] == 60
        assert status(again)["maxEntries"] == 3


def test_an_account_without_settings_waits_180_seconds(tmp_path: Path):
    with AppEnv(tmp_path, fake_backups=False) as env:
        env.unlock()
        before = status(env)
        assert before["afterSeconds"] == 180
        assert before["maxEntries"] == 20
        enqueue_ui(env, env.set_leaf("login-1", "name", "Mail"))
        next_at = datetime.fromisoformat(status(env)["nextCommitAt"])
        assert 170 <= (next_at - datetime.now(UTC)).total_seconds() <= 181


def test_commit_now(env: AppEnv):
    assert commit_now(env).json() == {"applied": [], "failed": [], "pending": [], "backup": None}
    ids = enqueue_ui(
        env, env.set_leaf("login-1", "name", "Mail"), env.set_leaf("note-3", "name", "Media")
    ).json()["ids"]
    response = commit_now(env)
    assert response.status_code == 200, response.text
    backup = env.login_backup
    assert response.json() == {"applied": ids, "failed": [], "pending": [], "backup": backup}
    assert env.item("login-1")["name"] == "Mail"
    assert env.item("note-3")["name"] == "Media"


def record_status_at_every_step(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[Any, ...]]:
    """The status's committing, phase, done and total at every bw serve request and every
    backup, each change once."""
    seen: list[tuple[Any, ...]] = []

    def record() -> None:
        answer = auto_commit(env).status()
        now = tuple(answer.get(key) for key in ("committing", "phase", "done", "total"))
        if not seen or seen[-1] != now:
            seen.append(now)

    handler = env.bw.handler
    backup = env.keeper.backup

    def hooked(request: Any) -> Any:
        record()
        return handler(request)

    async def backed_up(*args: Any, **kwargs: Any) -> Any:
        record()
        return await backup(*args, **kwargs)

    monkeypatch.setattr(env.bw, "handler", hooked)
    monkeypatch.setattr(env.keeper, "backup", backed_up)
    return seen


def test_the_status_says_what_a_commit_is_doing(env: AppEnv, monkeypatch: pytest.MonkeyPatch):
    ids = enqueue_ui(
        env, env.set_leaf("login-1", "name", "Mail"), env.set_leaf("note-3", "name", "Media")
    ).json()["ids"]
    ## a change elsewhere, so the login's backup cannot be reused
    env.item("card-1")["revisionDate"] = CHANGED
    seen = record_status_at_every_step(env, monkeypatch)
    response = commit_now(env)
    assert response.json()["applied"] == ids
    assert response.json()["backup"] != env.login_backup
    assert seen == [
        (True, "syncing", None, None),
        (True, "backingUp", None, None),
        (True, "syncing", None, None),
        (True, "writing", 0, 2),
        (True, "writing", 1, 2),
        (True, "reloading", None, None),
        ## the background backup after the commit
        (False, None, None, None),
    ]
    after = status(env)
    assert after["committing"] is False
    assert after["phase"] is None
    assert "done" not in after


def test_an_approve_counts_as_committing(env: AppEnv, monkeypatch: pytest.MonkeyPatch):
    mcp_id = enqueue_dict(env, env.set_leaf("login-1", "name", "Mail"), "rename mail")
    seen = record_status_at_every_step(env, monkeypatch)
    assert approve(env, mcp_id).json()["applied"] == [mcp_id]
    ## the login's backup is reused, so no step runs while the phase is backingUp
    assert seen == [
        (True, "syncing", None, None),
        (True, "writing", 0, 1),
        (True, "reloading", None, None),
        (False, None, None, None),
    ]
    assert status(env)["committing"] is False


def test_mcp_entries_are_never_committed_without_approval(env: AppEnv):
    env.client.put("/api/queue/auto-commit", json={"afterSeconds": 1, "maxEntries": 1})
    mcp_id = enqueue_dict(env, env.set_leaf("login-1", "name", "Mail"), "rename mail")
    env.clock.advance(60)
    assert commit_if_due(env) is None
    assert status(env)["pending"] == 0
    assert commit_now(env).json()["applied"] == []
    assert statuses(env, mcp_id) == ["pending"]
    ## a UI entry beside it is committed alone
    [ui_id] = enqueue_ui(env, env.set_leaf("note-3", "name", "Media")).json()["ids"]
    assert commit_now(env).json()["applied"] == [ui_id]
    assert statuses(env, mcp_id, ui_id) == ["pending", "applied"]
    assert env.item("login-1")["name"] == "Example Mail"


def test_a_stop_lets_a_running_commit_finish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(autocommit, "TICK", 0.01)
    with AppEnv(tmp_path) as env:
        env.unlock()
        quick_auto_commit(env)
        [entry_id] = enqueue_ui(env, env.set_leaf("login-1", "login.username", "queued")).json()[
            "ids"
        ]
        ## every bw serve call now takes a moment, so the stop falls inside the commit
        env.bw.delay = 0.05
        env.clock.advance(11)
        deadline = time.time() + 5
        while not auto_commit(env).committing and time.time() < deadline:
            time.sleep(0.005)
        assert auto_commit(env).committing
    assert env.item("login-1")["login"]["username"] == "queued"
    connection = sqlite3.connect(tmp_path / DB_FILE)
    [row_status] = connection.execute(
        "SELECT status FROM queue WHERE id = ?", (entry_id,)
    ).fetchone()
    connection.close()
    assert row_status == "applied"


def test_a_stop_lets_the_session_timeout_commit_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(autocommit, "TICK", 3600.0)
    monkeypatch.setattr(main, "CHECK_INTERVAL", 0.01)
    with AppEnv(tmp_path) as env:
        env.unlock()
        [entry_id] = enqueue_ui(env, env.set_leaf("login-1", "login.username", "queued")).json()[
            "ids"
        ]
        env.bw.delay = 0.05
        ## no heartbeat for 121 s: the watcher ends the session, which commits first
        env.clock.advance(121)
        deadline = time.time() + 5
        while not auto_commit(env).committing and time.time() < deadline:
            time.sleep(0.005)
        assert auto_commit(env).committing
    assert env.item("login-1")["login"]["username"] == "queued"
    connection = sqlite3.connect(tmp_path / DB_FILE)
    [row_status] = connection.execute(
        "SELECT status FROM queue WHERE id = ?", (entry_id,)
    ).fetchone()
    connection.close()
    assert row_status == "applied"


def test_ending_the_session_commits_the_pending_ui_edits_first(env: AppEnv):
    [a] = enqueue_ui(env, env.set_leaf("login-1", "name", "Mail")).json()["ids"]
    assert env.client.delete("/api/session").status_code == 204
    assert env.item("login-1")["name"] == "Mail"
    assert not env.bw.unlocked
    assert env.queue.db.queue_entries(ids=[a])[0]["status"] == "applied"
    ## the login's backup was reused, and the background one after the write was stopped
    ## with the session
    assert env.backups == [env.login_backup]
    ## the heartbeat timeout commits too
    env.unlock()
    [b] = enqueue_ui(env, env.set_leaf("note-3", "name", "Media")).json()["ids"]
    env.clock.advance(121)
    assert env.client.portal is not None
    env.client.portal.call(env.app.state.bw.session.check_timeouts)
    assert env.item("note-3")["name"] == "Media"
    assert env.queue.db.queue_entries(ids=[b])[0]["status"] == "applied"
    assert not env.bw.unlocked
    assert len(env.backups) == 2


def test_a_commit_that_cannot_run_at_the_end_leaves_the_edits_for_the_next_unlock(env: AppEnv):
    [a] = enqueue_ui(env, env.set_leaf("login-1", "name", "Mail")).json()["ids"]
    vault_unreachable(env, True)
    assert env.client.delete("/api/session").status_code == 204
    assert not env.bw.unlocked
    assert env.queue.db.queue_entries(ids=[a])[0]["status"] == "pending"
    assert env.item("login-1")["name"] == "Example Mail"
    vault_unreachable(env, False)
    env.unlock()
    ## an entry left over from the last session is due at once
    outcome = commit_if_due(env)
    assert outcome is not None
    assert outcome["applied"] == [a]
    assert env.item("login-1")["name"] == "Mail"


def test_a_failing_auto_commit_waits_before_it_tries_again(env: AppEnv):
    [a] = enqueue_ui(env, env.set_leaf("login-1", "name", "Mail")).json()["ids"]
    vault_unreachable(env, True)
    env.clock.advance(10)
    assert commit_if_due(env) is None
    assert statuses(env, a) == ["pending"]
    ## a commit that left every entry pending did not end
    assert status(env)["commitCount"] == 0
    vault_unreachable(env, False)
    assert commit_if_due(env) is None
    env.clock.advance(30)
    outcome = commit_if_due(env)
    assert outcome is not None
    assert outcome["applied"] == [a]
    assert status(env)["commitCount"] == 1
