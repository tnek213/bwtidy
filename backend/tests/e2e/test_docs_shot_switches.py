"""A docs shot scrolls every switch back to its start (UNSCROLL_SWITCHES in
tests/support/docs_shots.py). A click that Playwright retries scrolls its target into view with a
forced alignment, and a switch's root hides the overflow of its input, three times as wide as the
switch, so the root scrolls and the track and thumb sit to the left. Nothing here touches the
vault.
"""

from playwright.sync_api import Page

from tests.support.docs_shots import UNSCROLL_SWITCHES

SCROLL_LEFT = "root => root.scrollLeft"


def test_a_shot_scrolls_a_switch_back_that_a_retried_click_scrolled(unlocked_page: Page) -> None:
    page = unlocked_page
    switch = page.get_by_label("Hide empty columns")
    root = switch.locator("xpath=ancestor::*[contains(@class, 'MuiSwitch-root')][1]")
    ## the alignment of Playwright's first retry of a click
    switch.evaluate("input => input.scrollIntoView({block: 'end', inline: 'end'})")
    assert root.evaluate(SCROLL_LEFT) > 0
    page.evaluate(UNSCROLL_SWITCHES)
    assert root.evaluate(SCROLL_LEFT) == 0
