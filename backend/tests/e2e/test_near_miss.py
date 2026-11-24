"""Hovering a near miss in the template column lists what the item lacks."""

import time
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import Api
from support.pages import grid, hover_tooltip

from tests.support.section_changes import template_changes

SAVE_TIMEOUT = 60

## Media PC (a note) has user and user password but no BIOS password: 3/4
COMPUTER = {
    "name": "E2E computer",
    "type": 2,
    "coordinates": [
        {"kind": "field", "name": "user", "fieldType": 0},
        {"kind": "field", "name": "user password", "fieldType": 1},
        {"kind": "field", "name": "BIOS password", "fieldType": 1},
    ],
}
## ThinkPad X1 (a login) has all three fields, only its type is wrong: 3/4
DISK = {
    "name": "E2E disk",
    "type": 2,
    "coordinates": [
        {"kind": "field", "name": "LUKS", "fieldType": 1},
        {"kind": "field", "name": "BIOS Password", "fieldType": 1},
        {"kind": "field", "name": "User", "fieldType": 0},
    ],
}


def save_templates(api: Api, templates: list[dict[str, Any]]) -> None:
    """Save the templates and wait until the auto-commit wrote them."""
    current = api.get("/api/templates")
    changes = template_changes(current["templates"], templates)
    if changes:
        api.put("/api/templates", {"changes": changes, "revisionDate": current["revisionDate"]})
    deadline = time.monotonic() + SAVE_TIMEOUT
    while api.get("/api/templates")["saving"]:
        assert time.monotonic() < deadline, "the templates save was not committed in time"
        time.sleep(0.5)


@pytest.fixture
def templates_saved(unlocked_page: Page, api: Api) -> Iterator[Page]:
    """The two templates saved and the grid reloaded with their matches, the old list back after."""
    before = api.get("/api/templates")["templates"]
    save_templates(api, [*before, COMPUTER, DISK])
    unlocked_page.reload()
    grid(unlocked_page)
    yield unlocked_page
    save_templates(api, before)


def template_cell(page: Page, item: str) -> Locator:
    """The template cell of an item, found through the row of its name cell."""
    page.get_by_label("Search").fill(item)
    name = page.locator(".ag-cell[col-id='name']").get_by_text(item, exact=True)
    expect(name).to_have_count(1)
    row = name.locator("xpath=ancestor::*[@row-id][1]").get_attribute("row-id")
    return page.locator(f".ag-row[row-id='{row}'] .ag-cell[col-id='template']")


def test_hovering_a_near_miss_lists_the_missing_column(templates_saved: Page):
    page = templates_saved
    cell = template_cell(page, "Media PC")
    expect(cell).to_have_text("E2E computer 3/4")
    tooltip = hover_tooltip(page, cell)
    expect(tooltip).to_contain_text("Missing for E2E computer:")
    expect(tooltip).to_contain_text("• BIOS password")
    expect(tooltip).not_to_contain_text("Type:")


def test_hovering_a_near_miss_of_another_type_names_the_type(templates_saved: Page):
    page = templates_saved
    cell = template_cell(page, "ThinkPad X1")
    expect(cell).to_have_text("E2E disk 3/4")
    tooltip = hover_tooltip(page, cell)
    expect(tooltip).to_contain_text("Missing for E2E disk:")
    expect(tooltip).to_contain_text("• Type: login, the template wants note")
