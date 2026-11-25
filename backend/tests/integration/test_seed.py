"""The seeded dev vault holds the dev fixture as dev/dev-vault.md, "The fixture", lists it.

Assertions look at names, types, counts and field names only, never at secret values.
bw leaves out keys whose value is null (organizationId, folderId, totp), hence the .get.
"""

import shutil
import tempfile
from collections import Counter
from typing import Any

import pytest
from support.cli import BwCli

from bwtidy.vault.tool_note import TOOL_NOTE_NAME

pytestmark = pytest.mark.integration

LOGIN, NOTE, CARD, IDENTITY, SSH_KEY = 1, 2, 3, 4, 5
TEXT, HIDDEN = 0, 1
TRASH = {"Old forum account", "Expired license key"}
## dev/README.md, Credentials
RESTORE_EMAIL = "restore@bwtidy.test"


@pytest.fixture(scope="module")
def items(bw):
    return bw("list", "items")


@pytest.fixture(scope="module")
def org(bw):
    orgs = bw("list", "organizations")
    return next(o for o in orgs if o["name"] == "Family")


@pytest.fixture(scope="module")
def personal(items):
    return [i for i in items if i.get("organizationId") is None]


@pytest.fixture(scope="module")
def by_name(items):
    names = Counter(i["name"] for i in items)
    assert [n for n, c in names.items() if c > 1] == []
    return {i["name"]: i for i in items}


def fields(item):
    return [(f["name"], f["type"]) for f in item.get("fields") or []]


def field_names(item):
    return [f["name"] for f in item.get("fields") or []]


def test_personal_counts_by_type(personal):
    assert Counter(i["type"] for i in personal) == {
        LOGIN: 42,
        NOTE: 21,
        CARD: 2,
        IDENTITY: 1,
        SSH_KEY: 1,
    }


def test_folders(bw, personal):
    folders = {f["id"]: f["name"] for f in bw("list", "folders") if f["id"]}
    assert set(folders.values()) == {"Work", "Home", "Infra"}
    plain = [i for i in personal if i["type"] == LOGIN and not i.get("fields")]
    per_folder = Counter(folders.get(i.get("folderId")) for i in plain)
    assert per_folder == {"Work": 10, "Home": 10, "Infra": 10, None: 9}


def test_plain_logins(personal):
    plain = [i for i in personal if i["type"] == LOGIN and not i.get("fields")]
    assert len(plain) == 39
    assert all(i["login"].get("username") and i["login"].get("password") for i in plain)
    assert sum(1 for i in plain if i["login"].get("uris")) == 38
    assert sum(1 for i in plain if i["login"].get("totp")) == 8


def test_computers_in_three_shapes(by_name):
    assert by_name["ThinkPad X1"]["type"] == LOGIN
    assert fields(by_name["ThinkPad X1"]) == [
        ("MSDM Windows Key", TEXT),
        ("BIOS Password", HIDDEN),
        ("LUKS", HIDDEN),
        ("User", TEXT),
        ("User password", HIDDEN),
    ]
    assert by_name["Gaming desktop"]["type"] == LOGIN
    assert fields(by_name["Gaming desktop"]) == [
        ("LUKS/dm-crypt and Bitlocker", HIDDEN),
        ("Bitlocker RecoveryPassword", HIDDEN),
        ("UEFI Administrator Password", HIDDEN),
    ]
    assert by_name["Media PC"]["type"] == NOTE
    assert fields(by_name["Media PC"]) == [
        ("user", TEXT),
        ("user password", HIDDEN),
        ("dm-crypt password", HIDDEN),
    ]


def test_notes_that_are_logins(personal):
    shapes = Counter(
        tuple(fields(i))
        for i in personal
        if i["type"] == NOTE and field_names(i)[:1] == ["username"]
    )
    assert shapes == {
        (("username", TEXT), ("Password", HIDDEN)): 4,
        (("username", TEXT), ("password", HIDDEN)): 1,
        (("username", TEXT), ("code", HIDDEN)): 1,
    }


def test_free_text_notes(bw, personal):
    trash = bw("list", "items", "--trash")
    notes = [i for i in personal + trash if i["type"] == NOTE and not i.get("fields")]
    assert len(notes) == 8
    assert all(i.get("notes") for i in notes)


def test_odd_field_names(personal):
    names = {n for i in personal for n in field_names(i)}
    assert {
        "ca key passphrase",
        "token",
        "vault_pass_ai",
        "sudo",
        "infra",
        "Public Key",
        "Encryption passphrase ",
        "Recovery code",
        "Recovery key",
    } <= names
    assert "Encryption passphrase" not in names


def test_cards(personal):
    cards = [i for i in personal if i["type"] == CARD]
    assert sorted(i["card"]["brand"] for i in cards) == ["Mastercard", "Visa"]


def test_identity(personal, by_name):
    assert [i["name"] for i in personal if i["type"] == IDENTITY] == ["Dev Identity"]
    identity = by_name["Dev Identity"]
    assert identity.get("folderId") is None
    filled = {k for k, v in identity["identity"].items() if v}
    assert filled == {
        "title",
        "firstName",
        "lastName",
        "email",
        "phone",
        "address1",
        "city",
        "postalCode",
        "country",
        "username",
    }


def test_ssh_key(personal, by_name):
    assert [i["name"] for i in personal if i["type"] == SSH_KEY] == ["Build server key"]
    key = by_name["Build server key"]
    assert key.get("folderId") is None
    ssh = key["sshKey"]
    assert ssh["privateKey"].startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert ssh["publicKey"].startswith("ssh-ed25519 ")
    assert ssh["keyFingerprint"].startswith("SHA256:")


def test_favorites(bw, personal):
    folders = {f["id"]: f["name"] for f in bw("list", "folders") if f["id"]}
    favorites = {i["name"]: folders[i["folderId"]] for i in personal if i.get("favorite")}
    assert favorites == {"Wiki": "Work", "Streaming video": "Home", "Dashboards": "Infra"}
    assert all(i["type"] == LOGIN for i in personal if i.get("favorite"))


def test_same_field_name_twice(personal):
    twice = [i["name"] for i in personal if len(set(field_names(i))) < len(field_names(i))]
    assert twice == ["Home router"]


def test_passkey(personal, by_name):
    with_passkeys = [i["name"] for i in personal if (i.get("login") or {}).get("fido2Credentials")]
    assert with_passkeys == ["Example Forge"]
    [credential] = by_name["Example Forge"]["login"]["fido2Credentials"]
    assert credential["rpId"] == "forge.example.test"
    assert credential["keyAlgorithm"] == "ECDSA"
    assert credential["keyValue"]


def seeded_history(item: dict[str, Any]) -> list[dict[str, Any]]:
    """The history without hidden field records.

    bw adds "<name>: <value>" for every hidden field an edit changes, renames or removes,
    so the normalizing tests leave such entries behind even after their undo.
    """
    return [h for h in item.get("passwordHistory") or [] if ": " not in h["password"]]


def test_password_history(personal):
    history = {i["name"]: len(seeded_history(i)) for i in personal if seeded_history(i)}
    assert history == {"Example Mail": 2, "Online bank": 1, "Proxmox": 1}


def test_org_items_per_collection(bw, items, org):
    collections = {
        c["id"]: c["name"] for c in bw("list", "org-collections", "--organizationid", org["id"])
    }
    assert set(collections.values()) == {"Shared", "Youth"}
    org_items = [i for i in items if i.get("organizationId") == org["id"]]
    assert len(org_items) == 10
    per_collection = Counter(collections[c] for i in org_items for c in i["collectionIds"])
    assert per_collection == {"Shared": 6, "Youth": 4}


def test_org_computer_note(by_name, org):
    laptop = by_name["Youth laptop"]
    assert laptop["organizationId"] == org["id"]
    assert laptop["type"] == NOTE
    assert field_names(laptop) == ["User", "password", "Bitlocker recovery key"]


def test_attachments(items, org):
    attached = {i["name"]: len(i["attachments"]) for i in items if i.get("attachments")}
    assert attached == {"Internal CA": 1, "ThinkPad X1": 1, "Home insurance": 1, "Youth laptop": 1}
    owners = Counter(i.get("organizationId") for i in items if i.get("attachments"))
    assert owners == {None: 2, org["id"]: 2}


def test_trash(bw, items):
    trash = bw("list", "items", "--trash")
    assert {i["name"] for i in trash} == TRASH
    assert all(i["deletedDate"] for i in trash)
    assert TRASH.isdisjoint(i["name"] for i in items)


def test_the_dev_account_has_the_orgs_family_and_club(bw):
    assert sorted(o["name"] for o in bw("list", "organizations")) == ["Club", "Family"]


def test_club_holds_one_item_in_its_collection_shared(bw, items):
    """Club's collection has the name of one of Family's on purpose."""
    [club] = [o for o in bw("list", "organizations") if o["name"] == "Club"]
    [shared] = bw("list", "org-collections", "--organizationid", club["id"])
    assert shared["name"] == "Shared"
    [item] = [i for i in items if i.get("organizationId") == club["id"]]
    assert item["name"] == "Club locker code"
    assert item["type"] == NOTE
    assert item["collectionIds"] == [shared["id"]]
    assert not item.get("fields")
    assert not item.get("attachments")


def test_the_restore_account_has_the_orgs_family_club_and_restore():
    """Each with no collection and the vault empty, a CLI state of its own for the account.

    bwtidy's own note may be there once bwtidy has opened the account, so it is left out.
    """
    appdata = tempfile.mkdtemp(prefix="bw-restore-seed.")
    try:
        cli = BwCli(RESTORE_EMAIL, appdata)
        cli.ensure_logged_in()
        key = cli.unlock()
        try:
            cli.run("sync", session=key)
            orgs = cli.json("list", "organizations", session=key)
            assert sorted(o["name"] for o in orgs) == ["Club", "Family", "Restore"]
            for org in orgs:
                listed = cli.json(
                    "list", "org-collections", "--organizationid", org["id"], session=key
                )
                assert listed == [], org["name"]
            listed_items = cli.json("list", "items", session=key)
            left = [
                i for i in listed_items if not (i["name"] == TOOL_NOTE_NAME and i["type"] == NOTE)
            ]
            assert left == []
        finally:
            cli.run("logout")
    finally:
        shutil.rmtree(appdata)
