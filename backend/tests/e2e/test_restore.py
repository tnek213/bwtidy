"""Restore on the Change log page puts the items of a row back as its backup holds them
(docs/spec.md, Backups, Restoring a change log row).

Each test uses an item no other test touches: Bank account for a notes edit, Backup disk for
the trash.
"""

import re

from playwright.sync_api import Locator, Page, expect
from support.api import Api, commit_new_entries, last_entry_id, rows_by_name
from support.pages import QUEUED, item_cell, row, search


def menu_action(page: Page, item_id: str, column: str, action: str) -> None:
    item_cell(page, item_id, column).click(button="right")
    page.get_by_role("menuitem", name=action, exact=True).click()


def change_log_row(page: Page, operation: str, item: str) -> Locator:
    """The newest Change log row of an operation on an item, after opening the page."""
    page.get_by_role("button", name="Change log", exact=True).click()
    log = page.get_by_role("table", name="Change log")
    entries = log.get_by_role("row").filter(has=page.get_by_role("cell", name=item, exact=True))
    ## the Operation cell shows a label before the kind
    return entries.filter(has=page.get_by_text(operation, exact=True)).first


def restore(page: Page, api: Api, entry: Locator, item: str, text: str) -> None:
    """Press Restore on the row, check the plan names the item, queue it and commit it."""
    after = last_entry_id(api)
    entry.get_by_role("button", name="Restore").click()
    dialog = page.get_by_role("dialog", name="Restore")
    expect(dialog).to_contain_text("Put these items back as they were before this change")
    plan = dialog.get_by_role("list", name="What the restore does")
    expect(plan.get_by_role("listitem")).to_have_count(1)
    expect(plan.get_by_role("listitem")).to_contain_text(item)
    expect(plan.get_by_role("listitem")).to_contain_text(text)
    expect(dialog).to_contain_text("bwtidy takes a new backup first")
    dialog.get_by_role("button", name="Restore").click()
    expect(dialog).to_be_hidden()
    commit_new_entries(api, after)


def test_restore_puts_old_notes_back(unlocked_page: Page, api: Api):
    page = unlocked_page
    name = "Bank account"
    before = rows_by_name(api)[name]
    item_id = before["id"]
    old_notes = before["cells"].get("notes")

    search(page, name)
    after = last_entry_id(api)
    item_cell(page, item_id, "notes").dblclick()
    page.locator(".ag-popup-editor textarea").fill("Notes changed by the restore e2e test")
    page.get_by_text(re.compile(r"^\d+ of \d+ items$")).click()
    commit_new_entries(api, after)
    changed = rows_by_name(api)[name]["cells"].get("notes") != old_notes
    assert changed, "the notes edit was not written"

    entry = change_log_row(page, "setLeaf", name)
    expect(entry).to_be_visible()
    restore(page, api, entry, name, "put back as before")

    restored = rows_by_name(api)[name]["cells"].get("notes") == old_notes
    assert restored, "the notes are not the old ones after the restore"
    page.get_by_role("button", name="Vault", exact=True).click()
    search(page, name)
    shown = item_cell(page, item_id, "notes")
    expect(shown).to_be_visible()
    expect(shown).not_to_have_class(QUEUED)
    expect(shown).not_to_contain_text("Notes changed by the restore e2e test")

    restore_row = change_log_row(page, "restoreItems", name)
    expect(restore_row).to_be_visible()
    expect(restore_row.get_by_role("cell").nth(4)).to_have_text("OK")
    ## a restore has a backup of its own, so it can be restored too
    expect(restore_row.get_by_role("button", name="Restore")).to_be_visible()

    ## the restore row names the backup of the change it restored, and links to that change
    log = api.get("/api/change-log")
    changed_entry = max(
        (e for e in log if e["operationKind"] == "setLeaf" and item_id in e["itemIds"]),
        key=lambda e: e["id"],
    )
    restore_entry = max(
        (e for e in log if e["operationKind"] == "restoreItems"), key=lambda e: e["id"]
    )
    assert restore_entry["restoredBackup"] == changed_entry["backup"]
    assert restore_entry["restoredChangeId"] == changed_entry["id"]
    assert restore_entry["backup"] != changed_entry["backup"]
    expect(restore_row).to_contain_text(f"From backup {changed_entry['backup']}")
    link = restore_row.get_by_role("button", name=re.compile(r"^the change of "))
    expect(link).to_be_visible()
    link.click()
    expect(entry).to_be_in_viewport()

    ## the change is marked as undone by the restore, and the mark shows the restore's row
    assert changed_entry["restoredBy"]["id"] == restore_entry["id"]
    mark = entry.get_by_role("button", name=re.compile(rf"^Restored .*: notes on {name}$"))
    expect(mark).to_be_visible()
    mark.click()
    expect(restore_row).to_be_in_viewport()


def test_restore_brings_a_trashed_item_back(unlocked_page: Page, api: Api):
    page = unlocked_page
    name = "Backup disk"
    item_id = rows_by_name(api)[name]["id"]

    search(page, name)
    after = last_entry_id(api)
    menu_action(page, item_id, "name", "Move to trash")
    page.get_by_role("dialog").get_by_role("button", name="Move to trash").click()
    commit_new_entries(api, after)
    assert name not in rows_by_name(api)
    expect(row(page, item_id)).to_have_count(0)

    ## the item is in the trash, so the Change log shows it by id
    entry = change_log_row(page, "trashItem", item_id)
    expect(entry).to_be_visible()
    restore(page, api, entry, name, "take out of the trash and put back as before")

    assert rows_by_name(api)[name]["id"] == item_id
    page.get_by_role("button", name="Vault", exact=True).click()
    search(page, name)
    expect(row(page, item_id)).to_have_count(1)
