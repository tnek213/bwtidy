"""The to-fill list in the grid (docs/spec.md, The tool note and Grid).

A new item from a template with a hidden field goes on the list, the chip filters the grid to
it, and filling the marked cell takes it off. Mark to fill in and Unmark act on an empty secret
cell. The new item goes to the trash again (and the file deletes it for good at its end,
file_put_back) and the template is taken out, and the tests count from what the list holds
already, so they need no empty list and leave it as it was.
"""

import re
import time
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from support.api import COMMIT_TIMEOUT, Api, commit_new_entries, last_entry_id, rows_by_name
from support.pages import grid, hover_tooltip, item_cell, pick, row, search
from support.vault_items import NAME, new_item_dialog, save_templates, trash, wait_templates_saved

pytestmark = pytest.mark.usefixtures("file_put_back")

SECRET = "fields.root password"
TEMPLATE = {
    "name": "E2E to fill",
    "type": 2,
    "coordinates": [
        {"kind": "field", "name": "host", "fieldType": 0},
        {"kind": "field", "name": "root password", "fieldType": 1},
    ],
}
MARKED = re.compile(r"\bbw-to-fill-cell\b")
TOOLTIP = "Fill this in: double click to set it"


@pytest.fixture
def with_template(unlocked_page: Page, api: Api) -> Iterator[Page]:
    """The grid with TEMPLATE stored beside the stored ones, which are put back afterwards."""
    before = wait_templates_saved(api)["templates"]
    save_templates(api, [*before, TEMPLATE])
    unlocked_page.reload()
    grid(unlocked_page)
    yield unlocked_page
    save_templates(api, before)


def listed_count(api: Api) -> int:
    return sum(len(columns) for columns in api.get("/api/grid")["toFill"].values())


def chip_named(page: Page) -> Any:
    return page.get_by_role("button", name=re.compile(r"^To fill in \("))


def expect_chip(page: Page, count: int) -> None:
    """The chip counts count cells, and there is none when the list is empty."""
    if count > 0:
        expect(chip_named(page)).to_have_text(
            f"To fill in ({count})", timeout=COMMIT_TIMEOUT * 1000
        )
    else:
        expect(chip_named(page)).to_have_count(0, timeout=COMMIT_TIMEOUT * 1000)


def wait_list_saved(api: Api, wanted: list[str] | None, item_id: str) -> None:
    """Wait until nothing is pending and the real list says wanted for the item."""
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while (
        api.get("/api/queue?status=pending")
        or api.get("/api/grid")["toFill"].get(item_id) != wanted
    ):
        assert time.monotonic() < deadline, "the to-fill list was not saved"
        time.sleep(0.5)


def test_a_new_item_is_listed_until_its_cell_is_filled(with_template: Page, api: Api):
    page = with_template
    name = "E2E to fill item"
    before = listed_count(api)
    ## the dialog requires a template's hidden field, so only a queued entry leaves it blank
    operation = {
        "kind": "createItem",
        "name": name,
        "template": TEMPLATE["name"],
        "values": {"fields.host": "nas.local"},
    }
    after = last_entry_id(api)
    api.post("/api/queue", {"operations": [operation]})
    try:
        commit_new_entries(api, after)
        expect_chip(page, before + 1)
        item = rows_by_name(api)[name]
        listed = len(api.get("/api/grid")["toFill"])

        chip_named(page).click()
        expect(page.get_by_text(re.compile(rf"^{listed} of \d+ items$"))).to_be_visible()
        expect(row(page, item["id"])).to_have_count(1)
        target = item_cell(page, item["id"], SECRET)
        expect(target).to_have_class(MARKED)
        expect(item_cell(page, item["id"], "fields.host")).not_to_have_class(MARKED)
        expect(hover_tooltip(page, target)).to_have_text(TOOLTIP)

        after = last_entry_id(api)
        target.dblclick()
        secret = page.get_by_role("dialog")
        secret.get_by_label("New value").fill("e2e-filled-value")
        secret.get_by_role("button", name="Save").click()
        expect(secret).to_be_hidden()
        ## the edit, then the list without the cell, which its commit queued
        commit_new_entries(api, after, count=2)
        expect_chip(page, before)
        if before == 0:
            ## the filter went with the chip, so the other items show again
            expect(page.get_by_text(re.compile(r"^\d+ of \d+ items$"))).to_have_count(0)
        expect(item_cell(page, item["id"], SECRET)).not_to_have_class(MARKED)
    finally:
        trash(api, name)


def test_a_raw_login_with_a_blank_password_is_listed(unlocked_page: Page, api: Api):
    page = unlocked_page
    name = "E2E blank password"
    before = listed_count(api)
    dialog = new_item_dialog(page)
    pick(dialog, "Start from", "Login")
    dialog.get_by_label(NAME).fill(name)
    dialog.get_by_role("checkbox", name="Generate a password").uncheck()
    expect(dialog.get_by_label("login.password")).to_be_visible()
    create = dialog.get_by_role("button", name="Create", exact=True)
    expect(create).to_be_enabled()
    after = last_entry_id(api)
    create.click()
    expect(dialog).to_be_hidden()
    try:
        commit_new_entries(api, after)
        item = rows_by_name(api)[name]
        columns = api.get("/api/grid")["toFill"].get(item["id"], [])
        assert "login.password" in columns, "the blank password is not on the list"
        expect_chip(page, before + len(columns))
    finally:
        trash(api, name)


def test_mark_and_unmark_an_empty_secret_cell(unlocked_page: Page, api: Api):
    page = unlocked_page
    before = listed_count(api)
    rows = api.get("/api/grid")["rows"]
    target_row = next(
        r
        for r in rows
        if "login.totp" in r.get("emptySecrets", []) and r["cells"].get("login.password")
    )
    item_id = target_row["id"]
    search(page, target_row["cells"]["name"])
    target = item_cell(page, item_id, "login.totp")
    filled = item_cell(page, item_id, "login.password")

    filled.click(button="right")
    menu = page.get_by_role("menu")
    expect(menu.get_by_role("menuitem", name="Remove value")).to_be_visible()
    expect(menu.get_by_role("menuitem", name="Mark to fill in")).to_have_count(0)
    expect(menu.get_by_role("menuitem", name="Unmark")).to_have_count(0)
    page.keyboard.press("Escape")

    target.click(button="right")
    expect(page.get_by_role("menu").get_by_role("menuitem", name="Unmark")).to_have_count(0)
    page.get_by_role("menuitem", name="Mark to fill in").click()
    expect_chip(page, before + 1)
    expect(target).to_have_class(MARKED)
    wait_list_saved(api, ["login.totp"], item_id)

    target.click(button="right")
    expect(page.get_by_role("menu").get_by_role("menuitem", name="Mark to fill in")).to_have_count(
        0
    )
    page.get_by_role("menuitem", name="Unmark").click()
    expect_chip(page, before)
    expect(target).not_to_have_class(MARKED)
    wait_list_saved(api, None, item_id)
