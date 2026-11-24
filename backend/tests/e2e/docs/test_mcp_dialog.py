"""The dialog that shows a new MCP token (docs/spec.md, MCP token dialog) for docs/how-to/mcp.md.

The token shown is a dev token of the docs run. The dialog gives its text field data-shot-hide,
so the shot hides it. The token is removed at the end, so the app is as seeded afterwards.

The expiry time and the port of the URL differ from run to run, so the test writes fixed ones
into the dialog's text before the shot (the dialog is closed right after, React never redraws it).
"""

import re

from playwright.sync_api import Page, expect
from support.api import Api

from tests.support.docs_shots import shot


def test_mcp_token_dialog(unlocked_page: Page, api: Api) -> None:
    page = unlocked_page
    try:
        page.get_by_role("button", name="MCP token", exact=True).click()
        page.get_by_role("menuitem", name="Create", exact=True).click()
        ask = page.get_by_role("dialog", name="New MCP token")
        ask.get_by_role("button", name="Create", exact=True).click()
        dialog = page.get_by_role("dialog", name="MCP token", exact=True)
        link = dialog.get_by_role("link", name="How to connect your MCP client")
        expect(link).to_be_visible()
        expect(dialog.get_by_label("Token", exact=True)).not_to_have_value("")
        dialog.get_by_label("MCP URL", exact=True).evaluate(
            "field => { field.value = 'https://bwtidy.example/mcp' }"
        )
        dialog.get_by_text(re.compile(r"It expires")).evaluate(
            "text => { text.textContent = text.textContent.replace("
            "/It expires .*$/, 'It expires 2/15/2027, 7:01:30 PM.') }"
        )
        shot(page, "mcp-token-dialog", frame="box", around=[dialog], highlight=[link])
        dialog.get_by_role("button", name="Close").click()
        expect(dialog).to_have_count(0)
    finally:
        api.delete("/api/mcp/token")
