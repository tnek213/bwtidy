"""Cells that cannot hold a value are grey and say why on hover."""

import re
from typing import Literal

import pytest
from playwright.sync_api import Page, expect
from support.pages import cell, grid, hover_tooltip

UNAVAILABLE = re.compile(r"\bbw-unavailable-cell\b")
NOTE = "Door code"
LOGIN = "Code forge"


def background_alpha(color: str) -> float:
    """The alpha of a computed color: rgba(r, g, b, a) or color(srgb r g b / a), 1 when none."""
    channels = re.fullmatch(r"(?:rgba?|color)\((.*)\)", color)
    assert channels, color
    alpha = re.search(r"[,/]\s*([\d.]+)\s*$", channels.group(1))
    return float(alpha.group(1)) if alpha else 1.0


def test_a_notes_login_username_is_grey_and_says_why(unlocked_page: Page):
    page = unlocked_page
    username = cell(page, NOTE, "login.username")
    expect(username).to_have_class(UNAVAILABLE)

    expect(hover_tooltip(page, username)).to_have_text("Only login items have login.username.")

    username.dblclick()
    page.wait_for_timeout(500)
    expect(page.locator(".ag-cell-inline-editing")).to_have_count(0)
    expect(page.get_by_role("dialog")).to_have_count(0)
    ## a failed commit of an earlier test shows an alert of its own on every page
    expect(page.get_by_role("alert").filter(has_text="login.username")).to_have_count(0)


def test_a_logins_login_username_is_not_grey(unlocked_page: Page):
    username = cell(unlocked_page, LOGIN, "login.username")
    expect(username).to_be_visible()
    expect(username).not_to_have_class(UNAVAILABLE)


@pytest.mark.parametrize(("scheme", "alpha"), [("light", 0.014), ("dark", 0.028)])
def test_the_grey_keeps_35_percent_of_the_hover_tint(
    unlocked_page: Page, scheme: Literal["dark", "light"], alpha: float
):
    page = unlocked_page
    ## System is the default color mode, so the browser's color scheme decides
    page.emulate_media(color_scheme=scheme)
    page.reload()
    grid(page)
    username = cell(page, NOTE, "login.username")
    expect(username).to_have_class(UNAVAILABLE)
    color = username.evaluate("el => getComputedStyle(el).backgroundColor")
    assert abs(background_alpha(color) - alpha) <= 0.002, color
