"""The column chooser with its two boxes per column (docs/spec.md, Grid, the Columns button): the
checkbox on the left shows or hides the column, the box on the right selects it for a merge."""

import re

from playwright.sync_api import Page, expect

from tests.support.docs_shots import shot

COLUMN = "login.username"


def test_column_chooser(unlocked_page: Page) -> None:
    page = unlocked_page
    ## the open chooser hides the rest of the page from the accessibility tree
    button = page.get_by_role("button", name="Columns", exact=True, include_hidden=True)
    button.click()
    chooser = page.locator(".MuiPopover-paper").filter(has=page.get_by_label("Find column"))
    group = chooser.get_by_role("group", name="Login")
    login = group.locator("label").first
    expect(login).to_be_visible()
    shown = group.locator("label").filter(has_text=re.compile(f"^{re.escape(COLUMN)}$"))
    merge = group.get_by_role("checkbox", name=f"Select {COLUMN} for merge")
    expect(merge).not_to_be_checked()
    ## the end of the Item group, then Login and its first columns
    login.evaluate("e => e.scrollIntoView({block: 'center'})")
    shot(
        page,
        "column-chooser",
        frame="box",
        around=[button, chooser],
        highlight=[(login, "1"), (shown, "2"), (merge, "3")],
    )
