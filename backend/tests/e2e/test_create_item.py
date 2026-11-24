"""The New item dialog of the grid (docs/spec.md, Grid, and Writes, createItem).

Each test makes its own item and moves it to the trash again, so no other test sees it, and the
file deletes them and the folder it made for good at its end (file_put_back). The template test
stores one template of its own and puts the stored list back afterwards.
"""

import json
import re
import time
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from support.api import (
    COMMIT_TIMEOUT,
    Api,
    commit_new_entries,
    last_entry_id,
    rows_by_name,
    secret_is,
)
from support.pages import MASK, grid, item_cell, pick, row, search
from support.vault_items import (
    GENERATED_LENGTH,
    NAME,
    new_item_dialog,
    save_templates,
    trash,
    wait_templates_saved,
)

from tests.support.section_changes import add_datatype, datatype_changes

pytestmark = pytest.mark.usefixtures("file_put_back")

SECRETS_LEFT_EMPTY = "Secret fields left empty go on your To fill in list."
GENERATED_PASSWORD = "The password is generated and shown only when you reveal it in the grid."

TEMPLATE = {
    "name": "E2E server",
    "type": 2,
    "coordinates": [
        {"kind": "field", "name": "host", "fieldType": 0},
        {"kind": "field", "name": "root password", "fieldType": 1},
        {"kind": "path", "path": "notes"},
    ],
}


@pytest.fixture
def with_template(unlocked_page: Page, api: Api) -> Iterator[Page]:
    """The grid with TEMPLATE stored beside the stored ones, which are put back afterwards."""
    before = wait_templates_saved(api)["templates"]
    save_templates(api, [*before, TEMPLATE])
    unlocked_page.reload()
    grid(unlocked_page)
    yield unlocked_page
    save_templates(api, before)


def test_a_new_login_gets_a_generated_password_in_a_new_folder(unlocked_page: Page, api: Api):
    page = unlocked_page
    name = "E2E new login"
    folder = "E2E created"
    dialog = new_item_dialog(page)
    pick(dialog, "Start from", "Login")
    dialog.get_by_label(NAME).fill(name)
    pick(dialog, "Folder", "New folder")
    dialog.get_by_label("New folder name").fill(folder)
    dialog.get_by_label("login.username").fill("e2e-user")
    ## a generated password has no input, the other secret fields of a raw login do
    expect(dialog.get_by_label("login.password")).to_have_count(0)
    expect(dialog.get_by_label("login.totp")).to_have_css("-webkit-text-security", "disc")
    expect(dialog.get_by_role("checkbox", name="Generate a password")).to_be_checked()
    expect(dialog).to_contain_text(f"{SECRETS_LEFT_EMPTY} {GENERATED_PASSWORD}")
    after = last_entry_id(api)
    dialog.get_by_role("button", name="Create", exact=True).click()
    expect(dialog).to_be_hidden()
    try:
        commit_new_entries(api, after)

        item = rows_by_name(api)[name]
        assert item["cells"]["folder"] == folder
        assert item["cells"]["login.username"] == "e2e-user"
        ## the grid reloads after the commit, so the new item is a row like any other
        search(page, name)
        expect(row(page, item["id"])).to_have_count(1)
        password = row(page, item["id"]).locator('.ag-cell[col-id="login.password"]')
        value = password.get_by_test_id("secret-value")
        masked = value.inner_text() == MASK
        assert masked, "the generated password is not masked"
        password.get_by_role("button", name="Reveal login.password").click()
        expect(password.get_by_role("button", name="Hide login.password")).to_be_visible()
        shown = value.inner_text()
        generated = len(shown) == GENERATED_LENGTH and secret_is(api, item, "login.password", shown)
        assert generated, "the revealed password is not a generated one of 24 characters"
    finally:
        trash(api, name)


def test_a_note_from_a_template_is_gone_after_a_restore_of_its_row(with_template: Page, api: Api):
    page = with_template
    name = "E2E new note"
    dialog = new_item_dialog(page)
    pick(dialog, "Start from", "E2E server (Secure note)")
    expect(dialog.get_by_label("fields.host")).to_be_visible()
    expect(dialog.get_by_label("notes")).to_be_visible()
    expect(dialog.get_by_role("checkbox", name="Generate a password")).to_have_count(0)
    expect(dialog).not_to_contain_text(SECRETS_LEFT_EMPTY)
    dialog.get_by_label(NAME).fill(name)
    create = dialog.get_by_role("button", name="Create", exact=True)
    expect(dialog.get_by_text("Required by the template")).to_have_count(3)
    expect(create).to_be_disabled()
    dialog.get_by_label("fields.host").fill("nas.local")
    expect(create).to_be_disabled()
    dialog.get_by_label("notes").fill("a rack in the cellar")
    expect(create).to_be_disabled()
    dialog.get_by_label("fields.root password").fill("e2e-root-secret")
    expect(create).to_be_enabled()
    after = last_entry_id(api)
    create.click()
    expect(dialog).to_be_hidden()
    try:
        commit_new_entries(api, after)
        item = rows_by_name(api)[name]
        assert item["cells"]["type"] == "note"
        assert item["cells"]["fields.host"] == "nas.local"
        search(page, name)
        expect(row(page, item["id"])).to_have_count(1)

        page.get_by_role("button", name="Change log", exact=True).click()
        log = page.get_by_role("table", name="Change log")
        entry = (
            log.get_by_role("row")
            .filter(has=page.get_by_role("cell", name=name, exact=True))
            .filter(has=page.get_by_text("createItem", exact=True))
            .first
        )
        expect(entry).to_contain_text("Create an item")
        after = last_entry_id(api)
        entry.get_by_role("button", name="Restore").click()
        restore = page.get_by_role("dialog", name="Restore")
        plan = restore.get_by_role("list", name="What the restore does").get_by_role("listitem")
        expect(plan).to_have_count(1)
        expect(plan).to_contain_text(name)
        expect(plan).to_contain_text("move to the trash")
        restore.get_by_role("button", name="Restore").click()
        expect(restore).to_be_hidden()
        commit_new_entries(api, after)

        assert name not in rows_by_name(api)
        page.get_by_role("button", name="Vault", exact=True).click()
        search(page, name)
        expect(row(page, item["id"])).to_have_count(0)
    finally:
        trash(api, name)


def test_a_secret_typed_in_the_dialog_is_stored_and_never_queued_in_plain(
    with_template: Page, api: Api
):
    page = with_template
    name = "E2E typed secret"
    typed = "e2e-typed-secret-7"
    column = "fields.root password"
    dialog = new_item_dialog(page)
    pick(dialog, "Start from", "E2E server (Secure note)")
    dialog.get_by_label(NAME).fill(name)
    dialog.get_by_label("fields.host").fill("nas.local")
    dialog.get_by_label("notes").fill("a rack in the cellar")
    secret = dialog.get_by_label(column)
    ## a text input masked with CSS, so no password manager takes it for a password field
    expect(secret).to_have_attribute("type", "text")
    expect(secret).to_have_css("-webkit-text-security", "disc")
    secret.fill(typed)
    dialog.get_by_role("checkbox", name="Show what I type").check()
    expect(secret).to_have_css("-webkit-text-security", "none")
    after = last_entry_id(api)
    dialog.get_by_role("button", name="Create", exact=True).click()
    expect(dialog).to_be_hidden()
    try:
        ## the Queue answer, whatever status the entry has by now, never held the value
        deadline = time.monotonic() + COMMIT_TIMEOUT
        while not (entries := [e for e in api.get("/api/queue") if e["id"] > after]):
            assert time.monotonic() < deadline, "the item was not queued"
            time.sleep(0.2)
        in_queue = any(typed in json.dumps(entry) for entry in entries)
        assert not in_queue, "the Queue answer holds the typed secret"
        commit_new_entries(api, after)
        committed = any(
            typed in json.dumps(entry) for entry in api.get("/api/queue") if entry["id"] > after
        )
        assert not committed, "the Queue answer holds the typed secret after the commit"

        item = rows_by_name(api)[name]
        assert item["cells"]["fields.host"] == "nas.local"
        listed = column in api.get("/api/grid")["toFill"].get(item["id"], [])
        assert not listed, "the typed secret cell is on the to-fill list"
        search(page, name)
        expect(row(page, item["id"])).to_have_count(1)
        cell = item_cell(page, item["id"], column)
        cell.get_by_role("button", name=f"Reveal {column}").click()
        expect(cell.get_by_role("button", name=f"Hide {column}")).to_be_visible()
        expect(cell).not_to_have_class(re.compile(r"bw-to-fill-cell"))
        stored = secret_is(api, item, column, typed)
        assert stored, "the item does not hold the typed secret"
        shown = cell.get_by_test_id("secret-value").inner_text() == typed
        assert shown, "the revealed cell does not show the typed secret"
    finally:
        trash(api, name)


def wait_datatypes_saved(api: Api) -> list[dict[str, Any]]:
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while (doc := api.get("/api/datatypes"))["saving"]:
        assert time.monotonic() < deadline, "the datatypes save was not committed"
        time.sleep(0.5)
    return doc["datatypes"]


@pytest.fixture
def with_datatype_template(unlocked_page: Page, api: Api) -> Iterator[Page]:
    """The grid with a template whose field has a stored datatype, both put back afterwards."""
    datatypes = wait_datatypes_saved(api)
    templates = wait_templates_saved(api)["templates"]
    code = {
        "name": "E2E create code",
        "pattern": r"\d{4,8}",
        "description": "",
        "ignoreCase": False,
        "examples": ["1234"],
        "counterExamples": ["12a4"],
    }
    api.put("/api/datatypes", {"changes": [add_datatype(code)]})
    saved = wait_datatypes_saved(api)
    datatype_id = next(datatype["id"] for datatype in saved if datatype["name"] == code["name"])
    coordinate = {"kind": "field", "name": "e2e code", "fieldType": 0, "datatype": datatype_id}
    template = {"name": "E2E code note", "type": 2, "coordinates": [coordinate]}
    save_templates(api, [*templates, template])
    unlocked_page.reload()
    grid(unlocked_page)
    yield unlocked_page
    save_templates(api, templates)
    changes = datatype_changes(wait_datatypes_saved(api), datatypes)
    if changes:
        api.put("/api/datatypes", {"changes": changes})
        wait_datatypes_saved(api)


def test_a_value_off_its_datatype_keeps_create_disabled_until_it_matches(
    with_datatype_template: Page, api: Api
):
    page = with_datatype_template
    name = "E2E code item"
    dialog = new_item_dialog(page)
    pick(dialog, "Start from", "E2E code note (Secure note)")
    dialog.get_by_label(NAME).fill(name)
    create = dialog.get_by_role("button", name="Create", exact=True)
    expect(create).to_be_disabled()
    field = dialog.get_by_label("fields.e2e code")
    field.fill("12a4")
    expect(dialog.get_by_text(re.compile(r"^matches format \d+%"))).to_be_visible()
    expect(create).to_be_disabled()
    field.fill("1234")
    expect(dialog.get_by_text("matches format", exact=True)).to_be_visible()
    expect(create).to_be_enabled()
    after = last_entry_id(api)
    create.click()
    expect(dialog).to_be_hidden()
    try:
        commit_new_entries(api, after)
        item = rows_by_name(api)[name]
        assert item["cells"]["fields.e2e code"] == "1234"
    finally:
        trash(api, name)
