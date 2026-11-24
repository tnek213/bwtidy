"""The right-click menu groups its entries under Cell and Row."""

from playwright.sync_api import Page, expect
from support.pages import cell

LOGIN = "Code forge"
ROW_ENTRIES = ["Row", "Add field", "Convert to template…", "Merge into item…", "Move to trash"]


def menu_lines(page: Page):
    ## a selector list matches in document order, so this is the menu top to bottom
    return page.get_by_role("menu").locator(".MuiListSubheader-root, [role='menuitem']")


def test_a_username_cell_shows_cell_and_row(unlocked_page: Page):
    page = unlocked_page
    cell(page, LOGIN, "login.username").click(button="right")

    expect(menu_lines(page)).to_have_text(["Cell", "Edit value", "Remove value", *ROW_ENTRIES])


def test_the_template_cell_shows_row_only(unlocked_page: Page):
    page = unlocked_page
    cell(page, LOGIN, "template").click(button="right")

    expect(menu_lines(page)).to_have_text(ROW_ENTRIES)
    page.get_by_role("menuitem", name="Convert to template…").click()
    expect(page.get_by_role("dialog", name=f"Convert {LOGIN} to a template")).to_be_visible()


def test_shift_f10_on_the_template_cell_opens_the_same_menu(unlocked_page: Page):
    page = unlocked_page
    cell(page, LOGIN, "template").click()
    page.keyboard.press("Shift+F10")

    expect(menu_lines(page)).to_have_text(ROW_ENTRIES)
