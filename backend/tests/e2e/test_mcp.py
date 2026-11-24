"""MCP: the switch, proposals and the queue, a guessed secret.

Every test proposes on items of its own and rejects what it left pending, so Approve all in
another test never applies it. An MCP answer can echo a value the agent sent (a guessed
password in a rename summary), so no assert message shows an answer. What the approved
proposals wrote is put back at the end of the file (file_put_back).
"""

import json
import re
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from playwright.sync_api import FloatRect, Locator, Page, expect
from support.api import Api, commit_new_entries, last_entry_id, rows_by_name, secret_is
from support.login import unlock
from support.mcp import McpCall, create_mcp_token, mcp_caller
from support.pages import UNLOCK_TIMEOUT, cell, grid, item_cell
from support.queue_page import (
    APPROVE_TIMEOUT,
    approve_result,
    enable_mcp,
    item_id,
    mcp_switch,
    open_queue,
    select,
)
from support.vault_items import GENERATED_LENGTH, save_templates, trash, wait_templates_saved

pytestmark = pytest.mark.usefixtures("file_put_back")

DISABLED = "bwtidy MCP is disabled. A logged in user must enable it in the web UI."
UNAUTHORIZED = "bwtidy MCP needs a token. Create one in the web UI."
TOOL_NOTE = "bwtidy"
QUEUED = re.compile(r"\bbw-queued-cell\b")
TO_FILL = re.compile(r"\bbw-to-fill-cell\b")
## the Queue button and the grid poll the queue every 10 s
POLL_TIMEOUT = 15_000
## more than the test stacks' auto-commit of 10 s (TEST_AUTO_COMMIT) plus its check once a second
AUTO_COMMIT_WAIT = 15_000
## AG Grid fades a row out after a search, and a row shown again can be in the DOM twice
ROW = ".ag-row:not(.ag-opacity-zero)"
EDITOR = ".ag-cell-inline-editing, .ag-popup-editor"

Propose = Callable[..., dict[str, Any]]


@pytest.fixture
def mcp_page(unlocked_page: Page, mcp_token: str) -> Page:
    """The unlocked page with MCP on, which needs the token of mcp_token."""
    enable_mcp(unlocked_page)
    return unlocked_page


@pytest.fixture
def propose(mcp_page: Page, mcp_call: McpCall, api: Api) -> Iterator[Propose]:
    """Call a propose tool and answer its parsed answer, rejecting what is left pending after."""
    ids: list[int] = []

    def run(tool: str, **arguments: Any) -> dict[str, Any]:
        answer = json.loads(mcp_call(tool, **arguments))
        ids.append(answer["entryId"])
        return answer

    yield run
    pending = {entry["id"] for entry in api.get("/api/queue?status=pending")}
    left = [entry_id for entry_id in ids if entry_id in pending]
    if left:
        api.post("/api/queue/reject", {"ids": left})


def statuses(api: Api, *entry_ids: int) -> list[str]:
    found = {}
    for status in ("pending", "approved", "applied", "rejected", "failed"):
        for entry in api.get(f"/api/queue?status={status}"):
            found[entry["id"]] = status
    return [found.get(entry_id, "missing") for entry_id in entry_ids]


def entry_card(page: Page, entry_id: int) -> Locator:
    return page.get_by_test_id(f"queue-entry-{entry_id}")


def rename(propose: Propose, mcp_call: McpCall, name: str) -> dict[str, Any]:
    return propose("propose_rename_item", item_id=item_id(mcp_call, name), name=f"{name} (e2e)")


## 18: connect and toggle


def mcp_status(page: Page, token: str | None) -> tuple[int, str]:
    """POST a tools/list to /mcp with this bearer token (or none), answering status and text."""
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    response = page.request.post("/mcp", headers=headers, data=request)
    return response.status, response.text()


def refused(page: Page, token: str | None) -> bool:
    return mcp_status(page, token) == (401, UNAUTHORIZED)


def test_mcp_needs_the_token_made_in_the_web_ui(unlocked_page: Page, base_url: str, api: Api):
    page = unlocked_page
    ## another test's mcp_call made a token
    if api.get("/api/session")["mcpToken"]:
        page.get_by_role("button", name="MCP token", exact=True).click()
        page.get_by_role("menuitem", name="Remove", exact=True).click()
        page.get_by_role("button", name="MCP token", exact=True).click()
        expect(page.get_by_role("menuitem", name="Create", exact=True)).to_be_visible()
        page.keyboard.press("Escape")
    assert api.get("/api/session")["mcpToken"] is False
    assert refused(page, None)
    assert refused(page, "made-up-token")

    page.get_by_role("button", name="MCP token", exact=True).click()
    page.get_by_role("menuitem", name="Create", exact=True).click()
    ask = page.get_by_role("dialog", name="New MCP token")
    expect(ask.get_by_role("radio", name="90 days", exact=True)).to_be_checked()
    ask.get_by_role("radio", name="No expiry", exact=True).check()
    expect(ask).to_contain_text("A token without an expiry works until you delete it.")
    ask.get_by_role("radio", name="7 days", exact=True).check()
    expect(ask).not_to_contain_text("A token without an expiry")
    ask.get_by_role("button", name="Create", exact=True).click()
    dialog = page.get_by_role("dialog", name="MCP token")
    expect(dialog).to_contain_text("shown only now")
    expect(dialog).to_contain_text("It expires")
    first = dialog.get_by_label("Token", exact=True).input_value()
    expect(dialog.get_by_label("MCP URL", exact=True)).to_have_value(f"{base_url}/mcp")
    link = dialog.get_by_role("link", name="How to connect your MCP client", exact=True)
    expect(link).to_have_attribute("href", "/docs/how-to/mcp/")
    expect(link).to_have_attribute("target", "_blank")
    expect(dialog.get_by_role("button", name="Claude Code", exact=False)).to_have_count(0)
    dialog.get_by_role("button", name="Close").click()
    expect(dialog).to_have_count(0)
    shown = first in page.content()
    assert not shown, "the token stays on the page after the dialog closed"
    assert api.get("/api/session")["mcpToken"] is True

    ## made just now for 7 days, so no chip warns yet, and not used yet
    info = api.get("/api/mcp/token")
    span = datetime.fromisoformat(info["expiresAt"]) - datetime.fromisoformat(info["createdAt"])
    assert (span, info["lastUsedAt"]) == (timedelta(days=7), None)
    expect(page.get_by_text(re.compile(r"^MCP token expire"))).to_have_count(0)

    assert refused(page, None)
    assert refused(page, f"{first}x")
    assert mcp_status(page, first)[0] == 200
    assert api.get("/api/mcp/token")["lastUsedAt"] is not None
    page.get_by_role("button", name="MCP token", exact=True).click()
    times = page.get_by_label("MCP token times")
    expect(times).to_contain_text("Created: ")
    expect(times).not_to_contain_text("Expires: Never")
    expect(times).not_to_contain_text("Last used: Never")
    page.keyboard.press("Escape")
    call = mcp_caller(base_url, first)
    assert call("list_items") == DISABLED
    enable_mcp(page)
    folders = json.loads(call("list_folders"))["folders"]
    assert "Work" in {folder["name"] for folder in folders}

    second = create_mcp_token(page, "No expiry")
    assert refused(page, first)
    assert mcp_status(page, second)[0] == 200
    assert api.get("/api/mcp/token")["expiresAt"] is None
    expect(page.get_by_text(re.compile(r"^MCP token expire"))).to_have_count(0)
    page.get_by_role("button", name="MCP token", exact=True).click()
    expect(page.get_by_label("MCP token times")).to_contain_text("Expires: Never")
    page.keyboard.press("Escape")

    page.get_by_role("button", name="MCP token", exact=True).click()
    page.get_by_role("menuitem", name="Remove", exact=True).click()
    page.get_by_role("button", name="MCP token", exact=True).click()
    expect(page.get_by_role("menuitem", name="Create", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    assert refused(page, second)
    assert api.get("/api/session")["mcpToken"] is False


def test_the_switch_needs_a_token_and_the_app_bar_shows_it_on(unlocked_page: Page, api: Api):
    page = unlocked_page
    if api.get("/api/session")["mcpToken"]:
        api.delete("/api/mcp/token")
        page.reload()
        grid(page)
    bar = page.get_by_role("banner")
    before = bar.evaluate("e => getComputedStyle(e).backgroundColor")
    mcp_switch(page).click()
    expect(
        page.get_by_text("Create an MCP token first: MCP token menu, Create.", exact=True)
    ).to_be_visible()
    expect(mcp_switch(page)).not_to_be_checked()
    assert api.get("/api/session")["mcpEnabled"] is False

    create_mcp_token(page)
    enable_mcp(page)
    after = bar.evaluate("e => getComputedStyle(e).backgroundColor")
    assert after != before, "the app bar does not change while MCP is on"
    mcp_switch(page).click()
    expect(mcp_switch(page)).not_to_be_checked()
    expect(bar).to_have_css("background-color", before)


def test_every_tool_answers_disabled_while_the_switch_is_off(
    unlocked_page: Page, mcp_call: McpCall
):
    expect(mcp_switch(unlocked_page)).not_to_be_checked()
    assert mcp_call("list_items") == DISABLED
    assert mcp_call("list_folders") == DISABLED
    assert mcp_call("get_item", item_id="no-such-item") == DISABLED
    ## not even an argument error
    assert mcp_call("propose_rename_item") == DISABLED


def test_the_switch_enables_mcp(unlocked_page: Page, mcp_call: McpCall, api: Api):
    enable_mcp(unlocked_page)
    assert api.get("/api/session")["mcpEnabled"] is True
    folders = json.loads(mcp_call("list_folders"))["folders"]
    assert {"Work", "Home", "Infra"} <= {folder["name"] for folder in folders}


def test_end_session_answers_and_the_page_asks_for_the_unlock(mcp_page: Page, mcp_call: McpCall):
    """The answer reaches the client through the gateway although the worker ends the session
    and exits, and the open page shows the unlock of the same account, which works again."""
    page = mcp_page
    assert mcp_call("end_session") == "The bwtidy session ends now."
    expect(page.get_by_role("button", name="Unlock", exact=True)).to_be_visible(
        timeout=UNLOCK_TIMEOUT
    )
    expect(page.get_by_role("button", name="Log in", exact=True)).to_have_count(0)
    unlock(page)
    grid(page)
    expect(mcp_switch(page)).not_to_be_checked()


def test_list_items_holds_no_secret_values(
    mcp_page: Page, mcp_call: McpCall, assert_no_secrets: Callable[[str], None]
):
    text = mcp_call("list_items")
    assert_no_secrets(text)
    items = {item["name"]: item for item in json.loads(text)["items"]}
    assert "login.password" in items["Example Mail"]["secretColumns"]


def test_list_items_leaves_out_the_tool_note(mcp_page: Page, mcp_call: McpCall, api: Api):
    grid = api.get("/api/grid")
    assert grid["toolNoteId"], "the grid has no bwtidy note"
    listed = json.loads(mcp_call("list_items"))
    ids = {item["id"] for item in listed["items"]}
    assert grid["toolNoteId"] not in ids
    assert TOOL_NOTE not in {item["name"] for item in listed["items"]}
    assert listed["count"] == len(grid["rows"]) - 1


## 19: proposals and the queue


def test_a_proposal_shows_in_the_queue_badge(
    mcp_page: Page, mcp_call: McpCall, propose: Propose, api: Api
):
    rename(propose, mcp_call, "Example Chat")
    pending = api.get("/api/queue?status=pending")
    count = len([entry for entry in pending if entry["source"] != "ui"])
    badge = mcp_page.get_by_role(
        "button", name=f"Queue, {count} MCP proposals to review", exact=True
    )
    expect(badge).to_be_visible(timeout=POLL_TIMEOUT)


def test_a_proposal_shows_in_the_grid_and_cannot_be_edited_there(
    mcp_page: Page, mcp_call: McpCall, propose: Propose
):
    page = mcp_page
    target = item_id(mcp_call, "Timesheet")
    propose("propose_move_to_folder", item_ids=[target], folder="Home")
    page.get_by_label("Search", exact=True).fill("Timesheet")
    folder = page.locator(f'{ROW}[row-id="{target}"] .ag-cell[col-id="folder"]')
    expect(folder).to_have_text("Home", timeout=POLL_TIMEOUT)
    expect(folder).to_have_class(QUEUED)

    folder.dblclick()
    page.wait_for_timeout(700)
    expect(page.locator(EDITOR).or_(page.get_by_role("dialog"))).to_have_count(0)


def test_a_proposal_is_not_committed_automatically(
    mcp_page: Page, mcp_call: McpCall, propose: Propose, api: Api
):
    target = item_id(mcp_call, "Expense portal")
    answer = propose("propose_move_to_folder", item_ids=[target], folder="Home")
    mcp_page.wait_for_timeout(AUTO_COMMIT_WAIT)
    assert statuses(api, answer["entryId"]) == ["pending"]
    assert json.loads(mcp_call("get_item", item_id=target))["folder"] == "Work"


def test_the_queue_shows_the_diff_of_a_proposal(
    mcp_page: Page, mcp_call: McpCall, propose: Propose
):
    page = mcp_page
    answer = propose("propose_move_to_folder", item_ids=[item_id(mcp_call, "VPN")], folder="Home")
    open_queue(page)
    card = entry_card(page, answer["entryId"])
    expect(card).to_contain_text(answer["summary"])
    expect(card).to_contain_text("from mcp")
    diff = card.get_by_role("table", name=f"Changes of entry {answer['entryId']}")
    expect(diff.get_by_role("row")).to_have_count(2)
    expect(diff.get_by_role("row").nth(1).get_by_role("cell")).to_have_text(
        ["VPN", "folder", "Work", "Home"]
    )


## the columns of a Queue change table, in order
HEADERS = ["Item", "Column", "Before", "After"]
LONG_TOKEN = "x" * 60


def diff_table(page: Page, answer: dict[str, Any]) -> Locator:
    card = entry_card(page, answer["entryId"])
    return card.get_by_role("table", name=f"Changes of entry {answer['entryId']}")


def header_boxes(table: Locator) -> list[FloatRect]:
    """The boxes of the four header cells of a table, in order."""
    boxes = []
    for name in HEADERS:
        box = table.get_by_role("columnheader", name=name, exact=True).bounding_box()
        assert box is not None
        boxes.append(box)
    return boxes


def header_lefts(table: Locator) -> list[float]:
    return [box["x"] for box in header_boxes(table)]


def header_widths(table: Locator) -> list[float]:
    return [box["width"] for box in header_boxes(table)]


def propose_shapes(propose: Propose, mcp_call: McpCall) -> list[dict[str, Any]]:
    """Three entries of different shapes: a rename, a new item and a trash."""
    created = propose(
        "propose_create_item",
        name="SSH Key (github privat)",
        type="login",
        folder="Work",
        values={"login.username": "user", "notes": "e2e"},
        generate_password=True,
    )
    renamed = propose("propose_rename_item", item_id=item_id(mcp_call, "VPN"), name="VPN (e2e)")
    trashed = propose("propose_trash_item", item_id=item_id(mcp_call, "Expense portal"))
    return [renamed, created, trashed]


@pytest.mark.parametrize("width", [2500, 1200])
def test_the_columns_of_three_entries_of_different_shapes_line_up(
    mcp_page: Page, mcp_call: McpCall, propose: Propose, width: int
):
    page = mcp_page
    answers = propose_shapes(propose, mcp_call)
    page.set_viewport_size({"width": width, "height": 1000})
    open_queue(page)
    tables = [diff_table(page, answer) for answer in answers]
    for table in tables:
        expect(table).to_be_visible()
    first = header_lefts(tables[0])
    for table in tables[1:]:
        for position, expected in zip(header_lefts(table), first, strict=True):
            assert abs(position - expected) <= 1
    ## the Before and After columns are not squeezed to a few characters
    for box_width in header_widths(tables[1]):
        assert box_width > 60


def test_a_long_token_widens_its_column_in_its_own_table_only(
    mcp_page: Page, mcp_call: McpCall, propose: Propose
):
    page = mcp_page
    short = propose("propose_rename_item", item_id=item_id(mcp_call, "VPN"), name="VPN (e2e)")
    long_name = propose(
        "propose_rename_item", item_id=item_id(mcp_call, "Payroll"), name=LONG_TOKEN
    )
    page.set_viewport_size({"width": 1000, "height": 1000})
    open_queue(page)
    plain, wide = diff_table(page, short), diff_table(page, long_name)
    expect(wide).to_be_visible()
    expect(plain).to_be_visible()
    ## the other table keeps the default shares of its width
    plain_box = plain.bounding_box()
    assert plain_box is not None
    for share, column_width in zip([0.22, 0.18, 0.3, 0.3], header_widths(plain), strict=True):
        assert abs(column_width - share * plain_box["width"]) <= 2
    ## the long name is in the After column, which grows where it needs the room
    assert header_widths(wide)[3] > header_widths(plain)[3] + 10
    ## the table stays inside its card
    card_box = entry_card(page, long_name["entryId"]).bounding_box()
    table_box = wide.bounding_box()
    assert card_box is not None
    assert table_box is not None
    assert table_box["x"] + table_box["width"] <= card_box["x"] + card_box["width"] + 1


def test_approve_selected_applies_only_the_selected_entry(
    mcp_page: Page, mcp_call: McpCall, propose: Propose, api: Api
):
    page = mcp_page
    chosen = rename(propose, mcp_call, "Payroll")
    other = rename(propose, mcp_call, "Customer CRM")
    open_queue(page)
    select(page, chosen)
    page.get_by_role("button", name="Approve selected").click()

    expect(approve_result(page)).to_contain_text("Applied 1 entry", timeout=APPROVE_TIMEOUT)
    assert statuses(api, chosen["entryId"], other["entryId"]) == ["applied", "pending"]
    page.get_by_role("tab", name="Applied").click()
    expect(entry_card(page, chosen["entryId"])).to_contain_text(chosen["summary"])
    assert item_id(mcp_call, "Payroll (e2e)")


def test_reject_selected_rejects_only_the_selected_entry(
    mcp_page: Page, mcp_call: McpCall, propose: Propose, api: Api
):
    page = mcp_page
    chosen = rename(propose, mcp_call, "Issue tracker")
    other = rename(propose, mcp_call, "Online bank")
    open_queue(page)
    select(page, chosen)
    page.get_by_role("button", name="Reject selected").click()

    expect(page.get_by_text("Rejected 1 entry.")).to_be_visible()
    assert statuses(api, chosen["entryId"], other["entryId"]) == ["rejected", "pending"]
    page.get_by_role("tab", name="Rejected").click()
    expect(entry_card(page, chosen["entryId"])).to_contain_text(chosen["summary"])
    assert item_id(mcp_call, "Issue tracker")


def proposal(mcp_call: McpCall, entry_id: int, status: str | None = None) -> dict[str, Any] | None:
    """The entry as list_proposals shows it, None when it is not listed."""
    arguments = {} if status is None else {"status": status}
    listed = json.loads(mcp_call("list_proposals", **arguments))["proposals"]
    return next((entry for entry in listed if entry["id"] == entry_id), None)


def test_list_proposals_says_what_the_user_rejected(
    mcp_page: Page, mcp_call: McpCall, propose: Propose
):
    page = mcp_page
    answer = rename(propose, mcp_call, "Conference room booking")
    waiting = proposal(mcp_call, answer["entryId"])
    assert waiting is not None
    assert waiting["summary"] == answer["summary"]
    assert (waiting["status"], waiting["decidedAt"], waiting["reason"]) == ("pending", None, None)
    open_queue(page)
    select(page, answer)
    page.get_by_role("button", name="Reject selected").click()

    expect(page.get_by_text("Rejected 1 entry.")).to_be_visible()
    rejected = proposal(mcp_call, answer["entryId"], "rejected")
    assert rejected is not None
    assert rejected["summary"] == answer["summary"]
    assert rejected["status"] == "rejected"
    assert rejected["decidedAt"]
    assert rejected["reason"] is None
    assert proposal(mcp_call, answer["entryId"], "pending") is None


def test_approve_all_applies_every_pending_entry(
    mcp_page: Page, mcp_call: McpCall, propose: Propose, api: Api
):
    page = mcp_page
    first = rename(propose, mcp_call, "Train tickets")
    second = rename(propose, mcp_call, "Mastercard credit")
    mine = [first["entryId"], second["entryId"]]
    pending = [entry["id"] for entry in api.get("/api/queue?status=pending")]
    assert pending == mine, "entries another test left pending would be approved too"
    open_queue(page)
    expect(page.get_by_role("checkbox", name=second["summary"], exact=True)).to_be_visible()
    page.get_by_role("button", name="Approve all").click()

    expect(approve_result(page)).to_contain_text("Applied 2 entries", timeout=APPROVE_TIMEOUT)
    expect(page.get_by_role("progressbar", name="Working")).to_have_count(0)
    expect(page.get_by_text("No changes waiting for review.")).to_be_visible()
    assert statuses(api, *mine) == ["applied", "applied"]
    page.get_by_role("tab", name="Applied").click()
    for answer in (first, second):
        expect(entry_card(page, answer["entryId"])).to_contain_text(answer["summary"])


def test_a_proposal_that_no_longer_fits_fails_with_its_reason(
    mcp_page: Page, mcp_call: McpCall, propose: Propose, api: Api
):
    page = mcp_page
    target = item_id(mcp_call, "Windows license key")
    trash = propose("propose_trash_item", item_id=target)
    renamed = propose("propose_rename_item", item_id=target, name="Windows license key (e2e)")
    open_queue(page)
    select(page, trash)
    page.get_by_role("button", name="Approve selected").click()
    expect(approve_result(page)).to_contain_text("Applied 1 entry", timeout=APPROVE_TIMEOUT)

    select(page, renamed)
    page.get_by_role("button", name="Approve selected").click()
    expect(approve_result(page)).to_contain_text("Failed 1 entry", timeout=APPROVE_TIMEOUT)
    assert statuses(api, trash["entryId"], renamed["entryId"]) == ["applied", "failed"]
    page.get_by_role("tab", name="Failed").click()
    card = entry_card(page, renamed["entryId"])
    expect(card).to_contain_text(renamed["summary"])
    expect(card.get_by_role("alert").filter(has_text="Failed:")).to_contain_text("changed")


## 20: a guessed secret


GUESSED_ITEM = "Example Mail"
WRONG_GUESS = "Wrong guess e2e 4711"


@pytest.fixture
def own_password(fixture_items: dict[str, dict[str, Any]]) -> str:
    return fixture_items[GUESSED_ITEM]["login"]["password"]


def test_own_password_and_a_wrong_value_get_answers_of_the_same_shape(
    mcp_call: McpCall, propose: Propose, own_password: str
):
    target = item_id(mcp_call, GUESSED_ITEM)
    refused = propose("propose_rename_item", item_id=target, name=own_password)
    accepted = propose("propose_rename_item", item_id=target, name=WRONG_GUESS)
    for answer in (refused, accepted):
        assert sorted(answer) == ["entryId", "summary"]
        assert isinstance(answer["entryId"], int)
        assert answer["summary"].startswith(f'Rename "{GUESSED_ITEM}" to "')
    ## a bool first, so a failure does not show the summary with the password
    same = refused["summary"] == f'Rename "{GUESSED_ITEM}" to "{own_password}"'
    assert same, "the refused proposal got another summary than an accepted one would"


def test_own_password_lands_in_rejected_as_refused(
    mcp_page: Page,
    mcp_call: McpCall,
    propose: Propose,
    api: Api,
    own_password: str,
    assert_no_secrets: Callable[[str], None],
):
    page = mcp_page
    answer = propose(
        "propose_rename_item", item_id=item_id(mcp_call, GUESSED_ITEM), name=own_password
    )
    assert statuses(api, answer["entryId"]) == ["rejected"]
    ## to the agent it waits for the user like any proposal, until his next decision
    seen = proposal(mcp_call, answer["entryId"])
    assert seen is not None
    as_answered = seen["summary"] == answer["summary"]
    assert as_answered, "list_proposals shows another summary than the propose call answered"
    assert (seen["status"], seen["decidedAt"], seen["reason"]) == ("pending", None, None)
    open_queue(page)
    page.get_by_role("tab", name="Rejected").click()

    card = entry_card(page, answer["entryId"])
    expect(card).to_contain_text(f'Refused proposal for "{GUESSED_ITEM}"')
    expect(card.get_by_role("alert").filter(has_text="Refused:")).to_have_count(1)
    expect(card.get_by_test_id("masked-value")).to_have_count(1)
    html = page.content()
    shown = own_password in html
    assert not shown, "the item's own password is on the page"
    assert_no_secrets(html)


def test_a_wrong_value_waits_in_pending(mcp_call: McpCall, propose: Propose, api: Api):
    answer = propose(
        "propose_rename_item", item_id=item_id(mcp_call, GUESSED_ITEM), name=WRONG_GUESS
    )
    assert statuses(api, answer["entryId"]) == ["pending"]


## a new item


def test_a_proposed_new_item_shows_its_values_and_is_created_when_approved(
    mcp_page: Page, mcp_call: McpCall, propose: Propose, api: Api
):
    page = mcp_page
    name = "Created by e2e"
    answer = propose(
        "propose_create_item",
        name=name,
        type="login",
        folder="Work",
        values={"login.username": "e2e"},
        generate_password=True,
    )
    assert answer["summary"] == f'Create login "{name}" with a generated password'
    open_queue(page)
    card = entry_card(page, answer["entryId"])
    expect(card).to_contain_text(answer["summary"])
    diff = card.get_by_role("table", name=f"Changes of entry {answer['entryId']}")
    rows = diff.get_by_role("row")
    ## the header, then name, type, folder, login.username and the generated password
    expect(rows).to_have_count(6)
    expect(rows.nth(1).get_by_role("cell")).to_have_text([name, "name", "none", name])
    expect(rows.nth(4).get_by_role("cell")).to_have_text([name, "login.username", "none", "e2e"])
    expect(rows.nth(5).get_by_role("cell")).to_have_text(
        [name, "login.password", "none", "(generated)"]
    )
    select(page, answer)
    page.get_by_role("button", name="Approve selected").click()
    expect(approve_result(page)).to_contain_text("Applied 1 entry", timeout=APPROVE_TIMEOUT)

    row = rows_by_name(api)[name]
    assert row["cells"]["login.username"] == "e2e"
    assert row["cells"]["folder"] == "Work"
    assert row["cells"]["login.password"] == {"masked": True}
    revealed = api.request.get(f"/api/items/{row['id']}/secret?column=login.password")
    generated = revealed.ok and len(revealed.json()["value"]) == 24
    assert generated, "the new login has no generated password of 24 characters"
    ## the new item goes to the trash again, so no other test sees it
    after = last_entry_id(api)
    trash = {"kind": "trashItem", "itemId": row["id"], "revisionDate": row["revisionDate"]}
    api.post("/api/queue", {"operations": [trash]})
    commit_new_entries(api, after)


def test_a_proposed_item_with_a_generated_secret_and_a_blank_one(
    mcp_page: Page, propose: Propose, api: Api
):
    page = mcp_page
    name = "Generated by e2e"
    generated = "fields.generated key"
    blank = "fields.blank key"
    template = {
        "name": "E2E two keys",
        "type": 2,
        "coordinates": [
            {"kind": "field", "name": "generated key", "fieldType": 1},
            {"kind": "field", "name": "blank key", "fieldType": 1},
        ],
    }
    before = wait_templates_saved(api)["templates"]
    save_templates(api, [*before, template])
    try:
        answer = propose(
            "propose_create_item",
            name=name,
            template=template["name"],
            generate_secrets=[generated],
        )
        assert (
            answer["summary"]
            == f'Create "{name}" from template "E2E two keys" with generated values'
        )
        open_queue(page)
        card = entry_card(page, answer["entryId"])
        diff = card.get_by_role("table", name=f"Changes of entry {answer['entryId']}")
        rows = diff.get_by_role("row")
        ## the header, then name, type and the generated value, nothing for the blank field
        expect(rows).to_have_count(4)
        expect(rows.nth(3).get_by_role("cell")).to_have_text(
            [name, generated, "none", "(generated)"]
        )
        select(page, answer)
        page.get_by_role("button", name="Approve selected").click()
        expect(approve_result(page)).to_contain_text("Applied 1 entry", timeout=APPROVE_TIMEOUT)

        row = rows_by_name(api)[name]
        assert api.get("/api/grid")["toFill"][row["id"]] == [blank]
        page.reload()
        grid(page)
        ## near its template, not full: the type and the generated key of three
        expect(cell(page, name, "template")).to_have_text("E2E two keys 2/3")
        expect(item_cell(page, row["id"], blank)).to_have_class(TO_FILL)
        target = item_cell(page, row["id"], generated)
        expect(target).not_to_have_class(TO_FILL)
        target.get_by_role("button", name=f"Reveal {generated}").click()
        expect(target.get_by_role("button", name=f"Hide {generated}")).to_be_visible()
        shown = target.get_by_test_id("secret-value").inner_text()
        is_generated = len(shown) == GENERATED_LENGTH and secret_is(api, row, generated, shown)
        assert is_generated, "the revealed value is not a generated one of 24 characters"
    finally:
        trash(api, name)
        save_templates(api, before)


def test_a_proposed_attachment_rename_is_approved_and_restored_from_the_change_log(
    mcp_page: Page, mcp_call: McpCall, propose: Propose, api: Api
):
    """The agent names the attachment, bwtidy renames it, and its contents never pass
    through MCP."""
    page = mcp_page
    target = item_id(mcp_call, "ThinkPad X1")
    [attachment] = json.loads(mcp_call("get_item", item_id=target))["attachments"]
    assert attachment["fileName"] == "thinkpad-recovery.txt"
    answer = propose(
        "propose_rename_attachment",
        item_id=target,
        attachment_id=attachment["id"],
        new_name="bitlocker-recovery.txt",
    )
    assert answer["summary"] == (
        'Rename attachment "thinkpad-recovery.txt" of "ThinkPad X1" to "bitlocker-recovery.txt"'
    )
    open_queue(page)
    card = entry_card(page, answer["entryId"])
    diff = card.get_by_role("table", name=f"Changes of entry {answer['entryId']}")
    expect(diff.get_by_role("row").nth(1)).to_contain_text("thinkpad-recovery.txt")
    expect(diff.get_by_role("row").nth(1)).to_contain_text("bitlocker-recovery.txt")
    select(page, answer)
    page.get_by_role("button", name="Approve selected").click()
    expect(approve_result(page)).to_contain_text("Applied 1 entry", timeout=APPROVE_TIMEOUT)
    [renamed] = json.loads(mcp_call("get_item", item_id=target))["attachments"]
    assert renamed["fileName"] == "bitlocker-recovery.txt"
    assert renamed["size"] == attachment["size"]

    page.get_by_role("button", name="Change log", exact=True).click()
    log = page.get_by_role("table", name="Change log")
    row = log.get_by_role("row").filter(has_text="renameAttachment").first
    expect(row).to_contain_text("Rename attachment")
    expect(row).to_contain_text(f"Attachment {attachment['id']} replaced by {renamed['id']}")
    after = last_entry_id(api)
    row.get_by_role("button", name="Restore").click()
    restore = page.get_by_role("dialog", name="Restore")
    expect(restore).to_contain_text("Give the attachment back the name it had before this change")
    expect(restore).to_contain_text(
        'rename the attachment "bitlocker-recovery.txt" back to "thinkpad-recovery.txt"'
    )
    restore.get_by_role("button", name="Restore").click()
    expect(restore).to_be_hidden()
    commit_new_entries(api, after)
    [back] = json.loads(mcp_call("get_item", item_id=target))["attachments"]
    assert back["fileName"] == "thinkpad-recovery.txt"
