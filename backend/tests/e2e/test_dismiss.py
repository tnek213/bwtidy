"""A failed edit can be dismissed: it leaves the Failed tab and the grid. A
conflict can be retried instead: it writes over the change elsewhere after a
confirm, with a backup first.

The edit fails because a second CLI state in the app container changes the item before the
commit (dev/dev-vault.md, Trying the auto-commit). That state logs in as another device, so the
app's own bw serve keeps its session. It puts the username back afterwards (username_put_back).
"""

import base64
import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import Api, last_entry_id
from support.login import EMAIL, PASSWORD, SERVER
from support.pages import grid

ITEM = "Bike club"
COLUMN = "login.username"
SUMMARY = f'Set {COLUMN} of "{ITEM}"'
FROM_THE_GRID = "bike-club-from-the-grid"
FROM_ELSEWHERE = "bike-club-from-elsewhere"

DEV_VAULT = Path(__file__).resolve().parents[3] / "scripts" / "dev-vault"
## the second CLI state, in the container's /tmp
CHECK_STATE = "BITWARDENCLI_APPDATA_DIR=/tmp/bwcheck-e2e"
BW_TIMEOUT = 120

CONFLICT_ROW = re.compile(r"\bbw-conflict-row\b")
CONFLICT_CELL = re.compile(r"\bbw-conflict-cell\b")


def bw_elsewhere(*args: str, session: str | None = None) -> str:
    """Run bw with the second CLI state in the app container and answer its output."""
    env = ["env", CHECK_STATE] + ([f"BW_SESSION={session}"] if session else [])
    result = subprocess.run(
        [str(DEV_VAULT), "exec", *env, "bw", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=BW_TIMEOUT,
    )
    ## the command name and the exit code only, the output can hold an item's values
    assert result.returncode == 0, f"bw {args[0]} exited with {result.returncode}"
    return result.stdout


def change_username_elsewhere(item_id: str, username: str) -> None:
    status = json.loads(bw_elsewhere("status"))
    if status.get("status") == "unauthenticated":
        bw_elsewhere("config", "server", SERVER)
        bw_elsewhere("login", EMAIL, PASSWORD)
    session = bw_elsewhere("unlock", PASSWORD, "--raw").strip()
    bw_elsewhere("sync", session=session)
    item = json.loads(bw_elsewhere("get", "item", item_id, session=session))
    item["login"]["username"] = username
    encoded = base64.b64encode(json.dumps(item).encode()).decode()
    bw_elsewhere("edit", "item", item_id, encoded, session=session)


def cell(page: Page, item: str, column: str) -> Locator:
    """The cell of an item's column, found through the row of its name cell."""
    page.get_by_label("Search").fill(item)
    name = page.locator(".ag-cell[col-id='name']").get_by_text(item, exact=True)
    expect(name).to_have_count(1)
    row = name.locator("xpath=ancestor::*[@row-id][1]").get_attribute("row-id")
    return page.locator(f".ag-row[row-id='{row}'] .ag-cell[col-id='{column}']")


def row_of(target: Locator) -> Locator:
    return target.locator("xpath=ancestor::*[contains(@class, 'ag-row')][1]")


def item_id(api: Api, name: str) -> str:
    rows = api.get("/api/grid")["rows"]
    [found] = [row["id"] for row in rows if row["cells"].get("name") == name]
    return found


@pytest.fixture
def username_put_back(unlocked_page: Page, api: Api) -> Iterator[None]:
    """The item's username as it was, put back as another device, then the session ends: the app
    would keep the username of the change elsewhere in RAM until a write, the next unlock loads
    the vault again. A failed entry a test left is dismissed, its snackbar would show in every
    later test."""
    [before] = [row for row in api.get("/api/grid")["rows"] if row["cells"].get("name") == ITEM]
    yield
    left = [e["id"] for e in api.get("/api/queue?status=failed") if e["summary"] == SUMMARY]
    if left:
        api.post("/api/queue/dismiss", {"ids": left})
    change_username_elsewhere(before["id"], before["cells"][COLUMN])
    api.delete("/api/session")


def username_elsewhere(item: str) -> str:
    """The item's username as another device reads it from the server."""
    session = bw_elsewhere("unlock", PASSWORD, "--raw").strip()
    bw_elsewhere("sync", session=session)
    return json.loads(bw_elsewhere("get", "item", item, session=session))["login"]["username"]


def fail_an_edit(page: Page, api: Api) -> Locator:
    """Edit the username in the grid, change it elsewhere before the commit, and answer the
    failed entry's card on the Failed tab of the Queue page."""
    settings = api.get("/api/queue/auto-commit")
    since = last_entry_id(api)
    ## the edit waits until the item has changed elsewhere and Commit now is pressed
    api.put("/api/queue/auto-commit", {"afterSeconds": 3600, "maxEntries": 1000})
    try:
        username = cell(page, ITEM, COLUMN)
        username.dblclick()
        editor = page.locator(".ag-cell-inline-editing input")
        editor.fill(FROM_THE_GRID)
        editor.press("Enter")
        expect(
            page.get_by_role("button", name=re.compile(r"^Queue, 1 unsaved edits"))
        ).to_be_visible()

        change_username_elsewhere(item_id(api, ITEM), FROM_ELSEWHERE)
        page.get_by_role("button", name="Commit now").click()

        snackbar = page.get_by_role("alert").filter(has_text="Could not save a change.")
        expect(snackbar).to_be_visible(timeout=120_000)
        expect(snackbar).to_contain_text(f"{ITEM}: 1 item changed since the change was queued")
        username = cell(page, ITEM, COLUMN)
        expect(username).to_have_class(CONFLICT_CELL)
        expect(row_of(username)).to_have_class(CONFLICT_ROW)
    finally:
        api.put(
            "/api/queue/auto-commit",
            {"afterSeconds": settings["afterSeconds"], "maxEntries": settings["maxEntries"]},
        )

    failed_entries = api.get("/api/queue?status=failed")
    [failed] = [e for e in failed_entries if e["summary"] == SUMMARY and e["id"] > since]
    page.get_by_role("button", name=re.compile(r"^Queue")).click()
    page.get_by_role("tab", name="Failed").click()
    card = page.get_by_test_id(f"queue-entry-{failed['id']}")
    expect(card).to_contain_text(SUMMARY)
    expect(card).to_contain_text("Failed: 1 item changed since the change was queued")
    return card


def test_dismiss_removes_a_failed_edit_from_the_failed_tab_and_the_grid(
    unlocked_page: Page, api: Api, username_put_back: None
):
    page = unlocked_page
    card = fail_an_edit(page, api)
    failed_id = int((card.get_attribute("data-testid") or "").removeprefix("queue-entry-"))

    card.get_by_role("button", name="Dismiss").click()

    expect(card).to_have_count(0)
    assert failed_id not in [e["id"] for e in api.get("/api/queue?status=failed")]
    page.get_by_role("button", name="Vault", exact=True).click()
    grid(page)
    username = cell(page, ITEM, COLUMN)
    expect(username).to_have_text(FROM_ELSEWHERE)
    expect(username).not_to_have_class(CONFLICT_CELL)
    expect(row_of(username)).not_to_have_class(CONFLICT_ROW)
    expect(page.get_by_text("Could not save a change.")).to_have_count(0)


def test_retry_writes_a_conflicting_edit_over_the_change_elsewhere_after_a_backup(
    unlocked_page: Page, api: Api, username_put_back: None
):
    page = unlocked_page
    card = fail_an_edit(page, api)
    failed_id = int((card.get_attribute("data-testid") or "").removeprefix("queue-entry-"))
    since = max((row["id"] for row in api.get("/api/change-log")), default=0)

    card.get_by_role("button", name="Retry").click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text(f"{ITEM}'s {COLUMN} was changed elsewhere since you edited it.")
    expect(dialog).to_contain_text(
        "Retry writes your value over that change. A backup is taken first, so the other value can"
        " be restored from the Change log."
    )
    dialog.get_by_role("button", name="Retry").click()

    result = page.get_by_test_id("approve-result")
    expect(result).to_contain_text("Applied 1 entry", timeout=120_000)
    expect(card).to_have_count(0)
    [applied] = [e for e in api.get("/api/queue?status=applied") if e["id"] == failed_id]
    backup = applied["result"]["backup"]
    assert backup
    the_grids = username_elsewhere(item_id(api, ITEM)) == FROM_THE_GRID
    assert the_grids
    [row] = [
        row
        for row in api.get("/api/change-log")
        if row["id"] > since and row["operationKind"] == "setLeaf"
    ]
    assert (row["itemIds"], row["result"], row["backup"]) == ([item_id(api, ITEM)], "ok", backup)
    page.get_by_role("button", name="Change log").click()
    log = page.get_by_role("table", name="Change log")
    written = log.get_by_role("row").filter(has_text="setLeaf").filter(has_text=ITEM)
    expect(written.filter(has_text=backup)).to_have_count(1)
    page.get_by_role("button", name="Vault", exact=True).click()
    grid(page)
    username = cell(page, ITEM, COLUMN)
    expect(username).to_have_text(FROM_THE_GRID)
    expect(username).not_to_have_class(CONFLICT_CELL)
