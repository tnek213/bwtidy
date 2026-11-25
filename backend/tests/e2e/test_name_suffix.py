"""The name cell shows the type and the template while their columns are out of view."""

import time
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import COMMIT_TIMEOUT, Api
from support.pages import cell, grid, point_at, shown_tooltip

from tests.support.section_changes import template_changes

## Code forge is a login with username, password and TOTP, so it matches this template
TEMPLATE = {
    "name": "E2E suffix",
    "type": 1,
    "coordinates": [
        {"kind": "path", "path": "login.username"},
        {"kind": "path", "path": "login.password"},
        {"kind": "path", "path": "login.totp"},
    ],
}
ITEM = "Code forge"
## AG Grid shows its tooltip after 500 ms
TOOLTIP_WAIT = 1000


def save_templates(api: Api, templates: list[dict[str, Any]]) -> None:
    """Save the templates and wait until the auto-commit wrote them."""
    current = api.get("/api/templates")
    changes = template_changes(current["templates"], templates)
    if changes:
        api.put("/api/templates", {"changes": changes, "revisionDate": current["revisionDate"]})
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while api.get("/api/templates")["saving"]:
        assert time.monotonic() < deadline, "the templates save was not committed in time"
        time.sleep(0.5)


@pytest.fixture
def suffix_page(unlocked_page: Page, api: Api) -> Iterator[Page]:
    """The grid with TEMPLATE saved and its matches loaded, the old templates back after."""
    before = api.get("/api/templates")["templates"]
    save_templates(api, [*before, TEMPLATE])
    unlocked_page.reload()
    grid(unlocked_page)
    yield unlocked_page
    save_templates(api, before)


def header(page: Page, column: str) -> Locator:
    return page.locator(f'.ag-header-row-column .ag-header-cell[col-id="{column}"]')


def set_columns(page: Page, columns: list[str], visible: bool) -> None:
    """Tick or untick the columns in the column chooser, then close it."""
    page.get_by_role("button", name="Columns").click()
    chooser = page.get_by_role("presentation").filter(has=page.get_by_label("Find column"))
    for column in columns:
        chooser.get_by_label(column, exact=True).set_checked(visible)
    page.keyboard.press("Escape")
    ## the popover fades out, its backdrop would take the mouse until it is gone
    expect(page.get_by_label("Find column")).to_have_count(0)


def test_hidden_type_and_template_show_after_the_name(suffix_page: Page):
    page = suffix_page
    name = cell(page, ITEM, "name")
    expect(name.locator("[data-suffix]")).to_have_count(0)

    set_columns(page, ["type", "template"], visible=False)
    expect(header(page, "type")).to_have_count(0)
    expect(header(page, "template")).to_have_count(0)

    expect(name.get_by_text(ITEM, exact=True)).to_be_visible()
    expect(name.locator("[data-suffix='template']")).to_have_text("E2E suffix")
    icon = name.get_by_role("img", name="Login")
    point_at(page, icon)
    expect(page.get_by_role("tooltip")).to_have_text("Login")
    ## the name cell's own tooltip stays away while the pointer is on the icon
    page.wait_for_timeout(TOOLTIP_WAIT)
    expect(shown_tooltip(page)).to_have_count(0)

    set_columns(page, ["type", "template"], visible=True)
    expect(header(page, "type")).to_be_visible()
    expect(name.locator("[data-suffix]")).to_have_count(0)
    expect(name).to_have_text(ITEM)


def test_a_type_column_scrolled_out_of_view_shows_after_the_name(suffix_page: Page):
    page = suffix_page
    name = cell(page, ITEM, "name")
    expect(header(page, "type")).to_be_visible()
    expect(name.locator("[data-suffix]")).to_have_count(0)

    scrollbar = page.locator(".ag-body-horizontal-scroll-viewport")
    scrollbar.evaluate("element => { element.scrollLeft = element.scrollWidth }")
    expect(name.get_by_role("img", name="Login")).to_be_visible()
    ## the template column is pinned, so it never scrolls away
    expect(name.locator("[data-suffix='template']")).to_have_count(0)

    scrollbar.evaluate("element => { element.scrollLeft = 0 }")
    expect(name.locator("[data-suffix]")).to_have_count(0)
