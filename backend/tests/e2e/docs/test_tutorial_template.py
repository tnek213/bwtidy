"""The tutorial Your first template (docs/tutorial/template.md): a datatype for web addresses, a
login template that uses it on login.uris, the grid with its matches and format marks, two notes
that are logins converted with the template, their web address added with the live format check,
Commit now, the matches, and the writes in the Change log.

The test puts the vault back as it found it (put_back) and removes the template and the datatype
again, so the docs tests after it see the seeded vault. The auto-commit waits the product's 180 s
while the test runs, so the edits wait for Commit now as in the tutorial, and is set back after.
Times, backup names and backup sizes differ from run to run, so the Change log shows fixed ones
(FixedValues), the times in their order. It names the trashed notes by their ids, which the
seeding makes anew, so it shows made-up ones.
"""

import json
import re
import time
from datetime import UTC, datetime
from typing import Any

from playwright.sync_api import Page, Route, expect
from support.api import COMMIT_TIMEOUT, Api, rows_by_name
from support.elsewhere import active_ids, last_change_id, put_back
from support.pages import UNLOCK_TIMEOUT, hover_tooltip, item_cell, pick, search, settle

from tests.support.docs_shots import (
    GROUP,
    FixedValues,
    hide_revision_dates,
    shot,
    wait_for_grid,
    wait_for_next_backup,
)
from tests.support.section_changes import datatype_changes, template_changes

DATATYPE = "Web address"
PATTERN = "https://.+"
DATATYPE_DESCRIPTION = "An address that starts with https://"
EXAMPLES = ["https://shop.example", "https://192.0.2.10:8443/admin"]
COUNTER_EXAMPLES = ["shop.example", "http://shop.example"]
TEMPLATE = "Web login"
DESCRIPTION = "A login to a website or a device's web page."
COORDINATES = [
    "login.username, Built-in",
    "login.password, Built-in, hidden",
    "login.uris, Built-in",
]
## notes of the fixture that hold a username and a password field, with the address each gets
NOTES = {
    "Old webshop": "https://shop.example.test",
    "School portal": "https://school.example.test",
}
## the note the pictures show
FIRST = "Old webshop"
## the last row by name with a value off its format, and of the search "shop"
LAST_OFF = "Train tickets"
LAST_SHOP = "Shoe shop"
## the product's default (docs/spec.md, Auto-commit)
DEFAULT_AUTO_COMMIT = {"afterSeconds": 180, "maxEntries": 20}
## made-up ids the Change log shows for the converted notes, the real ones come from the seeding
TRASHED_IDS = ["3c5e1f0a-7b2d-4e8f-9a61-0d4b7c2e5f18", "8f2a6d4b-1c9e-4a37-b5d0-6e3f9a1c7b42"]
## the newest instant the Change log shows
CHANGE_LOG_NEWEST = datetime(2026, 11, 17, 10, 12, 30, tzinfo=UTC)
COMMIT_MS = COMMIT_TIMEOUT * 1000


def wait_saved(api: Api, path: str) -> dict[str, Any]:
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while (doc := api.get(path))["saving"]:
        assert time.monotonic() < deadline, f"the save of {path} was not committed"
        time.sleep(0.5)
    return doc


def test_tutorial_template(unlocked_page: Page, api: Api) -> None:
    page = unlocked_page
    since = last_change_id(api)
    active_before = active_ids(api)
    templates = wait_saved(api, "/api/templates")["templates"]
    datatypes = wait_saved(api, "/api/datatypes")["datatypes"]
    found = api.get("/api/queue/auto-commit")
    api.put("/api/queue/auto-commit", DEFAULT_AUTO_COMMIT)
    try:
        make_datatype(page)
        make_template(page)
        convert_and_fill(page, api)
    finally:
        api.put("/api/queue/auto-commit", {key: found[key] for key in DEFAULT_AUTO_COMMIT})
        api.wait_saved()
        changes = template_changes(wait_saved(api, "/api/templates")["templates"], templates)
        if changes:
            api.put("/api/templates", {"changes": changes})
            wait_saved(api, "/api/templates")
        changes = datatype_changes(wait_saved(api, "/api/datatypes")["datatypes"], datatypes)
        if changes:
            api.put("/api/datatypes", {"changes": changes})
            wait_saved(api, "/api/datatypes")
        if put_back(api, since, active_before):
            api.delete("/api/session")


def make_datatype(page: Page) -> None:
    """The Datatypes tab and the datatype editor, filled in for web addresses."""
    page.get_by_role("button", name="Types", exact=True).click()
    page.get_by_role("tab", name="Datatypes").click()
    page.get_by_role("button", name="New datatype").click()
    editor = page.get_by_role("dialog", name="New datatype")
    editor.get_by_label("Name", exact=True).fill(DATATYPE)
    editor.get_by_label("Regular expression", exact=True).fill(PATTERN)
    editor.get_by_label("Description", exact=True).fill(DATATYPE_DESCRIPTION)
    editor.get_by_label("Examples", exact=True).fill("\n".join(EXAMPLES))
    editor.get_by_label("Counter-examples", exact=True).fill("\n".join(COUNTER_EXAMPLES))
    save = editor.get_by_role("button", name="Save")
    expect(save).to_be_enabled()
    expect(editor.get_by_label("Pattern facts")).to_be_visible()
    editor.get_by_role("heading", name="New datatype").click()
    shot(page, "tutorial-datatype-editor", frame="box", around=[editor], highlight=[save])
    save.click()
    expect(editor).to_be_hidden()


def make_template(page: Page) -> None:
    """The Templates tab and the template editor, the datatype on login.uris."""
    page.get_by_role("tab", name="Templates").click()
    page.get_by_role("button", name="New template").click()
    editor = page.get_by_role("dialog", name="New template")
    editor.get_by_label("Name", exact=True).fill(TEMPLATE)
    pick(editor, "Type", "Login")
    editor.get_by_label("Description", exact=True).fill(DESCRIPTION)
    for option in COORDINATES:
        editor.get_by_role("button", name="Add coordinate").click()
        page.get_by_role("option", name=option, exact=True).click()
    number = editor.get_by_role("group").count()
    row = editor.get_by_role("group", name=f"Coordinate row {number}", exact=True)
    pick(row, "Datatype", DATATYPE)
    save = editor.get_by_role("button", name="Save")
    save.scroll_into_view_if_needed()
    heading = editor.get_by_text("Coordinates", exact=True)
    shot(
        page,
        "tutorial-template-editor",
        frame="box",
        around=[heading, save],
        highlight=[(row.get_by_role("combobox", name="Datatype"), "1"), (save, "2")],
    )
    save.click()
    expect(editor).to_be_hidden()
    listed = page.get_by_role("table", name="Templates")
    expect(listed.get_by_text(f"login.uris: {DATATYPE}")).to_be_visible()


def hide_empty_columns(page: Page, on: bool) -> None:
    """Switch Hide empty columns on or off and wait for the grid to lay out its columns."""
    hide_empty = page.get_by_label("Hide empty columns")
    hide_empty.set_checked(on)
    ## dev/testing.md, Playwright gotchas: the grid's scroll area keeps every column's width for a
    ## moment after Hide empty columns
    page.wait_for_function(
        """() => {
            const area = document.querySelector('.ag-grid-viewport')
            const bar = document.querySelector('.ag-body-horizontal-scroll-viewport')
            return area && bar && area.scrollWidth <= bar.scrollWidth
        }"""
    )
    settle(page)


def convert_and_fill(page: Page, api: Api) -> None:
    """The grid's near misses, the two notes converted, their addresses, the matches, the log."""
    page.get_by_role("button", name="Vault", exact=True).click()
    wait_for_grid(page)
    rows = rows_by_name(api)
    ## the ids of the notes, which the Change log shows once they are in the trash
    note_ids = [rows[name]["id"] for name in NOTES]
    relay = item_cell(page, rows["Mail relay"]["id"], "template")
    ## the matches come with the next load of the vault after the template's save
    expect(page.locator(".ag-cell[col-id='template']").get_by_text(TEMPLATE).first).to_be_visible(
        timeout=COMMIT_MS
    )
    format_filter = page.get_by_role("combobox", name="Format")
    pick(page, "Format", "Off their format")
    hide_empty_columns(page, True)
    hide_revision_dates(page)
    page.mouse.move(1000, 150)
    relay_uris = item_cell(page, rows["Mail relay"]["id"], "login.uris")
    shot(
        page,
        "tutorial-template-off-format",
        frame="band",
        around=[format_filter, item_cell(page, rows[LAST_OFF]["id"], "name")],
        highlight=[(format_filter, "1"), (relay_uris, "2")],
    )
    tooltip = hover_tooltip(page, relay)
    expect(tooltip).to_contain_text(f"Off format for {TEMPLATE}")
    shot(page, "tutorial-template-tooltip", frame="band", around=[relay, tooltip])
    pick(page, "Format", "All")
    hide_empty_columns(page, False)

    commit = page.get_by_role("button", name="Commit now", exact=True)
    for name in NOTES:
        search(page, name)
        item_cell(page, rows[name]["id"], "name").click(button="right")
        page.get_by_role("menuitem", name="Convert to template…").click()
        dialog = page.get_by_role("dialog", name=f"Convert {name} to a template")
        pick(dialog, "Template", re.compile(rf"^{re.escape(TEMPLATE)}, "))
        expect(dialog.get_by_role("table", name="Mapping")).to_be_visible()
        convert = dialog.get_by_role("button", name="Convert", exact=True)
        if name == FIRST:
            shot(page, "tutorial-convert-dialog", frame="box", around=[dialog], highlight=[convert])
        convert.click()
        expect(dialog).to_be_hidden()
    search(page, "")
    commit.click()
    expect(commit).to_be_hidden(timeout=UNLOCK_TIMEOUT)
    api.wait_saved()

    rows = rows_by_name(api)
    pick(page, "Format", "Off their format")
    for name in NOTES:
        expect(item_cell(page, rows[name]["id"], "template")).to_have_text(
            re.compile(rf"^{TEMPLATE} "), timeout=COMMIT_MS
        )
    hide_empty_columns(page, True)
    hide_revision_dates(page)
    page.mouse.move(1000, 150)
    shot(
        page,
        "tutorial-template-converted",
        frame="band",
        around=[format_filter, item_cell(page, rows[LAST_OFF]["id"], "name")],
        highlight=[item_cell(page, rows[FIRST]["id"], "login.uris")],
    )
    for name, address in NOTES.items():
        item_cell(page, rows[name]["id"], "login.uris").dblclick()
        dialog = page.get_by_role("dialog", name=f"login.uris of {name}")
        field = dialog.get_by_role("textbox", name="URI 1")
        if name == FIRST:
            field.fill(address.removeprefix("https://"))
            expect(dialog.get_by_role("status")).to_contain_text("matches format ")
            shot(page, "tutorial-uri-check", frame="box", around=[dialog])
        field.fill(address)
        expect(dialog.get_by_role("status")).to_have_text("matches format")
        dialog.get_by_role("button", name="Save").click()
        expect(dialog).to_have_count(0)

    commit.click()
    expect(commit).to_be_hidden(timeout=UNLOCK_TIMEOUT)
    api.wait_saved()
    pick(page, "Format", "All")
    ## the bwtidy note holds the word too, in the datatype's examples
    pick(page, "Template", TEMPLATE)
    search(page, "shop")
    expect(item_cell(page, rows[FIRST]["id"], "template")).to_have_text(TEMPLATE, timeout=COMMIT_MS)
    hide_empty_columns(page, True)
    hide_revision_dates(page)
    page.mouse.move(1000, 150)
    first = item_cell(page, rows[FIRST]["id"], "template")
    last = item_cell(page, rows[LAST_SHOP]["id"], "name")
    shot(
        page,
        "tutorial-template-match",
        frame="band",
        around=[page.get_by_label("Search", exact=True), last],
        highlight=[first, item_cell(page, rows[FIRST]["id"], "login.uris")],
    )
    wait_for_next_backup(api)

    fixed = FixedValues(CHANGE_LOG_NEWEST, GROUP)
    trashed = dict(zip(note_ids, TRASHED_IDS, strict=True))
    fixed.learn([api.get("/api/change-log"), api.get("/api/backups")])
    page.route(re.compile(r"/api/backups\b"), fixed.route)

    def change_log(route: Route) -> None:
        """The Change log's rows with fixed times and fixed ids for the trashed notes."""
        response = route.fetch()
        text = response.text()
        for real, made_up in trashed.items():
            text = text.replace(real, made_up)
        data = json.loads(text)
        fixed.learn(data)
        route.fulfill(response=response, body=json.dumps(fixed.rewrite(data)))

    page.route(re.compile(r"/api/change-log\b"), change_log)
    page.get_by_role("button", name="Change log", exact=True).click()
    log = page.get_by_role("table", name="Change log")
    rows_of = log.locator("tbody tr")
    addresses = rows_of.filter(has_text="login.uris")
    converted = rows_of.filter(has_text="applyTemplate")
    expect(addresses).to_have_count(len(NOTES))
    expect(converted).to_have_count(len(NOTES))
    ## the Backup cell of each write, the backup taken right before it
    used = [addresses.first.locator("td").nth(5), converted.first.locator("td").nth(5)]
    backup = log.get_by_role("link", name=used[1].inner_text(), exact=True)
    shot(
        page,
        "tutorial-template-change-log",
        frame="band",
        around=[log.locator("thead"), backup],
        highlight=[(used[1], "1"), (used[0], "2")],
    )
