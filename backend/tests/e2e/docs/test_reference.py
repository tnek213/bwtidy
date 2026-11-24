"""The cell menu and the header menu of the grid, for docs/reference/ui.md (docs/spec.md, Grid).

Each menu is opened and closed again with Escape, so the vault stays as seeded.
"""

from playwright.sync_api import Page, expect
from support.pages import cell, settle

from tests.support.docs_shots import shot

LOGIN = "Example Mail"
## a note with the hidden field Password, which the near duplicate password has too
FIELD_ITEM = "Old webshop"
FIELD_COLUMN = "fields.Password"


def test_cell_menu(unlocked_page: Page) -> None:
    page = unlocked_page
    password = cell(page, LOGIN, "login.password")
    settle(page)
    password.click(button="right")
    menu = page.get_by_role("menu")
    expect(menu.get_by_role("menuitem", name="Generate new value")).to_be_visible()
    shot(page, "reference-cell-menu", frame="box", around=[password, menu])
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()


def test_header_menu(unlocked_page: Page) -> None:
    page = unlocked_page
    page.get_by_label("Search").fill(FIELD_ITEM)
    name = page.locator(".ag-cell[col-id='name']").get_by_text(FIELD_ITEM, exact=True)
    expect(name).to_have_count(1)
    ## the field's column is far to the right, with the empty ones hidden it is in view
    page.get_by_label("Hide empty columns").check()
    settle(page)
    button = page.get_by_role("button", name=f"Actions for {FIELD_COLUMN}", exact=True)
    button.click()
    menu = page.get_by_role("menu")
    expect(menu.get_by_role("menuitem", name="Merge near duplicates…")).to_be_visible()
    header = page.locator(f".ag-header-cell[col-id='{FIELD_COLUMN}']")
    shot(page, "reference-header-menu", frame="box", around=[header, menu])
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()
