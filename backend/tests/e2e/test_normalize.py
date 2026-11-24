"""Renaming and merging fields, near duplicates and merging items.

Every test works on items and fields no other test here touches, so each one runs on its own.
Values are compared in the test and never shown: an assert names items and columns only.
Each test puts the vault back as it found it (the fixture vault_put_back), so other files and
a second run of this one see the seeded vault.
"""

from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import Api, commit_new_entries, last_entry_id, rows_by_name, secret_is
from support.elsewhere import trashed_ids
from support.pages import cell, pick

## the notes with a text field username, and the ones with a hidden field Password
USERNAME_ITEMS = (
    "Old webshop",
    "Printer admin",
    "School portal",
    "Camera NVR",
    "Switch admin",
    "Alarm system",
)
PASSWORD_ITEMS = ("Old webshop", "Printer admin", "School portal", "Camera NVR", "Home wifi")
## it has a hidden field password already
LOWER_PASSWORD_ITEMS = ("Switch admin",)


pytestmark = pytest.mark.usefixtures("vault_put_back")


def field(item: dict[str, Any], name: str) -> str:
    return next(f["value"] for f in item["fields"] if f["name"] == name)


def show_columns_of(page: Page, item: str) -> None:
    """Search for the item and hide the empty columns, so its field headers are in view."""
    page.get_by_label("Search").fill(item)
    expect(page.locator(".ag-cell[col-id='name']").get_by_text(item, exact=True)).to_have_count(1)
    page.get_by_label("Hide empty columns").check()
    ## For a moment after the check the grid's scroll area keeps the width of every column. A
    ## hover that scrolls then (as Playwright's does) can scroll past the real end, and the grid
    ## pulls the headers back under the pointer, onto another column. A person scrolls a grid
    ## that has settled, so wait until it has its new width.
    page.wait_for_function(
        """() => {
            const area = document.querySelector('.ag-grid-viewport')
            const bar = document.querySelector('.ag-body-horizontal-scroll-viewport')
            return area && bar && area.scrollWidth <= bar.scrollWidth
        }"""
    )


def open_rename(page: Page, item: str, column: str) -> None:
    show_columns_of(page, item)
    page.get_by_role("button", name=f"Actions for {column}", exact=True).click()
    page.get_by_role("menuitem", name="Rename field…").click()


def test_rename_field_renames_it_in_every_item(unlocked_page: Page, api: Api, fixture_items):
    page = unlocked_page
    open_rename(page, "Old webshop", "fields.username")
    dialog = page.get_by_role("dialog", name="Rename field username")
    expect(dialog).to_contain_text(f"every item that has it ({len(USERNAME_ITEMS)} items)")

    after = last_entry_id(api)
    dialog.get_by_label("New name").fill("login name")
    dialog.get_by_role("button", name="Rename", exact=True).click()
    expect(dialog).to_be_hidden()
    commit_new_entries(api, after)

    rows = rows_by_name(api)
    left = [name for name, row in rows.items() if "fields.username" in row["cells"]]
    assert not left, f"still with fields.username: {left}"
    wrong = [
        name
        for name in USERNAME_ITEMS
        if rows[name]["cells"].get("fields.login name") != field(fixture_items[name], "username")
    ]
    assert not wrong, f"fields.login name is not the old username in: {wrong}"


def test_rename_onto_an_existing_name_merges(unlocked_page: Page, api: Api, fixture_items):
    page = unlocked_page
    open_rename(page, "Old webshop", "fields.Password")
    dialog = page.get_by_role("dialog", name="Rename field Password")
    dialog.get_by_label("New name").fill("password")
    expect(dialog.get_by_role("alert")).to_have_text(
        "A field named password exists already, so this merges fields.Password into "
        "fields.password. Items with values in both are listed for a choice."
    )

    after = last_entry_id(api)
    dialog.get_by_role("button", name="Merge", exact=True).click()
    expect(dialog).to_be_hidden()
    commit_new_entries(api, after)

    rows = rows_by_name(api)
    left = [name for name, row in rows.items() if "fields.Password" in row["cells"]]
    assert not left, f"still with fields.Password: {left}"
    expected = {name: field(fixture_items[name], "Password") for name in PASSWORD_ITEMS}
    expected |= {name: field(fixture_items[name], "password") for name in LOWER_PASSWORD_ITEMS}
    wrong = [
        name
        for name, value in expected.items()
        if not secret_is(api, rows[name], "fields.password", value)
    ]
    assert not wrong, f"fields.password does not hold the old value in: {wrong}"


def test_merge_columns_asks_which_value_to_keep(
    unlocked_page: Page, api: Api, fixture_items, assert_no_secrets
):
    page = unlocked_page
    item = "Gaming desktop"
    kept = "fields.LUKS/dm-crypt and Bitlocker"
    target = "fields.Bitlocker RecoveryPassword"
    page.get_by_role("button", name="Columns").click()
    page.get_by_role("checkbox", name=f"Select {kept} for merge").check()
    page.get_by_role("checkbox", name=f"Select {target} for merge").check()
    page.get_by_role("button", name="Merge selected columns").click()

    dialog = page.get_by_role("dialog", name="Merge columns")
    pick(page, "Target column", target)
    expect(dialog).to_contain_text(
        "1 of them has more than one value, you choose which to keep next."
    )
    dialog.get_by_role("button", name="Merge", exact=True).click()

    choices = page.get_by_role("dialog", name="Choose the values to keep")
    expect(choices.get_by_role("radiogroup")).to_have_count(1)
    choice = choices.get_by_role("radiogroup", name=item)
    expect(choice.get_by_role("radio")).to_have_count(2)
    assert_no_secrets(choices.inner_text())

    after = last_entry_id(api)
    choice.get_by_role("radio", name=kept, exact=True).check()
    choices.get_by_role("button", name="Merge", exact=True).click()
    expect(choices).to_be_hidden()
    commit_new_entries(api, after)

    row = rows_by_name(api)[item]
    gone = kept not in row["cells"]
    assert gone, f"{item} still has {kept}"
    value = field(fixture_items[item], kept.removeprefix("fields."))
    moved = secret_is(api, row, target, value)
    assert moved, f"{target} of {item} is not the value that was kept"


def test_near_duplicates_get_a_marker(unlocked_page: Page):
    page = unlocked_page
    show_columns_of(page, "ThinkPad X1")
    marker = page.get_by_role("button", name="Near duplicate of fields.user", exact=True)
    expect(marker).to_be_visible()
    marker.hover()
    ## the grid's own header tooltip of the column shows as well, after its delay
    tooltip = page.get_by_role("tooltip").filter(has_text="Near duplicate of")
    expect(tooltip).to_contain_text("Near duplicate of fields.user")
    expect(tooltip.get_by_role("button", name="Merge these")).to_be_visible()


def test_a_field_without_near_duplicates_has_no_marker(unlocked_page: Page):
    page = unlocked_page
    show_columns_of(page, "Code forge API")
    expect(page.get_by_role("button", name="Actions for fields.token", exact=True)).to_be_visible()
    expect(page.get_by_test_id("near-duplicate-marker")).to_have_count(0)


def test_merge_these_merges_the_near_duplicates(unlocked_page: Page, api: Api, fixture_items):
    page = unlocked_page
    source = "fields.user password"
    target = "fields.User password"
    show_columns_of(page, "Media PC")
    page.get_by_role("button", name=f"Near duplicate of {target}", exact=True).hover()
    page.get_by_role("tooltip").get_by_role("button", name="Merge these").click()

    dialog = page.get_by_role("dialog", name="Merge columns")
    pick(page, "Target column", target)
    expect(dialog).to_contain_text(f"{source} moves into {target} in 1 item.")
    after = last_entry_id(api)
    dialog.get_by_role("button", name="Merge", exact=True).click()
    expect(dialog).to_be_hidden()
    commit_new_entries(api, after)

    rows = rows_by_name(api)
    left = [name for name, row in rows.items() if source in row["cells"]]
    assert not left, f"still with {source}: {left}"
    media = field(fixture_items["Media PC"], "user password")
    moved = secret_is(api, rows["Media PC"], target, media)
    assert moved, f"{target} of Media PC is not its old value"
    thinkpad = field(fixture_items["ThinkPad X1"], "User password")
    kept = secret_is(api, rows["ThinkPad X1"], target, thinkpad)
    assert kept, f"{target} of ThinkPad X1 changed"


def open_merge_items(page: Page, source: str, target: str) -> Locator:
    """Open Merge into item… on the source's row and pick the target, answer the dialog."""
    cell(page, source, "name").click(button="right")
    page.get_by_role("menuitem", name="Merge into item…").click()
    dialog = page.get_by_role("dialog", name=f"Merge {source} into another item")
    dialog.get_by_label("Target item").fill(target)
    page.get_by_role("option", name=target).click()
    expect(dialog.get_by_role("table", name="Mapping")).to_be_visible()
    return dialog


def mapping_row(dialog: Locator, column: str) -> Locator:
    return dialog.get_by_role("row").filter(
        has=dialog.page.get_by_role("cell", name=column, exact=True)
    )


def test_merge_items_shows_the_targets_values(unlocked_page: Page, fixture_items):
    page = unlocked_page
    dialog = open_merge_items(page, "Hardware store", "Pharmacy")
    expect(dialog.get_by_role("columnheader", name="Pharmacy has")).to_be_visible()
    has = mapping_row(dialog, "login.uris").get_by_role("cell").nth(3)
    expect(has).not_to_be_empty()
    uri = fixture_items["Pharmacy"]["login"]["uris"][0]["uri"]
    shown = uri in has.inner_text()
    assert shown, "the Pharmacy has column does not show the target's URI"


def test_merge_items_offers_dont_move(unlocked_page: Page):
    page = unlocked_page
    dialog = open_merge_items(page, "Hardware store", "Pharmacy")
    dialog.get_by_role("combobox", name="Target for login.username").click()
    expect(page.get_by_role("option", name="Don't move", exact=True)).to_be_visible()
    expect(page.get_by_role("option", name="Leave it")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(dialog.get_by_text("Leave it")).to_have_count(0)


def test_merge_items_adds_uris_by_default(unlocked_page: Page):
    page = unlocked_page
    dialog = open_merge_items(page, "Hardware store", "Pharmacy")
    expect(dialog.get_by_role("combobox", name="Target for login.uris")).to_have_text("login.uris")
    choice = dialog.get_by_role("radiogroup", name="List login.uris")
    expect(choice.get_by_role("radio", name="Add to the list")).to_be_checked()


def test_merge_items_moves_the_values_and_trashes_the_source(
    unlocked_page: Page, api: Api, fixture_items
):
    page = unlocked_page
    source = fixture_items["Shoe shop"]["login"]
    target = fixture_items["Photo sharing"]["login"]
    own = source["uris"][0]["uri"]
    shared = target["uris"][0]["uri"]
    ## a URI both have, which must not end up twice
    rows = rows_by_name(api)
    before = rows["Shoe shop"]
    after = last_entry_id(api)
    api.post(
        "/api/queue",
        {
            "operations": [
                {
                    "kind": "setLeaf",
                    "itemId": before["id"],
                    "revisionDate": before["revisionDate"],
                    "column": "login.uris",
                    "value": [own, shared],
                }
            ]
        },
    )
    commit_new_entries(api, after)
    page.reload()
    expect(page.locator(".ag-row").first).to_be_visible()

    dialog = open_merge_items(page, "Shoe shop", "Photo sharing")
    after = last_entry_id(api)
    dialog.get_by_role("button", name="Merge", exact=True).click()
    expect(dialog).to_be_hidden()
    commit_new_entries(api, after)

    rows = rows_by_name(api)
    merged = rows["Photo sharing"]
    gone = "Shoe shop" not in rows
    assert gone, "Shoe shop is still in the vault"
    trashed = before["id"] in trashed_ids()
    assert trashed, "Shoe shop is not in the trash"
    uris_ok = merged["cells"].get("login.uris") == [shared, own]
    assert uris_ok, "login.uris of Photo sharing is not its own URI and then the new one, once each"
    username_ok = merged["cells"].get("fields.username") == source["username"]
    assert username_ok, "fields.username of Photo sharing is not the username of Shoe shop"
    password_ok = secret_is(api, merged, "fields.password", source["password"])
    assert password_ok, "fields.password of Photo sharing is not the password of Shoe shop"
    kept = merged["cells"].get("login.username") == target["username"]
    assert kept, "login.username of Photo sharing changed"
