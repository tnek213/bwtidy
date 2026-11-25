"""Approving entries into the write path, against the fake bw serve: one backup per batch, the
conflicts and failures each entry is blamed for, a commit cut off, retries and restores."""

import asyncio
import json
import shutil
import sqlite3
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

import bwtidy.backups.reader as reader
import bwtidy.queue.autocommit as autocommit
import bwtidy.queue.commit as commit
from bwtidy.backups.files import BackupError
from bwtidy.operations import Operation, parse_operations, refreshed, touched
from bwtidy.protocol import DB_FILE
from bwtidy.queue.state import dump_operation
from tests.support.app_env import AppEnv, logged_in
from tests.support.normalize_data import rename
from tests.support.queue_helpers import (
    CHANGED,
    HIDDEN_VALUE,
    LOGIN_USERNAME,
    MINIMAL_OPERATIONS,
    approve,
    auto_commit,
    before_the_reload,
    commit_if_due,
    commit_now,
    enqueue_dict,
    enqueue_ui,
    entry,
    hidden_setlers,
    listing,
    quick_auto_commit,
    raw_sqlite,
    retry,
    status,
    statuses,
    vault_unreachable,
)
from tests.support.quiet import assert_absent
from tests.support.sample_vault import LOGIN
from tests.support.writes_helpers import (
    PHONE,
    PHONE_REVISION,
    change_on_server_at_put,
    edit_at_sync,
    plain_decrypt,
)


@pytest.fixture
def renv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppEnv]:
    """An AppEnv whose backups are real zips of the fake vault, so a restore can read them."""
    monkeypatch.setattr(autocommit, "TICK", 3600.0)
    monkeypatch.setattr(reader, "decrypt_backup", plain_decrypt)
    yield from logged_in(tmp_path, zips=True)


## what a retry with overwrite takes from the vault (operations.refreshed), the rest stays
REFRESHED_KEYS = {
    "revisionDate",
    "revisionDates",
    "fromRevisionDate",
    "toRevisionDate",
    "items",
    "shownFolderIds",
    "shownFavorite",
    "shownCollectionIds",
    "shownName",
    "shownFileName",
}


SHOWN = {
    "setLeaf": {"column": "favorite", "value": False, "shownFavorite": False},
    "moveToFolder": {"revisionDates": {"login-1": "old"}, "shownFolderIds": {"login-1": "f1"}},
    "setCollections": {"shownCollectionIds": []},
    "renameField": {"items": [{"itemId": "login-1", "revisionDate": "old"}]},
    "mergeColumns": {"items": [{"itemId": "login-1", "revisionDate": "old"}]},
    "restoreItems": {"revisionDates": {"login-1": "old"}},
    "setUris": {
        "items": [
            {
                "itemId": "login-1",
                "revisionDate": "old",
                "shownUris": [{"uri": "https://old.example.test", "match": None}],
                "uris": [{"uri": "https://new.example.test", "match": 1}],
            }
        ]
    },
}


def shown_operation(kind: str) -> Operation:
    [op] = parse_operations([{"kind": kind, **MINIMAL_OPERATIONS[kind], **SHOWN.get(kind, {})}])
    return op


def test_a_refresh_takes_only_the_revision_dates_and_shown_values_of_the_vault():
    items = {
        "login-1": {
            "id": "login-1",
            "revisionDate": "now-1",
            "folderId": "f9",
            "favorite": True,
            "collectionIds": ["c2", "c1"],
            "name": "Changed elsewhere",
            "login": {"uris": [{"match": 3, "uri": "https://elsewhere.example.test"}]},
            "attachments": [{"id": "a1", "fileName": "renamed elsewhere.txt", "size": "81"}],
        },
        "login-2": {"id": "login-2", "revisionDate": "now-2"},
    }
    folders = {"f1": "Renamed elsewhere"}
    for kind in MINIMAL_OPERATIONS:
        op = shown_operation(kind)
        before = dump_operation(op)
        after = dump_operation(refreshed(op, items, folders))
        kept = {k: v for k, v in before.items() if k not in REFRESHED_KEYS}
        assert {k: v for k, v in after.items() if k not in REFRESHED_KEYS} == kept, kind
        assert set(after) == set(before), kind
        for key in ("revisionDate", "fromRevisionDate"):
            if key in after:
                assert after[key] == "now-1", kind
        if "toRevisionDate" in after:
            assert after["toRevisionDate"] == "now-2"
        if after.get("revisionDates"):
            assert after["revisionDates"] == {"login-1": "now-1"}, kind
        if after.get("items") and kind != "setUris":
            assert after["items"] == [{"itemId": "login-1", "revisionDate": "now-1"}], kind
    shown_now = {
        kind: dump_operation(refreshed(shown_operation(kind), items, folders))
        for kind in (
            "moveToFolder",
            "setLeaf",
            "setCollections",
            "renameFolder",
            "deleteFolder",
            "renameAttachment",
        )
    }
    assert shown_now["moveToFolder"]["shownFolderIds"] == {"login-1": "f9"}
    assert shown_now["setLeaf"]["shownFavorite"] is True
    assert shown_now["setCollections"]["shownCollectionIds"] == ["c1", "c2"]
    assert shown_now["renameFolder"]["shownName"] == "Renamed elsewhere"
    assert shown_now["deleteFolder"]["shownName"] == "Renamed elsewhere"
    ## the URIs it writes stay, those shown and the revisionDate are the vault's now
    [uris] = dump_operation(refreshed(shown_operation("setUris"), items, folders))["items"]
    assert uris == {
        "itemId": "login-1",
        "revisionDate": "now-1",
        "shownUris": [{"uri": "https://elsewhere.example.test", "match": 3}],
        "uris": [{"uri": "https://new.example.test", "match": 1}],
    }
    assert shown_now["renameAttachment"]["shownFileName"] == "renamed elsewhere.txt"


def test_approve_of_three_entries_makes_one_backup_and_applies_all(env: AppEnv):
    ids = [
        enqueue_dict(env, env.set_leaf("login-1", "name", "Mail"), "rename mail"),
        enqueue_dict(env, rename(env, "Shoe size", "Shoe", "login-2"), "shorter field"),
        enqueue_dict(env, env.op("trashItem", "card-1"), "old card"),
    ]
    response = approve(env, *ids)
    assert response.status_code == 200, response.text
    ## an approve on the Queue page counts as a commit like the auto-commit's
    assert status(env)["commitCount"] == 1
    ## the login's backup held the vault as it was, and one of the vault after followed
    backup = env.login_backup
    assert len(env.backups) == 2
    assert response.json() == {"applied": ids, "failed": [], "pending": [], "backup": backup}
    assert env.item("login-1")["name"] == "Mail"
    assert [f["name"] for f in env.item("login-2")["fields"]][-1] == "Shoe"
    assert [i["id"] for i in env.bw.trashed] == ["card-1"]
    applied = listing(env, "applied")
    assert [e["id"] for e in applied] == ids
    assert all(e["result"] == {"backup": backup} and e["decidedAt"] for e in applied)
    ## the diff stays as it was approved, not as the vault looks now
    assert applied[0]["diff"][0]["before"] == "Example Mail"
    assert [e["source"] for e in env.change_log()] == ["mcp", "mcp", "mcp"]
    ## an applied entry cannot be approved again
    assert approve(env, ids[0]).status_code == 422
    assert len(env.backups) == 2


def test_a_conflict_fails_only_the_affected_entry(env: AppEnv):
    first = enqueue_dict(env, env.set_leaf("login-1", "name", "Mail"), "rename mail")
    stale = enqueue_dict(env, env.op("trashItem", "card-1"), "old card")
    last = enqueue_dict(env, env.set_leaf("note-3", "name", "Media"), "rename pc")
    ## Another user edits the card after the agent queued its change
    env.item("card-1")["revisionDate"] = CHANGED
    response = approve(env, first, stale, last)
    assert response.status_code == 200, response.text
    ## the card changed elsewhere, so the login's backup could not be reused
    assert len(env.backups) == 3
    backup = env.backups[1]
    assert response.json() == {
        "applied": [first, last],
        "failed": [stale],
        "pending": [],
        "backup": backup,
    }
    assert env.bw.trashed == []
    assert env.item("note-3")["name"] == "Media"
    failed = entry(env, stale)
    assert failed["status"] == "failed"
    assert failed["result"]["error"] == "conflicts"
    assert failed["result"]["conflicts"] == [
        {"itemId": "card-1", "expected": "2026-10-26T12:00:00.000Z", "actual": CHANGED}
    ]
    assert [entry(env, i)["status"] for i in (first, last)] == ["applied", "applied"]


def test_an_entry_that_became_invalid_fails_alone(env: AppEnv):
    good = enqueue_dict(env, env.set_leaf("login-1", "name", "Mail"), "rename mail")
    rename_op = rename(env, "Shoe size", "Shoe", "login-2")
    broken = enqueue_dict(env, rename_op, "shorter field")
    ## the field gets renamed elsewhere, the item's revisionDate stays (fake bw serve)
    env.item("login-2")["fields"][2]["name"] = "Size"
    response = approve(env, good, broken)
    assert response.json()["applied"] == [good]
    assert response.json()["failed"] == [broken]
    assert entry(env, broken)["result"] == {
        "error": "invalid",
        "detail": "item login-2 has no field named 'Shoe size'",
    }
    assert len(env.backups) == 2


def test_a_failed_backup_leaves_the_entries_pending(env: AppEnv):
    entry_id = enqueue_dict(env, env.set_leaf("login-1", "name", "Mail"), "rename mail")
    ## a change elsewhere, so the login's backup cannot be reused
    env.item("card-1")["revisionDate"] = CHANGED
    env.backup_error = BackupError("export", "exit 1")
    response = approve(env, entry_id)
    assert response.status_code == 500
    assert response.json() == {"detail": "Backup failed at export: exit 1"}
    assert entry(env, entry_id)["status"] == "pending"
    assert env.bw.put == []


def test_a_vault_that_cannot_be_reached_answers_502_with_its_text(env: AppEnv):
    entry_id = enqueue_dict(env, env.set_leaf("login-1", "name", "Mail"), "rename mail")
    vault_unreachable(env, True)
    assert enqueue_ui(env, env.set_leaf("card-1", "name", "Visa")).status_code == 201
    for response in (approve(env, entry_id), commit_now(env)):
        assert response.status_code == 502
        assert isinstance(response.json()["detail"], str)
        assert response.json()["detail"]
    assert env.bw.put == []


def changed_elsewhere(env: AppEnv, item_id: str) -> None:
    """Another client changed the item, and bwtidy has seen it (the vault in RAM knows)."""
    env.item(item_id)["revisionDate"] = CHANGED
    cached = next(i for i in env.app.state.bw.session.cache.items if i["id"] == item_id)
    cached["revisionDate"] = CHANGED


def test_a_ui_restore_is_queued_with_its_plan_as_the_diff_and_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(autocommit, "TICK", 3600.0)
    monkeypatch.setattr(reader, "decrypt_backup", plain_decrypt)
    with AppEnv(tmp_path, zips=True) as env:
        env.unlock()
        quick_auto_commit(env)
        ids = enqueue_ui(
            env,
            env.set_leaf("login-1", "login.username", "changed@example.test"),
            env.op("trashItem", "card-1"),
        ).json()["ids"]
        assert commit_now(env).json()["applied"] == ids
        rows = env.change_log()
        assert [r["operationKind"] for r in rows] == ["trashItem", "setLeaf"]
        ## one restore for both rows, the way the UI would send it for one row each
        plan = env.plan(rows[1]["id"]).json()
        op = {
            **plan["operation"],
            "itemIds": ["login-1", "card-1"],
            "backup": env.login_backup,
        }
        response = enqueue_ui(env, op)
        assert response.status_code == 201, response.text
        [entry_id] = response.json()["ids"]
        queued = entry(env, entry_id)
        assert queued["summary"] == f"Restore 2 items from {env.login_backup}"
        assert queued["operation"] == op
        assert [
            (r["itemId"], r["itemName"], r["column"], r["before"], r["after"])
            for r in queued["diff"]
        ] == [
            ("login-1", "Example Mail", "restore", None, "put back as before"),
            ("login-1", "Example Mail", "login.username", "changed@example.test", LOGIN_USERNAME),
            (
                "card-1",
                "Visa debit",
                "restore",
                None,
                "take out of the trash and put back as before",
            ),
        ]
        ## the entry is a plain UI entry, so the auto-commit takes it
        env.clock.advance(60)
        outcome = commit_if_due(env)
        assert outcome is not None
        assert outcome["applied"] == [entry_id]
        assert statuses(env, entry_id) == ["applied"]
        assert env.item("login-1")["login"]["username"] == LOGIN_USERNAME
        assert env.bw.restored == ["card-1"]
        assert "card-1" in env.revisions()
        assert env.change_log()[0]["operationKind"] == "restoreItems"
        assert env.change_log()[0]["source"] == "ui"
        ## the diff kept with the decided entry holds no secret value
        dump = (env.data_dir / "bwtidy.sqlite").read_bytes()
        assert_absent(b"pw-login-secret", dump, "the database file")


def test_an_edit_based_on_a_revision_bwtidy_replaced_does_not_conflict(env: AppEnv):
    first = env.set_leaf("login-1", "name", "Mail")
    stale_revision = first["revisionDate"]
    [a] = enqueue_ui(env, first).json()["ids"]
    assert commit_now(env).json()["applied"] == [a]
    written_once = env.item("login-1")["revisionDate"]
    assert written_once != stale_revision
    ## the grid has not reloaded yet, so the next edit still carries the old revisionDate
    second = {**env.set_leaf("login-1", "login.username", "me@example.test")}
    second["revisionDate"] = stale_revision
    response = enqueue_ui(env, second)
    assert response.status_code == 201, response.text
    [b] = response.json()["ids"]
    assert commit_now(env).json()["applied"] == [b]
    assert env.item("login-1")["login"]["username"] == "me@example.test"
    ## two of bwtidy's own writes later the old revisionDate still resolves, along the chain
    third = {**env.set_leaf("login-1", "notes", "three"), "revisionDate": stale_revision}
    assert enqueue_ui(env, third).status_code == 201
    assert commit_now(env).json()["failed"] == []
    assert env.item("login-1")["notes"] == "three"
    ## a change made elsewhere still conflicts, whatever revisionDate the edit carries
    changed_elsewhere(env, "login-1")
    for revision in (stale_revision, written_once):
        stale = {**env.set_leaf("login-1", "name", "Nope"), "revisionDate": revision}
        response = enqueue_ui(env, stale)
        assert response.status_code == 409, response.text
        assert response.json()["conflicts"][0]["actual"] == CHANGED
    assert env.item("login-1")["name"] == "Mail"


def test_a_second_edit_on_the_old_revision_spelled_1_ms_off_follows_the_first_write(env: AppEnv):
    """A password set in the grid and committed, then the notes
    removed on the same page, its revisionDate the one from before the first write and spelled
    1 ms off from bw serve's (a cut and a rounded spelling). It follows the own write, one 2 ms
    off does not."""
    env.item("login-1")["notes"] = "to remove"
    first = env.set_leaf("login-1", "login.password", "set-in-the-dialog-5e1f")
    [a] = enqueue_ui(env, first).json()["ids"]
    assert commit_now(env).json()["applied"] == [a]
    assert first["revisionDate"] == "2026-10-26T10:00:00.000Z"
    second = {**env.op("removeLeaf", "login-1", column="notes")}
    second["revisionDate"] = "2026-10-26T10:00:00.001Z"
    response = enqueue_ui(env, second)
    assert response.status_code == 201, response.text
    [b] = response.json()["ids"]
    assert commit_now(env).json()["applied"] == [b]
    item = env.item("login-1")
    assert "notes" not in item or not item["notes"]
    assert item["login"]["password"] == "set-in-the-dialog-5e1f"
    ## a revision 2 ms off is no spelling of the one before the writes
    third = {**env.set_leaf("login-1", "name", "Nope"), "revisionDate": "2026-10-26T10:00:00.002Z"}
    assert enqueue_ui(env, third).status_code == 409


def test_an_edit_on_the_old_revision_queued_before_the_auto_commits_reload_is_taken(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    first = env.set_leaf("note-3", "notes", "queued in the grid")
    stale_revision = first["revisionDate"]
    second = {**env.set_leaf("note-3", "notes", "second edit"), "revisionDate": stale_revision}
    elsewhere = {**env.set_leaf("note-3", "name", "Nope"), "revisionDate": CHANGED}
    [a] = enqueue_ui(env, first).json()["ids"]
    seen: dict[str, Any] = {}

    async def step(client: httpx.AsyncClient) -> None:
        seen["status"] = env.queue.db.queue_entries(ids=[a])[0]["status"]
        seen["response"] = await client.post("/api/queue", json={"operations": [second]})
        seen["elsewhere"] = await client.post("/api/queue", json={"operations": [elsewhere]})

    ## the entry is applied as soon as its write is done, the cache still holds the item from
    ## before it until the reload, and the grid has not reloaded either
    before_the_reload(env, monkeypatch, step)
    env.clock.advance(10)
    assert commit_if_due(env) is not None
    assert seen["status"] == "applied"
    response = seen["response"]
    assert response.status_code == 201, response.text
    assert seen["elsewhere"].status_code == 409
    [b] = response.json()["ids"]
    env.clock.advance(10)
    outcome = commit_if_due(env)
    assert outcome is not None
    assert outcome["applied"] == [b]
    assert env.item("note-3")["notes"] == "second edit"


def test_a_change_made_elsewhere_after_the_edit_was_queued_fails_it_at_the_commit(env: AppEnv):
    first = env.set_leaf("login-1", "name", "Mail")
    [a] = enqueue_ui(env, first).json()["ids"]
    assert commit_now(env).json()["applied"] == [a]
    second = {**env.set_leaf("login-1", "notes", "late"), "revisionDate": first["revisionDate"]}
    [b] = enqueue_ui(env, second).json()["ids"]
    ## Another user edits the item before the commit, the write path's sync sees it
    env.item("login-1")["revisionDate"] = CHANGED
    outcome = commit_now(env).json()
    assert outcome["failed"] == [b]
    result = entry(env, b)["result"]
    assert result["error"] == "conflicts"
    assert result["conflicts"] == [
        {"itemId": "login-1", "expected": first["revisionDate"], "actual": CHANGED}
    ]
    assert env.item("login-1").get("notes") != "late"


def test_an_edit_queued_while_a_commit_runs_waits_and_does_not_conflict(env: AppEnv):
    first = env.set_leaf("login-1", "name", "Mail")
    second = {**env.set_leaf("login-1", "notes", "during"), "revisionDate": first["revisionDate"]}
    [a] = enqueue_ui(env, first).json()["ids"]
    assert env.client.portal is not None
    ## every request to the fake bw serve takes a while, so the commit is still running
    env.bw.delay = 0.05
    running = env.client.portal.start_task_soon(auto_commit(env).commit_pending)
    deadline = time.monotonic() + 5
    while not auto_commit(env).committing and time.monotonic() < deadline:
        time.sleep(0.005)
    assert auto_commit(env).committing
    response = enqueue_ui(env, second)
    assert response.status_code == 201, response.text
    [b] = response.json()["ids"]
    assert running.result(timeout=10)["applied"] == [a]
    env.bw.delay = 0.0
    assert statuses(env, a, b) == ["applied", "pending"]
    assert commit_now(env).json()["applied"] == [b]
    assert env.item("login-1")["notes"] == "during"
    ## the login's backup, then one after each of the two commits
    assert len(env.backups) == 3


def test_a_ui_entry_that_fails_at_the_commit_is_failed_with_the_reason(env: AppEnv):
    ids = enqueue_ui(
        env, env.set_leaf("login-1", "name", "Mail"), env.op("trashItem", "card-1")
    ).json()["ids"]
    env.item("card-1")["revisionDate"] = CHANGED
    outcome = commit_now(env).json()
    assert outcome["applied"] == [ids[0]]
    assert outcome["failed"] == [ids[1]]
    assert status(env)["commitCount"] == 1
    failed = entry(env, ids[1])
    assert failed["status"] == "failed"
    assert failed["result"]["error"] == "conflicts"
    assert failed["result"]["conflicts"][0]["actual"] == CHANGED
    assert env.bw.trashed == []


def test_a_change_a_sync_of_the_commit_brings_in_fails_the_later_entry_as_a_conflict(env: AppEnv):
    ids = enqueue_ui(
        env,
        {"kind": "createItem", "type": 1, "name": "Brand new"},
        env.set_leaf("login-1", "login.username", "mine"),
        env.set_leaf("card-1", "name", "Visa"),
    ).json()["ids"]
    ## sync 3 is the create's (sync 2 follows the backup), after the commit checked every
    ## revisionDate
    edit_at_sync(env, 3, "login-1")
    outcome = commit_now(env).json()
    assert outcome["applied"] == [ids[0]]
    assert outcome["failed"] == [ids[1]]
    assert outcome["pending"] == [ids[2]]
    failed = entry(env, ids[1])
    assert failed["result"]["error"] == "conflicts"
    assert failed["result"]["conflicts"] == [
        {"itemId": "login-1", "expected": LOGIN["revisionDate"], "actual": PHONE_REVISION}
    ]
    assert env.item("login-1")["login"]["username"] == PHONE
    ## the entry left pending is checked at the next commit like any other (the to-fill save of
    ## the new item's password commits with it)
    assert ids[2] in commit_now(env).json()["applied"]
    assert env.item("card-1")["name"] == "Visa"


def test_a_write_the_server_refuses_as_out_of_date_fails_its_entry_as_a_conflict(env: AppEnv):
    ids = enqueue_ui(
        env,
        env.set_leaf("card-1", "name", "Visa"),
        env.set_leaf("login-1", "login.username", "mine"),
        env.set_leaf("note-1", "notes", "later"),
    ).json()["ids"]
    change_on_server_at_put(env, "login-1")
    outcome = commit_now(env).json()
    assert outcome["applied"] == [ids[0]]
    assert outcome["failed"] == [ids[1]]
    assert outcome["pending"] == [ids[2]]
    failed = entry(env, ids[1])
    assert failed["result"]["error"] == "conflicts"
    assert failed["result"]["conflicts"] == [
        {"itemId": "login-1", "expected": LOGIN["revisionDate"], "actual": PHONE_REVISION}
    ]
    assert env.item("login-1")["login"]["username"] == PHONE
    assert env.item("note-1").get("notes") != "later"
    assert ids[2] in commit_now(env).json()["applied"]
    assert env.item("note-1")["notes"] == "later"


def shown_move(env: AppEnv, item_id: str, folder: str, shown: str | None) -> dict[str, Any]:
    return {
        "kind": "moveToFolder",
        "itemIds": [item_id],
        "revisionDates": {item_id: env.revisions()[item_id]},
        "folderName": folder,
        "shownFolderIds": {item_id: shown},
    }


def test_a_move_made_elsewhere_fails_only_the_entry_that_showed_the_folder(env: AppEnv):
    ids = enqueue_ui(
        env,
        shown_move(env, "login-1", "Archive", "f1"),
        env.set_leaf("login-1", "notes", "still mine"),
    ).json()["ids"]
    ## another device's partial update, the revisionDate stays
    env.item("login-1")["folderId"] = None
    outcome = commit_now(env).json()
    assert outcome["failed"] == [ids[0]]
    assert outcome["applied"] == [ids[1]]
    failed = entry(env, ids[0])
    assert failed["result"]["error"] == "conflicts"
    revision = LOGIN["revisionDate"]
    assert failed["result"]["conflicts"] == [
        {"itemId": "login-1", "expected": revision, "actual": revision, "column": "folder"}
    ]
    login = env.item("login-1")
    assert (login["folderId"], login["notes"]) == (None, "still mine")


def test_an_edit_queued_from_the_grid_before_a_commit_follows_its_own_move(env: AppEnv):
    ## the grid has not reloaded since, so the second move shows the folder from before
    stale = shown_move(env, "login-1", "", "f1")
    star = env.set_leaf("login-1", "favorite", False, shownFavorite=True)
    [first] = enqueue_ui(env, shown_move(env, "login-1", "Archive", "f1"), star).json()["ids"][:1]
    assert first in commit_now(env).json()["applied"]
    response = enqueue_ui(env, stale, {**star, "value": True})
    assert response.status_code == 201, response.text
    outcome = commit_now(env).json()
    assert outcome["failed"] == []
    login = env.item("login-1")
    assert (login["folderId"], login["favorite"]) == (None, True)


def test_a_hidden_value_is_written_after_a_restart(env: AppEnv, tmp_path: Path):
    [entry_id] = enqueue_ui(env, hidden_setlers(env)[0]).json()["ids"]
    ## the commit at the session's end cannot run, so the entry waits for the next unlock
    vault_unreachable(env, True)
    env.client.__exit__(None, None, None)
    vault_unreachable(env, False)
    assert env.queue.db.queue_entries(ids=[entry_id])[0]["status"] == "pending"
    assert_absent(HIDDEN_VALUE.encode(), raw_sqlite(tmp_path), "the database file")
    assert env.item("login-1")["login"]["password"] != HIDDEN_VALUE

    again = env.restarted()
    with again:
        again.unlock()
        outcome = commit_now(again).json()
        assert outcome["applied"] == [entry_id]
        assert env.item("login-1")["login"]["password"] == HIDDEN_VALUE
    assert_absent(HIDDEN_VALUE.encode(), raw_sqlite(tmp_path), "the database file")


def hold_after_first_put(env: AppEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    """From the next PUT on, every later bw serve call waits until env.bw.delay is reset."""
    handler = env.bw.handler
    held = False

    def hooked(request: Any) -> Any:
        nonlocal held
        response = handler(request)
        if request.method == "PUT" and not held:
            held = True
            env.bw.delay = 3600.0
        return response

    monkeypatch.setattr(env.bw, "handler", hooked)


async def until_applied(env: AppEnv, entry_id: int) -> None:
    deadline = time.time() + 5
    while env.queue.db.queue_entries(ids=[entry_id])[0]["status"] != "applied":
        assert time.time() < deadline
        await asyncio.sleep(0.001)


@pytest.mark.parametrize("cut", ["cancel", "error"])
def test_a_cut_off_commit_keeps_the_entries_it_wrote(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch, cut: str
):
    a, b, c = enqueue_ui(
        env,
        env.set_leaf("login-1", "name", "Mail"),
        env.set_leaf("note-1", "name", "Later"),
        env.set_leaf("note-3", "name", "Media"),
    ).json()["ids"]
    set_status = env.queue.db.set_queue_status

    def failing_for_b(entry_id: int, status: str, *args: Any, **kwargs: Any) -> None:
        if entry_id == b and status == "applied":
            raise sqlite3.OperationalError("disk I/O error")
        set_status(entry_id, status, *args, **kwargs)

    async def commit() -> None:
        task = asyncio.create_task(env.queue.approve([a, b, c]))
        try:
            await until_applied(env, a)
        finally:
            task.cancel()
            env.bw.delay = 0
        with pytest.raises(asyncio.CancelledError):
            await task

    assert env.client.portal is not None
    if cut == "cancel":
        hold_after_first_put(env, monkeypatch)
        env.client.portal.call(commit)
    else:
        ## the second write is done, but settling it fails
        with monkeypatch.context() as patch:
            patch.setattr(env.queue.db, "set_queue_status", failing_for_b)
            with pytest.raises(sqlite3.OperationalError):
                env.client.portal.call(env.queue.approve, [a, b, c])
    written = [item_id for item_id, _ in env.bw.put]
    assert written == (["login-1"] if cut == "cancel" else ["login-1", "note-1"])
    assert [row["itemIds"] for row in env.change_log() if row["result"] == "ok"] == [
        [item_id] for item_id in reversed(written)
    ]
    third = "pending" if cut == "cancel" else "failed"
    assert statuses(env, a, b, c) == ["applied", "failed", third]
    ## the frontend reloads after a cut off commit too, it wrote and failed entries
    assert status(env)["commitCount"] == 1
    assert entry(env, b)["result"]["error"] == "interrupted"
    if cut == "cancel":
        assert entry(env, c)["result"] is None
        assert commit_now(env).json()["applied"] == [c]
        assert env.item("note-3")["name"] == "Media"
    else:
        assert entry(env, c)["result"]["error"] == "interrupted"
        assert env.item("note-3")["name"] != "Media"


def test_a_kill_during_a_commit_fails_only_what_it_did_not_write_at_the_next_start(
    env: AppEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    a, b = enqueue_ui(
        env, env.set_leaf("login-1", "name", "Mail"), env.set_leaf("note-1", "name", "Later")
    ).json()["ids"]
    hold_after_first_put(env, monkeypatch)
    killed = tmp_path / "killed.sqlite"

    async def commit() -> None:
        task = asyncio.create_task(env.queue.approve([a, b]))
        try:
            await until_applied(env, a)
            ## what a kill right now leaves on disk
            source, copy_ = sqlite3.connect(tmp_path / DB_FILE), sqlite3.connect(killed)
            source.backup(copy_)
            source.close()
            copy_.close()
        finally:
            task.cancel()
            env.bw.delay = 0
        with pytest.raises(asyncio.CancelledError):
            await task

    assert env.client.portal is not None
    env.client.portal.call(commit)
    env.client.__exit__(None, None, None)
    shutil.copyfile(killed, tmp_path / DB_FILE)
    again = env.restarted()
    with again:
        again.unlock()
        assert statuses(again, a, b) == ["applied", "failed"]
        assert entry(again, b)["result"]["error"] == "interrupted"
        assert commit_now(again).json()["applied"] == []
    assert env.item("login-1")["name"] == "Mail"


@pytest.mark.parametrize("cut", ["cancel", "error"])
def test_a_commit_cut_off_before_its_writes_settles_only_its_own_entries(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch, cut: str
):
    [cut_off] = enqueue_ui(env, env.set_leaf("login-1", "name", "Mail")).json()["ids"]
    [waiting] = enqueue_ui(env, env.set_leaf("note-1", "name", "Later")).json()["ids"]
    started = asyncio.Event()

    async def apply(*args: Any, **kwargs: Any) -> None:
        started.set()
        if cut == "error":
            raise RuntimeError("out of the blue")
        await asyncio.Event().wait()

    async def commit() -> type[BaseException]:
        task = asyncio.create_task(env.queue.approve([cut_off]))
        await started.wait()
        task.cancel()
        with pytest.raises((asyncio.CancelledError, RuntimeError)) as raised:
            await task
        return raised.type

    assert env.client.portal is not None
    expected = asyncio.CancelledError if cut == "cancel" else RuntimeError
    with monkeypatch.context() as patch:
        patch.setattr(env.writes, "apply", apply)
        assert env.client.portal.call(commit) is expected
    assert entry(env, waiting)["result"] is None
    if cut == "cancel":
        ## nothing was written yet, so it waits for the next commit
        assert statuses(env, cut_off, waiting) == ["pending", "pending"]
        assert entry(env, cut_off)["result"] is None
        return
    assert statuses(env, cut_off, waiting) == ["failed", "pending"]
    assert entry(env, cut_off)["result"]["error"] == "interrupted"
    assert "may or may not" in entry(env, cut_off)["result"]["detail"]
    assert env.client.post("/api/queue/dismiss", json={"ids": [cut_off]}).status_code == 200
    assert [e["id"] for e in listing(env)] == [waiting]


def test_a_diff_that_raises_for_one_entry_leaves_the_whole_batch_pending(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    names = {
        "login-1": "One",
        "login-2": "Two",
        "note-1": "Three",
        "note-3": "Four",
        "card-1": "Five",
    }
    ids = [
        enqueue_dict(env, env.set_leaf(item_id, "name", name), f"rename {item_id}")
        for item_id, name in names.items()
    ]
    diff = commit.shown_now

    def failing_for_the_third(q: Any, entry: Any) -> dict[str, Any]:
        if entry.id == ids[2]:
            raise sqlite3.OperationalError("disk I/O error")
        return diff(q, entry)

    with monkeypatch.context() as patch:
        patch.setattr(commit, "shown_now", failing_for_the_third)
        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            approve(env, *ids)
    assert statuses(env, *ids) == ["pending"] * 5
    assert all(entry(env, i)["decidedAt"] is None for i in ids)
    assert env.bw.put == []
    ## nothing was left approved, so the same batch is approved at once
    response = approve(env, *ids)
    assert response.json()["applied"] == ids
    assert statuses(env, *ids) == ["applied"] * 5
    assert [env.item(item_id)["name"] for item_id in names] == list(names.values())


def test_a_retry_whose_refresh_fails_for_one_entry_resets_none(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    ids = enqueue_ui(
        env, env.set_leaf("login-1", "notes", "mine"), env.set_leaf("note-1", "name", "Mine")
    ).json()["ids"]
    for item_id in ("login-1", "note-1"):
        env.item(item_id).update({"name": "Hers", "revisionDate": CHANGED})
    assert commit_now(env).json()["failed"] == ids
    before = [entry(env, i) for i in ids]
    refresh = commit.refreshed

    def failing_for_the_second(op: Any, *args: Any) -> Any:
        if "note-1" in touched(op):
            raise RuntimeError("out of the blue")
        return refresh(op, *args)

    with monkeypatch.context() as patch:
        patch.setattr(commit, "refreshed", failing_for_the_second)
        with pytest.raises(RuntimeError, match="out of the blue"):
            retry(env, *ids, overwrite=True)
    assert [entry(env, i) for i in ids] == before
    assert statuses(env, *ids) == ["failed", "failed"]
    outcome = retry(env, *ids, overwrite=True).json()
    assert outcome["applied"] == ids
    assert (env.item("login-1")["notes"], env.item("note-1")["name"]) == ("mine", "Mine")


def test_a_failed_write_is_retried_and_applied(env: AppEnv):
    [entry_id] = enqueue_ui(env, env.set_leaf("login-1", "notes", "retried")).json()["ids"]
    env.bw.fail_at.add(("PUT", "/object/item/login-1"))
    assert commit_now(env).json()["failed"] == [entry_id]
    assert entry(env, entry_id)["result"]["error"] == "writeFailed"
    env.bw.fail_at.discard(("PUT", "/object/item/login-1"))
    commits = status(env)["commitCount"]

    response = retry(env, entry_id)
    assert response.status_code == 200, response.text
    outcome = response.json()
    assert (outcome["applied"], outcome["failed"]) == ([entry_id], [])
    assert env.item("login-1")["notes"] == "retried"
    retried = entry(env, entry_id)
    assert retried["status"] == "applied"
    assert retried["result"] == {"backup": outcome["backup"]}
    assert status(env)["commitCount"] == commits + 1
    assert retry(env, entry_id).status_code == 409
    assert retry(env, 99).status_code == 422
    env.client.cookies.clear()
    assert retry(env, entry_id).status_code == 401


def test_a_conflict_is_retried_only_with_overwrite_and_writes_over_the_other_change(env: AppEnv):
    [entry_id] = enqueue_ui(env, env.set_leaf("login-1", "notes", "mine")).json()["ids"]
    ## Another user changes the notes and the name before the commit
    elsewhere = env.item("login-1")
    elsewhere.update({"notes": "hers", "name": "Hers", "revisionDate": CHANGED})
    assert commit_now(env).json()["failed"] == [entry_id]
    assert entry(env, entry_id)["result"]["error"] == "conflicts"

    refused = retry(env, entry_id)
    assert refused.status_code == 409
    assert "overwrite" in refused.json()["detail"]
    assert statuses(env, entry_id) == ["failed"]
    assert env.item("login-1")["notes"] == "hers"

    backups = len(env.backups)
    outcome = retry(env, entry_id, overwrite=True).json()
    assert outcome["applied"] == [entry_id]
    login = env.item("login-1")
    ## only the column of the entry is written over, the other user's name stays
    assert (login["notes"], login["name"]) == ("mine", "Hers")
    ## a backup with the other user's notes is taken before the write, the change log names it
    assert len(env.backups) > backups
    assert outcome["backup"] == env.backups[backups]
    [row] = env.change_log()[:1]
    assert (row["result"], row["backup"], row["itemIds"]) == ("ok", outcome["backup"], ["login-1"])
    operation = entry(env, entry_id)["operation"]
    assert operation["revisionDate"] == CHANGED
    assert (operation["column"], operation["value"]) == ("notes", "mine")


def test_a_conflict_of_a_shown_folder_is_written_over_with_the_folder_now(env: AppEnv):
    [entry_id] = enqueue_ui(env, shown_move(env, "login-1", "Archive", "f1")).json()["ids"]
    ## another device's partial update, the revisionDate stays
    env.item("login-1")["folderId"] = None
    assert commit_now(env).json()["failed"] == [entry_id]
    outcome = retry(env, entry_id, overwrite=True).json()
    assert outcome["applied"] == [entry_id]
    archive = next(f["id"] for f in env.bw.folders if f["name"] == "Archive")
    assert env.item("login-1")["folderId"] == archive
    assert entry(env, entry_id)["operation"]["shownFolderIds"] == {"login-1": None}


def folder_conflict(env: AppEnv, op: dict[str, Any], elsewhere: str | None) -> int:
    """A failed entry of the folder operation on the empty folder f2 "Empty", which another
    device renamed to elsewhere (deleted for None) before the commit."""
    env.bw.folders.append({"object": "folder", "id": "f2", "name": "Empty"})
    assert env.client.portal is not None
    env.client.portal.call(env.writes.reload)
    [entry_id] = enqueue_ui(env, {"folderId": "f2", "shownName": "Empty", **op}).json()["ids"]
    if elsewhere is None:
        env.bw.folders.pop()
    else:
        env.bw.folders[-1]["name"] = elsewhere
    assert commit_now(env).json()["failed"] == [entry_id]
    assert entry(env, entry_id)["result"]["conflicts"] == [
        {"folderId": "f2", "expected": "Empty", "actual": elsewhere}
    ]
    return entry_id


@pytest.mark.parametrize(
    ("op", "after"),
    [
        ({"kind": "renameFolder", "newName": "Spare"}, [("f2", "Spare")]),
        ({"kind": "deleteFolder"}, []),
    ],
)
def test_a_folder_conflict_is_written_over_with_the_folders_name_now(
    env: AppEnv, op: dict[str, Any], after: list[tuple[str, str]]
):
    entry_id = folder_conflict(env, op, "Elsewhere")
    assert retry(env, entry_id).status_code == 409
    outcome = retry(env, entry_id, overwrite=True).json()
    assert outcome["applied"] == [entry_id]
    folders = [(f["id"], f["name"]) for f in env.bw.folders if f.get("id") not in (None, "f1")]
    assert folders == after
    assert entry(env, entry_id)["operation"] == {**op, "folderId": "f2", "shownName": "Elsewhere"}


def test_a_folder_conflict_whose_folder_is_gone_is_not_retried(env: AppEnv):
    entry_id = folder_conflict(env, {"kind": "renameFolder", "newName": "Spare"}, None)
    response = retry(env, entry_id, overwrite=True)
    assert response.status_code == 409
    assert response.json()["detail"] == f"entry {entry_id} changes a folder that is gone"
    assert statuses(env, entry_id) == ["failed"]
    assert entry(env, entry_id)["operation"]["shownName"] == "Empty"


def failed_entry(env: AppEnv, result: dict[str, Any]) -> int:
    """A failed entry with this result, as a commit leaves it."""
    entry_id = enqueue_dict(env, env.set_leaf("card-1", "name", "Visa"), "rename the card")
    env.queue.db.set_queue_status(entry_id, "failed", json.dumps(result))
    return entry_id


def test_an_interrupted_entry_and_a_merge_conflict_are_never_retried(env: AppEnv):
    interrupted = failed_entry(env, {"error": "interrupted", "detail": "cut off"})
    merge = failed_entry(env, {"error": "mergeConflicts", "detail": "choose", "mergeConflicts": []})
    pending = enqueue_dict(env, env.set_leaf("note-1", "name", "Later"), "rename the note")
    for entry_id, words in [
        (interrupted, "never retried"),
        (merge, "a choice per item"),
        (pending, "not failed"),
    ]:
        response = retry(env, entry_id, overwrite=True)
        assert response.status_code == 409
        assert words in response.json()["detail"]
    ## one refused entry refuses the whole retry
    invalid = failed_entry(env, {"error": "invalid", "detail": "no longer allowed"})
    assert retry(env, invalid, merge).status_code == 409
    assert statuses(env, interrupted, merge, invalid, pending) == [
        "failed",
        "failed",
        "failed",
        "pending",
    ]
    assert env.item("card-1")["name"] != "Visa"
    ## an agent's entry is retried like the UI's
    assert retry(env, invalid).json()["applied"] == [invalid]
    assert env.item("card-1")["name"] == "Visa"


def delete_folder(env: AppEnv, folder_id: str) -> None:
    """Delete a folder the way the server does: the items in it get no folder."""
    env.bw.folders = [f for f in env.bw.folders if f["id"] != folder_id]
    for item in env.bw.items:
        if item.get("folderId") == folder_id:
            item["folderId"] = None


def delete_collection(env: AppEnv, collection_id: str) -> None:
    """Delete a collection the way the server does: the items leave it."""
    env.bw.collections = [c for c in env.bw.collections if c["id"] != collection_id]
    for item in env.bw.items:
        if collection_id in (item.get("collectionIds") or []):
            item["collectionIds"] = [c for c in item["collectionIds"] if c != collection_id]


def restore_row(env: AppEnv, row: dict[str, Any]) -> dict[str, Any]:
    """Queue the restore of a change log row like the UI does and commit it."""
    plan = env.plan(row["id"]).json()
    response = enqueue_ui(env, plan["operation"])
    assert response.status_code == 201, response.text
    outcome = commit_now(env).json()
    return {"ids": response.json()["ids"], **outcome}


def folders_named(env: AppEnv, name: str) -> list[str]:
    return [f["id"] for f in env.bw.folders if f["name"] == name]


def write_then_row(env: AppEnv, *operations: dict[str, Any]) -> dict[str, Any]:
    ids = enqueue_ui(env, *operations).json()["ids"]
    assert commit_now(env).json()["applied"] == ids
    return env.change_log()[0]


def test_restore_of_an_item_whose_folder_was_deleted_makes_the_folder_again_by_name(renv: AppEnv):
    env = renv
    ## login-1 sits in folder f1 ("Work") in the login backup
    assert env.item("login-1")["folderId"] == "f1"
    row = write_then_row(env, env.set_leaf("login-1", "login.username", "changed@example.test"))
    delete_folder(env, "f1")
    env.reload()
    plan = env.plan(row["id"]).json()
    response = enqueue_ui(env, plan["operation"])
    assert response.status_code == 201, response.text
    [entry_id] = response.json()["ids"]
    ## the diff names the folder, never the stale id
    assert ("folder", None, "Work") in [
        (r["column"], r["before"], r["after"]) for r in entry(env, entry_id)["diff"]
    ]
    outcome = commit_now(env).json()
    assert outcome["applied"] == [entry_id], outcome
    [new_id] = folders_named(env, "Work")
    assert new_id != "f1"
    item_id, body = env.bw.put[-1]
    assert (item_id, body["folderId"]) == ("login-1", new_id)
    assert env.item("login-1")["folderId"] == new_id
    assert env.item("login-1")["login"]["username"] == LOGIN_USERNAME


def test_restore_of_an_item_whose_folder_was_deleted_finds_a_folder_of_that_name(renv: AppEnv):
    env = renv
    row = write_then_row(env, env.set_leaf("login-1", "login.username", "changed@example.test"))
    delete_folder(env, "f1")
    env.bw.folders.append({"object": "folder", "id": "f-again", "name": "Work"})
    assert restore_row(env, row)["failed"] == []
    assert env.bw.calls("POST", "/object/folder") == 0
    assert env.bw.put[-1][1]["folderId"] == "f-again"


def test_restore_of_two_items_whose_folder_was_deleted_makes_the_folder_once(renv: AppEnv):
    env = renv
    env.item("card-1")["folderId"] = "f1"
    env.item("card-1")["revisionDate"] = CHANGED
    env.reload()
    revisions = env.revisions()
    row = write_then_row(
        env,
        {
            "kind": "moveToFolder",
            "itemIds": ["login-1", "card-1"],
            "revisionDates": {i: revisions[i] for i in ("login-1", "card-1")},
            "folderName": "Elsewhere",
        },
    )
    delete_folder(env, "f1")
    assert restore_row(env, row)["failed"] == []
    [new_id] = folders_named(env, "Work")
    assert [body["folderId"] for _, body in env.bw.put[-2:]] == [new_id, new_id]


def test_restore_of_an_item_whose_folder_was_deleted_without_a_name_goes_without_one(renv: AppEnv):
    """A backup without raw/folders.json (or without the folder in it) knows no name."""
    env = renv
    row = write_then_row(env, env.set_leaf("login-1", "login.username", "changed@example.test"))
    path = env.data_dir / "backups" / row["backup"]
    with zipfile.ZipFile(path) as archive:
        members = {n: archive.read(n) for n in archive.namelist() if n != "raw/folders.json"}
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    delete_folder(env, "f1")
    assert restore_row(env, row)["failed"] == []
    assert env.bw.calls("POST", "/object/folder") == 0
    assert env.bw.put[-1][1]["folderId"] is None
    assert env.item("login-1")["login"]["username"] == LOGIN_USERNAME


def test_restore_keeps_the_folder_and_collections_by_id_while_they_exist(renv: AppEnv):
    env = renv
    note_row = write_then_row(env, env.set_leaf("note-1", "notes", "changed"))
    login_row = write_then_row(env, env.set_leaf("login-1", "login.username", "changed"))
    ## renamed since the backup, and a new one under the old name that must not be picked
    env.bw.folders[1]["name"] = "Job"
    env.bw.folders.append({"object": "folder", "id": "f-again", "name": "Work"})
    env.bw.collections[0]["name"] = "Renamed"
    env.bw.collections.append(
        {"object": "collection", "id": "c-again", "organizationId": "o1", "name": "Youth"}
    )
    assert restore_row(env, login_row)["failed"] == []
    assert env.bw.put[-1][1]["folderId"] == "f1"
    assert restore_row(env, note_row)["failed"] == []
    assert env.item("note-1")["collectionIds"] == ["c2", "c1"]
    assert env.bw.calls("PUT", "/object/item-collections/note-1") == 0
    assert env.bw.calls("POST", "/object/folder") == 0


def test_restore_asks_bw_serve_again_when_the_listing_misses_a_folder_or_collection(renv: AppEnv):
    """A listing right after a sync can miss what exists: no second folder, no lost collection."""
    env = renv
    row = write_then_row(env, env.set_leaf("note-1", "notes", "changed"))
    login_row = write_then_row(env, env.set_leaf("login-1", "login.username", "changed"))
    client = env.app.state.bw.session.client
    real_folders, real_collections = client.list_folders, client.list_collections
    folder_answers = [[]]
    collection_answers = [[]]

    async def list_folders() -> list[dict[str, Any]]:
        return folder_answers.pop() if folder_answers else await real_folders()

    async def list_collections() -> list[dict[str, Any]]:
        return collection_answers.pop() if collection_answers else await real_collections()

    client.list_folders, client.list_collections = list_folders, list_collections
    assert restore_row(env, login_row)["failed"] == []
    assert env.bw.calls("POST", "/object/folder") == 0
    assert env.bw.put[-1][1]["folderId"] == "f1"
    folder_answers.append([])
    collection_answers.append([])
    env.item("note-1")["collectionIds"] = ["c1"]
    assert restore_row(env, row)["failed"] == []
    assert env.item("note-1")["collectionIds"] == ["c1", "c2"]


def test_restore_of_an_item_whose_collection_was_deleted_leaves_it_out(renv: AppEnv):
    env = renv
    env.bw.collections.append(
        {"object": "collection", "id": "c3", "organizationId": "o1", "name": "extra"}
    )
    row = write_then_row(env, env.set_leaf("note-1", "notes", "changed"))
    write_then_row(env, env.op("setCollections", "note-1", collectionNames=["extra"]))
    delete_collection(env, "c2")
    calls = env.bw.calls("PUT", "/object/item-collections/note-1")
    outcome = restore_row(env, row)
    assert outcome["applied"] == outcome["ids"], outcome
    assert env.bw.calls("PUT", "/object/item-collections/note-1") == calls + 1
    assert env.item("note-1")["collectionIds"] == ["c1"]
    assert env.item("note-1")["notes"] != "changed"


def test_restore_of_an_item_whose_collection_was_deleted_finds_one_of_that_name(renv: AppEnv):
    env = renv
    row = write_then_row(env, env.set_leaf("note-1", "notes", "changed"))
    delete_collection(env, "c2")
    env.bw.collections += [
        {"object": "collection", "id": "other-org", "organizationId": "o2", "name": "shared"},
        {"object": "collection", "id": "c-again", "organizationId": "o1", "name": "shared"},
    ]
    assert restore_row(env, row)["failed"] == []
    assert sorted(env.item("note-1")["collectionIds"]) == ["c-again", "c1"]


def test_restore_of_an_item_whose_collection_was_deleted_diffs_it_as_gone(renv: AppEnv):
    env = renv
    env.bw.collections.append(
        {"object": "collection", "id": "c3", "organizationId": "o1", "name": "extra"}
    )
    row = write_then_row(env, env.set_leaf("note-1", "notes", "changed"))
    write_then_row(env, env.op("setCollections", "note-1", collectionNames=["extra"]))
    delete_collection(env, "c2")
    env.reload()
    plan = env.plan(row["id"]).json()
    [entry_id] = enqueue_ui(env, plan["operation"]).json()["ids"]
    assert ("collections", ["extra"], ["Youth"]) in [
        (r["column"], r["before"], r["after"]) for r in entry(env, entry_id)["diff"]
    ]


def test_restore_of_an_item_whose_collections_were_all_deleted_keeps_the_ones_it_has(renv: AppEnv):
    env = renv
    env.bw.collections.append(
        {"object": "collection", "id": "c3", "organizationId": "o1", "name": "extra"}
    )
    row = write_then_row(env, env.set_leaf("note-1", "notes", "changed"))
    write_then_row(env, env.op("setCollections", "note-1", collectionNames=["extra"]))
    delete_collection(env, "c1")
    delete_collection(env, "c2")
    calls = env.bw.calls("PUT", "/object/item-collections/note-1")
    outcome = restore_row(env, row)
    assert outcome["applied"] == outcome["ids"], outcome
    assert env.bw.calls("PUT", "/object/item-collections/note-1") == calls
    assert env.item("note-1")["collectionIds"] == ["c3"]
    assert env.bw.put[-1][1]["collectionIds"] == ["c3"]
    assert env.item("note-1")["notes"] != "changed"


def test_restore_of_an_item_with_no_collection_left_at_all_makes_no_collections_call(renv: AppEnv):
    env = renv
    row = write_then_row(env, env.set_leaf("note-1", "notes", "changed"))
    delete_collection(env, "c1")
    delete_collection(env, "c2")
    outcome = restore_row(env, row)
    assert outcome["applied"] == outcome["ids"], outcome
    assert env.bw.calls("PUT", "/object/item-collections/") == 0
    assert env.bw.calls("PUT", "/object/item-collections/note-1") == 0
    assert env.item("note-1")["notes"] != "changed"


def test_restore_of_a_trashed_item_whose_folder_and_collection_were_deleted(renv: AppEnv):
    env = renv
    trashed = write_then_row(env, env.op("trashItem", "login-1"))
    note = write_then_row(env, env.op("trashItem", "note-1"))
    delete_folder(env, "f1")
    delete_collection(env, "c2")
    for item in env.bw.trashed:
        item["collectionIds"] = [c for c in item.get("collectionIds") or [] if c != "c2"]
        if item.get("folderId") == "f1":
            item["folderId"] = None
    assert restore_row(env, trashed)["failed"] == []
    [new_id] = folders_named(env, "Work")
    assert env.bw.put[-1][1]["folderId"] == new_id
    assert restore_row(env, note)["failed"] == []
    assert env.item("note-1")["collectionIds"] == ["c1"]


def test_restore_anew_of_an_item_whose_folder_and_collection_were_deleted(renv: AppEnv):
    env = renv
    login = write_then_row(env, env.op("trashItem", "login-1"))
    note = write_then_row(env, env.op("trashItem", "note-1"))
    env.bw.trashed = []
    delete_folder(env, "f1")
    delete_collection(env, "c2")
    assert restore_row(env, login)["failed"] == []
    [new_id] = folders_named(env, "Work")
    assert env.bw.posted[-1]["folderId"] == new_id
    assert restore_row(env, note)["failed"] == []
    assert env.bw.posted[-1]["collectionIds"] == ["c1"]


def test_restore_anew_of_an_item_whose_collections_were_all_deleted_says_so(renv: AppEnv):
    env = renv
    row = write_then_row(env, env.op("trashItem", "note-1"))
    env.bw.trashed = []
    delete_collection(env, "c1")
    delete_collection(env, "c2")
    posted = len(env.bw.posted)
    outcome = restore_row(env, row)
    assert outcome["failed"] == outcome["ids"]
    assert entry(env, outcome["failed"][0])["result"]["detail"] == (
        "item note-1 cannot go back into its organization: none of its collections exists any more"
    )
    assert len(env.bw.posted) == posted
    assert env.bw.calls("POST", "/object/folder") == 0


def test_restore_out_of_an_organization_of_an_item_whose_folder_was_deleted(renv: AppEnv):
    env = renv
    row = write_then_row(
        env,
        env.op(
            "moveToOrganization", "login-1", organizationName="Family", collectionNames=["shared"]
        ),
    )
    delete_folder(env, "f1")
    outcome = restore_row(env, row)
    assert outcome["applied"] == outcome["ids"], outcome
    [new_id] = folders_named(env, "Work")
    assert env.bw.posted[-1]["folderId"] == new_id
    assert env.bw.posted[-1]["organizationId"] is None


def test_restore_into_an_organization_of_an_item_whose_collection_was_deleted(renv: AppEnv):
    env = renv
    ## some other client took the note out of the organization, keeping its id
    env.item("note-1")["organizationId"] = None
    env.item("note-1")["collectionIds"] = []
    env.item("note-1")["revisionDate"] = CHANGED
    env.reload()
    row_id = env.writes.db.record("ui", "setCollections", ["note-1"], "ok", env.login_backup)
    delete_collection(env, "c2")
    outcome = restore_row(env, {"id": row_id})
    assert outcome["applied"] == outcome["ids"], outcome
    assert ("POST", "/move/note-1/o1") in env.bw.requests
    assert env.item("note-1")["organizationId"] == "o1"
    assert env.item("note-1")["collectionIds"] == ["c1"]
    assert env.bw.calls("PUT", "/object/item-collections/note-1") == 0


def test_restore_into_an_organization_with_every_collection_deleted_says_so(renv: AppEnv):
    env = renv
    env.item("note-1")["organizationId"] = None
    env.item("note-1")["collectionIds"] = []
    env.item("note-1")["revisionDate"] = CHANGED
    env.reload()
    row_id = env.writes.db.record("ui", "setCollections", ["note-1"], "ok", env.login_backup)
    delete_collection(env, "c1")
    delete_collection(env, "c2")
    outcome = restore_row(env, {"id": row_id})
    assert outcome["failed"] == outcome["ids"]
    assert ("POST", "/move/note-1/o1") not in env.bw.requests
    assert env.item("note-1").get("organizationId") is None
