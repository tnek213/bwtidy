"""Accessibility: axe-core on every view and dialog of docs/reference/ui.md in both color modes,
and the main flows with the keyboard alone (docs/reference/ui.md, Keyboard).

The axe tests open each page, menu and dialog, run axe-core (axe-playwright-python bundles it)
in the light and the dark color mode and fail with every violation they found, named by view,
rule and element. KEPT names the few that bwtidy keeps on purpose, each for its element only.
The keyboard tests press keys only, never click, and check at every step that the focus is
where it should be and shows a ring.
"""

import json
import re
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Locator, Page, expect
from support.api import Api, rows_by_name
from support.login import EMAIL, PASSWORD, SERVER, log_in, log_out_dialog, open_account, web_login
from support.mcp import McpCall
from support.pages import (
    MASK,
    QUEUED,
    UNLOCK_TIMEOUT,
    cell,
    grid,
    item_cell,
    pick,
    row,
    search,
    settle,
)
from support.queue_page import APPROVE_TIMEOUT, enable_mcp, item_id, open_queue
from support.vault_items import new_item_dialog, save_templates

pytestmark = pytest.mark.usefixtures("file_put_back")

SCHEMES = ("light", "dark")
## the page's background in each, set once MUI has switched its theme
BACKGROUNDS = {"light": "rgb(255, 255, 255)", "dark": "rgb(18, 18, 18)"}
## WCAG 2.2 A and AA and axe's best practices (landmarks, headings, unique names)
AXE_OPTIONS = {
    "runOnly": {
        "type": "tag",
        "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"],
    },
    "resultTypes": ["violations"],
}
## (rule, selector of the element, why it stays): violations bwtidy keeps, for that element only
KEPT = [
    ## the build commit after the title is faint on purpose: it changes with every commit, and
    ## the About page shows the version and the commit in full contrast
    ("color-contrast", "header [data-shot-hide]"),
    ## a menu, a popover and a tooltip are layers over the page that MUI puts at the end of the
    ## body, outside the landmarks, and belong to the control that opened them
    ("region", ".MuiPopover-root *, .MuiPopper-root, .MuiPopper-root *"),
]
## runs axe and answers its violations as lines, without the ones KEPT allows
AXE_RUN = """async ([options, kept]) => {
    const results = await axe.run(document, options)
    const allowed = (rule, node) => kept.some(([id, selector]) => {
        const element = document.querySelector(node.target[node.target.length - 1])
        return id === rule && element !== null && element.matches(selector)
    })
    return results.violations.flatMap(violation => violation.nodes
        .filter(node => !allowed(violation.id, node))
        .map(node => `${violation.id} (${violation.help}) at ${node.target.join(' ')}: `
            + node.failureSummary.replace(/\\s+/g, ' ')))
}"""
## true once no transition or finite animation runs (a menu that grows, a dialog that fades in)
SETTLED = """() => document.getAnimations().every(animation =>
    animation.playState !== 'running' || animation.effect?.getTiming().iterations === Infinity)"""

## items no other test of this file touches
EDITED = "Streaming video"
PROPOSED = "Grocery delivery"
REJECTED = "Payroll"
RESTORED = "Conference room booking"
KEYBOARD_ITEM = "Customer CRM"
LOGIN = "Example Mail"
NOTE_WITH_FIELDS = "Old webshop"
ATTACHMENTS = "Internal CA"
## near the top of the grid, so their rows are rendered
TICKED = ("Bike club", "Car insurance")
## written in one batch, so the Change log shows their rows sharing one backup
GROUPED = ("Pharmacy", "Dashboards")
## written alone, its backup then deleted, so the Change log shows a backup that is gone
GONE_BACKUP_ITEM = "Example Chat"
## a manual backup waits for a running background backup and takes one itself
BACKUP_MS = 180_000
TEMPLATE_NAME = "A11y login"
TEMPLATE: dict[str, Any] = {
    "name": TEMPLATE_NAME,
    "type": 1,
    "description": "A login with a user name and a password",
    "coordinates": [
        {"kind": "path", "path": "login.username"},
        {"kind": "path", "path": "login.password"},
    ],
}


class Axes:
    """Runs axe on views and keeps what it found, so one run names every violation."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.axe = Axe()
        self.found: list[str] = []

    def scan(self, view: str) -> None:
        """Run axe on the page as it is now, in the light and in the dark color mode."""
        page = self.page
        ## off every control, so no hover tint is in the colors axe measures
        page.mouse.move(0, 0)
        for scheme in SCHEMES:
            page.emulate_media(color_scheme=scheme)
            page.wait_for_function(
                "color => getComputedStyle(document.body).backgroundColor === color",
                arg=BACKGROUNDS[scheme],
            )
            page.wait_for_function(SETTLED)
            page.evaluate(self.axe.axe_script)
            lines = page.evaluate(AXE_RUN, [AXE_OPTIONS, KEPT])
            self.found += [f"{view} ({scheme}): {line}" for line in lines]
        page.emulate_media(color_scheme="light")

    def check(self) -> None:
        assert not self.found, "axe found violations:\n" + "\n".join(self.found)


@pytest.fixture
def axes(page: Page) -> Iterator[Axes]:
    yield Axes(page)


def close(page: Page, layer: Locator) -> None:
    """Close a menu or dialog with Escape."""
    page.keyboard.press("Escape")
    expect(layer).to_be_hidden()


def menu_entry(page: Page, name: str) -> Locator:
    return page.get_by_role("menu").get_by_role("menuitem", name=name, exact=True)


def scan_dialog(axes: Axes, view: str) -> None:
    """Scan the dialog that is open (there is one) and close it with Escape."""
    dialog = axes.page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    axes.scan(view)
    close(axes.page, dialog)


def cell_menu_dialog(axes: Axes, target: Locator, entry: str) -> None:
    """Open a dialog from the cell menu of the target, scan it and close it."""
    target.click(button="right")
    menu_entry(axes.page, entry).click()
    scan_dialog(axes, f"cell menu, {entry}")


def header_menu_dialog(axes: Axes, column: str, entry: str) -> None:
    axes.page.get_by_role("button", name=f"Actions for {column}", exact=True).click()
    menu_entry(axes.page, entry).click()
    scan_dialog(axes, f"header menu of {column}, {entry}")


def test_the_login_pages_pass_axe(page: Page, axes: Axes) -> None:
    page.goto("/")
    expect(page.get_by_role("button", name="Continue", exact=True)).to_be_visible(
        timeout=UNLOCK_TIMEOUT
    )
    axes.scan("account form")
    page.get_by_label("Server").fill(SERVER)
    page.get_by_label("Email").fill("nobody-a11y@bwtidy.test")
    page.get_by_role("button", name="Continue", exact=True).click()
    scan_dialog(axes, "create account dialog")

    open_account(page, EMAIL)
    if page.get_by_label("Server").count():
        axes.scan("Bitwarden login")
    else:
        axes.scan("unlock")
    log_in(page)
    grid(page)

    log_out_dialog(page, bitwarden=True)
    axes.scan("log out dialog")
    page.get_by_role("dialog", name="Log out").get_by_role("button", name="Log out").click()
    expect(page.get_by_role("button", name="Log in", exact=True)).to_be_visible(
        timeout=UNLOCK_TIMEOUT
    )
    axes.scan("Bitwarden login")
    web_login(page)
    grid(page)
    axes.check()


def test_the_vault_page_passes_axe(unlocked_page: Page, axes: Axes, api: Api) -> None:
    page = unlocked_page
    axes.scan("vault")

    page.get_by_role("button", name="Columns", exact=True).click()
    axes.scan("column chooser")
    page.keyboard.press("Escape")

    password = cell(page, LOGIN, "login.password")
    password.click(button="right")
    axes.scan("cell menu")
    close(page, page.get_by_role("menu"))
    cell_menu_dialog(axes, password, "Edit value")
    cell_menu_dialog(axes, password, "Generate new value")
    cell_menu_dialog(axes, password, "Generator settings for this item")
    cell_menu_dialog(axes, password, "Add field")
    cell_menu_dialog(axes, password, "Convert to template…")
    cell_menu_dialog(axes, password, "Merge into item…")
    cell_menu_dialog(axes, password, "Move to trash")
    uris = cell(page, LOGIN, "login.uris")
    uris.dblclick()
    scan_dialog(axes, "login.uris dialog")
    cell(page, LOGIN, "organization").dblclick()
    scan_dialog(axes, "organization dialog")
    attachments = cell(page, ATTACHMENTS, "attachments")
    cell_menu_dialog(axes, attachments, "Rename attachments")

    header_menu_dialog(axes, "folder", "Manage folders…")
    header_menu_dialog(axes, "rareFields", "Set N")
    search(page, NOTE_WITH_FIELDS)
    page.get_by_label("Hide empty columns").check()
    settle(page)
    page.get_by_role("button", name="Actions for fields.Password", exact=True).click()
    axes.scan("header menu")
    close(page, page.get_by_role("menu"))
    header_menu_dialog(axes, "fields.Password", "Rename field…")
    header_menu_dialog(axes, "fields.Password", "Merge into…")
    page.get_by_label("Hide empty columns").uncheck()
    search(page, "")

    rows = rows_by_name(api)
    for name in TICKED:
        row(page, rows[name]["id"]).get_by_role("checkbox").check()
    axes.scan("selection bar")
    bar = page.get_by_role("toolbar", name="Selected items")
    bar.get_by_role("button", name="Move to trash").click()
    scan_dialog(axes, "move items to the trash")
    bar.get_by_role("button", name="Clear").click()

    dialog = new_item_dialog(page)
    pick(dialog, "Start from", "Login")
    scan_dialog(axes, "new item dialog")

    page.get_by_role("button", name=re.compile(r"^Color mode")).click()
    axes.scan("color mode menu")
    close(page, page.get_by_role("menu"))
    page.get_by_role("button", name="More actions").click()
    axes.scan("more actions menu")
    close(page, page.get_by_role("menu"))
    page.get_by_role("button", name="MCP token").click()
    axes.scan("MCP token menu")
    close(page, page.get_by_role("menu"))
    log_out_dialog(page)
    scan_dialog(axes, "log out dialog")
    axes.check()


def test_the_mcp_token_dialog_passes_axe(unlocked_page: Page, axes: Axes, mcp_token: str) -> None:
    page = unlocked_page
    page.get_by_role("button", name="MCP token").click()
    page.get_by_role("menuitem", name=re.compile(r"^(Create|Replace)")).click()
    dialog = page.get_by_role("dialog", name="New MCP token")
    expect(dialog).to_be_visible()
    axes.scan("new MCP token dialog")
    dialog.get_by_role("button", name=re.compile(r"^(Create|Replace)")).click()
    token = page.get_by_role("dialog", name="MCP token", exact=True)
    expect(token).to_be_visible()
    axes.scan("MCP token dialog")
    close(page, token)
    axes.check()


@pytest.fixture
def one_template(unlocked_page: Page, api: Api) -> Iterator[None]:
    save_templates(api, [TEMPLATE])
    yield
    save_templates(api, [])


@pytest.mark.usefixtures("one_template")
def test_the_types_pages_pass_axe(unlocked_page: Page, axes: Axes) -> None:
    page = unlocked_page
    page.get_by_role("navigation").get_by_role("button", name="Types").click()
    expect(page.get_by_role("cell", name=TEMPLATE_NAME, exact=True)).to_be_visible()
    axes.scan("templates")
    page.get_by_role("button", name=f"Edit {TEMPLATE_NAME}").click()
    scan_dialog(axes, "template editor")
    page.get_by_role("button", name=f"Delete {TEMPLATE_NAME}").click()
    scan_dialog(axes, "delete template dialog")
    page.get_by_role("button", name="New template").click()
    editor = page.get_by_role("dialog", name="New template")
    editor.get_by_role("button", name="Add coordinate").click()
    page.get_by_role("option", name="New field…", exact=True).click()
    axes.scan("new template editor")
    close(page, editor)

    page.get_by_role("tab", name="Datatypes").click()
    axes.scan("datatypes")
    page.get_by_role("button", name="New datatype").click()
    editor = page.get_by_role("dialog", name="New datatype")
    editor.get_by_role("textbox", name="Regular expression").fill("[0-9]{4}")
    editor.get_by_role("textbox", name="Examples", exact=True).fill("1234\n12a4")
    axes.scan("datatype editor")
    close(page, editor)
    page.get_by_role("button", name="Pattern limits and statistics").click()
    expect(page.get_by_role("heading", name="Statistics")).to_be_visible()
    axes.scan("statistics")
    axes.check()


Propose = Callable[[str], dict[str, Any]]


def entry_box(page: Page, answer: dict[str, Any]) -> Locator:
    """The checkbox of a pending entry, named by its summary."""
    card = page.get_by_test_id(f"queue-entry-{answer['entryId']}")
    return card.get_by_role("checkbox", name=answer["summary"], exact=True)


@pytest.fixture
def propose_rename(unlocked_page: Page, mcp_call: McpCall, api: Api) -> Iterator[Propose]:
    """Propose through MCP to rename an item, rejecting what is left pending after the test."""
    enable_mcp(unlocked_page)
    ids: list[int] = []

    def run(name: str) -> dict[str, Any]:
        target = item_id(mcp_call, name)
        answer = json.loads(mcp_call("propose_rename_item", item_id=target, name=f"{name} (a11y)"))
        ids.append(answer["entryId"])
        return answer

    yield run
    pending = {entry["id"] for entry in api.get("/api/queue?status=pending")}
    left = [entry_id for entry_id in ids if entry_id in pending]
    if left:
        api.post("/api/queue/reject", {"ids": left})


def write_usernames(api: Api, names: tuple[str, ...]) -> str:
    """Write a new username to each item in one batch, answer the backup of that write."""
    rows = rows_by_name(api)
    operations = [
        {
            "kind": "setLeaf",
            "itemId": rows[name]["id"],
            "revisionDate": rows[name]["revisionDate"],
            "column": "login.username",
            "value": "a11y-batch",
        }
        for name in names
    ]
    answer = api.post("/api/operations", {"operations": operations}, timeout=BACKUP_MS)
    return answer["backup"]


def test_the_queue_and_the_change_log_pass_axe(
    unlocked_page: Page, axes: Axes, api: Api, propose_rename: Propose
) -> None:
    page = unlocked_page
    rows = rows_by_name(api)
    target = item_cell(page, rows[EDITED]["id"], "login.username")
    search(page, EDITED)
    target.click()
    page.keyboard.press("Enter")
    page.locator(".ag-cell-inline-editing input").fill("a11y-user")
    page.keyboard.press("Enter")
    expect(target).to_have_class(QUEUED)
    proposal = propose_rename(PROPOSED)
    open_queue(page)
    expect(entry_box(page, proposal)).to_be_visible(timeout=15_000)
    axes.scan("queue, pending")
    entry_box(page, proposal).check()
    page.get_by_role("button", name="Reject selected").click()
    page.get_by_role("tab", name=re.compile(r"^Rejected")).click()
    axes.scan("queue, rejected")
    page.get_by_role("button", name="Commit now").click()
    api.wait_saved()
    page.get_by_role("tab", name=re.compile(r"^Applied")).click()
    expect(page.get_by_text("a11y-user").first).to_be_visible()
    axes.scan("queue, applied")
    page.get_by_role("tab", name=re.compile(r"^Failed")).click()
    axes.scan("queue, failed")

    ## the rows that share a backup and a backup that is gone look fainter, so they are scanned too
    shared = write_usernames(api, GROUPED)
    gone = write_usernames(api, (GONE_BACKUP_ITEM,))
    assert shared != gone, "the two writes share one backup"
    ## a backup by hand, so the one of the last write is not the newest and can be deleted
    api.post("/api/backups", timeout=BACKUP_MS)
    api.delete(f"/api/backups/{gone}")
    page.get_by_role("navigation").get_by_role("button", name="Change log").click()
    log = page.get_by_role("table", name="Change log")
    expect(log).to_be_visible()
    struck = log.get_by_text(gone, exact=True).first
    expect(struck).to_have_css("text-decoration-line", "line-through")
    expect(log.get_by_text(shared, exact=True)).not_to_have_count(0)
    axes.scan("change log")
    page.get_by_role("button", name="Diff report").click()
    scan_dialog(axes, "diff report dialog")
    page.get_by_role("button", name="Restore").first.click()
    scan_dialog(axes, "restore dialog")
    delete = page.get_by_role("button", name="Delete", exact=True)
    if delete.count() > 1 and delete.last.is_enabled():
        delete.last.click()
        scan_dialog(axes, "delete backup dialog")
    axes.check()


def test_settings_about_and_statistics_pass_axe(unlocked_page: Page, axes: Axes) -> None:
    page = unlocked_page
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("menuitem", name="Settings").click()
    expect(page.get_by_role("heading", name="Settings")).to_be_visible()
    axes.scan("settings")
    page.get_by_role("button", name="Delete my data").click()
    scan_dialog(axes, "delete my data dialog")
    page.get_by_role("button", name="Delete my account").click()
    scan_dialog(axes, "delete my account dialog")
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("menuitem", name="About bwtidy").click()
    expect(page.get_by_role("heading", name="About bwtidy")).to_be_visible()
    axes.scan("about")
    axes.check()


## the keyboard flows

## why the element that has the focus shows no focus ring, empty when it shows one: an outline,
## a shadow, the border of a focused MUI field or the border of AG Grid's focused cell
NO_FOCUS_RING = """() => {
    const element = document.activeElement
    if (!element || element === document.body) {
        return 'nothing has the focus'
    }
    const ring = element.closest('.Mui-focusVisible') ?? element
    const style = getComputedStyle(ring)
    if (style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 1) {
        return ''
    }
    if (style.boxShadow !== 'none') {
        return ''
    }
    if (element.closest('.Mui-focused')) {
        return ''
    }
    const cell = element.closest('.ag-cell-focus')
    if (cell && getComputedStyle(cell).borderTopStyle !== 'none') {
        return ''
    }
    return `no focus ring on ${element.tagName} ${element.getAttribute('aria-label') ?? ''}`
}"""


def focused(page: Page) -> Locator:
    return page.locator(":focus")


def expect_focus_ring(page: Page) -> None:
    missing = page.evaluate(NO_FOCUS_RING)
    assert not missing, missing


def press(page: Page, key: str) -> None:
    """Press a key and check that the focus shows."""
    page.keyboard.press(key)
    expect_focus_ring(page)


def tab_to(page: Page, target: Locator, key: str = "Tab", limit: int = 40) -> None:
    """Press Tab (or another key) until the target has the focus, the ring showing each time."""
    for _ in range(limit):
        page.keyboard.press(key)
        page.wait_for_function(SETTLED)
        ## the focus passes the browser's own controls on its way round the page
        if page.evaluate("() => document.activeElement === document.body"):
            continue
        expect_focus_ring(page)
        if target.evaluate("element => element === document.activeElement"):
            return
    raise AssertionError(f"{key} did not reach the target in {limit} presses")


def focused_cell_column(page: Page) -> str | None:
    return page.evaluate(
        "() => document.activeElement?.closest('.ag-cell')?.getAttribute('col-id') ?? null"
    )


def arrow_to_column(page: Page, column: str, limit: int = 120) -> None:
    """Move the focused grid cell right along its row with ArrowRight until it is in the column."""
    for _ in range(limit):
        if focused_cell_column(page) == column:
            expect_focus_ring(page)
            return
        page.keyboard.press("ArrowRight")
    raise AssertionError(f"ArrowRight did not reach {column}")


def into_grid(page: Page) -> None:
    """From the search box Tab to the grid's header, then ArrowDown to the first row."""
    tab_to(page, page.locator(".ag-header-cell").first)
    press(page, "ArrowDown")
    expect(focused(page)).to_have_attribute("role", "gridcell")


def test_log_in_with_the_keyboard(page: Page) -> None:
    page.goto("/")
    email = page.get_by_role("textbox", name="Email")
    password = page.get_by_role("textbox", name="Master password", exact=True)
    expect(email.or_(password)).to_be_visible(timeout=UNLOCK_TIMEOUT)
    if email.is_visible():
        expect(email).to_be_focused()
        tab_to(page, page.get_by_role("textbox", name="Server"), "Shift+Tab")
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.type(SERVER)
        tab_to(page, email)
        page.keyboard.type(EMAIL)
        page.keyboard.press("Enter")
        create = page.get_by_role("dialog", name="Create account")
        expect(password.or_(create)).to_be_visible(timeout=UNLOCK_TIMEOUT)
        if create.is_visible():
            tab_to(page, create.get_by_role("button", name="Create", exact=True))
            page.keyboard.press("Enter")
            expect(password).to_be_visible(timeout=UNLOCK_TIMEOUT)
    ## the Bitwarden login asks for the email until a login saved it
    web_email = page.get_by_label("Email")
    if (
        page.get_by_role("button", name="Log in", exact=True).count()
        and not web_email.input_value()
    ):
        tab_to(page, web_email)
        page.keyboard.type(EMAIL)
    if not password.evaluate("element => element === document.activeElement"):
        tab_to(page, password)
    page.keyboard.type(PASSWORD)
    page.keyboard.press("Enter")
    grid(page)


def test_find_edit_and_reveal_with_the_keyboard(unlocked_page: Page, api: Api) -> None:
    page = unlocked_page
    item = rows_by_name(api)[KEYBOARD_ITEM]
    search_box = page.get_by_role("searchbox", name="Search")
    tab_to(page, search_box)
    page.keyboard.type(KEYBOARD_ITEM)
    settle(page)
    into_grid(page)

    arrow_to_column(page, "login.username")
    press(page, "Enter")
    editor = page.locator(".ag-cell-inline-editing input")
    expect(editor).to_be_focused()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("keyboard-user")
    press(page, "Enter")
    username = item_cell(page, item["id"], "login.username")
    expect(username).to_have_class(QUEUED)
    expect(username).to_be_focused()
    expect(username).to_have_attribute("aria-description", re.compile(r"^Unsaved in #\d+"))

    arrow_to_column(page, "login.password")
    password = item_cell(page, item["id"], "login.password")
    press(page, "Shift+F10")
    reveal = menu_entry(page, "Reveal value")
    expect(reveal).to_be_focused()
    press(page, "Enter")
    expect(password).to_be_focused()
    value = password.get_by_test_id("secret-value")
    expect(value).not_to_have_text(MASK)
    expect(value).to_have_attribute("aria-live", "polite")
    press(page, "Shift+F10")
    expect(menu_entry(page, "Hide value")).to_be_focused()
    press(page, "Enter")
    expect(value).to_have_text(MASK)
    expect(value.get_by_role("img", name="Hidden")).to_be_visible()

    press(page, "Shift+F10")
    expect(page.get_by_role("menu")).to_be_visible()
    press(page, "Escape")
    expect(page.get_by_role("menu")).to_be_hidden()
    expect(password).to_be_focused()

    ## Tab leaves the grid instead of moving to the next cell
    page.keyboard.press("Tab")
    focus_in_grid = page.evaluate("() => !!document.activeElement?.closest('.ag-root')")
    assert not focus_in_grid, "Tab stayed in the grid"


def test_the_header_menu_with_the_keyboard(unlocked_page: Page) -> None:
    page = unlocked_page
    folder = page.locator(".ag-header-cell[col-id='folder']")
    tab_to(page, page.locator(".ag-header-cell").first)
    ## along the header to the group Item, down to its columns, along them to folder
    for _ in range(40):
        if folder.evaluate("element => element === document.activeElement"):
            break
        in_group = page.evaluate(
            "() => document.activeElement?.classList.contains('ag-header-group-cell') ?? false"
        )
        page.keyboard.press("ArrowDown" if in_group else "ArrowRight")
    expect(folder).to_be_focused()
    press(page, "Shift+F10")
    manage = menu_entry(page, "Manage folders…")
    expect(manage).to_be_focused()
    press(page, "Enter")
    dialog = page.get_by_role("dialog", name="Folders")
    expect(dialog).to_be_visible()
    expect_focus_inside(page, dialog)
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(folder).to_be_focused()


def expect_focus_inside(page: Page, layer: Locator) -> None:
    inside = layer.evaluate("element => element.contains(document.activeElement)")
    assert inside, "the focus is not in the dialog"


## the buttons on the Vault page that open a dialog, a menu or a popover, with what they open
OPENERS: dict[str, Callable[[Page], Locator]] = {
    "New item": lambda page: page.get_by_role("dialog", name="New item"),
    "Columns": lambda page: page.locator(".MuiPopover-paper"),
    "More actions": lambda page: page.get_by_role("menu"),
    "MCP token": lambda page: page.get_by_role("menu"),
    "Log out": lambda page: page.get_by_role("dialog", name="Log out"),
}


@pytest.mark.parametrize("opener", list(OPENERS))
def test_a_dialog_or_menu_takes_the_focus_and_gives_it_back(
    unlocked_page: Page, opener: str
) -> None:
    page = unlocked_page
    layer_of = OPENERS[opener]
    button = page.get_by_role("button", name=opener, exact=True)
    tab_to(page, button, limit=60)
    press(page, "Enter")
    layer = layer_of(page)
    expect(layer.first).to_be_visible()
    page.wait_for_function(SETTLED)
    expect_focus_inside(page, layer.first)
    expect_focus_ring(page)
    page.keyboard.press("Escape")
    expect(layer.first).to_be_hidden()
    expect(button).to_be_focused()


def test_a_cell_menu_dialog_gives_the_focus_back_to_the_cell(unlocked_page: Page, api: Api) -> None:
    page = unlocked_page
    search_box = page.get_by_role("searchbox", name="Search")
    tab_to(page, search_box)
    page.keyboard.type(KEYBOARD_ITEM)
    settle(page)
    into_grid(page)
    arrow_to_column(page, "name")
    name = focused(page)
    press(page, "Shift+F10")
    tab_to(page, menu_entry(page, "Add field"), "ArrowDown")
    press(page, "Enter")
    dialog = page.get_by_role("dialog", name=f"Add a field to {KEYBOARD_ITEM}")
    expect(dialog).to_be_visible()
    expect_focus_inside(page, dialog)
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(name).to_be_focused()


FOCUS_AFTER_DECISION = """() => {
    const element = document.activeElement
    return element?.closest('[role="tab"][aria-selected="true"]') !== null
        || element?.closest('label')?.textContent === 'Select all'
}"""


def test_approve_and_reject_proposals_with_the_keyboard(
    unlocked_page: Page, api: Api, propose_rename: Propose
) -> None:
    page = unlocked_page
    approved = propose_rename(PROPOSED)
    rejected = propose_rename(REJECTED)
    queue = page.get_by_role("button", name=re.compile(r"^Queue\b"))
    announcer = page.get_by_test_id("announcer")
    ## the Queue button polls every 10 s, the announcer says what came
    expect(announcer).to_have_text(re.compile(r"new MCP proposals? to review"), timeout=15_000)
    tab_to(page, queue)
    press(page, "Enter")
    expect(page.get_by_role("heading", name="Queue", exact=True)).to_be_visible()

    tab_to(page, entry_box(page, rejected))
    press(page, "Space")
    tab_to(page, page.get_by_role("button", name="Reject selected"))
    ## the button disables itself while the reject runs, the focus comes back after it
    page.keyboard.press("Enter")
    expect(entry_box(page, rejected)).to_be_hidden()
    ## the button the focus was on is disabled now, the focus went to Select all
    expect(page.get_by_role("checkbox", name="Select all")).to_be_focused()

    tab_to(page, entry_box(page, approved))
    press(page, "Space")
    tab_to(page, page.get_by_role("button", name="Approve selected"), "Shift+Tab")
    page.keyboard.press("Enter")
    expect(announcer).to_have_text(re.compile(r"The write is done"), timeout=APPROVE_TIMEOUT)
    ## Select all when entries are left (from elsewhere), else the open tab
    page.wait_for_function(FOCUS_AFTER_DECISION)
    expect_focus_ring(page)
    names = rows_by_name(api)
    assert f"{PROPOSED} (a11y)" in names, "the approved rename was not written"
    assert f"{REJECTED} (a11y)" not in names, "the rejected rename was written"


def test_restore_from_the_change_log_with_the_keyboard(unlocked_page: Page, api: Api) -> None:
    page = unlocked_page
    item = rows_by_name(api)[RESTORED]
    target = item_cell(page, item["id"], "login.username")
    search(page, RESTORED)
    target.click()
    page.keyboard.press("Enter")
    page.locator(".ag-cell-inline-editing input").fill("restore-me")
    page.keyboard.press("Enter")
    expect(target).to_have_class(QUEUED)
    page.get_by_role("button", name="Commit now").click()
    api.wait_saved()

    change_log = page.get_by_role("navigation").get_by_role("button", name="Change log")
    tab_to(page, change_log, "Shift+Tab", limit=60)
    press(page, "Enter")
    row_of_item = page.get_by_role("row", name=re.compile(RESTORED))
    restore = row_of_item.get_by_role("button", name="Restore")
    tab_to(page, restore.first, limit=80)
    opener = restore.first.element_handle()
    press(page, "Enter")
    dialog = page.get_by_role("dialog", name="Restore")
    expect(dialog).to_be_visible()
    expect_focus_inside(page, dialog)
    tab_to(page, dialog.get_by_role("button", name="Restore", exact=True))
    page.keyboard.press("Enter")
    expect(dialog).to_be_hidden()
    ## back on the Restore button of the row, once the dialog has gone
    page.wait_for_function(SETTLED)
    page.wait_for_function("opener => opener === document.activeElement", arg=opener)
    expect_focus_ring(page)
    api.wait_saved()
    restored = rows_by_name(api)[RESTORED]
    assert restored.get("cells", {}).get("login.username") != "restore-me", "not restored"
