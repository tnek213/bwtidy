"""Password managers fill only the login pages (docs/spec.md, Password managers).

Inside the app every input that takes typed text carries the opt-out attributes of Bitwarden,
1Password, LastPass and Dashlane plus autocomplete off, and none is a password input: a secret is
typed into a text input masked with CSS. The account form and the unlock keep inviting a password
manager. Checkboxes and switches take no typed text, so the check leaves them out.

Nothing here writes to the vault: every editor and dialog is cancelled.
"""

import re
from typing import Any

from playwright.sync_api import Locator, Page, expect
from support.api import Api, rows_by_name
from support.login import log_in, open_account
from support.pages import grid, item_cell, pick, search

OPT_OUT = {
    "autocomplete": "off",
    "data-bwignore": "true",
    "data-1p-ignore": "true",
    "data-lpignore": "true",
    "data-form-type": "other",
}

## every rendered input and textarea under the root, described by its label and attributes only
## (never its value, which can be a secret). MUI's multiline field keeps a second textarea with
## visibility hidden to measure its height, nobody types into it.
DESCRIBE = """root => [...root.querySelectorAll('input, textarea')]
  .filter(e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden')
  .filter(e => !['checkbox', 'radio', 'hidden'].includes(e.type))
  .map(e => ({
    what: e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].textContent)
      || e.getAttribute('name') || e.className,
    type: e.getAttribute('type'),
    attributes: Object.fromEntries(
      ['autocomplete', 'data-bwignore', 'data-1p-ignore', 'data-lpignore', 'data-form-type']
        .map(name => [name, e.getAttribute(name)])),
  }))"""


def inputs(scope: Locator) -> list[dict[str, Any]]:
    return scope.evaluate(DESCRIBE)


def assert_opted_out(scope: Locator, where: str, at_least: int = 1) -> None:
    """Every text input under scope opts out of password managers and none is a password input."""
    found = inputs(scope)
    assert len(found) >= at_least, f"{where}: expected at least {at_least} inputs, found {found}"
    offenders = [i for i in found if i["type"] == "password" or i["attributes"] != OPT_OUT]
    assert not offenders, f"{where}: inputs open to password managers: {offenders}"


def main(page: Page) -> Locator:
    return page.locator("body")


def open_settings(page: Page) -> None:
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("menuitem", name="Settings").click()
    expect(page.get_by_role("combobox", name="Layout")).to_be_visible()


def test_the_login_pages_invite_password_managers(page: Page):
    page.goto("/")
    email = page.get_by_label("Email")
    expect(email).to_be_visible()
    expect(email).to_have_attribute("autocomplete", "username")
    expect(email).not_to_have_attribute("data-bwignore", re.compile(".*"))

    open_account(page)
    password = page.get_by_role("textbox", name="Master password", exact=True)
    expect(password).to_have_attribute("type", "password")
    expect(password).to_have_attribute("autocomplete", "current-password")
    expect(password).not_to_have_attribute("data-bwignore", re.compile(".*"))
    log_in(page)
    grid(page)


def test_every_page_keeps_password_managers_out(unlocked_page: Page):
    page = unlocked_page
    assert_opted_out(main(page), "Vault")

    page.get_by_role("button", name="Types", exact=True).click()
    page.get_by_role("tab", name="Templates").click()
    new_template = page.get_by_role("button", name="New template", exact=True)
    expect(new_template).to_be_visible()
    assert_opted_out(main(page), "Templates", at_least=0)
    new_template.click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    assert_opted_out(dialog, "New template")
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)

    page.get_by_role("tab", name="Datatypes").click()
    page.get_by_role("button", name="New datatype", exact=True).click()
    editor = page.get_by_role("button", name="Save", exact=True).locator("xpath=ancestor::form[1]")
    expect(editor).to_be_visible()
    assert_opted_out(editor, "New datatype")
    page.keyboard.press("Escape")

    page.get_by_role("button", name="Types", exact=True).click()
    page.get_by_role("tab", name="Datatypes").click()
    page.get_by_role("button", name="Pattern limits and statistics").click()
    expect(page.get_by_role("table", name="Limits")).to_be_visible()
    assert_opted_out(main(page), "Statistics", at_least=0)

    page.get_by_role("button", name=re.compile("^Queue")).click()
    expect(page.get_by_role("form", name="Auto-commit")).to_be_visible()
    assert_opted_out(main(page), "Queue", at_least=2)

    page.get_by_role("button", name="Change log", exact=True).click()
    expect(page.get_by_role("heading", name="Change log", exact=True)).to_be_visible()
    assert_opted_out(main(page), "Change log", at_least=2)

    open_settings(page)
    assert_opted_out(main(page), "Settings", at_least=2)


def test_the_dialogs_and_editors_that_take_values_keep_password_managers_out(
    unlocked_page: Page, api: Api
):
    page = unlocked_page
    item_id = rows_by_name(api)["Airline"]["id"]
    search(page, "Airline")

    ## a cell being edited inline: AG Grid's own text editor
    item_cell(page, item_id, "login.username").click()
    page.keyboard.press("Enter")
    inline = page.locator(".ag-cell-inline-editing")
    expect(inline.locator("input")).to_be_visible()
    assert_opted_out(inline, "inline cell editor")
    page.keyboard.press("Escape")
    expect(inline).to_have_count(0)

    ## AG Grid's large text editor, a popup
    item_cell(page, item_id, "notes").click()
    page.keyboard.press("Enter")
    popup = page.locator(".ag-popup-editor")
    expect(popup.locator("textarea")).to_be_visible()
    assert_opted_out(popup, "notes editor")
    page.keyboard.press("Escape")
    expect(popup).to_have_count(0)

    ## the secret dialog of a login's password cell, hidden and shown
    item_cell(page, item_id, "login.password").dblclick()
    dialog = page.get_by_role("dialog")
    value = dialog.get_by_label("New value")
    expect(value).to_have_css("-webkit-text-security", "disc")
    assert_opted_out(dialog, "secret dialog")
    dialog.get_by_role("checkbox", name="Show what I type").check()
    expect(value).to_have_css("-webkit-text-security", "none")
    assert_opted_out(dialog, "secret dialog, shown")
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_have_count(0)

    ## Add field with a hidden value
    item_cell(page, item_id, "name").click(button="right")
    page.get_by_role("menuitem", name="Add field", exact=True).click()
    dialog = page.get_by_role("dialog")
    pick(dialog, "Type", "Hidden")
    expect(dialog.get_by_label("Value")).to_have_css("-webkit-text-security", "disc")
    assert_opted_out(dialog, "Add field", at_least=2)
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_have_count(0)

    ## New item with secret fields: a raw login with its password typed
    page.get_by_role("button", name="New item", exact=True).click()
    dialog = page.get_by_role("dialog", name="New item")
    pick(dialog, "Start from", "Login")
    dialog.get_by_role("checkbox", name="Generate a password").uncheck()
    expect(dialog.get_by_label(re.compile(r"^login\.password"))).to_have_css(
        "-webkit-text-security", "disc"
    )
    assert_opted_out(dialog, "New item", at_least=4)
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_have_count(0)
