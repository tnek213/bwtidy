"""The column chooser with its group checkboxes (docs/spec.md, Grid, the Columns button)."""

from playwright.sync_api import Page, expect

from tests.support.docs_shots import shot


def test_column_chooser(unlocked_page: Page) -> None:
    page = unlocked_page
    ## the open chooser hides the rest of the page from the accessibility tree
    button = page.get_by_role("button", name="Columns", exact=True, include_hidden=True)
    button.click()
    chooser = page.locator(".MuiPopover-paper").filter(has=page.get_by_label("Find column"))
    login = chooser.get_by_role("group", name="Login").locator("label").first
    expect(login).to_be_visible()
    ## the end of the Item group, then Login and its first columns
    login.evaluate("e => e.scrollIntoView({block: 'center'})")
    shot(page, "column-chooser", frame="box", around=[button, chooser], highlight=[login])
