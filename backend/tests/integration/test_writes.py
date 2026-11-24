"""Writes against the seeded dev vault through a real bw serve.

What only a real server can tell: a full PUT keeps passkeys, password history and
attachments, a change made elsewhere is caught by the revisionDate check, a type change keeps
organization, collections and attachment bytes, a move out of an organization keeps the values
and the attachment bytes. The CLI runs with the app's own session key
and is never unlocked here, because a CLI unlock invalidates the running app's key. Only the
other device, a CLI with its own state, unlocks.

Every write is undone at the end of the module (PUT of the original item, restore from the
trash, permanent delete of a created item), so the other integration tests see the seeded
vault again. A test that fails midway can leave the vault changed, scripts/dev-vault reset
gives a fresh one. Nothing here prints an item or a value.
"""

import asyncio
import base64
import json
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from support.app import App
from support.cli import BwCli
from support.vault import Vault, attachment_ids, same, same_secret

from tests.support.quiet import assert_absent

pytestmark = pytest.mark.integration

LOGIN, NOTE = 1, 2
HIDDEN = 1
FIRST_LINE = b"Made-up test file for the bwtidy dev vault.\n"


@pytest.fixture(scope="module")
def vault(app: App, bw_cli: BwCli) -> Iterator[Vault]:
    assert app.login().status_code == 200
    app.settle()
    vault = Vault(app, bw_cli)
    yield vault
    assert app.client.portal is not None
    vault.beat()
    vault.cli_run("sync")
    for undo in reversed(vault.undo):
        app.client.portal.call(undo)
    app.client.portal.call(vault.client.sync)
    app.client.portal.call(app.session.cache.load)


def test_full_put_keeps_passkeys_history_and_attachments(vault: Vault):
    forge = vault.raw(vault.by_name("Example Forge")["id"])
    mail = vault.raw(vault.by_name("Example Mail")["id"])
    thinkpad = vault.raw(vault.by_name("ThinkPad X1")["id"])
    assert len(forge["login"]["fido2Credentials"]) == 1
    assert len(mail["passwordHistory"]) == 2
    assert len(thinkpad["attachments"]) == 1
    for original in (forge, mail, thinkpad):
        vault.undo_put(original)
    before = vault.backups()

    response = vault.post(
        vault.set_leaf(forge, "login.username", "renamed@forge.example.test"),
        vault.set_leaf(mail, "notes", "Edited through the write path"),
        vault.set_leaf(thinkpad, "fields.User", "user2"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] == 3
    ## the write reused the newest backup, taken after the login while nothing changed, and
    ## left a backup of the vault after the write behind
    assert body["backup"] == before[0]
    [after] = [b for b in vault.backups() if b not in before]
    assert vault.backups() == sorted([*before, after], reverse=True)
    assert (vault.app.data_dir / "backups" / body["backup"]).is_file()
    assert vault.app.writes.db.backup_fingerprint(after) == vault.app.backups.vault_fingerprint()

    forge_after = after = vault.raw_after(forge["id"], forge["revisionDate"])
    assert after["revisionDate"] != forge["revisionDate"]
    assert after["login"]["username"] == "renamed@forge.example.test"
    passkey_kept = same(after["login"]["fido2Credentials"], forge["login"]["fido2Credentials"])
    assert passkey_kept, "the passkey changed"
    password_kept = same_secret(after["login"]["password"], forge["login"]["password"])
    assert password_kept, "the login password changed"
    history_kept = same(after.get("passwordHistory"), forge.get("passwordHistory"))
    assert history_kept, "history changed"

    after = vault.raw_after(mail["id"], mail["revisionDate"])
    assert after["notes"] == "Edited through the write path"
    history_kept = same(after["passwordHistory"], mail["passwordHistory"])
    assert history_kept, "the password history changed"
    password_kept = same_secret(after["login"]["password"], mail["login"]["password"])
    assert password_kept, "the login password changed"
    assert after["login"].get("passwordRevisionDate") == mail["login"].get("passwordRevisionDate")

    after = vault.raw_after(thinkpad["id"], thinkpad["revisionDate"])
    assert [f["name"] for f in after["fields"]] == [f["name"] for f in thinkpad["fields"]]
    assert next(f["value"] for f in after["fields"] if f["name"] == "User") == "user2"
    assert attachment_ids(after) == attachment_ids(thinkpad)
    login_kept = same(after["login"], thinkpad["login"])
    assert login_kept, "the login changed"
    ## the grid sees the change, with the new revisionDate
    rows = vault.app.client.get("/api/grid").json()["rows"]
    row = next(r for r in rows if r["id"] == forge["id"])
    assert row["cells"]["login.username"] == "renamed@forge.example.test"
    assert row["revisionDate"] == forge_after["revisionDate"]
    log = vault.change_log()
    assert [e["operationKind"] for e in log[:3]] == ["setLeaf", "setLeaf", "setLeaf"]
    assert all(e["result"] == "ok" and e["backup"] == body["backup"] for e in log[:3])


def test_a_change_made_elsewhere_is_a_conflict(vault: Vault):
    note = vault.raw(vault.by_name("SIM PIN")["id"])
    vault.undo_put(note)
    stale = vault.set_leaf(note, "notes", "typed in the grid")
    backups = vault.backups()
    log_size = len(vault.change_log())

    ## another device: the CLI edits the same item, with the app's own session key
    vault.cli_run("sync")
    edited = {**vault.cli("get", "item", note["id"]), "notes": "edited elsewhere"}
    encoded = base64.b64encode(json.dumps(edited).encode()).decode()
    vault.cli_run("edit", "item", note["id"], encoded)

    response = vault.post(stale)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["detail"] == "1 item changed since the grid was loaded, nothing was written"
    [conflict] = body["conflicts"]
    assert conflict["itemId"] == note["id"]
    assert conflict["expected"] == note["revisionDate"]
    assert conflict["actual"] not in (None, note["revisionDate"])
    current = vault.raw_after(note["id"], note["revisionDate"])
    assert current["notes"] == "edited elsewhere"
    assert current["revisionDate"] == conflict["actual"]
    assert vault.backups() == backups
    assert len(vault.change_log()) == log_size
    ## the cache was reloaded, so the grid now carries the new revisionDate
    assert vault.by_name("SIM PIN")["revisionDate"] == conflict["actual"]
    ## with the fresh revisionDate the same edit goes through
    response = vault.post(vault.set_leaf(current, "notes", "typed in the grid"))
    assert response.status_code == 200, response.text
    assert vault.raw_after(note["id"], current["revisionDate"])["notes"] == "typed in the grid"


@pytest.fixture
def other_device() -> Iterator[tuple[BwCli, str]]:
    """bw logged in as another device, with its own CLI state and session key."""
    appdata = tempfile.mkdtemp(prefix="bw-device.")
    try:
        cli = BwCli(appdata=appdata)
        cli.ensure_logged_in()
        try:
            yield cli, cli.unlock()
        finally:
            cli.run("logout")
    finally:
        shutil.rmtree(appdata)


def test_a_write_vaultwarden_refuses_as_out_of_date_is_a_conflict(
    vault: Vault, other_device: tuple[BwCli, str], monkeypatch: pytest.MonkeyPatch
):
    note = vault.raw(vault.by_name("Alarm system")["id"])
    vault.undo_put(note)
    cli, key = other_device
    real_put = vault.client.put_item

    def edit_elsewhere() -> None:
        cli.run("sync", session=key)
        edited = {**cli.json("get", "item", note["id"], session=key), "notes": "edited elsewhere"}
        encoded = base64.b64encode(json.dumps(edited).encode()).decode()
        cli.run("edit", "item", note["id"], encoded, session=key)

    ## the other device edits the item after the write path's last sync, right before its PUT
    async def put_item(item_id: str, obj: dict[str, Any]) -> dict[str, Any]:
        if item_id == note["id"]:
            await asyncio.to_thread(edit_elsewhere)
        return await real_put(item_id, obj)

    monkeypatch.setattr(vault.client, "put_item", put_item)
    response = vault.post(vault.set_leaf(note, "notes", "typed in the grid"))
    monkeypatch.undo()
    assert response.status_code == 500, response.text
    body = response.json()
    assert (body["applied"], body["failed"], body["pending"]) == ([], 0, [])
    [conflict] = body["conflicts"]
    assert conflict["itemId"] == note["id"]
    assert conflict["expected"] == note["revisionDate"]
    assert conflict["actual"] not in (None, note["revisionDate"])
    current = vault.raw_after(note["id"], note["revisionDate"])
    assert current["notes"] == "edited elsewhere"
    assert current["revisionDate"] == conflict["actual"]
    assert vault.by_name("Alarm system")["revisionDate"] == conflict["actual"]
    assert vault.change_log()[0]["result"] == "failed"


def test_a_collections_change_made_elsewhere_is_a_conflict(
    vault: Vault, other_device: tuple[BwCli, str]
):
    """Vaultwarden changes an item's collections without a new revisionDate, so only the
    collections the grid showed tell another device's change (docs/spec.md, Writes)."""
    mail = vault.raw(vault.by_name("Youth mail")["id"])
    ids = {
        c["name"]: c["id"]
        for c in vault.app.session.cache.collections
        if c["organizationId"] == mail["organizationId"]
    }
    assert mail["collectionIds"] == [ids["Youth"]]
    backups = vault.backups()
    log_size = len(vault.change_log())
    cli, key = other_device

    def collections_elsewhere(collection_ids: list[str]) -> None:
        cli.run("sync", session=key)
        encoded = base64.b64encode(json.dumps(collection_ids).encode()).decode()
        cli.run("edit", "item-collections", mail["id"], encoded, session=key)

    collections_elsewhere([ids["Youth"], ids["Shared"]])
    try:
        response = vault.post(
            vault.op(
                "setCollections",
                mail,
                collectionNames=["Shared"],
                shownCollectionIds=[ids["Youth"]],
            )
        )
        assert response.status_code == 409, response.text
        [conflict] = response.json()["conflicts"]
        assert (conflict["itemId"], conflict["column"]) == (mail["id"], "collections")
        assert conflict["expected"] == mail["revisionDate"]
        ## the other device's choice stays and nothing was written
        current = vault.raw(mail["id"])
        assert sorted(current["collectionIds"]) == sorted([ids["Youth"], ids["Shared"]])
        assert vault.backups() == backups
        assert len(vault.change_log()) == log_size
    finally:
        collections_elsewhere([ids["Youth"]])


def test_convert_a_note_that_is_a_login_into_a_login(vault: Vault):
    old = vault.raw(vault.by_name("Switch admin")["id"])
    assert old["type"] == NOTE
    assert [(f["name"], f["type"]) for f in old["fields"]] == [("username", 0), ("password", 1)]
    assert old["folderId"]
    created: list[str] = []
    vault.undo_convert(old["id"], created)

    response = vault.post(
        vault.op(
            "convertItem",
            old,
            newType=LOGIN,
            mapping={"login.username": "fields.username", "login.password": "fields.password"},
        )
    )
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 1

    new = vault.by_name("Switch admin")
    created.append(new["id"])
    assert new["id"] != old["id"]
    new = vault.raw(new["id"])
    assert new["type"] == LOGIN
    assert new["login"]["username"] == old["fields"][0]["value"]
    password_moved = same_secret(new["login"]["password"], old["fields"][1]["value"])
    assert password_moved, "the login password is not the old field's value"
    assert new["login"].get("uris", []) == []
    assert new["login"].get("totp") is None
    assert new.get("fields", []) == []
    assert new["folderId"] == old["folderId"]
    assert new.get("notes") == old.get("notes")
    assert new["reprompt"] == old["reprompt"]
    assert new["favorite"] == old["favorite"]
    assert new.get("organizationId") is None
    assert new.get("deletedDate") is None

    trashed = vault.raw_after(old["id"], old["revisionDate"])
    assert trashed["deletedDate"]
    assert old["id"] not in {i["id"] for i in vault.items()}
    assert old["id"] in {i["id"] for i in vault.cli("list", "items", "--trash")}
    ## the trashed item still holds its values, the backup and the trash are the way back
    assert [f["name"] for f in trashed["fields"]] == ["username", "password"]


def test_convert_an_org_item_keeps_organization_collections_and_attachment(vault: Vault):
    old = vault.raw(vault.by_name("Youth laptop")["id"])
    assert old["type"] == NOTE
    assert old["organizationId"]
    assert len(old["collectionIds"]) == 1
    [attachment] = old["attachments"]
    content = vault.call(vault.client.get_attachment, attachment["id"], old["id"])
    assert content.startswith(FIRST_LINE)
    assert b"Youth laptop recovery sheet" in content
    created: list[str] = []
    vault.undo_convert(old["id"], created)

    response = vault.post(
        vault.op(
            "convertItem",
            old,
            newType=LOGIN,
            mapping={"login.username": "fields.User", "login.password": "fields.password"},
        )
    )
    assert response.status_code == 200, response.text

    new = vault.by_name("Youth laptop")
    created.append(new["id"])
    assert new["id"] != old["id"]
    new = vault.raw(new["id"])
    assert new["type"] == LOGIN
    assert new["organizationId"] == old["organizationId"]
    assert new["collectionIds"] == old["collectionIds"]
    assert new["login"]["username"] == old["fields"][0]["value"]
    password_moved = same_secret(new["login"]["password"], old["fields"][1]["value"])
    assert password_moved, "the login password is not the old field's value"
    assert [(f["name"], f["type"]) for f in new["fields"]] == [("Bitlocker recovery key", HIDDEN)]
    key_moved = same_secret(new["fields"][0]["value"], old["fields"][2]["value"])
    assert key_moved, "the recovery key lost the value"
    [copied] = new["attachments"]
    assert copied["id"] != attachment["id"]
    assert copied["fileName"] == attachment["fileName"]
    copied_content = vault.call(vault.client.get_attachment, copied["id"], new["id"])
    assert copied_content == content
    assert vault.raw_after(old["id"], old["revisionDate"])["deletedDate"]
    assert old["id"] in {i["id"] for i in vault.cli("list", "items", "--trash")}
    ## the grid shows the new item where the old one was
    rows = vault.app.client.get("/api/grid").json()["rows"]
    [row] = [r for r in rows if r["cells"].get("name") == "Youth laptop"]
    assert row["id"] == new["id"]
    assert row["cells"]["organization"] == "Family"
    assert row["cells"]["collections"] == ["Youth"]
    assert row["cells"]["attachments"] == ["youth-laptop-recovery.txt"]
    assert row["cells"]["type"] == "login"


def test_move_an_org_item_to_personal_keeps_values_and_attachment(vault: Vault):
    old = vault.raw(vault.by_name("Youth laptop")["id"])
    assert old["organizationId"]
    assert old["collectionIds"]
    [attachment] = old["attachments"]
    content = vault.call(vault.client.get_attachment, attachment["id"], old["id"])
    created: list[str] = []
    vault.undo_convert(old["id"], created)

    response = vault.post(vault.op("moveToPersonal", old))
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 1

    new = vault.by_name("Youth laptop")
    created.append(new["id"])
    assert new["id"] != old["id"]
    new = vault.raw(new["id"])
    assert new.get("organizationId") is None
    assert not new.get("collectionIds")
    assert new.get("deletedDate") is None
    assert new["type"] == old["type"]
    assert new.get("folderId") == old.get("folderId")
    assert new.get("notes") == old.get("notes")
    assert (new["favorite"], new["reprompt"]) == (old["favorite"], old["reprompt"])
    fields_kept = same(new.get("fields"), old.get("fields"))
    assert fields_kept, "the fields changed"
    for group in ("login", "secureNote", "card", "identity", "sshKey"):
        if old.get(group) is not None:
            group_kept = same(new.get(group), old.get(group))
            assert group_kept, f"{group} changed"
    [copied] = new["attachments"]
    assert copied["id"] != attachment["id"]
    assert copied["fileName"] == attachment["fileName"]
    copied_content = vault.call(vault.client.get_attachment, copied["id"], new["id"])
    assert copied_content == content, "the attachment bytes changed"
    trashed = vault.raw_after(old["id"], old["revisionDate"])
    assert trashed["deletedDate"]
    assert trashed["organizationId"] == old["organizationId"]
    assert old["id"] in {i["id"] for i in vault.cli("list", "items", "--trash")}
    rows = vault.app.client.get("/api/grid").json()["rows"]
    [row] = [r for r in rows if r["cells"].get("name") == "Youth laptop"]
    assert row["id"] == new["id"]
    assert row["cells"].get("organization") is None
    assert row["cells"].get("collections") is None
    assert row["cells"]["attachments"] == ["youth-laptop-recovery.txt"]
    assert vault.change_log()[0]["operationKind"] == "moveToPersonal"


def test_move_to_a_new_folder_creates_it(vault: Vault):
    item = vault.raw(vault.by_name("Bank account")["id"])
    assert item.get("folderId") is None
    folder_names = {f["name"] for f in vault.call(vault.client.list_folders)}
    assert "Archive" not in folder_names
    vault.undo_put(item)

    async def remove_folder() -> None:
        folder = next(f for f in await vault.client.list_folders() if f["name"] == "Archive")
        vault.cli_run("delete", "folder", folder["id"])

    vault.undo.append(remove_folder)

    response = vault.post(
        {
            "kind": "moveToFolder",
            "itemIds": [item["id"]],
            "revisionDates": {item["id"]: item["revisionDate"]},
            "folderName": "Archive",
        }
    )
    assert response.status_code == 200, response.text
    folders = {f["name"]: f["id"] for f in vault.call(vault.client.list_folders) if f["id"]}
    assert "Archive" in folders
    assert vault.raw_after(item["id"], item["revisionDate"])["folderId"] == folders["Archive"]
    assert vault.by_name("Bank account")["folderId"] == folders["Archive"]
    rows = vault.app.client.get("/api/grid").json()["rows"]
    [row] = [r for r in rows if r["id"] == item["id"]]
    assert row["cells"]["folder"] == "Archive"


def test_trash_item_moves_it_to_the_trash(vault: Vault):
    item = vault.by_name("Door code")

    async def restore() -> None:
        vault.cli_run("restore", "item", item["id"])

    vault.undo.append(restore)
    assert "Door code" not in {i["name"] for i in vault.cli("list", "items", "--trash")}
    response = vault.post(vault.op("trashItem", item))
    assert response.status_code == 200, response.text
    assert item["id"] not in {i["id"] for i in vault.items()}
    trash = vault.cli("list", "items", "--trash")
    assert item["id"] in {i["id"] for i in trash}
    assert {"Old forum account", "Expired license key"} <= {i["name"] for i in trash}
    assert vault.raw_after(item["id"], item["revisionDate"])["deletedDate"]
    assert vault.change_log()[0]["operationKind"] == "trashItem"
    assert vault.change_log()[0]["itemIds"] == [item["id"]]


def test_a_secret_into_notes_is_refused(vault: Vault):
    item = vault.raw(vault.by_name("Backup disk")["id"])
    [field] = item["fields"]
    assert field["type"] == HIDDEN
    backups = vault.backups()
    log_size = len(vault.change_log())

    response = vault.post(vault.set_leaf(item, "notes", field["value"]))
    assert response.status_code == 422, response.status_code
    detail = response.json()["detail"]
    assert "secret" in detail
    assert "notes" in detail
    assert_absent(field["value"], response.text, "the response")
    assert vault.raw(item["id"])["revisionDate"] == item["revisionDate"]
    assert vault.backups() == backups
    assert len(vault.change_log()) == log_size


def folders_until(vault: Vault, shown: Callable[[dict[str, str]], bool]) -> dict[str, str]:
    """The folders by id once shown(folders) holds, the last answer after 5 seconds: right
    after an own write bw serve can list a folder as it was before it for a moment."""
    deadline = time.monotonic() + 5
    while True:
        vault.beat()
        listed = vault.call(vault.client.list_folders)
        folders = {f["id"]: f["name"] for f in listed if f.get("id")}
        if shown(folders) or time.monotonic() > deadline:
            return folders
        time.sleep(0.2)


def item_states(vault: Vault) -> dict[str, tuple[str, str | None]]:
    """Every item's revisionDate and folder, the trash included."""
    items = vault.call(vault.client.list_items) + vault.call(vault.client.list_trash)
    return {i["id"]: (i["revisionDate"], i.get("folderId")) for i in items}


def restore_row(vault: Vault, row: dict[str, Any]) -> dict[str, Any]:
    """Restore a change log row the way the UI does: its plan, then its operation."""
    plan = vault.app.client.get("/api/restore-plan", params={"changeLogId": row["id"]})
    assert plan.status_code == 200, plan.text
    response = vault.post(plan.json()["operation"])
    assert response.status_code == 200, response.text
    return plan.json()


def test_a_folder_rename_changes_no_item_and_its_restore_renames_it_back(vault: Vault):
    folders = {name: i for i, name in folders_until(vault, bool).items()}
    folder_id = folders["Infra"]

    async def rename_back() -> None:
        listed = {f["id"]: f["name"] for f in await vault.client.list_folders() if f.get("id")}
        if listed.get(folder_id) != "Infra":
            await vault.client.edit_folder(folder_id, "Infra")

    vault.undo.append(rename_back)
    before = item_states(vault)
    assert any(state[1] == folder_id for state in before.values())

    op = {"kind": "renameFolder", "folderId": folder_id, "shownName": "Infra", "newName": "Servers"}
    response = vault.post(op)
    assert response.status_code == 200, response.text
    assert folders_until(vault, lambda f: f.get(folder_id) == "Servers")[folder_id] == "Servers"
    assert item_states(vault) == before
    row = vault.change_log()[0]
    assert (row["operationKind"], row["folderId"], row["itemIds"]) == (
        "renameFolder",
        folder_id,
        [],
    )
    grid = vault.app.client.get("/api/grid").json()
    assert {"Servers"} <= {r["cells"].get("folder") for r in grid["rows"]}

    stale = vault.post({**op, "newName": "Hosts"})
    assert stale.status_code == 409, stale.text

    plan = restore_row(vault, row)
    assert plan["folder"]["action"] == "renameBack"
    assert folders_until(vault, lambda f: f.get(folder_id) == "Infra")[folder_id] == "Infra"
    assert item_states(vault) == before
    assert vault.change_log()[0]["operationKind"] == "restoreFolder"


def test_an_empty_folder_is_deleted_without_changing_an_item_and_its_restore_creates_it(
    vault: Vault,
):
    async def remove_spare() -> None:
        for folder in await vault.client.list_folders():
            if folder.get("name") == "Spare":
                vault.cli_run("delete", "folder", folder["id"])

    vault.undo.append(remove_spare)
    vault.call(vault.client.create_folder, "Spare")
    vault.call(vault.client.sync)
    vault.call(vault.app.session.cache.load)
    [spare] = [
        i
        for i, name in folders_until(vault, lambda f: "Spare" in f.values()).items()
        if name == "Spare"
    ]
    work = next(i for i, name in folders_until(vault, bool).items() if name == "Work")
    before = item_states(vault)

    held = vault.post({"kind": "deleteFolder", "folderId": work, "shownName": "Work"})
    assert held.status_code == 422, held.text
    assert "move them out first" in held.json()["detail"]

    response = vault.post({"kind": "deleteFolder", "folderId": spare, "shownName": "Spare"})
    assert response.status_code == 200, response.text
    assert spare not in folders_until(vault, lambda f: spare not in f)
    assert item_states(vault) == before
    row = vault.change_log()[0]
    assert (row["operationKind"], row["folderId"]) == ("deleteFolder", spare)

    plan = restore_row(vault, row)
    assert plan["folder"]["action"] == "createAgain"
    folders = folders_until(vault, lambda f: "Spare" in f.values())
    [again] = [i for i, name in folders.items() if name == "Spare"]
    assert again != spare
    assert vault.change_log()[0]["folderId"] == again
    assert item_states(vault) == before
