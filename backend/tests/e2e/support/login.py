"""The dev account's credentials and the flows that open the app.

The account form, the unlock, the Bitwarden web login and the log out.
"""

from playwright.sync_api import Locator, Page, expect

from support.pages import UNLOCK_TIMEOUT

## dev credentials from dev/README.md, never used anywhere else
PASSWORD = "bwtidy-dev-password-1"
EMAIL = "dev@bwtidy.test"
SERVER = "https://vaultwarden"
## the second account of the dev stack, its vault empty but for the tool note
RESTORE_EMAIL = "restore@bwtidy.test"


def open_account(page: Page, email: str = EMAIL, server: str = SERVER) -> None:
    """Fill the account form and Continue, Create when the account is new.

    Waits until the account's unlock or Bitwarden login shows.
    """
    page.get_by_label("Server").fill(server)
    page.get_by_label("Email").fill(email)
    page.get_by_role("button", name="Continue", exact=True).click()
    password = page.get_by_role("textbox", name="Master password", exact=True)
    create = page.get_by_role("dialog", name="Create account")
    expect(password.or_(create)).to_be_visible(timeout=UNLOCK_TIMEOUT)
    if create.is_visible():
        create.get_by_role("button", name="Create", exact=True).click()
        expect(password).to_be_visible(timeout=UNLOCK_TIMEOUT)


def enter_account(page: Page, email: str = EMAIL) -> None:
    """Open the app at the account's unlock or Bitwarden login.

    Through the account form, unless the browser remembers the account (then the app opens
    it by itself).
    """
    page.goto("/")
    form = page.get_by_role("button", name="Continue", exact=True)
    password = page.get_by_role("textbox", name="Master password", exact=True)
    expect(form.or_(password)).to_be_visible(timeout=UNLOCK_TIMEOUT)
    if form.is_visible():
        open_account(page, email)


def log_in(page: Page) -> None:
    """Unlock, or log in to Bitwarden when the account's CLI is not logged in."""
    if page.get_by_label("Server").count():
        web_login(page)
    else:
        unlock(page)


def web_login(page: Page, password: str = PASSWORD) -> None:
    """Fill the Bitwarden login form and press Log in, without waiting for the result."""
    expect(page.get_by_label("Server")).to_have_value(SERVER)
    email = page.get_by_label("Email")
    ## the email is empty until a web login or a log out of Bitwarden saved it
    if not email.input_value():
        email.fill(EMAIL)
    page.get_by_role("textbox", name="Master password", exact=True).fill(password)
    page.get_by_role("button", name="Log in", exact=True).click()


def web_login_done(page: Page) -> bool:
    """Wait for the grid or the web login's Try again, True for the grid."""
    rows = page.locator(".ag-row").first
    failed = page.get_by_role("button", name="Try again")
    expect(rows.or_(failed)).to_be_visible(timeout=UNLOCK_TIMEOUT)
    return rows.is_visible()


LOG_OUT_OF_BITWARDEN = (
    "Also log out of Bitwarden (the next login asks for the new device code again)"
)


def log_out_dialog(page: Page, *, bitwarden: bool = False) -> Locator:
    """Open Log out in the app bar and set its Bitwarden box, without confirming."""
    page.get_by_role("button", name="Log out", exact=True).click()
    dialog = page.get_by_role("dialog", name="Log out")
    box = dialog.get_by_role("checkbox", name=LOG_OUT_OF_BITWARDEN)
    expect(box).not_to_be_checked()
    if bitwarden:
        box.check()
    return dialog


def log_out(page: Page) -> None:
    """Log out of bwtidy only (the box unchecked), without waiting for the unlock page."""
    log_out_dialog(page).get_by_role("button", name="Log out", exact=True).click()


def log_out_of_bitwarden(page: Page) -> None:
    """Log out with the Bitwarden box checked and wait for the Bitwarden login form."""
    log_out_dialog(page, bitwarden=True).get_by_role("button", name="Log out", exact=True).click()
    expect(page.get_by_label("Server")).to_have_value(SERVER, timeout=UNLOCK_TIMEOUT)
    expect(page.get_by_role("button", name="Log in", exact=True)).to_be_visible()


def unlock(page: Page, password: str = PASSWORD) -> None:
    """Unlock from the unlock page, without waiting for the result."""
    page.get_by_role("textbox", name="Master password", exact=True).fill(password)
    page.get_by_role("button", name="Unlock", exact=True).click()
