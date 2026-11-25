"""The documentation site at /docs/ of the gateway, without a login (docs/spec.md, Gateway)."""

import re
from urllib.parse import urlsplit

from playwright.sync_api import Page, Request, Response, expect

## the header's background is transparent unless the site's stylesheet loaded
TRANSPARENT = "rgba(0, 0, 0, 0)"


def test_the_docs_home_shows_without_a_login(page: Page):
    page.goto("/docs/")
    expect(page.get_by_role("heading", name="bwtidy documentation", level=1)).to_be_visible()
    expect(page).to_have_title("Home - bwtidy")
    assert page.context.cookies() == []


def test_docs_without_the_slash_lands_on_the_home(page: Page):
    page.goto("/docs")
    expect(page).to_have_url(page.url.split("/docs")[0] + "/docs/")
    expect(page.get_by_role("heading", name="bwtidy documentation", level=1)).to_be_visible()


def test_a_nested_page_loads_with_its_styles(page: Page):
    stylesheets: list[Response] = []
    page.on("response", lambda r: stylesheets.append(r) if r.url.endswith(".css") else None)
    page.goto("/docs/how-to/mcp/")
    expect(page.get_by_role("heading", name="Connect an MCP client", level=1)).to_be_visible()
    assert stylesheets
    assert all(r.ok and "/docs/assets/" in r.url for r in stylesheets)
    header = page.locator(".md-header")
    expect(header).not_to_have_css("background-color", TRANSPARENT)
    images = page.locator("article img")
    for index in range(images.count()):
        assert images.nth(index).evaluate("img => img.complete && img.naturalWidth > 0")


def test_the_color_scheme_follows_the_system_and_toggles(page: Page):
    page.emulate_media(color_scheme="dark")
    page.goto("/docs/")
    body = page.locator("body")
    expect(body).to_have_attribute("data-md-color-scheme", "slate")
    page.get_by_title("Switch to light mode").click()
    expect(body).to_have_attribute("data-md-color-scheme", "default")
    page.get_by_title("Switch to dark mode").click()
    expect(body).to_have_attribute("data-md-color-scheme", "slate")


def test_search_finds_pages(page: Page):
    page.goto("/docs/")
    page.keyboard.press("/")
    page.get_by_placeholder("Search").fill("backup")
    expect(page.get_by_text(re.compile(r"^\d+ results$"))).to_be_visible()
    expect(page.get_by_role("link", name="Restoring a vault from a bwtidy backup")).to_be_visible()


## the theme's own content column is about 810 px wide in a 1600 px window
THEME_COLUMN = 810


def test_the_content_column_is_wide_and_keeps_inline_code_on_one_line(page: Page):
    page.goto("/docs/reference/configuration/")
    width = page.locator(".md-content").evaluate("e => e.getBoundingClientRect().width")
    assert width > THEME_COLUMN + 150
    codes = page.locator("article :not(pre) > code")
    assert codes.count() > 10
    split = codes.evaluate_all(
        "codes => codes.filter(c => c.getClientRects().length > 1).map(c => c.textContent)"
    )
    assert split == []
    assert page.locator("article td code", has_text="bwtidy-backups").count() > 0


def test_running_text_wraps_at_a_readable_length_while_pictures_use_the_column(page: Page):
    page.goto("/docs/tutorial/first-cleanup/")
    column = page.locator("article").evaluate("e => e.getBoundingClientRect().width")
    ## the longest line a paragraph may hold, measured in the paragraph's own font
    limit = page.locator("article p").first.evaluate(
        "p => { const s = document.createElement('span'); s.textContent = '0'.repeat(80);"
        " p.appendChild(s); const w = s.getBoundingClientRect().width; s.remove(); return w }"
    )
    text = page.locator("article p:not(:has(img))")
    assert text.count() > 3
    widest = max(text.evaluate_all("ps => ps.map(p => p.getBoundingClientRect().width)"))
    assert widest <= limit * 1.01
    assert widest < column - 150
    picture = page.locator("article p:has(img)").first
    assert picture.evaluate("p => p.getBoundingClientRect().width") > column - 50
    page.goto("/docs/reference/configuration/")
    table = page.locator("article table").first
    assert table.evaluate("t => t.getBoundingClientRect().width") > limit + 100


def test_the_disclaimer_banner_is_one_slim_line(page: Page):
    page.goto("/docs/reference/configuration/")
    text = page.locator("[data-disclaimer]")
    expect(text).to_be_visible()
    height = text.evaluate("p => p.getBoundingClientRect().height")
    line = text.evaluate("p => parseFloat(getComputedStyle(p).lineHeight)")
    assert height < line * 1.5
    assert page.locator(".md-banner").evaluate("b => b.getBoundingClientRect().height") < 30


def test_a_picture_shown_smaller_opens_larger_from_the_site_itself(page: Page):
    ## a running bwtidy serves the site offline, so its scripts, styles and pictures are its own
    hosts: set[str] = set()

    def note(request: Request) -> None:
        if request.resource_type in ("script", "stylesheet", "image", "font"):
            hosts.add(urlsplit(request.url).netloc)

    page.on("request", note)
    page.goto("/docs/tutorial/first-cleanup/")
    picture = page.locator("article img[data-zoom]").first
    expect(picture).to_have_attribute("title", "Show larger")
    shown = picture.evaluate("i => i.clientWidth")
    dialog = page.locator("dialog[data-zoom]")
    picture.click()
    expect(dialog).to_be_visible()
    large = dialog.locator("img")
    assert large.evaluate("i => i.clientWidth") > shown
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    picture.focus()
    page.keyboard.press("Enter")
    expect(dialog).to_be_visible()
    large.click()
    expect(dialog).to_be_hidden()
    assert hosts == {urlsplit(page.url).netloc}


def test_a_screenshot_has_a_border_and_a_shadow(page: Page):
    page.emulate_media(color_scheme="light")
    page.goto("/docs/tutorial/first-cleanup/")
    picture = page.locator("article img[data-zoom]").first
    style = picture.evaluate(
        "i => { const s = getComputedStyle(i);"
        " return [s.borderTopWidth, s.borderTopStyle, s.boxShadow] }"
    )
    assert style[0] == "1px"
    assert style[1] == "solid"
    assert style[2] != "none"
    page.emulate_media(color_scheme="dark")
    page.goto("/docs/tutorial/first-cleanup/")
    picture = page.locator("article img[data-zoom]").first
    border = picture.evaluate("i => getComputedStyle(i).borderTopColor")
    assert border != TRANSPARENT
    assert picture.evaluate("i => getComputedStyle(i).borderTopWidth") == "1px"
