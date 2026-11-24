"""The check of a batch (writes/check.py) through POST /api/operations: a vanished item, the
tool note guard, the shapes an operation is refused for, the values the grid showed (folder,
favorite, collections, URIs) against what the vault holds and the chain of own writes."""

from datetime import UTC, datetime
from typing import Any

import pytest

from bwtidy.protocol import backup_name
from tests.support.app_env import AppEnv
from tests.support.writes_helpers import move, moved_elsewhere, shown_conflict, uris_op


def test_a_vanished_item_is_a_conflict(env: AppEnv):
    op = env.op("trashItem", "card-1")
    env.bw.items.remove(env.item("card-1"))
    response = env.post(op)
    assert response.status_code == 409
    assert response.json()["conflicts"] == [
        {"itemId": "card-1", "expected": "2026-10-26T12:00:00.000Z", "actual": None}
    ]
    assert env.bw.trashed == []
    assert env.backups == [env.login_backup]


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ({"kind": "setLeaf", "column": "not.a.column", "value": "x"}, "Unknown coordinate"),
        (
            {"kind": "setLeaf", "column": "login.username", "value": "x", "_item": "note-1"},
            "no login",
        ),
        ({"kind": "setLeaf", "column": "type", "value": "note"}, "not a leaf"),
        ({"kind": "setLeaf", "column": "fields.X", "value": "x", "fieldType": 3}, "fieldType"),
        ({"kind": "removeLeaf", "column": "name"}, "required"),
        ({"kind": "removeLeaf", "column": "fields.Nope"}, "does not exist"),
        ({"kind": "setCollections", "collectionNames": ["Youth"]}, "only an organization item"),
        (
            {"kind": "setCollections", "collectionNames": ["Nope"], "_item": "note-1"},
            "no collection named",
        ),
        ({"kind": "setCollections", "collectionNames": [], "_item": "note-1"}, "at least one"),
        (
            {
                "kind": "moveToOrganization",
                "organizationName": "Family",
                "collectionNames": ["Youth"],
                "_item": "note-1",
            },
            "already in an organization",
        ),
        (
            {
                "kind": "moveToOrganization",
                "organizationName": "Nope",
                "collectionNames": ["Youth"],
            },
            "no organization named",
        ),
        ({"kind": "moveToPersonal"}, "already in the personal vault"),
        ({"kind": "convertItem", "newType": 1, "mapping": {}}, "already has that type"),
        ({"kind": "convertItem", "newType": 9, "mapping": {}}, "unknown item type"),
        ({"kind": "frobnicate"}, "kind"),
    ],
)
def test_invalid_operations_are_422_and_write_nothing(
    env: AppEnv, operation: dict[str, Any], expected: str
):
    item_id = operation.pop("_item", "login-1")
    response = env.post({**operation, "itemId": item_id, "revisionDate": env.revisions()[item_id]})
    assert response.status_code == 422, response.text
    assert expected in response.json()["detail"]
    assert env.bw.put == []
    assert env.bw.trashed == []
    assert env.backups == [env.login_backup]
    assert env.change_log() == []


def tool_note_operations(env: AppEnv, note_id: str) -> list[dict[str, Any]]:
    """One operation of every kind, each touching the tool note."""
    revisions = env.revisions()
    note, other = revisions[note_id], revisions["login-1"]
    return [
        env.set_leaf(note_id, "fields.extra", "x"),
        env.set_leaf(note_id, "name", "bwtidyK"),
        env.op("removeLeaf", note_id, column="notes"),
        {
            "kind": "moveToFolder",
            "itemIds": [note_id],
            "revisionDates": {note_id: note},
            "folderName": "Work",
        },
        env.op("setCollections", note_id, collectionNames=["Youth"]),
        env.op("moveToOrganization", note_id, organizationName="Family", collectionNames=["Youth"]),
        env.op("moveToPersonal", note_id),
        env.op("trashItem", note_id),
        env.op("convertItem", note_id, newType=1, mapping={}),
        env.op("applyTemplate", note_id, template="Computer", mapping={}),
        {
            "kind": "renameField",
            "from": "backup-password",
            "to": "pw",
            "items": [{"itemId": note_id, "revisionDate": note}],
        },
        {
            "kind": "mergeColumns",
            "sources": ["fields.backup-password"],
            "target": "fields.pw",
            "items": [{"itemId": note_id, "revisionDate": note}],
        },
        {
            "kind": "mergeItems",
            "fromItemId": note_id,
            "fromRevisionDate": note,
            "toItemId": "login-1",
            "toRevisionDate": other,
            "mapping": {},
        },
        {
            "kind": "mergeItems",
            "fromItemId": "login-1",
            "fromRevisionDate": other,
            "toItemId": note_id,
            "toRevisionDate": note,
            "mapping": {},
        },
    ]


def test_every_operation_on_the_tool_note_is_refused(env: AppEnv):
    note_id = next(i["id"] for i in env.bw.items if i.get("name") == "bwtidy")
    operations = tool_note_operations(env, note_id)
    kinds = {op["kind"] for op in operations}
    assert kinds == {
        "setLeaf",
        "removeLeaf",
        "moveToFolder",
        "setCollections",
        "moveToOrganization",
        "moveToPersonal",
        "trashItem",
        "convertItem",
        "applyTemplate",
        "renameField",
        "mergeColumns",
        "mergeItems",
    }
    for op in operations:
        response = env.post(op)
        assert response.status_code == 422, (op["kind"], response.text)
        detail = response.json()["detail"]
        assert "the bwtidy note is managed by bwtidy" in detail, op["kind"]
        assert "Types page" in detail
    ## one refused operation refuses the whole batch
    response = env.post(env.set_leaf("login-1", "login.username", "x"), operations[0])
    assert response.status_code == 422
    assert response.json()["detail"].startswith("Operation 2 (setLeaf)")
    assert env.bw.put == []
    assert env.bw.trashed == []
    ## only the login's backup
    assert env.backups == [backup_name(datetime(2026, 10, 26, 12, tzinfo=UTC))]
    assert env.change_log() == []


def test_a_renamed_tool_note_is_still_refused(env: AppEnv):
    note = next(i for i in env.bw.items if i.get("name") == "bwtidy")
    note["name"] = "bwtidyK"
    note["revisionDate"] = "2026-10-28T10:00:00.000Z"
    response = env.post(env.set_leaf(note["id"], "name", "something else"))
    assert response.status_code == 422
    assert "managed by bwtidy" in response.json()["detail"]
    assert env.bw.put == []


def test_a_move_after_another_devices_move_is_a_conflict(env: AppEnv):
    op = move(env, "login-1", "Archive", shownFolderIds={"login-1": "f1"})
    moved_elsewhere(env, "login-1", folderId=None)
    response = env.post(op)
    assert response.status_code == 409, response.text
    assert response.json()["conflicts"] == [shown_conflict(env, "login-1", "folder")]
    assert env.item("login-1")["folderId"] is None
    assert env.bw.put == []
    assert env.bw.calls("POST", "/object/folder") == 0
    assert env.change_log() == []


def test_a_favorite_after_another_devices_star_is_a_conflict(env: AppEnv):
    op = env.set_leaf("login-1", "favorite", False, shownFavorite=True)
    moved_elsewhere(env, "login-1", favorite=False, folderId=None)
    response = env.post(op)
    assert response.status_code == 409, response.text
    assert response.json()["conflicts"] == [shown_conflict(env, "login-1", "favorite")]
    assert env.bw.put == []


def test_collections_after_another_devices_change_are_a_conflict(env: AppEnv):
    op = env.op(
        "setCollections", "note-1", collectionNames=["shared"], shownCollectionIds=["c2", "c1"]
    )
    moved_elsewhere(env, "note-1", collectionIds=["c1"])
    response = env.post(op)
    assert response.status_code == 409, response.text
    assert response.json()["conflicts"] == [shown_conflict(env, "note-1", "collections")]
    assert env.item("note-1")["collectionIds"] == ["c1"]
    assert ("PUT", "/object/item-collections/note-1") not in env.bw.requests


def test_without_the_shown_values_the_change_elsewhere_is_overwritten_as_before(env: AppEnv):
    ops = [
        move(env, "login-1", "Work"),
        env.set_leaf("login-1", "favorite", True),
        env.op("setCollections", "note-1", collectionNames=["shared"]),
    ]
    moved_elsewhere(env, "login-1", folderId=None, favorite=False)
    moved_elsewhere(env, "note-1", collectionIds=["c1"])
    response = env.post(*ops)
    assert response.status_code == 200, response.text
    login = env.item("login-1")
    assert (login["folderId"], login["favorite"]) == ("f1", True)
    assert env.item("note-1")["collectionIds"] == ["c2"]


def test_shown_values_that_still_hold_pass(env: AppEnv):
    response = env.post(
        move(env, "login-1", "", shownFolderIds={"login-1": "f1"}),
        move(env, "card-1", "Work", shownFolderIds={"card-1": None}),
        env.set_leaf("card-1", "favorite", True, shownFavorite=False),
        env.op(
            "setCollections", "note-1", collectionNames=["Youth"], shownCollectionIds=["c1", "c2"]
        ),
    )
    assert response.status_code == 200, response.text
    assert env.item("login-1")["folderId"] is None
    card = env.item("card-1")
    assert (card["folderId"], card["favorite"]) == ("f1", True)
    assert env.item("note-1")["collectionIds"] == ["c1"]


def test_an_own_earlier_change_is_no_conflict(env: AppEnv):
    """Edits queued before a commit carry what the grid showed then, also after the commit."""
    stale_move = move(env, "login-1", "", shownFolderIds={"login-1": "f1"})
    stale_star = env.set_leaf("login-1", "favorite", True, shownFavorite=True)
    stale_collections = env.op(
        "setCollections", "note-1", collectionNames=["shared"], shownCollectionIds=["c1", "c2"]
    )
    response = env.post(
        move(env, "login-1", "Archive", shownFolderIds={"login-1": "f1"}),
        env.set_leaf("login-1", "favorite", False, shownFavorite=True),
        stale_star,
        env.op(
            "setCollections", "note-1", collectionNames=["Youth"], shownCollectionIds=["c1", "c2"]
        ),
    )
    assert response.status_code == 200, response.text
    response = env.post(stale_move, stale_collections)
    assert response.status_code == 200, response.text
    login = env.item("login-1")
    assert (login["folderId"], login["favorite"]) == (None, True)
    assert env.item("note-1")["collectionIds"] == ["c2"]


def test_a_change_elsewhere_after_an_own_one_is_a_conflict(env: AppEnv):
    stale = move(env, "login-1", "", shownFolderIds={"login-1": "f1"})
    first = move(env, "login-1", "Archive", shownFolderIds={"login-1": "f1"})
    assert env.post(first).status_code == 200
    env.bw.folders.append({"object": "folder", "id": "f9", "name": "Phone"})
    moved_elsewhere(env, "login-1", folderId="f9")
    response = env.post(stale)
    assert response.status_code == 409, response.text
    assert response.json()["conflicts"] == [
        {
            "itemId": "login-1",
            "expected": stale["revisionDates"]["login-1"],
            "actual": env.item("login-1")["revisionDate"],
            "column": "folder",
        }
    ]
    assert env.item("login-1")["folderId"] == "f9"


@pytest.mark.parametrize(
    ("uris", "item_id", "problem"),
    [
        ([("  ", None)], "login-1", "URI 1 is empty"),
        ([("ok", 4), ("^https://(a|b", 4)], "login-1", "URI 2 is not a valid regular expression"),
        ([("https://a.example.test", 0)], "card-1", "only a login has URIs"),
        ([("https://a.example.test", 6)], "login-1", "match"),
    ],
)
def test_set_uris_refuses_what_bitwarden_cannot_use(
    env: AppEnv, uris: list[tuple[str, int | None]], item_id: str, problem: str
):
    op = uris_op(env, *uris)
    if item_id != "login-1":
        op["items"][0].update(itemId=item_id, revisionDate=env.revisions()[item_id], shownUris=[])
    response = env.post(op)
    assert response.status_code == 422, response.text
    assert problem in response.text
    assert env.bw.put == []


def test_set_uris_over_uris_written_since_is_a_conflict(env: AppEnv):
    """The revisionDate follows bwtidy's own write, the URIs shown do not."""
    stale = uris_op(env, ("https://mail.example.test", 1))
    grid = env.set_leaf("login-1", "login.uris", ["https://grid.example.test"])
    assert env.post(grid).status_code == 200
    response = env.post(stale)
    assert response.status_code == 409, response.text
    [conflict] = response.json()["conflicts"]
    assert (conflict["itemId"], conflict["column"]) == ("login-1", "login.uris")
    assert env.item("login-1")["login"]["uris"] == [
        {"match": None, "uri": "https://grid.example.test"}
    ]
    ## an own write of another column leaves the URIs shown as they are
    fresh = uris_op(env, ("https://grid.example.test", 3))
    assert env.post(env.set_leaf("login-1", "name", "Mail")).status_code == 200
    assert env.post(fresh).status_code == 200
    assert env.item("login-1")["login"]["uris"] == [
        {"match": 3, "uri": "https://grid.example.test"}
    ]


def test_a_shown_value_for_another_column_or_item_is_refused(env: AppEnv):
    notes = env.set_leaf("login-1", "notes", "x", shownFavorite=True)
    assert env.post(notes).status_code == 422
    other = move(env, "login-1", "Work", shownFolderIds={"card-1": None})
    assert env.post(other).status_code == 422
    assert env.bw.put == []


@pytest.mark.parametrize(
    ("first", "item_id", "column", "plain"),
    [
        (
            {"kind": "generateLeaf", "column": "fields.Recovery"},
            "note-1",
            "fields.Recovery",
            ("notes", "Free text, not secret"),
        ),
        (
            {"kind": "renameField", "from": "PIN", "to": "Code"},
            "login-1",
            "fields.Code",
            ("login.username", "dev@mail.example.test"),
        ),
    ],
    ids=["generateLeaf", "renameField"],
)
def test_a_set_leaf_after_a_generation_or_rename_in_the_batch_sees_the_field_hidden(
    env: AppEnv, first: dict[str, Any], item_id: str, column: str, plain: tuple[str, str]
):
    plain_column, plain_value = plain
    revision = env.revisions()[item_id]
    if first["kind"] == "renameField":
        first = {**first, "items": [{"itemId": item_id, "revisionDate": revision}]}
    else:
        first = {**first, "itemId": item_id, "revisionDate": revision}
    response = env.post(first, env.set_leaf(item_id, column, plain_value))
    assert response.status_code == 422
    assert response.json()["detail"] == (
        f"Operation 2 (setLeaf): the non-secret column {plain_column} may not equal the secret "
        f"column {column} of the same item"
    )
    assert env.bw.put == []
