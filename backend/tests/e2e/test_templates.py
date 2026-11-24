"""Templates: the page, the template column and filter, converting, the renamed note.

Every test starts from TEMPLATES (saved only when the
stored list differs) and works on items no other test here writes to. The module ends with no
templates, the way the seed has it, and puts back the items it converted (file_put_back).
"""

import re
import time
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Browser, Locator, Page, expect
from support.api import (
    COMMIT_TIMEOUT,
    Api,
    commit_new_entries,
    last_entry_id,
    rows_by_name,
    secret_is,
)
from support.elsewhere import elsewhere, trashed_ids
from support.login import enter_account, log_in, log_out, unlock
from support.pages import (
    add_coordinate,
    add_new_field,
    cell,
    coordinate_row,
    grid,
    hover_tooltip,
    pick,
)

from tests.support.section_changes import add_datatype, datatype_changes, template_changes

pytestmark = pytest.mark.usefixtures("file_put_back")


def path(coordinate: str) -> dict[str, Any]:
    return {"kind": "path", "path": coordinate}


def text(name: str) -> dict[str, Any]:
    return {"kind": "field", "name": name, "fieldType": 0}


def hidden(name: str) -> dict[str, Any]:
    return {"kind": "field", "name": name, "fieldType": 1}


## Web login: logins with a TOTP match, the other logins are near misses (3/4).
## Disk key: a note template whose two fields ThinkPad X1 (a login) has, so 2/3 with the type wrong.
## API token and Plain note are for converting, in the same type and into another one.
TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "Web login",
        "type": 1,
        "coordinates": [path("login.username"), path("login.password"), path("login.totp")],
    },
    {"name": "Disk key", "type": 2, "coordinates": [hidden("LUKS"), hidden("BIOS Password")]},
    {"name": "API token", "type": 2, "coordinates": [hidden("Token")]},
    {"name": "Plain note", "type": 2, "coordinates": [text("username"), hidden("password")]},
]

EXTENSION_HINT = "the browser extension copies those straight from the item list"
RENAMED = "bwtidyK"


def wait_saved(api: Api) -> dict[str, Any]:
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while (doc := api.get("/api/templates"))["saving"]:
        assert time.monotonic() < deadline, "the templates save was not committed"
        time.sleep(0.5)
    return doc


def save_templates(api: Api, templates: list[dict[str, Any]]) -> None:
    doc = wait_saved(api)
    if doc["templates"] == templates:
        return
    changes = template_changes(doc["templates"], templates)
    api.put("/api/templates", {"changes": changes, "revisionDate": doc["revisionDate"]})
    saved = wait_saved(api)["templates"] == templates
    assert saved, "the templates were not saved"


@pytest.fixture
def templated(unlocked_page: Page, api: Api) -> Page:
    """The grid unlocked with TEMPLATES stored, reloaded so it shows their matches."""
    save_templates(api, TEMPLATES)
    unlocked_page.reload()
    grid(unlocked_page)
    return unlocked_page


@pytest.fixture(scope="module", autouse=True)
def no_templates_afterwards(browser: Browser, base_url: str) -> Iterator[None]:
    yield
    context = browser.new_context(base_url=base_url)
    try:
        page = context.new_page()
        enter_account(page)
        log_in(page)
        grid(page)
        save_templates(Api(page.request), [])
    finally:
        context.close()


def stored(api: Api) -> list[dict[str, Any]]:
    return wait_saved(api)["templates"]


def templates_page(page: Page) -> None:
    page.get_by_role("button", name="Types", exact=True).click()
    expect(page.get_by_role("heading", name="Templates")).to_be_visible()


def template_row(page: Page, name: str) -> Locator:
    return (
        page.get_by_role("table", name="Templates")
        .get_by_role("row")
        .filter(has=page.get_by_role("cell", name=name, exact=True))
    )


## Types page


def test_templates_page_shows_the_extension_hint(templated: Page):
    page = templated
    templates_page(page)
    expect(page.get_by_role("alert").filter(has_text=EXTENSION_HINT)).to_be_visible()
    page.get_by_role("button", name="New template").click()
    editor = page.get_by_role("dialog", name="New template")
    expect(editor.get_by_role("alert").filter(has_text=EXTENSION_HINT)).to_be_visible()


def test_the_extension_hint_ends_after_its_text(unlocked_page: Page):
    page = unlocked_page
    templates_page(page)
    hint = page.get_by_role("alert").filter(has_text=EXTENSION_HINT)
    expect(hint).to_be_visible()
    ## 75ch of the hint's font, from a probe of that width
    sizes = hint.evaluate(
        """alert => {
          const text = alert.querySelector('p')
          const probe = document.createElement('div')
          probe.style.font = getComputedStyle(text).font
          probe.style.width = '75ch'
          probe.style.position = 'absolute'
          document.body.append(probe)
          const limit = probe.getBoundingClientRect().width
          probe.remove()
          return {
            alert: alert.getBoundingClientRect().width,
            content: alert.parentElement.getBoundingClientRect().width,
            text: text.getBoundingClientRect().width,
            limit,
          }
        }"""
    )
    assert sizes["alert"] < sizes["content"] - 100, sizes
    assert sizes["text"] <= sizes["limit"] + 0.5, sizes


def test_type_guidance_follows_the_type(unlocked_page: Page):
    page = unlocked_page
    templates_page(page)
    page.get_by_role("button", name="New template").click()
    editor = page.get_by_role("dialog", name="New template")
    guidance = editor.get_by_role("region", name="Type guidance")
    ## a new template starts as a secure note
    expect(guidance).to_contain_text("A secret that is not a sign-in")
    expect(guidance).to_contain_text("Use Secure note for")
    pick(editor, "Type", "Login")
    expect(guidance).to_contain_text("A website or web app you sign in to")
    expect(guidance).to_contain_text("Use Login for")
    expect(guidance).not_to_contain_text("Use for")
    expect(guidance).not_to_contain_text("A secret that is not a sign-in")
    pick(editor, "Type", "Secure note")
    expect(guidance).to_contain_text("A secret that is not a sign-in")
    expect(guidance).to_contain_text("with a username and password: a login")
    editor.get_by_role("button", name="Cancel").click()
    expect(editor).to_be_hidden()


def test_create_a_template(templated: Page, api: Api):
    page = templated
    templates_page(page)
    page.get_by_role("button", name="New template").click()
    editor = page.get_by_role("dialog", name="New template")
    editor.get_by_label("Name").fill("Computer")
    pick(editor, "Type", "Secure note")
    fields = [("user", "Text"), ("user password", "Hidden"), ("BIOS password", "Hidden")]
    for name, kind in fields:
        add_new_field(editor, name, kind)
    editor.get_by_role("button", name="Save").click()
    expect(editor).to_be_hidden()

    row = template_row(page, "Computer")
    expect(row).to_contain_text("Secure note")
    for name, _ in fields:
        expect(row.get_by_text(f"fields.{name}", exact=True)).to_be_visible()
    expected = {
        "name": "Computer",
        "type": 2,
        "coordinates": [text("user"), hidden("user password"), hidden("BIOS password")],
    }
    assert stored(api) == [*TEMPLATES, expected]


def test_a_description_shows_in_the_list_and_the_new_item_dialog(templated: Page, api: Api):
    page = templated
    description = "Shown for the E2E described template"
    templates_page(page)
    page.get_by_role("button", name="New template").click()
    editor = page.get_by_role("dialog", name="New template")
    editor.get_by_label("Name").fill("E2E described")
    editor.get_by_label("Description").fill(description)
    expect(editor).to_contain_text("Shown in the list and to an agent connected through MCP.")
    add_new_field(editor, "e2e host", "Text")
    editor.get_by_role("button", name="Save").click()
    expect(editor).to_be_hidden()

    expect(template_row(page, "E2E described").get_by_text(description, exact=True)).to_be_visible()
    expected = {
        "name": "E2E described",
        "description": description,
        "type": 2,
        "coordinates": [text("e2e host")],
    }
    assert stored(api) == [*TEMPLATES, expected]

    page.get_by_role("button", name="Vault", exact=True).click()
    grid(page)
    page.get_by_role("button", name="New item", exact=True).click()
    dialog = page.get_by_role("dialog", name="New item")
    expect(dialog).not_to_contain_text(description)
    pick(dialog, "Start from", "E2E described (Secure note)")
    expect(dialog.get_by_text(description, exact=True)).to_be_visible()
    pick(dialog, "Start from", "Web login (Login)")
    expect(dialog).not_to_contain_text(description)


def test_edit_a_template_saves_in_the_background(templated: Page, api: Api):
    page = templated
    templates_page(page)
    page.get_by_role("button", name="Edit Disk key").click()
    editor = page.get_by_role("dialog", name="Edit Disk key")
    add_new_field(editor, "TPM PIN")
    editor.get_by_role("button", name="Save").click()
    ## the save is only queued, the editor does not wait for the commit
    expect(editor).to_be_hidden(timeout=3_000)
    saving = page.get_by_text("Saving", exact=True)
    expect(saving).to_be_visible()
    expect(saving).to_be_hidden(timeout=COMMIT_TIMEOUT * 1000)

    expect(template_row(page, "Disk key").get_by_text("fields.TPM PIN", exact=True)).to_be_visible()
    disk_key = next(t for t in stored(api) if t["name"] == "Disk key")
    assert disk_key["coordinates"][-1] == text("TPM PIN")


def test_delete_a_template(templated: Page, api: Api):
    page = templated
    templates_page(page)
    page.get_by_role("button", name="Delete API token").click()
    confirm = page.get_by_role("dialog", name="Delete template")
    confirm.get_by_role("button", name="Delete").click()
    expect(confirm).to_be_hidden()
    expect(template_row(page, "API token")).to_have_count(0)
    assert [t["name"] for t in stored(api)] == ["Web login", "Disk key", "Plain note"]


def test_a_copy_of_a_template_warns_that_the_first_takes_every_match(templated: Page, api: Api):
    page = templated
    templates_page(page)
    page.get_by_role("button", name="New template").click()
    editor = page.get_by_role("dialog", name="New template")
    editor.get_by_label("Name").fill("API token copy")
    pick(editor, "Type", "Secure note")
    warning = (
        "API token copy: same type and fields as API token, which is listed first"
        " and takes every match."
    )
    expect(editor.get_by_text(warning)).to_have_count(0)
    add_new_field(editor, "Token", "Hidden")
    ## the editor shows it before saving
    expect(editor.get_by_text(warning)).to_be_visible()
    editor.get_by_role("button", name="Save").click()
    expect(editor).to_be_hidden()

    ## saving stays allowed, and the page shows the warning of the API
    expect(template_row(page, "API token copy")).to_be_visible()
    expect(page.get_by_text(warning)).to_be_visible()
    assert [t["name"] for t in stored(api)][-1] == "API token copy"

    page.get_by_role("button", name="Delete API token copy").click()
    confirm = page.get_by_role("dialog", name="Delete template")
    confirm.get_by_role("button", name="Delete").click()
    expect(confirm).to_be_hidden()
    expect(page.get_by_text(warning)).to_have_count(0)
    assert [t["name"] for t in stored(api)] == [t["name"] for t in TEMPLATES]


## Template column and filter


def test_template_column_shows_the_match(templated: Page):
    page = templated
    expect(cell(page, "Game launcher", "template")).to_have_text("Web login")


def test_template_column_shows_a_near_miss_with_its_fill(templated: Page):
    page = templated
    expect(cell(page, "Library", "template")).to_have_text("Web login 3/4")


def test_a_near_miss_counts_the_type_as_one_to_match(templated: Page):
    page = templated
    ## ThinkPad X1 is a login with both fields of the note template Disk key
    expect(cell(page, "ThinkPad X1", "template")).to_have_text("Disk key 2/3")


def test_no_near_miss_below_two_matched(templated: Page):
    page = templated
    ## Door code is a note without fields: only its type matches Disk key and the others
    expect(cell(page, "Door code", "template")).to_have_text("")


def filter_template(page: Page, option: str) -> None:
    pick(page, "Template", option)


def shown(page: Page, count: int) -> None:
    """Wait until the toolbar says that count items are shown."""
    expect(page.get_by_text(re.compile(rf"^{count} of \d+ items$"))).to_be_visible()


def name_cells(page: Page) -> Locator:
    return page.locator(".ag-cell[col-id='name']")


def test_template_filter_shows_the_matches(templated: Page, api: Api):
    page = templated
    matches = api.get("/api/template-matches")
    expected = sum(1 for match in matches.values() if match["match"] == "Web login")
    filter_template(page, "Web login")
    shown(page, expected)
    ## polled: rows that the filter removes stay in the page for a moment
    expect(page.locator(".ag-cell[col-id='template']")).to_have_text(["Web login"] * expected)


def test_template_filter_shows_the_near_misses(templated: Page):
    ## ThinkPad X1 is the one item with a field of Disk key, Door code and the other notes only
    ## have its type (1/3), which is no near miss
    page = templated
    filter_template(page, "Near misses of Disk key")
    shown(page, 1)
    expect(name_cells(page)).to_have_text(["ThinkPad X1"])
    expect(page.locator(".ag-cell[col-id='template']")).to_have_text(["Disk key 2/3"])


def test_template_filter_shows_the_items_without_a_match(templated: Page):
    page = templated
    filter_template(page, "No template")
    ## only Door code has "front", "door" or "code" alone also match Club locker code
    page.get_by_label("Search").fill("Front door")
    expect(name_cells(page)).to_have_text(["Door code"])
    ## a near miss is no match
    expect(cell(page, "Library", "template")).to_have_text("Web login 3/4")
    page.get_by_label("Search").fill("Game launcher")
    shown(page, 0)


def test_an_optional_field_is_not_needed_for_a_match(templated: Page, api: Api):
    page = templated
    templates_page(page)
    page.get_by_role("button", name="New template").click()
    editor = page.get_by_role("dialog", name="New template")
    editor.get_by_label("Name").fill("Alarm")
    pick(editor, "Type", "Secure note")
    add_coordinate(editor, "fields.username, Text")
    number = add_coordinate(editor, "fields.code, Hidden")
    coordinate_row(editor, number).get_by_role("checkbox", name="Optional").check()
    editor.get_by_role("button", name="Save").click()
    expect(editor).to_be_hidden()
    expect(template_row(page, "Alarm").get_by_text("fields.code (optional)")).to_be_visible()
    optional_code = {**hidden("code"), "optional": True}
    expected = {"name": "Alarm", "type": 2, "coordinates": [text("username"), optional_code]}
    assert stored(api) == [*TEMPLATES, expected]

    page.get_by_role("button", name="Vault", exact=True).click()
    grid(page)
    ## Old webshop has username and Password but no code, Alarm system has both
    expect(cell(page, "Old webshop", "template")).to_have_text("Alarm")
    expect(cell(page, "Alarm system", "template")).to_have_text("Alarm")


## Convert to template


def open_convert(page: Page, item: str, template: str) -> Locator:
    cell(page, item, "name").click(button="right")
    page.get_by_role("menuitem", name="Convert to template…").click()
    dialog = page.get_by_role("dialog", name=f"Convert {item} to a template")
    pick(dialog, "Template", re.compile(rf"^{re.escape(template)}, "))
    expect(dialog.get_by_role("table", name="Mapping")).to_be_visible()
    return dialog


def test_convert_maps_equal_names_by_default(templated: Page, assert_no_secrets):
    page = templated
    dialog = open_convert(page, "Bike club", "Plain note")
    username = dialog.get_by_role("combobox", name="Value for fields.username")
    password = dialog.get_by_role("combobox", name="Value for fields.password")
    expect(username).to_be_visible()
    by_name = username.inner_text().startswith("login.username: ")
    assert by_name, "fields.username does not default to login.username"
    by_name = password.inner_text().startswith("login.password: ")
    assert by_name, "fields.password does not default to login.password"
    assert_no_secrets(dialog.inner_text())


def test_convert_warns_when_the_type_changes(templated: Page):
    page = templated
    dialog = open_convert(page, "Bike club", "Plain note")
    warning = (
        "A new item of the new type is created with the mapped values, "
        "and the old item goes to the trash."
    )
    expect(dialog.get_by_role("alert").filter(has_text=warning)).to_be_visible()
    expect(dialog.get_by_role("button", name="Convert", exact=True)).to_be_enabled()

    pick(dialog, "Template", re.compile(r"^Web login, "))
    expect(dialog.get_by_role("alert").filter(has_text=warning)).to_have_count(0)
    expect(dialog.get_by_role("button", name="Apply template", exact=True)).to_be_enabled()


def test_convert_in_the_same_type_keeps_the_item(templated: Page, api: Api, fixture_items):
    page = templated
    item = "Code forge API"
    before = rows_by_name(api)[item]
    dialog = open_convert(page, item, "API token")
    by_name = (
        dialog.get_by_role("combobox", name="Value for fields.Token")
        .inner_text()
        .startswith("fields.token: ")
    )
    assert by_name, "fields.Token does not default to fields.token"
    after = last_entry_id(api)
    dialog.get_by_role("button", name="Apply template", exact=True).click()
    expect(dialog).to_be_hidden()
    commit_new_entries(api, after)

    row = rows_by_name(api)[item]
    same = row["id"] == before["id"]
    assert same, f"{item} is a new item"
    gone = "fields.token" not in row["cells"]
    assert gone, f"{item} still has fields.token"
    token = fixture_items[item]["fields"][0]["value"]
    moved = secret_is(api, row, "fields.Token", token)
    assert moved, f"fields.Token of {item} is not its old token"


def test_convert_to_another_type_trashes_the_old_item(templated: Page, api: Api, fixture_items):
    page = templated
    item = "Airline"
    old = rows_by_name(api)[item]
    dialog = open_convert(page, item, "Plain note")
    after = last_entry_id(api)
    dialog.get_by_role("button", name="Convert", exact=True).click()
    expect(dialog).to_be_hidden()
    commit_new_entries(api, after)

    row = rows_by_name(api)[item]
    new = row["id"] != old["id"]
    assert new, f"{item} kept its id"
    assert row["cells"]["type"] == "note"
    login = fixture_items[item]["login"]
    username = row["cells"].get("fields.username") == login["username"]
    assert username, "fields.username of the new item is not the old login.username"
    password = secret_is(api, row, "fields.password", login["password"])
    assert password, "fields.password of the new item is not the old login.password"
    trashed = old["id"] in trashed_ids()
    assert trashed, f"the old {item} is not in the trash"


## A field name pattern

PATTERN_DATATYPE = "E2E user code"
PATTERN_TEMPLATE = "Shared users"
PATTERN_FIT = "E2E pattern fit"
PATTERN_OFF = "E2E pattern off"


def wait_datatypes_saved(api: Api) -> list[dict[str, Any]]:
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while (doc := api.get("/api/datatypes"))["saving"]:
        assert time.monotonic() < deadline, "the datatypes save was not committed"
        time.sleep(0.5)
    return doc["datatypes"]


@pytest.fixture
def user_codes(templated: Page, api: Api) -> Iterator[str]:
    """The datatype's id, and two notes with user_ fields: one in the format, one with a
    second field off it. The datatype and the template go afterwards, the notes with the file."""
    datatypes = wait_datatypes_saved(api)
    code = {
        "name": PATTERN_DATATYPE,
        "pattern": r"\d{4}",
        "description": "",
        "ignoreCase": False,
        "examples": ["1234"],
        "counterExamples": ["12a4"],
    }
    api.put("/api/datatypes", {"changes": [add_datatype(code)]})
    saved = wait_datatypes_saved(api)
    datatype_id = next(d["id"] for d in saved if d["name"] == PATTERN_DATATYPE)
    notes = {PATTERN_FIT: {"fields.user_a": "1234"}}
    notes[PATTERN_OFF] = {"fields.user_a": "1234", "fields.user_b": "12a4"}
    operations = [
        {"kind": "createItem", "name": name, "type": 2, "values": values}
        for name, values in notes.items()
    ]
    after = last_entry_id(api)
    api.post("/api/queue", {"operations": operations})
    commit_new_entries(api, after, len(operations))
    yield datatype_id
    save_templates(api, TEMPLATES)
    changes = datatype_changes(wait_datatypes_saved(api), datatypes)
    if changes:
        api.put("/api/datatypes", {"changes": changes})
        wait_datatypes_saved(api)


def test_a_field_name_pattern_matches_and_names_the_field_off_its_format(
    templated: Page, api: Api, user_codes: str
):
    page = templated
    page.reload()
    templates_page(page)
    page.get_by_role("button", name="New template").click()
    editor = page.get_by_role("dialog", name="New template")
    editor.get_by_label("Name").fill(PATTERN_TEMPLATE)
    pick(editor, "Type", "Secure note")
    number = add_coordinate(editor, "Field name pattern…")
    row = coordinate_row(editor, number)
    expect(row.get_by_text("* matches any characters, for example user_*")).to_be_visible()
    row.get_by_label(f"Field name pattern {number}", exact=True).fill("user_*")
    pick(row, "Datatype", PATTERN_DATATYPE)
    editor.get_by_role("button", name="Save").click()
    expect(editor).to_be_hidden()
    chip = f"fields.user_*: {PATTERN_DATATYPE} (pattern)"
    expect(template_row(page, PATTERN_TEMPLATE).get_by_text(chip)).to_be_visible()
    pattern = {"kind": "fieldPattern", "pattern": "user_*", "fieldType": 0, "datatype": user_codes}
    expected = {"name": PATTERN_TEMPLATE, "type": 2, "coordinates": [pattern]}
    assert stored(api) == [*TEMPLATES, expected]

    page.get_by_role("button", name="Vault", exact=True).click()
    grid(page)
    expect(cell(page, PATTERN_FIT, "template")).to_have_text(PATTERN_TEMPLATE)
    off = cell(page, PATTERN_OFF, "template")
    expect(off).to_have_text(re.compile(rf"^{PATTERN_TEMPLATE} 2\.\d\d/3$"))
    tooltip = hover_tooltip(page, off)
    expect(tooltip).to_contain_text(f"Off format for {PATTERN_TEMPLATE}:")
    expect(tooltip).to_contain_text("• user_b: matches format")
    off_id = rows_by_name(api)[PATTERN_OFF]["id"]
    scores = api.get("/api/template-matches")[off_id]["scores"]
    [score] = [s for s in scores if s["template"] == PATTERN_TEMPLATE]
    assert [f["column"] for f in score["formats"]] == ["fields.user_b"]
    assert score["missing"] == []


## The bwtidy note renamed elsewhere

SET_NAME = (
    "import base64, json, sys; item = json.load(sys.stdin); item['name'] = sys.argv[1]; "
    "print(base64.b64encode(json.dumps(item).encode()).decode())"
)
GET_NAME = "import json, sys; print(json.load(sys.stdin)['name'])"


def name_elsewhere(item_id: str) -> str:
    return elsewhere('bw get item "$1" | python3 -c "$2"', item_id, GET_NAME).strip()


def rename_elsewhere(item_id: str, name: str) -> None:
    elsewhere(
        'bw get item "$1" | python3 -c "$3" "$2" | bw edit item "$1" >/dev/null',
        item_id,
        name,
        SET_NAME,
    )


@pytest.fixture
def renamed(templated: Page, api: Api) -> Iterator[Page]:
    """The bwtidy note renamed to RENAMED by another device, then a log out and an unlock.

    A reload keeps the vault the app has in RAM, only an unlock loads it again.
    """
    page = templated
    note = api.get("/api/templates")["toolNoteId"]
    rename_elsewhere(note, RENAMED)
    log_out(page)
    unlock(page)
    grid(page)
    yield page
    if name_elsewhere(note) != "bwtidy":
        rename_elsewhere(note, "bwtidy")


def test_a_renamed_note_keeps_the_templates(renamed: Page, api: Api):
    doc = api.get("/api/templates")
    assert doc["toolNoteName"] == RENAMED
    assert doc["templates"] == TEMPLATES
    templates_page(renamed)
    for template in TEMPLATES:
        expect(template_row(renamed, template["name"])).to_be_visible()


def test_a_renamed_note_shows_a_warning(renamed: Page):
    warning = renamed.get_by_role("alert").filter(
        has_text=f'bwtidy\'s note is named "{RENAMED}" now.'
    )
    expect(warning).to_be_visible()
    expect(warning.get_by_role("button", name="Rename it back")).to_be_visible()


def test_rename_it_back(renamed: Page, api: Api):
    ## the quoted name, so the success snackbar ("is named bwtidy again") does not match too
    warning = renamed.get_by_role("alert").filter(has_text=f'is named "{RENAMED}" now.')
    warning.get_by_role("button", name="Rename it back").click()
    done = renamed.get_by_role("alert").filter(has_text="bwtidy's note is named bwtidy again.")
    expect(done).to_be_visible(timeout=COMMIT_TIMEOUT * 1000)
    expect(warning).to_be_hidden()
    doc = api.get("/api/templates")
    assert doc["toolNoteName"] == "bwtidy"
    assert name_elsewhere(doc["toolNoteId"]) == "bwtidy"
