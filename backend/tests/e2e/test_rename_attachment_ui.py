"""Rename attachments in the cell menu of an attachments cell (docs/spec.md, Grid, the cell
menu): the rename of the seeded attachment ca-notes.txt of Internal CA, committed by the
auto-commit, and the Change log's Restore that gives the old name back. What MCP
can do, the UI can do too. The fixture vault_put_back puts the item back
when a step fails.
"""

import time

import pytest
from playwright.sync_api import Page, expect
from support.api import COMMIT_TIMEOUT, Api, commit_new_entries, last_entry_id, rows_by_name
from support.pages import cell

pytestmark = pytest.mark.usefixtures("vault_put_back")

ITEM = "Internal CA"
OLD = "ca-notes.txt"
NEW = "ca-notes-2026.txt"


def attachments(api: Api) -> list[dict[str, str]]:
    return rows_by_name(api)[ITEM].get("attachments", [])


def wait_queued(api: Api, after: int) -> dict:
    """The first queue entry newer than after, once the UI has sent it."""
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while True:
        entries = [entry for entry in api.get("/api/queue") if entry["id"] > after]
        if entries:
            return entries[0]
        assert time.monotonic() < deadline, "the rename was not queued"
        time.sleep(0.5)


def test_an_attachment_is_renamed_from_the_cell_menu_and_restored(unlocked_page: Page, api: Api):
    page = unlocked_page
    [before] = attachments(api)
    ## the grid answer names an attachment by id and file name only, never its url or size
    assert set(before) == {"id", "fileName"}
    assert before["fileName"] == OLD

    cell(page, ITEM, "attachments").click(button="right")
    page.get_by_role("menu").get_by_role("menuitem", name="Rename attachments").click()
    dialog = page.get_by_role("dialog", name=f"Attachments of {ITEM}")
    expect(dialog.get_by_role("listitem")).to_have_count(1)
    line = dialog.get_by_role("listitem", name=OLD, exact=True)
    line.get_by_role("button", name="Rename").click()
    field = dialog.get_by_label(f"New name for {OLD}")
    field.fill("notes/ca.txt")
    expect(dialog.get_by_text("A file name may not hold a slash or a backslash")).to_be_visible()
    expect(dialog.get_by_role("button", name="Save")).to_be_disabled()
    after = last_entry_id(api)
    field.fill(NEW)
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_be_hidden()

    entry = wait_queued(api, after)
    assert entry["source"] == "ui"
    assert entry["operation"]["kind"] == "renameAttachment"
    api.wait_saved()
    [renamed] = attachments(api)
    assert renamed["fileName"] == NEW
    assert renamed["id"] != before["id"]
    expect(cell(page, ITEM, "attachments")).to_have_text(NEW)

    page.get_by_role("button", name="Change log", exact=True).click()
    log = page.get_by_role("table", name="Change log")
    row = log.get_by_role("row").filter(has_text="renameAttachment").first
    expect(row).to_contain_text("Rename attachment")
    expect(row).to_contain_text(f"Attachment {before['id']} replaced by {renamed['id']}")
    ## Time, Source, Operation, Items, Result, Backup
    expect(row.get_by_role("cell").nth(1)).to_have_text("UI")
    after = last_entry_id(api)
    row.get_by_role("button", name="Restore").click()
    restore = page.get_by_role("dialog", name="Restore")
    expect(restore).to_contain_text(f'rename the attachment "{NEW}" back to "{OLD}"')
    restore.get_by_role("button", name="Restore").click()
    expect(restore).to_be_hidden()
    commit_new_entries(api, after)
    [back] = attachments(api)
    assert back["fileName"] == OLD
