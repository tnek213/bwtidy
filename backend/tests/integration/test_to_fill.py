"""The to-fill list against the seeded dev vault through a real bw serve.

What only a real server can tell: a created item's entry lands in the real tool note's notes,
and a value set with bw directly (as another Bitwarden client) takes it off at the next login.
The module deletes the item it made for good at the end, the tool note is deleted by the app
fixture. Nothing here prints an item or a value.
"""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from support.app import App
from support.cli import BwCli
from support.vault import Vault, edit_by_cli

from bwtidy.vault.tool_note import TOOL_NOTE_NAME

pytestmark = pytest.mark.integration

LOGIN = 1
NAME = "To fill login"


class Filler(Vault):
    """The Vault of test_writes plus the item this module made."""

    made: list[str]


@pytest.fixture(scope="module")
def vault(app: App, bw_cli: BwCli) -> Iterator[Filler]:
    assert app.login().status_code == 200
    app.settle()
    vault = Filler(app, bw_cli)
    vault.made = []
    yield vault
    assert app.client.portal is not None
    vault.beat()
    vault.cli_run("sync")
    for item_id in vault.made:
        vault.cli_run("delete", "item", item_id, "--permanent")
    app.client.portal.call(vault.client.sync)
    app.client.portal.call(app.session.cache.load)


def commit(vault: Filler) -> dict[str, Any]:
    vault.beat()
    response = vault.app.client.post("/api/queue/commit")
    assert response.status_code == 200, response.text
    return response.json()


def note_revision(vault: Filler) -> str | None:
    """The tool note's revisionDate before a commit that writes it, None while it does not exist."""
    notes = [i for i in vault.items() if i["name"] == TOOL_NOTE_NAME]
    return notes[0]["revisionDate"] if notes else None


def to_fill_in_note(vault: Filler, before: str | None) -> list[dict[str, Any]]:
    """The list in the tool note, read once bw serve shows the commit that wrote it."""
    note = vault.raw_after(vault.by_name(TOOL_NOTE_NAME)["id"], before)
    return json.loads(note["notes"]).get("toFill", [])


def test_a_new_login_is_listed_and_a_value_set_elsewhere_takes_it_off(vault: Filler):
    client = vault.app.client
    vault.beat()
    create = {
        "kind": "createItem",
        "type": LOGIN,
        "name": NAME,
        "values": {"login.username": "to-fill"},
    }
    response = client.post("/api/queue", json={"operations": [create]})
    assert response.status_code == 201, response.text
    committed = commit(vault)
    assert committed["failed"] == []
    item = vault.by_name(NAME)
    vault.made.append(item["id"])
    assert not item["login"].get("password")
    assert client.get("/api/grid").json()["toFill"] == {item["id"]: ["login.password"]}

    ## the create queued the list, this commit writes it to the note
    before = note_revision(vault)
    assert commit(vault)["failed"] == []
    [entry] = to_fill_in_note(vault, before)
    assert (entry["itemId"], entry["columns"]) == (item["id"], ["login.password"])
    assert client.get("/api/queue", params={"status": "pending"}).json() == []

    login = {**vault.raw(item["id"])["login"], "password": "set-with-bw-directly"}
    edit_by_cli(vault, item["id"], login=login)
    assert vault.app.login().status_code == 200
    vault.app.settle()
    vault.beat()
    filled = bool(vault.by_name(NAME)["login"].get("password"))
    assert filled
    assert client.get("/api/grid").json()["toFill"] == {}

    before = note_revision(vault)
    assert commit(vault)["failed"] == []
    assert to_fill_in_note(vault, before) == []
    ## a list that is already right queues nothing, so the commit above was the last write
    assert client.get("/api/queue", params={"status": "pending"}).json() == []
