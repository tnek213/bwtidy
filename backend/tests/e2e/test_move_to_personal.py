"""The organization dialog moves an organization item into the personal vault.

The item goes back into the organization at the end of the file, its personal copy is deleted
(file_put_back).
"""

import re

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import Api

pytestmark = pytest.mark.usefixtures("file_put_back")

ITEM = "Youth laptop"
WARNING = (
    "bwtidy makes a personal copy and moves this item to the trash. Attachments are copied, "
    "passkeys and password history are lost. Everyone who shares its collections loses it."
)
QUEUED = re.compile(r"\bbw-queued-cell\b")
## the auto-commit waits 10 seconds (the queued value shows well before that), then backs up,
## writes, copies the attachment and reloads the vault
COMMIT_TIMEOUT = 120_000


def row_id(page: Page, item: str) -> str:
    """The grid row id of an item, found through its name cell after a search."""
    page.get_by_label("Search").fill(item)
    name = page.locator(".ag-cell[col-id='name']").get_by_text(item, exact=True)
    expect(name).to_have_count(1)
    found = name.locator("xpath=ancestor::*[@row-id][1]").get_attribute("row-id")
    assert found is not None
    return found


def cell(page: Page, row: str, column: str) -> Locator:
    return page.locator(f".ag-row[row-id='{row}'] .ag-cell[col-id='{column}']")


def moves_to_personal(api: Api) -> int:
    """The change log rows of moveToPersonal so far, an earlier run's included."""
    return sum(entry["operationKind"] == "moveToPersonal" for entry in api.get("/api/change-log"))


def test_move_an_organization_item_to_the_personal_vault(unlocked_page: Page, api: Api):
    page = unlocked_page
    earlier = moves_to_personal(api)
    old = row_id(page, ITEM)
    organization = cell(page, old, "organization")
    expect(organization).to_have_text("Family")
    expect(cell(page, old, "attachments")).to_have_text("youth-laptop-recovery.txt")

    organization.dblclick()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_role("heading")).to_have_text(f"Move {ITEM} out of Family")
    expect(dialog.get_by_role("combobox", name="Move to")).to_have_text("Personal vault")
    expect(dialog.get_by_role("alert")).to_have_text(WARNING)
    dialog.get_by_role("button", name="Move").click()
    expect(dialog).to_have_count(0)

    ## the old row shows the queued value until the commit replaces it with the personal copy
    expect(organization).to_have_class(QUEUED, timeout=5_000)
    expect(organization).to_have_text("")
    expect(page.locator(f".ag-row[row-id='{old}']")).to_have_count(0, timeout=COMMIT_TIMEOUT)
    new = row_id(page, ITEM)
    assert new != old
    expect(cell(page, new, "organization")).to_have_text("")
    expect(cell(page, new, "organization")).not_to_have_class(QUEUED)
    expect(cell(page, new, "collections")).to_have_text("")
    expect(cell(page, new, "attachments")).to_have_text("youth-laptop-recovery.txt")

    page.get_by_role("button", name="Change log").click()
    log = page.get_by_role("table", name="Change log")
    ## the newest row first
    writes = log.get_by_role("row").filter(has_text="moveToPersonal")
    expect(writes).to_have_count(earlier + 1)
    write = writes.first
    expect(write.get_by_role("cell").nth(1)).to_have_text("UI")
    expect(write.get_by_role("cell").nth(3)).to_have_text(old)
    expect(write.get_by_role("cell").nth(4)).to_have_text("OK")
