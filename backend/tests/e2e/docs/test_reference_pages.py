"""The Settings page's Display section and the Log out dialog, for docs/reference/ui.md
(docs/spec.md, Settings, Log out).

Every dialog is closed with Cancel, so the app is as seeded afterwards. The Settings page shows
the time now, so its shot fixes it.
"""

from playwright.sync_api import Page, expect

from tests.support.docs_shots import shot

FIXED_TIME = "2/15/2027, 7:01:30 PM"


def test_display_settings(unlocked_page: Page) -> None:
    page = unlocked_page
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("menuitem", name="Settings").click()
    display = page.get_by_role("region", name="Display")
    expect(display.get_by_role("heading", name="Display")).to_be_visible()
    ## System shows the time now, a fixed one keeps the image the same from run to run
    display.get_by_role("combobox", name="Date and time format").evaluate(
        "(box, time) => { box.textContent = box.textContent.replace(/[(].*[)]/, `(${time})`) }",
        FIXED_TIME,
    )
    shot(page, "reference-settings-display", frame="box", around=[display], margin=8)


def test_log_out_dialog(unlocked_page: Page) -> None:
    page = unlocked_page
    page.get_by_role("button", name="Log out", exact=True).click()
    dialog = page.get_by_role("dialog", name="Log out")
    expect(dialog.get_by_role("checkbox", name="Also log out of Bitwarden")).to_be_visible()
    shot(page, "reference-log-out", frame="box", around=[dialog])
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()
