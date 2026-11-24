"""The display settings of the Settings page (docs/spec.md, Settings): the Change log follows the
date and time format, which the backend keeps, so a reload shows it again, its date fields show and
take the format, and a checkbox hides the tool note in the Vault grid.

The fixture puts the stored settings back. Nothing here touches the vault.
"""

import re
import time
from collections.abc import Iterator

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import Api
from support.pages import grid, pick, search

ISO = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
DOTS = re.compile(r"^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}$")


@pytest.fixture
def format_put_back(unlocked_page: Page, api: Api) -> Iterator[None]:
    """The stored settings back afterwards, and a backup for the change log to show, the one the
    unlock takes in the background or an older one."""
    before = api.get("/api/settings/display")
    deadline = time.monotonic() + 180
    while not api.get("/api/backups"):
        assert time.monotonic() < deadline, "no backup to show"
        time.sleep(1)
    yield
    api.put("/api/settings/display", before)


def choose_format(page: Page, option: re.Pattern[str]) -> None:
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("menuitem", name="Settings").click()
    pick(page, "Date and time format", option)


def wait_stored(api: Api, date_format: str) -> None:
    """The select saves without waiting, so the PUT can still be on its way."""
    deadline = time.monotonic() + 10
    while api.get("/api/settings/display")["dateFormat"] != date_format:
        assert time.monotonic() < deadline, f"{date_format} was not saved"
        time.sleep(0.2)


def first_time(page: Page) -> Locator:
    """The time of the change log's first row, a change or a backup."""
    page.get_by_role("button", name="Change log", exact=True).click()
    log = page.get_by_role("table", name="Change log")
    return log.locator("tbody tr").first.locator("td").first


def test_the_change_log_follows_the_format(unlocked_page: Page, api: Api, format_put_back: None):
    page = unlocked_page
    choose_format(page, re.compile(r"^ISO 8601 "))
    expect(first_time(page)).to_have_text(ISO)
    wait_stored(api, "iso")

    page.reload()
    grid(page)
    expect(first_time(page)).to_have_text(ISO)

    choose_format(page, re.compile(r"^Europe \(dots\) "))
    expect(first_time(page)).to_have_text(DOTS)
    wait_stored(api, "europe-dots")


def date_field(page: Page, label: str) -> Locator:
    """A date field of the change log's filters (MUI X's date picker), shown in the format."""
    page.get_by_role("button", name="Change log", exact=True).click()
    return page.get_by_role("search", name="Change log filters").get_by_role("group", name=label)


def type_day(field: Locator, first: str, digits: str) -> None:
    """Types the digits key by key from the first section on, the field moves on by itself."""
    field.get_by_role("spinbutton", name=first).click()
    field.page.keyboard.type(digits)


def test_the_date_fields_follow_the_format(unlocked_page: Page, api: Api, format_put_back: None):
    page = unlocked_page
    log = page.get_by_role("table", name="Change log")
    choose_format(page, re.compile(r"^ISO 8601 "))
    since = date_field(page, "From")
    expect(since).to_contain_text("YYYY-MM-DD")
    expect(log.locator("tbody tr").first).to_be_visible()

    type_day(since, "Year", "20991231")
    expect(since).to_contain_text("2099-12-31")
    expect(page.get_by_text("No changes match the filters.")).to_be_visible()
    wait_stored(api, "iso")

    choose_format(page, re.compile(r"^Europe \(\d"))
    since = date_field(page, "From")
    expect(since).to_contain_text("DD/MM/YYYY")
    type_day(since, "Day", "16112026")
    expect(since).to_contain_text("16/11/2026")
    wait_stored(api, "europe")


def wait_hidden(api: Api, hidden: bool) -> None:
    """The checkbox saves without waiting, so the PUT can still be on its way."""
    deadline = time.monotonic() + 10
    while api.get("/api/settings/display")["hideToolNote"] != hidden:
        assert time.monotonic() < deadline, f"hideToolNote {hidden} was not saved"
        time.sleep(0.2)


def test_the_checkbox_hides_the_tool_note_in_the_vault_grid(
    unlocked_page: Page, api: Api, format_put_back: None
):
    page = unlocked_page
    format_before = api.get("/api/settings/display")["dateFormat"]
    note_name = page.locator(".ag-cell[col-id='name']").get_by_text("bwtidy", exact=True)
    search(page, "bwtidy")
    expect(note_name).to_have_count(1)

    page.get_by_role("button", name="More actions").click()
    page.get_by_role("menuitem", name="Settings").click()
    box = page.get_by_role("checkbox", name="Hide bwtidy's own note")
    ## the box follows the query cache a moment after the click, which check() does not wait for
    box.click()
    expect(box).to_be_checked()
    wait_hidden(api, True)
    page.get_by_role("button", name="Vault", exact=True).click()
    grid(page)
    search(page, "bwtidy")
    expect(note_name).to_have_count(0)

    page.get_by_role("button", name="More actions").click()
    page.get_by_role("menuitem", name="Settings").click()
    box = page.get_by_role("checkbox", name="Hide bwtidy's own note")
    box.click()
    expect(box).not_to_be_checked()
    wait_hidden(api, False)
    page.get_by_role("button", name="Vault", exact=True).click()
    grid(page)
    search(page, "bwtidy")
    expect(note_name).to_have_count(1)
    assert api.get("/api/settings/display")["dateFormat"] == format_before
