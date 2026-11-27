"""A backup of the seeded dev vault imported into the empty restore account.

docs/restore.md (RESTORE.md in every backup) says how to bring a lost vault back from a
backup: import the personal export and every organization export (each into the organization
of the same name), then find each backup item's imported item by its content and upload its
attachments. This test does those steps with the CLI as restore@bwtidy.test (a CLI state of
its own, so the app's login stays as it is): the imports itself, the attachments by running
the document's program attach.py as it stands in the worktree (/app/docs/restore.md). Then it
finds each backup item's imported item with a matching of its own and compares what arrived
with the backup's raw/items.json. The restore account is emptied again before and after, so
the test runs again on the same stack.

Two personal notes named Twin, each with an attachment same.txt of other bytes, two notes
named Org twin equal in every compared leaf, one in Family's collection Shared and one in
Club's, each with an attachment codes.txt of other bytes, and a note in the trash with an
attachment are made in the dev vault for the backup and deleted for good right after it. So
are values the seeded vault lacks: an empty folder, a folder Work/Clients, an empty collection,
a login with reprompt, a boolean and a linked field and a URI match, a Family note in a folder,
one marked favorite (both per user, so no organization export holds them) and one in two
collections. So are three archived notes with an attachment each: a personal one, a Family
one and one in the trash.

Nothing here prints an item or a value: a mismatch names items and keys only.
"""

import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from support.app import App, Elsewhere
from support.cli import BW_TIMEOUT, PASSWORD, BwCli

from bwtidy.backups.collect import RESTORE_DOC, attachment_members
from bwtidy.backups.crypto import decrypt_backup
from tests.support.restore_doc import attach_program

pytestmark = pytest.mark.integration

## dev/README.md, Credentials: registered by scripts/dev-vault setup with an empty vault
RESTORE_EMAIL = "restore@bwtidy.test"
## dev/dev-vault.md, The fixture: both have a collection Shared
ORG_TWINS = {"Family": b"family\n", "Club": b"club\n"}

TWINS = {"first twin": b"one\n", "second twin": b"two\n"}
SEEDED_ITEMS = 79
SEEDED_ATTACHMENTS = 4
## the twins, the org twins, the four below and the two archived notes that are not in the
## trash (in raw/archived.json): the trashed notes are in neither file
MADE_ITEMS = 10
## one each for the twins, the org twins and those two archived notes
MADE_ATTACHMENTS = 6
## archived notes made for the backup, by their notes: name, file name and bytes
ARCHIVED = {
    "shelved": ("Shelved", "shelved.txt", b"archived\n"),
    "org shelved": ("Org shelved", "org-shelved.txt", b"archived in Family\n"),
    "shelved and binned": ("Shelved and binned", "binned.txt", b"archived in the trash\n"),
}
EMPTY = "Empty"
CLIENTS = "Work/Clients"
## in the folder CLIENTS
PROBE = {
    "type": 1,
    "name": "Probe login",
    "notes": "probe",
    "reprompt": 1,
    "login": {"username": "probe", "uris": [{"uri": "https://bwtidy.example", "match": 3}]},
    "fields": [
        {"name": "remember", "value": "true", "type": 2},
        ## linked to the login's username
        {"name": "user", "value": None, "type": 3, "linkedId": 100},
    ],
}


class Placed(NamedTuple):
    """Where a Family item made for the backup is: its folder, favorite and collections."""

    folder: str | None = None
    favorite: bool = False
    collections: tuple[str, ...] = ("Shared",)


FAMILY_MADE = {
    "Org in folder": Placed(folder=CLIENTS),
    "Org favorite": Placed(favorite=True),
    "Org in two": Placed(collections=("Shared", "Youth")),
}

Item = dict[str, Any]

## what an import makes anew or what the key compares by name instead (docs/restore.md, attach.py)
NOT_COMPARED = {
    "id",
    "revisionDate",
    "creationDate",
    "deletedDate",
    "archivedDate",
    "organizationId",
    "collectionIds",
    "folderId",
    "attachments",
}
## docs/restore.md, the trash: what bw import refuses or would take over from a trashed item
TRASH_DROPPED = ("id", "deletedDate", "folderId", "organizationId", "collectionIds")
## what attach.py prints for an attachment it uploaded, and for one it could not
UPLOADED = " is on item "
NOT_UPLOADED = " is not uploaded"
## what it prints for an organization item whose folder or favorite it set, and for one whose
## folder name the account lacks
SET = " has the folder and favorite of backup item "
NO_FOLDER = " stays in its folder"


def leaves(value: Any, path: str = "") -> dict[str, Any]:
    """Every leaf below value by its path, null leaves left out.

    bw serve's listing (raw/items.json) leaves out null keys, the CLI's listing has them.
    """
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            result.update(leaves(v, f"{path}.{k}" if path else k))
        return result
    if isinstance(value, list):
        result = {}
        for i, v in enumerate(value):
            result.update(leaves(v, f"{path}[{i}]"))
        return result
    return {} if value is None else {path: value}


def compared(item: Item) -> dict[str, Any]:
    """The leaves an import keeps as they were."""
    return leaves({k: v for k, v in item.items() if k not in NOT_COMPARED})


class Names:
    """Folder, collection and organization names by id, for one side of the import."""

    def __init__(self, folders: list[Item], collections: list[Item], orgs: list[Item]) -> None:
        self.folders = {f["id"]: f["name"] for f in folders if f.get("id")}
        self.collections = {c["id"]: c["name"] for c in collections}
        self.organizations = {o["id"]: o["name"] for o in orgs}
        assert len(set(self.organizations.values())) == len(orgs)

    def place(self, item: Item) -> str:
        org_id = item.get("organizationId")
        org = self.organizations.get(org_id, f"id {org_id}") if org_id else None
        return json.dumps([item.get("type"), item.get("name"), org])

    def key(self, item: Item) -> str:
        return json.dumps(
            [
                self.place(item),
                self.folders.get(item.get("folderId") or ""),
                sorted(self.collections.get(c, "") for c in item.get("collectionIds") or []),
                compared(item),
            ],
            sort_keys=True,
        )


@dataclass
class Matching:
    """Backup item id to imported item, and the backup items no imported item fits."""

    pairs: dict[str, Item] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)


def match(old: list[Item], new: list[Item], old_names: Names, new_names: Names) -> Matching:
    """Each backup item to the first unused imported item with the same content, else to the
    one unused imported item of its type, name and organization, as docs/restore.md does it."""
    by_key: dict[str, list[Item]] = defaultdict(list)
    for item in new:
        by_key[new_names.key(item)].append(item)
    result = Matching()
    used: set[str] = set()
    for item in old:
        candidates = by_key.get(old_names.key(item))
        if candidates:
            found = candidates.pop(0)
            result.pairs[item["id"]] = found
            used.add(found["id"])
    for item in old:
        if item["id"] in result.pairs:
            continue
        place = old_names.place(item)
        same = [n for n in new if n["id"] not in used and new_names.place(n) == place]
        if len(same) == 1:
            result.pairs[item["id"]] = same[0]
            used.add(same[0]["id"])
        else:
            result.unmatched.append(item["id"])
    return result


class Backup:
    """The decrypted backup, unzipped into a tmpfs directory as docs/restore.md does it."""

    def __init__(self, data: bytes, root: Path, made: dict[str, str]) -> None:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(root)
        with zipfile.ZipFile(root / "personal-export.zip") as personal:
            personal.extract("data.json", root)
        self.root = root
        ## the twins' and the trashed note's ids in the dev vault, by their notes
        self.made = made

    def json(self, member: str) -> Any:
        return json.loads((self.root / member).read_text())

    def attachments(self, item: dict[str, Any]) -> list[tuple[str, bytes]]:
        """File name and bytes of every attachment of a backup item."""
        return sorted(
            (a["fileName"], (self.root / member).read_bytes())
            for a, member in attachment_members(item)
        )


class Restore:
    """The restore account through its own CLI state, unlocked."""

    def __init__(self, cli: BwCli, key: str) -> None:
        self.cli = cli
        self.key = key

    def run(self, *args: str) -> str:
        return self.cli.run(*args, session=self.key)

    def json(self, *args: str) -> Any:
        return self.cli.json(*args, session=self.key)

    def items(self) -> list[Item]:
        """The items that are not in the trash, the archived ones too."""
        return self.json("list", "items") + self.json("list", "items", "--archived")

    def org_ids(self) -> dict[str, str]:
        """Every organization's id by its name (dev/README.md: Restore, Family and Club)."""
        orgs = self.json("list", "organizations")
        by_name = {o["name"]: o["id"] for o in orgs}
        assert len(by_name) == len(orgs)
        return by_name

    def attachments(self, item: dict[str, Any], out: Path) -> list[tuple[str, bytes]]:
        """File name and bytes of every attachment of an imported item, downloaded."""
        result = []
        for attachment in item.get("attachments") or []:
            path = out / attachment["id"]
            self.run(
                "get",
                "attachment",
                attachment["id"],
                "--itemid",
                item["id"],
                "--output",
                str(path),
            )
            result.append((attachment["fileName"], path.read_bytes()))
            path.unlink()
        return sorted(result)

    def empty(self) -> None:
        """Delete every item, folder and collection of every organization for good."""
        self.run("sync")
        trash = self.json("list", "items", "--trash") + self.json(
            "list", "items", "--trash", "--archived"
        )
        for item in self.items() + trash:
            self.run("delete", "item", item["id"], "--permanent")
        for folder in self.json("list", "folders"):
            if folder["id"]:
                self.run("delete", "folder", folder["id"])
        for org_id in self.org_ids().values():
            for collection in self.json("list", "org-collections", "--organizationid", org_id):
                self.run("delete", "org-collection", collection["id"], "--organizationid", org_id)
        self.run("sync")


class Places:
    """The dev vault's organizations and their collections, by name."""

    def __init__(self, elsewhere: Elsewhere) -> None:
        key = elsewhere.key
        orgs = elsewhere.bw_cli.json("list", "organizations", session=key)
        self.orgs = {o["name"]: o["id"] for o in orgs}
        names = {o["id"]: o["name"] for o in orgs}
        self.collections: dict[tuple[str, str], str] = {
            (names[c["organizationId"]], c["name"]): c["id"]
            for c in elsewhere.bw_cli.json("list", "collections", session=key)
        }

    def __call__(self, org: str, *collections: str) -> dict[str, Any]:
        """organizationId and collectionIds of these collections of the organization."""
        return {
            "organizationId": self.orgs[org],
            "collectionIds": [self.collections[(org, c)] for c in collections],
        }


def make_probes(elsewhere: Elsewhere, places: Places, made: dict[str, str]) -> None:
    """The folders, the collection and the items of the values the seeded vault lacks."""
    elsewhere.folder(EMPTY)
    clients = elsewhere.folder(CLIENTS)
    elsewhere.collection(places.orgs["Family"], EMPTY)
    made["probe"] = elsewhere.item(**PROBE, folderId=clients)
    for name, placed in FAMILY_MADE.items():
        note = {"type": 2, "name": name, "notes": name, "secureNote": {"type": 0}}
        folder = {"folderId": clients} if placed.folder else {}
        made[name] = elsewhere.item(
            **note, **places("Family", *placed.collections), **folder, favorite=placed.favorite
        )


@pytest.fixture(scope="module")
def backup(app: App, bw_cli: BwCli) -> Iterator[Backup]:
    assert app.login().status_code == 200
    ## the login started a backup in the background, the one by hand comes after it
    app.settle()
    ## plain secrets, so in RAM only and gone afterwards, as docs/restore.md does it
    root = Path(tempfile.mkdtemp(prefix="bwtidy-restore.", dir="/dev/shm"))
    try:
        elsewhere = Elsewhere(app, bw_cli, root / "made")
        try:
            made = {
                notes: elsewhere.note_with_attachment("Twin", "same.txt", content, notes)
                for notes, content in TWINS.items()
            }
            places = Places(elsewhere)
            for org, content in ORG_TWINS.items():
                made[org] = elsewhere.note_with_attachment(
                    "Org twin", "codes.txt", content, "org twin", **places(org, "Shared")
                )
            make_probes(elsewhere, places, made)
            made["binned"] = elsewhere.note_with_attachment(
                "Binned", "codes.txt", b"from the trash\n", "binned"
            )
            elsewhere.trash(made["binned"])
            for notes, (item_name, file_name, content) in ARCHIVED.items():
                place = places("Family", "Shared") if notes == "org shelved" else {}
                made[notes] = elsewhere.note_with_attachment(
                    item_name, file_name, content, notes, **place
                )
                elsewhere.archive(made[notes])
            elsewhere.trash(made["shelved and binned"])
            name = elsewhere.backup()
        finally:
            elsewhere.clean_up()
        shutil.rmtree(root / "made")
        data = decrypt_backup(app.data_dir / "backups" / name, PASSWORD)
        yield Backup(data, root / "backup", made)
    finally:
        shutil.rmtree(root)


@pytest.fixture(scope="module")
def restore(bw_cli: BwCli) -> Iterator[Restore]:
    appdata = tempfile.mkdtemp(prefix="bw-restore.")
    try:
        cli = BwCli(RESTORE_EMAIL, appdata)
        cli.ensure_logged_in()
        restore = Restore(cli, cli.unlock())
        restore.empty()
        try:
            yield restore
        finally:
            restore.empty()
            cli.run("logout")
    finally:
        shutil.rmtree(appdata)


def match_backup(backup: Backup, restore: Restore, trash: bool = False) -> Matching:
    """Every backup item (and trashed item with trash, as imported) to the item it became."""
    old = backup.json("raw/items.json") + not_trashed(backup.json("raw/archived.json"))
    if trash:
        old += [
            {**{k: v for k, v in i.items() if k not in TRASH_DROPPED}, "id": i["id"]}
            for i in trashed(backup)
        ]
    old_names = Names(
        backup.json("raw/folders.json"),
        backup.json("raw/collections.json"),
        backup.json("raw/organizations.json"),
    )
    restore.run("sync")
    new_names = Names(
        restore.json("list", "folders"),
        restore.json("list", "collections"),
        restore.json("list", "organizations"),
    )
    return match(old, restore.items(), old_names, new_names)


def not_trashed(items: list[Item]) -> list[Item]:
    return [i for i in items if not i.get("deletedDate")]


def trashed(backup: Backup) -> list[Item]:
    """The items in the trash as docs/restore.md, step 6, takes them: the archived ones too."""
    archived = backup.json("raw/archived.json")
    return backup.json("raw/trash.json") + [i for i in archived if i.get("deletedDate")]


## the files of docs/restore.md, step 5
BACKUP_ITEMS = ("raw/items.json", "raw/archived.json")


def attach(backup: Backup, restore: Restore, *items: str) -> list[str]:
    """Run docs/restore.md's attach.py on the backup items of the files items, as the document
    does it in the unpacked backup. Returns the lines it printed, which name only ids."""
    program = backup.root / "attach.py"
    program.write_text(attach_program(RESTORE_DOC.read_text()))
    assert restore.cli.appdata is not None
    env = {
        **os.environ,
        "BW_SESSION": restore.key,
        "BITWARDENCLI_APPDATA_DIR": restore.cli.appdata,
    }
    result = subprocess.run(
        ["python3", "attach.py", *items],
        cwd=backup.root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=BW_TIMEOUT * 4,
    )
    ## stderr only holds bw's errors and a traceback, never an item
    assert result.returncode == 0, result.stderr
    assert not (backup.root / "upload").exists()
    return result.stdout.splitlines()


@pytest.fixture(scope="module")
def first_run(backup: Backup, restore: Restore) -> list[str]:
    """The steps of docs/restore.md: the imports, then its attach.py.

    Returns the lines attach.py printed."""
    restore.run("import", "bitwardenjson", str(backup.root / "data.json"))
    org_ids = restore.org_ids()
    for org in backup.json("raw/organizations.json"):
        export = backup.root / f"org-{org['id']}-export.json"
        restore.run(
            "import", "bitwardenjson", str(export), "--organizationid", org_ids[org["name"]]
        )
    lines = attach(backup, restore, *BACKUP_ITEMS)
    assert not [line for line in lines if NOT_UPLOADED in line or NO_FOLDER in line], lines
    return lines


@pytest.fixture(scope="module")
def uploaded(first_run: list[str]) -> int:
    """How many attachments attach.py uploaded."""
    return sum(UPLOADED in line for line in first_run)


@pytest.fixture(scope="module")
def raw(backup: Backup) -> dict[str, dict[str, Any]]:
    """The backup items that step 5 brings back: the active ones, archived or not."""
    items = backup.json("raw/items.json") + not_trashed(backup.json("raw/archived.json"))
    return {i["id"]: i for i in items}


@pytest.fixture(scope="module")
def imported(backup: Backup, restore: Restore, uploaded: int) -> dict[str, dict[str, Any]]:
    """Every imported item by the id of the backup item it was matched to, with attachments."""
    matching = match_backup(backup, restore)
    assert not matching.unmatched, f"backup items no imported item fits: {matching.unmatched}"
    return matching.pairs


def names(listing: list[dict[str, Any]], ids: list[str] | None) -> list[str]:
    by_id = {e["id"]: e["name"] for e in listing}
    return sorted(by_id[i] for i in ids or [])


def differing(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """The paths whose values differ, never the values."""
    a, b = compared(old), compared(new)
    return sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))


def test_the_backup_holds_the_twins_and_the_trashed_note(backup: Backup, raw: dict):
    twins = [i for i in raw.values() if i["name"] == "Twin"]
    assert sorted(i["notes"] for i in twins) == sorted(TWINS)
    assert all([a["fileName"] for a in i["attachments"]] == ["same.txt"] for i in twins)
    [binned] = [i for i in backup.json("raw/trash.json") if i["name"] == "Binned"]
    assert binned["id"] == backup.made["binned"]
    org_twins = [i for i in raw.values() if i["name"] == "Org twin"]
    assert sorted(i["id"] for i in org_twins) == sorted(backup.made[o] for o in ORG_TWINS)
    assert len({json.dumps(compared(i), sort_keys=True) for i in org_twins}) == 1
    assert len(raw) == SEEDED_ITEMS + MADE_ITEMS


def test_the_backup_holds_the_org_items_folder_and_favorite(backup: Backup, raw: dict):
    """bw serve lists the user's own folder and favorite of an organization item."""
    folders = {f["id"]: f["name"] for f in backup.json("raw/folders.json") if f["id"]}
    in_folder = raw[backup.made["Org in folder"]]
    assert folders[in_folder["folderId"]] == CLIENTS
    assert not in_folder["favorite"]
    favorite = raw[backup.made["Org favorite"]]
    assert favorite["favorite"] and not favorite.get("folderId")
    exports = [
        backup.json(f"org-{o['id']}-export.json") for o in backup.json("raw/organizations.json")
    ]
    exported = {i["name"]: i for e in exports for i in e["items"]}
    ## which is why attach.py puts them back
    assert not exported["Org in folder"].get("folderId")
    assert not exported["Org favorite"]["favorite"]


def test_the_backup_holds_the_archived_notes_apart(backup: Backup):
    archived = {i["notes"]: i for i in backup.json("raw/archived.json")}
    assert archived.keys() == ARCHIVED.keys()
    for notes, (_, file_name, content) in ARCHIVED.items():
        assert archived[notes]["id"] == backup.made[notes]
        assert archived[notes]["archivedDate"]
        assert backup.attachments(archived[notes]) == [(file_name, content)]
    assert archived["shelved and binned"]["deletedDate"]
    listed = backup.json("raw/items.json") + backup.json("raw/trash.json")
    assert not {i["id"] for i in listed} & {backup.made[n] for n in ARCHIVED}


def test_an_archived_personal_item_comes_back_archived_with_its_attachment(
    backup: Backup, restore: Restore, imported: dict
):
    """The personal export holds its archivedDate, an organization export holds none, so
    the archived Family note comes back active (docs/restore.md, What does not come back)."""
    archived_ids = {i["id"] for i in restore.json("list", "items", "--archived")}
    shelved = imported[backup.made["shelved"]]
    assert shelved["id"] in archived_ids
    assert shelved["archivedDate"]
    org_shelved = imported[backup.made["org shelved"]]
    assert org_shelved["id"] not in archived_ids
    assert not org_shelved.get("archivedDate")
    out = backup.root.parent / "archived"
    out.mkdir()
    for notes in ("shelved", "org shelved"):
        _, file_name, content = ARCHIVED[notes]
        same = restore.attachments(imported[backup.made[notes]], out) == [(file_name, content)]
        assert same, f"{notes}: not its own file"


def test_every_attachment_is_uploaded(raw: dict, uploaded: int):
    assert uploaded == sum(len(i.get("attachments") or []) for i in raw.values())
    assert uploaded == SEEDED_ATTACHMENTS + MADE_ATTACHMENTS


def test_attach_sets_the_folder_and_favorite_of_the_org_items_that_lack_them(
    backup: Backup, first_run: list[str]
):
    changed = [line for line in first_run if SET in line]
    named = sorted(line.rsplit(" ", 1)[1] for line in changed)
    assert named == sorted(backup.made[n] for n in ("Org in folder", "Org favorite"))


def test_every_item_is_back_once(restore: Restore, raw: dict, imported: dict):
    assert imported.keys() == raw.keys()
    assert len({i["id"] for i in imported.values()}) == len(raw)
    assert len(restore.items()) == len(raw)


def test_every_item_has_the_same_leaves(raw: dict, imported: dict):
    diffs = [
        f"{item['name']}: {path}"
        for item_id, item in sorted(raw.items())
        for path in differing(item, imported[item_id])
    ]
    assert not diffs, f"leaves the import changed or dropped: {diffs}"


def test_only_the_ids_and_dates_are_new(raw: dict, imported: dict):
    """What the import does not keep, beyond folders and collections compared by name."""
    for item_id, item in raw.items():
        new = imported[item_id]
        assert new["id"] != item_id
        assert new["revisionDate"] != item["revisionDate"]
    same_created = sum(
        1 for i, item in raw.items() if imported[i]["creationDate"] == item.get("creationDate")
    )
    assert same_created == 0
    assert {"id", "revisionDate", "creationDate"} <= NOT_COMPARED


def test_passkeys_and_password_history_come_back(raw: dict, imported: dict):
    """Two things a convertItem loses (docs/spec.md, Writes), so worth naming outright."""
    by_name = {i["name"]: (i, imported[item_id]) for item_id, i in raw.items()}
    old, new = by_name["Example Forge"]
    assert len(new["login"].get("fido2Credentials") or []) == 1
    assert (
        old["login"]["fido2Credentials"][0]["credentialId"]
        == (new["login"]["fido2Credentials"][0]["credentialId"])
    )
    for name in ("Example Mail", "Online bank", "Proxmox"):
        old, new = by_name[name]
        assert old.get("passwordHistory"), name
        same = old["passwordHistory"] == new.get("passwordHistory")
        assert same, f"{name}: the password history differs"


def test_items_are_in_the_same_folders(backup: Backup, restore: Restore, raw: dict, imported: dict):
    old_folders = backup.json("raw/folders.json")
    new_folders = restore.json("list", "folders")
    wrong = sorted(
        item["name"]
        for item_id, item in raw.items()
        if names(old_folders, [item["folderId"]] if item.get("folderId") else None)
        != names(new_folders, [n] if (n := imported[item_id].get("folderId")) else None)
    )
    assert not wrong, f"in another folder: {wrong}"


def org_name(listing: list[dict[str, Any]], item: dict[str, Any]) -> str | None:
    org_id = item.get("organizationId")
    return names(listing, [org_id])[0] if org_id else None


def test_org_items_are_in_the_org_of_the_same_name_and_the_same_collections(
    backup: Backup, restore: Restore, raw: dict, imported: dict
):
    old_orgs = backup.json("raw/organizations.json")
    new_orgs = restore.json("list", "organizations")
    old_collections = backup.json("raw/collections.json")
    new_collections = restore.json("list", "collections")
    wrong = []
    for item_id, item in sorted(raw.items()):
        new = imported[item_id]
        if org_name(old_orgs, item) != org_name(new_orgs, new):
            wrong.append(f"{item['name']}: organization")
        if names(old_collections, item.get("collectionIds")) != names(
            new_collections, new.get("collectionIds")
        ):
            wrong.append(f"{item['name']}: collections")
    assert not wrong, f"wrong place: {wrong}"
    ## Family's 10 items and Club's 1, plus one org twin in each
    by_org = sorted(o for i in imported.values() if (o := org_name(new_orgs, i)))
    ## and the archived Family note
    assert by_org == ["Club"] * 2 + ["Family"] * (12 + len(FAMILY_MADE))


def test_the_made_items_are_back_with_their_folder_favorite_and_collections(
    backup: Backup, restore: Restore, imported: dict
):
    folders = {f["id"]: f["name"] for f in restore.json("list", "folders") if f["id"]}
    collections = {c["id"]: c["name"] for c in restore.json("list", "collections")}
    for name, placed in FAMILY_MADE.items():
        new = imported[backup.made[name]]
        assert folders.get(new.get("folderId") or "") == placed.folder, name
        assert new["favorite"] == placed.favorite, name
        wanted = sorted(placed.collections)
        assert sorted(collections[c] for c in new["collectionIds"]) == wanted, name
    probe = imported[backup.made["probe"]]
    assert folders[probe["folderId"]] == CLIENTS


def test_reprompt_custom_fields_and_uri_match_come_back(backup: Backup, imported: dict):
    """Values the seeded vault lacks, compared leaf by leaf above and named here outright."""
    probe = imported[backup.made["probe"]]
    assert probe["reprompt"] == PROBE["reprompt"]
    assert [(f["name"], f["type"], f.get("linkedId")) for f in probe["fields"]] == [
        ("remember", 2, None),
        ("user", 3, 100),
    ]
    assert probe["fields"][0]["value"] == "true"
    assert [u.get("match") for u in probe["login"]["uris"]] == [3]


def test_every_folder_and_collection_is_back_by_name(backup: Backup, restore: Restore, imported):
    old = sorted(f["name"] for f in backup.json("raw/folders.json") if f["id"])
    new = sorted(f["name"] for f in restore.json("list", "folders") if f["id"])
    assert old == new == sorted(["Home", "Infra", "Work", EMPTY, CLIENTS])
    old_orgs = backup.json("raw/organizations.json")
    new_orgs = restore.json("list", "organizations")
    old = sorted((org_name(old_orgs, c), c["name"]) for c in backup.json("raw/collections.json"))
    new = sorted((org_name(new_orgs, c), c["name"]) for c in restore.json("list", "collections"))
    ## Family and Club each have a collection Shared, each lands in its own organization
    assert (
        old
        == new
        == sorted(
            [("Club", "Shared"), ("Family", "Shared"), ("Family", "Youth"), ("Family", EMPTY)]
        )
    )


def test_every_attachment_has_the_same_bytes_and_file_name(
    backup: Backup, restore: Restore, raw: dict, imported: dict
):
    out = backup.root.parent / "downloaded"
    out.mkdir()
    wrong = []
    count = 0
    for item_id, item in sorted(raw.items()):
        old = backup.attachments(item)
        new = restore.attachments(imported[item_id], out)
        same = old == new
        if not same:
            wrong.append(item["name"])
        count += len(new)
    assert not wrong, f"attachments not back as they were: {wrong}"
    assert count == SEEDED_ATTACHMENTS + MADE_ATTACHMENTS


def test_each_twin_gets_its_own_file(backup: Backup, restore: Restore, imported: dict):
    """Found by notes in the restore account, not through the matching under test."""
    restore.run("sync")
    twins = [i for i in restore.json("list", "items") if i["name"] == "Twin"]
    assert sorted(i["notes"] for i in twins) == sorted(TWINS)
    out = backup.root.parent / "twins"
    out.mkdir()
    for twin in twins:
        same = restore.attachments(twin, out) == [("same.txt", TWINS[twin["notes"]])]
        assert same, f"{twin['notes']}: not its own same.txt"


def test_each_org_twin_gets_its_own_organizations_file(
    backup: Backup, restore: Restore, imported: dict
):
    """Found by organization in the restore account, not through the matching under test."""
    restore.run("sync")
    new_orgs = restore.json("list", "organizations")
    twins = [i for i in restore.json("list", "items") if i["name"] == "Org twin"]
    assert sorted(org_name(new_orgs, i) or "" for i in twins) == sorted(ORG_TWINS)
    out = backup.root.parent / "org-twins"
    out.mkdir()
    for twin in twins:
        org = org_name(new_orgs, twin) or ""
        same = restore.attachments(twin, out) == [("codes.txt", ORG_TWINS[org])]
        assert same, f"{org}: not its own codes.txt"


def revisions(restore: Restore) -> dict[str, str]:
    restore.run("sync")
    return {i["id"]: i["revisionDate"] for i in restore.items()}


def test_a_second_run_of_attach_changes_nothing(backup: Backup, restore: Restore, imported):
    """docs/restore.md: run it again after a failure, it only adds what is missing."""
    before = revisions(restore)
    assert attach(backup, restore, *BACKUP_ITEMS) == []
    assert revisions(restore) == before


def test_a_folder_name_the_account_lacks_leaves_the_item_in_its_folder(
    backup: Backup, restore: Restore, imported: dict
):
    [clients] = [f for f in restore.json("list", "folders") if f["name"] == CLIENTS]

    def rename(name: str) -> None:
        encoded = base64.b64encode(json.dumps({"name": name}).encode()).decode()
        restore.run("edit", "folder", clients["id"], encoded)

    before = revisions(restore)
    rename(f"{CLIENTS} renamed")
    try:
        lines = attach(backup, restore, *BACKUP_ITEMS)
    finally:
        rename(CLIENTS)
    in_folder = imported[backup.made["Org in folder"]]["id"]
    assert len(lines) == 1, lines
    assert NO_FOLDER in lines[0]
    assert backup.made["Org in folder"] in lines[0] and in_folder in lines[0]
    after = revisions(restore)
    assert after == before
    assert restore.json("get", "item", in_folder)["folderId"] == clients["id"]


def test_the_trash_imports_as_docs_restore_says(backup: Backup, restore: Restore, imported):
    """Runs last: it adds the trash items to the restore account, then their attachments."""
    trash = trashed(backup)
    assert {i["name"] for i in trash} == {
        "Old forum account",
        "Expired license key",
        "Binned",
        "Shelved and binned",
    }
    items = [{k: v for k, v in i.items() if k not in TRASH_DROPPED} for i in trash]
    path = backup.root / "trash-import.json"
    path.write_text(json.dumps({"encrypted": False, "items": items}))
    restore.run("import", "bitwardenjson", str(path))
    ## with-trash.json of step 6: the trashed items keep their ids, so attach.py finds their files
    moved = [{k: v for k, v in i.items() if k == "id" or k not in TRASH_DROPPED} for i in trash]
    (backup.root / "with-trash.json").write_text(json.dumps(backup.json("raw/items.json") + moved))
    lines = attach(backup, restore, "with-trash.json", "raw/archived.json")
    assert sum(UPLOADED in line for line in lines) == 2
    assert len(lines) == 2, lines
    matching = match_backup(backup, restore, trash=True)
    assert not matching.unmatched
    out = backup.root.parent / "trash"
    out.mkdir()
    for item in trash:
        new = matching.pairs[item["id"]]
        assert not new.get("deletedDate") and not new.get("organizationId")
        diffs = differing(item, new)
        assert not diffs, f"{item['name']}: {diffs}"
        same = restore.attachments(new, out) == backup.attachments(item)
        assert same, f"{item['name']}: attachments differ"
        assert bool(new.get("archivedDate")) == bool(item.get("archivedDate")), item["name"]
