"""The tutorials (docs/tutorial), one test each, in the order of the docs on one vault.

A reader takes the tutorials one after the other, so each test continues from the vault the one
before left: Your first template finds the field Password renamed to password by Your first
cleanup, and Your first MCP-assisted cleanup finds the two logins that Your first template made
in no folder. The vault is put back once, after the last test (one_vault), together with the
templates and datatypes of the bwtidy note. A test whose predecessor did not pass in this run
skips, so a run of one later tutorial alone takes no image (-k test_tutorials runs them all).
Each test carries the marker tutorial with its docs page, and tests/repo/test_tutorial_order.py
checks that their order is the one of zensical.toml and docs/index.md. The other docs tests are
in files of their own, so they run before or after these on the seeded vault.

No AI client runs here: test_mcp makes the proposals an agent would make for the tutorial's
prompt through the real MCP endpoint with the token of the dialog (mcp_caller), so the Queue
shows real proposals. The token is never printed.

The auto-commit waits the product's 180 s while a tutorial runs, so the edits wait for Commit
now as in the tutorial, and is set back after. Times, backup names and backup sizes differ from
run to run, so the Queue and the Change log show fixed ones (FixedValues), the times in their
order. The Change log of Your first template names the trashed notes by their ids, which the
seeding makes anew, so it shows made-up ones. The MCP token's expiry and the port of the MCP URL
differ too, so its dialog shows fixed ones.
"""

import json
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, Route, expect
from support.api import COMMIT_TIMEOUT, Api, app_api, rows_by_name
from support.elsewhere import active_ids, last_change_id, put_back
from support.login import EMAIL, PASSWORD, SERVER
from support.mcp import McpCall, mcp_caller
from support.pages import (
    UNLOCK_TIMEOUT,
    cell,
    grid,
    hover_tooltip,
    item_cell,
    pick,
    search,
    settle,
)
from support.queue_page import APPROVE_TIMEOUT, approve_result, mcp_switch

from tests.support.docs_shots import (
    GROUP,
    FixedValues,
    hide_revision_dates,
    shot,
    wait_for_grid,
    wait_for_next_backup,
)
from tests.support.section_changes import datatype_changes, template_changes

## the product's default (docs/spec.md, Auto-commit)
DEFAULT_AUTO_COMMIT = {"afterSeconds": 180, "maxEntries": 20}
COMMIT_MS = COMMIT_TIMEOUT * 1000


def wait_saved(api: Api, path: str) -> dict[str, Any]:
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while (doc := api.get(path))["saving"]:
        assert time.monotonic() < deadline, f"the save of {path} was not committed"
        time.sleep(0.5)
    return doc


@pytest.fixture(scope="module")
def one_vault(browser: Browser, base_url: str) -> Iterator[list[str]]:
    """The vault of the tutorials, put back once after the last, with the bwtidy note's
    templates and datatypes. Yields the names of the tests that passed, in their order.

    The session ends afterwards, so the next unlock loads the vault again: the app has not seen
    what put_back deleted.
    """
    with app_api(browser, base_url) as api:
        since = last_change_id(api)
        active_before = active_ids(api)
        templates = wait_saved(api, "/api/templates")["templates"]
        datatypes = wait_saved(api, "/api/datatypes")["datatypes"]
    passed: list[str] = []
    yield passed
    with app_api(browser, base_url) as api:
        api.wait_saved()
        changes = template_changes(wait_saved(api, "/api/templates")["templates"], templates)
        if changes:
            api.put("/api/templates", {"changes": changes})
            wait_saved(api, "/api/templates")
        changes = datatype_changes(wait_saved(api, "/api/datatypes")["datatypes"], datatypes)
        if changes:
            api.put("/api/datatypes", {"changes": changes})
            wait_saved(api, "/api/datatypes")
        put_back(api, since, active_before)
        api.delete("/api/session")


@pytest.fixture(autouse=True)
def after_the_one_before(request: pytest.FixtureRequest, one_vault: list[str]) -> Iterator[None]:
    """Skip a tutorial whose predecessors did not all pass in this run, note one that passed.

    The call's report is logged before the teardown, so testsfailed counts this test already.
    """
    tests = [name for name in vars(request.module) if name.startswith("test_")]
    name = request.function.__name__
    earlier = tests[: tests.index(name)]
    missing = [test for test in earlier if test not in one_vault]
    if missing:
        pytest.skip(
            f"continues from {missing[-1]}, which did not pass in this run: "
            "run the tutorials together (-k test_tutorials)"
        )
    failed = request.session.testsfailed
    yield
    if request.session.testsfailed == failed:
        one_vault.append(name)


## Your first cleanup (docs/tutorial/first-cleanup.md): the account form and the unlock, the
## grid, a name edited in a cell, a field renamed across items through its header menu, both
## waiting in the Queue, Commit now, and the write with its backup in the Change log.

## the item whose name the tutorial edits, and its new name
RENAMED_ITEM = "Library"
NEW_NAME = "City library"
## the item whose field the tutorial renames in every item that has it
FIELD_ITEM = "Old webshop"
FIELD = "fields.Password"
NEW_FIELD = "password"
## the newest instant the Queue and the Change log show
CLEANUP_QUEUE_NEWEST = datetime(2026, 11, 17, 9, 41, 35, tzinfo=UTC)
CLEANUP_CHANGE_LOG_NEWEST = datetime(2026, 11, 17, 9, 42, 44, tzinfo=UTC)


@pytest.mark.tutorial("tutorial/first-cleanup.md")
def test_first_cleanup(page: Page, api: Api) -> None:
    log_in(page)
    found = api.get("/api/queue/auto-commit")
    api.put("/api/queue/auto-commit", DEFAULT_AUTO_COMMIT)
    try:
        clean_up(page, api)
    finally:
        ## back to the quick auto-commit first, so an edit a failure left pending is written
        api.put("/api/queue/auto-commit", {key: found[key] for key in DEFAULT_AUTO_COMMIT})
        api.wait_saved()


def log_in(page: Page) -> None:
    """The account form and the unlock, up to the grid."""
    page.goto("/")
    form = page.locator(".MuiPaper-root").filter(has=page.get_by_label("Server"))
    expect(form).to_be_visible(timeout=UNLOCK_TIMEOUT)
    page.get_by_label("Server").fill(SERVER)
    page.get_by_label("Email").fill(EMAIL)
    proceed = page.get_by_role("button", name="Continue", exact=True)
    shot(page, "tutorial-account-form", frame="box", around=[form], highlight=[proceed])

    proceed.click()
    password = page.get_by_role("textbox", name="Master password", exact=True)
    expect(password).to_be_visible(timeout=UNLOCK_TIMEOUT)
    password.fill(PASSWORD)
    unlock = page.get_by_role("button", name="Unlock", exact=True)
    paper = page.locator(".MuiPaper-root").filter(has=unlock)
    shot(page, "tutorial-unlock-form", frame="box", around=[paper], highlight=[unlock])

    unlock.click()
    grid(page)


def clean_up(page: Page, api: Api) -> None:
    """The grid, the two edits, the Queue, Commit now and the Change log."""
    wait_for_grid(page)
    ## the pointer left on the grid after the unlock shades a row and a column
    page.mouse.move(1000, 150)
    search = page.get_by_label("Search", exact=True)
    hide_empty = page.get_by_label("Hide empty columns")
    ## the switch with its text, the input alone is the switch's thumb
    hide_empty_label = page.locator("label").filter(has=hide_empty)
    columns = page.get_by_role("button", name="Columns", exact=True)
    shot(
        page,
        "tutorial-grid",
        highlight=[(search, "1"), (hide_empty_label, "2"), (columns, "3")],
    )

    name = cell(page, RENAMED_ITEM, "name")
    settle(page)
    name.dblclick()
    editing = page.locator(".ag-cell-inline-editing input")
    editing.fill(NEW_NAME)
    header = page.locator(".ag-header-row-column .ag-header-cell[col-id='name']")
    hide_revision_dates(page)
    shot(
        page,
        "tutorial-cell-edit",
        frame="band",
        around=[search, header, name],
        highlight=[name],
    )
    page.keyboard.press("Enter")
    commit = page.get_by_role("button", name="Commit now", exact=True)
    expect(commit).to_be_visible()

    search.fill(FIELD_ITEM)
    expect(
        page.locator(".ag-cell[col-id='name']").get_by_text(FIELD_ITEM, exact=True)
    ).to_have_count(1)
    hide_empty.check()
    ## dev/testing.md, Playwright gotchas: the grid's scroll area keeps every column's width for a
    ## moment after Hide empty columns
    page.wait_for_function(
        """() => {
            const area = document.querySelector('.ag-grid-viewport')
            const bar = document.querySelector('.ag-body-horizontal-scroll-viewport')
            return area && bar && area.scrollWidth <= bar.scrollWidth
        }"""
    )
    ## the open menu hides the rest of the page from the accessibility tree
    actions = page.get_by_role(
        "button", name=f"Actions for {FIELD}", exact=True, include_hidden=True
    )
    actions.click()
    menu = page.get_by_role("menu")
    rename = menu.get_by_role("menuitem", name="Rename field…")
    expect(rename).to_be_visible()
    field_header = page.locator(f".ag-header-cell[col-id='{FIELD}']")
    hide_revision_dates(page)
    shot(
        page, "tutorial-header-menu", frame="band", around=[field_header, menu], highlight=[rename]
    )

    rename.click()
    dialog = page.get_by_role("dialog", name=f"Rename field {FIELD.removeprefix('fields.')}")
    dialog.get_by_label("New name").fill(NEW_FIELD)
    merge = dialog.get_by_role("button", name="Merge", exact=True)
    expect(dialog.get_by_role("alert")).to_be_visible()
    shot(page, "tutorial-rename-field", frame="box", around=[dialog], highlight=[merge])
    merge.click()
    expect(dialog).to_be_hidden()
    hide_empty.uncheck()
    search.fill("")

    fixed = FixedValues(CLEANUP_QUEUE_NEWEST)
    fixed.learn(api.get("/api/queue"))
    page.route(re.compile(r"/api/queue\?status="), fixed.route)
    queue = page.get_by_role("button", name=re.compile(r"^Queue, 2 unsaved edits"))
    expect(queue).to_be_visible()
    queue.click()
    ## the Queue button's tooltip would cover the page's title
    page.mouse.move(1200, 450)
    expect(page.get_by_role("tooltip")).to_have_count(0)
    entries = page.locator("[data-testid^='queue-entry-']")
    expect(entries).to_have_count(2)
    ## down to the second entry's summary and its line below, the table under it is long
    second = entries.nth(1).locator("p").filter(has_text="from ui")
    app_bar = page.locator("header").first
    shot(
        page,
        "tutorial-queue",
        frame="band",
        around=[app_bar, second],
        margin=(16, 12),
        highlight=[commit],
    )

    commit.click()
    expect(commit).to_be_hidden(timeout=UNLOCK_TIMEOUT)
    api.wait_saved()
    wait_for_next_backup(api)

    fixed = FixedValues(CLEANUP_CHANGE_LOG_NEWEST, GROUP)
    fixed.learn([api.get("/api/change-log"), api.get("/api/backups")])
    page.route(re.compile(r"/api/(change-log|backups)\b"), fixed.route)
    page.get_by_role("button", name="Change log", exact=True).click()
    log = page.get_by_role("table", name="Change log")
    written = (
        log.locator("tbody tr")
        .filter(has_text=NEW_NAME)
        .or_(log.locator("tbody tr").filter(has_text=FIELD_ITEM))
    )
    expect(written).to_have_count(2)
    used = written.first.locator("td").nth(5)
    backup = log.get_by_role("link", name=used.inner_text(), exact=True)
    shot(
        page,
        "tutorial-change-log",
        frame="band",
        around=[log.locator("thead"), backup],
        highlight=[used, backup],
    )


## Your first template (docs/tutorial/template.md): a datatype for web addresses, a login
## template that uses it on login.uris, the grid with its matches and format marks, two notes
## that are logins converted with the template, their web address added with the live format
## check, Commit now, the matches, and the writes in the Change log.

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
## made-up ids the Change log shows for the converted notes, the real ones come from the seeding
TRASHED_IDS = ["3c5e1f0a-7b2d-4e8f-9a61-0d4b7c2e5f18", "8f2a6d4b-1c9e-4a37-b5d0-6e3f9a1c7b42"]
## the newest instant the Change log shows
TEMPLATE_CHANGE_LOG_NEWEST = datetime(2026, 11, 17, 10, 12, 30, tzinfo=UTC)


@pytest.mark.tutorial("tutorial/template.md")
def test_template(unlocked_page: Page, api: Api) -> None:
    page = unlocked_page
    found = api.get("/api/queue/auto-commit")
    api.put("/api/queue/auto-commit", DEFAULT_AUTO_COMMIT)
    try:
        make_datatype(page)
        make_template(page)
        convert_and_fill(page, api)
    finally:
        api.put("/api/queue/auto-commit", {key: found[key] for key in DEFAULT_AUTO_COMMIT})
        api.wait_saved()


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
        mapping = dialog.get_by_role("table", name="Mapping")
        expect(mapping).to_be_visible()
        ## Your first cleanup renamed the field Password of these notes
        expect(mapping).to_contain_text(f"fields.{NEW_FIELD}")
        expect(mapping).not_to_contain_text(FIELD)
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

    fixed = FixedValues(TEMPLATE_CHANGE_LOG_NEWEST, GROUP)
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


## Your first MCP-assisted cleanup (docs/tutorial/mcp.md): an MCP token made in the app, MCP
## switched on, an agent's proposals in the Queue, Approve selected, and the write with its backup
## in the Change log.

## the logins in no folder that the agent puts in a folder, by folder (dev/dev-vault.md), Old
## webshop and School portal as Your first template left them
MOVES = {
    "Home": (
        [
            "Online bank",
            "Tax agency",
            "Health portal",
            "Shoe shop",
            "Old webshop",
            "School portal",
        ],
        "household logins like the bank, the tax agency, the school and shops are all in Home",
    ),
    "Work": (["Example Forge"], "a code forge account, like Code forge and Issue tracker in Work"),
}
## the newest instant the Change log shows
MCP_CHANGE_LOG_NEWEST = datetime(2026, 11, 17, 10, 31, 12, tzinfo=UTC)


@pytest.mark.tutorial("tutorial/mcp.md")
def test_mcp(unlocked_page: Page, api: Api, base_url: str) -> None:
    page = unlocked_page
    try:
        token = make_token(page)
        switch_on(page)
        entries = propose(mcp_caller(base_url, token))
        review(page, api, entries)
    finally:
        pending = [entry["id"] for entry in api.get("/api/queue?status=pending")]
        if pending:
            api.post("/api/queue/reject", {"ids": pending})
        if mcp_switch(page).is_checked():
            mcp_switch(page).click()
            expect(mcp_switch(page)).not_to_be_checked()
        api.delete("/api/mcp/token")


def make_token(page: Page) -> str:
    """The MCP token menu, the dialog that asks for its lifetime and the one that shows it."""
    page.get_by_role("button", name="MCP token", exact=True).click()
    menu = page.get_by_role("menu")
    create = menu.get_by_role("menuitem", name="Create", exact=True)
    header = page.locator("header").first
    shot(page, "tutorial-mcp-token-menu", frame="band", around=[header, menu], highlight=[create])
    create.click()
    ask = page.get_by_role("dialog", name="New MCP token")
    ask.get_by_role("button", name="Create", exact=True).click()
    dialog = page.get_by_role("dialog", name="MCP token", exact=True)
    field = dialog.get_by_label("Token", exact=True)
    expect(field).not_to_have_value("")
    token = field.input_value()
    dialog.get_by_label("MCP URL", exact=True).evaluate(
        "field => { field.value = 'https://bwtidy.example/mcp' }"
    )
    dialog.get_by_text(re.compile(r"It expires")).evaluate(
        "text => { text.textContent = text.textContent.replace("
        "/It expires .*$/, 'It expires 2/15/2027, 7:01:30 PM.') }"
    )
    copy_token = dialog.get_by_role("button", name="Copy token", exact=True)
    copy_url = dialog.get_by_role("button", name="Copy mcp url", exact=True)
    shot(
        page,
        "tutorial-mcp-token",
        frame="box",
        around=[dialog],
        highlight=[(copy_token, "1"), (copy_url, "2")],
    )
    dialog.get_by_role("button", name="Close").click()
    expect(dialog).to_have_count(0)
    return token


def switch_on(page: Page) -> None:
    switch = mcp_switch(page)
    switch.click()
    expect(switch).to_be_checked()
    page.mouse.move(800, 500)
    header = page.locator("header").first
    shot(page, "tutorial-mcp-switch", frame="band", around=[header], highlight=[switch])


def propose(mcp_call: McpCall) -> list[int]:
    """What an agent does for the tutorial's prompt: list the logins in no folder, one proposal
    per folder."""
    items = json.loads(mcp_call("list_items", type="login", folder=""))["items"]
    ids = {item["name"]: item["id"] for item in items}
    moved = [name for names, _ in MOVES.values() for name in names]
    assert not set(moved) - set(ids), f"not logins in no folder: {sorted(set(moved) - set(ids))}"
    entries = []
    for folder, (names, reason) in MOVES.items():
        answer = json.loads(
            mcp_call(
                "propose_move_to_folder",
                item_ids=[ids[name] for name in names],
                folder=folder,
                reason=reason,
            )
        )
        entries.append(answer["entryId"])
    return entries


def review(page: Page, api: Api, entries: list[int]) -> None:
    page.get_by_role("button", name=re.compile(r"^Queue\b")).click()
    cards = [page.get_by_test_id(f"queue-entry-{entry}") for entry in entries]
    for card in cards:
        expect(card).to_be_visible()
    select_all = page.get_by_role("checkbox", name="Select all")
    select_all.check()
    approve = page.get_by_role("button", name="Approve selected")
    expect(approve).to_be_enabled()
    for card in cards:
        ## the line with the entry's id and time, which change with every run
        card.get_by_text(re.compile(r"^#\d+ · from mcp")).evaluate(
            "line => line.setAttribute('data-shot-hide', '')"
        )
    tabs = page.get_by_role("tablist", name="Queue entries")
    ## the last proposal at the bottom of the window, the tabs above it stay in view
    cards[-1].evaluate("card => card.scrollIntoView({ block: 'end' })")
    shot(
        page,
        "tutorial-mcp-queue",
        frame="band",
        around=[tabs, cards[-1]],
        highlight=[(select_all, "1"), (approve, "2")],
    )
    approve.click()
    expect(approve_result(page)).to_contain_text("Applied 2 entries", timeout=APPROVE_TIMEOUT)
    api.wait_saved()
    wait_for_next_backup(api)

    fixed = FixedValues(MCP_CHANGE_LOG_NEWEST, GROUP)
    fixed.learn([api.get("/api/change-log"), api.get("/api/backups")])
    page.route(re.compile(r"/api/(change-log|backups)\b"), fixed.route)
    page.get_by_role("button", name="Change log", exact=True).click()
    log = page.get_by_role("table", name="Change log")
    written = log.locator("tbody tr").filter(has_text="Move to folder")
    expect(written).to_have_count(len(MOVES))
    used = written.first.locator("td").nth(5)
    backup = log.get_by_role("link", name=used.inner_text(), exact=True)
    shot(
        page,
        "tutorial-mcp-change-log",
        frame="band",
        around=[log.locator("thead"), backup],
        highlight=[used, backup],
    )
