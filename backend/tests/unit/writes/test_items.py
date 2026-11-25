"""The writes of writes/items.py through POST /api/operations: a leaf, a move to a folder, the
collections and the organization, the trash, the URIs, and renameField, mergeColumns and
mergeItems on the vault with normalize_data's EXTRA items."""

import copy
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support.app_env import AppEnv, logged_in
from tests.support.normalize_data import (
    ALL_SECRETS,
    EXTRA,
    listed,
    merge,
    merge_items,
    rename,
    with_shop_uris,
)
from tests.support.sample_vault import LOGIN
from tests.support.writes_helpers import create, edit_at_sync, uris_op


@pytest.fixture
def env(tmp_path: Path) -> Iterator[AppEnv]:
    yield from logged_in(tmp_path, extra=EXTRA)


@pytest.fixture
def uris_env(tmp_path: Path) -> Iterator[AppEnv]:
    yield from with_shop_uris(tmp_path)


def test_set_leaf_puts_the_full_item_with_one_leaf_changed(env: AppEnv):
    response = env.post(env.set_leaf("login-1", "login.username", "new@example.test"))
    assert response.status_code == 200
    ## the backup made after the login holds the vault as it was, so the write reused it,
    ## and a backup of the vault after the write followed
    assert response.json() == {"applied": 1, "backup": env.login_backup}
    assert env.backup_files() == env.backups == [env.login_backup, env.backups[1]]
    [(item_id, body)] = env.bw.put
    assert item_id == "login-1"
    expected = copy.deepcopy(LOGIN)
    expected["login"]["username"] = "new@example.test"
    assert json.dumps(body, sort_keys=True) == json.dumps(expected, sort_keys=True)
    ## the cache was reloaded, the grid shows the change
    rows = env.client.get("/api/grid").json()["rows"]
    row = next(r for r in rows if r["id"] == "login-1")
    assert row["cells"]["login.username"] == "new@example.test"
    [entry] = env.change_log()
    assert entry["source"] == "ui"
    assert entry["operationKind"] == "setLeaf"
    assert entry["itemIds"] == ["login-1"]
    assert entry["result"] == "ok"
    assert entry["backup"] == env.backups[0]
    assert entry["id"] == 1
    assert entry["at"].startswith("2")


def test_move_to_an_existing_folder_and_to_no_folder(env: AppEnv):
    revisions = env.revisions()
    response = env.post(
        {
            "kind": "moveToFolder",
            "itemIds": ["note-1"],
            "revisionDates": {"note-1": revisions["note-1"]},
            "folderName": "Work",
        },
        {
            "kind": "moveToFolder",
            "itemIds": ["login-1"],
            "revisionDates": {"login-1": revisions["login-1"]},
            "folderName": "",
        },
    )
    assert response.status_code == 200
    assert env.bw.calls("POST", "/object/folder") == 0
    assert env.item("note-1")["folderId"] == "f1"
    assert env.item("login-1")["folderId"] is None
    bad = env.post(
        {"kind": "moveToFolder", "itemIds": ["note-1"], "revisionDates": {}, "folderName": "Work"}
    )
    assert bad.status_code == 409


def test_collections_and_organization_moves(env: AppEnv):
    response = env.post(
        env.op("setCollections", "note-1", collectionNames=["Youth"]),
        env.op(
            "moveToOrganization", "login-1", organizationName="Family", collectionNames=["shared"]
        ),
    )
    assert response.status_code == 200
    assert ("PUT", "/object/item-collections/note-1") in env.bw.requests
    assert ("POST", "/move/login-1/o1") in env.bw.requests
    assert env.item("note-1")["collectionIds"] == ["c1"]
    assert env.item("login-1")["organizationId"] == "o1"
    assert env.item("login-1")["collectionIds"] == ["c2"]
    assert env.bw.put == []


def test_a_trash_checks_the_item_right_before_it(env: AppEnv):
    op = env.op("trashItem", "login-1")
    edit_at_sync(env, 3, "login-1")
    response = env.post(create(type=1, name="Brand new"), op)
    assert response.status_code == 500, response.text
    assert response.json()["conflicts"][0]["itemId"] == "login-1"
    assert env.bw.trashed == []


def test_set_uris_writes_the_exact_list_with_its_matches(env: AppEnv):
    before = copy.deepcopy(env.item("login-1"))
    op = uris_op(env, ("https://b.example.test", 5), ("mail.example.test", 1), ("x", None))
    response = env.post(op)
    assert response.status_code == 200, response.text
    after = env.item("login-1")
    assert after["login"]["uris"] == [
        {"match": 5, "uri": "https://b.example.test"},
        {"match": 1, "uri": "mail.example.test"},
        {"match": None, "uri": "x"},
    ]
    for key in ("username", "password", "totp", "fido2Credentials"):
        assert after["login"][key] == before["login"][key], key
    assert after["fields"] == before["fields"]
    [row] = env.change_log()
    assert (row["operationKind"], row["columns"], row["itemIds"]) == (
        "setUris",
        ["login.uris"],
        ["login-1"],
    )


def fields(env: AppEnv, item_id: str) -> list[tuple[str, int]]:
    return [(f["name"], f["type"]) for f in env.item(item_id).get("fields") or []]


def value(env: AppEnv, item_id: str, name: str) -> str:
    [found] = [f["value"] for f in env.item(item_id)["fields"] if f["name"] == name]
    return found


def no_secret_in(text: str) -> bool:
    return not any(secret in text for secret in ALL_SECRETS)


def nothing_written(env: AppEnv) -> bool:
    return env.bw.put == [] and env.bw.trashed == [] and env.backups == [env.login_backup]


def test_rename_a_field_across_items(env: AppEnv):
    response = env.post(rename(env, "Password", "password", "login-2"))
    assert response.status_code == 200, response.text
    assert response.json() == {"applied": 1, "backup": env.backups[0]}
    assert fields(env, "login-2") == [("user", 0), ("password", 1), ("Shoe size", 0)]
    assert value(env, "login-2", "password") == "shop-secret-31"

    response = env.post(rename(env, "User", "user", "login-1", "note-3"))
    assert response.status_code == 409
    response = env.post(rename(env, "User", "username", "login-1", "note-3"))
    assert response.status_code == 200, response.text
    ## the login's backup and one after each write
    assert len(env.backups) == 3
    assert fields(env, "login-1") == [("username", 0), ("PIN", 1), ("Linked", 3)]
    assert fields(env, "note-3") == [("username", 0), ("user", 0)]
    ## everything else of the item came along untouched
    after = copy.deepcopy(env.item("login-1"))
    after["fields"][0]["name"] = "User"
    del after["revisionDate"]
    assert after == {k: v for k, v in LOGIN.items() if k != "revisionDate"}
    [entry, _] = env.change_log()
    assert entry["operationKind"] == "renameField"
    assert entry["itemIds"] == ["login-1", "note-3"]
    rows = {r["id"]: r for r in env.client.get("/api/grid").json()["rows"]}
    assert rows["note-3"]["cells"]["fields.username"] == "carol"


def test_rename_onto_an_existing_name_is_a_merge_conflict(env: AppEnv):
    response = env.post(rename(env, "Password", "password", "login-2", "note-4"))
    assert response.status_code == 409
    body = response.json()
    assert body["mergeConflicts"] == [
        {"itemId": "note-4", "columns": ["fields.Password", "fields.password"]}
    ]
    assert "choose which to keep" in body["detail"]
    assert "conflicts" not in body
    assert no_secret_in(response.text)
    assert nothing_written(env)
    assert env.change_log() == []


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ({"from": "Password", "to": ""}, "empty"),
        ({"from": "Password", "to": "Password"}, "is the old one"),
        ({"from": "Nope", "to": "x"}, "no field named 'Nope'"),
        ({"from": "Password", "to": "pw", "_twice": "yes"}, "item twice"),
        ({"from": "Password", "to": "pw", "_none": "yes"}, "items is empty"),
    ],
)
def test_invalid_renames_are_422(env: AppEnv, operation: dict[str, str], expected: str):
    items = listed(env, "login-2")
    if operation.pop("_twice", None):
        items = items * 2
    if operation.pop("_none", None):
        items = []
    response = env.post({"kind": "renameField", **operation, "items": items})
    assert response.status_code == 422, response.text
    assert expected in response.json()["detail"]
    assert nothing_written(env)


def test_merge_columns_needs_a_resolution_where_both_hold_values(env: AppEnv):
    op = merge(env, ["fields.User", "fields.user"], "fields.user", "login-1", "login-2", "note-3")
    response = env.post(op)
    assert response.status_code == 409
    assert response.json()["mergeConflicts"] == [
        {"itemId": "note-3", "columns": ["fields.user", "fields.User"]}
    ]
    assert nothing_written(env)

    response = env.post({**op, "resolutions": {"note-3": "fields.User"}})
    assert response.status_code == 200, response.text
    assert len(env.backups) == 2
    ## renamed in place: the field keeps its type and its position
    assert fields(env, "login-1") == [("user", 0), ("PIN", 1), ("Linked", 3)]
    assert value(env, "login-1", "user") == "dev"
    assert fields(env, "note-3") == [("user", 0)]
    assert value(env, "note-3", "user") == "carol"
    ## login-2 already had its value in the target, nothing to write
    assert [item_id for item_id, _ in env.bw.put] == ["login-1", "note-3"]
    [entry] = env.change_log()
    assert entry["operationKind"] == "mergeColumns"
    assert entry["itemIds"] == ["login-1", "login-2", "note-3"]


def test_merge_columns_keeping_the_target_removes_the_sources(env: AppEnv):
    op = merge(env, ["fields.User"], "fields.user", "note-3", resolutions={"note-3": "fields.user"})
    response = env.post(op)
    assert response.status_code == 200, response.text
    assert fields(env, "note-3") == [("user", 0)]
    assert value(env, "note-3", "user") == "carol2"


def test_a_resolution_where_there_is_no_choice_is_ignored(env: AppEnv):
    resolutions = {"login-1": "fields.user"}
    op = merge(env, ["fields.User"], "fields.user", "login-1", resolutions=resolutions)
    response = env.post(op)
    assert response.status_code == 200, response.text
    assert value(env, "login-1", "user") == "dev"


def test_a_resolution_must_name_a_merged_column(env: AppEnv):
    for resolutions, expected in [
        ({"note-3": "fields.Nope"}, "not merged"),
        ({"login-1": "fields.User"}, "not in items"),
    ]:
        op = merge(env, ["fields.User"], "fields.user", "note-3", resolutions=resolutions)
        response = env.post(op)
        assert response.status_code == 422, response.text
        assert expected in response.json()["detail"]
    assert nothing_written(env)


def test_merge_into_a_built_in_slot_of_a_login(env: AppEnv):
    response = env.post(merge(env, ["fields.user"], "login.username", "login-2"))
    assert response.status_code == 200, response.text
    item = env.item("login-2")
    assert item["login"]["username"] == "bob"
    assert fields(env, "login-2") == [("Password", 1), ("Shoe size", 0)]
    response = env.post(merge(env, ["fields.Password"], "login.password", "login-2"))
    assert response.status_code == 200, response.text
    assert env.item("login-2")["login"]["password"] == "shop-secret-31"
    assert fields(env, "login-2") == [("Shoe size", 0)]


def test_merge_into_a_built_in_slot_of_a_note_needs_template_conversion(env: AppEnv):
    response = env.post(merge(env, ["fields.User"], "login.username", "note-3"))
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "a note has no login.username" in detail
    assert "template conversion" in detail
    assert nothing_written(env)


def test_a_hidden_field_into_a_text_field_is_refused(env: AppEnv):
    ## note-1 has an (empty) text field Empty, so the hidden value would land in plain text
    response = env.post(merge(env, ["fields.WPA key#2"], "fields.Empty", "note-1"))
    assert response.status_code == 422, response.text
    assert "secret" in response.json()["detail"]
    ## a hidden value in a text target, kept by a resolution
    op = merge(env, ["fields.PIN"], "fields.User", "login-1", resolutions={"login-1": "fields.PIN"})
    response = env.post(op)
    assert response.status_code == 422, response.text
    assert "secret" in response.json()["detail"]
    ## a secret built-in leaf into a non-secret one: both hold values, so it needs a choice
    response = env.post(merge(env, ["login.password"], "login.username", "login-1"))
    assert response.status_code == 409
    response = env.post(
        merge(
            env,
            ["login.password"],
            "login.username",
            "login-1",
            resolutions={"login-1": "login.password"},
        )
    )
    assert response.status_code == 422, response.text
    assert no_secret_in(response.text)
    assert nothing_written(env)


def test_a_hidden_field_into_a_new_field_stays_hidden(env: AppEnv):
    response = env.post(merge(env, ["fields.PIN"], "fields.Pin code", "login-1"))
    assert response.status_code == 200, response.text
    assert fields(env, "login-1") == [("User", 0), ("Pin code", 1), ("Linked", 3)]


def test_merge_items_moves_leaves_and_trashes_the_source(env: AppEnv):
    op = merge_items(
        env,
        "note-5",
        "login-1",
        {"fields.Recovery code": "fields.Recovery code", "fields.Where": "notes"},
    )
    response = env.post(op)
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 1
    assert len(env.backups) == 2
    assert fields(env, "login-1")[-2:] == [("Recovery code", 1), ("Where", 0)]
    assert value(env, "login-1", "Recovery code") == "recovery-secret-6"
    assert value(env, "login-1", "Where") == "Printed in the drawer"
    assert env.item("login-1")["login"] == LOGIN["login"]
    assert [item_id for item_id, _ in env.bw.put] == ["login-1"]
    assert [i["id"] for i in env.bw.trashed] == ["note-5"]
    [entry] = env.change_log()
    assert entry["operationKind"] == "mergeItems"
    assert entry["itemIds"] == ["note-5", "login-1"]
    assert "note-5" not in env.revisions()


def test_merge_items_with_append_unions_the_uris(uris_env: AppEnv):
    op = merge_items(
        uris_env, "login-2", "login-1", {"login.uris": "login.uris"}, append=["login.uris"]
    )
    response = uris_env.post(op)
    assert response.status_code == 200, response.text
    assert uris_env.item("login-1")["login"]["uris"] == [
        *LOGIN["login"]["uris"],
        {"match": 4, "uri": "https://shop.example.test"},
    ]
    assert [i["id"] for i in uris_env.bw.trashed] == ["login-2"]


def test_merge_items_without_append_replaces_the_uris(uris_env: AppEnv):
    response = uris_env.post(
        merge_items(uris_env, "login-2", "login-1", {"login.uris": "login.uris"})
    )
    assert response.status_code == 200, response.text
    uris = [u["uri"] for u in uris_env.item("login-1")["login"]["uris"]]
    assert uris == ["https://mail.example.test", "https://shop.example.test"]


@pytest.mark.parametrize(
    ("mapping", "append", "expected"),
    [
        ({"notes": "notes"}, ["notes"], "not a list column"),
        ({"fields.Shoe size": "fields.Shoe size"}, ["fields.Shoe size"], "not a list column"),
        ({"collections": "collections"}, ["collections"], "not a list column"),
        ({"fields.Shoe size": "fields.Shoe size"}, ["login.uris"], "not in the mapping"),
    ],
)
def test_append_needs_a_mapped_list_column(
    uris_env: AppEnv, mapping: dict[str, str], append: list[str], expected: str
):
    response = uris_env.post(merge_items(uris_env, "login-2", "login-1", mapping, append=append))
    assert response.status_code == 422, response.text
    assert expected in response.json()["detail"]
    assert nothing_written(uris_env)


@pytest.mark.parametrize(
    ("source", "target", "mapping", "expected"),
    [
        ("note-5", "login-1", {"notes": "fields.Recovery code"}, "secret"),
        ("note-5", "login-1", {"fields.User": "fields.Recovery code"}, "secret"),
        ("login-1", "note-3", {"login.password": "login.password"}, "a note has no"),
        ("note-5", "login-1", {"fields.x": "folder"}, "not a leaf"),
        ("note-5", "note-5", {}, "into itself"),
    ],
)
def test_invalid_item_merges_are_422(
    env: AppEnv, source: str, target: str, mapping: dict[str, str], expected: str
):
    response = env.post(merge_items(env, source, target, mapping))
    assert response.status_code == 422, response.text
    assert expected in response.json()["detail"]
    assert no_secret_in(response.text)
    assert nothing_written(env)


def test_the_tool_note_is_never_the_source_of_an_item_merge(env: AppEnv):
    note_id = next(i["id"] for i in env.bw.items if i.get("name") == "bwtidy")
    response = env.post(merge_items(env, note_id, "login-1", {}))
    assert response.status_code == 422
    assert "bwtidy note" in response.json()["detail"]
    assert nothing_written(env)


def test_a_stale_item_in_a_merge_is_a_conflict(env: AppEnv):
    op = merge(env, ["fields.User"], "fields.user", "login-1", "note-3")
    env.item("note-3")["revisionDate"] = "2026-10-26T16:00:00.000Z"
    response = env.post(op)
    assert response.status_code == 409
    assert [c["itemId"] for c in response.json()["conflicts"]] == ["note-3"]
    assert nothing_written(env)
