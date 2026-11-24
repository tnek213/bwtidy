"""createItem against the seeded dev vault through POST /api/operations.

A login with a generated password, a secure note from a template with a hidden field, a card
in a new folder and an organization item in a collection, then a Restore of one change log
row, which moves its item to the trash, and secret values a person typed, queued as ciphertext.
The module deletes every item it made for good at the end, and the folder it made. Nothing here
prints an item or a value: every secret is compared into a bool before the assert.
"""

import json
import secrets
import string
from collections.abc import Iterator
from typing import Any

import pytest
from support.app import App
from support.cli import BwCli
from support.restorer import Restorer
from support.vault import raw_sqlite, same_secret

pytestmark = pytest.mark.integration

LOGIN, NOTE, CARD = 1, 2, 3
FOLDER = "Created by bwtidy test"
TEMPLATES = {
    "version": 1,
    "templates": [
        {
            "name": "Created server",
            "type": NOTE,
            "coordinates": [
                {"kind": "field", "name": "host", "fieldType": 0},
                {"kind": "field", "name": "root password", "fieldType": 1},
            ],
        },
        {
            "name": "Created keys",
            "type": NOTE,
            "coordinates": [
                {"kind": "field", "name": "generated key", "fieldType": 1},
                {"kind": "field", "name": "blank key", "fieldType": 1},
            ],
        },
    ],
}


class Creator(Restorer):
    """The Restorer of test_restore plus the items this module made."""

    made: list[str]

    def create(self, **fields: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        """Apply one createItem, answer its change log row and the new item as bw holds it."""
        response = self.post({"kind": "createItem", **fields})
        assert response.status_code == 200, response.text
        row = self.row()
        assert (row["operationKind"], row["itemIds"], row["result"]) == ("createItem", [], "ok")
        [new_id] = row["createdItemIds"]
        self.made.append(new_id)
        return row, self.raw(new_id)


@pytest.fixture(scope="module")
def vault(app: App, bw_cli: BwCli) -> Iterator[Creator]:
    assert app.login().status_code == 200
    app.settle()
    vault = Creator(app, bw_cli)
    vault.made = []
    yield vault
    assert app.client.portal is not None
    vault.beat()
    vault.cli_run("sync")
    for item_id in vault.made:
        vault.cli_run("delete", "item", item_id, "--permanent")
    for folder in vault.cli("list", "folders"):
        if folder.get("name") == FOLDER:
            vault.cli_run("delete", "folder", folder["id"])
    app.client.portal.call(vault.client.sync)
    app.client.portal.call(app.session.cache.load)


def strong(password: Any) -> bool:
    """24 characters with upper and lower case letters, numbers and special characters."""
    if not isinstance(password, str) or len(password) != 24:
        return False
    classes = (string.ascii_uppercase, string.ascii_lowercase, string.digits)
    has_classes = all(any(c in chars for c in password) for chars in classes)
    return has_classes and any(not c.isalnum() for c in password)


def test_a_login_with_a_generated_password(vault: Creator):
    row, item = vault.create(
        type=LOGIN,
        name="Created login",
        values={"login.username": "created", "login.uris": ["https://created.example.test"]},
        generatePassword=True,
    )
    assert item["type"] == LOGIN
    assert item["login"]["username"] == "created"
    assert [u["uri"] for u in item["login"]["uris"]] == ["https://created.example.test"]
    password = item["login"]["password"]
    is_strong = strong(password)
    assert is_strong
    ## the password is in the vault only: not in the change log, the grid or the queue
    shown = json.dumps(vault.change_log()) + vault.app.client.get("/api/grid").text
    shown += vault.app.client.get("/api/queue").text
    leaked = password in shown
    assert not leaked


def test_a_secure_note_from_a_template_with_a_hidden_field(vault: Creator):
    vault.set_templates(json.dumps(TEMPLATES))
    _, item = vault.create(
        template="Created server", name="Created server", values={"fields.host": "db1"}
    )
    assert item["type"] == NOTE
    assert item["secureNote"] == {"type": 0}
    fields = [(f["name"], f.get("value"), f["type"]) for f in item["fields"]]
    assert fields == [("host", "db1", 0), ("root password", None, 1)]


def test_a_generated_hidden_field_from_a_template(vault: Creator):
    vault.set_templates(json.dumps(TEMPLATES))
    _, item = vault.create(
        template="Created keys", name="Created keys", generateColumns=["fields.generated key"]
    )
    fields = {f["name"]: f for f in item["fields"]}
    assert (fields["generated key"]["type"], fields["blank key"]["type"]) == (1, 1)
    assert fields["blank key"].get("value") is None
    ## non-empty and as long as the default generator settings say
    value = fields["generated key"]["value"]
    is_strong = strong(value)
    assert is_strong
    shown = json.dumps(vault.change_log()) + vault.app.client.get("/api/grid").text
    shown += vault.app.client.get("/api/queue").text
    leaked = value in shown
    assert not leaked


def test_a_card_in_a_new_folder(vault: Creator):
    assert FOLDER not in [f.get("name") for f in vault.cli("list", "folders")]
    _, item = vault.create(
        type=CARD, name="Created card", folderName=FOLDER, values={"card.brand": "Visa"}
    )
    assert item["type"] == CARD
    assert item["card"]["brand"] == "Visa"
    assert not item["card"].get("number")
    [folder] = [f for f in vault.cli("list", "folders") if f.get("name") == FOLDER]
    assert item["folderId"] == folder["id"]


def test_an_organization_item_in_a_collection(vault: Creator):
    _, item = vault.create(
        type=NOTE, name="Created shared note", organizationName="Family", collectionNames=["Shared"]
    )
    organization = next(o for o in vault.app.session.cache.organizations if o["name"] == "Family")
    collection = next(
        c
        for c in vault.app.session.cache.collections
        if c["name"] == "Shared" and c["organizationId"] == organization["id"]
    )
    assert item["organizationId"] == organization["id"]
    assert item["collectionIds"] == [collection["id"]]


def test_restore_of_a_create_moves_the_new_item_to_the_trash(vault: Creator):
    row, item = vault.create(type=LOGIN, name="Created and restored", generatePassword=True)
    assert not vault.in_trash(item["id"])
    vault.restore(row, ["trash"])
    assert vault.in_trash(item["id"])
    assert item["id"] not in {i["id"] for i in vault.items()}


def test_secret_values_queued_as_ciphertext_reach_the_vault(vault: Creator):
    vault.set_templates(json.dumps(TEMPLATES))
    password, root = (f"typed-{secrets.token_hex(8)}" for _ in range(2))
    client = vault.app.client
    vault.beat()
    login = {
        "kind": "createItem",
        "type": LOGIN,
        "name": "Created with a typed password",
        "secretValues": {"login.password": password},
    }
    server = {
        "kind": "createItem",
        "template": "Created server",
        "name": "Created server with a typed secret",
        "values": {"fields.host": "db2"},
        "secretValues": {"fields.root password": root},
    }
    response = client.post("/api/queue", json={"operations": [login, server]})
    assert response.status_code == 201, response.text
    ids = response.json()["ids"]
    in_queue = password in client.get("/api/queue").text or root in client.get("/api/queue").text
    assert not in_queue
    in_sqlite = password.encode() in raw_sqlite(vault) or root.encode() in raw_sqlite(vault)
    assert not in_sqlite

    vault.beat()
    response = client.post("/api/queue/commit")
    assert response.status_code == 200, response.text
    assert sorted(response.json()["applied"]) == sorted(ids)
    vault.app.settle()
    vault.beat()
    created = [r for r in vault.change_log() if r["operationKind"] == "createItem"][:2]
    new_ids = [i for row in created for i in row["createdItemIds"]]
    vault.made += new_ids
    items = {i["name"]: i for i in (vault.cli("get", "item", new_id) for new_id in new_ids)}
    item = items["Created with a typed password"]
    assert item["type"] == LOGIN
    password_written = same_secret(item["login"]["password"], password)
    assert password_written
    fields = items["Created server with a typed secret"]["fields"]
    assert [(f["name"], f["type"]) for f in fields] == [("host", 0), ("root password", 1)]
    root_written = same_secret(fields[1]["value"], root)
    assert root_written

    shown = client.get("/api/queue").text + json.dumps(vault.change_log())
    leaked = password in shown or root in shown
    assert not leaked
    in_sqlite = password.encode() in raw_sqlite(vault) or root.encode() in raw_sqlite(vault)
    assert not in_sqlite


def test_a_secret_value_through_the_operations_route(vault: Creator):
    number = f"4{secrets.randbelow(10**15):015d}"
    _, item = vault.create(
        type=CARD, name="Created typed card", secretValues={"card.number": number}
    )
    written = same_secret(vault.cli("get", "item", item["id"])["card"]["number"], number)
    assert written
    leaked = number in json.dumps(vault.change_log())
    assert not leaked
