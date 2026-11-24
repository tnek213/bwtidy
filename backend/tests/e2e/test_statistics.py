"""The Statistics page (docs/spec.md, Statistics).

A datatype on a custom field of one fixture item gets scored by the reload after the save, and
the page shows its row. The fixture puts the templates and the datatypes back.
"""

from collections.abc import Callable, Iterator

import pytest
from playwright.sync_api import Page, expect
from support.api import COMMIT_TIMEOUT, Api
from support.pages import grid
from support.vault_items import save_templates, wait_templates_saved

from tests.support.section_changes import datatype_changes

DATATYPE = {"name": "E2E statistics", "pattern": r"\w{1,40}"}
ITEM = "Server sudo"
COLUMN = "fields.infra"
COORDINATE = {"kind": "field", "name": "infra", "fieldType": 0}


def save_datatypes(api: Api, datatypes: list[dict]) -> list[dict]:
    current = api.get("/api/datatypes")["datatypes"]
    changes = datatype_changes(current, datatypes)
    saved = api.put("/api/datatypes", {"changes": changes})["datatypes"] if changes else current
    api.wait_saved(COMMIT_TIMEOUT)
    return saved


@pytest.fixture
def with_datatype(unlocked_page: Page, api: Api) -> Iterator[Page]:
    """The grid with DATATYPE on a template beside the stored ones."""
    datatypes = api.get("/api/datatypes")["datatypes"]
    templates = wait_templates_saved(api)["templates"]
    saved = save_datatypes(api, [*datatypes, DATATYPE])
    coordinate = {**COORDINATE, "datatype": saved[-1]["id"]}
    save_templates(
        api, [*templates, {"name": "E2E statistics", "type": 2, "coordinates": [coordinate]}]
    )
    unlocked_page.reload()
    grid(unlocked_page)
    yield unlocked_page
    save_templates(api, templates)
    save_datatypes(api, datatypes)


def test_statistics_shows_the_row_of_a_scored_value(
    with_datatype: Page, assert_no_secrets: Callable[[str], None]
):
    page = with_datatype
    page.get_by_role("button", name="Types", exact=True).click()
    page.get_by_role("tab", name="Datatypes").click()
    page.get_by_role("button", name="Pattern limits and statistics").click()

    expect(page.get_by_text("kept in memory for this session only")).to_be_visible()
    limits = page.get_by_role("table", name="Limits")
    expect(limits.get_by_role("row", name="Value length 1000")).to_be_visible()
    datatypes = page.get_by_role("table", name="Datatypes")
    expect(datatypes.get_by_role("row").filter(has_text=DATATYPE["name"])).to_be_visible()

    rows = page.get_by_role("table", name="Scored values").get_by_role("row")
    row = rows.filter(has_text=ITEM).filter(has_text=COLUMN).filter(has_text=DATATYPE["name"])
    expect(row).to_be_visible()
    expect(row.get_by_role("cell").nth(5)).to_contain_text("of 40")

    assert_no_secrets(page.get_by_role("main").inner_text())
