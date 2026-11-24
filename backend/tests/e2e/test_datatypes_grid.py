"""Format matches in the grid (docs/spec.md, Grid and Datatypes).

A template with a PIN datatype on a text field: an item with a conforming value matches it, a value
off its format shows a live warning, still saves and makes the item a near miss with a bullet.
The field, the template and the datatype are taken out again.
"""

import re
import time
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import COMMIT_TIMEOUT, Api, rows_by_name
from support.pages import grid, hover_tooltip, item_cell, pick, search
from support.vault_items import save_templates, wait_templates_saved

from tests.support.section_changes import add_datatype, datatype_changes

ITEM = "Door code"
FIELD = "E2E PIN"
COLUMN = f"fields.{FIELD}"
TEMPLATE = "E2E PIN note"
DATATYPE = "E2E PIN"
PARTIAL = re.compile(r"\bbw-format-partial\b")
FULL = re.compile(r"\bbw-format-full\b")
OFF_TEXT = "matches format 80%, expected a digit at position 1"
BULLET = f"• {FIELD}: matches format 80% (1 change at position 1, expected a digit)"
COMMIT_MS = COMMIT_TIMEOUT * 1000


def wait_datatypes_saved(api: Api) -> list[dict[str, Any]]:
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while (doc := api.get("/api/datatypes"))["saving"]:
        assert time.monotonic() < deadline, "the datatypes save was not committed"
        time.sleep(0.5)
    return doc["datatypes"]


def write_field(api: Api, operation: dict[str, Any]) -> None:
    """Write one change to the item at once, past the queue."""
    row = rows_by_name(api)[ITEM]
    api.post(
        "/api/operations",
        {"operations": [{"itemId": row["id"], "revisionDate": row["revisionDate"], **operation}]},
        timeout=COMMIT_MS,
    )


@pytest.fixture
def pin_item(unlocked_page: Page, api: Api) -> Iterator[str]:
    """The item's id, with the field filled and the datatype and template stored."""
    datatypes = wait_datatypes_saved(api)
    templates = wait_templates_saved(api)["templates"]
    pin = {
        "name": DATATYPE,
        "pattern": r"\d{4,8}",
        "description": "",
        "ignoreCase": False,
        "examples": ["1234"],
        "counterExamples": ["12a4"],
    }
    api.put("/api/datatypes", {"changes": [add_datatype(pin)]})
    saved = wait_datatypes_saved(api)
    datatype_id = next(datatype["id"] for datatype in saved if datatype["name"] == DATATYPE)
    coordinate = {"kind": "field", "name": FIELD, "fieldType": 0, "datatype": datatype_id}
    save_templates(api, [*templates, {"name": TEMPLATE, "type": 2, "coordinates": [coordinate]}])
    write_field(api, {"kind": "setLeaf", "column": COLUMN, "value": "1234"})
    unlocked_page.reload()
    grid(unlocked_page)
    yield rows_by_name(api)[ITEM]["id"]
    api.wait_saved()
    write_field(api, {"kind": "removeLeaf", "column": COLUMN})
    save_templates(api, templates)
    changes = datatype_changes(wait_datatypes_saved(api), datatypes)
    if changes:
        api.put("/api/datatypes", {"changes": changes})
        wait_datatypes_saved(api)


def edit(page: Page, target: Locator, value: str, live: str) -> None:
    """Open the cell's editor, type the value, wait for the live text and save with Enter."""
    target.click()
    page.keyboard.press("Enter")
    editor = page.locator(".ag-popup-editor")
    editor.locator("input").fill(value)
    expect(editor.get_by_role("status")).to_have_text(live)
    page.keyboard.press("Enter")
    expect(editor).to_have_count(0)


def test_a_value_off_its_format_warns_saves_and_is_a_near_miss(
    unlocked_page: Page, api: Api, pin_item: str
):
    page = unlocked_page
    search(page, ITEM)
    template = item_cell(page, pin_item, "template")
    field = item_cell(page, pin_item, COLUMN)
    expect(template).to_have_text(TEMPLATE)
    expect(field).to_have_class(FULL)

    edit(page, field, " 1234", OFF_TEXT)
    api.wait_saved()
    expect(template).to_have_text(f"{TEMPLATE} 2.80/3", timeout=COMMIT_MS)
    expect(field).to_have_class(PARTIAL)

    tooltip = hover_tooltip(page, template)
    expect(tooltip).to_contain_text(f"Off format for {TEMPLATE}:")
    expect(tooltip).to_contain_text(BULLET)

    pick(page, "Format", "Off their format")
    expect(template).to_be_visible()
    pick(page, "Format", "All")

    edit(page, field, "1234", "matches format")
    api.wait_saved()
    expect(template).to_have_text(TEMPLATE, timeout=COMMIT_MS)
    expect(field).to_have_class(FULL)
