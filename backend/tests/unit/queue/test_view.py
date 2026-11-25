"""What the Queue page shows of an entry, against the fake bw serve: its summary and its masked
diff."""

import contextlib

from bwtidy.items.coordinates import MASKED
from bwtidy.operations import InvalidOperation, Operation, parse_operations
from bwtidy.queue.view import diff, summarize
from tests.support.app_env import AppEnv
from tests.support.normalize_data import ALL_SECRETS, merge, rename
from tests.support.queue_helpers import (
    MINIMAL_OPERATIONS,
    commit_now,
    enqueue_dict,
    enqueue_ui,
    entry,
    listing,
)
from tests.support.quiet import assert_absent
from tests.support.section_changes import add_template, changes
from tests.support.writes_helpers import raw_sqlite_text, store_templates


def test_diff_masks_secrets(env: AppEnv):
    op = merge(env, ["fields.Password"], "login.password", "login-2")
    entry_id = enqueue_dict(env, op, "Move the shop password into the login slot")
    rename_id = enqueue_dict(env, rename(env, "PIN", "pin", "login-1"), "lower case")
    assert entry(env, entry_id)["diff"] == [
        {
            "itemId": "login-2",
            "itemName": "Example Shop",
            "column": "login.password",
            "before": None,
            "after": MASKED,
        },
        {
            "itemId": "login-2",
            "itemName": "Example Shop",
            "column": "fields.Password",
            "before": MASKED,
            "after": None,
        },
    ]
    rows = entry(env, rename_id)["diff"]
    columns = [(row["column"], row["before"], row["after"]) for row in rows]
    assert columns == [("fields.PIN", MASKED, None), ("fields.pin", None, MASKED)]
    text = env.client.get("/api/queue").text
    ## indexes only, so a failure never prints a secret
    leaked = [i for i, secret in enumerate(ALL_SECRETS) if secret in text]
    assert leaked == []


def test_summaries_and_diffs_handle_every_kind_of_operation(env: AppEnv):
    kinds = {kind.__annotations__["kind"].__args__[0] for kind in Operation.__value__.__args__}
    assert set(MINIMAL_OPERATIONS) == kinds
    session = env.app.state.bw.session
    cache = session.cache
    for kind, fields in MINIMAL_OPERATIONS.items():
        [op] = parse_operations([{"kind": kind, **fields}])
        assert summarize(op, cache), kind
        ## an operation that does not fit the vault raises ValueError, never AssertionError
        with contextlib.suppress(ValueError, InvalidOperation):
            diff(op, cache, session.tool_note_id)


def test_diff_of_organizational_ops_and_trash(env: AppEnv):
    revisions = env.revisions()
    folder = enqueue_dict(
        env,
        {
            "kind": "moveToFolder",
            "itemIds": ["login-1", "card-1"],
            "revisionDates": {i: revisions[i] for i in ("login-1", "card-1")},
            "folderName": "Money",
        },
        "file them",
    )
    trash = enqueue_dict(env, env.op("trashItem", "ssh-1"), "old key")
    diffs = {
        e["id"]: [(r["itemId"], r["column"], r["before"], r["after"]) for r in e["diff"]]
        for e in listing(env)
    }
    assert diffs[folder] == [
        ("login-1", "folder", "Work", "Money"),
        ("card-1", "folder", None, "Money"),
    ]
    assert diffs[trash] == [("ssh-1", "trash", False, True)]


def test_a_ui_move_to_personal_is_queued_with_its_diff_and_committed(env: AppEnv):
    [entry_id] = enqueue_ui(env, env.op("moveToPersonal", "note-1")).json()["ids"]
    queued = entry(env, entry_id)
    assert queued["summary"] == 'Move "Backup disk" to the personal vault'
    assert [(r["itemId"], r["column"], r["before"], r["after"]) for r in queued["diff"]] == [
        ("note-1", "organization", "Family", None),
        ("note-1", "collections", ["shared", "Youth"], None),
    ]
    response = commit_now(env)
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == [entry_id]
    [new] = [i for i in env.bw.items if i.get("name") == "Backup disk"]
    assert new["id"] != "note-1"
    assert not new.get("organizationId")
    assert "note-1" in [i["id"] for i in env.bw.trashed]


def test_a_ui_create_item_is_queued_with_its_summary_and_diff_and_committed(env: AppEnv):
    revision = env.client.get("/api/templates").json()["revisionDate"]
    template = {
        "name": "Router",
        "type": 1,
        "coordinates": [
            {"kind": "path", "path": "login.username"},
            {"kind": "field", "name": "WPA key", "fieldType": 1},
        ],
    }
    body = {**changes(add_template(template)), "revisionDate": revision}
    assert env.client.put("/api/templates", json=body).status_code == 202
    assert commit_now(env).status_code == 200
    login = {
        "kind": "createItem",
        "type": 1,
        "name": "Forge",
        "folderName": "Servers",
        "values": {"login.username": "user"},
        "generatePassword": True,
    }
    router = {
        "kind": "createItem",
        "template": "Router",
        "name": "Attic router",
        "organizationName": "Family",
        "collectionNames": ["shared", "Youth"],
        "values": {"fields.SSID": "attic"},
    }
    response = enqueue_ui(env, login, router, {"kind": "createItem", "type": 2, "name": "Plain"})
    assert response.status_code == 201, response.text
    first, second, third = (entry(env, i) for i in response.json()["ids"])
    assert first["summary"] == 'Create login "Forge" with a generated password'
    assert second["summary"] == 'Create "Attic router" from template "Router"'
    assert third["summary"] == 'Create secure note "Plain"'
    rows = [
        (r["itemId"], r["itemName"], r["column"], r["before"], r["after"]) for r in first["diff"]
    ]
    assert rows == [
        (None, "Forge", "name", None, "Forge"),
        (None, "Forge", "type", None, "login"),
        (None, "Forge", "folder", None, "Servers"),
        (None, "Forge", "login.username", None, "user"),
        (None, "Forge", "login.password", None, "(generated)"),
    ]
    assert [(r["column"], r["after"]) for r in second["diff"]] == [
        ("name", "Attic router"),
        ("type", "login"),
        ("organization", "Family"),
        ("collections", ["shared", "Youth"]),
        ("fields.SSID", "attic"),
    ]
    assert first["diffError"] is None
    ## queueing writes nothing
    assert env.bw.generated == []
    created = len(env.bw.created)
    response = commit_now(env)
    assert response.status_code == 200, response.text
    assert sorted(response.json()["applied"]) == [first["id"], second["id"], third["id"]]
    forge, attic, plain = env.bw.created[created:]
    assert forge["login"]["password"] == env.bw.generated[0]
    assert [(f["name"], f["value"], f["type"]) for f in attic["fields"]] == [
        ("WPA key", None, 1),
        ("SSID", "attic", 0),
    ]
    assert plain["type"] == 2
    log = env.client.get("/api/change-log").json()
    assert [r["operationKind"] for r in log[:3]] == ["createItem"] * 3
    assert sorted(id for r in log[:3] for id in r["createdItemIds"]) == sorted(
        i["id"] for i in (forge, attic, plain)
    )
    assert_absent(env.bw.generated[0], env.client.get("/api/queue").text, "the queue response")


def test_a_create_item_with_generated_columns(env: AppEnv):
    template = {
        "name": "Two keys",
        "type": 2,
        "coordinates": [
            {"kind": "field", "name": "generated key", "fieldType": 1},
            {"kind": "field", "name": "blank key", "fieldType": 1},
        ],
    }
    store_templates(env, template)
    op = {
        "kind": "createItem",
        "template": "Two keys",
        "name": "Keys",
        "generateColumns": ["fields.generated key"],
    }
    response = enqueue_ui(env, op)
    assert response.status_code == 201, response.text
    [entry_id] = response.json()["ids"]
    queued = entry(env, entry_id)
    assert queued["summary"] == 'Create "Keys" from template "Two keys" with generated values'
    assert [(r["column"], r["before"], r["after"]) for r in queued["diff"]] == [
        ("name", None, "Keys"),
        ("type", None, "note"),
        ("fields.generated key", None, "(generated)"),
    ]
    assert queued["operation"]["generateColumns"] == ["fields.generated key"]
    assert queued["operation"]["values"] == {}
    assert env.bw.generated == []

    assert commit_now(env).json()["failed"] == []
    [value] = env.bw.generated
    [new] = [i for i in env.bw.items if i.get("name") == "Keys"]
    ## the list as the next load left it: the generated column went by itself, the blank one stays
    grid = env.client.get("/api/grid")
    assert grid.json()["toFill"] == {new["id"]: ["fields.blank key"]}
    shown = grid.text + env.client.get("/api/queue").text + raw_sqlite_text(env)
    leaked = value in shown
    assert not leaked


def test_a_password_asked_for_both_ways_has_one_row(env: AppEnv):
    op = {
        "kind": "createItem",
        "type": 1,
        "name": "Keys",
        "generatePassword": True,
        "generateColumns": ["login.password"],
    }
    response = enqueue_ui(env, op)
    assert response.status_code == 201, response.text
    queued = entry(env, response.json()["ids"][0])
    assert queued["summary"] == 'Create login "Keys" with generated values'
    assert [r["column"] for r in queued["diff"]] == ["name", "type", "login.password"]
