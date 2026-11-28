"""Renaming and deleting folders, and restoring both, against the fake bw serve.

The write path through POST /api/operations, the queue (an agent's rename into a secret value,
a folder conflict that blames one entry), the restore plan of a folder row and the two MCP
tools.
"""

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

import bwtidy.backups.reader as reader
from bwtidy.items.coordinates import MASKED
from bwtidy.queue.review import ReviewQueue
from bwtidy.queue.state import as_operation
from bwtidy.queue.wipe import backup_in_use
from tests.support.app_env import MCP_URL, AppEnv
from tests.support.mcp_helpers import call, enable, text
from tests.support.quiet import assert_absent
from tests.support.writes_helpers import SYNC, plain_decrypt

PIN_SECRET = "pin-secret-4711"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppEnv]:
    """An AppEnv whose backups are real zips of the fake vault, so a restore can read them, with
    a second folder Empty that no item is in."""
    monkeypatch.setattr(reader, "decrypt_backup", plain_decrypt)
    env = AppEnv(tmp_path, base_url=MCP_URL, zips=True)
    env.bw.folders.append({"object": "folder", "id": "f2", "name": "Empty"})
    with env:
        env.unlock()
        yield env


def rename(folder_id: str, shown: str, new: str) -> dict[str, Any]:
    return {"kind": "renameFolder", "folderId": folder_id, "shownName": shown, "newName": new}


def delete(folder_id: str, shown: str) -> dict[str, Any]:
    return {"kind": "deleteFolder", "folderId": folder_id, "shownName": shown}


def names(env: AppEnv) -> dict[str, str]:
    return {f["id"]: f["name"] for f in env.bw.folders if f.get("id")}


def queue(env: AppEnv) -> ReviewQueue:
    return env.app.state.bw.queue


def test_a_rename_renames_the_folder_and_writes_no_item(env: AppEnv):
    items = copy.deepcopy(env.bw.items)
    response = env.post(rename("f1", "Work", "Job"))
    assert response.status_code == 200, response.text
    assert names(env) == {"f1": "Job", "f2": "Empty"}
    assert env.bw.put == []
    assert env.bw.items == items
    [row] = env.change_log()
    assert (row["operationKind"], row["result"]) == ("renameFolder", "ok")
    assert (row["itemIds"], row["createdItemIds"], row["columns"]) == ([], [], ["folder"])
    assert row["folderId"] == "f1"
    folders = env.client.get("/api/grid").json()["folders"]
    assert folders == [
        {"id": "f2", "name": "Empty", "items": 0, "trashedItems": 0},
        {"id": "f1", "name": "Job", "items": 1, "trashedItems": 0},
    ]


@pytest.mark.parametrize(
    ("new", "reason"),
    [
        ("", "the new folder name is empty"),
        ("  ", "the new folder name is empty"),
        ("Work", "the folder has that name already"),
        ("empty", 'another folder is named "empty"'),
    ],
)
def test_a_rename_is_refused_for_a_bad_name(env: AppEnv, new: str, reason: str):
    response = env.post(rename("f1", "Work", new))
    assert response.status_code == 422
    assert reason in response.json()["detail"]
    assert names(env) == {"f1": "Work", "f2": "Empty"}
    assert env.change_log() == []


def test_a_rename_only_in_case_is_allowed(env: AppEnv):
    assert env.post(rename("f1", "Work", "work")).status_code == 200
    assert names(env)["f1"] == "work"


def test_a_name_equal_to_a_secret_of_an_item_in_the_folder_is_refused(env: AppEnv):
    response = env.post(rename("f1", "Work", PIN_SECRET))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "folder" in detail and "fields.PIN" in detail
    assert_absent(PIN_SECRET, response.text, "the response")
    assert names(env)["f1"] == "Work"
    ## the empty folder holds no item whose secret it would show
    assert env.post(rename("f2", "Empty", PIN_SECRET)).status_code == 200


def test_a_name_equal_to_a_secret_of_an_item_in_the_trash_is_refused(env: AppEnv):
    env.post(env.op("trashItem", "login-1"))
    response = env.post(rename("f1", "Work", PIN_SECRET))
    assert response.status_code == 422
    assert_absent(PIN_SECRET, response.text, "the response")


@pytest.mark.parametrize("kind", ["rename", "delete"])
def test_a_folder_renamed_elsewhere_is_a_conflict(env: AppEnv, kind: str):
    env.bw.folders[-1]["name"] = "Elsewhere"
    op = rename("f2", "Empty", "Other") if kind == "rename" else delete("f2", "Empty")
    response = env.post(op)
    assert response.status_code == 409
    assert response.json()["conflicts"] == [
        {"folderId": "f2", "expected": "Empty", "actual": "Elsewhere"}
    ]
    assert "1 folder changed" in response.json()["detail"]
    assert names(env)["f2"] == "Elsewhere"


def test_a_folder_deleted_elsewhere_is_a_conflict(env: AppEnv):
    env.bw.folders.pop()
    response = env.post(rename("f2", "Empty", "Other"))
    assert response.status_code == 409
    assert response.json()["conflicts"] == [{"folderId": "f2", "expected": "Empty", "actual": None}]


def rename_elsewhere_at_sync(env: AppEnv, number: int, folder_id: str, name: str) -> None:
    """The sync with this number, counted from now, brings in another device's folder rename
    (sync 1 is the write path's own at the start, sync 2 the one after the backup)."""
    real = env.bw.handler
    seen = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == SYNC:
            seen[0] += 1
            if seen[0] == number:
                next(f for f in env.bw.folders if f.get("id") == folder_id)["name"] = name
        return real(request)

    vars(env.bw)["handler"] = handler


def test_a_rename_elsewhere_after_the_check_fails_the_write_as_a_conflict(env: AppEnv):
    """A rename elsewhere that a sync inside the batch brings in, after a create synced."""
    create = {"kind": "createItem", "name": "Brand new", "type": 2}
    rename_elsewhere_at_sync(env, 3, "f2", "Elsewhere")
    response = env.post(create, rename("f2", "Empty", "Other"))
    assert response.status_code == 500, response.text
    answer = response.json()
    assert (answer["applied"], answer["failed"]) == ([0], 1)
    assert answer["conflicts"] == [{"folderId": "f2", "expected": "Empty", "actual": "Elsewhere"}]
    assert names(env)["f2"] == "Elsewhere"


def test_a_delete_deletes_an_empty_folder_and_writes_no_item(env: AppEnv):
    items = copy.deepcopy(env.bw.items)
    response = env.post(delete("f2", "Empty"))
    assert response.status_code == 200, response.text
    assert names(env) == {"f1": "Work"}
    assert env.bw.items == items
    [row] = env.change_log()
    assert (row["operationKind"], row["itemIds"], row["columns"], row["folderId"]) == (
        "deleteFolder",
        [],
        ["folder"],
        "f2",
    )


def test_a_delete_of_a_folder_that_holds_items_is_refused(env: AppEnv):
    response = env.post(delete("f1", "Work"))
    assert response.status_code == 422
    assert "the folder holds 1 item, move them out first" in response.json()["detail"]
    assert names(env)["f1"] == "Work"


def test_a_delete_of_a_folder_with_an_item_in_the_trash_is_refused(env: AppEnv):
    env.post(env.op("trashItem", "login-1"))
    response = env.post(delete("f1", "Work"))
    assert response.status_code == 422
    assert "1 item in the trash" in response.json()["detail"]
    assert names(env)["f1"] == "Work"


def archive(env: AppEnv, item_id: str, *, trash: bool = False) -> None:
    """Archive an item in the fake bw serve, as another device would: only archived=true lists
    it then, with trash=true when it is in the trash too."""
    item = env.item(item_id)
    env.bw.items.remove(item)
    archived = {**item, "archivedDate": "2026-10-26T13:00:00.000Z"}
    if trash:
        archived["deletedDate"] = "2026-10-26T14:00:00.000Z"
    env.bw.archived.append(archived)


def test_a_delete_of_a_folder_that_holds_only_an_archived_item_is_refused(env: AppEnv):
    """Bitwarden would take the archived item out of the folder too."""
    archive(env, "login-1")
    response = env.post(delete("f1", "Work"))
    assert response.status_code == 422
    assert "the folder holds 1 item in the archive" in response.json()["detail"]
    assert names(env)["f1"] == "Work"
    assert env.bw.archived[0]["folderId"] == "f1"


def test_a_delete_of_a_folder_with_an_archived_item_in_the_trash_is_refused(env: AppEnv):
    archive(env, "login-1", trash=True)
    response = env.post(delete("f1", "Work"))
    assert response.status_code == 422
    assert "1 item in the trash" in response.json()["detail"]
    assert names(env)["f1"] == "Work"


def test_an_item_archived_into_the_folder_after_the_check_fails_the_delete(env: AppEnv):
    """The write path lists the archived items again right before the delete."""
    real = env.bw.handler
    seen = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == SYNC:
            seen[0] += 1
            if seen[0] == 3:
                env.item("card-1")["folderId"] = "f2"
                archive(env, "card-1")
        return real(request)

    vars(env.bw)["handler"] = handler
    create = {"kind": "createItem", "name": "Brand new", "type": 2}
    response = env.post(create, delete("f2", "Empty"))
    assert response.status_code == 500, response.text
    assert "the folder holds 1 item in the archive" in response.json()["detail"]
    assert names(env)["f2"] == "Empty"


@pytest.mark.parametrize("trash", [False, True])
def test_a_name_equal_to_a_secret_of_an_archived_item_is_refused(env: AppEnv, trash: bool):
    archive(env, "login-1", trash=trash)
    response = env.post(rename("f1", "Work", PIN_SECRET))
    assert response.status_code == 422
    assert_absent(PIN_SECRET, response.text, "the response")
    assert names(env)["f1"] == "Work"


def test_an_item_moved_in_after_the_check_fails_the_delete(env: AppEnv):
    """The write path lists the items again right before the delete."""
    real = env.bw.handler
    seen = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == SYNC:
            seen[0] += 1
            if seen[0] == 3:
                env.item("card-1")["folderId"] = "f2"
        return real(request)

    vars(env.bw)["handler"] = handler
    create = {"kind": "createItem", "name": "Brand new", "type": 2}
    response = env.post(create, delete("f2", "Empty"))
    assert response.status_code == 500, response.text
    assert "the folder holds 1 item" in response.json()["detail"]
    assert names(env)["f2"] == "Empty"
    assert env.item("card-1")["folderId"] == "f2"


def test_the_restore_of_a_rename_renames_the_folder_back(env: AppEnv):
    env.post(rename("f1", "Work", "Job"))
    [row] = env.change_log()
    plan = env.plan(row["id"])
    assert plan.status_code == 200, plan.text
    answer = plan.json()
    assert answer["items"] == []
    assert answer["folder"] == {
        "folderId": "f1",
        "name": "Job",
        "action": "renameBack",
        "text": 'rename back to "Work"',
    }
    assert answer["summary"] == f'Restore the folder "Job" from {row["backup"]}'
    assert answer["operation"] == {
        "kind": "restoreFolder",
        "backup": row["backup"],
        "folderId": "f1",
        "changeLogId": row["id"],
    }
    response = env.restore(row["id"])
    assert response.status_code == 200, response.text
    assert names(env)["f1"] == "Work"
    restored = env.change_log()[0]
    assert (restored["operationKind"], restored["folderId"]) == ("restoreFolder", "f1")
    assert (restored["restoredBackup"], restored["restoredChangeId"]) == (row["backup"], row["id"])
    assert restored["columns"] == ["folder"]


def test_the_restore_of_a_delete_creates_the_folder_again(env: AppEnv):
    env.post(delete("f2", "Empty"))
    [row] = env.change_log()
    plan = env.plan(row["id"]).json()
    assert plan["folder"]["action"] == "createAgain"
    assert plan["folder"]["text"] == 'create again as "Empty"'
    assert plan["summary"] == f"Restore a deleted folder from {row['backup']}"
    assert env.restore(row["id"]).status_code == 200
    [again] = [f for f in env.bw.folders if f.get("name") == "Empty"]
    assert again["id"] != "f2"
    ## the restore's row names the folder it made
    assert env.change_log()[0]["folderId"] == again["id"]


def test_a_restore_leaves_a_folder_that_has_the_name_already(env: AppEnv):
    env.post(delete("f2", "Empty"))
    [row] = env.change_log()
    env.bw.folders.append({"object": "folder", "id": "f3", "name": "Empty"})
    env.reload()
    plan = env.plan(row["id"]).json()
    assert plan["folder"]["action"] == "leave"
    folders = len(env.bw.folders)
    assert env.restore(row["id"]).status_code == 200
    assert len(env.bw.folders) == folders


def test_a_restore_is_refused_when_another_folder_took_the_name(env: AppEnv):
    env.post(rename("f2", "Empty", "Spare"))
    [row] = env.change_log()
    env.post(rename("f1", "Work", "Empty"))
    plan = env.plan(row["id"])
    assert plan.status_code == 422
    assert 'another folder is named "Empty"' in plan.json()["detail"]


def test_a_restore_is_refused_for_a_folder_the_backup_lacks(env: AppEnv):
    env.post(env.op("trashItem", "card-1"))
    [row] = env.change_log()
    op = {"kind": "restoreFolder", "backup": row["backup"], "folderId": "nope"}
    response = env.post(op)
    assert response.status_code == 422
    assert "holds no folder with the id nope" in response.json()["detail"]


def test_the_queue_shows_a_folder_entry_as_one_row_of_the_folder(env: AppEnv):
    response = env.client.post(
        "/api/queue", json={"operations": [rename("f1", "Work", "Job"), delete("f2", "Empty")]}
    )
    assert response.status_code == 201, response.text
    entries = env.client.get("/api/queue", params={"status": "pending"}).json()
    assert [e["summary"] for e in entries] == [
        'Rename the folder "Work" to "Job"',
        'Delete the folder "Empty"',
    ]
    rows = [
        (e["diff"][0]["itemName"], e["diff"][0]["before"], e["diff"][0]["after"]) for e in entries
    ]
    assert rows == [("Work", "Work", "Job"), ("Empty", "Empty", None)]
    assert all(len(e["diff"]) == 1 and e["diff"][0]["itemId"] is None for e in entries)
    assert all(e["diff"][0]["column"] == "folder" for e in entries)


def test_a_folder_conflict_fails_only_the_entry_of_that_folder(env: AppEnv):
    ids = [
        queue(env).enqueue(as_operation(rename("f2", "Empty", "Spare")), "spare"),
        queue(env).enqueue(as_operation(env.set_leaf("card-1", "name", "Visa")), "visa"),
    ]
    env.bw.folders[-1]["name"] = "Elsewhere"
    response = env.client.post("/api/queue/approve", json={"ids": ids})
    env.settle()
    assert response.status_code == 200, response.text
    assert (response.json()["applied"], response.json()["failed"]) == ([ids[1]], [ids[0]])
    failed = env.client.get("/api/queue", params={"status": "failed"}).json()
    assert failed[0]["result"]["detail"] == "the folder changed since the change was queued"
    assert failed[0]["result"]["conflicts"] == [
        {"folderId": "f2", "expected": "Empty", "actual": "Elsewhere"}
    ]


def test_an_agents_rename_into_a_secret_is_stored_rejected_without_the_value(env: AppEnv):
    entry_id = queue(env).enqueue(as_operation(rename("f1", "Work", PIN_SECRET)), "summary")
    [rejected] = env.client.get("/api/queue", params={"status": "rejected"}).json()
    assert rejected["id"] == entry_id
    assert rejected["summary"] == (
        'Refused proposal for the folder "Work": it would put a secret value of an item in it'
        " into its name"
    )
    assert rejected["operation"]["newName"] == MASKED
    assert rejected["diff"][0]["after"] == MASKED
    assert rejected["result"]["error"] == "secretValue"
    assert_absent(PIN_SECRET, json.dumps(rejected), "the refusal")
    ## from the UI the same rename is refused with the error
    refused = [rename("f1", "Work", PIN_SECRET)]
    assert env.client.post("/api/queue", json={"operations": refused}).status_code == 422


def test_an_agents_rename_into_a_secret_of_an_archived_item_shows_the_item(env: AppEnv):
    archive(env, "login-1")
    env.reload()
    queue(env).enqueue(as_operation(rename("f1", "Work", PIN_SECRET)), "summary")
    [rejected] = env.client.get("/api/queue", params={"status": "rejected"}).json()
    assert [(r["itemId"], r["after"]) for r in rejected["diff"]] == [("login-1", MASKED)]
    assert_absent(PIN_SECRET, json.dumps(rejected), "the refusal")


def test_the_mcp_tools_propose_a_rename_and_a_delete(env: AppEnv):
    enable(env)
    answer = json.loads(text(call(env, "list_folders")))
    assert {"name": "Empty", "items": 0, "itemsInTrash": 0} in answer["folders"]
    renamed = json.loads(text(call(env, "propose_rename_folder", folder="Work", new_name="Job")))
    assert renamed["summary"] == 'Rename the folder "Work" to "Job"'
    deleted = json.loads(text(call(env, "propose_delete_folder", folder="Empty", reason="unused")))
    assert deleted["summary"] == 'Delete the folder "Empty". Reason: unused'
    pending = env.client.get("/api/queue", params={"status": "pending"}).json()
    assert [(e["source"], e["operation"]) for e in pending] == [
        ("mcp", rename("f1", "Work", "Job")),
        ("mcp", delete("f2", "Empty")),
    ]
    assert names(env) == {"f1": "Work", "f2": "Empty"}


def test_the_mcp_tools_refuse_an_unknown_folder_and_a_folder_with_items(env: AppEnv):
    enable(env)
    unknown = call(env, "propose_rename_folder", folder="Nope", new_name="X")
    assert unknown.is_error and "no folder named 'Nope'" in text(unknown)
    held = call(env, "propose_delete_folder", folder="Work")
    assert held.is_error and "the folder holds 1 item" in text(held)
    ## a secret value is refused silently, the answer looks like an accepted one
    secret = call(env, "propose_rename_folder", folder="Work", new_name=PIN_SECRET)
    assert not secret.is_error
    assert json.loads(text(secret))["summary"] == f'Rename the folder "Work" to "{PIN_SECRET}"'
    assert env.client.get("/api/queue", params={"status": "pending"}).json() == []


def test_a_queued_folder_restore_keeps_its_backup_in_use(env: AppEnv):
    env.post(rename("f1", "Work", "Job"))
    [row] = env.change_log()
    operation = env.plan(row["id"]).json()["operation"]
    response = env.client.post("/api/queue", json={"operations": [operation]})
    assert response.status_code == 201, response.text
    [entry_id] = response.json()["ids"]
    assert backup_in_use(env.db, row["backup"]) == (
        f"Backup {row['backup']} is what the queued restore #{entry_id} reads."
    )
