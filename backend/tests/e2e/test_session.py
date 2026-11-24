"""Unlock, Log out with and without the CLI, the version in the menu."""

from playwright.sync_api import Page, expect
from support.api import Api
from support.login import EMAIL, SERVER, log_out, log_out_dialog, unlock, web_login, web_login_done
from support.pages import UNLOCK_TIMEOUT, grid


def test_unlock_lands_in_the_grid(unlocked_page: Page):
    expect(unlocked_page.get_by_role("button", name="Log out", exact=True)).to_be_visible()


def test_wrong_master_password_says_so(unlocked_page: Page):
    page = unlocked_page
    log_out(page)
    unlock(page, "not-the-master-password")
    expect(page.get_by_role("alert")).to_have_text("Wrong master password", timeout=UNLOCK_TIMEOUT)
    expect(page.locator(".ag-row")).to_have_count(0)


def test_log_out_shows_the_unlock_of_the_same_account(unlocked_page: Page):
    page = unlocked_page
    log_out(page)
    expect(page.get_by_role("button", name="Unlock", exact=True)).to_be_visible()
    expect(page.get_by_role("textbox", name="Master password", exact=True)).to_be_visible()
    expect(page.get_by_label("Server")).to_have_count(0)
    expect(page.get_by_text(f"Account {EMAIL} (vaultwarden)", exact=True)).to_be_visible()

    ## the worker exited with the session, the unlock starts the account's next one
    unlock(page)
    expect(page.get_by_text("Starting", exact=True)).to_be_visible()
    grid(page)


def test_log_out_cancel_keeps_the_session(unlocked_page: Page):
    page = unlocked_page
    dialog = log_out_dialog(page)
    expect(dialog).to_contain_text("Log out of bwtidy. Unsaved changes are written first.")
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_have_count(0)
    expect(page.locator(".ag-row").first).to_be_visible()
    page.reload()
    expect(page.locator(".ag-row").first).to_be_visible(timeout=UNLOCK_TIMEOUT)


def test_log_out_with_the_box_checked_shows_the_bitwarden_login(unlocked_page: Page):
    page = unlocked_page
    log_out_dialog(page, bitwarden=True).get_by_role("button", name="Log out", exact=True).click()
    expect(page.get_by_role("button", name="Log in", exact=True)).to_be_visible(
        timeout=UNLOCK_TIMEOUT
    )
    expect(page.get_by_label("Server")).to_have_value(SERVER)
    expect(page.get_by_label("Email")).to_have_value(EMAIL)
    expect(page.get_by_role("button", name="Unlock", exact=True)).to_have_count(0)

    web_login(page)
    assert web_login_done(page), "the web login failed"


def test_about_page_shows_the_version(unlocked_page: Page, api: Api):
    version = api.get("/api/health")["version"]
    assert version
    unlocked_page.get_by_role("button", name="More actions").click()
    expect(unlocked_page.get_by_role("menuitem", name="Log out of Bitwarden")).to_have_count(0)
    unlocked_page.get_by_role("menuitem", name="About bwtidy", exact=True).click()
    expect(unlocked_page.get_by_role("heading", name="About bwtidy")).to_be_visible()
    expect(unlocked_page.get_by_text(f"Version {version}", exact=True)).to_be_visible()
    expect(unlocked_page.get_by_role("link", name="Source code", exact=True)).to_have_attribute(
        "href", "https://github.com/tnek213/bwtidy"
    )
