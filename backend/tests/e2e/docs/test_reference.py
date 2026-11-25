"""The Vault page's parts for docs/reference/ui.md (docs/spec.md, Grid): the toolbar, a few
cells, the cell menu, the header menu, the selection bar and the New item dialog.

Each menu and dialog is closed again and the ticked rows cleared, so the vault stays as seeded.
"""

from playwright.sync_api import Page, expect
from support.api import Api, rows_by_name
from support.pages import cell, pick, row, settle

from tests.support.docs_shots import shot

LOGIN = "Example Mail"
## a note with the hidden field Password, which the near duplicate password has too
FIELD_ITEM = "Old webshop"
FIELD_COLUMN = "fields.Password"
TICKED = ("Bike club", "Car insurance")


def test_toolbar(unlocked_page: Page) -> None:
    page = unlocked_page
    new_item = page.get_by_role("button", name="New item", exact=True)
    columns = page.get_by_role("button", name="Columns", exact=True)
    expect(columns).to_be_visible()
    shot(page, "reference-toolbar", frame="band", around=[new_item, columns], margin=12)


def test_cells(unlocked_page: Page) -> None:
    page = unlocked_page
    password = cell(page, LOGIN, "login.password")
    ## the login columns are far to the right, with the empty ones hidden they are in view
    page.get_by_label("Hide empty columns").check()
    settle(page)
    password.scroll_into_view_if_needed()
    reveal = password.get_by_role("button", name="Reveal login.password")
    expect(reveal).to_be_visible()
    settle(page)
    header = page.locator(".ag-header-cell[col-id='login.username']")
    uris = cell(page, LOGIN, "login.uris")
    shot(page, "reference-cells", frame="box", around=[header, uris], highlight=[reveal])


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


def test_selection_bar(unlocked_page: Page, api: Api) -> None:
    page = unlocked_page
    rows = rows_by_name(api)
    for name in TICKED:
        row(page, rows[name]["id"]).get_by_role("checkbox").check()
    bar = page.get_by_role("toolbar", name="Selected items")
    expect(bar.get_by_role("status")).to_have_text(f"{len(TICKED)} selected")
    clear = bar.get_by_role("button", name="Clear")
    shot(page, "reference-selection-bar", frame="box", around=[bar.get_by_role("status"), clear])
    clear.click()
    expect(bar).to_be_hidden()


def test_new_item_dialog(unlocked_page: Page) -> None:
    page = unlocked_page
    page.get_by_role("button", name="New item", exact=True).click()
    dialog = page.get_by_role("dialog", name="New item")
    pick(dialog, "Start from", "Login")
    expect(dialog.get_by_role("checkbox", name="Generate a password")).to_be_checked()
    shot(page, "reference-new-item", frame="box", around=[dialog])
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
