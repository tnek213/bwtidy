"""The batch driver of POST /api/operations against the fake bw serve: the syncs, one backup per
call or its reuse, the lock, the revision checks right before each write (the chain of own
writes, bw serve's stale answers, the server's out of date refusal), the partial results, the
change log and the table of operation kinds.

The fake backup behaves like create_backup towards the write path: it writes a file, stores
the fingerprint of the fake bw serve's listings and can be held or made to fail. The login
and every write start a background backup, which AppEnv waits for, so the counts below are
deterministic: login makes one backup, a write reuses the newest one when nothing changed
in between and leaves a fresh one behind.
"""

import asyncio
import copy
import inspect
import io
import json
import stat
import sys
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import httpx2 as httpx
import pytest

from bwtidy.backups.crypto import decrypt_backup
from bwtidy.backups.files import BackupError, BackupInfo
from bwtidy.items.ssh_keys import derive
from bwtidy.operations import Operation, SetLeaf
from bwtidy.writes.path import APPLY, SYNC_MAX_AGE
from tests.support import fake_bw
from tests.support.app_env import AppEnv
from tests.support.fake_bw import PASSWORD
from tests.support.fake_bw_script import FAKE_BW
from tests.support.sample_vault import LOGIN, SECRETS
from tests.support.ssh_keys import ed25519_private_key
from tests.support.writes_helpers import (
    PHONE,
    PHONE_REVISION,
    SYNC,
    change_on_server_at_put,
    create,
    edit_at_sync,
    move,
    moved_elsewhere,
    shown_conflict,
)


def test_operations_need_a_session(env: AppEnv):
    op = env.set_leaf("login-1", "login.username", "x")
    env.client.cookies.clear()
    assert env.post(op).status_code == 401
    assert env.client.get("/api/change-log").status_code == 401
    assert env.bw.put == []


def test_sync_and_reload_come_before_the_check(env: AppEnv):
    stale = env.set_leaf("login-1", "login.username", "x")
    ## another device changes the item after the grid was loaded
    env.item("login-1")["revisionDate"] = "2026-10-26T16:00:00.000Z"
    syncs = env.bw.calls(*SYNC)
    response = env.post(stale)
    assert response.status_code == 409
    assert response.json() == {
        "detail": "1 item changed since the grid was loaded, nothing was written",
        "conflicts": [
            {
                "itemId": "login-1",
                "expected": LOGIN["revisionDate"],
                "actual": "2026-10-26T16:00:00.000Z",
            }
        ],
    }
    assert env.bw.calls(*SYNC) == syncs + 1
    assert env.bw.put == []
    assert env.backups == [env.login_backup]
    assert env.backup_files() == [env.login_backup]
    assert env.change_log() == []


def test_an_empty_item_list_right_after_the_sync_is_asked_again(env: AppEnv):
    """bw serve lists no items for a moment after a sync, that is not every item vanishing."""
    op = env.set_leaf("login-1", "login.username", "patient@example.test")
    env.bw.empty_lists_after_sync = 2
    lists = env.bw.calls("GET", "/list/object/items")
    response = env.post(op)
    assert response.status_code == 200, response.text
    assert env.item("login-1")["login"]["username"] == "patient@example.test"
    ## two empty answers, the real one, the trash listing and the archived items outside and
    ## inside the trash (the same path) before the check, the same six after the sync that
    ## follows the backup and for the reload after the write
    assert env.bw.calls("GET", "/list/object/items") == lists + 6 + 6 + 6
    assert len(env.app.state.bw.session.cache.items) == len(env.bw.items)


def test_malformed_requests_are_422(env: AppEnv):
    assert env.post().status_code == 422
    assert env.client.post("/api/operations", json={}).status_code == 422
    response = env.post({"kind": "setLeaf", "itemId": "login-1"})
    assert response.status_code == 422
    assert "revisionDate" in response.json()["detail"]
    assert env.backups == [env.login_backup]


def test_one_backup_for_a_batch_of_many_operations(env: AppEnv):
    response = env.post(
        env.set_leaf("login-1", "login.username", "batch@example.test"),
        env.op("removeLeaf", "login-1", column="fields.PIN"),
        {
            "kind": "moveToFolder",
            "itemIds": ["note-1", "card-1"],
            "revisionDates": {
                k: v for k, v in env.revisions().items() if k in ("note-1", "card-1")
            },
            "folderName": "Archive",
        },
        env.op("trashItem", "ssh-1"),
    )
    assert response.status_code == 200
    assert response.json() == {"applied": 4, "backup": env.login_backup}
    ## the login's backup, reused, and the one taken after the batch
    assert len(env.backups) == 2
    assert env.backup_files() == env.backups
    assert [i for i, _ in env.bw.put] == ["login-1", "login-1", "note-1", "card-1"]
    assert env.bw.put[1][1]["fields"] == [f for f in LOGIN["fields"] if f["name"] != "PIN"]
    assert env.bw.put[1][1]["login"]["username"] == "batch@example.test"
    folder = next(f for f in env.bw.folders if f["name"] == "Archive")
    assert env.bw.calls("POST", "/object/folder") == 1
    assert env.item("note-1")["folderId"] == folder["id"]
    assert env.item("card-1")["folderId"] == folder["id"]
    assert [i["id"] for i in env.bw.trashed] == ["ssh-1"]
    log = env.change_log()
    assert [e["operationKind"] for e in log] == [
        "trashItem",
        "moveToFolder",
        "removeLeaf",
        "setLeaf",
    ]
    assert {e["backup"] for e in log} == {env.backups[0]}
    assert log[1]["itemIds"] == ["note-1", "card-1"]
    assert all(e["result"] == "ok" for e in log)


def test_the_lock_serializes_two_concurrent_calls(env: AppEnv):
    env.bw.delay = 0.002
    revisions = env.revisions()

    async def both():
        first = [
            SetLeaf(
                kind="setLeaf",
                itemId="login-1",
                revisionDate=revisions["login-1"],
                column="login.username",
                value="one",
            )
        ]
        second = [
            SetLeaf(
                kind="setLeaf",
                itemId="card-1",
                revisionDate=revisions["card-1"],
                column="card.brand",
                value="two",
            )
        ]
        return await asyncio.gather(env.writes.apply(first, "ui"), env.writes.apply(second, "ui"))

    assert env.client.portal is not None
    results = env.client.portal.call(both)
    env.settle()
    assert [r.applied for r in results] == [1, 1]
    ## the first reused the login's backup, the second the one taken after the first
    assert [r.backup for r in results] == env.backups[:2]
    assert len(env.backups) == 3
    assert not env.backups_overlapped
    requests = env.bw.requests
    first_put = requests.index(("PUT", "/object/item/login-1"))
    second_put = requests.index(("PUT", "/object/item/card-1"))
    assert first_put < second_put
    ## the second call's own syncs come after the first call's write, not interleaved
    login_syncs = env.bw.calls(*SYNC) - 6
    assert requests[:first_put].count(SYNC) == login_syncs + 2
    assert requests[first_put:second_put].count(SYNC) == 3


def test_a_failure_midway_reports_applied_and_pending(env: AppEnv):
    env.bw.fail_at.add(("PUT", "/object/item/card-1"))
    response = env.post(
        env.set_leaf("login-1", "login.username", "done@example.test"),
        env.set_leaf("card-1", "card.brand", "Mastercard"),
        env.set_leaf("note-1", "notes", "never written"),
    )
    assert response.status_code == 500
    body = response.json()
    assert body["applied"] == [0]
    assert body["failed"] == 1
    assert body["pending"] == [2]
    assert body["backup"] == env.backups[0]
    assert body["detail"] == (
        "Operation 2 failed: forced failure. 1 applied before it, 1 not applied. "
        f"Backup {env.backups[0]} holds the vault from before."
    )
    ## the fake refuses the second PUT before it records it
    assert [i for i, _ in env.bw.put] == ["login-1"]
    assert ("PUT", "/object/item/card-1") in env.bw.requests
    assert env.item("note-1")["notes"] == "Free text, not secret"
    log = env.change_log()
    assert [(e["operationKind"], e["itemIds"], e["result"]) for e in log] == [
        ("setLeaf", ["card-1"], "failed"),
        ("setLeaf", ["login-1"], "ok"),
    ]
    ## the cache was reloaded even so
    rows = env.client.get("/api/grid").json()["rows"]
    row = next(r for r in rows if r["id"] == "login-1")
    assert row["cells"]["login.username"] == "done@example.test"


def test_a_failed_batch_releases_its_backup(env: AppEnv):
    selected: list[str | None] = []
    real = env.bw.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            selected.append(env.keeper._write_backup)
        return real(request)

    vars(env.bw)["handler"] = handler
    env.bw.fail_at.add(("PUT", "/object/item/login-1"))
    response = env.post(env.set_leaf("login-1", "login.username", "x"))
    assert response.status_code == 500
    assert selected == [env.login_backup]
    assert env.keeper._write_backup is None


def test_a_failed_backup_writes_nothing(env: AppEnv):
    ## a change elsewhere, so the login's backup cannot be reused
    env.item("card-1")["revisionDate"] = "2026-10-26T16:00:00.000Z"
    env.backup_error = BackupError("personal export", "bw exited with 1")
    response = env.post(env.set_leaf("login-1", "login.username", "x"))
    assert response.status_code == 500
    assert response.json() == {"detail": "Backup failed at personal export: bw exited with 1"}
    assert env.bw.put == []
    assert env.backup_files() == [env.login_backup]
    assert env.change_log() == []


def test_a_write_reuses_the_backup_taken_after_the_write_before(env: AppEnv):
    stored = env.writes.db.backup_fingerprint
    assert stored(env.login_backup) == env.keeper.vault_fingerprint()
    first = env.post(env.set_leaf("login-1", "login.username", "first@example.test"))
    assert first.status_code == 200
    assert first.json()["backup"] == env.login_backup
    [_, after_first] = env.backups
    assert stored(after_first) == env.keeper.vault_fingerprint()
    assert stored(after_first) != stored(env.login_backup)
    second = env.post(env.set_leaf("card-1", "card.brand", "Mastercard"))
    assert second.status_code == 200
    assert second.json()["backup"] == after_first
    assert len(env.backups) == 3
    assert env.backup_files() == env.backups
    assert [e["backup"] for e in env.change_log()] == [after_first, env.login_backup]


def test_a_change_elsewhere_in_between_makes_a_fresh_backup(env: AppEnv):
    first = env.post(env.set_leaf("login-1", "login.username", "first@example.test"))
    assert first.status_code == 200
    [_, after_first] = env.backups
    ## another device edits an item the second write does not touch
    env.item("note-1")["revisionDate"] = "2026-10-26T16:00:00.000Z"
    second = env.post(env.set_leaf("card-1", "card.brand", "Mastercard"))
    assert second.status_code == 200
    fresh = second.json()["backup"]
    assert fresh not in (env.login_backup, after_first)
    assert env.backups == [env.login_backup, after_first, fresh, env.backups[3]]
    assert env.backup_files() == env.backups


def change_during_the_backup(env: AppEnv, item_id: str) -> None:
    """The first backup of the write finds the item edited on another device by its sync."""
    real = env.fake_backup
    fired = []

    async def backup(*args: Any, **kwargs: Any) -> BackupInfo:
        if not fired:
            fired.append(True)
            item = env.item(item_id)
            item["revisionDate"] = "2026-10-26T16:00:00.000Z"
            item["notes"] = "edited elsewhere"
        return await real(*args, **kwargs)

    env.keeper.backup = backup


def test_a_change_the_backups_sync_brings_in_is_a_conflict(env: AppEnv):
    op = env.set_leaf("login-1", "notes", "mine")
    ## something else changed, so the write takes a fresh backup
    env.item("card-1")["revisionDate"] = "2026-10-26T15:00:00.000Z"
    change_during_the_backup(env, "login-1")
    response = env.post(op)
    assert response.status_code == 409
    assert response.json()["conflicts"] == [
        {
            "itemId": "login-1",
            "expected": LOGIN["revisionDate"],
            "actual": "2026-10-26T16:00:00.000Z",
        }
    ]
    assert env.bw.put == []
    assert env.item("login-1")["notes"] == "edited elsewhere"
    assert env.change_log() == []


## LOGIN's revisionDate as another path spells the same instant, 1 ms off (bw/revision.py)
RESPELLED = "2026-10-26T10:00:00.001Z"


def test_an_expected_revision_spelled_1_ms_off_is_written(env: AppEnv):
    """The entry's revisionDate is the item's own, rounded where bw serve cut it. Both the
    check before the batch and the one right before the item's write take it as the same
    revision, either alone would fail it as a conflict."""
    assert LOGIN["revisionDate"] == "2026-10-26T10:00:00.000Z"
    op = {**env.set_leaf("login-1", "notes", "mine"), "revisionDate": RESPELLED}
    response = env.post(op)
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 1
    assert env.item("login-1")["notes"] == "mine"


def test_bw_serve_respelling_the_revision_during_the_batch_is_no_conflict(env: AppEnv):
    """bw serve answers the item's revision 1 ms off after the batch's syncs."""
    op = env.set_leaf("login-1", "notes", "mine")
    env.item("card-1")["revisionDate"] = "2026-10-26T15:00:00.000Z"
    real = env.fake_backup

    async def backup(*args: Any, **kwargs: Any) -> BackupInfo:
        env.item("login-1")["revisionDate"] = RESPELLED
        return await real(*args, **kwargs)

    env.keeper.backup = backup
    response = env.post(op)
    assert response.status_code == 200, response.text
    assert env.item("login-1")["notes"] == "mine"


def test_a_revision_2_ms_later_is_still_a_conflict(env: AppEnv):
    op = env.set_leaf("login-1", "notes", "mine")
    env.item("login-1")["revisionDate"] = "2026-10-26T10:00:00.002Z"
    response = env.post(op)
    assert response.status_code == 409
    assert response.json()["conflicts"] == [
        {
            "itemId": "login-1",
            "expected": LOGIN["revisionDate"],
            "actual": "2026-10-26T10:00:00.002Z",
        }
    ]
    assert env.bw.put == []


def test_a_change_to_another_item_during_the_backup_lets_the_write_through(env: AppEnv):
    op = env.set_leaf("login-1", "notes", "mine")
    env.item("card-1")["revisionDate"] = "2026-10-26T15:00:00.000Z"
    change_during_the_backup(env, "card-1")
    response = env.post(op)
    assert response.status_code == 200, response.text
    assert env.item("login-1")["notes"] == "mine"
    assert env.item("card-1")["notes"] == "edited elsewhere"
    assert [item_id for item_id, _ in env.bw.put] == ["login-1"]


def test_a_change_a_create_of_the_batch_syncs_in_is_a_conflict_of_the_later_write(env: AppEnv):
    op = env.set_leaf("login-1", "login.username", "mine")
    edit_at_sync(env, 3, "login-1")
    response = env.post(create(type=1, name="Brand new"), op)
    assert response.status_code == 500, response.text
    answer = response.json()
    assert (answer["applied"], answer["failed"], answer["pending"]) == ([0], 1, [])
    assert answer["conflicts"] == [
        {"itemId": "login-1", "expected": LOGIN["revisionDate"], "actual": PHONE_REVISION}
    ]
    assert env.item("login-1")["login"]["username"] == PHONE
    assert [i for i, _ in env.bw.put] == []
    assert any(i.get("name") == "Brand new" for i in env.bw.items)
    assert [(row["operationKind"], row["result"]) for row in reversed(env.change_log())] == [
        ("createItem", "ok"),
        ("setLeaf", "failed"),
    ]


def revision_at_sync(env: AppEnv, number: int, item_id: str, revision: str) -> None:
    """The sync with this number, counted from now, gives the item this revisionDate and
    nothing else (numbered as in edit_at_sync)."""
    real = env.bw.handler
    seen = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == SYNC:
            seen[0] += 1
            if seen[0] == number:
                env.item(item_id)["revisionDate"] = revision
        return real(request)

    vars(env.bw)["handler"] = handler


def test_a_respelled_revision_a_create_syncs_in_is_no_conflict_of_the_later_write(env: AppEnv):
    op = env.set_leaf("login-1", "notes", "mine")
    revision_at_sync(env, 3, "login-1", RESPELLED)
    response = env.post(create(type=1, name="Brand new"), op)
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 2
    assert env.item("login-1")["notes"] == "mine"


def test_a_revision_2_ms_later_a_create_syncs_in_is_a_conflict_of_the_later_write(env: AppEnv):
    op = env.set_leaf("login-1", "notes", "mine")
    revision_at_sync(env, 3, "login-1", "2026-10-26T10:00:00.002Z")
    response = env.post(create(type=1, name="Brand new"), op)
    assert response.status_code == 500, response.text
    assert response.json()["conflicts"] == [
        {
            "itemId": "login-1",
            "expected": LOGIN["revisionDate"],
            "actual": "2026-10-26T10:00:00.002Z",
        }
    ]
    assert [i for i, _ in env.bw.put] == []


def test_a_write_the_server_refuses_as_out_of_date_is_a_conflict(env: AppEnv):
    ops = [
        env.set_leaf("card-1", "name", "Visa"),
        env.set_leaf("login-1", "login.username", "mine"),
        env.set_leaf("note-1", "notes", "later"),
    ]
    change_on_server_at_put(env, "login-1")
    response = env.post(*ops)
    assert response.status_code == 500, response.text
    answer = response.json()
    assert (answer["applied"], answer["failed"], answer["pending"]) == ([0], 1, [2])
    assert answer["conflicts"] == [
        {"itemId": "login-1", "expected": LOGIN["revisionDate"], "actual": PHONE_REVISION}
    ]
    ## the other device's change stays, the write before it too, the one after it never ran
    assert env.item("login-1")["login"]["username"] == PHONE
    assert env.item("card-1")["name"] == "Visa"
    assert env.item("note-1").get("notes") != "later"
    assert [i for i, _ in env.bw.put] == ["card-1", "login-1"]
    assert [(row["operationKind"], row["result"]) for row in reversed(env.change_log())] == [
        ("setLeaf", "ok"),
        ("setLeaf", "failed"),
    ]
    assert env.revisions()["login-1"] == PHONE_REVISION


def test_another_refused_write_still_fails_with_bws_text(env: AppEnv):
    ops = [env.set_leaf("card-1", "name", "Visa"), env.set_leaf("login-1", "notes", "mine")]
    env.bw.fail_at.add(("PUT", "/object/item/login-1"))
    response = env.post(*ops)
    assert response.status_code == 500, response.text
    answer = response.json()
    assert (answer["applied"], answer["failed"], answer["conflicts"]) == ([0], 1, [])
    assert "forced failure" in answer["detail"]


def test_an_earlier_write_of_the_batch_is_no_conflict_for_a_later_one(env: AppEnv):
    ## both based on the revisionDate the grid showed, the create's sync in between
    first = env.set_leaf("login-1", "notes", "first")
    second = env.set_leaf("login-1", "login.username", "mine")
    move = {
        "kind": "moveToFolder",
        "itemIds": ["login-1"],
        "revisionDates": {"login-1": first["revisionDate"]},
        "folderName": "Work",
    }
    response = env.post(first, create(type=1, name="Brand new"), second, move)
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 4
    login = env.item("login-1")
    assert (login["notes"], login["login"]["username"]) == ("first", "mine")
    assert login["folderId"] == "f1"


def stale_after_put(env: AppEnv, monkeypatch: pytest.MonkeyPatch, item_id: str, gets: int) -> None:
    """After each PUT of the item, its next gets GETs answer it as it was before that PUT,
    like bw serve 2026.10.0 for a moment right after its own write (see
    WritePath.past_own_write). A negative gets answers it so for good."""
    real = env.bw.handler
    item_path = f"/object/item/{item_id}"
    state: dict[str, Any] = {"before": None, "left": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == ("PUT", item_path):
            state["before"] = copy.deepcopy(env.item(item_id))
            state["left"] = gets
        elif (request.method, request.url.path) == ("GET", item_path) and state["left"]:
            state["left"] -= 1
            env.bw.requests.append(("GET", item_path))
            return fake_bw.ok(copy.deepcopy(state["before"]))
        return real(request)

    monkeypatch.setattr(env.bw, "handler", handler)
    monkeypatch.setattr("bwtidy.writes.path.OWN_WRITE_DELAY", 0)


def test_bw_serve_answering_from_before_the_batchs_own_write_is_asked_again(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    first = env.set_leaf("login-1", "notes", "first")
    second = env.set_leaf("login-1", "login.username", "mine")
    stale_after_put(env, monkeypatch, "login-1", 2)
    response = env.post(first, second)
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 2
    login = env.item("login-1")
    assert (login["notes"], login["login"]["username"]) == ("first", "mine")
    ## the second PUT went on top of the first one, not on the stale copy
    assert env.bw.put[1][1]["notes"] == "first"
    assert env.bw.calls("GET", "/object/item/login-1") == 4


def test_an_answer_that_stays_from_before_the_own_write_is_still_a_conflict(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    first = env.set_leaf("login-1", "notes", "first")
    second = env.set_leaf("login-1", "login.username", "mine")
    stale_after_put(env, monkeypatch, "login-1", -1)
    monkeypatch.setattr("bwtidy.writes.path.OWN_WRITE_RETRIES", 3)
    response = env.post(first, second)
    assert response.status_code == 500, response.text
    answer = response.json()
    assert (answer["applied"], answer["failed"], answer["pending"]) == ([0], 1, [])
    assert answer["conflicts"] == [
        {"itemId": "login-1", "expected": LOGIN["revisionDate"], "actual": LOGIN["revisionDate"]}
    ]
    assert len(env.bw.put) == 1
    assert env.bw.calls("GET", "/object/item/login-1") == 1 + 1 + 3


def test_a_change_elsewhere_after_the_batchs_own_write_is_a_conflict_at_once(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    first = env.set_leaf("login-1", "notes", "first")
    second = env.set_leaf("login-1", "login.username", "mine")
    real = env.bw.handler

    def handler(request: httpx.Request) -> httpx.Response:
        answer = real(request)
        if (request.method, request.url.path) == ("PUT", "/object/item/login-1"):
            item = env.item("login-1")
            item["login"] = {**item["login"], "username": PHONE}
            item["revisionDate"] = PHONE_REVISION
        return answer

    monkeypatch.setattr(env.bw, "handler", handler)
    response = env.post(first, second)
    assert response.status_code == 500, response.text
    answer = response.json()
    assert (answer["applied"], answer["failed"]) == ([0], 1)
    assert answer["conflicts"] == [
        {"itemId": "login-1", "expected": LOGIN["revisionDate"], "actual": PHONE_REVISION}
    ]
    assert env.item("login-1")["login"]["username"] == PHONE
    assert env.bw.calls("GET", "/object/item/login-1") == 2


def at_each_put(env: AppEnv, then: Callable[[str], None]) -> None:
    """then(item_id) runs right before each PUT of an item reaches the fake bw serve."""
    real = env.bw.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path.startswith("/object/item/"):
            then(request.url.path.removeprefix("/object/item/"))
        return real(request)

    vars(env.bw)["handler"] = handler


def moved_on_server(env: AppEnv, item_id: str, **change: Any) -> None:
    """Another device's partial update reaches the server after bw serve's last sync, its
    revisionDate stays, so the server would take a PUT on bw serve's stale copy."""
    env.bw.change_on_server({**copy.deepcopy(env.item(item_id)), **change})


MakeOp = Callable[[AppEnv], dict[str, Any]]


ELSEWHERE_DURING_BATCH = [
    (
        lambda env: move(env, "login-1", "Archive", shownFolderIds={"login-1": "f1"}),
        "login-1",
        {"folderId": None},
        "folder",
    ),
    (
        lambda env: env.set_leaf("login-1", "favorite", False, shownFavorite=True),
        "login-1",
        {"favorite": False, "folderId": None},
        "favorite",
    ),
    (
        lambda env: env.op(
            "setCollections", "note-1", collectionNames=["Youth"], shownCollectionIds=["c2", "c1"]
        ),
        "note-1",
        {"collectionIds": ["c2"]},
        "collections",
    ),
]


@pytest.mark.parametrize(("make", "item_id", "change", "column"), ELSEWHERE_DURING_BATCH)
def test_a_change_elsewhere_during_the_batch_is_a_conflict_after_the_sync_age(
    env: AppEnv, make: MakeOp, item_id: str, change: dict[str, Any], column: str
):
    ops = [env.set_leaf("card-1", "notes", "first"), make(env)]

    def elsewhere(put_id: str) -> None:
        if put_id == "card-1":
            env.clock.advance(SYNC_MAX_AGE)
            moved_on_server(env, item_id, **change)

    at_each_put(env, elsewhere)
    response = env.post(*ops)
    assert response.status_code == 500, response.text
    answer = response.json()
    assert (answer["applied"], answer["failed"]) == ([0], 1)
    assert answer["conflicts"] == [shown_conflict(env, item_id, column)]
    item = env.item(item_id)
    assert {key: item.get(key) for key in change} == change
    assert [i for i, _ in env.bw.put] == ["card-1"]
    assert ("PUT", f"/object/item-collections/{item_id}") not in env.bw.requests


@pytest.mark.parametrize(("make", "item_id", "change", "column"), ELSEWHERE_DURING_BATCH)
def test_a_change_elsewhere_within_the_sync_age_is_still_overwritten(
    env: AppEnv, make: MakeOp, item_id: str, change: dict[str, Any], column: str
):
    """The window docs/spec.md Writes leaves open: bw serve's copy is younger than
    SYNC_MAX_AGE, so the operation does not sync and the server takes its write."""
    ops = [env.set_leaf("card-1", "notes", "first"), make(env)]

    def elsewhere(put_id: str) -> None:
        if put_id == "card-1":
            moved_on_server(env, item_id, **change)

    at_each_put(env, elsewhere)
    response = env.post(*ops)
    assert response.status_code == 200, response.text
    item = env.item(item_id)
    assert {key: item.get(key) for key in change} != change


def test_an_operation_syncs_only_when_the_last_sync_is_older_than_the_sync_age(env: AppEnv):
    ops = [
        env.set_leaf("card-1", "notes", "one"),
        env.set_leaf("login-1", "notes", "two"),
        env.set_leaf("note-1", "notes", "three"),
        env.set_leaf("card-1", "name", "four"),
    ]
    syncs = env.bw.calls(*SYNC)
    ## the start, after the backup, the reload: none before an operation
    assert env.post(*ops[:2]).status_code == 200
    assert env.bw.calls(*SYNC) == syncs + 3
    syncs = env.bw.calls(*SYNC)
    at_each_put(env, lambda _: env.clock.advance(SYNC_MAX_AGE))
    response = env.post(*ops[2:], env.set_leaf("login-1", "name", "five"))
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 3
    ## every operation after the first, whose last sync is the one after the backup
    assert env.bw.calls(*SYNC) == syncs + 3 + 2
    assert env.item("note-1")["notes"] == "three"
    assert (env.item("card-1")["name"], env.item("login-1")["name"]) == ("four", "five")


def test_the_sync_after_a_fresh_backup_finds_a_move_made_during_it(env: AppEnv):
    op = move(env, "login-1", "Archive", shownFolderIds={"login-1": "f1"})
    ## something else changed, so the write takes a fresh backup
    env.item("card-1")["revisionDate"] = "2026-10-26T15:00:00.000Z"
    real = env.fake_backup

    async def backup(*args: Any, **kwargs: Any) -> BackupInfo:
        info = await real(*args, **kwargs)
        ## after the backup's listings, before the writes
        moved_elsewhere(env, "login-1", folderId=None)
        return info

    env.keeper.backup = backup
    syncs = env.bw.calls(*SYNC)
    response = env.post(op)
    assert response.status_code == 409, response.text
    assert response.json()["conflicts"] == [shown_conflict(env, "login-1", "folder")]
    assert env.backups[-1] != env.login_backup
    assert env.item("login-1")["folderId"] is None
    assert env.bw.put == []
    ## the write's sync and the one after the backup
    assert env.bw.calls(*SYNC) == syncs + 2


def test_a_move_a_sync_of_the_batch_brings_in_fails_the_later_move(env: AppEnv):
    op = move(env, "login-1", "Archive", shownFolderIds={"login-1": "f1"})
    real = env.bw.handler
    seen = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == SYNC:
            seen[0] += 1
            ## the sync of the createItem
            if seen[0] == 3:
                moved_elsewhere(env, "login-1", folderId=None)
        return real(request)

    vars(env.bw)["handler"] = handler
    response = env.post(create(type=1, name="Brand new"), op)
    assert response.status_code == 500, response.text
    answer = response.json()
    assert (answer["applied"], answer["failed"]) == ([0], 1)
    assert answer["conflicts"] == [shown_conflict(env, "login-1", "folder")]
    assert env.item("login-1")["folderId"] is None
    assert env.bw.calls("POST", "/object/folder") == 0


def test_a_write_during_a_background_backup_waits_for_it_and_reuses_it(env: AppEnv):
    assert env.client.portal is not None
    gate = asyncio.Event()
    env.backup_gate = gate
    ## without settle, so the background backup after this write stays at the gate
    first = env.client.post(
        "/api/operations",
        json={"operations": [env.set_leaf("login-1", "login.username", "first@example.test")]},
    )
    assert first.status_code == 200
    assert first.json()["backup"] == env.login_backup
    second_ops: list[Operation] = [
        SetLeaf(
            kind="setLeaf",
            itemId="card-1",
            revisionDate=env.item("card-1")["revisionDate"],
            column="card.brand",
            value="Mastercard",
        )
    ]
    future = env.client.portal.start_task_soon(env.writes.apply, second_ops, "ui")
    time.sleep(0.1)
    ## the second write waits: the background backup still runs and nothing was written
    assert env.backups_active == 1
    assert len(env.backups) == 1
    assert [i for i, _ in env.bw.put] == ["login-1"]
    assert not future.done()

    async def open_gate() -> None:
        gate.set()

    env.client.portal.call(open_gate)
    result = future.result(timeout=5)
    env.settle()
    assert result.backup == env.backups[1]
    assert [i for i, _ in env.bw.put] == ["login-1", "card-1"]
    assert not env.backups_overlapped
    assert len(env.backups) == 3


def test_a_failed_background_backup_leaves_the_next_write_taking_its_own(
    env: AppEnv, caplog: pytest.LogCaptureFixture
):
    env.backup_error = BackupError("org export", "bw exited with 1")
    with caplog.at_level("WARNING", logger="bwtidy.writes.path"):
        first = env.post(env.set_leaf("login-1", "login.username", "first@example.test"))
    assert first.status_code == 200
    assert first.json()["backup"] == env.login_backup
    assert env.backups == [env.login_backup]
    assert "Background backup failed: Backup failed at org export: bw exited with 1" in [
        r.getMessage() for r in caplog.records
    ]
    env.backup_error = None
    second = env.post(env.set_leaf("card-1", "card.brand", "Mastercard"))
    assert second.status_code == 200
    assert second.json()["backup"] == env.backups[1]
    assert len(env.backups) == 3
    assert env.change_log()[0]["backup"] == env.backups[1]


def test_no_background_backup_when_the_newest_holds_the_vault_already(env: AppEnv):
    assert env.client.delete("/api/session").status_code == 204
    env.unlock()
    assert env.backups == [env.login_backup]
    ## a change elsewhere, and the next login backs up again
    env.item("note-1")["revisionDate"] = "2026-10-26T16:00:00.000Z"
    assert env.client.delete("/api/session").status_code == 204
    env.unlock()
    assert len(env.backups) == 2


def test_the_session_end_cancels_a_running_background_backup(env: AppEnv):
    assert env.client.portal is not None
    env.backup_gate = asyncio.Event()
    first = env.client.post(
        "/api/operations",
        json={"operations": [env.set_leaf("login-1", "login.username", "first@example.test")]},
    )
    assert first.status_code == 200
    time.sleep(0.05)
    assert env.backups_active == 1
    assert env.client.delete("/api/session").status_code == 204
    ## returns at once: the task is gone, the gate was never opened
    env.settle()
    assert env.backups == [env.login_backup]


def test_the_change_log_carries_the_columns_and_no_values(env: AppEnv):
    values = [
        "newuser-7f3a@example.test",
        "newpw-secret-1c2d",
        "hidden-field-value-9e",
        "free text fifty five",
        "https://brand-new.example.test",
    ]
    response = env.post(
        env.set_leaf("login-1", "login.username", values[0]),
        env.set_leaf("login-1", "login.password", values[1]),
        env.set_leaf("login-1", "fields.Recovery", values[2], fieldType=1),
        env.set_leaf("note-1", "notes", values[3]),
        env.set_leaf("login-1", "login.uris", [values[4]]),
    )
    assert response.status_code == 200
    env.bw.fail_at.add(("PUT", "/object/item/card-1"))
    assert env.post(env.set_leaf("card-1", "card.code", "code-value-31")).status_code == 500
    log = env.change_log()
    assert len(log) == 6
    assert log[0]["result"] == "failed"
    assert [e["id"] for e in log] == [6, 5, 4, 3, 2, 1]
    dump = (env.data_dir / "bwtidy.sqlite").read_bytes()
    leaked = [
        i
        for i, value in enumerate([*values, "code-value-31", *SECRETS, PASSWORD])
        if value.encode() in dump
    ]
    ## indexes only, so a failure never prints a secret
    assert leaked == []
    assert b"login-1" in dump
    ## the coordinates are logged, the values not
    assert [e["columns"] for e in log] == [
        ["card.code"],
        ["login.uris"],
        ["notes"],
        ["fields.Recovery"],
        ["login.password"],
        ["login.username"],
    ]


def test_change_log_returns_the_latest_200(env: AppEnv):
    for index in range(205):
        env.writes.db.record("ui", "setLeaf", [f"item-{index}"], "ok", "b.zip.age")
    log = env.change_log()
    assert len(log) == 200
    assert log[0]["itemIds"] == ["item-204"]
    assert log[-1]["itemIds"] == ["item-5"]


@pytest.mark.bw_cli
def test_the_real_backup_runs_before_the_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bw_bin = tmp_path / "bw"
    bw_bin.write_text(f"#!{sys.executable}\n{FAKE_BW}")
    bw_bin.chmod(bw_bin.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_BW_LOG", str(tmp_path / "bw.log"))
    with AppEnv(tmp_path, bw_bin=str(bw_bin), fake_backups=False) as env:
        env.unlock()
        assert env.bw.session_key
        monkeypatch.setenv("FAKE_BW_KEY", env.bw.session_key)
        response = env.post(env.set_leaf("login-1", "login.username", "after@example.test"))
        assert response.status_code == 200
        name = response.json()["backup"]
        ## the login's background backup failed (the fake bw got its key only afterwards), so
        ## the write took its own, and the one after the write follows it
        assert env.backup_files()[0] == name
        assert len(env.backup_files()) == 2
        plaintext = decrypt_backup(tmp_path / "backups" / name, PASSWORD)
        with zipfile.ZipFile(io.BytesIO(plaintext)) as archive:
            items = json.loads(archive.read("raw/items.json"))
        login = next(i for i in items if i["id"] == "login-1")
        assert login["login"]["username"] == LOGIN["login"]["username"]
        assert env.item("login-1")["login"]["username"] == "after@example.test"


def test_a_private_key_set_in_the_grid_fills_an_empty_public_key_and_fingerprint(env: AppEnv):
    for item in env.bw.items:
        if item["id"] == "ssh-1":
            item["sshKey"]["publicKey"] = item["sshKey"]["keyFingerprint"] = None
    private_key = ed25519_private_key()
    response = env.post(env.set_leaf("ssh-1", "sshKey.privateKey", private_key))
    assert response.status_code == 200, response.text
    ssh = env.item("ssh-1")["sshKey"]
    derived = (ssh["publicKey"], ssh["keyFingerprint"]) == derive(private_key)
    assert derived
    [row] = env.change_log()
    assert row["columns"] == ["sshKey.privateKey", "sshKey.publicKey", "sshKey.keyFingerprint"]


def test_a_new_private_key_keeps_the_public_key_and_fingerprint_there(env: AppEnv):
    response = env.post(env.set_leaf("ssh-1", "sshKey.privateKey", ed25519_private_key()))
    assert response.status_code == 200, response.text
    ssh = env.item("ssh-1")["sshKey"]
    assert (ssh["publicKey"], ssh["keyFingerprint"]) == (
        "ssh-ed25519 AAAAC3Nza dev@host",
        "SHA256:abcdef",
    )
    [row] = env.change_log()
    assert row["columns"] == ["sshKey.privateKey"]


def test_a_write_after_a_favorite_toggle_elsewhere_does_not_reuse_the_old_backup(env: AppEnv):
    was = bool(env.item("login-1").get("favorite"))
    ## the Bitwarden web vault stars the item: PUT /ciphers/{id}/partial, which leaves the
    ## revisionDate unchanged on Vaultwarden
    for item in env.bw.items:
        if item["id"] == "login-1":
            item["favorite"] = not was
    response = env.post(env.set_leaf("login-1", "login.username", "x@example.test"))
    assert response.status_code == 200, response.text
    assert response.json()["backup"] != env.login_backup, "the login backup holds the old favorite"


def test_a_write_after_a_folder_move_elsewhere_does_not_reuse_the_old_backup(env: AppEnv):
    ## dragged out of its folder in the web vault: the same partial update, no new revisionDate
    assert env.item("login-1").get("folderId") == "f1"
    for item in env.bw.items:
        if item["id"] == "login-1":
            item["folderId"] = None
    response = env.post(env.set_leaf("login-1", "login.username", "x@example.test"))
    assert response.status_code == 200, response.text
    assert response.json()["backup"] != env.login_backup, "the login backup holds the old folder"


def test_a_change_of_an_archived_item_elsewhere_makes_a_fresh_backup(env: AppEnv):
    """Only archived=true lists an archived item, and the backup holds it, so a change of it
    must keep a write from reusing the backup taken before."""
    [note] = [i for i in env.bw.items if i["id"] == "note-1"]
    env.bw.items.remove(note)
    archived = {**note, "archivedDate": "2026-10-26T13:00:00.000Z"}
    env.bw.archived.append(archived)
    first = env.post(env.set_leaf("login-1", "login.username", "first@example.test"))
    assert first.status_code == 200
    after_first = env.backups[-1]
    assert env.writes.db.backup_fingerprint(after_first) == env.keeper.vault_fingerprint()
    ## another device edits the archived item, which stays archived
    archived["revisionDate"] = "2026-10-26T16:00:00.000Z"
    second = env.post(env.set_leaf("card-1", "card.brand", "Mastercard"))
    assert second.status_code == 200
    assert second.json()["backup"] != after_first, "the backup holds the old archived item"


def test_every_kind_of_operation_has_a_function_that_applies_it():
    """The table APPLY closes the chain the way assert_never closes _check_op's."""
    kinds = set(get_args(Operation.__value__))
    assert kinds
    assert set(APPLY) == kinds


def test_every_entry_of_apply_takes_the_operation_class_it_is_paired_with():
    """What _entry types for ty, checked on the functions as they run: the third parameter of
    each is annotated with the entry's class (by name where the module defers its annotations)."""
    for cls, fn in APPLY.items():
        [_path, _write, op] = inspect.signature(fn).parameters.values()
        assert op.annotation in (cls, cls.__name__), f"the entry of {cls.__name__}"
