"""The Layout setting of the Settings page (docs/spec.md, Settings): Compact shrinks the theme
spacing to 5 px (so the Change log's page padding goes from 16 px to 10 px, 5/8) and makes the app
bar's toolbar dense (lower). It is kept per browser in localStorage under bwtidy.layout, so a reload
keeps it.

Each test gets a fresh browser context (conftest's context fixture: no localStorage from another
test), and this test sets the layout back to Normal before it ends anyway. Nothing here touches the
vault.
"""

from playwright.sync_api import Locator, Page, expect
from support.pages import grid, pick


def change_log_box(page: Page) -> Locator:
    """Open the Change log and answer its padded page container (the Box with p: 2)."""
    page.get_by_role("button", name="Change log", exact=True).click()
    heading = page.get_by_role("heading", name="Change log", exact=True)
    expect(heading).to_be_visible()
    return heading.locator("xpath=..")


def padding_top(box: Locator) -> float:
    return box.evaluate("e => parseFloat(getComputedStyle(e).paddingTop)")


def toolbar_height(page: Page) -> float:
    box = page.locator(".MuiToolbar-root").first.bounding_box()
    assert box is not None
    return box["height"]


def stored_layout(page: Page) -> str | None:
    return page.evaluate("() => localStorage.getItem('bwtidy.layout')")


def choose_layout(page: Page, option: str) -> None:
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("menuitem", name="Settings").click()
    pick(page, "Layout", option)
    expect(page.get_by_role("combobox", name="Layout")).to_have_text(option)


def test_compact_shrinks_the_padding_and_the_app_bar_and_a_reload_keeps_it(unlocked_page: Page):
    page = unlocked_page

    normal_padding = padding_top(change_log_box(page))
    normal_bar = toolbar_height(page)
    assert normal_padding == 16

    choose_layout(page, "Compact")
    assert stored_layout(page) == "compact"

    compact_padding = padding_top(change_log_box(page))
    compact_bar = toolbar_height(page)
    assert compact_padding == 10
    assert compact_padding == normal_padding * 5 / 8
    assert compact_bar < normal_bar

    page.reload()
    grid(page)
    assert stored_layout(page) == "compact"
    assert padding_top(change_log_box(page)) == 10
    assert toolbar_height(page) < normal_bar

    choose_layout(page, "Normal")
    assert stored_layout(page) is None
    assert padding_top(change_log_box(page)) == 16
    assert toolbar_height(page) == normal_bar
