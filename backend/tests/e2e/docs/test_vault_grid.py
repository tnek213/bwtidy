"""The unlocked vault grid, the screenshot of README.md (docs/spec.md, Grid)."""

from playwright.sync_api import Page, expect

from tests.support.docs_shots import shot, wait_for_grid


def test_vault_grid(unlocked_page: Page) -> None:
    page = unlocked_page
    expect(page.get_by_role("button", name="Columns", exact=True)).to_be_visible()
    wait_for_grid(page)
    ## the pointer left on the grid after the unlock shades a row and a column
    page.mouse.move(1000, 150)
    shot(page, "vault-grid")
