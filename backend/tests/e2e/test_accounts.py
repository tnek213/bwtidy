"""The account form in front of the gateway (docs/spec.md, Session and access and Gateway).

The restore account of the dev stack is the new account here: its directory is removed
before the module, so it is new also on a run with --no-reset. Its vault holds nothing of the
dev fixture.
"""

import subprocess
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect
from support.accounts import ACCOUNT_DIR, account_dir_exists
from support.api import Api, rows_by_name
from support.elsewhere import DEV_VAULT
from support.login import (
    EMAIL,
    RESTORE_EMAIL,
    SERVER,
    enter_account,
    log_in,
    log_out,
    open_account,
    unlock,
    web_login,
    web_login_done,
)
from support.pages import TIMEOUT, UNLOCK_TIMEOUT, grid

WRONG_PASSWORD = "not-the-master-password"


@pytest.fixture(scope="module", autouse=True)
def restore_account_is_new() -> None:
    """No directory for the restore account: no test before this module used it."""
    path = ACCOUNT_DIR.format(email=RESTORE_EMAIL)
    subprocess.run(
        [str(DEV_VAULT), "exec", "--root", "rm", "-rf", path],
        stdin=subprocess.DEVNULL,
        check=True,
        timeout=60,
    )


@pytest.fixture
def new_context(browser: Browser, base_url: str) -> Iterator[Any]:
    """Makes more browser contexts (other browser profiles), closed after the test."""
    contexts: list[BrowserContext] = []

    def make(**options: Any) -> BrowserContext:
        context = browser.new_context(
            base_url=base_url, viewport={"width": 1600, "height": 900}, **options
        )
        context.set_default_timeout(TIMEOUT)
        contexts.append(context)
        return context

    yield make
    for context in contexts:
        context.close()


def fill_form(page: Page, email: str) -> None:
    page.get_by_label("Server").fill(SERVER)
    page.get_by_label("Email").fill(email)
    page.get_by_role("button", name="Continue", exact=True).click()


def create_dialog(page: Page, email: str):
    dialog = page.get_by_role("dialog", name="Create account")
    expect(dialog).to_contain_text(
        f"There is no bwtidy account for {email} on {SERVER} yet. Create it? "
        "It is kept only if the Bitwarden login succeeds."
    )
    return dialog


def names(page: Page) -> set[str]:
    return set(rows_by_name(Api(page.request)))


STARTING = "Starting"
FIRST_RUN_NOTICE = "Before you start"


def test_a_wrong_master_password_leaves_no_account_behind(page: Page):
    ## an email no run used before, so the account is new on every run
    email = f"nobody-{uuid.uuid4().hex[:12]}@bwtidy.test"
    page.goto("/")
    fill_form(page, email)
    create_dialog(page, email).get_by_role("button", name="Create", exact=True).click()

    expect(page.get_by_label("Email")).to_have_value(email, timeout=UNLOCK_TIMEOUT)
    ## opening the new account started nothing: no worker, no directory before Log in
    state = Api(page.request).get("/gateway/account")
    assert (state["running"], state["exists"], state["loggedIn"]) == (False, False, False)
    ## the dev account logged in before the first test (quick_auto_commit), so no notice for a
    ## first start (docs/spec.md, The account form) on any login page
    assert state["firstRun"] is False
    expect(page.get_by_role("note", name=FIRST_RUN_NOTICE)).to_have_count(0)
    assert not account_dir_exists(email), "the account form made the directory"
    web_login(page, WRONG_PASSWORD)
    expect(page.get_by_text(STARTING, exact=True)).to_be_visible()
    assert not web_login_done(page), "the wrong password logged in"
    assert account_dir_exists(email), "the first Log in made no directory"

    page.get_by_role("button", name="Switch account", exact=True).click()
    expect(page.get_by_role("button", name="Continue", exact=True)).to_be_visible()
    expect(page.get_by_role("note", name=FIRST_RUN_NOTICE)).to_have_count(0)
    fill_form(page, email)
    dialog = create_dialog(page, email)
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(dialog).to_have_count(0)
    expect(page.get_by_role("button", name="Continue", exact=True)).to_be_enabled()


def test_a_new_account_is_offered_and_created(page: Page):
    page.goto("/")
    expect(page.get_by_label("Server")).to_have_value("https://vault.bitwarden.com")
    fill_form(page, RESTORE_EMAIL)
    create_dialog(page, RESTORE_EMAIL).get_by_role("button", name="Create", exact=True).click()

    expect(page.get_by_label("Email")).to_have_value(RESTORE_EMAIL, timeout=UNLOCK_TIMEOUT)
    expect(page.get_by_label("Email")).not_to_be_editable()
    expect(page.get_by_label("Server")).not_to_be_editable()
    web_login(page)
    assert web_login_done(page), "the web login of the new account failed"
    expect(page.get_by_role("banner")).to_contain_text(f"{RESTORE_EMAIL} (vaultwarden)")

    ## kept: the form opens it without offering to create it
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("menuitem", name="Switch account", exact=True).click()
    fill_form(page, RESTORE_EMAIL)
    expect(page.get_by_role("button", name="Unlock", exact=True)).to_be_visible(
        timeout=UNLOCK_TIMEOUT
    )
    expect(page.get_by_role("dialog", name="Create account")).to_have_count(0)


def second_tab(page: Page) -> Page:
    """Another tab of the same browser profile, showing the grid like the first."""
    tab = page.context.new_page()
    tab.goto("/")
    grid(tab)
    return tab


def mark(page: Page) -> None:
    page.evaluate("window.bwtidyNotReloaded = true")


def reloaded(page: Page) -> bool:
    return page.evaluate("window.bwtidyNotReloaded === undefined")


def test_other_tabs_reload_when_the_account_changes(unlocked_page: Page):
    first = unlocked_page
    other = second_tab(first)
    mark(other)

    first.get_by_role("button", name="More actions").click()
    first.get_by_role("menuitem", name="Switch account", exact=True).click()
    expect(first.get_by_role("button", name="Continue", exact=True)).to_be_visible(
        timeout=UNLOCK_TIMEOUT
    )
    expect(other.get_by_role("button", name="Continue", exact=True)).to_be_visible(
        timeout=UNLOCK_TIMEOUT
    )
    assert reloaded(other), "the other tab kept its page"

    ## the restore account was created and logged in by the test before
    mark(other)
    open_account(first, RESTORE_EMAIL)
    expect(other.get_by_text(f"Account {RESTORE_EMAIL} (vaultwarden)", exact=True)).to_be_visible(
        timeout=UNLOCK_TIMEOUT
    )
    expect(other.get_by_role("button", name="Unlock", exact=True)).to_be_visible()
    assert reloaded(other), "the other tab kept its page"

    ## nothing changes any more, so neither tab reloads again
    mark(first)
    mark(other)
    first.wait_for_timeout(3000)
    assert not reloaded(first), "the first tab reloaded by itself"
    assert not reloaded(other), "the other tab reloaded again"


def test_log_out_in_one_tab_reloads_the_others(unlocked_page: Page):
    first = unlocked_page
    other = second_tab(first)
    mark(other)

    log_out(first)
    expect(first.get_by_role("button", name="Unlock", exact=True)).to_be_visible(
        timeout=UNLOCK_TIMEOUT
    )
    expect(other.get_by_role("button", name="Unlock", exact=True)).to_be_visible(
        timeout=UNLOCK_TIMEOUT
    )
    expect(other.get_by_text(f"Account {EMAIL} (vaultwarden)", exact=True)).to_be_visible()
    assert reloaded(other), "the other tab kept its page"

    mark(first)
    mark(other)
    first.wait_for_timeout(3000)
    assert not reloaded(first), "the first tab reloaded by itself"
    assert not reloaded(other), "the other tab reloaded again"
    ## the account still opens in either tab
    unlock(other)
    grid(other)


def test_two_accounts_each_see_only_their_own_items(unlocked_page: Page, new_context):
    dev = unlocked_page
    restore = new_context().new_page()
    enter_account(restore, RESTORE_EMAIL)
    log_in(restore)
    grid(restore)

    expect(dev.get_by_role("banner")).to_contain_text(f"{EMAIL} (vaultwarden)")
    expect(restore.get_by_role("banner")).to_contain_text(f"{RESTORE_EMAIL} (vaultwarden)")
    dev_names, restore_names = names(dev), names(restore)
    assert {"Bank account", "Home insurance"} <= dev_names
    assert not (dev_names - {"bwtidy"}) & restore_names, "the restore account sees dev items"
    ## the dev page still reaches its own worker after the other account logged in
    assert names(dev) == dev_names


def test_new_item_offers_an_organization_without_collections_as_disabled(
    unlocked_page: Page, new_context
):
    ## setup gives the restore account Restore, Family and Club, none with a collection
    restore = new_context().new_page()
    enter_account(restore, RESTORE_EMAIL)
    log_in(restore)
    grid(restore)

    restore.get_by_role("button", name="New item", exact=True).click()
    dialog = restore.get_by_role("dialog", name="New item")
    dialog.get_by_role("combobox", name="Organization").click()
    option = restore.get_by_role(
        "option", name="Restore has no collections, create one in Bitwarden first", exact=True
    )
    expect(option).to_be_visible()
    expect(option).to_have_attribute("aria-disabled", "true")
    dialog.page.keyboard.press("Escape")


def test_switch_account_shows_the_form(unlocked_page: Page):
    page = unlocked_page
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("menuitem", name="Switch account", exact=True).click()

    expect(page.get_by_role("button", name="Continue", exact=True)).to_be_visible(
        timeout=UNLOCK_TIMEOUT
    )
    expect(page.get_by_label("Server")).to_have_value(SERVER)
    expect(page.get_by_label("Email")).to_have_value(EMAIL)
    ## the account is forgotten, a reload does not open it again
    page.reload()
    expect(page.get_by_role("button", name="Continue", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Unlock", exact=True)).to_have_count(0)


def test_a_returning_user_types_only_the_master_password(unlocked_page: Page, new_context):
    ## the browser was closed: its localStorage stays, the cookies (no Max-Age) are gone
    state = unlocked_page.context.storage_state()
    page = new_context(storage_state={"cookies": [], "origins": state["origins"]}).new_page()
    page.goto("/")

    expect(page.get_by_role("button", name="Unlock", exact=True)).to_be_visible(
        timeout=UNLOCK_TIMEOUT
    )
    expect(page.get_by_role("button", name="Continue", exact=True)).to_have_count(0)
    expect(page.get_by_text(f"Account {EMAIL} (vaultwarden)", exact=True)).to_be_visible()
    unlock(page)
    grid(page)
