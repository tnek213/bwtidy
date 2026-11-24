"""The pictures of the explanation pages: a change log row with its Restore button (docs/spec.md,
Backups, Restoring a change log row) and an agent's proposal waiting in the Queue (MCP and the
queue).

The notes edit is put back by vault_put_back, the proposal is rejected and the MCP token removed,
so the vault and the app are as seeded afterwards. The proposal comes first: after a reset its
Queue tabs count no applied entries yet.
"""

import json
import re
from datetime import UTC, datetime

import pytest
from playwright.sync_api import Page, expect
from support.api import Api, commit_new_entries, last_entry_id, rows_by_name
from support.mcp import McpCall

from tests.support.docs_shots import GROUP, FixedValues, shot

## Wiki is far down the grid, so no other image shows its new revision date. The proposal is
## rejected and changes nothing.
EDITED = "Wiki"
NOTES = "Moved to the new wiki in November."
PROPOSED = "Customer CRM"
NEW_NAME = "CRM (customers)"
REASON = "the other business logins start with what they are"
## the newest instant the Change log shows
CHANGE_LOG_NEWEST = datetime(2026, 11, 17, 9, 43, 0, tzinfo=UTC)


def test_queue_proposal(unlocked_page: Page, api: Api, mcp_call: McpCall) -> None:
    page = unlocked_page
    switch = page.get_by_label("MCP", exact=True)
    switch.click()
    expect(switch).to_be_checked()

    items = json.loads(mcp_call("list_items", text=PROPOSED))["items"]
    item_id = next(item["id"] for item in items if item["name"] == PROPOSED)
    answer = json.loads(
        mcp_call("propose_rename_item", item_id=item_id, name=NEW_NAME, reason=REASON)
    )
    try:
        page.get_by_role("button", name=re.compile(r"^Queue\b")).click()
        card = page.get_by_test_id(f"queue-entry-{answer['entryId']}")
        expect(card).to_be_visible()
        checkbox = card.get_by_role("checkbox")
        checkbox.check()
        approve = page.get_by_role("button", name="Approve selected")
        expect(approve).to_be_enabled()
        ## the line with the entry's id and time, which change with every run
        card.get_by_text(re.compile(r"^#\d+ · from mcp")).evaluate(
            "line => line.setAttribute('data-shot-hide', '')"
        )
        shot(
            page,
            "explanation-queue-proposal",
            frame="box",
            around=[page.get_by_role("tablist", name="Queue entries"), card],
            highlight=[checkbox, approve],
        )
    finally:
        api.post("/api/queue/reject", {"ids": [answer["entryId"]]})
        switch.click()
        expect(switch).not_to_be_checked()
        api.delete("/api/mcp/token")


@pytest.mark.usefixtures("vault_put_back")
def test_change_log_row(unlocked_page: Page, api: Api) -> None:
    page = unlocked_page
    item = rows_by_name(api)[EDITED]
    after = last_entry_id(api)
    edit = {
        "kind": "setLeaf",
        "itemId": item["id"],
        "revisionDate": item["revisionDate"],
        "column": "notes",
        "value": NOTES,
    }
    api.post("/api/queue", {"operations": [edit]})
    commit_new_entries(api, after)

    fixed = FixedValues(CHANGE_LOG_NEWEST, GROUP)
    fixed.learn([api.get("/api/change-log"), api.get("/api/backups")])
    page.route(re.compile(r"/api/(change-log|backups)\b"), fixed.route)
    page.get_by_role("button", name="Change log", exact=True).click()
    log = page.get_by_role("table", name="Change log")
    ## newest first, a run without a reset shows the restores of earlier runs too
    edited = page.get_by_role("cell", name=EDITED, exact=True)
    entry = log.get_by_role("row").filter(has=edited).filter(has_text="setLeaf").first
    restore = entry.get_by_role("button", name="Restore")
    expect(restore).to_be_visible()
    header = log.locator("thead tr")
    ## no margin below, the next row would show cut in half
    shot(
        page,
        "explanation-change-log",
        frame="band",
        around=[header, entry],
        margin=(16, 0),
        highlight=[restore],
    )
