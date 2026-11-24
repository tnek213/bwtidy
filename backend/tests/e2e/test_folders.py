"""Manage folders in the Folder column's header menu: rename a folder, delete an empty one
(docs/spec.md, Grid, Manage folders), and the Change log's Restore of the rename.

The folder Infra is renamed and renamed back by the Restore, the folder Spare is made empty as
another device and deleted. The fixture vault_put_back puts both back when a step fails.
"""

from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import Api, commit_new_entries, last_entry_id
from support.elsewhere import elsewhere
from support.pages import item_cell, search

pytestmark = pytest.mark.usefixtures("vault_put_back")

MAKE_SPARE = """echo '{"name":"Spare"}' | bw encode | bw create folder >/dev/null"""


def folders(api: Api) -> dict[str, dict[str, Any]]:
    return {folder["name"]: folder for folder in api.get("/api/grid")["folders"]}


def manage_folders(page: Page) -> Locator:
    page.get_by_role("button", name="Actions for folder", exact=True).click()
    menu = page.get_by_role("menu")
    expect(menu.get_by_role("menuitem")).to_have_count(1)
    menu.get_by_role("menuitem", name="Manage folders…").click()
    dialog = page.get_by_role("dialog", name="Folders")
    expect(dialog).to_be_visible()
    return dialog


def test_manage_folders_renames_a_folder_and_deletes_an_empty_one(unlocked_page: Page, api: Api):
    page = unlocked_page
    elsewhere(MAKE_SPARE)
    infra = folders(api)["Infra"]
    in_infra = [r for r in api.get("/api/grid")["rows"] if r.get("folderId") == infra["id"]]
    assert in_infra, "no item is in the folder Infra"

    dialog = manage_folders(page)
    row = dialog.get_by_role("listitem", name="Infra", exact=True)
    count = infra["items"]
    expect(row).to_contain_text(f"{count} item" if count == 1 else f"{count} items")
    expect(row.get_by_role("button", name="Delete")).to_be_disabled()
    after = last_entry_id(api)
    row.get_by_role("button", name="Rename").click()
    field = dialog.get_by_label("New name for Infra")
    field.fill("Work")
    expect(dialog.get_by_text("Another folder is named Work")).to_be_visible()
    expect(dialog.get_by_role("button", name="Save")).to_be_disabled()
    field.fill("Servers")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_be_hidden()
    ## the rename's commit syncs, so the vault in RAM sees Spare from here on
    commit_new_entries(api, after)
    now = folders(api)
    assert now["Servers"]["id"] == infra["id"]
    assert "Infra" not in now
    assert now["Spare"]["items"] == 0

    page.reload()
    search(page, in_infra[0]["cells"]["name"])
    expect(item_cell(page, in_infra[0]["id"], "folder")).to_have_text("Servers")

    dialog = manage_folders(page)
    servers = dialog.get_by_role("listitem", name="Servers", exact=True)
    servers.get_by_role("button", name="Delete").hover(force=True)
    expect(page.get_by_role("tooltip")).to_contain_text("out first")
    after = last_entry_id(api)
    dialog.get_by_role("listitem", name="Spare", exact=True).get_by_role(
        "button", name="Delete"
    ).click()
    expect(dialog).to_be_hidden()
    commit_new_entries(api, after)
    assert "Spare" not in folders(api)

    page.get_by_role("button", name="Change log", exact=True).click()
    log = page.get_by_role("table", name="Change log")
    renamed = log.get_by_role("row").filter(has_text="renameFolder").first
    expect(renamed).to_contain_text("Folder Servers")
    after = last_entry_id(api)
    renamed.get_by_role("button", name="Restore").click()
    restore = page.get_by_role("dialog", name="Restore")
    expect(restore).to_contain_text("Put this folder back as it was before this change")
    expect(restore).to_contain_text('rename back to "Infra"')
    restore.get_by_role("button", name="Restore").click()
    expect(restore).to_be_hidden()
    commit_new_entries(api, after)
    assert folders(api)["Infra"]["id"] == infra["id"]
