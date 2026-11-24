"""The MCP token of the web UI and a caller of the MCP tools over streamable HTTP."""

import asyncio
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx2 as httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from playwright.sync_api import Page, expect

McpCall = Callable[..., str]


def create_mcp_token(page: Page, expiry: str = "90 days") -> str:
    """Create (or replace) the MCP token with the app bar's MCP token menu and answer it.

    expiry is the lifetime picked in the dialog that asks for it. The dialog that shows the
    token once is closed again. Never print the token.
    """
    page.get_by_role("button", name="MCP token", exact=True).click()
    page.get_by_role("menuitem", name=re.compile(r"^(Create|Replace)$")).click()
    ask = page.get_by_role("dialog", name="New MCP token")
    ask.get_by_role("radio", name=expiry, exact=True).check()
    ask.get_by_role("button", name="Create", exact=True).click()
    expect(ask).to_have_count(0)
    dialog = page.get_by_role("dialog", name="MCP token")
    field = dialog.get_by_label("Token", exact=True)
    expect(field).not_to_have_value("")
    token = field.input_value()
    dialog.get_by_role("button", name="Close").click()
    expect(dialog).to_have_count(0)
    return token


def mcp_caller(base_url: str, token: str) -> McpCall:
    """Call MCP tools over streamable HTTP at /mcp with this bearer token, answering the text.

    The gateway finds the account by the token, like for any MCP client without a cookie.
    Runs on a thread of its own, since Playwright's sync API keeps an event loop running on
    the test's thread.
    """

    async def call(tool: str, arguments: dict[str, Any]) -> str:
        http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=300)
        transport = streamable_http_client(f"{base_url}/mcp", http_client=http)
        async with http, Client(transport) as client:
            result = await client.call_tool(tool, arguments)
        texts = [c.text for c in result.content if isinstance(c, TextContent)]
        ## the tool's own error text names no value, so it is safe to show on a failure
        assert not result.is_error, texts
        return "".join(texts)

    def run(tool: str, **arguments: Any) -> str:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(call(tool, arguments))).result()

    return run
