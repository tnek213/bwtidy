"""The tutorial Your first cleanup (docs/tutorial/first-cleanup.md): the account form and the
unlock, the grid, a name edited in a cell, a field renamed across items through its header menu,
both waiting in the Queue, Commit now, and the write with its backup in the Change log.

The auto-commit waits the product's 180 s while the test runs, so the edits are still in the
queue for its shot, and is set back after. The test puts the vault back as it found it (put_back),
so the docs tests after it see the seeded vault. Times, backup names and backup sizes differ
from run to run, so the Queue and the Change log show fixed ones (FixedValues), the times in
their order.
"""

import re
from datetime import UTC, datetime

from playwright.sync_api import Page, expect
from support.api import Api
from support.elsewhere import active_ids, last_change_id, put_back
from support.login import EMAIL, PASSWORD, SERVER
from support.pages import UNLOCK_TIMEOUT, cell, grid, settle

from tests.support.docs_shots import (
    GROUP,
    FixedValues,
    hide_revision_dates,
    shot,
    wait_for_grid,
    wait_for_next_backup,
)

## the item whose name the tutorial edits, and its new name
RENAMED_ITEM = "Library"
NEW_NAME = "City library"
## the item whose field the tutorial renames in every item that has it
FIELD_ITEM = "Old webshop"
FIELD = "fields.Password"
NEW_FIELD = "password"
## the product's default (docs/spec.md, Auto-commit)
DEFAULT_AUTO_COMMIT = {"afterSeconds": 180, "maxEntries": 20}

## the newest instant the Queue and the Change log show
QUEUE_NEWEST = datetime(2026, 11, 17, 9, 41, 35, tzinfo=UTC)
CHANGE_LOG_NEWEST = datetime(2026, 11, 17, 9, 42, 44, tzinfo=UTC)


def test_tutorial(page: Page, api: Api) -> None:
    log_in(page)
    since = last_change_id(api)
    active_before = active_ids(api)
    found = api.get("/api/queue/auto-commit")
    api.put("/api/queue/auto-commit", DEFAULT_AUTO_COMMIT)
    try:
        clean_up(page, api)
    finally:
        ## back to the quick auto-commit first, so an edit a failure left pending is written
        api.put("/api/queue/auto-commit", {key: found[key] for key in DEFAULT_AUTO_COMMIT})
        if put_back(api, since, active_before):
            api.delete("/api/session")


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

    fixed = FixedValues(QUEUE_NEWEST)
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

    fixed = FixedValues(CHANGE_LOG_NEWEST, GROUP)
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
