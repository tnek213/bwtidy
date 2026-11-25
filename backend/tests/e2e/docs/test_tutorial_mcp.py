"""The tutorial Clean up with an AI agent (docs/tutorial/mcp.md): an MCP token made in the app,
MCP switched on, an agent's proposals in the Queue, Approve selected, and the write with its
backup in the Change log.

No AI client runs here: the test makes the proposals an agent would make for the tutorial's
prompt through the real MCP endpoint with the token of the dialog (mcp_caller), so the Queue shows
real proposals. The token is never printed. The test puts the vault back as it found it (put_back),
switches MCP off and removes the token, so the docs tests after it see the seeded vault. The
token's expiry and the port of the MCP URL differ from run to run, so the dialog shows fixed ones.
Times, backup names and backup sizes differ too, so the Change log shows fixed ones (FixedValues).
"""

import json
import re
from datetime import UTC, datetime

from playwright.sync_api import Page, expect
from support.api import Api
from support.elsewhere import active_ids, last_change_id, put_back
from support.mcp import McpCall, mcp_caller
from support.queue_page import APPROVE_TIMEOUT, approve_result, mcp_switch

from tests.support.docs_shots import GROUP, FixedValues, shot, wait_for_next_backup

## the logins in no folder that the agent puts in a folder, by folder (dev/dev-vault.md)
MOVES = {
    "Home": (
        ["Online bank", "Tax agency", "Health portal", "Shoe shop"],
        "household logins like the bank, the tax agency and shops are all in Home",
    ),
    "Work": (["Example Forge"], "a code forge account, like Code forge and Issue tracker in Work"),
}
## the newest instant the Change log shows
CHANGE_LOG_NEWEST = datetime(2026, 11, 17, 10, 31, 12, tzinfo=UTC)


def test_tutorial_mcp(unlocked_page: Page, api: Api, base_url: str) -> None:
    page = unlocked_page
    since = last_change_id(api)
    active_before = active_ids(api)
    try:
        token = make_token(page)
        switch_on(page)
        entries = propose(mcp_caller(base_url, token))
        review(page, api, entries)
    finally:
        pending = [entry["id"] for entry in api.get("/api/queue?status=pending")]
        if pending:
            api.post("/api/queue/reject", {"ids": pending})
        if mcp_switch(page).is_checked():
            mcp_switch(page).click()
            expect(mcp_switch(page)).not_to_be_checked()
        api.delete("/api/mcp/token")
        if put_back(api, since, active_before):
            api.delete("/api/session")


def make_token(page: Page) -> str:
    """The MCP token menu, the dialog that asks for its lifetime and the one that shows it."""
    page.get_by_role("button", name="MCP token", exact=True).click()
    menu = page.get_by_role("menu")
    create = menu.get_by_role("menuitem", name="Create", exact=True)
    header = page.locator("header").first
    shot(page, "tutorial-mcp-token-menu", frame="band", around=[header, menu], highlight=[create])
    create.click()
    ask = page.get_by_role("dialog", name="New MCP token")
    ask.get_by_role("button", name="Create", exact=True).click()
    dialog = page.get_by_role("dialog", name="MCP token", exact=True)
    field = dialog.get_by_label("Token", exact=True)
    expect(field).not_to_have_value("")
    token = field.input_value()
    dialog.get_by_label("MCP URL", exact=True).evaluate(
        "field => { field.value = 'https://bwtidy.example/mcp' }"
    )
    dialog.get_by_text(re.compile(r"It expires")).evaluate(
        "text => { text.textContent = text.textContent.replace("
        "/It expires .*$/, 'It expires 2/15/2027, 7:01:30 PM.') }"
    )
    copy_token = dialog.get_by_role("button", name="Copy token", exact=True)
    copy_url = dialog.get_by_role("button", name="Copy mcp url", exact=True)
    shot(
        page,
        "tutorial-mcp-token",
        frame="box",
        around=[dialog],
        highlight=[(copy_token, "1"), (copy_url, "2")],
    )
    dialog.get_by_role("button", name="Close").click()
    expect(dialog).to_have_count(0)
    return token


def switch_on(page: Page) -> None:
    switch = mcp_switch(page)
    switch.click()
    expect(switch).to_be_checked()
    page.mouse.move(800, 500)
    header = page.locator("header").first
    shot(page, "tutorial-mcp-switch", frame="band", around=[header], highlight=[switch])


def propose(mcp_call: McpCall) -> list[int]:
    """What an agent does for the tutorial's prompt: list the items, one proposal per folder."""
    items = json.loads(mcp_call("list_items"))["items"]
    ids = {item["name"]: item["id"] for item in items}
    entries = []
    for folder, (names, reason) in MOVES.items():
        answer = json.loads(
            mcp_call(
                "propose_move_to_folder",
                item_ids=[ids[name] for name in names],
                folder=folder,
                reason=reason,
            )
        )
        entries.append(answer["entryId"])
    return entries


def review(page: Page, api: Api, entries: list[int]) -> None:
    page.get_by_role("button", name=re.compile(r"^Queue\b")).click()
    cards = [page.get_by_test_id(f"queue-entry-{entry}") for entry in entries]
    for card in cards:
        expect(card).to_be_visible()
    select_all = page.get_by_role("checkbox", name="Select all")
    select_all.check()
    approve = page.get_by_role("button", name="Approve selected")
    expect(approve).to_be_enabled()
    for card in cards:
        ## the line with the entry's id and time, which change with every run
        card.get_by_text(re.compile(r"^#\d+ · from mcp")).evaluate(
            "line => line.setAttribute('data-shot-hide', '')"
        )
    tabs = page.get_by_role("tablist", name="Queue entries")
    ## the last proposal at the bottom of the window, the tabs above it stay in view
    cards[-1].evaluate("card => card.scrollIntoView({ block: 'end' })")
    shot(
        page,
        "tutorial-mcp-queue",
        frame="band",
        around=[tabs, cards[-1]],
        highlight=[(select_all, "1"), (approve, "2")],
    )
    approve.click()
    expect(approve_result(page)).to_contain_text("Applied 2 entries", timeout=APPROVE_TIMEOUT)
    api.wait_saved()
    wait_for_next_backup(api)

    fixed = FixedValues(CHANGE_LOG_NEWEST, GROUP)
    fixed.learn([api.get("/api/change-log"), api.get("/api/backups")])
    page.route(re.compile(r"/api/(change-log|backups)\b"), fixed.route)
    page.get_by_role("button", name="Change log", exact=True).click()
    log = page.get_by_role("table", name="Change log")
    written = log.locator("tbody tr").filter(has_text="Move to folder")
    expect(written).to_have_count(len(MOVES))
    used = written.first.locator("td").nth(5)
    backup = log.get_by_role("link", name=used.inner_text(), exact=True)
    shot(
        page,
        "tutorial-mcp-change-log",
        frame="band",
        around=[log.locator("thead"), backup],
        highlight=[used, backup],
    )
