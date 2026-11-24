"""A wrong master password, then the right one, in the web login.

Every cycle must reach the grid: a bw serve restarted while bw login runs would write the
logged out state back and fail the right password.
"""

from playwright.sync_api import Page
from support.login import log_out_of_bitwarden, web_login, web_login_done

CYCLES = 5
WRONG_PASSWORD = "not-the-master-password"


def test_the_right_password_after_a_wrong_one_always_lands_in_the_grid(unlocked_page: Page):
    page = unlocked_page
    for cycle in range(1, CYCLES + 1):
        log_out_of_bitwarden(page)
        web_login(page, WRONG_PASSWORD)
        assert not web_login_done(page), f"cycle {cycle}: the wrong password logged in"
        page.get_by_role("button", name="Try again").click()
        web_login(page)
        assert web_login_done(page), f"cycle {cycle}: the right password did not reach the grid"
