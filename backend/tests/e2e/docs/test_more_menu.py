"""The More actions menu of the app bar and the About page it opens (docs/spec.md, More actions)."""

from playwright.sync_api import Page, expect

from tests.support.docs_shots import shot


def test_more_menu(unlocked_page: Page) -> None:
    page = unlocked_page
    ## the open menu hides the rest of the page from the accessibility tree
    button = page.get_by_role("button", name="More actions", include_hidden=True)
    shot(page, "more-menu-closed", frame="band", around=[button], highlight=[(button, "1")])

    button.click()
    menu = page.get_by_role("menu")
    about = menu.get_by_role("menuitem", name="About bwtidy", exact=True)
    expect(about).to_be_visible()
    ## the grid's rounded corner starts 6 px below the menu, under the menu's shadow its pixels
    ## differ from run to run
    shot(
        page,
        "more-menu-open",
        frame="band",
        around=[button, menu],
        margin=(16, 6),
        highlight=[(about, "2")],
    )

    about.click()
    expect(page.get_by_role("heading", name="About bwtidy")).to_be_visible()
    shot(page, "about-page")
