"""Restores and folder deletes of archived items against the dev vault.

bw serve leaves an archived item out of every listing unless it is asked for archived items.
A restore of a change log row puts such an item back as its backup holds it and keeps it
archived, and a folder that holds only an archived item is not deleted (Bitwarden would take
the item out of it). Each test makes its own items with the CLI and deletes them for good at
the end. Nothing here prints an item or a value.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from support.app import App, Elsewhere, wait_until_listed
from support.cli import BwCli
from support.restorer import Restorer

pytestmark = pytest.mark.integration

NOTE = 2


@pytest.fixture(scope="module")
def vault(app: App, bw_cli: BwCli) -> Iterator[Restorer]:
    assert app.login().status_code == 200
    app.settle()
    yield Restorer(app, bw_cli)


@pytest.fixture
def elsewhere(vault: Restorer, bw_cli: BwCli, tmp_path: Path) -> Iterator[Elsewhere]:
    made = Elsewhere(vault.app, bw_cli, tmp_path)
    try:
        yield made
    finally:
        made.clean_up()


def reload(vault: Restorer) -> None:
    """Let the app see what the CLI changed."""
    vault.beat()
    vault.call(vault.client.sync)
    vault.call(vault.app.session.cache.load)


def note(elsewhere: Elsewhere, vault: Restorer, name: str, **place: str) -> str:
    """A secure note made elsewhere, place (folderId) as given, once the app sees it."""
    item_id = elsewhere.item(
        type=NOTE, name=name, notes="as backed up", secureNote={"type": 0}, **place
    )
    wait_until_listed(vault.app, item_id)
    reload(vault)
    return item_id


def named(vault: Restorer, name: str) -> list[dict]:
    """Every item of this name: active, archived, in the trash or archived in the trash."""
    every = vault.cli("list", "items") + vault.cli("list", "items", "--archived")
    every += vault.cli("list", "items", "--trash")
    every += vault.cli("list", "items", "--trash", "--archived")
    return [i for i in every if i.get("name") == name]


def test_a_restore_puts_an_archived_item_back_and_keeps_it_archived(
    vault: Restorer, elsewhere: Elsewhere
):
    item_id = note(elsewhere, vault, "Shelved restore")
    item = vault.raw(item_id)
    row = vault.apply(vault.set_leaf(item, "notes", "changed in bwtidy"))
    vault.raw_after(item_id, item["revisionDate"])
    elsewhere.archive(item_id)
    reload(vault)
    assert item_id not in vault.app.session.cache.items_by_id()

    restored = vault.restore(row, ["put"])
    assert restored["createdItemIds"] == []
    after = vault.raw_until(item_id, lambda i: i.get("notes") == "as backed up")
    assert after.get("notes") == "as backed up", "the archived item was not put back"
    assert after.get("archivedDate"), "the archived item left the archive"
    assert [i["id"] for i in named(vault, "Shelved restore")] == [item_id]


def test_a_restore_takes_an_archived_item_out_of_the_trash_and_keeps_it_archived(
    vault: Restorer, elsewhere: Elsewhere
):
    item_id = note(elsewhere, vault, "Shelved and binned restore")
    item = vault.raw(item_id)
    row = vault.apply(vault.set_leaf(item, "notes", "changed in bwtidy"))
    vault.raw_after(item_id, item["revisionDate"])
    elsewhere.archive(item_id)
    elsewhere.trash(item_id)
    reload(vault)

    restored = vault.restore(row, ["untrash"])
    assert restored["createdItemIds"] == []
    after = vault.raw_until(item_id, lambda i: i.get("notes") == "as backed up")
    assert after.get("notes") == "as backed up", "the archived item was not put back"
    assert after.get("archivedDate") and not after.get("deletedDate")
    assert [i["id"] for i in named(vault, "Shelved and binned restore")] == [item_id]


def test_a_folder_that_holds_only_an_archived_item_is_not_deleted(
    vault: Restorer, elsewhere: Elsewhere
):
    folder_id = elsewhere.folder("Shelf")
    item_id = note(elsewhere, vault, "Shelved in a folder", folderId=folder_id)
    elsewhere.archive(item_id)
    reload(vault)
    assert vault.raw(item_id)["folderId"] == folder_id

    response = vault.post({"kind": "deleteFolder", "folderId": folder_id, "shownName": "Shelf"})
    assert response.status_code == 422, response.text
    assert "the folder holds 1 item in the archive" in response.json()["detail"]
    reload(vault)
    assert folder_id in {f["id"] for f in vault.app.session.cache.folders}
    assert vault.raw(item_id)["folderId"] == folder_id
