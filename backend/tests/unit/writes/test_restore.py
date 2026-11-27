"""restoreItems (writes/restore.py) through POST /api/operations against real backup zips of the
fake vault: each action of the plan, the collections, the attachments from the backup, the
refusals and the restore of a restore."""

import copy
import zipfile
from typing import Any

import httpx2 as httpx
import pytest

import bwtidy.backups.reader as reader
from tests.support.app_env import AppEnv, zip_backup
from tests.support.fake_bw import fail
from tests.support.queue_helpers import enqueue_ui, entry
from tests.support.sample_vault import CARD, LOGIN
from tests.support.writes_helpers import (
    PHONE,
    create,
    edit_at_sync,
    give_note_attachments,
    plain_decrypt,
    without_members,
)


def test_a_restore_after_a_create_keeps_a_change_its_sync_brought_in(renv: AppEnv):
    assert renv.post(renv.set_leaf("login-1", "notes", "first")).status_code == 200
    [row] = renv.change_log()
    plan = renv.plan(row["id"])
    assert plan.status_code == 200, plan.text
    edit_at_sync(renv, 3, "login-1")
    response = renv.post(create(type=1, name="Brand new"), plan.json()["operation"])
    assert response.status_code == 500, response.text
    answer = response.json()
    assert (answer["applied"], answer["failed"]) == ([0], 1)
    assert [c["itemId"] for c in answer["conflicts"]] == ["login-1"]
    assert renv.item("login-1")["login"]["username"] == PHONE
    assert renv.item("login-1")["notes"] == "first"


def test_a_restore_after_an_earlier_write_of_the_batch_puts_the_item_back(renv: AppEnv):
    assert renv.post(renv.set_leaf("login-1", "login.username", "first")).status_code == 200
    [row] = renv.change_log()
    plan = renv.plan(row["id"])
    assert plan.status_code == 200, plan.text
    ## the restore's revisionDate is the one the grid shows, the edit before it writes the item
    edit = renv.set_leaf("login-1", "name", "Renamed")
    response = renv.post(edit, create(type=1, name="Brand new"), plan.json()["operation"])
    assert response.status_code == 200, response.text
    login = renv.item("login-1")
    restored = (LOGIN["name"], LOGIN["login"]["username"])
    assert (login["name"], login["login"]["username"]) == restored


def test_restore_puts_an_item_back_as_the_backup_holds_it(renv: AppEnv):
    env = renv
    before = copy.deepcopy(env.item("login-1"))
    assert (
        env.post(env.set_leaf("login-1", "login.username", "changed@example.test")).status_code
        == 200
    )
    [row] = env.change_log()
    assert row["createdItemIds"] == []
    changed = copy.deepcopy(env.item("login-1"))
    assert changed["login"]["username"] == "changed@example.test"
    ## the background backup after the write holds the vault as it is now
    assert len(env.backups) == 2

    plan = env.plan(row["id"])
    assert plan.status_code == 200, plan.text
    assert plan.json() == {
        "changeLogId": row["id"],
        "backup": env.login_backup,
        "summary": f"Restore 1 item from {env.login_backup}",
        "operation": {
            "kind": "restoreItems",
            "backup": env.login_backup,
            "itemIds": ["login-1"],
            "revisionDates": {"login-1": changed["revisionDate"]},
            "changeLogId": row["id"],
        },
        "items": [
            {
                "itemId": "login-1",
                "itemName": "Example Mail",
                "action": "put",
                "text": "put back as before",
            }
        ],
    }
    response = env.post(plan.json()["operation"])
    assert response.status_code == 200, response.text
    ## the restore's own backup is the one taken after the write, it holds the changed item
    assert response.json() == {"applied": 1, "backup": env.backups[1]}
    saved = env.backup_items(env.backups[1])["login-1"]
    assert saved["login"]["username"] == "changed@example.test"
    assert len(env.backups) == 3
    [(item_id, body)] = env.bw.put[1:]
    assert item_id == "login-1"
    ## the backup's item with the current revisionDate, nothing else
    assert body == {**before, "revisionDate": changed["revisionDate"]}
    after = env.item("login-1")
    assert after["login"]["username"] == LOGIN["login"]["username"]
    assert after["revisionDate"] != changed["revisionDate"]
    log = env.change_log()
    assert [(e["operationKind"], e["itemIds"], e["createdItemIds"], e["result"]) for e in log] == [
        ("restoreItems", ["login-1"], [], "ok"),
        ("setLeaf", ["login-1"], [], "ok"),
    ]
    assert log[0]["source"] == "ui"
    assert log[0]["backup"] == env.backups[1]
    ## the restore's row names the backup it put back and the row it was planned for
    assert log[0]["restoredBackup"] == env.login_backup
    assert log[0]["restoredChangeId"] == row["id"]
    assert log[1]["restoredBackup"] is None
    assert log[1]["restoredChangeId"] is None
    ## the change is marked as undone by the restore, the restore itself is not
    assert log[1]["restoredBy"] == {"id": log[0]["id"], "at": log[0]["at"], "itemIds": ["login-1"]}
    assert log[0]["restoredBy"] is None


def test_restore_puts_the_collections_back_with_their_own_call(renv: AppEnv):
    env = renv
    assert (
        env.post(env.op("setCollections", "note-1", collectionNames=["Youth"])).status_code == 200
    )
    [row] = env.change_log()
    assert env.item("note-1")["collectionIds"] == ["c1"]
    response = env.restore(row["id"])
    assert response.status_code == 200, response.text
    [(item_id, body)] = env.bw.put
    assert item_id == "note-1"
    assert env.bw.requests.count(("PUT", "/object/item-collections/note-1")) == 2
    assert env.item("note-1")["collectionIds"] == ["c1", "c2"]
    ## the revisionDate chain follows both writes, so an edit based on the one before passes
    assert env.post(env.set_leaf("note-1", "notes", "after the restore")).status_code == 200


def test_a_restore_with_a_change_in_between_takes_its_own_backup_first(renv: AppEnv):
    env = renv
    assert (
        env.post(env.set_leaf("login-1", "login.username", "changed@example.test")).status_code
        == 200
    )
    [row] = env.change_log()
    ## another device changes a card after the background backup
    env.item("card-1")["revisionDate"] = "2026-10-26T16:00:00.000Z"
    env.item("card-1")["card"]["brand"] = "Mastercard"
    response = env.restore(row["id"])
    assert response.status_code == 200, response.text
    ## a fresh backup, taken before the PUT, holds the card's change and the changed login
    fresh = response.json()["backup"]
    assert fresh == env.backups[2]
    saved = env.backup_items(fresh)
    assert saved["card-1"]["card"]["brand"] == "Mastercard"
    assert saved["login-1"]["login"]["username"] == "changed@example.test"
    assert env.item("login-1")["login"]["username"] == LOGIN["login"]["username"]


def test_restore_of_a_convert_untrashes_the_old_item_and_trashes_the_new(renv: AppEnv):
    env = renv
    give_note_attachments(env)
    old = copy.deepcopy(env.item("note-1"))
    response = env.post(
        env.op("convertItem", "note-1", newType=1, mapping={"login.username": "fields.dm.crypt.v2"})
    )
    assert response.status_code == 200, response.text
    [row] = env.change_log()
    [new] = env.bw.created[1:]
    assert row["itemIds"] == ["note-1"]
    assert row["createdItemIds"] == [new["id"]]
    assert env.item(new["id"])["type"] == 1
    assert [i["id"] for i in env.bw.trashed] == ["note-1"]

    plan = env.plan(row["id"])
    assert plan.status_code == 200, plan.text
    assert plan.json()["operation"]["itemIds"] == ["note-1", new["id"]]
    assert plan.json()["operation"]["revisionDates"] == {
        new["id"]: env.item(new["id"])["revisionDate"]
    }
    assert [(i["itemId"], i["action"], i["text"]) for i in plan.json()["items"]] == [
        ("note-1", "untrash", "take out of the trash and put back as before"),
        (new["id"], "trash", "move to the trash"),
    ]
    response = env.post(plan.json()["operation"])
    assert response.status_code == 200, response.text
    assert env.bw.restored == ["note-1"]
    [(item_id, body)] = env.bw.put
    assert item_id == "note-1"
    assert body["revisionDate"] != old["revisionDate"]
    assert {k: v for k, v in body.items() if k != "revisionDate"} == {
        k: v for k, v in old.items() if k != "revisionDate"
    }
    assert env.item("note-1")["type"] == 2
    assert env.item("note-1")["attachments"] == old["attachments"]
    assert [i["id"] for i in env.bw.trashed] == [new["id"]]
    assert new["id"] not in env.revisions()
    ## no attachment was uploaded or created anew, the old item kept its own
    assert len(env.bw.uploads) == 2
    assert len(env.bw.created) == 2
    log = env.change_log()
    assert (log[0]["operationKind"], log[0]["itemIds"], log[0]["createdItemIds"]) == (
        "restoreItems",
        ["note-1", new["id"]],
        [],
    )

    ## the restore can be restored: its backup holds the new item active and the old in the trash
    response = env.restore(log[0]["id"])
    assert response.status_code == 200, response.text
    assert [i["id"] for i in env.bw.trashed] == ["note-1"]
    assert env.bw.restored == ["note-1", new["id"]]
    assert env.item(new["id"])["type"] == 1


def test_restore_of_a_move_to_personal_recreates_nothing_and_untrashes_the_original(renv: AppEnv):
    env = renv
    give_note_attachments(env)
    assert env.post(env.op("moveToPersonal", "note-1")).status_code == 200
    [row] = env.change_log()
    [new] = env.bw.created[1:]
    assert row["createdItemIds"] == [new["id"]]
    response = env.restore(row["id"])
    assert response.status_code == 200, response.text
    assert env.item("note-1")["organizationId"] == "o1"
    assert env.item("note-1")["collectionIds"] == ["c2", "c1"]
    assert [i["id"] for i in env.bw.trashed] == [new["id"]]


def test_restore_moves_an_item_back_out_of_an_organization_by_creating_it_anew(renv: AppEnv):
    env = renv
    before = copy.deepcopy(env.item("login-1"))
    response = env.post(
        env.op(
            "moveToOrganization", "login-1", organizationName="Family", collectionNames=["shared"]
        )
    )
    assert response.status_code == 200, response.text
    [row] = env.change_log()
    assert env.item("login-1")["organizationId"] == "o1"
    plan = env.plan(row["id"])
    assert plan.status_code == 200, plan.text
    assert [(i["itemId"], i["action"]) for i in plan.json()["items"]] == [("login-1", "recreate")]
    created = len(env.bw.created)
    response = env.post(plan.json()["operation"])
    assert response.status_code == 200, response.text
    [posted] = env.bw.posted[created:]
    [new] = env.bw.created[created:]
    for key in ("id", "revisionDate", "attachments", "passwordHistory"):
        assert key not in posted, key
    assert "fido2Credentials" not in posted["login"]
    assert (posted["organizationId"], posted["collectionIds"]) == (None, [])
    assert posted["folderId"] == "f1"
    assert posted["login"]["username"] == before["login"]["username"]
    assert posted["fields"] == before["fields"]
    ## the attachment comes from the current item, the old one goes to the trash
    assert env.bw.uploads == [(new["id"], "codes.txt")]
    assert env.bw.attachments[(new["id"], "up-1")] == b"recovery codes"
    assert [i["id"] for i in env.bw.trashed] == ["login-1"]
    assert env.bw.put == []
    log = env.change_log()
    assert (log[0]["itemIds"], log[0]["createdItemIds"]) == (["login-1"], [new["id"]])


def test_restore_moves_an_item_back_into_its_organization(renv: AppEnv):
    env = renv
    before = copy.deepcopy(env.item("note-1"))
    ## some other client took the note out of the organization, keeping its id
    env.item("note-1")["organizationId"] = None
    env.item("note-1")["collectionIds"] = []
    env.item("note-1")["revisionDate"] = "2026-10-26T16:00:00.000Z"
    env.reload()
    row_id = env.writes.db.record("ui", "setCollections", ["note-1"], "ok", env.login_backup)
    plan = env.plan(row_id)
    assert plan.status_code == 200, plan.text
    assert [(i["action"], i["text"]) for i in plan.json()["items"]] == [
        ("toOrganization", "move back into the organization and put back as before")
    ]
    response = env.post(plan.json()["operation"])
    assert response.status_code == 200, response.text
    assert ("POST", "/move/note-1/o1") in env.bw.requests
    [(item_id, body)] = env.bw.put
    assert item_id == "note-1"
    assert body["collectionIds"] == before["collectionIds"]
    assert env.item("note-1")["organizationId"] == "o1"
    assert env.item("note-1")["collectionIds"] == ["c2", "c1"]
    assert env.item("note-1")["fields"] == before["fields"]


def test_a_failed_read_of_a_trashed_item_makes_no_duplicate(
    renv: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    env = renv
    assert env.post(env.op("trashItem", "login-1")).status_code == 200
    [row] = env.change_log()
    env.reload()
    plan = env.plan(row["id"]).json()
    before = len(env.bw.created)
    real = env.bw.handler
    failed = []

    def flaky(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == ("GET", "/object/item/login-1") and not failed:
            failed.append(True)
            return fail("forced failure")
        return real(request)

    monkeypatch.setattr(env.bw, "handler", flaky)
    response = env.post(plan["operation"])
    assert response.status_code == 200, response.text
    assert len(env.bw.created) == before
    assert env.bw.restored == ["login-1"]


def test_restore_waits_for_bw_serve_after_the_move_into_the_organization(renv: AppEnv):
    env = renv
    env.item("note-1")["organizationId"] = None
    env.item("note-1")["collectionIds"] = []
    env.item("note-1")["revisionDate"] = "2026-10-26T16:00:00.000Z"
    env.reload()
    row_id = env.writes.db.record("ui", "setCollections", ["note-1"], "ok", env.login_backup)
    plan = env.plan(row_id).json()
    env.bw.empty_lists_after_sync = 2
    response = env.post(plan["operation"])
    assert response.status_code == 200, response.text
    assert env.item("note-1")["organizationId"] == "o1"


def test_restore_creates_an_item_anew_when_the_trash_was_emptied(renv: AppEnv):
    env = renv
    before = copy.deepcopy(env.item("login-1"))
    assert env.post(env.op("trashItem", "login-1")).status_code == 200
    [row] = env.change_log()
    ## the trash was emptied for good
    env.bw.trashed = []
    env.reload()
    plan = env.plan(row["id"])
    assert plan.status_code == 200, plan.text
    assert plan.json()["operation"]["revisionDates"] == {}
    assert [(i["itemId"], i["itemName"], i["action"], i["text"]) for i in plan.json()["items"]] == [
        (
            "login-1",
            "Example Mail",
            "create",
            "create anew (passkeys and password history are lost)",
        )
    ]
    created = len(env.bw.created)
    response = env.post(plan.json()["operation"])
    assert response.status_code == 200, response.text
    [posted] = env.bw.posted[created:]
    [new] = env.bw.created[created:]
    for key in ("id", "revisionDate", "attachments", "passwordHistory"):
        assert key not in posted, key
    assert "fido2Credentials" not in posted["login"]
    assert posted["name"] == before["name"]
    assert posted["folderId"] == before["folderId"]
    assert posted["favorite"] == before["favorite"]
    assert posted["fields"] == before["fields"]
    assert posted["login"]["password"] == before["login"]["password"]
    assert posted["login"]["uris"] == before["login"]["uris"]
    ## the attachment comes from item-attachments in the backup, by item id
    assert env.bw.uploads == [(new["id"], "codes.txt")]
    assert env.bw.attachments[(new["id"], "up-1")] == b"recovery codes"
    assert new["id"] in env.revisions()
    log = env.change_log()
    assert (log[0]["operationKind"], log[0]["itemIds"], log[0]["createdItemIds"]) == (
        "restoreItems",
        ["login-1"],
        [new["id"]],
    )


def test_restore_creates_an_org_item_anew_with_its_org_attachments(renv: AppEnv):
    env = renv
    give_note_attachments(env)
    assert env.post(env.op("trashItem", "note-1")).status_code == 200
    [row] = env.change_log()
    env.bw.trashed = []
    env.reload()
    created = len(env.bw.created)
    response = env.restore(row["id"])
    assert response.status_code == 200, response.text
    [posted] = env.bw.posted[created:]
    [new] = env.bw.created[created:]
    assert posted["organizationId"] == "o1"
    assert posted["collectionIds"] == ["c2", "c1"]
    assert env.bw.uploads == [(new["id"], "config.bin"), (new["id"], "notes.txt")]
    assert env.bw.attachments[(new["id"], "up-1")] == b"binary config"
    assert env.bw.attachments[(new["id"], "up-2")] == b"plain notes"


def test_restore_refusals_write_nothing(renv: AppEnv):
    env = renv
    assert env.post(env.set_leaf("login-1", "notes", "changed")).status_code == 200
    [row] = env.change_log()
    revision = env.item("login-1")["revisionDate"]

    def refused(detail: str, **changes: Any) -> None:
        op = {
            "kind": "restoreItems",
            "backup": row["backup"],
            "itemIds": ["login-1"],
            "revisionDates": {"login-1": revision},
            **changes,
        }
        response = env.post(op)
        assert response.status_code == 422, response.text
        assert detail in response.json()["detail"]
        queued = env.client.post("/api/queue", json={"operations": [op]})
        assert queued.status_code == 422, queued.text
        assert detail in queued.json()["detail"]

    note = next(i for i in env.bw.items if i["name"] == "bwtidy")
    refused(
        "the bwtidy note is not restored this way, its templates are restored by saving them "
        "on the Types page",
        itemIds=["login-1", note["id"]],
        revisionDates={"login-1": revision, note["id"]: note["revisionDate"]},
    )
    refused("the backup 20200101T000000Z.zip.age does not exist", backup="20200101T000000Z.zip.age")
    refused("is not a backup name", backup="../bwtidy.sqlite")
    refused("itemIds is empty", itemIds=[], revisionDates={})
    ## a backup encrypted with another password
    other = "20260101T000000Z.zip.age"
    (env.data_dir / "backups" / other).write_bytes(zip_backup(env.bw))
    env.keeper.reader.clear()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(reader, "decrypt_backup", lambda path, password: plain_decrypt(path, "no"))
        refused(f"the backup {other} does not decrypt with the session's password", backup=other)
    assert len(env.bw.put) == 1
    assert len(env.change_log()) == 1
    assert env.item("login-1")["notes"] == "changed"
    ## the restore-plan route answers the same for the tool note row, and 404 for no row
    note_row = env.writes.db.record("ui", "setLeaf", [note["id"]], "ok", row["backup"])
    assert env.plan(note_row).status_code == 422
    assert env.plan(99999).status_code == 404


def test_restore_refuses_an_item_that_became_active_since_it_was_planned(renv: AppEnv):
    env = renv
    assert env.post(env.op("trashItem", "card-1")).status_code == 200
    [row] = env.change_log()
    plan = env.plan(row["id"])
    assert plan.status_code == 200, plan.text
    assert plan.json()["items"][0]["action"] == "untrash"
    ## somebody takes the card out of the trash before the restore runs
    env.bw.trashed = []
    env.bw.items.append({**copy.deepcopy(CARD), "revisionDate": "2026-10-26T17:00:00.000Z"})
    response = env.post(plan.json()["operation"])
    assert response.status_code == 422, response.text
    assert "card-1 is active now but was not when the restore was planned" in response.text
    assert env.bw.put == []


def gone(env: AppEnv, item_id: str) -> dict[str, Any]:
    """Trash an item through the app and empty the trash, answering the change log row."""
    assert env.post(env.op("trashItem", item_id)).status_code == 200
    row = env.change_log()[0]
    assert row["itemIds"] == [item_id]
    env.bw.trashed = []
    env.reload()
    return row


def test_restore_gives_two_items_of_the_same_name_each_their_own_attachments(renv: AppEnv):
    env = renv
    twin = {
        **copy.deepcopy(LOGIN),
        "id": "login-twin",
        "attachments": [{"id": "a9", "fileName": "codes.txt", "size": "81"}],
        "revisionDate": "2026-10-26T16:00:00.000Z",
    }
    env.bw.items.append(twin)
    env.bw.attachments[("login-twin", "a9")] = b"twin codes"
    env.reload()
    first = gone(env, "login-1")
    second = gone(env, "login-twin")
    created = len(env.bw.created)
    assert env.restore(second["id"]).status_code == 200
    assert env.restore(first["id"]).status_code == 200
    [new_twin, new_login] = env.bw.created[created:]
    assert new_twin["name"] == new_login["name"] == LOGIN["name"]
    assert env.bw.uploads == [(new_twin["id"], "codes.txt"), (new_login["id"], "codes.txt")]
    assert env.bw.attachments[(new_twin["id"], "up-1")] == b"twin codes"
    assert env.bw.attachments[(new_login["id"], "up-2")] == b"recovery codes"


def test_restore_gives_a_file_name_used_twice_its_original_name_back(renv: AppEnv):
    env = renv
    item = env.item("login-1")
    item["attachments"].append({"id": "a2", "fileName": "codes.txt", "size": "81"})
    env.bw.attachments[("login-1", "a2")] = b"second codes"
    item["revisionDate"] = "2026-10-26T16:00:00.000Z"
    env.reload()
    row = gone(env, "login-1")
    with zipfile.ZipFile(env.data_dir / "backups" / row["backup"]) as archive:
        members = sorted(m for m in archive.namelist() if m.startswith("item-attachments/"))
    assert members == [
        "item-attachments/login-1/a2-codes.txt",
        "item-attachments/login-1/codes.txt",
    ]
    created = len(env.bw.created)
    response = env.restore(row["id"])
    assert response.status_code == 200, response.text
    [new] = env.bw.created[created:]
    assert env.bw.uploads == [(new["id"], "codes.txt"), (new["id"], "codes.txt")]
    assert env.bw.attachments[(new["id"], "up-1")] == b"recovery codes"
    assert env.bw.attachments[(new["id"], "up-2")] == b"second codes"


def test_a_backup_without_item_attachments_refuses_only_items_with_attachments(renv: AppEnv):
    """No layout of an older backup is read: a backup without item-attachments at all lacks
    the attachments its items list, like one that lacks a single member."""
    env = renv
    give_note_attachments(env)
    env.item("note-1")["revisionDate"] = "2026-10-26T16:00:00.000Z"
    env.reload()
    card = gone(env, "card-1")
    login = gone(env, "login-1")
    note = gone(env, "note-1")
    for backup in (env.data_dir / "backups").iterdir():
        plain = without_members(backup.read_bytes(), lambda m: m.startswith("item-attachments/"))
        backup.write_bytes(plain)
    created = len(env.bw.created)
    for row, item_id in ((login, "login-1"), (note, "note-1")):
        expected = f"item {item_id} cannot be created anew: the backup lacks one of its attachments"
        plan = env.plan(row["id"])
        assert plan.status_code == 422, plan.text
        assert expected in plan.json()["detail"]
        op = {
            "kind": "restoreItems",
            "backup": row["backup"],
            "itemIds": [item_id],
            "revisionDates": {},
        }
        response = env.post(op)
        assert response.status_code == 422, response.text
        assert expected in response.json()["detail"]
        assert env.client.post("/api/queue", json={"operations": [op]}).status_code == 422
    assert len(env.bw.created) == created
    assert env.bw.uploads == []
    ## an item without attachments is created anew from such a backup
    response = env.restore(card["id"])
    assert response.status_code == 200, response.text
    [new] = env.bw.created[created:]
    assert new["name"] == CARD["name"]
    assert env.bw.uploads == []


def test_restore_of_a_create_item_trashes_the_new_item(renv: AppEnv):
    env = renv
    response = env.post(create(type=1, name="Short lived", generatePassword=True))
    assert response.status_code == 200, response.text
    [row] = env.change_log()
    [new_id] = row["createdItemIds"]
    plan = env.plan(row["id"])
    assert plan.status_code == 200, plan.text
    assert [(i["itemId"], i["action"]) for i in plan.json()["items"]] == [(new_id, "trash")]
    response = env.restore(row["id"])
    assert response.status_code == 200, response.text
    assert [i["id"] for i in env.bw.trashed] == [new_id]
    assert new_id not in env.revisions()


def archive(env: AppEnv, item_id: str, *, trash: bool = False) -> dict[str, Any]:
    """Archive an item in the fake bw serve, as another device would: only archived=true lists
    it then, with trash=true when it is in the trash too. Answers the archived item."""
    item = env.item(item_id)
    env.bw.items.remove(item)
    archived = {**item, "archivedDate": "2026-10-26T13:00:00.000Z"}
    if trash:
        archived["deletedDate"] = "2026-10-26T14:00:00.000Z"
    env.bw.archived.append(archived)
    env.reload()
    return archived


def test_restore_puts_an_archived_item_back_and_keeps_it_archived(renv: AppEnv):
    """bw serve lists an archived item in no listing but archived=true, and the restore does
    not take it for gone: it makes no second copy beside it."""
    env = renv
    assert env.post(env.set_leaf("login-1", "login.username", "changed")).status_code == 200
    [row] = env.change_log()
    archived = archive(env, "login-1")
    plan = env.plan(row["id"])
    assert plan.status_code == 200, plan.text
    assert plan.json()["operation"]["revisionDates"] == {"login-1": archived["revisionDate"]}
    assert [(i["action"], i["text"]) for i in plan.json()["items"]] == [
        ("put", "put back as before (it stays archived)")
    ]
    ## the login created the tool note
    made = len(env.bw.created)
    response = env.post(plan.json()["operation"])
    assert response.status_code == 200, response.text
    assert len(env.bw.created) == made
    assert env.bw.restored == []
    [after] = env.bw.archived
    assert after["login"]["username"] == LOGIN["login"]["username"]
    assert after["archivedDate"] == archived["archivedDate"]
    assert "login-1" not in {i["id"] for i in env.bw.items}


def test_restore_takes_an_archived_item_out_of_the_trash_and_keeps_it_archived(renv: AppEnv):
    env = renv
    assert env.post(env.set_leaf("login-1", "login.username", "changed")).status_code == 200
    [row] = env.change_log()
    archive(env, "login-1", trash=True)
    plan = env.plan(row["id"])
    assert plan.status_code == 200, plan.text
    assert plan.json()["operation"]["revisionDates"] == {}
    assert [(i["action"], i["text"]) for i in plan.json()["items"]] == [
        ("untrash", "take out of the trash and put back as before (it stays archived)")
    ]
    ## the login created the tool note
    made = len(env.bw.created)
    response = env.post(plan.json()["operation"])
    assert response.status_code == 200, response.text
    assert len(env.bw.created) == made
    assert env.bw.restored == ["login-1"]
    [after] = env.bw.archived
    assert after["login"]["username"] == LOGIN["login"]["username"]
    assert after["archivedDate"] and not after.get("deletedDate")


def test_an_archived_item_changed_after_the_plan_is_a_conflict(renv: AppEnv):
    env = renv
    assert env.post(env.set_leaf("login-1", "notes", "changed")).status_code == 200
    [row] = env.change_log()
    archived = archive(env, "login-1")
    plan = env.plan(row["id"])
    assert plan.status_code == 200, plan.text
    archived["revisionDate"] = "2026-10-26T17:00:00.000Z"
    response = env.post(plan.json()["operation"])
    assert response.status_code == 409, response.text
    assert [c["itemId"] for c in response.json()["conflicts"]] == ["login-1"]
    assert env.bw.put[1:] == []


def test_restore_of_a_create_item_trashes_the_new_item_when_it_is_archived(renv: AppEnv):
    env = renv
    response = env.post(create(type=1, name="Short lived", generatePassword=True))
    assert response.status_code == 200, response.text
    [row] = env.change_log()
    [new_id] = row["createdItemIds"]
    archive(env, new_id)
    plan = env.plan(row["id"])
    assert plan.status_code == 200, plan.text
    assert [(i["action"], i["text"]) for i in plan.json()["items"]] == [
        ("trash", "move to the trash")
    ]
    assert env.post(plan.json()["operation"]).status_code == 200
    [after] = env.bw.archived
    assert after["id"] == new_id
    assert after["archivedDate"] and after["deletedDate"]


def test_restore_refuses_an_archived_item_whose_organization_changed(renv: AppEnv):
    """A PUT cannot move it back, and the copy a restore would make is not archived."""
    env = renv
    assert env.post(env.set_leaf("login-1", "notes", "changed")).status_code == 200
    [row] = env.change_log()
    archived = archive(env, "login-1")
    archived["organizationId"] = "o1"
    env.reload()
    plan = env.plan(row["id"])
    assert plan.status_code == 422
    assert "login-1 is archived and its organization changed" in plan.json()["detail"]


def test_the_queue_shows_the_restore_of_an_archived_item(renv: AppEnv):
    env = renv
    assert env.post(env.set_leaf("login-1", "login.username", "changed")).status_code == 200
    [row] = env.change_log()
    archive(env, "login-1")
    response = enqueue_ui(env, env.plan(row["id"]).json()["operation"])
    assert response.status_code == 201, response.text
    [entry_id] = response.json()["ids"]
    assert [(r["itemId"], r["column"], r["after"]) for r in entry(env, entry_id)["diff"]] == [
        ("login-1", "restore", "put back as before (it stays archived)"),
        ("login-1", "login.username", LOGIN["login"]["username"]),
    ]
