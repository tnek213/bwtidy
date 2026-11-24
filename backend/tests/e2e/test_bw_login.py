"""The Bitwarden web login: prefill, console, wrong password, cancel.

Every test logs the CLI out through the app first (never bw logout in the container while
the backend runs, see dev/dev-vault.md) and leaves it logged in again. The right password right
after a wrong one is test_bw_login_race.py, logging out of Bitwarden after the session ended
and the email after a reload on a stack without a web login are test_bw_logout.py, Log out
with the Bitwarden box checked and log in again is test_session.py.
"""

import re
from collections.abc import Iterator

import pytest
from playwright.sync_api import Locator, Page, Response, expect
from support.api import Api
from support.login import EMAIL, PASSWORD, SERVER, log_out_of_bitwarden, web_login, web_login_done

WRONG_PASSWORD = "not-the-master-password"
## MASK in backend/src/bwtidy/bw/login.py
BULLETS = "•" * 8
OUTPUT_PATH = "/api/bw-login/output"


@pytest.fixture
def logged_out_page(unlocked_page: Page, api: Api) -> Iterator[Page]:
    """The Bitwarden login form after a log out of Bitwarden, logged in again afterwards."""
    log_out_of_bitwarden(unlocked_page)
    yield unlocked_page
    ## the gateway knows it also when no worker runs (none starts before Log in)
    if api.get("/gateway/account")["loggedIn"]:
        return
    unlocked_page.goto("/")
    web_login(unlocked_page)
    assert web_login_done(unlocked_page), "the web login after the test failed"


@pytest.fixture
def console_text(logged_out_page: Page) -> list[str]:
    """All the console got from the backend, also what the grid replaced before a check."""
    texts: list[str] = []

    def collect(response: Response) -> None:
        if OUTPUT_PATH in response.url and response.ok:
            texts.append(response.json()["text"])

    logged_out_page.on("response", collect)
    return texts


def console(page: Page) -> Locator:
    return page.get_by_role("log", name="Bitwarden CLI output")


def test_the_login_form_prefills_the_server_and_email_after_a_reload(logged_out_page: Page):
    page = logged_out_page
    page.reload()
    expect(page.get_by_label("Server")).to_have_value(SERVER)
    expect(page.get_by_label("Email")).to_have_value(EMAIL)


def test_the_focus_is_in_the_password_field(logged_out_page: Page):
    page = logged_out_page
    page.reload()
    expect(page.get_by_role("textbox", name="Master password", exact=True)).to_be_focused()


def test_log_in_shows_what_bw_prints_in_the_console(logged_out_page: Page, console_text: list[str]):
    page = logged_out_page
    web_login(page)
    expect(console(page)).to_be_visible()
    assert web_login_done(page), "the web login failed"
    printed = "You are logged in!" in "".join(console_text)
    assert printed, "the console did not get what bw login printed"


def test_the_console_shows_no_password_or_session_key(
    logged_out_page: Page, console_text: list[str]
):
    page = logged_out_page
    web_login(page)
    assert web_login_done(page), "the web login failed"
    text = "".join(console_text)
    ## a bool first, so a failure does not show the text with what leaked
    leaked = PASSWORD in text
    assert not leaked, "the master password is in the console"
    sessions = re.findall(r'BW_SESSION="([^"]*)"', text) + re.findall(r"--session (\S+)", text)
    assert sessions, "bw printed no session key line"
    assert all(key == BULLETS for key in sessions), "a session key in the console is not masked"


def test_log_in_lands_in_the_grid_without_the_password_again(logged_out_page: Page):
    page = logged_out_page
    web_login(page)
    assert web_login_done(page), "the web login did not reach the grid"
    expect(page.get_by_role("button", name="Log out", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Unlock", exact=True)).to_have_count(0)


def test_a_wrong_password_keeps_what_bw_said(logged_out_page: Page):
    page = logged_out_page
    web_login(page, WRONG_PASSWORD)
    assert not web_login_done(page), "the wrong password logged in"
    expect(console(page)).to_contain_text("Username or password is incorrect")
    expect(page.get_by_role("alert")).to_contain_text("bw login failed")


def test_try_again_goes_back_to_the_form(logged_out_page: Page):
    page = logged_out_page
    web_login(page, WRONG_PASSWORD)
    assert not web_login_done(page), "the wrong password logged in"
    page.get_by_role("button", name="Try again").click()

    expect(page.get_by_role("button", name="Try again")).to_have_count(0)
    expect(page.get_by_role("button", name="Log in", exact=True)).to_be_enabled()
    expect(page.get_by_label("Server")).to_have_value(SERVER)
    expect(page.get_by_label("Email")).to_have_value(EMAIL)
    expect(page.get_by_role("textbox", name="Master password", exact=True)).to_be_editable()
    expect(page.get_by_role("textbox", name="Master password", exact=True)).to_have_value("")


def cancel_a_login(page: Page) -> None:
    """Press Log in and Cancel while bw login still runs."""
    web_login(page)
    page.get_by_role("button", name="Cancel").click()


def test_cancel_goes_back_to_the_form(logged_out_page: Page, api: Api):
    page = logged_out_page
    cancel_a_login(page)

    expect(page.get_by_role("button", name="Log in", exact=True)).to_be_enabled()
    expect(page.get_by_role("button", name="Cancel")).to_have_count(0)
    expect(page.get_by_role("alert")).to_have_count(0)
    expect(page.locator(".ag-row")).to_have_count(0)
    assert not api.get("/api/session")["bwLogin"]["loggedIn"], "the cancelled login logged in"


def test_log_in_after_a_cancel_lands_in_the_grid(logged_out_page: Page):
    page = logged_out_page
    cancel_a_login(page)
    expect(page.get_by_role("button", name="Log in", exact=True)).to_be_enabled()

    web_login(page)
    assert web_login_done(page), "the web login after a cancel failed"


def test_log_out_of_bitwarden_prefills_the_server_and_email(logged_out_page: Page):
    page = logged_out_page
    expect(page.get_by_label("Server")).to_have_value(SERVER)
    expect(page.get_by_label("Email")).to_have_value(EMAIL)
    expect(page.get_by_text("The Bitwarden CLI is not logged in.")).to_be_visible()
