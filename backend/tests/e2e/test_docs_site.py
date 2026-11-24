"""The documentation site at /docs/ of the gateway, without a login (docs/spec.md, Gateway)."""

import re

from playwright.sync_api import Page, Response, expect

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
