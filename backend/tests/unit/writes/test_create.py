"""The writes of writes/create.py through POST /api/operations: createItem of each type and from a
template, the generated values, the secret values and the refusals, a new SSH key's derived
columns, moveToPersonal, and convertItem against a WritePath built by hand (the plan of a
conversion, the new item with the mapped values and the moved attachments, the old one
trashed last)."""

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from bwtidy.items.coordinates import IDENTITY_KEYS, Lookups
from bwtidy.items.ssh_keys import derive
from bwtidy.operations import ConvertItem, InvalidOperation, parse_operations
from bwtidy.queue.view import diff
from bwtidy.writes.path import WriteFailed
from bwtidy.writes.planners import GENERATE_COLUMNS, changed_columns, plan_conversion
from tests.support.app_env import AppEnv, logged_in, sample_bw
from tests.support.fake_bw import FakeBwServe
from tests.support.quiet import assert_absent
from tests.support.sample_vault import CARD, COLLECTIONS, FOLDERS, LOGIN, NOTE, ORGANIZATIONS
from tests.support.ssh_keys import ed25519_private_key, passphrase_private_key
from tests.support.writes_helpers import (
    create,
    give_note_attachments,
    raw_sqlite_text,
    store_templates,
)


def test_move_to_personal_creates_a_personal_copy_and_trashes_the_old(env: AppEnv):
    give_note_attachments(env)
    env.item("note-1")["folderId"] = "f1"
    env.item("note-1")["passwordHistory"] = [{"lastUsedDate": "2025-11-02", "password": "old"}]
    old = copy.deepcopy(env.item("note-1"))
    ## the login created the tool note, so the move's item is the next one
    created = len(env.bw.created)
    response = env.post(env.op("moveToPersonal", "note-1"))
    assert response.status_code == 200, response.text
    [posted] = env.bw.posted[created:]
    for key in ("id", "revisionDate", "attachments", "passwordHistory"):
        assert key not in posted, key
    assert posted["organizationId"] is None
    assert posted["collectionIds"] == []
    assert posted["folderId"] == "f1"
    assert posted["fields"] == old["fields"]
    assert (posted["name"], posted["notes"], posted["type"]) == (
        old["name"],
        old["notes"],
        old["type"],
    )
    [new] = env.bw.created[created:]
    new_id = new["id"]
    new = env.item(new_id)
    assert not new.get("organizationId")
    assert not new.get("collectionIds")
    assert env.bw.uploads == [(new_id, "config.bin"), (new_id, "notes.txt")]
    assert env.bw.attachments[(new_id, "up-1")] == b"binary config"
    assert env.bw.attachments[(new_id, "up-2")] == b"plain notes"
    assert [i["id"] for i in env.bw.trashed] == ["note-1"]
    assert "note-1" not in env.revisions()
    assert new_id in env.revisions()
    [entry] = env.change_log()
    assert (entry["operationKind"], entry["itemIds"], entry["result"]) == (
        "moveToPersonal",
        ["note-1"],
        "ok",
    )


def test_a_failed_upload_of_a_move_trashes_the_new_item_and_keeps_the_old(env: AppEnv):
    give_note_attachments(env)
    env.bw.fail_at.add(("POST", "/attachment"))
    created = len(env.bw.created)
    response = env.post(env.op("moveToPersonal", "note-1"))
    assert response.status_code == 500, response.text
    [new] = env.bw.created[created:]
    new_id = new["id"]
    assert [i["id"] for i in env.bw.trashed] == [new_id]
    assert env.item("note-1")["organizationId"] == "o1"
    assert new_id not in env.revisions()
    [entry] = env.change_log()
    assert entry["result"] == "failed"


SERVER_TEMPLATE = {
    "name": "Server",
    "type": 2,
    "coordinates": [
        {"kind": "path", "path": "notes"},
        {"kind": "field", "name": "host", "fieldType": 0},
        {"kind": "field", "name": "root password", "fieldType": 1},
        {"kind": "field", "name": "monitored", "fieldType": 2},
    ],
}


@pytest.mark.parametrize(
    ("item_type", "sub_object"),
    [
        (1, {"login": {"username": None, "password": None, "totp": None, "uris": []}}),
        (2, {"secureNote": {"type": 0}}),
        (3, {"card": dict.fromkeys(CARD["card"])}),
        (4, {"identity": dict.fromkeys(IDENTITY_KEYS)}),
        (5, {"sshKey": {"privateKey": None, "publicKey": None, "keyFingerprint": None}}),
    ],
)
def test_create_item_of_each_type(env: AppEnv, item_type: int, sub_object: dict[str, Any]):
    response = env.post(create(type=item_type, name="Brand new"))
    assert response.status_code == 200, response.text
    assert response.json() == {"applied": 1, "backup": env.login_backup}
    body = env.bw.posted[-1]
    assert body == {
        "type": item_type,
        "name": "Brand new",
        "notes": None,
        "favorite": False,
        "reprompt": 0,
        "folderId": None,
        "organizationId": None,
        "collectionIds": None,
        "fields": [],
        **sub_object,
    }
    new = env.bw.created[-1]
    [row] = env.change_log()
    assert (row["operationKind"], row["itemIds"], row["createdItemIds"], row["result"]) == (
        "createItem",
        [],
        [new["id"]],
        "ok",
    )
    ## the grid shows the new item after the reload
    assert new["id"] in env.revisions()


def test_a_template_brings_its_type_and_fields_and_values_go_by_column(env: AppEnv):
    store_templates(env, SERVER_TEMPLATE)
    response = env.post(
        create(
            template="Server",
            name="db1",
            folderName="Servers",
            values={
                "notes": "the database",
                "fields.host": "db1.example.test",
                "fields.monitored": True,
                "fields.rack": "B2",
                "favorite": True,
            },
        )
    )
    assert response.status_code == 200, response.text
    body = env.bw.posted[-1]
    assert body["type"] == 2
    assert body["secureNote"] == {"type": 0}
    assert body["notes"] == "the database"
    assert body["favorite"] is True
    assert [(f["name"], f["value"], f["type"]) for f in body["fields"]] == [
        ("host", "db1.example.test", 0),
        ("root password", None, 1),
        ("monitored", "true", 2),
        ("rack", "B2", 0),
    ]
    ## the missing folder was created and the item is in it
    [folder] = [f for f in env.bw.folders if f["name"] == "Servers"]
    assert body["folderId"] == folder["id"]
    [row] = env.change_log()
    assert row["createdItemIds"] == [env.bw.created[-1]["id"]]


def test_a_login_with_values_in_an_existing_folder(env: AppEnv):
    response = env.post(
        create(
            type=1,
            name="Forge",
            folderName="Work",
            values={"login.username": "user", "login.uris": ["https://forge.example.test"]},
        )
    )
    assert response.status_code == 200, response.text
    body = env.bw.posted[-1]
    assert body["folderId"] == "f1"
    assert body["login"] == {
        "username": "user",
        "password": None,
        "totp": None,
        "uris": [{"match": None, "uri": "https://forge.example.test"}],
    }
    assert len(env.bw.folders) == len(FOLDERS)


def test_an_organization_item_goes_into_its_collections(env: AppEnv):
    response = env.post(
        create(type=3, name="Shared card", organizationName="Family", collectionNames=["shared"])
    )
    assert response.status_code == 200, response.text
    body = env.bw.posted[-1]
    assert (body["organizationId"], body["collectionIds"]) == ("o1", ["c2"])


def test_a_generated_password_is_set_only_at_write_time(env: AppEnv):
    response = env.post(create(type=1, name="Generated", generatePassword=True))
    assert response.status_code == 200, response.text
    [password] = env.bw.generated
    assert env.bw.generate_queries == [
        {
            "length": "24",
            "uppercase": "true",
            "lowercase": "true",
            "number": "true",
            "special": "true",
            "minNumber": "1",
            "minSpecial": "1",
            "ambiguous": "false",
        }
    ]
    stored = env.bw.posted[-1]["login"]["password"] == password
    assert stored
    assert len(password) == 24
    ## the password is in the vault only: not in the answer, the change log or the grid
    assert_absent(password, response.text, "the response")
    assert_absent(password, env.client.get("/api/change-log").text, "the change log response")
    assert_absent(password, env.client.get("/api/grid").text, "the grid response")
    assert_absent(password, raw_sqlite_text(env), "the database file")


TWO_KEYS_TEMPLATE = {
    "name": "Two keys",
    "type": 2,
    "coordinates": [
        {"kind": "field", "name": "generated key", "fieldType": 1},
        {"kind": "field", "name": "blank key", "fieldType": 1},
    ],
}


def test_a_generated_hidden_field_is_set_only_at_write_time(env: AppEnv):
    store_templates(env, TWO_KEYS_TEMPLATE)
    response = env.post(
        create(template="Two keys", name="Keys", generateColumns=["fields.generated key"])
    )
    assert response.status_code == 200, response.text
    [value] = env.bw.generated
    body = env.bw.posted[-1]
    fields = {f["name"]: f for f in body["fields"]}
    holds_it = fields["generated key"]["value"] == value
    assert holds_it
    assert fields["generated key"]["type"] == 1
    assert (fields["blank key"]["value"], fields["blank key"]["type"]) == (None, 1)
    assert_absent(value, response.text, "the response")
    assert_absent(value, env.client.get("/api/change-log").text, "the change log response")
    assert_absent(value, env.client.get("/api/grid").text, "the grid response")
    assert_absent(value, raw_sqlite_text(env), "the database file")


def test_each_generated_column_gets_its_own_value_with_the_same_settings(env: AppEnv):
    response = env.post(
        create(
            type=1,
            name="Keys",
            generateColumns=["login.password", "fields.new", "fields.new#2", "fields.new"],
        )
    )
    assert response.status_code == 200, response.text
    first, second, third = env.bw.generated
    query, *others = env.bw.generate_queries
    assert others == [query, query]
    body = env.bw.posted[-1]
    fields = [(f["name"], f["value"], f["type"]) for f in body["fields"]]
    placed = body["login"]["password"] == first and fields == [
        ("new", second, 1),
        ("new", third, 1),
    ]
    assert placed


def test_a_password_asked_for_both_ways_is_generated_once(env: AppEnv):
    response = env.post(
        create(type=1, name="Keys", generatePassword=True, generateColumns=["login.password"])
    )
    assert response.status_code == 200, response.text
    [password] = env.bw.generated
    stored = env.bw.posted[-1]["login"]["password"] == password
    assert stored


def test_a_create_that_fails_after_generating_leaves_no_value(
    env: AppEnv, caplog: pytest.LogCaptureFixture
):
    caplog.set_level("DEBUG")
    created = len(env.bw.created)
    env.bw.fail_at.add(("POST", "/object/item"))
    response = env.post(create(type=1, name="Keys", generateColumns=["login.password"]))
    [value] = env.bw.generated
    assert len(env.bw.created) == created
    shown = response.text + caplog.text + raw_sqlite_text(env)
    shown += env.client.get("/api/change-log").text + env.client.get("/api/grid").text
    leaked = value in shown
    assert not leaked


## the notes of a tool note that kept the name Pass
KEPT_NAME_NOTES = '{"version": 1, "note": {"name": "Pass"}}'


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (
            create(type=1, name="x", values={"login.password": "hunter2"}),
            "login.password is secret: a new item never takes a secret value, fill it in the "
            "grid after it is created",
        ),
        (create(type=1, name="x", values={"login.totp": "otp"}), "login.totp is secret"),
        (create(type=3, name="x", values={"card.number": "4111"}), "card.number is secret"),
        (create(type=3, name="x", values={"card.code": "123"}), "card.code is secret"),
        (
            create(type=5, name="x", values={"sshKey.privateKey": "k"}),
            "sshKey.privateKey is secret",
        ),
        (
            create(template="Server", name="x", values={"fields.root password": "pw"}),
            "fields.root password is secret",
        ),
        (create(type=2, name="x", generatePassword=True), "only a login can get a generated"),
        (
            create(template="Server", name="x", generatePassword=True),
            "only a login can get a generated",
        ),
        (
            create(template="Server", name="x", generateColumns=["fields.host"]),
            GENERATE_COLUMNS,
        ),
        (
            create(template="Server", name="x", generateColumns=["fields.monitored"]),
            GENERATE_COLUMNS,
        ),
        (create(type=3, name="x", generateColumns=["card.number"]), GENERATE_COLUMNS),
        (create(type=1, name="x", generateColumns=["login.totp"]), GENERATE_COLUMNS),
        (create(type=2, name="x", generateColumns=["login.password"]), GENERATE_COLUMNS),
        (create(type=1, name="x", generateColumns=["notes"]), GENERATE_COLUMNS),
        (create(type=1, name="x", generateColumns=["nope"]), GENERATE_COLUMNS),
        (create(type=1, name="x", generateColumns=["fields.key#2"]), GENERATE_COLUMNS),
        (
            create(
                template="Server",
                name="x",
                values={"fields.root password": "hunter2"},
                generateColumns=["fields.root password"],
            ),
            GENERATE_COLUMNS,
        ),
        (
            create(
                type=1,
                name="x",
                values={"fields.key": "plain"},
                generateColumns=["fields.key"],
            ),
            GENERATE_COLUMNS,
        ),
        (create(type=9, name="x"), "unknown item type 9"),
        (create(template="Nope", name="x"), "no template named 'Nope'"),
        (create(type=1, template="Server", name="x"), "exactly one of type and template"),
        (create(name="x"), "exactly one of type and template"),
        (create(type=1, name=""), "a new item needs a name"),
        (create(type=1, name="  "), "a new item needs a name"),
        (create(type=1, name="x", organizationName="Family"), "needs at least one collection"),
        (
            create(type=1, name="x", organizationName="Nope", collectionNames=["shared"]),
            "no organization named 'Nope'",
        ),
        (
            create(type=1, name="x", organizationName="Family", collectionNames=["Nope"]),
            "no collection named 'Nope'",
        ),
        (create(type=1, name="x", collectionNames=["shared"]), "collections need an organization"),
        (create(type=1, name="x", values={"card.brand": "Visa"}), "a login has no card.brand"),
        (create(type=1, name="x", values={"login.nope": "v"}), "Unknown coordinate path"),
        (create(type=1, name="x", values={"folder": "Work"}), "folder is not a leaf"),
        (create(type=1, name="x", values={"name": "y"}), "given as name"),
        (create(type=2, name="bwtidy"), "would be taken for bwtidy's own note"),
        (
            create(type=2, name="Pass", values={"notes": KEPT_NAME_NOTES}),
            "whose notes give its own name as the name of bwtidy's note",
        ),
    ],
)
def test_create_item_refusals_write_nothing(env: AppEnv, operation: dict[str, Any], expected: str):
    store_templates(env, SERVER_TEMPLATE)
    posted = len(env.bw.posted)
    response = env.post(operation)
    assert response.status_code == 422, response.text
    assert expected in response.json()["detail"]
    assert_absent("hunter2", response.text, "the response")
    assert len(env.bw.posted) == posted
    assert env.bw.generated == []
    assert env.backups == [env.login_backup]
    assert env.change_log() == []
    ## the queue refuses it the same way
    queued = env.client.post("/api/queue", json={"operations": [operation]})
    assert queued.status_code == 422
    assert expected in queued.json()["detail"]


def test_a_bwtidy_note_in_an_organization_or_a_login_named_so_is_fine(env: AppEnv):
    response = env.post(
        create(type=2, name="bwtidy", organizationName="Family", collectionNames=["shared"]),
        create(type=1, name="bwtidy"),
    )
    assert response.status_code == 200, response.text
    assert [row["createdItemIds"] != [] for row in env.change_log()] == [True, True]


TYPED_SECRET = "typed-new-item-secret-6620"


ONE_PASSWORD = {"login.password": TYPED_SECRET}


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (create(type=1, name="x", secretValues={"notes": TYPED_SECRET}), "notes is not secret"),
        (
            create(type=1, name="x", secretValues={"login.username": TYPED_SECRET}),
            "login.username is not secret",
        ),
        (
            create(template="Server", name="x", secretValues={"fields.host": TYPED_SECRET}),
            "fields.host is not secret",
        ),
        (
            create(template="Server", name="x", secretValues={"fields.monitored": TYPED_SECRET}),
            "fields.monitored takes true or false",
        ),
        (
            create(type=1, name="x", secretValues={"login.password": ""}),
            "the secret value of login.password is empty",
        ),
        (
            create(type=1, name="x", secretValues=ONE_PASSWORD, generatePassword=True),
            "login.password is generated",
        ),
        (
            create(type=1, name="x", secretValues=ONE_PASSWORD, generateColumns=["login.password"]),
            "login.password is generated",
        ),
        (
            create(
                template="Server",
                name="x",
                secretValues={"fields.root password": TYPED_SECRET},
                generateColumns=["fields.root password"],
            ),
            "fields.root password is generated",
        ),
        (create(type=1, name="x", secretValues={"name": TYPED_SECRET}), "given as name"),
        (create(type=1, name="x", secretValues={"card.code": TYPED_SECRET}), "no card.code"),
        (create(type=1, name="x", secretValues={"folder": TYPED_SECRET}), "folder is not a leaf"),
        (
            create(
                type=1,
                name="x",
                secretValues={"login.password": TYPED_SECRET},
                values={"notes": TYPED_SECRET},
            ),
            "the non-secret column notes",
        ),
        (
            create(
                type=1,
                name="x",
                secretValues={"login.password": TYPED_SECRET},
                values={"login.uris": ["https://a.example.test", TYPED_SECRET]},
            ),
            "the non-secret column login.uris",
        ),
        (
            create(type=1, name=TYPED_SECRET, secretValues={"login.password": TYPED_SECRET}),
            "the non-secret column name",
        ),
        (
            create(
                type=1,
                name="x",
                folderName=TYPED_SECRET,
                secretValues={"login.password": TYPED_SECRET},
            ),
            "the non-secret column folder",
        ),
    ],
)
def test_create_item_refuses_secret_values_that_do_not_fit(
    env: AppEnv, operation: dict[str, Any], expected: str
):
    store_templates(env, SERVER_TEMPLATE)
    posted = len(env.bw.posted)
    for route in ("/api/operations", "/api/queue"):
        response = env.client.post(route, json={"operations": [operation]})
        assert response.status_code == 422
        named = expected in response.json()["detail"]
        assert named
        leaked = TYPED_SECRET in response.text
        assert not leaked
    assert len(env.bw.posted) == posted
    assert env.change_log() == []
    assert env.client.get("/api/queue").json() == []


def test_create_item_writes_secret_values_and_an_unnamed_field_becomes_hidden(env: AppEnv):
    store_templates(env, SERVER_TEMPLATE)
    response = env.post(
        create(
            template="Server",
            name="db1",
            secretValues={"fields.root password": TYPED_SECRET, "fields.Recovery": TYPED_SECRET},
        )
    )
    assert response.status_code == 200
    fields = {f["name"]: f for f in env.bw.created[-1]["fields"]}
    assert (fields["root password"]["type"], fields["Recovery"]["type"]) == (1, 1)
    written = fields["root password"]["value"] == fields["Recovery"]["value"] == TYPED_SECRET
    assert written
    leaked = TYPED_SECRET in json.dumps(env.change_log())
    assert not leaked


def test_a_create_item_takes_every_built_in_secret_column(env: AppEnv):
    values = {"card.number": TYPED_SECRET, "card.code": TYPED_SECRET}
    assert env.post(create(type=3, name="Card", secretValues=values)).status_code == 200
    card = env.bw.created[-1]["card"]
    written = card["number"] == card["code"] == TYPED_SECRET
    assert written


@pytest.mark.parametrize("value", [1234, True, ["a-typed-value-5150"], {"a": "a-typed-value-5150"}])
def test_a_secret_value_that_is_no_string_answers_without_the_value(env: AppEnv, value: Any):
    operation = create(type=1, name="x", secretValues={"login.password": value})
    for route in ("/api/operations", "/api/queue"):
        response = env.client.post(route, json={"operations": [operation]})
        assert response.status_code == 422
        assert "secretValues.login.password" in response.json()["detail"]
        leaked = "a-typed-value-5150" in response.text or "1234" in response.text
        assert not leaked
    with pytest.raises(InvalidOperation) as refused:
        parse_operations([operation])
    leaked = "a-typed-value-5150" in str(refused.value) or "1234" in str(refused.value)
    assert not leaked


def test_a_new_ssh_key_gets_its_public_key_and_fingerprint_from_its_private_key(env: AppEnv):
    private_key = ed25519_private_key()
    response = env.post(
        create(type=5, name="Typed key", secretValues={"sshKey.privateKey": private_key})
    )
    assert response.status_code == 200, response.text
    ssh = env.bw.posted[-1]["sshKey"]
    written = ssh["privateKey"] == private_key
    assert written
    derived = (ssh["publicKey"], ssh["keyFingerprint"]) == derive(private_key)
    assert derived
    assert ssh["publicKey"].startswith("ssh-ed25519 ")
    ## a create logs no columns, the derived ones neither
    [row] = env.change_log()
    assert (row["operationKind"], row["columns"], row["result"]) == ("createItem", [], "ok")
    leaked = any(line in json.dumps(env.change_log()) for line in private_key.splitlines()[1:-1])
    assert not leaked


def test_a_public_key_already_given_is_kept(env: AppEnv):
    private_key = ed25519_private_key()
    response = env.post(
        create(
            type=5,
            name="Typed key",
            values={"sshKey.publicKey": "ssh-ed25519 AAAA kept@host"},
            secretValues={"sshKey.privateKey": private_key},
        )
    )
    assert response.status_code == 200, response.text
    ssh = env.bw.posted[-1]["sshKey"]
    assert ssh["publicKey"] == "ssh-ed25519 AAAA kept@host"
    derived = ssh["keyFingerprint"] == derive(private_key)[1]
    assert derived


def test_a_new_ssh_key_with_a_passphrase_fails_and_creates_nothing(env: AppEnv):
    private_key = passphrase_private_key()
    response = env.post(
        create(type=5, name="Locked key", secretValues={"sshKey.privateKey": private_key})
    )
    assert response.status_code == 500
    assert response.json()["applied"] == []
    assert "the private key has a passphrase" in response.json()["detail"]
    ## the tool note the login made is the only item created
    assert [body["type"] for body in env.bw.posted] == [2]
    [row] = env.change_log()
    assert row["result"] == "failed"
    leaked = any(line in response.text for line in private_key.splitlines()[1:-1])
    assert not leaked


LOOKUPS = Lookups.build(FOLDERS, ORGANIZATIONS, COLLECTIONS)

## a login stored as a note, like the dev fixture's notes that are really logins
LOGIN_NOTE: dict[str, Any] = {
    "object": "item",
    "id": "note-2",
    "folderId": "f1",
    "type": 2,
    "reprompt": 1,
    "name": "Router admin",
    "notes": "The router in the hall",
    "favorite": True,
    "fields": [
        {"name": "username", "value": "admin", "type": 0, "linkedId": None},
        {"name": "Password", "value": "router-secret-77", "type": 1, "linkedId": None},
        {"name": "BIOS Password", "value": "bios-secret-12", "type": 1, "linkedId": None},
        {"name": "Serial", "value": "SN-1", "type": 0, "linkedId": None},
    ],
    "secureNote": {"type": 0},
    "attachments": [
        {"id": "a7", "fileName": "config.bin", "size": "140"},
        {"id": "a8", "fileName": "notes.txt", "size": "60"},
    ],
    "revisionDate": "2026-10-26T09:00:00.000Z",
}


def test_note_to_login_maps_fields_into_the_login_slots():
    new = plan_conversion(
        LOGIN_NOTE,
        1,
        {"login.username": "fields.username", "login.password": "fields.Password"},
        LOOKUPS,
    )
    assert new == {
        "type": 1,
        "name": "Router admin",
        "notes": "The router in the hall",
        "favorite": True,
        "reprompt": 1,
        "folderId": "f1",
        "organizationId": None,
        "collectionIds": None,
        "fields": [
            {"name": "BIOS Password", "value": "bios-secret-12", "type": 1, "linkedId": None},
            {"name": "Serial", "value": "SN-1", "type": 0, "linkedId": None},
        ],
        "login": {"username": "admin", "password": "router-secret-77", "totp": None, "uris": []},
    }


def test_login_to_note_turns_unmapped_leaves_into_fields_hidden_when_secret():
    new = plan_conversion(LOGIN, 2, {}, LOOKUPS)
    assert new["type"] == 2
    assert new["secureNote"] == {"type": 0}
    assert "login" not in new
    assert new["fields"][:3] == LOGIN["fields"]
    assert new["fields"][3:] == [
        {"name": "username", "value": "dev@mail.example.test", "type": 0, "linkedId": None},
        {"name": "password", "value": "pw-login-secret-e0e7", "type": 1, "linkedId": None},
        {
            "name": "totp",
            "value": "otpauth://totp/Mail:dev?secret=TOTPSECRETKRSXG5",
            "type": 1,
            "linkedId": None,
        },
        {
            "name": "uris",
            "value": "https://mail.example.test\nhttps://webmail.example.test",
            "type": 0,
            "linkedId": None,
        },
    ]
    assert new["folderId"] == "f1"
    assert new["favorite"] is True
    assert new["reprompt"] == 0


def test_the_changed_columns_of_a_conversion_are_every_leaf_it_moves():
    new = plan_conversion(LOGIN, 2, {}, LOOKUPS)
    assert changed_columns(LOGIN, new, LOOKUPS) == [
        "type",
        "fields.username",
        "fields.password",
        "fields.totp",
        "fields.uris",
        "login.username",
        "login.password",
        "login.totp",
        "login.uris",
    ]


def test_a_mapped_built_in_leaf_becomes_the_named_field_with_the_right_type():
    new = plan_conversion(
        LOGIN, 2, {"fields.Password": "login.password", "fields.Account": "login.username"}, LOOKUPS
    )
    names = [f["name"] for f in new["fields"]]
    assert "password" not in names
    assert "username" not in names
    assert {"name": "Password", "value": "pw-login-secret-e0e7", "type": 1, "linkedId": None} in (
        new["fields"]
    )
    assert {"name": "Account", "value": "dev@mail.example.test", "type": 0, "linkedId": None} in (
        new["fields"]
    )


def test_mapping_into_an_existing_field_keeps_its_type_and_the_secret_rule_applies():
    new = plan_conversion(LOGIN, 2, {"fields.User": "login.username"}, LOOKUPS)
    assert new["fields"][0] == {
        "name": "User",
        "value": "dev@mail.example.test",
        "type": 0,
        "linkedId": None,
    }
    with pytest.raises(ValueError, match="secret login.password may not be mapped"):
        plan_conversion(LOGIN, 2, {"fields.User": "login.password"}, LOOKUPS)
    with pytest.raises(ValueError, match="non-secret notes"):
        plan_conversion(LOGIN, 2, {"notes": "login.password"}, LOOKUPS)
    with pytest.raises(ValueError, match="non-secret login.username"):
        plan_conversion(LOGIN_NOTE, 1, {"login.username": "fields.Password"}, LOOKUPS)


def test_notes_as_a_source_moves():
    new = plan_conversion(LOGIN_NOTE, 1, {"fields.Comment": "notes"}, LOOKUPS)
    assert new["notes"] is None
    assert new["fields"][-1] == {
        "name": "Comment",
        "value": "The router in the hall",
        "type": 0,
        "linkedId": None,
    }


def test_card_to_login_and_org_item_keeps_the_collections():
    new = plan_conversion(CARD, 1, {"login.username": "card.cardholderName"}, LOOKUPS)
    assert new["login"]["username"] == "Dev User"
    assert [(f["name"], f["type"]) for f in new["fields"]] == [
        ("brand", 0),
        ("number", 1),
        ("expMonth", 0),
        ("expYear", 0),
        ("code", 1),
    ]
    org = plan_conversion(NOTE, 1, {}, LOOKUPS)
    assert org["organizationId"] == "o1"
    assert org["collectionIds"] == ["c2", "c1"]
    assert org["fields"] == NOTE["fields"]


@pytest.mark.parametrize(
    ("new_type", "mapping", "message"),
    [
        (2, {}, "already has that type"),
        (7, {}, "unknown item type"),
        (1, {"login.username": "folder"}, "cannot be mapped"),
        (1, {"login.username": "attachments"}, "cannot be mapped"),
        (1, {"card.code": "fields.Password"}, "a login has no card.code"),
        (1, {"type": "fields.Serial"}, "not a leaf"),
        (1, {"login.username": "nope"}, "Unknown coordinate"),
    ],
)
def test_bad_conversions_are_refused(new_type: int, mapping: dict[str, str], message: str):
    with pytest.raises(ValueError, match=message):
        plan_conversion(LOGIN_NOTE, new_type, mapping, LOOKUPS)


def test_an_empty_or_identical_source_is_skipped():
    new = plan_conversion(
        LOGIN_NOTE, 1, {"login.totp": "fields.Missing", "fields.Serial": "fields.Serial"}, LOOKUPS
    )
    assert new["login"]["totp"] is None
    assert {"name": "Serial", "value": "SN-1", "type": 0, "linkedId": None} in new["fields"]


## in the vault already, so the login creates no item before the tests count them
TOOL_NOTE = {
    "object": "item",
    "id": "tool",
    "type": 2,
    "name": "bwtidy",
    "notes": None,
    "fields": [],
    "secureNote": {"type": 0},
    "revisionDate": "2026-10-26T09:00:00.000Z",
}


def direct_bw() -> FakeBwServe:
    bw = sample_bw([LOGIN, NOTE, LOGIN_NOTE, TOOL_NOTE])
    bw.attachments[("note-2", "a7")] = b"binary config"
    bw.attachments[("note-2", "a8")] = b"plain notes"
    bw.attachments[("login-1", "a1")] = b"codes"
    return bw


def convert(env: AppEnv, item_id: str, new_type: int, mapping: dict[str, str]):
    """Convert on the vault as the fake bw serve holds it now, straight through the write path."""
    env.reload()
    revision = next(i["revisionDate"] for i in env.cache.items if i["id"] == item_id)
    op = ConvertItem(
        kind="convertItem",
        itemId=item_id,
        revisionDate=revision,
        newType=new_type,
        mapping=mapping,
    )
    return env.call(lambda: env.writes.apply([op], "ui"))


def ids(env: AppEnv) -> list[str]:
    return [i["id"] for i in env.bw.items]


@pytest.fixture
def direct(tmp_path: Path) -> Iterator[AppEnv]:
    """The app's write path on three items, called without the routes around it."""
    yield from logged_in(tmp_path, bw=direct_bw())


def test_convert_creates_moves_attachments_and_trashes_the_old_item_last(direct: AppEnv):
    result = convert(
        direct,
        "note-2",
        1,
        {"login.username": "fields.username", "login.password": "fields.Password"},
    )
    assert result.applied == 1
    [posted] = direct.bw.posted
    assert posted["type"] == 1
    assert posted["login"]["username"] == "admin"
    assert posted["login"]["password"] == "router-secret-77"
    assert [f["name"] for f in posted["fields"]] == ["BIOS Password", "Serial"]
    new_id = direct.bw.created[0]["id"]
    assert direct.bw.uploads == [(new_id, "config.bin"), (new_id, "notes.txt")]
    assert direct.bw.attachments[(new_id, "up-1")] == b"binary config"
    assert direct.bw.attachments[(new_id, "up-2")] == b"plain notes"
    assert [i["id"] for i in direct.bw.trashed] == ["note-2"]
    assert "note-2" not in ids(direct)
    assert new_id in ids(direct)
    requests = direct.bw.requests
    create = requests.index(("POST", "/object/item"))
    first_upload = requests.index(("POST", "/attachment"))
    trash = requests.index(("DELETE", "/object/item/note-2"))
    ## bw serve knows the new item only after a sync, so one runs before the uploads
    assert ("POST", "/sync") in requests[create:first_upload]
    assert trash > first_upload
    writes = [i for i, r in enumerate(requests) if r[0] != "GET" and r != ("POST", "/sync")]
    assert trash == writes[-1]
    ## the cache was reloaded with the new item
    assert new_id in [i["id"] for i in direct.cache.items]
    [entry] = direct.writes.db.latest()
    assert (entry["operationKind"], entry["itemIds"], entry["result"]) == (
        "convertItem",
        ["note-2"],
        "ok",
    )
    ## the conversion names the type and each mapped pair, a note has no built-in leaf to move
    assert entry["columns"] == [
        "type",
        "fields.username",
        "login.username",
        "fields.Password",
        "login.password",
    ]


def test_a_failed_upload_trashes_the_new_item_and_keeps_the_old(direct: AppEnv):
    direct.bw.fail_at.add(("POST", "/attachment"))
    with pytest.raises(WriteFailed) as error:
        convert(direct, "note-2", 1, {})
    assert error.value.applied == []
    assert error.value.failed == 0
    assert "forced failure" in str(error.value)
    new_id = direct.bw.created[0]["id"]
    assert [i["id"] for i in direct.bw.trashed] == [new_id]
    assert "note-2" in ids(direct)
    assert new_id not in ids(direct)
    assert ("DELETE", f"/object/item/{new_id}") in direct.bw.requests
    [entry] = direct.writes.db.latest()
    assert entry["result"] == "failed"


def test_a_failed_trash_of_the_old_item_rolls_back_too(direct: AppEnv):
    direct.bw.fail_at.add(("DELETE", "/object/item/note-2"))
    with pytest.raises(WriteFailed):
        convert(direct, "note-2", 1, {})
    new_id = direct.bw.created[0]["id"]
    assert [i["id"] for i in direct.bw.trashed] == [new_id]
    assert "note-2" in ids(direct)


def test_a_failed_download_before_any_upload_leaves_nothing_new(direct: AppEnv):
    direct.bw.fail_at.add(("GET", "/object/attachment/a7"))
    with pytest.raises(WriteFailed):
        convert(direct, "note-2", 1, {})
    assert direct.bw.uploads == []
    assert [i["id"] for i in direct.bw.trashed] == [direct.bw.created[0]["id"]]


def test_a_refused_mapping_creates_nothing(direct: AppEnv):
    with pytest.raises(InvalidOperation, match="secret"):
        convert(direct, "login-1", 2, {"notes": "login.password"})
    assert direct.bw.posted == []
    assert direct.bw.trashed == []
    assert direct.writes.db.latest() == []


def test_convert_waits_out_bw_serves_decrypt_window_after_its_sync(direct: AppEnv):
    """After the sync that makes bw serve know the new item, it answers Not found for every
    object for a moment, so the attachments wait until it lists the new item."""
    direct.bw.empty_lists_after_sync = 2
    result = convert(direct, "note-2", 1, {"login.username": "fields.username"})
    assert result.applied == 1
    new_id = direct.bw.created[0]["id"]
    assert direct.bw.uploads == [(new_id, "config.bin"), (new_id, "notes.txt")]
    assert [i["id"] for i in direct.bw.trashed] == ["note-2"]
    requests = direct.bw.requests
    create = requests.index(("POST", "/object/item"))
    first_upload = requests.index(("POST", "/attachment"))
    ## the sync, two empty lists and the one that names the new item come before the uploads
    assert requests[create:first_upload].count(("GET", "/list/object/items")) == 3
    assert ("GET", "/object/attachment/a7") in requests[create:first_upload]
    assert not any(r == ("DELETE", f"/object/item/{new_id}") for r in requests)


def key_note(private_key: str) -> dict[str, Any]:
    return {
        "object": "item",
        "id": "note-key",
        "type": 2,
        "reprompt": 0,
        "name": "Deploy key",
        "notes": private_key,
        "favorite": False,
        "fields": [],
        "secureNote": {"type": 0},
        "revisionDate": "2026-10-26T09:30:00.000Z",
    }


KEY_MAPPING = {"sshKey.privateKey": "notes"}


def key_conversion() -> ConvertItem:
    return ConvertItem(
        kind="convertItem",
        itemId="note-key",
        revisionDate="2026-10-26T09:30:00.000Z",
        newType=5,
        mapping=KEY_MAPPING,
    )


def test_a_note_that_holds_a_key_becomes_an_ssh_key_with_all_three(direct: AppEnv):
    private_key = ed25519_private_key()
    direct.bw.items.append(key_note(private_key))
    result = convert(direct, "note-key", 5, KEY_MAPPING)
    assert result.applied == 1
    [posted] = direct.bw.posted
    ssh = posted["sshKey"]
    moved = ssh["privateKey"] == private_key and not posted.get("notes")
    assert moved
    derived = (ssh["publicKey"], ssh["keyFingerprint"]) == derive(private_key)
    assert derived
    assert [i["id"] for i in direct.bw.trashed] == ["note-key"]
    [entry] = direct.writes.db.latest()
    assert entry["result"] == "ok"
    assert entry["columns"] == [
        "type",
        "notes",
        "sshKey.privateKey",
        "sshKey.publicKey",
        "sshKey.keyFingerprint",
    ]


def test_the_queue_diff_of_the_conversion_shows_the_derived_columns(direct: AppEnv):
    private_key = ed25519_private_key()
    direct.bw.items.append(key_note(private_key))

    direct.reload()
    rows = {
        row["column"]: row for row in diff(key_conversion(), direct.cache, direct.writes.remembered)
    }
    public_key, fingerprint = derive(private_key)
    assert rows["sshKey.privateKey"]["after"] == {"masked": True}
    shown = (rows["sshKey.publicKey"]["after"], rows["sshKey.keyFingerprint"]["after"]) == (
        public_key,
        fingerprint,
    )
    assert shown


def test_a_note_that_holds_a_key_with_a_passphrase_fails_and_creates_nothing(direct: AppEnv):
    private_key = passphrase_private_key()
    direct.bw.items.append(key_note(private_key))
    with pytest.raises(WriteFailed) as error:
        convert(direct, "note-key", 5, KEY_MAPPING)
    assert error.value.reason == "the private key has a passphrase"
    leaked = any(line in str(error.value) for line in private_key.splitlines()[1:-1])
    assert not leaked
    assert direct.bw.posted == []
    assert direct.bw.trashed == []
    assert "note-key" in ids(direct)
    [entry] = direct.writes.db.latest()
    assert entry["result"] == "failed"
    ## the diff does not fail on it, the write does
    rows = {row["column"] for row in diff(key_conversion(), direct.cache, direct.writes.remembered)}
    assert "sshKey.privateKey" in rows
    assert "sshKey.publicKey" not in rows


def test_a_note_that_holds_no_key_fails_and_creates_nothing(direct: AppEnv):
    direct.bw.items.append(key_note("just some notes"))
    with pytest.raises(WriteFailed) as error:
        convert(direct, "note-key", 5, KEY_MAPPING)
    assert error.value.reason == "the private key cannot be read"
    assert direct.bw.posted == []
