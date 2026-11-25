"""The Types page of docs/reference/ui.md with a template, its editors and the limits of the
Statistics page (docs/spec.md, Templates, Datatypes, Statistics).

The template and the datatype are added through the API and taken out again at the end, every
editor is closed with Cancel, so the vault is as seeded afterwards. Each save is a queue entry
that stays applied, which the Queue and Change log shots of test_tutorials.py would show, so the
file's name sorts after it. The Statistics page counts the vault's reloads, which differ by the
tests run, so its shot leaves that out.
"""

from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, ViewportSize, expect
from support.api import COMMIT_TIMEOUT, Api
from support.pages import grid
from support.vault_items import save_templates, wait_templates_saved

from tests.support.docs_shots import shot
from tests.support.section_changes import datatype_changes

DATATYPE = {
    "name": "User name",
    "pattern": r"[a-z][a-z0-9_-]{1,31}",
    "description": "A login name of a device's admin page",
    "examples": ["admin", "dev"],
    "counterExamples": ["Admin User"],
}
## matched by Printer admin and Camera NVR, a near miss of Switch admin (its password is lowercase)
TEMPLATE = "Device admin"
WINDOW = ViewportSize(width=1600, height=900)
TALL = ViewportSize(width=1600, height=1100)


def save_datatypes(api: Api, datatypes: list[dict]) -> list[dict]:
    current = api.get("/api/datatypes")["datatypes"]
    changes = datatype_changes(current, datatypes)
    saved = api.put("/api/datatypes", {"changes": changes})["datatypes"] if changes else current
    api.wait_saved(COMMIT_TIMEOUT)
    return saved


@pytest.fixture
def types_page(unlocked_page: Page, api: Api) -> Iterator[Page]:
    """The Types page with TEMPLATE and DATATYPE beside the stored ones."""
    datatypes = api.get("/api/datatypes")["datatypes"]
    templates = wait_templates_saved(api)["templates"]
    saved = save_datatypes(api, [*datatypes, DATATYPE])
    coordinates = [
        {"kind": "field", "name": "username", "fieldType": 0, "datatype": saved[-1]["id"]},
        {"kind": "field", "name": "Password", "fieldType": 1},
    ]
    save_templates(api, [*templates, {"name": TEMPLATE, "type": 2, "coordinates": coordinates}])
    page = unlocked_page
    page.reload()
    grid(page)
    page.get_by_role("button", name="Types", exact=True).click()
    ## the pointer left on the app bar shows a button's tooltip over the tabs
    page.mouse.move(800, 600)
    expect(page.get_by_role("tooltip")).to_have_count(0)
    yield page
    save_templates(api, templates)
    save_datatypes(api, datatypes)


def test_types_pages(types_page: Page) -> None:
    page = types_page
    table = page.get_by_role("table", name="Templates")
    expect(table.get_by_role("row").filter(has_text=TEMPLATE)).to_be_visible()
    tabs = page.get_by_role("tablist")
    shot(page, "reference-templates", frame="box", around=[tabs, table])

    ## the template editor is taller than the docs run's window
    page.set_viewport_size(TALL)
    page.get_by_role("button", name=f"Edit {TEMPLATE}").click()
    editor = page.get_by_role("dialog", name=f"Edit {TEMPLATE}")
    expect(editor.get_by_role("button", name="Save")).to_be_visible()
    shot(page, "reference-template-editor", frame="box", around=[editor])
    editor.get_by_role("button", name="Cancel").click()
    expect(editor).to_be_hidden()
    page.set_viewport_size(WINDOW)

    page.get_by_role("tab", name="Datatypes").click()
    page.get_by_role("button", name=f"Edit {DATATYPE['name']}").click()
    editor = page.get_by_role("dialog", name=f"Edit {DATATYPE['name']}")
    editor.get_by_label("Try", exact=True).fill("root")
    expect(editor.get_by_role("button", name="Save")).to_be_visible()
    shot(page, "reference-datatype-editor", frame="box", around=[editor])
    editor.get_by_role("button", name="Cancel").click()
    expect(editor).to_be_hidden()

    page.get_by_role("button", name="Pattern limits and statistics").click()
    limits = page.get_by_role("table", name="Limits")
    expect(limits.get_by_role("row", name="Value length 1000")).to_be_visible()
    ## the text under the page's heading counts the vault's reloads, which differ by the tests run
    shot(page, "reference-statistics-limits", frame="box", around=[limits])
