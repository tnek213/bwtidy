"""Editing in the grid: every kind of cell, the cell menu, a conflict, the tool note.

Every edit goes into the queue and the auto-commit writes it, so a test that edits waits until
the vault holds the change. Each test edits items of its own, and the file puts the vault back
as it found it at its end (file_put_back).
"""

import re
import time
import uuid
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from support.api import COMMIT_TIMEOUT, Api, last_entry_id, rows_by_name, secret_is
from support.elsewhere import edit_notes_elsewhere, elsewhere, put_notes_back_elsewhere, trashed_ids
from support.login import EMAIL
from support.pages import (
    MASK,
    QUEUED,
    add_new_field,
    editor,
    grid,
    item_cell,
    pick,
    row,
    search,
    type_into,
)

pytestmark = pytest.mark.usefixtures("file_put_back")


def saved(page: Page, api: Api, name: str) -> dict[str, Any]:
    """Wait for the auto-commit and the grid reload, answer the item's cells from the vault."""
    api.wait_saved()
    expect(page.locator(".bw-queued-cell")).to_have_count(0)
    return rows_by_name(api)[name]["cells"]


def sync_the_app(api: Api) -> None:
    """The app knows of a change elsewhere only after a sync, which every write makes. A write
    through the scripts' route leaves this page's grid as it was."""
    other = rows_by_name(api)["Electricity company"]
    api.post(
        "/api/operations",
        {
            "operations": [
                {
                    "kind": "setLeaf",
                    "itemId": other["id"],
                    "revisionDate": other["revisionDate"],
                    "column": "notes",
                    "value": f"Written to make the app sync {uuid.uuid4().hex[:8]}",
                }
            ]
        },
        timeout=COMMIT_TIMEOUT * 1000,
    )


## the id of the seeded organization, the organizations on stdin
FAMILY_ID = (
    "import json, sys; print(next(o['id'] for o in json.load(sys.stdin) if o['name'] == 'Family'))"
)
## the body of a collection the dev account can see (ensure_collections in
## scripts/devlib/seed.py), the organization's members on stdin
COLLECTION_BODY = """
import json, os, sys
me = next(m for m in json.load(sys.stdin) if m["email"] == os.environ["EMAIL"])
user = {"id": me["id"], "readOnly": False, "hidePasswords": False, "manage": True}
print(json.dumps({
    "organizationId": os.environ["ORG"],
    "name": os.environ["NAME"],
    "externalId": None,
    "groups": [],
    "users": [user],
}))
"""
COLLECTION_ID = "import json, sys; print(json.load(sys.stdin)['id'])"


def create_collection_elsewhere(name: str) -> tuple[str, str]:
    """Create an empty collection in Family as another device, answer the organization's and
    its id."""
    created = elsewhere(
        """
org=$(bw list organizations | python3 -c "$2")
echo "$org"
bw list org-members --organizationid "$org" \\
  | EMAIL="$3" ORG="$org" NAME="$1" python3 -c "$4" \\
  | bw encode \\
  | bw create org-collection --organizationid "$org" \\
  | python3 -c "$5"
""",
        name,
        FAMILY_ID,
        EMAIL,
        COLLECTION_BODY,
        COLLECTION_ID,
    )
    organization_id, collection_id = created.split()
    return organization_id, collection_id


def delete_collection_elsewhere(organization_id: str, collection_id: str) -> None:
    elsewhere(
        'bw delete org-collection "$2" --organizationid "$1" >/dev/null',
        organization_id,
        collection_id,
    )


def test_enter_edits_a_text_cell(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Home Assistant"]["id"]
    search(page, "Home Assistant")
    type_into(page, item_id, "login.username", "reader-e2e")
    expect(item_cell(page, item_id, "login.username")).to_have_text("reader-e2e")
    assert saved(page, api, "Home Assistant")["login.username"] == "reader-e2e"


def test_double_click_edits_a_custom_field(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Server sudo"]["id"]
    search(page, "Server sudo")
    target = item_cell(page, item_id, "fields.infra")
    target.dblclick()
    page.locator(".ag-cell-inline-editing input").fill("infra-e2e")
    page.keyboard.press("Enter")
    expect(target).to_have_class(QUEUED)
    assert saved(page, api, "Server sudo")["fields.infra"] == "infra-e2e"


def test_notes_open_a_larger_text_box(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Office license key"]["id"]
    search(page, "Office license key")
    target = item_cell(page, item_id, "notes")
    target.dblclick()
    box = page.locator(".ag-popup-editor textarea")
    expect(box).to_be_visible()
    box.fill("First line\nSecond line from e2e")
    ## a click outside ends the edit, Enter in the box is a new line
    page.get_by_text(re.compile(r"^\d+ of \d+ items$")).click()
    expect(target).to_have_class(QUEUED)
    assert saved(page, api, "Office license key")["notes"] == "First line\nSecond line from e2e"


def test_the_favorite_checkbox_makes_an_item_a_favorite(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Phone backup"]["id"]
    search(page, "Phone backup")
    target = item_cell(page, item_id, "favorite")
    expect(target).to_have_text("")
    target.dblclick()
    page.locator(".ag-cell-inline-editing input[type=checkbox]").check()
    page.keyboard.press("Enter")
    expect(target).to_have_class(QUEUED)
    expect(target).to_have_text("✓")
    assert saved(page, api, "Phone backup")["favorite"] is True


def test_the_list_editor_adds_a_uri(unlocked_page: Page, api: Api):
    page = unlocked_page
    before = rows_by_name(api)["Music service"]
    search(page, "Music service")
    item_cell(page, before["id"], "login.uris").dblclick()
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("button", name="Add URI").click()
    count = len(before["cells"]["login.uris"])
    dialog.get_by_role("textbox", name=f"URI {count + 1}").fill("https://music-e2e.example.test")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    chips = item_cell(page, before["id"], "login.uris").locator(".MuiChip-root")
    expect(chips).to_have_count(count + 1)
    uris = saved(page, api, "Music service")["login.uris"]
    assert uris == [*before["cells"]["login.uris"], "https://music-e2e.example.test"]


def choose_folder(page: Page, name: str) -> None:
    pick(page.get_by_role("dialog"), "Folder", name)


def test_the_folder_dialog_moves_an_item_to_a_folder(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Tax agency"]["id"]
    search(page, "Tax agency")
    item_cell(page, item_id, "folder").dblclick()
    choose_folder(page, "Home")
    page.get_by_role("dialog").get_by_role("button", name="Save").click()
    expect(item_cell(page, item_id, "folder")).to_have_text("Home")
    assert saved(page, api, "Tax agency")["folder"] == "Home"


def test_the_folder_dialog_makes_a_new_folder(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Health portal"]["id"]
    search(page, "Health portal")
    item_cell(page, item_id, "folder").dblclick()
    choose_folder(page, "New folder")
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("New folder name").fill("Health e2e")
    dialog.get_by_role("button", name="Save").click()
    expect(item_cell(page, item_id, "folder")).to_have_text("Health e2e")
    assert saved(page, api, "Health portal")["folder"] == "Health e2e"


def test_the_folder_label_sits_on_the_frame(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Shoe shop"]["id"]
    search(page, "Shoe shop")
    item_cell(page, item_id, "folder").dblclick()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_role("combobox", name="Folder")).to_have_text("No folder")
    ## a shrunk label sits on the outline, above the value
    expect(dialog.locator("label", has_text="Folder")).to_have_attribute("data-shrink", "true")
    dialog.get_by_role("button", name="Cancel").click()


def test_the_collections_dialog_adds_a_collection(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Kids school app"]["id"]
    search(page, "Kids school app")
    item_cell(page, item_id, "collections").dblclick()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Youth", exact=True).check()
    dialog.get_by_role("button", name="Save").click()
    chips = item_cell(page, item_id, "collections").locator(".MuiChip-root")
    expect(chips).to_have_text(["Shared", "Youth"])
    assert sorted(saved(page, api, "Kids school app")["collections"]) == ["Shared", "Youth"]


def test_the_collections_dialog_offers_a_collection_no_item_is_in(unlocked_page: Page, api: Api):
    page = unlocked_page
    ## a new name every run: a killed run leaves its collection behind, and a second one of the
    ## same name would match the label twice
    name = f"Empty e2e {uuid.uuid4().hex[:8]}"
    organization_id, collection_id = create_collection_elsewhere(name)
    try:
        sync_the_app(api)
        page.reload()
        grid(page)
        item_id = rows_by_name(api)["Family streaming"]["id"]
        search(page, "Family streaming")
        item_cell(page, item_id, "collections").dblclick()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_label(name, exact=True)).not_to_be_checked()
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).to_have_count(0)
    finally:
        delete_collection_elsewhere(organization_id, collection_id)
        sync_the_app(api)
    listed = api.get("/api/grid")["organizations"]
    assert not any(name in organization["collections"] for organization in listed)


def test_the_organization_dialog_says_a_collection_is_needed(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Photo sharing"]["id"]
    search(page, "Photo sharing")
    item_cell(page, item_id, "organization").dblclick()
    dialog = page.get_by_role("dialog")
    ## the dialog preselects only a sole organization, the dev vault has Family and Club
    pick(dialog, "Organization", "Family")
    hint = dialog.get_by_text("Pick at least one collection.")
    move = dialog.get_by_role("button", name="Move", exact=True)
    expect(hint).to_be_visible()
    expect(move).to_be_disabled()
    dialog.get_by_label("Shared", exact=True).check()
    expect(hint).to_have_count(0)
    expect(move).to_be_enabled()
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_have_count(0)
    expect(item_cell(page, item_id, "organization")).to_have_text("")


def test_the_organization_dialog_moves_an_item_into_the_organization(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["SIM PIN"]["id"]
    search(page, "SIM PIN")
    item_cell(page, item_id, "organization").dblclick()
    dialog = page.get_by_role("dialog")
    pick(dialog, "Organization", "Family")
    dialog.get_by_label("Shared", exact=True).check()
    dialog.get_by_role("button", name="Move", exact=True).click()
    expect(item_cell(page, item_id, "organization")).to_have_text("Family")
    cells = saved(page, api, "SIM PIN")
    assert (cells["organization"], cells["collections"]) == ("Family", ["Shared"])


def test_the_secret_dialog_is_never_prefilled(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Airline"]["id"]
    search(page, "Airline")
    item_cell(page, item_id, "login.password").dblclick()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_label("New value")).to_have_value("")
    expect(dialog.get_by_label("New value")).to_have_css("-webkit-text-security", "disc")
    dialog.get_by_role("button", name="Cancel").click()


def test_the_secret_dialog_warns_when_the_field_is_text_on_its_item(unlocked_page: Page, api: Api):
    page = unlocked_page
    column = "fields.token"
    rows = rows_by_name(api)
    item = rows["Family grocery account"]
    ## Code forge API has a hidden field of this name, so the whole column is secret. A template
    ## leaves an empty text field like this one. The write makes the app sync too.
    api.post(
        "/api/operations",
        {
            "operations": [
                {
                    "kind": "setLeaf",
                    "itemId": item["id"],
                    "revisionDate": item["revisionDate"],
                    "column": column,
                    "value": "",
                    "fieldType": 0,
                }
            ]
        },
        timeout=COMMIT_TIMEOUT * 1000,
    )
    fresh = rows_by_name(api)["Family grocery account"]
    assert column in fresh["emptyTextFields"]
    page.reload()
    grid(page)
    search(page, "Family grocery account")
    item_cell(page, item["id"], column).dblclick()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_role("alert")).to_contain_text("stored as plain text")
    expect(dialog.get_by_text("The value is stored as a secret.")).to_have_count(0)
    dialog.get_by_label("New value").fill("token-e2e-plain")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    ## the field stayed text, the API shows its value unmasked
    assert saved(page, api, "Family grocery account")[column] == "token-e2e-plain"
    ## so the grid shows it as text with the mark, with nothing to reveal
    plain = item_cell(page, item["id"], column)
    expect(plain).to_have_text("token-e2e-plain")
    expect(plain).to_have_class(re.compile(r"\bbw-plain-in-secret\b"))
    expect(plain.get_by_role("button", name=f"Reveal {column}")).to_have_count(0)

    ## the hidden token of the same column stays masked, without the mark
    hidden_id = rows["Code forge API"]["id"]
    search(page, "Code forge API")
    hidden = item_cell(page, hidden_id, column)
    expect(hidden.get_by_test_id("secret-value")).to_have_text(MASK)
    expect(hidden).not_to_have_class(re.compile(r"\bbw-plain-in-secret\b"))
    expect(hidden.get_by_role("button", name=f"Reveal {column}")).to_have_count(1)

    other = rows["Electricity contract"]
    search(page, "Electricity contract")
    item_cell(page, other["id"], column).dblclick()
    expect(dialog.get_by_text("The value is stored as a secret.")).to_be_visible()
    expect(dialog.get_by_role("alert")).to_have_count(0)
    dialog.get_by_role("button", name="Cancel").click()


def test_a_boolean_field_in_a_secret_column_gets_a_checkbox(unlocked_page: Page, api: Api):
    page = unlocked_page
    column = "fields.token"
    item = rows_by_name(api)["Tax agency"]
    ## Code forge API has a hidden field of this name, so the whole column is secret
    api.post(
        "/api/operations",
        {
            "operations": [
                {
                    "kind": "setLeaf",
                    "itemId": item["id"],
                    "revisionDate": item["revisionDate"],
                    "column": column,
                    "value": False,
                    "fieldType": 2,
                }
            ]
        },
        timeout=COMMIT_TIMEOUT * 1000,
    )
    assert column in rows_by_name(api)["Tax agency"]["booleanFields"]
    page.reload()
    grid(page)
    search(page, "Tax agency")
    item_cell(page, item["id"], column).click(button="right")
    menu = page.get_by_role("menu")
    expect(menu.get_by_role("menuitem", name="Edit value")).to_be_visible()
    expect(menu.get_by_role("menuitem", name="Generate new value")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)
    item_cell(page, item["id"], column).dblclick()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_role("alert")).to_contain_text("this is a boolean field")
    expect(dialog.get_by_text("The value is stored as a secret.")).to_have_count(0)
    expect(dialog.get_by_label("New value")).to_have_count(0)
    dialog.get_by_role("checkbox", name="True").check()
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    assert saved(page, api, "Tax agency")[column] == "true"


def menu_action(page: Page, item_id: str, column: str, action: str) -> None:
    item_cell(page, item_id, column).click(button="right")
    page.get_by_role("menuitem", name=action, exact=True).click()


def test_remove_value_empties_a_cell(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Car insurance"]["id"]
    search(page, "Car insurance")
    menu_action(page, item_id, "login.username", "Remove value")
    expect(item_cell(page, item_id, "login.username")).to_have_class(QUEUED)
    expect(item_cell(page, item_id, "login.username")).to_have_text("")
    assert "login.username" not in saved(page, api, "Car insurance")


def add_field(page: Page, item_id: str, name: str, value: str, field_type: str) -> None:
    menu_action(page, item_id, "name", "Add field")
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Name").fill(name)
    pick(dialog, "Type", field_type)
    dialog.get_by_label("Value").fill(value)
    dialog.get_by_role("button", name="Add", exact=True).click()
    expect(dialog).to_have_count(0)


def test_add_field_adds_a_text_field(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Water and waste"]["id"]
    search(page, "Water and waste")
    add_field(page, item_id, "Customer number e2e", "4711", "Text")
    expect(item_cell(page, item_id, "fields.Customer number e2e")).to_have_text("4711")
    assert saved(page, api, "Water and waste")["fields.Customer number e2e"] == "4711"


def test_add_field_adds_a_hidden_field(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Grocery delivery"]["id"]
    search(page, "Grocery delivery")
    add_field(page, item_id, "Door pin e2e", "hidden-e2e-value", "Hidden")
    target = item_cell(page, item_id, "fields.Door pin e2e")
    expect(target).to_have_text(MASK)
    assert saved(page, api, "Grocery delivery")["fields.Door pin e2e"] == {"masked": True}
    expect(target.get_by_role("button", name="Reveal fields.Door pin e2e")).to_be_visible()


def test_the_secret_dialog_warns_when_a_queued_add_field_makes_the_field_text(
    unlocked_page: Page, api: Api
):
    page = unlocked_page
    column = "fields.token"
    name = "Youth bank"
    item_id = rows_by_name(api)[name]["id"]
    settings = api.get("/api/queue/auto-commit")
    ## Add field waits in the queue while the secret dialog opens on its cell
    api.put("/api/queue/auto-commit", {"afterSeconds": 3600, "maxEntries": 1000})
    try:
        search(page, name)
        add_field(page, item_id, "token", "token-e2e-start", "Text")
        target = item_cell(page, item_id, column)
        expect(target).to_have_class(QUEUED)
        expect(
            page.get_by_role("button", name=re.compile(r"^Queue, 1 unsaved edits"))
        ).to_be_visible()
        target.dblclick()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_role("alert")).to_contain_text("stored as plain text")
        expect(dialog.get_by_text("The value is stored as a secret.")).to_have_count(0)
        dialog.get_by_label("New value").fill("token-e2e-queued")
        dialog.get_by_role("button", name="Save").click()
        expect(dialog).to_have_count(0)
        expect(
            page.get_by_role("button", name=re.compile(r"^Queue, 2 unsaved edits"))
        ).to_be_visible()
        page.get_by_role("button", name="Commit now").click()
        ## both entries apply, the field is text and holds the value the dialog warned about
        assert saved(page, api, name)[column] == "token-e2e-queued"
    finally:
        api.put(
            "/api/queue/auto-commit",
            {"afterSeconds": settings["afterSeconds"], "maxEntries": settings["maxEntries"]},
        )


def test_move_to_trash_asks_first(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Conference room booking"]["id"]
    search(page, "Conference room booking")
    menu_action(page, item_id, "name", "Move to trash")
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("Conference room booking")
    dialog.get_by_role("button", name="Move to trash").click()
    api.wait_saved()
    expect(row(page, item_id)).to_have_count(0)
    assert "Conference room booking" not in rows_by_name(api)
    assert item_id in trashed_ids(), "the item is not in the trash"


def test_an_edit_of_an_item_changed_elsewhere_is_refused(unlocked_page: Page, api: Api):
    page = unlocked_page
    rows = rows_by_name(api)
    changed = rows["Proxmox"]
    fixture_notes = changed["cells"].get("notes")
    search(page, "Proxmox")
    ## a new value every run, so a run on a vault an earlier run changed sees a change too
    notes = f"Edited on another device {uuid.uuid4().hex[:8]}"
    edit_notes_elsewhere(changed["id"], notes)
    try:
        sync_the_app(api)
        target = item_cell(page, changed["id"], "login.username")
        target.click()
        page.keyboard.press("Enter")
        page.locator(".ag-cell-inline-editing input").fill("expenses-e2e")
        page.keyboard.press("Enter")
        ## a failed commit of an earlier test can show its own alert next to this one
        alert = page.get_by_role("alert").filter(has_text="Nothing was saved")
        expect(alert).to_contain_text("Proxmox")
        notes_cell = item_cell(page, changed["id"], "notes")
        expect(row(page, changed["id"])).to_have_class(re.compile(r"\bbw-conflict-row\b"))
        expect(notes_cell).to_have_class(re.compile(r"\bbw-conflict-cell\b"))
        expect(notes_cell).to_have_text(notes)
        pending = api.get("/api/queue?status=pending")
        assert not any(changed["id"] in str(entry["operation"]) for entry in pending)
        assert rows_by_name(api)["Proxmox"]["cells"]["login.username"] != "expenses-e2e"
    finally:
        put_notes_back_elsewhere(changed["id"], fixture_notes)


def give_notes(page: Page, api: Api, name: str) -> str:
    """Give a login item notes through the scripts' route (the fixture's logins have none),
    reload the page so the grid shows them and answer the item's id."""
    item = rows_by_name(api)[name]
    api.post(
        "/api/operations",
        {
            "operations": [
                {
                    "kind": "setLeaf",
                    "itemId": item["id"],
                    "revisionDate": item["revisionDate"],
                    "column": "notes",
                    "value": f"Notes to remove {uuid.uuid4().hex[:8]}",
                }
            ]
        },
        timeout=COMMIT_TIMEOUT * 1000,
    )
    page.reload()
    grid(page)
    search(page, name)
    return item["id"]


def set_secret(page: Page, item_id: str, column: str, value: str) -> None:
    item_cell(page, item_id, column).dblclick()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("New value").fill(value)
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)


def edit_notes(page: Page, item_id: str, notes: str) -> None:
    target = item_cell(page, item_id, "notes")
    target.dblclick()
    page.locator(".ag-popup-editor textarea").fill(notes)
    page.get_by_text(re.compile(r"^\d+ of \d+ items$")).click()
    expect(target).to_have_class(QUEUED)


def committed(api: Api, after: int, count: int) -> None:
    """Wait until count UI entries newer than after are queued and the auto-commit decided
    them, and fail unless every one is applied. The message names the summary, the error and
    the revisionDates of a conflict, never a value."""
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while True:
        new = [entry for entry in api.get("/api/queue") if entry["id"] > after]
        if len(new) >= count and all(e["status"] not in ("pending", "approved") for e in new):
            break
        assert time.monotonic() < deadline, f"{count} entries were not committed"
        time.sleep(0.5)
    failed = [(entry["summary"], entry["result"]) for entry in new if entry["status"] != "applied"]
    assert not failed, f"entries not applied: {failed}"
    ## an entry is applied right after its write, the grid's rows follow after the reload
    api.wait_saved()


## A password set on an item in the grid and then a removal of its notes, without reloading the
## page: the second change must not fail as changed since it was queued. Each change is
## committed by the auto-commit before the next is made, the grid holds the item as the commit
## before left it or, when it did not reload yet, as it was before.
def test_a_password_and_then_a_removal_of_the_notes_both_apply(unlocked_page: Page, api: Api):
    page = unlocked_page
    name = "Youth mail"
    item_id = give_notes(page, api, name)
    value = f"e2e-{uuid.uuid4().hex}"
    after = last_entry_id(api)
    set_secret(page, item_id, "login.password", value)
    committed(api, after, 1)
    menu_action(page, item_id, "notes", "Remove value")
    committed(api, after, 2)
    item = rows_by_name(api)[name]
    removed = "notes" not in item["cells"]
    assert removed, "the notes are still there"
    assert secret_is(api, item, "login.password", value), "the password is not the new one"


def test_a_removal_of_the_notes_and_then_a_password_both_apply(unlocked_page: Page, api: Api):
    page = unlocked_page
    name = "Home router"
    item_id = give_notes(page, api, name)
    value = f"e2e-{uuid.uuid4().hex}"
    after = last_entry_id(api)
    menu_action(page, item_id, "notes", "Remove value")
    committed(api, after, 1)
    set_secret(page, item_id, "login.password", value)
    committed(api, after, 2)
    item = rows_by_name(api)[name]
    removed = "notes" not in item["cells"]
    assert removed, "the notes are still there"
    assert secret_is(api, item, "login.password", value), "the password is not the new one"


def test_a_username_and_then_the_notes_both_apply(unlocked_page: Page, api: Api):
    page = unlocked_page
    name = "Gaming desktop"
    item_id = rows_by_name(api)[name]["id"]
    search(page, name)
    after = last_entry_id(api)
    type_into(page, item_id, "login.username", "gamer-e2e")
    committed(api, after, 1)
    edit_notes(page, item_id, "Notes after the username e2e")
    committed(api, after, 2)
    cells = rows_by_name(api)[name]["cells"]
    assert cells.get("login.username") == "gamer-e2e"
    assert cells.get("notes") == "Notes after the username e2e"


def tool_note_row(page: Page, api: Api) -> str:
    item_id = api.get("/api/grid")["toolNoteId"]
    search(page, "bwtidy")
    expect(row(page, item_id)).to_be_visible()
    return item_id


def test_the_tool_note_opens_no_editor(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = tool_note_row(page, api)
    for column in ("name", "notes", "folder", "favorite"):
        item_cell(page, item_id, column).dblclick()
        page.wait_for_timeout(500)
        expect(editor(page)).to_have_count(0)
        item_cell(page, item_id, column).click()
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        expect(editor(page)).to_have_count(0)


def test_the_tool_note_menu_has_every_action_disabled(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = tool_note_row(page, api)
    item_cell(page, item_id, "notes").click(button="right")
    items = page.get_by_role("menuitem")
    expect(items.first).to_be_visible()
    for index in range(items.count()):
        expect(items.nth(index)).to_be_disabled()
    expect(page.get_by_role("menuitem", name="Remove value")).to_have_count(0)


def test_a_template_saves_although_the_note_is_read_only(unlocked_page: Page, api: Api):
    page = unlocked_page
    page.get_by_role("button", name="Types", exact=True).click()
    page.get_by_role("button", name="New template").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Name", exact=True).fill("E2E router")
    add_new_field(dialog, "Router e2e")
    dialog.get_by_role("button", name="Save").click()
    table = page.get_by_role("table", name="Templates")
    expect(table).to_contain_text("E2E router")
    expect(page.get_by_text("Saving", exact=True)).to_have_count(0, timeout=120_000)
    names = [template["name"] for template in api.get("/api/templates")["templates"]]
    assert "E2E router" in names
    ## other tests read the templates, so this one goes again
    page.get_by_role("button", name="Delete E2E router").click()
    page.get_by_role("dialog").get_by_role("button", name="Delete").click()
    expect(table.get_by_text("E2E router")).to_have_count(0)
    expect(page.get_by_text("Saving", exact=True)).to_have_count(0, timeout=120_000)
