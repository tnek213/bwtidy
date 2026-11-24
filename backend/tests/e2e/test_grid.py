"""The grid: columns, search, filters, secrets and column widths.

A secret value is compared as a bool and never handed to expect(): a failure message would
show it.
"""

import re
import time
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import Api, commit_new_entries, last_entry_id, rows_by_name
from support.elsewhere import trashed_ids
from support.pages import MASK, ROW, editor, hover_tooltip, item_cell, pick, row, search, settle

COUNT = re.compile(r"^(\d+ of )?\d+ items$")
## the revealed value hides after 30 s (REVEAL_MS in frontend/src/grid/cells.tsx)
REVEAL_SECONDS = 30
MAX_AUTOSIZE_CHARS = 50


def header(page: Page, column: str) -> Locator:
    return page.locator(f'.ag-header-row-column .ag-header-cell[col-id="{column}"]')


def count_text(page: Page) -> Locator:
    return page.get_by_text(COUNT)


def secret_text(target: Locator) -> str:
    return target.get_by_test_id("secret-value").inner_text()


def attribute(target: Locator, name: str) -> str:
    value = target.get_attribute(name)
    assert value is not None, f"no attribute {name}"
    return value


def wait_masked(target: Locator, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while secret_text(target) != MASK:
        if time.monotonic() > deadline:
            return False
        target.page.wait_for_timeout(500)
    return True


def test_name_column_is_pinned_left(unlocked_page: Page):
    pinned = unlocked_page.locator(".ag-header-row-column .ag-grid-pinned-left-cells")
    expect(pinned.locator('[col-id="name"]')).to_be_visible()


def test_group_headers_carry_the_coordinate_prefix(unlocked_page: Page):
    groups = unlocked_page.locator(".ag-header-group-cell")
    expect(groups).to_have_text(
        [
            "Item",
            "Login (login)",
            "Card (card)",
            "Identity (identity)",
            "SSH key (sshKey)",
            "Custom fields (fields)",
        ]
    )


@pytest.mark.parametrize(
    ("column", "label"),
    [
        ("name", "name"),
        ("login.username", "username"),
        ("card.number", "number"),
        ("identity.firstName", "firstName"),
        ("sshKey.publicKey", "publicKey"),
        ("fields.WPA key", "WPA key"),
    ],
)
def test_column_headers_leave_the_prefix_out(unlocked_page: Page, column: str, label: str):
    ## a field column's header ends in the button of its menu
    text = header(unlocked_page, column).locator(".ag-header-cell-text")
    expect(text).to_have_text(re.compile(rf"^{re.escape(label)}⋮?$"))


def test_header_tooltip_counts_the_items_with_a_value(unlocked_page: Page, api: Api):
    page = unlocked_page
    rows = api.get("/api/grid")["rows"]
    count = sum(1 for row in rows if row["cells"].get("card.number") not in (None, "", False, []))
    assert count > 1
    number = header(page, "card.number")
    expect(hover_tooltip(page, number)).to_have_text(f"card.number, in {count} items")
    ## a search shows fewer rows, the count stays the vault's
    search(page, "Wiki")
    expect(hover_tooltip(page, number)).to_have_text(f"card.number, in {count} items")


def test_uris_and_collections_show_as_chips(unlocked_page: Page, api: Api):
    page = unlocked_page
    rows = rows_by_name(api)
    search(page, "Wiki")
    uris = item_cell(page, rows["Wiki"]["id"], "login.uris").locator(".MuiChip-root")
    expect(uris).to_have_count(len(rows["Wiki"]["cells"]["login.uris"]))
    search(page, "Family streaming")
    collections = item_cell(page, rows["Family streaming"]["id"], "collections")
    expect(collections.locator(".MuiChip-root")).to_have_text(["Shared"])


def test_the_three_favorites_have_a_check_mark(unlocked_page: Page, api: Api):
    page = unlocked_page
    rows = rows_by_name(api)
    ## descending, so every favorite comes first
    label = header(page, "favorite").locator(".ag-header-cell-label")
    label.click()
    label.click()
    expect(header(page, "favorite")).to_have_attribute("aria-sort", "descending")
    settle(page)
    ## other tests may make an item a favorite
    favorites = {row["id"] for row in rows.values() if row["cells"].get("favorite")}
    assert {rows[name]["id"] for name in ("Wiki", "Streaming video", "Dashboards")} <= favorites
    first = [
        attribute(page.locator(f'{ROW}[row-index="{index}"]'), "row-id")
        for index in range(len(favorites) + 1)
    ]
    assert set(first[:-1]) == favorites, "the first rows are not the favorites"
    for item_id in first[:-1]:
        expect(item_cell(page, item_id, "favorite")).to_have_text("✓")
    expect(item_cell(page, first[-1], "favorite")).to_have_text("")


def test_the_count_says_how_many_items_show(unlocked_page: Page, api: Api):
    page = unlocked_page
    total = len(rows_by_name(api))
    expect(count_text(page)).to_have_text(f"{total} items")
    search(page, "Dashboards")
    expect(count_text(page)).to_have_text(re.compile(rf"^[1-9]\d* of {total} items$"))


def test_identity_and_ssh_key_items_fill_their_groups(
    unlocked_page: Page, api: Api, fixture_items: dict[str, dict[str, Any]]
):
    page = unlocked_page
    rows = rows_by_name(api)
    identity = fixture_items["Dev Identity"]["identity"]
    search(page, "Dev Identity")
    item_id = rows["Dev Identity"]["id"]
    expect(item_cell(page, item_id, "identity.firstName")).to_have_text(identity["firstName"])
    expect(item_cell(page, item_id, "identity.city")).to_have_text(identity["city"])
    search(page, "Build server key")
    item_id = rows["Build server key"]["id"]
    public_key = fixture_items["Build server key"]["sshKey"]["publicKey"]
    expect(item_cell(page, item_id, "sshKey.publicKey")).to_have_text(public_key)
    private_key = item_cell(page, item_id, "sshKey.privateKey")
    masked = secret_text(private_key) == MASK
    assert masked, "the private key is not masked"


def test_search_keeps_the_rows_with_every_word(unlocked_page: Page, api: Api):
    page = unlocked_page
    rows = rows_by_name(api)
    search(page, "streaming family")
    expect(item_cell(page, rows["Family streaming"]["id"], "name")).to_be_visible()
    expect(item_cell(page, rows["Streaming video"]["id"], "name")).to_have_count(0)


def test_search_finds_words_in_notes(unlocked_page: Page, api: Api):
    page = unlocked_page
    rows = rows_by_name(api)
    search(page, "living room")
    expect(item_cell(page, rows["Media PC"]["id"], "name")).to_be_visible()
    expect(count_text(page)).to_have_text(re.compile(r"^1 of \d+ items$"))


def test_search_never_matches_a_secret(
    unlocked_page: Page, api: Api, fixture_items: dict[str, dict[str, Any]]
):
    page = unlocked_page
    rows = rows_by_name(api)
    search(page, fixture_items["Payroll"]["login"]["password"])
    expect(count_text(page)).to_have_text(re.compile(r"^0 of \d+ items$"))
    expect(item_cell(page, rows["Payroll"]["id"], "name")).to_have_count(0)


def test_search_tints_only_the_matching_cells(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Dashboards"]["id"]
    search(page, "dashboards")
    hit = re.compile(r"\bbw-search-hit\b")
    expect(item_cell(page, item_id, "name")).to_have_class(hit)
    expect(item_cell(page, item_id, "folder")).not_to_have_class(hit)
    expect(item_cell(page, item_id, "login.username")).not_to_have_class(hit)


def holds(shown: Any, value: str) -> bool:
    return value in shown if isinstance(shown, list) else shown == value


@pytest.mark.parametrize(
    ("label", "option", "column", "value"),
    [
        ("Type", "Card", "type", "card"),
        ("Folder", "Work", "folder", "Work"),
        ("Organization", "Family", "organization", "Family"),
        ("Collection", "Youth", "collections", "Youth"),
    ],
)
def test_a_filter_keeps_its_rows(
    unlocked_page: Page, api: Api, label: str, option: str, column: str, value: str
):
    page = unlocked_page
    rows = rows_by_name(api)
    kept = [name for name, row in rows.items() if holds(row["cells"].get(column), value)]
    pick(page, label, option)
    settle(page)
    expect(count_text(page)).to_have_text(f"{len(kept)} of {len(rows)} items")
    shown = page.locator(f"{ROW} .ag-grid-pinned-left-cells .ag-cell[col-id='name']")
    expect(shown).to_have_count(len(kept))
    assert sorted(shown.all_inner_texts()) == sorted(kept)


def test_hide_empty_columns_follows_the_filter(unlocked_page: Page):
    page = unlocked_page
    pick(page, "Type", "Card")
    page.get_by_label("Hide empty columns").check()
    expect(header(page, "card.number")).to_be_visible()
    expect(header(page, "login.username")).to_have_count(0)
    pick(page, "Type", "All")
    expect(header(page, "login.username")).to_be_visible()


def test_the_column_chooser_hides_and_shows_a_column(unlocked_page: Page):
    page = unlocked_page
    page.get_by_role("button", name="Columns", exact=True).click()
    chooser = page.get_by_role("presentation").filter(has=page.get_by_label("Find column"))
    chooser.get_by_label("notes", exact=True).uncheck()
    expect(header(page, "notes")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Columns (1 hidden)")).to_be_visible()
    page.get_by_role("button", name="Columns (1 hidden)").click()
    chooser.get_by_role("button", name="Show all").click()
    expect(header(page, "notes")).to_be_visible()


def test_the_column_chooser_group_checkbox_hides_and_shows_a_group(unlocked_page: Page):
    page = unlocked_page
    page.get_by_role("button", name="Columns", exact=True).click()
    chooser = page.get_by_role("presentation").filter(has=page.get_by_label("Find column"))
    group = chooser.get_by_role("checkbox", name="Login", exact=True)
    group.uncheck()
    expect(header(page, "login.username")).to_have_count(0)
    group.check()
    expect(header(page, "login.username")).to_be_visible()


def test_reveal_shows_the_value_and_hide_masks_it(
    unlocked_page: Page, api: Api, fixture_items: dict[str, dict[str, Any]]
):
    page = unlocked_page
    item_id = rows_by_name(api)["Payroll"]["id"]
    search(page, "Payroll")
    password = item_cell(page, item_id, "login.password")
    masked = secret_text(password) == MASK
    assert masked, "the password is not masked"
    password.get_by_role("button", name="Reveal login.password").click()
    expect(password.get_by_role("button", name="Hide login.password")).to_be_visible()
    shown = secret_text(password) == fixture_items["Payroll"]["login"]["password"]
    assert shown, "the revealed password differs from the fixture"
    password.get_by_role("button", name="Hide login.password").click()
    assert wait_masked(password, 5), "the password shows after Hide"


def test_a_revealed_value_hides_after_30_seconds(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Timesheet"]["id"]
    search(page, "Timesheet")
    password = item_cell(page, item_id, "login.password")
    password.get_by_role("button", name="Reveal login.password").click()
    expect(password.get_by_role("button", name="Hide login.password")).to_be_visible()
    assert secret_text(password) != MASK, "the password was not revealed"
    started = time.monotonic()
    assert wait_masked(password, REVEAL_SECONDS + 10), "the password still shows after 40 s"
    assert time.monotonic() - started > REVEAL_SECONDS - 5, "the password hid too early"


## a click on the eye focuses its cell, and AG Grid keeps the row of the focused cell rendered
## when it scrolls out, so the row stays in the DOM, out of view
def test_a_revealed_value_hides_when_its_row_scrolls_out(unlocked_page: Page):
    page = unlocked_page
    row = page.locator(f'{ROW}[row-index="0"]')
    item_id = attribute(row, "row-id")
    reveal = row.get_by_role("button", name=re.compile(r"^Reveal ")).first
    column = attribute(reveal, "aria-label").removeprefix("Reveal ")
    reveal.click()
    secret = item_cell(page, item_id, column)
    expect(secret.get_by_role("button", name=f"Hide {column}")).to_be_visible()
    viewport = page.locator(".ag-grid-viewport")
    viewport.evaluate("element => { element.scrollTop = element.scrollHeight }")
    expect(row).not_to_be_in_viewport()
    viewport.evaluate("element => { element.scrollTop = 0 }")
    expect(secret).to_be_visible()
    masked = secret_text(secret) == MASK
    assert masked, "the value shows again after scrolling back"


def char_width(page: Page) -> float:
    """The width of one character in the grid's cell font, as the autosize cap counts it."""
    return page.locator(".ag-cell").first.evaluate(
        """cell => {
            const style = getComputedStyle(cell)
            const context = new OffscreenCanvas(1, 1).getContext('2d')
            context.font = style.font
            return context.measureText('0').width
        }"""
    )


def width(target: Locator) -> float:
    box = target.bounding_box()
    assert box is not None
    return box["width"]


def test_a_double_click_on_a_column_edge_fits_the_content(unlocked_page: Page):
    page = unlocked_page
    before = width(header(page, "type"))
    header(page, "type").locator(".ag-header-cell-resize").dblclick()
    expect(header(page, "type")).not_to_have_css("width", f"{before:.0f}px")
    clipped = page.locator('.ag-cell[col-id="type"]').evaluate_all(
        "cells => cells.filter(cell => cell.scrollWidth > cell.clientWidth).length"
    )
    assert clipped == 0, f"{clipped} type cells are clipped after the fit"


def test_a_fitted_column_is_at_most_about_50_characters_wide(unlocked_page: Page, api: Api):
    page = unlocked_page
    ## a note longer than 50 characters, alone in the grid so its row is rendered
    long_note = "Proton recovery phrase"
    assert len(rows_by_name(api)[long_note]["cells"]["notes"]) > MAX_AUTOSIZE_CHARS
    search(page, long_note)
    expect(count_text(page)).to_have_text(re.compile(r"^1 of \d+ items$"))
    header(page, "notes").locator(".ag-header-cell-resize").dblclick()
    page.wait_for_timeout(500)
    chars = width(header(page, "notes")) / char_width(page)
    assert MAX_AUTOSIZE_CHARS - 5 < chars < MAX_AUTOSIZE_CHARS + 10, f"{chars:.0f} characters"


def test_dragging_a_column_edge_goes_past_the_cap(unlocked_page: Page):
    page = unlocked_page
    handle = header(page, "notes").locator(".ag-header-cell-resize").bounding_box()
    assert handle is not None
    x, y = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 600, y, steps=10)
    page.mouse.up()
    chars = width(header(page, "notes")) / char_width(page)
    assert chars > MAX_AUTOSIZE_CHARS + 20, f"{chars:.0f} characters after dragging"


def test_a_second_field_waits_for_the_first(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Ansible vault"]["id"]
    search(page, "Ansible vault")
    waiting = item_cell(page, item_id, "fields.WPA key#2")
    expect(hover_tooltip(page, waiting)).to_contain_text("WPA key")
    waiting.dblclick()
    page.wait_for_timeout(700)
    expect(editor(page)).to_have_count(0)


def test_both_fields_of_a_name_are_editable(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Home router"]["id"]
    search(page, "Home router")
    for column in ("fields.WPA key", "fields.WPA key#2"):
        item_cell(page, item_id, column).dblclick()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_contain_text(f"{column} of Home router")
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).to_have_count(0)


def test_ticked_items_move_to_the_trash_with_one_confirmation(
    unlocked_page: Page, api: Api, vault_put_back: None
):
    """vault_put_back restores both items at the end."""
    page = unlocked_page
    rows = rows_by_name(api)
    ids = [rows[name]["id"] for name in ("Car insurance", "Customer CRM")]
    ## a click on a cell selects nothing
    item_cell(page, ids[0], "login.username").click()
    expect(page.get_by_text("selected", exact=False)).to_have_count(0)
    for item_id in ids:
        row(page, item_id).get_by_role("checkbox").check()
    expect(page.get_by_text("2 selected")).to_be_visible()

    after = last_entry_id(api)
    page.get_by_role("button", name="Move to trash").click()
    dialog = page.get_by_role("dialog", name="Move 2 items to the trash?")
    expect(dialog).to_contain_text("Car insurance")
    expect(dialog).to_contain_text("Customer CRM")
    expect(dialog).to_contain_text("restored from the trash in Bitwarden or from the Change log")
    dialog.get_by_role("button", name="Move to trash").click()
    commit_new_entries(api, after, 2)

    expect(page.get_by_text("selected", exact=False)).to_have_count(0)
    assert not set(ids) & {r["id"] for r in api.get("/api/grid")["rows"]}
    assert set(ids) <= trashed_ids()


def test_the_selection_bar_sits_below_the_grid_and_the_rows_stay_put(unlocked_page: Page, api: Api):
    """Ticking a row must not move it, so the same click unticks it."""
    page = unlocked_page
    item_id = rows_by_name(api)["Car insurance"]["id"]
    checkbox = row(page, item_id).get_by_role("checkbox")
    before = checkbox.bounding_box()
    assert before is not None
    checkbox.check()
    bar = page.get_by_role("toolbar", name="Selected items")
    expect(bar).to_be_visible()
    grid = page.locator(".ag-root-wrapper").bounding_box()
    bar_box = bar.bounding_box()
    assert grid is not None and bar_box is not None
    assert bar_box["y"] >= grid["y"] + grid["height"] - 1
    viewport = page.viewport_size
    assert viewport is not None
    assert bar_box["y"] + bar_box["height"] <= viewport["height"]
    assert checkbox.bounding_box() == before
    ## the same point unticks it and the bar goes
    page.mouse.click(before["x"] + before["width"] / 2, before["y"] + before["height"] / 2)
    expect(bar).to_have_count(0)
    expect(checkbox).not_to_be_checked()


@pytest.fixture
def display_put_back(unlocked_page: Page, api: Api) -> Iterator[None]:
    """The stored display settings back afterwards, also when the test fails before it puts N
    back to 0 itself."""
    before = api.get("/api/settings/display")
    yield
    api.put("/api/settings/display", before)


def set_rare_fields_max(page: Page, value: int) -> None:
    page.get_by_role("button", name="Actions for rareFields", exact=True).click()
    page.get_by_role("menuitem", name="Set N").click()
    dialog = page.get_by_role("dialog", name="Rare fields")
    expect(dialog).to_contain_text("0 turns Rare fields off.")
    dialog.get_by_label("N", exact=True).fill(str(value))
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)


def test_rare_fields_show_as_json_and_hide_their_columns(
    unlocked_page: Page, api: Api, fixture_items: dict[str, dict[str, Any]], display_put_back: None
):
    page = unlocked_page
    rows = rows_by_name(api)
    ## Server sudo alone has the fields infra (text) and sudo (hidden)
    users = [row for row in rows.values() if "fields.infra" in row["cells"]]
    assert [row["id"] for row in users] == [rows["Server sudo"]["id"]]
    item_id = rows["Server sudo"]["id"]
    switch = page.get_by_role("switch", name="Hide rare field columns")
    expect(header(page, "rareFields")).to_contain_text("Rare fields (off)")
    expect(switch).to_be_disabled()
    expect(header(page, "fields.infra")).to_be_visible()

    set_rare_fields_max(page, 2)
    expect(header(page, "rareFields")).to_contain_text("Rare fields ≤ 2")
    assert api.get("/api/settings/display")["rareFieldsMax"] == 2
    expect(switch).to_be_checked()
    expect(header(page, "fields.infra")).to_have_count(0)
    expect(header(page, "fields.sudo")).to_have_count(0)
    search(page, "Server sudo")
    rare = item_cell(page, item_id, "rareFields")
    expect(rare).to_contain_text('"infra": "host-a, host-b"')
    sudo = fixture_items["Server sudo"]["fields"][0]["value"]
    masked = sudo not in rare.inner_text()
    assert masked, "the sudo value shows before its eye is clicked"
    rare.get_by_role("button", name="Reveal fields.sudo").click()
    expect(rare.get_by_role("button", name="Hide fields.sudo")).to_be_visible()
    shown = f'"sudo": "{sudo}"' in rare.inner_text()
    assert shown, "the revealed sudo value is not in the cell"
    rare.get_by_role("button", name="Hide fields.sudo").click()
    expect(rare.get_by_role("button", name="Reveal fields.sudo")).to_be_visible()

    switch.uncheck()
    expect(header(page, "fields.infra")).to_be_visible()
    expect(header(page, "fields.sudo")).to_be_visible()

    set_rare_fields_max(page, 0)
    expect(header(page, "rareFields")).to_contain_text("Rare fields (off)")
    expect(switch).to_be_disabled()
    expect(rare).to_have_text("")
    assert api.get("/api/settings/display")["rareFieldsMax"] == 0
