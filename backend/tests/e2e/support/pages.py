"""Helpers for the grid page and its dialogs: waiting for rows, finding cells, typing, pointing.

Plain functions that take a Playwright page or locator, none of them reads the environment.
"""

import contextlib
import re
import time

from playwright.sync_api import Locator, Page, expect

## an unlock runs the KDF and loads the vault, a web login also runs bw login first
UNLOCK_TIMEOUT = 120_000
TIMEOUT = 30_000


def grid(page: Page) -> None:
    """Wait until the grid shows rows."""
    expect(page.locator(".ag-row").first).to_be_visible(timeout=UNLOCK_TIMEOUT)


def shown_tooltip(page: Page) -> Locator:
    """AG Grid keeps a fading tooltip in the page with ag-tooltip-hiding, so skip it."""
    return page.locator(".ag-tooltip:not(.ag-tooltip-hiding)")


## the centre of the element when it is the element (or its child) there, else scroll it to the
## middle of the grid and answer null
CENTRE_IN_VIEW = """element => {
    const box = element.getBoundingClientRect()
    const x = box.x + box.width / 2
    const y = box.y + box.height / 2
    const there = document.elementFromPoint(x, y)
    if (there && element.contains(there)) {
        return [x, y]
    }
    element.scrollIntoView({ block: 'center', inline: 'center' })
    return null
}"""


def point_at(page: Page, target: Locator) -> None:
    """Move the mouse onto the target's centre as a person does.

    The mouse leaves the grid first, so the target gets a mouseenter of its own. A target out of
    view scrolls to the middle of the grid, one in view (a pinned cell too) does not move. The
    mouse goes to the centre only once the target has held still there for 300 ms, as the rows
    slide after a filter and the grid's scroll areas follow each other a frame later. Playwright's
    hover() scrolls a pinned cell, and waits only two animation frames for the target to hold still.
    """
    page.mouse.move(0, 0)
    deadline = time.monotonic() + 10
    readings: list[list[float] | None] = []
    while len(readings) < 3 or readings[-1] is None or len(set(map(str, readings[-3:]))) > 1:
        assert time.monotonic() < deadline, "the target did not hold still in view"
        readings.append(target.evaluate(CENTRE_IN_VIEW))
        page.wait_for_timeout(150)
    centre = readings[-1]
    page.mouse.move(centre[0], centre[1])


def hover_tooltip(page: Page, target: Locator) -> Locator:
    """Point at the target until AG Grid shows a tooltip, and answer it.

    Every reload of the rows (after a commit, also the auto-commit of a list a commit queued)
    refreshes the cells, and AG Grid hides a shown tooltip then and shows it again only at the
    next mouseenter. A person moves the mouse off and back on, so this points again when no
    tooltip shows. A target without one comes back with none after the tries.
    """
    tooltip = shown_tooltip(page)
    for _ in range(4):
        point_at(page, target)
        with contextlib.suppress(AssertionError):
            ## 500 ms of tooltipShowDelay, with room for a busy laptop
            expect(tooltip).to_be_visible(timeout=3000)
            return tooltip
    return tooltip


def cell(page: Page, item: str, column: str) -> Locator:
    """The cell of an item's column, found through the row of its name cell.

    Fills the search box with the item's name, so the row is the only one or one of few.
    """
    page.get_by_label("Search").fill(item)
    name = page.locator(".ag-cell[col-id='name']").get_by_text(item, exact=True)
    expect(name).to_have_count(1)
    row = name.locator("xpath=ancestor::*[@row-id][1]").get_attribute("row-id")
    return page.locator(f".ag-row[row-id='{row}'] .ag-cell[col-id='{column}']")


def pick(scope: Page | Locator, combobox: str, option: str | re.Pattern[str]) -> None:
    """Open the combobox (a MUI select) in scope and click the option, which is on the page."""
    page = scope if isinstance(scope, Page) else scope.page
    scope.get_by_role("combobox", name=combobox).click()
    page.get_by_role("option", name=option, exact=True).click()


def coordinate_row(editor: Locator, number: int) -> Locator:
    """The template editor's coordinate row number (counted from 1)."""
    return editor.get_by_role("group", name=f"Coordinate row {number}", exact=True)


def add_coordinate(editor: Locator, option: str) -> int:
    """Add a coordinate row to the template editor and pick an option, answer the row's number.

    The option is named like the list shows it: `fields.user, Text`, `login.totp, Built-in,
    hidden` or `New field…`. The new row's options open on their own.
    """
    editor.get_by_role("button", name="Add coordinate").click()
    number = editor.get_by_role("group").count()
    editor.page.get_by_role("option", name=option, exact=True).click()
    return number


def add_new_field(editor: Locator, name: str, field_type: str = "Text") -> int:
    """Add a field no item has to the template editor (New field…), answer the row's number."""
    number = add_coordinate(editor, "New field…")
    editor.get_by_label(f"New field name {number}", exact=True).fill(name)
    if field_type != "Text":
        pick(coordinate_row(editor, number), "Field type", field_type)
    return number


## AG Grid fades a row out after a change of the rows, and a row shown again can be in the
## DOM twice for a moment
ROW = ".ag-row:not(.ag-opacity-zero)"
## how long AG Grid's row animation keeps a leaving row (about 300 ms seen)
ROW_ANIMATION = 400
## what a masked secret cell shows (MASK in frontend/src/shared/values.ts)
MASK = "•" * 8
## a cell with a queued value the vault does not hold yet
QUEUED = re.compile(r"\bbw-queued-cell\b")


def settle(page: Page) -> None:
    """Wait until the rows' animation after a search, filter or sort is over."""
    page.wait_for_timeout(ROW_ANIMATION)
    page.wait_for_function(
        """() => {
            const rows = [...document.querySelectorAll('.ag-row')]
            const ids = rows.map(row => row.getAttribute('row-id'))
            return new Set(ids).size === ids.length
        }"""
    )


def row(page: Page, item_id: str) -> Locator:
    return page.locator(f'{ROW}[row-id="{item_id}"]')


def item_cell(page: Page, item_id: str, column: str) -> Locator:
    """The grid cell of an item by its id in a column, without touching the search box."""
    return row(page, item_id).locator(f'.ag-cell[col-id="{column}"]')


def search(page: Page, text: str) -> None:
    """Type into the grid's search box, so only the rows with the words are rendered."""
    page.get_by_label("Search", exact=True).fill(text)
    settle(page)


def type_into(page: Page, item_id: str, column: str, value: str) -> None:
    """Edit a text cell inline: Enter opens the editor, Enter saves."""
    target = item_cell(page, item_id, column)
    target.click()
    page.keyboard.press("Enter")
    page.locator(".ag-cell-inline-editing input").fill(value)
    page.keyboard.press("Enter")
    expect(target).to_have_class(QUEUED)


def editor(page: Page) -> Locator:
    """An open cell editor or edit dialog, inline or as a popup."""
    return page.locator(".ag-cell-inline-editing, .ag-popup-editor").or_(page.get_by_role("dialog"))
