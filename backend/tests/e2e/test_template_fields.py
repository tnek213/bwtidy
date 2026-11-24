"""The template editor picks fields the vault has, and warns about a field no item has.

The test leaves the stored templates as it found them.
"""

import time
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from support.api import COMMIT_TIMEOUT, Api
from support.pages import add_coordinate, add_new_field, coordinate_row, pick

from tests.support.section_changes import template_changes

UNKNOWN = "No item has this field"
NEW_FIELD = "E2E field nobody has"
NAME = "E2E picked fields"
EXPECTED: dict[str, Any] = {
    "name": NAME,
    "type": 2,
    "coordinates": [
        {"kind": "field", "name": "BIOS Password", "fieldType": 1},
        {"kind": "field", "name": "MSDM Windows Key", "fieldType": 0},
        {"kind": "field", "name": NEW_FIELD, "fieldType": 0},
    ],
}


def wait_saved(api: Api) -> dict[str, Any]:
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while (doc := api.get("/api/templates"))["saving"]:
        assert time.monotonic() < deadline, "the templates save was not committed"
        time.sleep(0.5)
    return doc


@pytest.fixture
def templates_kept(unlocked_page: Page, api: Api) -> Iterator[list[dict[str, Any]]]:
    """The stored templates before the test, saved back afterwards when they changed."""
    before = wait_saved(api)["templates"]
    yield before
    doc = wait_saved(api)
    if doc["templates"] != before:
        changes = template_changes(doc["templates"], before)
        api.put("/api/templates", {"changes": changes, "revisionDate": doc["revisionDate"]})
        restored = wait_saved(api)["templates"] == before
        assert restored, "the templates were not restored"


def test_pick_two_vault_fields_and_add_a_new_one(
    unlocked_page: Page, api: Api, templates_kept: list[dict[str, Any]]
):
    page = unlocked_page
    page.get_by_role("button", name="Types", exact=True).click()
    page.get_by_role("button", name="New template").click()
    editor = page.get_by_role("dialog", name="New template")
    editor.get_by_label("Name", exact=True).fill(NAME)
    pick(editor, "Type", "Secure note")

    ## the options name each field's type, and picking one takes it over
    add_coordinate(editor, "fields.BIOS Password, Hidden")
    expect(coordinate_row(editor, 1).get_by_role("combobox", name="Field type")).to_have_text(
        "Hidden"
    )
    add_coordinate(editor, "fields.MSDM Windows Key, Text")
    add_new_field(editor, NEW_FIELD)
    expect(coordinate_row(editor, 3).get_by_text("Not in the vault yet")).to_be_visible()

    expect(coordinate_row(editor, 3)).to_have_attribute("title", UNKNOWN)
    for number in (1, 2):
        expect(coordinate_row(editor, number)).not_to_have_attribute("title", UNKNOWN)

    editor.get_by_role("button", name="Save").click()
    expect(editor).to_be_hidden()
    saved = wait_saved(api)["templates"] == [*templates_kept, EXPECTED]
    assert saved, "the template was not saved with the picked fields"

    row = (
        page.get_by_role("table", name="Templates")
        .get_by_role("row")
        .filter(has=page.get_by_role("cell", name=NAME, exact=True))
    )
    warned = row.get_by_title(UNKNOWN)
    expect(warned).to_have_count(1)
    expect(warned).to_have_text(f"fields.{NEW_FIELD}")
    expect(row.get_by_text("fields.BIOS Password", exact=True)).to_be_visible()
