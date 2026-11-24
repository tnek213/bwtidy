"""The password generator settings: the Settings page, New item, the cell menu (docs/spec.md,
Settings).

The fixture puts the default back and trashes the items of the test, whatever happened, and the
file deletes them for good at its end (file_put_back). Nothing
here prints a value: a revealed one is judged into a bool before the assert.
"""

import re
import string
import time
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import COMMIT_TIMEOUT, Api, commit_new_entries, last_entry_id, rows_by_name
from support.pages import grid, hover_tooltip, item_cell, pick, search

pytestmark = pytest.mark.usefixtures("file_put_back")

NAME = re.compile(r"^Name\b")
PASSPHRASE = {
    "type": "passphrase",
    "words": 4,
    "separator": "-",
    "capitalize": False,
    "includeNumber": False,
}
## the words of bw's list with a hyphen of their own, so a passphrase joined by "-" can hold more
## hyphens than words
HYPHENATED = re.compile(r"drop-down|felt-tip|t-shirt|yo-yo")


def wait_generator(api: Api) -> dict[str, Any]:
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while (doc := api.get("/api/generator"))["saving"]:
        assert time.monotonic() < deadline, "the generator settings were not saved"
        time.sleep(0.5)
    return doc


def trash(api: Api, name: str) -> None:
    item = rows_by_name(api).get(name)
    if item is None:
        return
    after = last_entry_id(api)
    op = {"kind": "trashItem", "itemId": item["id"], "revisionDate": item["revisionDate"]}
    api.post("/api/queue", {"operations": [op]})
    commit_new_entries(api, after)


@pytest.fixture
def made(unlocked_page: Page, api: Api) -> Iterator[list[str]]:
    """The names of the items the test makes. The stored default comes back afterwards, which
    also drops the settings of the trashed items."""
    before = wait_generator(api)["default"]
    names: list[str] = []
    yield names
    api.wait_saved()
    for name in names:
        trash(api, name)
    api.put("/api/generator/default", before)
    wait_generator(api)
    api.wait_saved()


def create_login(page: Page, api: Api, name: str, default_text: str) -> dict[str, Any]:
    """Make a login with Generate a password in the dialog, answer its row once it is written."""
    page.get_by_role("button", name="New item", exact=True).click()
    dialog = page.get_by_role("dialog", name="New item")
    pick(dialog, "Start from", "Login")
    dialog.get_by_label(NAME).fill(name)
    expect(dialog.get_by_role("checkbox", name="Generate a password")).to_be_checked()
    expect(dialog.get_by_test_id("generator-default")).to_have_text(default_text)
    after = last_entry_id(api)
    dialog.get_by_role("button", name="Create", exact=True).click()
    expect(dialog).to_be_hidden()
    commit_new_entries(api, after)
    item = rows_by_name(api)[name]
    search(page, name)
    return item


def reveal_password(page: Page, item_id: str) -> str:
    cell = item_cell(page, item_id, "login.password")
    button = cell.get_by_role("button", name="Reveal login.password")
    expect(button).to_be_visible(timeout=COMMIT_TIMEOUT * 1000)
    button.click()
    expect(cell.get_by_role("button", name="Hide login.password")).to_be_visible()
    return cell.get_by_test_id("secret-value").inner_text()


def open_settings(page: Page) -> Locator:
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("menuitem", name="Settings").click()
    section = page.get_by_role("region", name="Password generator")
    expect(section).to_be_visible()
    return section


def is_passphrase(value: str) -> bool:
    words = HYPHENATED.sub("word", value).split("-")
    return len(words) == 4 and all(w.isalpha() and w.islower() for w in words)


def test_the_default_on_the_settings_page_rules_a_new_login(
    unlocked_page: Page, api: Api, made: list[str]
):
    page = unlocked_page
    section = open_settings(page)
    expect(section.get_by_role("radio", name="Password", exact=True)).to_be_checked()
    expect(section.get_by_role("button", name="Save")).to_be_disabled()
    section.get_by_role("radio", name="Passphrase").click()
    section.get_by_label("Number of words").fill("4")
    section.get_by_label("Word separator").fill("-")
    ## bw answers an example with these options, and a new one on the refresh button
    example = section.get_by_test_id("generator-example")
    expect(example).to_have_text(re.compile(r".+-.+-.+-.+"))
    first = example.inner_text()
    section.get_by_role("button", name="Another example").click()
    expect(example).not_to_have_text(first)
    section.get_by_role("button", name="Save").click()
    expect(section.get_by_role("button", name="Save")).to_be_disabled()
    assert wait_generator(api)["default"] == PASSPHRASE
    api.wait_saved()

    page.get_by_role("button", name="Vault", exact=True).click()
    grid(page)
    name = "E2E generator passphrase"
    made.append(name)
    item = create_login(page, api, name, "4 words")
    fits = is_passphrase(reveal_password(page, item["id"]))
    assert fits, "the generated password is not 4 words joined by a hyphen"


def test_an_items_own_setting_rules_generate_new_value(
    unlocked_page: Page, api: Api, made: list[str]
):
    page = unlocked_page
    name = "E2E generator item"
    made.append(name)
    default = wait_generator(api)["default"]
    default_text = f"{default['length']} characters"
    item = create_login(page, api, name, default_text)
    before = reveal_password(page, item["id"])
    cell = item_cell(page, item["id"], "login.password")
    cell.get_by_role("button", name="Hide login.password").click()

    ## its own setting: 12 characters without special characters
    cell.click(button="right")
    page.get_by_role("menuitem", name="Generator settings for this item").click()
    dialog = page.get_by_role("dialog", name=f"Generator settings for {name}")
    switch = dialog.get_by_role("switch", name="Use the default")
    expect(switch).to_be_checked()
    switch.click()
    dialog.get_by_label("Length").fill("12")
    dialog.get_by_role("checkbox", name="Special").click()
    expect(dialog.get_by_test_id("generator-example")).to_have_text(
        re.compile(r"^[A-Za-z0-9]{12}$")
    )
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_be_hidden()
    wait_generator(api)
    api.wait_saved()
    assert api.get("/api/generator")["items"][item["id"]]["length"] == 12
    ## the grid marks the cell with the item's own setting, and no other cell
    expect(cell).to_have_class(re.compile(r"\bbw-own-generator-cell\b"))
    expect(item_cell(page, item["id"], "name")).not_to_have_class(
        re.compile(r"\bbw-own-generator-cell\b")
    )
    expect(hover_tooltip(page, cell)).to_contain_text("Own generator setting")
    ## the toolbar chip filters the grid to the items with their own setting
    rows = api.get("/api/grid")["rows"]
    own = [row["id"] for row in rows if row.get("ownGenerator")]
    others = [row["id"] for row in rows if not row.get("ownGenerator")]
    assert item["id"] in own
    assert others
    chip = page.get_by_role("button", name=f"Own generator settings ({len(own)})")
    chip.click()
    expect(item_cell(page, item["id"], "name")).to_be_visible()
    expect(item_cell(page, others[0], "name")).to_have_count(0)
    expect(chip).to_have_attribute("aria-pressed", "true")
    chip.click()
    expect(chip).to_have_attribute("aria-pressed", "false")

    ## asks first, shows (generated) until the commit and then has the new value to reveal
    cell.click(button="right")
    page.get_by_role("menuitem", name="Generate new value").click()
    question = page.get_by_role("dialog", name=re.compile(r"^Generate new value for"))
    expect(question).to_contain_text(
        "Replace the value with a generated one? The old value stays in the item's password"
        " history."
    )
    after = last_entry_id(api)
    question.get_by_role("button", name="Generate").click()
    expect(question).to_be_hidden()
    commit_new_entries(api, after)
    entries = [entry for entry in api.get("/api/queue") if entry["id"] > after]
    assert [row["after"] for row in entries[0]["diff"]] == ["(generated)"]
    assert entries[0]["operation"]["kind"] == "generateLeaf"

    value = reveal_password(page, item["id"])
    alphabet = string.ascii_letters + string.digits
    fits = len(value) == 12 and all(c in alphabet for c in value) and value != before
    assert fits, "the new password does not follow the item's setting"

    ## the change log shows the kind in plain words
    page.get_by_role("button", name="Change log").click()
    expect(page.get_by_text("Generate a value").first).to_be_visible()
    logged = [r for r in api.get("/api/change-log") if r["operationKind"] == "generateLeaf"]
    assert logged[0]["itemIds"] == [item["id"]]
    assert logged[0]["columns"] == ["login.password"]

    ## back on the default: the setting of the item is gone
    page.get_by_role("button", name="Vault", exact=True).click()
    grid(page)
    search(page, name)
    item_cell(page, item["id"], "login.password").click(button="right")
    page.get_by_role("menuitem", name="Generator settings for this item").click()
    dialog = page.get_by_role("dialog", name=f"Generator settings for {name}")
    switch = dialog.get_by_role("switch", name="Use the default")
    expect(switch).not_to_be_checked()
    switch.click()
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_be_hidden()
    assert item["id"] not in wait_generator(api)["items"]
    expect(item_cell(page, item["id"], "login.password")).not_to_have_class(
        re.compile(r"\bbw-own-generator-cell\b")
    )
