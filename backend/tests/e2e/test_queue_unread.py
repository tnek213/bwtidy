"""The Queue tabs count the entries this browser has not shown yet.

Every test gets a fresh browser context, so the entries earlier tests left in a tab count as
unread at first. The test looks at each tab long enough to read them before it counts. The
item the approval renamed gets its name back at the end of the file (file_put_back).
"""

import json
import re

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import Api
from support.mcp import McpCall
from support.queue_page import (
    APPROVE_TIMEOUT,
    approve_result,
    enable_mcp,
    item_id,
    open_queue,
    select,
)

pytestmark = pytest.mark.usefixtures("file_put_back")

ITEM = "Youth gym"
## a tab counts as read after 4 s, the check leaves a second more
READ_WAIT = 5_000


def tab(page: Page, name: str) -> Locator:
    tabs = page.get_by_role("tablist", name="Queue entries")
    return tabs.get_by_role("tab", name=re.compile(rf"^{name}\b"))


def read_tab(page: Page, name: str, status: str) -> None:
    """Show a tab until its entries count as read."""
    tab(page, name).click()
    expect(page.get_by_role("progressbar", name=f"Loading the {status} entries")).to_have_count(0)
    page.wait_for_timeout(READ_WAIT)
    expect(tab(page, name)).to_have_text(name)


def test_the_tabs_count_unread_entries_and_follow_an_approval(
    unlocked_page: Page, mcp_call: McpCall, api: Api
):
    page = unlocked_page
    enable_mcp(page)
    answer = json.loads(
        mcp_call("propose_rename_item", item_id=item_id(mcp_call, ITEM), name=f"{ITEM} (e2e)")
    )
    try:
        pending = [entry["id"] for entry in api.get("/api/queue?status=pending")]
        assert pending == [answer["entryId"]], "another test left an entry pending"

        open_queue(page)
        expect(tab(page, "Pending")).to_have_text("Pending (1)")
        page.wait_for_timeout(READ_WAIT)
        expect(tab(page, "Pending")).to_have_text("Pending")

        read_tab(page, "Applied", "applied")
        tab(page, "Pending").click()
        select(page, answer)
        page.get_by_role("button", name="Approve selected").click()

        expect(approve_result(page)).to_contain_text("Applied 1 entry", timeout=APPROVE_TIMEOUT)
        expect(tab(page, "Applied")).to_have_text("Applied (1)")
        expect(tab(page, "Pending")).to_have_text("Pending")
        read_tab(page, "Applied", "applied")
    finally:
        ## so Approve all in another test never applies it
        if answer["entryId"] in {entry["id"] for entry in api.get("/api/queue?status=pending")}:
            api.post("/api/queue/reject", {"ids": [answer["entryId"]]})
