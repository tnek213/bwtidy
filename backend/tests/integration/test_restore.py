"""restoreItems against the seeded dev vault: every write kind, applied and then restored.

Each case applies one operation through POST /api/operations, restores its change log row
through GET /api/restore-plan and POST /api/operations, and checks that the item equals the
backup's version on every leaf except revisionDate and passwordHistory (bw adds history
entries for hidden fields a PUT cannot take away), with the same attachments (name and
bytes), and that the items the operation created are in the trash. One case trashes an item,
deletes it for good and restores it: it comes back under a new id with its attachments.
Another does the same with two items of the same name, each with its own same.txt.

The module undoes its changes at the end like test_writes.py. Two items end up under a
new id with the same values: Wiki (moved into the organization, which a restore cannot undo
in place) and ThinkPad X1 (deleted for good). Nothing here prints an item or a value.
"""

import base64
import json
from collections.abc import Awaitable, Callable, Iterator

import pytest
from support.app import App, wait_until_listed
from support.cli import BwCli
from support.restorer import Restorer
from support.vault import same, same_secret

from bwtidy.operations import SetLeaf

pytestmark = pytest.mark.integration

LOGIN, NOTE = 1, 2
Undo = Callable[[], Awaitable[None]]

TEMPLATES = {
    "version": 1,
    "templates": [
        {
            "name": "Login box",
            "type": LOGIN,
            "coordinates": [
                {"kind": "path", "path": "login.username"},
                {"kind": "path", "path": "login.password"},
            ],
        }
    ],
}


@pytest.fixture(scope="module")
def vault(app: App, bw_cli: BwCli) -> Iterator[Restorer]:
    assert app.login().status_code == 200
    app.settle()
    vault = Restorer(app, bw_cli)
    yield vault
    assert app.client.portal is not None
    vault.beat()
    vault.cli_run("sync")
    for undo in reversed(vault.undo):
        app.client.portal.call(undo)
    app.client.portal.call(vault.client.sync)
    app.client.portal.call(app.session.cache.load)


def test_set_leaf_on_an_item_with_a_passkey(vault: Restorer):
    forge = vault.raw(vault.by_name("Example Forge")["id"])
    assert len(forge["login"]["fido2Credentials"]) == 1
    row = vault.apply(vault.set_leaf(forge, "login.username", "restored@forge.example.test"))
    assert row["createdItemIds"] == []
    shown = vault.raw_after(forge["id"], forge["revisionDate"])
    assert shown["login"]["username"] == "restored@forge.example.test"
    vault.restore(row, ["put"])
    after = vault.check_same_id("setLeaf", row, forge["id"], {})
    assert after["login"]["username"] == forge["login"]["username"]
    passkey_kept = same(after["login"]["fido2Credentials"], forge["login"]["fido2Credentials"])
    assert passkey_kept, "the passkey changed"


def test_remove_leaf_of_a_hidden_field(vault: Restorer):
    desktop = vault.raw(vault.by_name("Gaming desktop")["id"])
    assert "Bitlocker RecoveryPassword" in [f["name"] for f in desktop["fields"]]
    row = vault.apply(vault.op("removeLeaf", desktop, column="fields.Bitlocker RecoveryPassword"))
    shown = vault.raw_after(desktop["id"], desktop["revisionDate"])
    assert "Bitlocker RecoveryPassword" not in [f["name"] for f in shown["fields"]]
    vault.restore(row, ["put"])
    after = vault.check_same_id("removeLeaf", row, desktop["id"], {})
    assert [f["name"] for f in after["fields"]] == [f["name"] for f in desktop["fields"]]
    value_kept = same_secret(after["fields"][1]["value"], desktop["fields"][1]["value"])
    assert value_kept, "the hidden field changed"


def test_move_to_folder(vault: Restorer):
    item = vault.raw(vault.by_name("Bank account")["id"])
    assert item.get("folderId") is None

    async def remove_folder() -> None:
        folder = next(f for f in await vault.client.list_folders() if f["name"] == "Restore me")
        vault.cli_run("delete", "folder", folder["id"])

    vault.undo.append(remove_folder)
    row = vault.apply(
        {
            "kind": "moveToFolder",
            "itemIds": [item["id"]],
            "revisionDates": {item["id"]: item["revisionDate"]},
            "folderName": "Restore me",
        }
    )
    assert vault.raw_after(item["id"], item["revisionDate"])["folderId"]
    vault.restore(row, ["put"])
    after = vault.check_same_id("moveToFolder", row, item["id"], {})
    assert after.get("folderId") is None


def test_restore_after_the_folder_was_deleted_makes_it_again_by_name(vault: Restorer):
    """The server answers "Invalid folder" to a folderId it no longer has."""
    item = vault.raw(vault.by_name("Office license key")["id"])
    assert item.get("folderId") is None
    vault.undo_put(item)

    async def remove_folders() -> None:
        for folder in await vault.client.list_folders():
            if folder["name"] == "Doomed":
                vault.cli_run("delete", "folder", folder["id"])

    vault.undo.append(remove_folders)
    vault.apply(
        {
            "kind": "moveToFolder",
            "itemIds": [item["id"]],
            "revisionDates": {item["id"]: item["revisionDate"]},
            "folderName": "Doomed",
        }
    )
    moved = vault.raw_after(item["id"], item["revisionDate"])
    doomed = moved["folderId"]
    assert doomed
    row = vault.apply(vault.set_leaf(moved, "notes", "changed while the folder was there"))
    changed = vault.raw_after(item["id"], moved["revisionDate"])
    vault.cli_run("delete", "folder", doomed)
    ## no sync by this CLI: it shares bw serve's state, whose own sync would then find nothing new
    ## and go on listing the deleted folder
    vault.beat()
    vault.call(vault.client.sync)
    vault.call(vault.app.session.cache.load)
    assert doomed not in {f["id"] for f in vault.app.session.cache.folders}

    vault.restore(row, ["put"])
    after = vault.raw_after(item["id"], changed["revisionDate"])
    assert same(after.get("notes"), moved.get("notes"))
    assert after["folderId"] not in (None, doomed)
    named = {f["id"]: f["name"] for f in vault.call(vault.client.list_folders) if f["id"]}
    assert named[after["folderId"]] == "Doomed"


def test_restore_after_a_collection_was_deleted_leaves_it_out(vault: Restorer):
    """The server answers "Invalid collection ID provided" to an id it no longer has."""
    item = vault.raw(vault.by_name("Home insurance")["id"])
    organization_id = item["organizationId"]
    vault.undo_put(item)
    body = {"organizationId": organization_id, "name": "Doomed", "groups": [], "users": []}
    encoded = base64.b64encode(json.dumps(body).encode()).decode()
    doomed = vault.cli("create", "org-collection", encoded, "--organizationid", organization_id)
    vault.beat()
    vault.call(vault.client.sync)
    vault.call(vault.app.session.cache.load)
    collections = vault.app.session.cache.collections
    names = [c["name"] for c in collections if c["id"] in item["collectionIds"]]
    vault.apply(vault.op("setCollections", item, collectionNames=[*names, "Doomed"]))
    moved = vault.raw_after(item["id"], item["revisionDate"])
    assert doomed["id"] in moved["collectionIds"]
    row = vault.apply(vault.set_leaf(moved, "notes", "changed while the collection was there"))
    changed = vault.raw_after(item["id"], moved["revisionDate"])
    vault.cli_run("delete", "org-collection", doomed["id"], "--organizationid", organization_id)
    vault.beat()
    vault.call(vault.client.sync)
    vault.call(vault.app.session.cache.load)

    vault.restore(row, ["put"])
    after = vault.raw_after(item["id"], changed["revisionDate"])
    assert same(after.get("notes"), moved.get("notes"))
    assert sorted(after["collectionIds"]) == sorted(item["collectionIds"])


def test_set_collections_of_an_org_item_with_an_attachment(vault: Restorer):
    item = vault.raw(vault.by_name("Home insurance")["id"])
    assert item["organizationId"]
    [attachment] = item["attachments"]
    attachments = vault.attachment_bytes(item)
    row = vault.apply(vault.op("setCollections", item, collectionNames=["Youth"]))
    shown = vault.raw_after(item["id"], item["revisionDate"])
    assert shown["collectionIds"] != item["collectionIds"]
    vault.restore(row, ["put"])
    after = vault.check_same_id("setCollections", row, item["id"], attachments)
    assert after["collectionIds"] == item["collectionIds"]
    assert after["attachments"][0]["id"] == attachment["id"]


def test_move_to_organization_comes_back_as_a_new_personal_item(vault: Restorer):
    wiki = vault.raw(vault.by_name("Wiki")["id"])
    assert not wiki.get("organizationId")
    assert wiki["favorite"]
    row = vault.apply(
        vault.op("moveToOrganization", wiki, organizationName="Family", collectionNames=["Shared"])
    )
    assert vault.raw_after(wiki["id"], wiki["revisionDate"])["organizationId"]
    ## the moved item stays in the trash for good: it cannot leave the organization
    vault.undo_delete_for_good([wiki["id"]])
    restored = vault.restore(row, ["recreate"])
    [new_id] = restored["createdItemIds"]
    assert new_id != wiki["id"]
    after = vault.check_new_id("moveToOrganization", row, wiki["id"], new_id, {})
    assert not after.get("organizationId")
    assert after["favorite"]
    assert after["folderId"] == wiki["folderId"]
    assert vault.in_trash(wiki["id"])
    assert vault.raw(wiki["id"])["deletedDate"]


def test_move_to_personal_untrashes_the_original_and_trashes_the_copy(vault: Restorer):
    laptop = vault.raw(vault.by_name("Youth laptop")["id"])
    assert laptop["organizationId"]
    attachments = vault.attachment_bytes(laptop)
    assert attachments
    row = vault.apply(vault.op("moveToPersonal", laptop))
    [new_id] = row["createdItemIds"]
    assert not vault.raw(new_id).get("organizationId")
    assert vault.in_trash(laptop["id"])
    vault.undo_delete_for_good([new_id])
    restored = vault.restore(row, ["untrash", "trash"])
    assert restored["createdItemIds"] == []
    after = vault.check_same_id("moveToPersonal", row, laptop["id"], attachments)
    assert after["organizationId"] == laptop["organizationId"]
    assert after["collectionIds"] == laptop["collectionIds"]
    assert not vault.in_trash(laptop["id"])
    assert vault.in_trash(new_id)


def test_trash_item(vault: Restorer):
    door = vault.raw(vault.by_name("Door code")["id"])
    row = vault.apply(vault.op("trashItem", door))
    assert vault.in_trash(door["id"])
    vault.restore(row, ["untrash"])
    vault.check_same_id("trashItem", row, door["id"], {})
    assert not vault.in_trash(door["id"])


def test_convert_item_with_an_attachment(vault: Restorer):
    ca = vault.raw(vault.by_name("Internal CA")["id"])
    assert ca["type"] == NOTE
    attachments = vault.attachment_bytes(ca)
    assert attachments
    row = vault.apply(vault.op("convertItem", ca, newType=LOGIN, mapping={}))
    [new_id] = row["createdItemIds"]
    assert vault.raw(new_id)["type"] == LOGIN
    assert vault.in_trash(ca["id"])
    vault.undo_delete_for_good([new_id])
    vault.restore(row, ["untrash", "trash"])
    after = vault.check_same_id("convertItem", row, ca["id"], attachments)
    assert after["type"] == NOTE
    assert vault.in_trash(new_id)


def test_apply_template_across_types(vault: Restorer):
    original = vault.raw(vault.by_name("bwtidy")["id"]).get("notes")
    assert isinstance(original, str)
    vault.set_templates(json.dumps(TEMPLATES))

    async def templates_back() -> None:
        ## no heartbeat here, this runs inside the portal where the test client cannot be used
        [cached] = [i for i in vault.app.session.cache.items if i["name"] == "bwtidy"]
        note = await vault.client.get_item(cached["id"])
        op = SetLeaf(
            kind="setLeaf",
            itemId=note["id"],
            revisionDate=note["revisionDate"],
            column="notes",
            value=original,
        )
        await vault.app.writes.apply([op], "ui", edits_tool_note=True)

    vault.undo.append(templates_back)
    media = vault.raw(vault.by_name("Media PC")["id"])
    assert media["type"] == NOTE
    row = vault.apply(
        vault.op(
            "applyTemplate",
            media,
            template="Login box",
            mapping={"login.username": "fields.user", "login.password": "fields.user password"},
        )
    )
    [new_id] = row["createdItemIds"]
    assert vault.raw(new_id)["type"] == LOGIN
    vault.undo_delete_for_good([new_id])
    vault.restore(row, ["untrash", "trash"])
    after = vault.check_same_id("applyTemplate", row, media["id"], {})
    assert [f["name"] for f in after["fields"]] == ["user", "user password", "dm-crypt password"]
    assert vault.in_trash(new_id)


def test_rename_field(vault: Restorer):
    switch = vault.raw(vault.by_name("Switch admin")["id"])
    row = vault.apply(
        {
            "kind": "renameField",
            "from": "username",
            "to": "login name",
            "items": [{"itemId": switch["id"], "revisionDate": switch["revisionDate"]}],
        }
    )
    shown = vault.raw_after(switch["id"], switch["revisionDate"])
    assert "login name" in [f["name"] for f in shown["fields"]]
    vault.restore(row, ["put"])
    after = vault.check_same_id("renameField", row, switch["id"], {})
    assert [f["name"] for f in after["fields"]] == ["username", "password"]


def test_merge_columns_into_a_built_in_slot(vault: Restorer):
    thinkpad = vault.raw(vault.by_name("ThinkPad X1")["id"])
    attachments = vault.attachment_bytes(thinkpad)
    assert attachments
    row = vault.apply(
        {
            "kind": "mergeColumns",
            "sources": ["fields.User"],
            "target": "login.username",
            "items": [{"itemId": thinkpad["id"], "revisionDate": thinkpad["revisionDate"]}],
            "resolutions": {thinkpad["id"]: "fields.User"},
        }
    )
    shown = vault.raw_after(thinkpad["id"], thinkpad["revisionDate"])
    assert "User" not in [f["name"] for f in shown["fields"]]
    vault.restore(row, ["put"])
    after = vault.check_same_id("mergeColumns", row, thinkpad["id"], attachments)
    assert [f["name"] for f in after["fields"]] == [f["name"] for f in thinkpad["fields"]]


def test_merge_items(vault: Restorer):
    sim = vault.raw(vault.by_name("SIM PIN")["id"])
    video = vault.raw(vault.by_name("Streaming video")["id"])
    assert sim.get("notes")
    row = vault.apply(
        {
            "kind": "mergeItems",
            "fromItemId": sim["id"],
            "fromRevisionDate": sim["revisionDate"],
            "toItemId": video["id"],
            "toRevisionDate": video["revisionDate"],
            "mapping": {"fields.SIM PIN": "notes"},
        }
    )
    assert row["itemIds"] == [sim["id"], video["id"]]
    assert vault.in_trash(sim["id"])
    shown = vault.raw_after(video["id"], video["revisionDate"])
    assert "SIM PIN" in [f["name"] for f in shown["fields"]]
    vault.restore(row, ["untrash", "put"])
    vault.check_same_id("mergeItems", row, sim["id"], {})
    after = vault.check_same_id("mergeItems", row, video["id"], {})
    assert not after.get("fields")
    assert not vault.in_trash(sim["id"])


def test_an_item_deleted_for_good_comes_back_with_its_attachments(vault: Restorer):
    thinkpad = vault.raw(vault.by_name("ThinkPad X1")["id"])
    ## a second attachment with dots before the suffix, which the personal export mangles
    vault.call(vault.client.upload_attachment, thinkpad["id"], "my.notes.v2.txt", b"dotted name")
    vault.call(vault.client.sync)
    vault.call(vault.app.session.cache.load)
    thinkpad = vault.raw_after(thinkpad["id"], thinkpad["revisionDate"])
    attachments = vault.attachment_bytes(thinkpad)
    assert sorted(attachments) == ["my.notes.v2.txt", "thinkpad-recovery.txt"]
    row = vault.apply(vault.op("trashItem", thinkpad))
    ## the backup of the trash was taken with the attachment in place
    saved = vault.backup_item(row["backup"], thinkpad["id"])
    assert len(saved["attachments"]) == 2
    vault.cli_run("delete", "item", thinkpad["id"], "--permanent")
    vault.call(vault.client.sync)
    vault.call(vault.app.session.cache.load)
    assert thinkpad["id"] not in {i["id"] for i in vault.items()}
    assert not vault.in_trash(thinkpad["id"])

    restored = vault.restore(row, ["create"])
    [new_id] = restored["createdItemIds"]
    after = vault.check_new_id("permanent delete", row, thinkpad["id"], new_id, attachments)
    assert after["name"] == "ThinkPad X1"
    assert after["folderId"] == thinkpad["folderId"]
    assert [f["name"] for f in after["fields"]] == [f["name"] for f in thinkpad["fields"]]
    assert not after.get("deletedDate")
    ## the copy stays as the seed's ThinkPad X1, without the extra attachment
    [extra] = [a for a in after["attachments"] if a["fileName"] == "my.notes.v2.txt"]

    async def drop_extra() -> None:
        vault.cli_run("delete", "attachment", extra["id"], "--itemid", new_id)

    vault.undo.append(drop_extra)


def test_two_items_of_the_same_name_deleted_for_good_each_get_their_own_file(
    vault: Restorer,
):
    """Both are named Restore twin and carry same.txt with other bytes. The personal export
    files both under one name, the restore takes them from the backup by item id."""
    made: list[str] = []

    async def delete_made() -> None:
        present = {i["id"] for i in await vault.client.list_items()}
        present |= {i["id"] for i in await vault.client.list_trash()}
        for item_id in made:
            if item_id in present:
                vault.cli_run("delete", "item", item_id, "--permanent")

    vault.undo.append(delete_made)
    twins = []
    for content in (b"first twin\n", b"second twin\n"):
        item = {
            "type": NOTE,
            "name": "Restore twin",
            "notes": "made by test_restore",
            "secureNote": {"type": 0},
        }
        vault.beat()
        item_id = vault.call(vault.client.create_item, item)["id"]
        made.append(item_id)
        wait_until_listed(vault.app, item_id)
        vault.call(vault.client.upload_attachment, item_id, "same.txt", content)
        twins.append((item_id, content))
    vault.call(vault.client.sync)
    vault.call(vault.app.session.cache.load)

    rows = []
    for item_id, _ in twins:
        row = vault.apply(vault.op("trashItem", vault.raw(item_id)))
        assert row["itemIds"] == [item_id]
        rows.append(row)
    for item_id, _ in twins:
        vault.cli_run("delete", "item", item_id, "--permanent")
    vault.call(vault.client.sync)
    vault.call(vault.app.session.cache.load)
    for item_id, _ in twins:
        assert item_id not in {i["id"] for i in vault.items()}
        assert not vault.in_trash(item_id)

    for row, (item_id, content) in zip(rows, twins, strict=True):
        restored = vault.restore(row, ["create"])
        [new_id] = restored["createdItemIds"]
        made.append(new_id)
        after = vault.check_new_id("same name", row, item_id, new_id, {"same.txt": content})
        assert after["name"] == "Restore twin"
        assert [a["fileName"] for a in after["attachments"]] == ["same.txt"]
