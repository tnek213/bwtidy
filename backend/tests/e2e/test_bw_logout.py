"""Log out with the Bitwarden box checked after the session ended.

Only a session logs the CLI out, so the page asks for the unlock first.
"""

from playwright.sync_api import Page, expect
from support.login import (
    EMAIL,
    SERVER,
    log_out_dialog,
    log_out_of_bitwarden,
    unlock,
    web_login,
    web_login_done,
)
from support.pages import UNLOCK_TIMEOUT, grid

## ends the session from the page: the page still shows the grid, its cookie is stale
END_SESSION = """async () => {
    const response = await fetch('/api/session', {method: 'DELETE', credentials: 'same-origin'})
    return response.status
}"""

UNLOCK_FIRST = "Unlock first to log out of Bitwarden"


def test_log_out_of_bitwarden_after_the_session_ended(unlocked_page: Page):
    page = unlocked_page
    dialog = log_out_dialog(page, bitwarden=True)
    expect(dialog).to_be_visible()

    assert page.evaluate(END_SESSION) == 204
    dialog.get_by_role("button", name="Log out", exact=True).click()

    ## the CLI stays logged in: the unlock, not the Bitwarden login
    expect(page.get_by_text(UNLOCK_FIRST)).to_be_visible(timeout=UNLOCK_TIMEOUT)
    expect(page.get_by_role("button", name="Unlock", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Log in", exact=True)).to_have_count(0)

    unlock(page)
    grid(page)
    expect(page.get_by_text(UNLOCK_FIRST)).to_have_count(0)
    log_out_of_bitwarden(page)
    expect(page.get_by_label("Email")).to_have_value(EMAIL)

    ## the account's worker shows its own server and email, also after a reload
    page.reload()
    expect(page.get_by_label("Server")).to_have_value(SERVER)
    expect(page.get_by_label("Email")).to_have_value(EMAIL)

    web_login(page)
    assert web_login_done(page)
    expect(page.get_by_role("button", name="Log out", exact=True)).to_be_visible()
