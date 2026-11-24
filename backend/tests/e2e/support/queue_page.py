"""The MCP switch and the review queue's page, for the tests that propose and approve."""

import json
import re
from typing import Any

from playwright.sync_api import Locator, Page, expect

from support.mcp import McpCall

## an approval makes a backup (about 18 s on the dev vault), writes and reloads the vault
APPROVE_TIMEOUT = 120_000


def mcp_switch(page: Page) -> Locator:
    return page.get_by_label("MCP", exact=True)


def enable_mcp(page: Page) -> None:
    """Click the switch and wait for the server's answer, which sets its state."""
    mcp_switch(page).click()
    expect(mcp_switch(page)).to_be_checked()


def item_id(mcp_call: McpCall, name: str) -> str:
    """The id of the item with this name, as MCP lists it."""
    items = json.loads(mcp_call("list_items", text=name))["items"]
    found = [item["id"] for item in items if item["name"] == name]
    assert len(found) == 1, f"{len(found)} items named {name}"
    return found[0]


def open_queue(page: Page) -> None:
    page.get_by_role("button", name=re.compile(r"^Queue\b")).click()
    expect(page.get_by_role("heading", name="Queue", exact=True)).to_be_visible()


def select(page: Page, answer: dict[str, Any]) -> None:
    """Tick a pending entry, its checkbox is named by its summary."""
    page.get_by_role("checkbox", name=answer["summary"], exact=True).check()


def approve_result(page: Page) -> Locator:
    return page.get_by_test_id("approve-result")
