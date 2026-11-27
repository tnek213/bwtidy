"""Rename attachments in the cell menu of an attachments cell (docs/spec.md, Grid, the cell
menu): the rename of the seeded attachment ca-notes.txt of Internal CA, committed by the
auto-commit, and the Change log's Restore that gives the old name back. What MCP
can do, the UI can do too. The fixture vault_put_back puts the item back
when a step fails.

The dialog refuses another attachment's name as the backend does, by case folding: a second
attachment Straße.txt, added as another device, clashes with STRASSE.txt.
"""

import time

import pytest
from playwright.sync_api import Page, expect
from support.api import COMMIT_TIMEOUT, Api, commit_new_entries, last_entry_id, rows_by_name
from support.elsewhere import elsewhere
from support.login import enter_account, log_in
from support.pages import cell, grid

pytestmark = pytest.mark.usefixtures("vault_put_back")

ITEM = "Internal CA"
OLD = "ca-notes.txt"
NEW = "ca-notes-2026.txt"


def attachments(api: Api) -> list[dict[str, str]]:
    return rows_by_name(api)[ITEM].get("attachments", [])


## attaches a one-line file named $2 to the item $1
ADD_ATTACHMENT = """
dir=$(mktemp -d)
echo e2e > "$dir/$2"
bw create attachment --file "$dir/$2" --itemid "$1" >/dev/null
rm -r "$dir"
"""
## the ids of the attachments named argv 1 of the item on stdin
ATTACHMENTS_NAMED = (
    "import json, sys; item = json.load(sys.stdin); "
    "named = [a['id'] for a in item.get('attachments') or [] if a['fileName'] == sys.argv[1]]; "
    "print('\\n'.join(named))"
)
## deletes the attachments named $2 of the item $1
REMOVE_ATTACHMENT = """
for id in $(bw get item "$1" | python3 -c "$3" "$2"); do
  bw delete attachment "$id" --itemid "$1" >/dev/null
done
"""


def unlock_again(page: Page, api: Api) -> None:
    """End the session and unlock, so the app loads the vault with a change made elsewhere."""
    api.delete("/api/session")
    enter_account(page)
    log_in(page)
    grid(page)


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


def test_the_rename_refuses_another_attachments_name_by_case_folding(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)[ITEM]["id"]
    elsewhere(ADD_ATTACHMENT, item_id, "Straße.txt")
    try:
        unlock_again(page, api)
        cell(page, ITEM, "attachments").click(button="right")
        page.get_by_role("menu").get_by_role("menuitem", name="Rename attachments").click()
        dialog = page.get_by_role("dialog", name=f"Attachments of {ITEM}")
        expect(dialog.get_by_role("listitem")).to_have_count(2)
        line = dialog.get_by_role("listitem", name=OLD, exact=True)
        line.get_by_role("button", name="Rename").click()
        field = dialog.get_by_label(f"New name for {OLD}")
        ## str.casefold turns ß into ss, which lowercasing alone does not
        field.fill("STRASSE.txt")
        expect(dialog.get_by_text("Another attachment is named Straße.txt")).to_be_visible()
        expect(dialog.get_by_role("button", name="Save")).to_be_disabled()
        field.fill("STRASSE-2.txt")
        expect(dialog.get_by_role("button", name="Save")).to_be_enabled()
    finally:
        elsewhere(REMOVE_ATTACHMENT, item_id, "Straße.txt", ATTACHMENTS_NAMED)
        ## a session again, for vault_put_back, with the vault as the test found it
        unlock_again(page, api)
