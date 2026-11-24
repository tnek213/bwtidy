"""The color mode menu in the app bar (docs/spec.md, Light and dark mode).

The setting is System, Light or Dark. Light and Dark are a cookie readable by the page, System
is no cookie and the browser's color scheme decides.
"""

from typing import Literal

import pytest
from playwright.sync_api import Page, expect
from support.pages import grid

COOKIE = "bwtidy_color_mode"
Scheme = Literal["dark", "light"]
DARK = "rgb(18, 18, 18)"
LIGHT = "rgb(255, 255, 255)"


def load_with_os(page: Page, scheme: Scheme) -> None:
    """Load the app again with the browser preferring SCHEME."""
    page.emulate_media(color_scheme=scheme)
    page.reload()
    grid(page)


def cookie_value(page: Page) -> str | None:
    cookies = [c for c in page.context.cookies() if c["name"] == COOKIE]
    return cookies[0]["value"] if cookies else None


def choose(page: Page, current: str, choice: str) -> None:
    """Open the color mode menu, which shows CURRENT, and pick CHOICE."""
    page.get_by_role("button", name=f"Color mode: {current}").click()
    page.get_by_role("menuitemradio", name=choice).click()
    expect(page.get_by_role("button", name=f"Color mode: {choice}")).to_be_visible()


@pytest.mark.parametrize(("scheme", "background"), [("dark", DARK), ("light", LIGHT)])
def test_system_is_the_default_and_the_browser_decides(
    unlocked_page: Page, scheme: Scheme, background: str
):
    page = unlocked_page
    load_with_os(page, scheme)

    expect(page.locator("body")).to_have_css("background-color", background)
    assert cookie_value(page) is None
    page.get_by_role("button", name="Color mode: System").click()
    for name, checked in [("System", "true"), ("Light", "false"), ("Dark", "false")]:
        item = page.get_by_role("menuitemradio", name=name)
        expect(item).to_have_attribute("aria-checked", checked)


def test_dark_and_light_are_kept_over_a_reload(unlocked_page: Page):
    page = unlocked_page
    load_with_os(page, "light")
    body = page.locator("body")
    grid_background = page.locator(".ag-root-wrapper")
    expect(body).to_have_css("background-color", LIGHT)
    light_grid = grid_background.evaluate("e => getComputedStyle(e).backgroundColor")

    choose(page, "System", "Dark")

    expect(body).to_have_css("background-color", DARK)
    expect(grid_background).not_to_have_css("background-color", light_grid)
    assert cookie_value(page) == "dark"

    page.reload()
    grid(page)
    expect(page.get_by_role("button", name="Color mode: Dark")).to_be_visible()
    expect(body).to_have_css("background-color", DARK)

    choose(page, "Dark", "Light")
    expect(body).to_have_css("background-color", LIGHT)
    expect(grid_background).to_have_css("background-color", light_grid)
    assert cookie_value(page) == "light"


def test_the_choice_wins_over_the_browser_and_system_gives_the_browser_back(unlocked_page: Page):
    page = unlocked_page
    load_with_os(page, "dark")
    expect(page.locator("body")).to_have_css("background-color", DARK)

    choose(page, "System", "Light")
    expect(page.locator("body")).to_have_css("background-color", LIGHT)
    assert cookie_value(page) == "light"

    page.reload()
    grid(page)
    expect(page.locator("body")).to_have_css("background-color", LIGHT)

    choose(page, "Light", "System")
    expect(page.locator("body")).to_have_css("background-color", DARK)
    assert cookie_value(page) is None

    page.reload()
    grid(page)
    expect(page.locator("body")).to_have_css("background-color", DARK)
    expect(page.get_by_role("button", name="Color mode: System")).to_be_visible()
