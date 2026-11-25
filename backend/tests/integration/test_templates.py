"""Templates against the seeded dev vault: save one in the tool note, apply it, match it.

The computer exists in three shapes in the dev vault (dev/dev-vault.md): two logins and one
note. A Computer template of type note is saved, applied to both logins (a type change) and
to the note (same type), and every item then matches or nearly matches it. Every write is
undone at the end of the module, the tool note is deleted by the app fixture. Nothing here
prints an item or a value.
"""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from support.app import App
from support.cli import BwCli
from support.vault import Vault, edit_by_cli, same_secret

from bwtidy.vault.tool_note import TOOL_NOTE_NAME, ToolNoteId, find_tool_note
from tests.support.section_changes import (
    add_datatype,
    add_template,
    changes,
    remove_datatype,
    remove_template,
    template_changes,
)

pytestmark = pytest.mark.integration

NOTE = 2
TEXT, HIDDEN = 0, 1

COMPUTER = {
    "name": "Computer",
    "type": NOTE,
    "coordinates": [
        {"kind": "field", "name": "user", "fieldType": TEXT},
        {"kind": "field", "name": "user password", "fieldType": HIDDEN},
        {"kind": "field", "name": "BIOS password", "fieldType": HIDDEN},
        {"kind": "field", "name": "disk encryption", "fieldType": HIDDEN},
    ],
}


@pytest.fixture(scope="module")
def vault(app: App, bw_cli: BwCli) -> Iterator[Vault]:
    assert app.login().status_code == 200
    vault = Vault(app, bw_cli)
    yield vault
    assert app.client.portal is not None
    vault.beat()
    vault.cli_run("sync")
    for undo in reversed(vault.undo):
        app.client.portal.call(undo)
    app.client.portal.call(vault.client.sync)
    app.client.portal.call(app.session.cache.load)


def fields(item: dict[str, Any]) -> dict[str, tuple[int, str | None]]:
    return {f["name"]: (f["type"], f.get("value")) for f in item.get("fields") or []}


def test_save_the_computer_template_and_apply_it_to_every_computer(vault: Vault):
    client = vault.app.client
    vault.beat()
    stored = client.get("/api/templates").json()
    assert stored["templates"] == []
    body = {**changes(add_template(COMPUTER)), "revisionDate": stored["revisionDate"]}
    response = client.put("/api/templates", json=body)
    assert response.status_code == 202, response.text
    saved = response.json()
    assert saved["templates"] == [COMPUTER]
    ## the PUT queued the save and started its commit, this waits for it
    vault.beat()
    committed = client.post("/api/queue/commit").json()
    applied = client.get("/api/queue", params={"status": "applied"}).json()
    assert [e["id"] for e in applied] == [saved["entryId"]]
    assert committed["failed"] == []
    assert applied[0]["result"]["backup"] in vault.backups()
    note = vault.raw_after(vault.by_name(TOOL_NOTE_NAME)["id"], stored["revisionDate"])
    assert note["revisionDate"] != stored["revisionDate"]
    assert json.loads(note["notes"]) == {"version": 1, "templates": [COMPUTER]}
    assert [f["name"] for f in note["fields"]] == ["backup-password"]
    templates = client.get("/api/templates").json()
    assert templates["templates"] == [COMPUTER]
    assert templates["saving"] is False
    assert templates["revisionDate"] == note["revisionDate"]

    thinkpad = vault.raw(vault.by_name("ThinkPad X1")["id"])
    desktop = vault.raw(vault.by_name("Gaming desktop")["id"])
    media = vault.raw(vault.by_name("Media PC")["id"])
    assert (thinkpad["type"], desktop["type"], media["type"]) == (1, 1, NOTE)
    created_thinkpad: list[str] = []
    created_desktop: list[str] = []
    vault.undo_convert(thinkpad["id"], created_thinkpad)
    vault.undo_convert(desktop["id"], created_desktop)
    vault.undo_put(media)
    matches = client.get("/api/template-matches").json()
    assert note["id"] not in matches
    assert matches[thinkpad["id"]]["match"] is None
    assert matches[thinkpad["id"]]["scores"][0]["typeMatches"] is False
    assert matches[media["id"]]["scores"][0]["filled"] == 3

    response = vault.post(
        vault.op(
            "applyTemplate",
            thinkpad,
            template="Computer",
            mapping={
                "fields.user": "fields.User",
                "fields.user password": "fields.User password",
                "fields.BIOS password": "fields.BIOS Password",
                "fields.disk encryption": "fields.LUKS",
            },
        ),
        vault.op(
            "applyTemplate",
            desktop,
            template="Computer",
            mapping={
                "fields.user": "login.username",
                "fields.user password": "login.password",
                "fields.BIOS password": "fields.UEFI Administrator Password",
                "fields.disk encryption": "fields.LUKS/dm-crypt and Bitlocker",
            },
        ),
        vault.op(
            "applyTemplate",
            media,
            template="Computer",
            mapping={"fields.disk encryption": "fields.dm-crypt password"},
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 3

    new_thinkpad = vault.by_name("ThinkPad X1")
    new_desktop = vault.by_name("Gaming desktop")
    created_thinkpad.append(new_thinkpad["id"])
    created_desktop.append(new_desktop["id"])
    assert new_thinkpad["id"] != thinkpad["id"]
    assert new_desktop["id"] != desktop["id"]
    trash = {i["id"] for i in vault.cli("list", "items", "--trash")}
    assert {thinkpad["id"], desktop["id"]} <= trash
    assert media["id"] not in trash

    matches = client.get("/api/template-matches").json()
    assert matches[new_thinkpad["id"]]["match"] == "Computer"
    assert matches[new_desktop["id"]]["match"] == "Computer"
    assert matches[media["id"]]["match"] is None
    assert matches[media["id"]]["scores"][0]["filled"] == 4

    new = vault.raw(new_thinkpad["id"])
    assert new["type"] == NOTE
    assert new["folderId"] == thinkpad["folderId"]
    old_fields = fields(thinkpad)
    new_fields = fields(new)
    assert sorted(new_fields) == sorted(
        ["MSDM Windows Key", "user", "user password", "BIOS password", "disk encryption"]
    )
    assert new_fields["user"] == (TEXT, "dev")
    for target, source in [
        ("user password", "User password"),
        ("BIOS password", "BIOS Password"),
        ("disk encryption", "LUKS"),
    ]:
        assert new_fields[target][0] == HIDDEN
        value_moved = same_secret(new_fields[target][1] or "", old_fields[source][1] or "")
        assert value_moved, f"{target} does not hold the value of {source}"
    assert [a["fileName"] for a in new["attachments"]] == [
        a["fileName"] for a in thinkpad["attachments"]
    ]

    new = vault.raw(new_desktop["id"])
    new_fields = fields(new)
    assert sorted(new_fields) == sorted(
        [
            "Bitlocker RecoveryPassword",
            "user",
            "user password",
            "BIOS password",
            "disk encryption",
        ]
    )
    assert new_fields["user"] == (TEXT, desktop["login"]["username"])
    assert new_fields["user password"][0] == HIDDEN
    password_moved = same_secret(new_fields["user password"][1] or "", desktop["login"]["password"])
    assert password_moved, "user password does not hold the login password"
    assert new_fields["Bitlocker RecoveryPassword"][0] == HIDDEN

    after = vault.raw_after(media["id"], media["revisionDate"])
    after_fields = fields(after)
    assert "dm-crypt password" not in after_fields
    assert after_fields["disk encryption"][0] == HIDDEN
    disk_value_kept = same_secret(
        after_fields["disk encryption"][1] or "", fields(media)["dm-crypt password"][1] or ""
    )
    assert disk_value_kept, "disk encryption does not hold the dm-crypt password"
    ## the missing template field is there, empty and hidden
    assert after_fields["BIOS password"][0] == HIDDEN
    assert not after_fields["BIOS password"][1]
    assert after["notes"] == media["notes"]
    log = vault.change_log()
    assert [e["operationKind"] for e in log[:3]] == ["applyTemplate"] * 3


def rename_by_cli(vault: Vault, note_id: str, name: str) -> None:
    edit_by_cli(vault, note_id, name=name)


def personal_notes_named_bwtidy(vault: Vault) -> list[dict[str, Any]]:
    vault.cli_run("sync")
    items = vault.cli("list", "items", "--search", TOOL_NOTE_NAME)
    return [
        i
        for i in items
        if i["type"] == NOTE
        and i["name"].startswith(TOOL_NOTE_NAME)
        and not i.get("organizationId")
    ]


def test_a_note_renamed_in_bitwarden_keeps_the_templates_and_no_second_note(vault: Vault):
    client = vault.app.client
    vault.beat()
    before = client.get("/api/templates").json()
    note_id = before["toolNoteId"]
    assert before["toolNoteName"] == TOOL_NOTE_NAME
    rename_by_cli(vault, note_id, "bwtidyK")
    try:
        assert vault.app.login().status_code == 200
        vault.beat()
        after = client.get("/api/templates").json()
        assert after["toolNoteId"] == note_id
        assert after["toolNoteName"] == "bwtidyK"
        assert after["templates"] == before["templates"]
        assert client.get("/api/grid").json()["toolNoteId"] == note_id
        assert [n["id"] for n in personal_notes_named_bwtidy(vault)] == [note_id]

        response = client.post(
            "/api/tool-note/rename-back", json={"revisionDate": after["revisionDate"]}
        )
        assert response.status_code == 200, response.text
        assert response.json()["toolNoteName"] == TOOL_NOTE_NAME
        renamed = vault.raw_after(note_id, after["revisionDate"])
        assert renamed["name"] == TOOL_NOTE_NAME
        assert client.get("/api/templates").json()["toolNoteName"] == TOOL_NOTE_NAME
    finally:
        ## the app fixture purges the note by its name, so it must be bwtidy again
        if vault.raw(note_id)["name"] != TOOL_NOTE_NAME:
            rename_by_cli(vault, note_id, TOOL_NOTE_NAME)


def test_keep_this_name_on_the_real_note(vault: Vault):
    client = vault.app.client
    vault.beat()
    before = client.get("/api/templates").json()
    note_id = before["toolNoteId"]
    notes = vault.raw(note_id)["notes"]
    rename_by_cli(vault, note_id, "bwtidyK")
    try:
        assert vault.app.login().status_code == 200
        vault.beat()
        after = client.get("/api/templates").json()
        assert (after["toolNoteName"], after["toolNoteExpectedName"]) == ("bwtidyK", "bwtidy")

        response = client.post("/api/tool-note/keep-name", json={"name": "bwtidyK"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["toolNoteName"], body["toolNoteExpectedName"]) == ("bwtidyK", "bwtidyK")
        assert body["backup"]
        kept = vault.raw_after(note_id, after["revisionDate"])
        assert kept["name"] == "bwtidyK"
        assert json.loads(kept["notes"]) == {**json.loads(notes), "note": {"name": "bwtidyK"}}
        doc = client.get("/api/templates").json()
        assert (doc["toolNoteName"], doc["toolNoteExpectedName"]) == ("bwtidyK", "bwtidyK")
        assert doc["templates"] == before["templates"]
        ## a bwtidy that does not know the id (a reinstall) finds the note by the name it kept
        found = find_tool_note(vault.app.session.cache, ToolNoteId())
        assert found is not None
        assert found["id"] == note_id
        assert [n["id"] for n in personal_notes_named_bwtidy(vault)] == [note_id]
    finally:
        ## the app fixture purges the note by its name, so it must be bwtidy again
        edit_by_cli(vault, note_id, name=TOOL_NOTE_NAME, notes=notes)
        vault.call(vault.client.sync)
        vault.call(vault.app.session.cache.load)


def test_a_templates_save_keeps_a_section_it_does_not_know(vault: Vault):
    client = vault.app.client
    vault.beat()
    before = client.get("/api/templates").json()
    note_id = before["toolNoteId"]
    notes = vault.raw(note_id)["notes"]
    sections = json.loads(notes)
    edit_by_cli(vault, note_id, notes=json.dumps({**sections, "later": {"kept": [1, 2]}}))
    edited_revision = vault.cli("get", "item", note_id)["revisionDate"]
    try:
        vault.beat()
        body = {"changes": template_changes(before["templates"], [COMPUTER])}
        response = client.put("/api/templates", json=body)
        assert response.status_code == 202, response.text
        vault.beat()
        committed = client.post("/api/queue/commit").json()
        assert committed["failed"] == []
        saved = json.loads(vault.raw_after(note_id, edited_revision)["notes"])
        assert saved == {"version": 1, "templates": [COMPUTER], "later": {"kept": [1, 2]}}
        assert client.get("/api/templates").json()["templates"] == [COMPUTER]
    finally:
        edit_by_cli(vault, note_id, notes=notes)
        vault.call(vault.client.sync)
        vault.call(vault.app.session.cache.load)


def test_datatypes_save_load_and_score_on_the_real_note(vault: Vault):
    client = vault.app.client
    vault.beat()
    stored = client.get("/api/templates").json()
    note_id = stored["toolNoteId"]
    new = {"name": "Media name", "pattern": "Media PC", "examples": ["Media PC"]}
    saved = client.put("/api/datatypes", json=changes(add_datatype(new)))
    assert saved.status_code == 202, saved.text
    datatype = saved.json()["datatypes"][0]
    named = {
        "name": "Named",
        "type": NOTE,
        "coordinates": [{"kind": "path", "path": "name", "datatype": datatype["id"]}],
    }
    response = client.put("/api/templates", json=changes(add_template(named)))
    assert response.status_code == 202, response.text
    vault.beat()
    assert client.post("/api/queue/commit").json()["failed"] == []

    written = vault.raw_after(note_id, stored["revisionDate"])
    sections = json.loads(written["notes"])
    assert sections["datatypes"][0]["pattern"] == "Media PC"
    assert sections["templates"][-1] == named
    assert [c.get("datatype") for c in sections["templates"][0]["coordinates"]] == [None] * 4
    ## a new login reads the section from the real note
    assert vault.app.login().status_code == 200
    vault.beat()
    listed = client.get("/api/datatypes").json()
    assert [d["id"] for d in listed["datatypes"]] == [datatype["id"]]
    assert listed["datatypes"][0]["broken"] is None
    assert "warnings" not in client.get("/api/templates").json()

    matches = client.get("/api/template-matches").json()
    media = vault.by_name("Media PC")["id"]
    other = vault.by_name("ThinkPad X1")["id"]
    assert matches[media]["scores"][-1]["filled"] == 3
    assert matches[media]["scores"][-1]["formats"][0]["score"] == 1
    assert matches[other]["scores"][-1]["total"] == 3
    assert 0 <= matches[other]["scores"][-1]["formats"][0]["score"] < 1
    assert matches[other]["scores"][-1]["filled"] < 3

    removed = changes(remove_datatype(datatype["id"]))
    refused = client.put("/api/datatypes", json=removed)
    assert refused.status_code == 422
    assert "Named" in refused.json()["detail"]
    assert client.put("/api/templates", json=changes(remove_template("Named"))).status_code == 202
    assert client.put("/api/datatypes", json=removed).status_code == 202
    vault.beat()
    assert client.post("/api/queue/commit").json()["failed"] == []
    cleared = vault.raw_after(note_id, written["revisionDate"])
    assert json.loads(cleared["notes"])["datatypes"] == []
