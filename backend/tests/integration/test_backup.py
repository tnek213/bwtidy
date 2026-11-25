"""A real backup of the seeded dev vault, decrypted and checked member by member.

Attachment contents are compared with the first lines of dev/attachments/*.txt (the dev
directory is not mounted in the app container). Nothing here prints an item.
"""

import base64
import io
import json
import zipfile
from pathlib import Path

import pytest
from support.app import App, Elsewhere
from support.cli import PASSWORD, BwCli

from bwtidy.backups.collect import encrypted_size
from bwtidy.backups.crypto import decrypt_backup
from bwtidy.vault.cache import list_items_settled
from bwtidy.vault.tool_note import TOOL_NOTE_NAME

pytestmark = pytest.mark.integration

FIRST_LINE = b"Made-up test file for the bwtidy dev vault.\n"
ORG_ATTACHMENTS = {
    "Home insurance": ("insurance-policy.txt", b"Home insurance policy 123-456"),
    "Youth laptop": ("youth-laptop-recovery.txt", b"Youth laptop recovery sheet"),
}
PERSONAL_ATTACHMENTS = {
    "Internal CA": ("ca-notes.txt", b"Lab CA notes"),
    "ThinkPad X1": ("thinkpad-recovery.txt", b"ThinkPad X1 BitLocker recovery"),
}
TRASH = {"Old forum account", "Expired license key"}
SEEDED_ATTACHMENTS = {**ORG_ATTACHMENTS, **PERSONAL_ATTACHMENTS}
## the dev account's orgs: how many items and which collections each holds
SEEDED_ORGS = {"Family": (10, {"Shared", "Youth"}), "Club": (1, {"Shared"})}


class Backup:
    def __init__(self, app: App, name: str) -> None:
        self.path: Path = app.data_dir / "backups" / name
        self.archive = zipfile.ZipFile(io.BytesIO(decrypt_backup(self.path, PASSWORD)))

    def json(self, member: str):
        return json.loads(self.archive.read(member))


@pytest.fixture(scope="module")
def created(app: App) -> dict:
    assert app.client.get("/api/backups").status_code == 401
    assert app.login().status_code == 200
    ## the login started a backup in the background, the one by hand comes after it
    app.settle()
    response = app.client.post("/api/backups")
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def backup(app: App, created: dict) -> Backup:
    return Backup(app, created["name"])


@pytest.fixture(scope="module")
def served_items(app: App) -> list[dict]:
    assert app.client.portal is not None
    return app.client.portal.call(app.session.client.list_items)


def by_name(items: list[dict]) -> dict[str, dict]:
    return {i["name"]: i for i in items}


def orgs_by_name(backup: Backup) -> dict[str, dict]:
    orgs = backup.json("raw/organizations.json")
    named = by_name(orgs)
    assert len(named) == len(orgs)
    return named


def test_the_backup_is_listed_and_named_by_utc_time(app: App, created: dict, backup: Backup):
    assert set(created) == {"name", "size", "time"}
    assert created["name"].endswith(".zip.age")
    assert created["size"] == backup.path.stat().st_size > 1000
    listed = app.client.get("/api/backups").json()
    assert created in listed
    ## the login's background backup and the one by hand, nothing else
    assert sorted(p.name for p in backup.path.parent.iterdir()) == sorted(b["name"] for b in listed)
    assert len(listed) == 2
    assert listed[0] == created
    assert backup.path.read_bytes().startswith(b"age-encryption.org/v1\n-> scrypt ")


def attachment_paths(items: list[dict]) -> list[str]:
    return [f"item-attachments/{i['id']}/{a['fileName']}" for i in items for a in i["attachments"]]


def test_every_member_is_there(backup: Backup, served_items: list[dict]):
    orgs = orgs_by_name(backup)
    assert set(orgs) == set(SEEDED_ORGS)
    attachments = attachment_paths(served_items) + attachment_paths(backup.json("raw/trash.json"))
    assert len(attachments) == 4
    assert sorted(backup.archive.namelist()) == sorted(
        [
            "RESTORE.md",
            "personal-export.zip",
            *(f"org-{org['id']}-export.json" for org in orgs.values()),
            *attachments,
            "raw/items.json",
            "raw/trash.json",
            "raw/folders.json",
            "raw/collections.json",
            "raw/organizations.json",
        ]
    )
    assert backup.archive.testzip() is None


def test_personal_export_is_the_import_format(backup: Backup):
    """The personal export stays as bw import takes it. Its attachments folders are not the
    source of attachments: they are filed by item name, which collides (see the spec)."""
    with zipfile.ZipFile(io.BytesIO(backup.archive.read("personal-export.zip"))) as export:
        names = export.namelist()
        assert "data.json" in names
        data = json.loads(export.read("data.json"))
        assert data["encrypted"] is False
        exported = by_name(data["items"])
        assert all(not i.get("organizationId") for i in data["items"])
        ## the export sees what bw serve wrote: the tool note created at login
        assert TOOL_NOTE_NAME in exported


@pytest.mark.parametrize("org_name", SEEDED_ORGS)
def test_org_export_holds_the_org_items(backup: Backup, served_items: list[dict], org_name: str):
    count, collections = SEEDED_ORGS[org_name]
    org = orgs_by_name(backup)[org_name]
    export = backup.json(f"org-{org['id']}-export.json")
    assert export["encrypted"] is False
    assert len(export["items"]) == count
    org_items = [i for i in served_items if i.get("organizationId") == org["id"]]
    assert {i["name"] for i in export["items"]} == {i["name"] for i in org_items}
    assert {c["name"] for c in export["collections"]} == collections


def test_every_attachment_has_the_right_bytes_under_item_attachments(backup: Backup):
    """Personal and organization attachments alike, by item id, whole, and nothing else."""
    raw = backup.json("raw/items.json")
    with_attachments = {i["name"]: i for i in raw if i.get("attachments")}
    assert set(with_attachments) == set(SEEDED_ATTACHMENTS)
    members = [m for m in backup.archive.namelist() if m.startswith("item-attachments/")]
    assert len(members) == 4
    for item_name, (file_name, phrase) in SEEDED_ATTACHMENTS.items():
        item = with_attachments[item_name]
        [attachment] = item["attachments"]
        assert attachment["fileName"] == file_name
        content = backup.archive.read(f"item-attachments/{item['id']}/{file_name}")
        assert content.startswith(FIRST_LINE)
        assert phrase in content
        assert encrypted_size(len(content)) == int(attachment["size"])


def test_two_items_of_the_same_name_each_keep_their_own_attachment(
    app: App, bw_cli: BwCli, tmp_path: Path
):
    """What the personal export cannot do: both are named Twin, both files are same.txt."""
    elsewhere = Elsewhere(app, bw_cli, tmp_path)
    try:
        first = elsewhere.note_with_attachment("Twin", "same.txt", b"one\n")
        second = elsewhere.note_with_attachment("Twin", "same.txt", b"two\n")
        backup = Backup(app, elsewhere.backup())
        raw = {i["id"]: i for i in backup.json("raw/items.json")}
        assert first in raw and second in raw
        assert raw[first]["name"] == raw[second]["name"] == "Twin"
        assert backup.archive.read(f"item-attachments/{first}/same.txt") == b"one\n"
        assert backup.archive.read(f"item-attachments/{second}/same.txt") == b"two\n"
    finally:
        elsewhere.clean_up()


def test_an_attachment_of_an_item_in_the_trash_is_backed_up(
    app: App, bw_cli: BwCli, tmp_path: Path
):
    elsewhere = Elsewhere(app, bw_cli, tmp_path)
    try:
        item_id = elsewhere.note_with_attachment("Binned", "codes.txt", b"from the trash\n")
        elsewhere.trash(item_id)
        backup = Backup(app, elsewhere.backup())
        listed_ids = {i["id"] for i in backup.json("raw/items.json")}
        assert item_id not in listed_ids
        [trashed] = [i for i in backup.json("raw/trash.json") if i["id"] == item_id]
        assert trashed["deletedDate"]
        assert [a["fileName"] for a in trashed["attachments"]] == ["codes.txt"]
        assert backup.archive.read(f"item-attachments/{item_id}/codes.txt") == b"from the trash\n"
    finally:
        elsewhere.clean_up()


def test_raw_items_equal_bw_serves_list(backup: Backup, served_items: list[dict]):
    raw = backup.json("raw/items.json")
    ## compared without assert ==, so a mismatch never prints an item
    same = raw == served_items
    assert same, "raw/items.json differs from bw serve's item list"
    names = {i["name"] for i in raw}
    assert len(names) == len(raw) == 79
    ## the tool note was created through bw serve at login, the backup's sync makes it show up
    assert TOOL_NOTE_NAME in names
    assert TRASH.isdisjoint(names)


def test_raw_trash_holds_the_two_trashed_items(backup: Backup):
    trash = backup.json("raw/trash.json")
    assert {i["name"] for i in trash} == TRASH
    assert all(i["deletedDate"] for i in trash)


def test_raw_folders_and_collections(backup: Backup):
    folders = {f["name"] for f in backup.json("raw/folders.json") if f["id"]}
    assert folders == {"Work", "Home", "Infra"}
    ## Family and Club each have a collection Shared
    collections = sorted(c["name"] for c in backup.json("raw/collections.json"))
    assert collections == ["Shared", "Shared", "Youth"]


def test_wrong_password_does_not_decrypt(backup: Backup):
    from bwtidy.backups.files import BackupError

    with pytest.raises(BackupError):
        decrypt_backup(backup.path, "not-the-master-password")


def test_second_backup_gets_its_own_name(app: App, created: dict):
    before = [b["name"] for b in app.client.get("/api/backups").json()]
    response = app.client.post("/api/backups")
    assert response.status_code == 201, response.text
    assert response.json()["name"] not in before
    names = [b["name"] for b in app.client.get("/api/backups").json()]
    assert names == sorted(names, reverse=True)
    assert set(names) == {*before, response.json()["name"]}
    assert not list(Path(app.data_dir / "backups").glob(".*"))


def test_two_writes_in_a_row_reuse_the_backup_taken_after_the_first(app: App):
    """The second write answers the backup made in the background after the first, and that
    backup holds the first write's value. The second write puts the original value back."""

    def beat() -> None:
        assert app.client.post("/api/session/heartbeat").status_code == 204

    def write(item: dict, notes: str | None) -> dict:
        beat()
        op = {
            "kind": "setLeaf",
            "itemId": item["id"],
            "revisionDate": item["revisionDate"],
            "column": "notes",
            "value": notes,
        }
        response = app.client.post("/api/operations", json={"operations": [op]})
        assert response.status_code == 200, response.text
        return response.json()

    def cached(item_id: str) -> dict:
        [item] = [i for i in app.session.cache.items if i["id"] == item_id]
        return item

    app.settle()
    note = cached(next(i["id"] for i in app.session.cache.items if i["name"] == "SIM PIN"))
    original_notes = note.get("notes")
    before = [b["name"] for b in app.client.get("/api/backups").json()]

    first = write(note, "written first")
    app.settle()
    beat()
    listed = [b["name"] for b in app.client.get("/api/backups").json()]
    [after_first] = [name for name in listed if name not in before]
    assert after_first != first["backup"]
    assert after_first > first["backup"]
    assert app.writes.db.backup_fingerprint(after_first) == app.backups.vault_fingerprint()

    second = write(cached(note["id"]), original_notes)
    assert second["backup"] == after_first
    app.settle()
    assert cached(note["id"]).get("notes") == original_notes

    saved = Backup(app, after_first)
    [item] = [i for i in saved.json("raw/items.json") if i["id"] == note["id"]]
    assert item["notes"] == "written first"
    assert item["revisionDate"] != note["revisionDate"]
    log = app.client.get("/api/change-log").json()
    assert [e["backup"] for e in log[:2]] == [after_first, first["backup"]]


def test_a_backup_right_after_a_change_elsewhere_holds_every_item(app: App, bw_cli: BwCli):
    """The real race: a sync that fetched a change makes bw serve list no items for a moment.

    Another device changes an item through the CLI with the app's own session key (never an
    unlock, that would invalidate the app's key), then a backup is taken at once. Three
    rounds, since the empty list does not show up every time. The item is changed back at
    the end so the suite can run again on the same stack.
    """
    key = app.session.session_key
    assert key

    def beat() -> None:
        assert app.client.post("/api/session/heartbeat").status_code == 204

    def edit_elsewhere(item_id: str, notes: str | None) -> str:
        bw_cli.run("sync", session=key)
        current = bw_cli.json("get", "item", item_id, session=key)
        edited = {**current, "notes": notes}
        encoded = base64.b64encode(json.dumps(edited).encode()).decode()
        bw_cli.run("edit", "item", item_id, encoded, session=key)
        return current["revisionDate"]

    assert app.client.portal is not None
    [note] = [i for i in app.session.cache.items if i["name"] == "SIM PIN"]
    original_notes = note.get("notes")
    try:
        for round_number in range(3):
            beat()
            before = edit_elsewhere(note["id"], f"edited elsewhere, round {round_number}")
            response = app.client.post("/api/backups")
            assert response.status_code == 201, response.text
            backup = Backup(app, response.json()["name"])
            raw = backup.json("raw/items.json")
            served = app.client.portal.call(list_items_settled, app.session.client)
            assert len(served) == 79
            assert len(raw) == len(served), f"round {round_number}: items missing from the backup"
            ## the sync fetched the change, which is what makes bw serve decrypt again
            [saved] = [i for i in raw if i["id"] == note["id"]]
            assert saved["revisionDate"] != before, f"round {round_number}: the sync got nothing"
            org = orgs_by_name(backup)["Family"]
            members = backup.archive.namelist()
            for item_name, (file_name, _) in SEEDED_ATTACHMENTS.items():
                [item] = [i for i in served if i["name"] == item_name]
                assert (item.get("organizationId") == org["id"]) == (item_name in ORG_ATTACHMENTS)
                assert f"item-attachments/{item['id']}/{file_name}" in members, (
                    f"round {round_number}: an attachment is missing"
                )
    finally:
        beat()
        edit_elsewhere(note["id"], original_notes)
        app.client.portal.call(app.session.client.sync)
        app.client.portal.call(app.session.cache.load)


def test_backup_needs_an_unlocked_vault(app: App):
    assert app.client.delete("/api/session").status_code == 204
    assert app.client.post("/api/backups").status_code == 401
