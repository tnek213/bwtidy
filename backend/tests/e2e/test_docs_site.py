"""The documentation site at /docs/ of the gateway, without a login (docs/spec.md, Gateway)."""

import re
import tomllib
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Locator, Page, Request, Response, expect

from bwtidy.version import semver

ROOT = Path(__file__).resolve().parents[3]
VERSION = semver(
    tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text())["project"]["version"]
)
PAGES_HOST = "tnek213.github.io"
## the pages to check: the address under /docs/, the source file and the address on GitHub Pages
IMPROVE_PAGES = [
    ("/docs/", "docs/index.md", f"https://{PAGES_HOST}/bwtidy/"),
    (
        "/docs/tutorial/first-cleanup/",
        "docs/tutorial/first-cleanup.md",
        f"https://{PAGES_HOST}/bwtidy/tutorial/first-cleanup/",
    ),
]

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
    nav = page.get_by_role("navigation", name="How-to guides")
    expect(nav.get_by_role("link", name="Restore a vault from a backup")).to_be_visible()


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
    ## the longest line a paragraph may hold (72 characters), measured in the paragraph's own font
    ## without its letter spacing, as the unit ch is the width of a 0 alone
    limit = page.locator("article p").first.evaluate(
        "p => { const s = document.createElement('span'); s.textContent = '0'.repeat(72);"
        " s.style.letterSpacing = 'normal'; p.appendChild(s);"
        " const w = s.getBoundingClientRect().width; s.remove(); return w }"
    )
    text = page.locator("article p:not(:has(img))")
    assert text.count() > 3
    widest = max(text.evaluate_all("ps => ps.map(p => p.getBoundingClientRect().width)"))
    assert limit * 0.97 <= widest <= limit * 1.01
    assert widest < column - 150
    headings = page.locator("article h2")
    assert headings.count() > 0
    assert max(headings.evaluate_all("hs => hs.map(h => h.getBoundingClientRect().width)")) <= (
        limit * 1.01
    )
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


def test_the_disclaimer_banner_links_to_the_disclaimer_on_the_home(page: Page):
    page.goto("/docs/reference/configuration/")
    banner = page.locator("[data-disclaimer]")
    expect(banner).to_contain_text("bwtidy is beta software made with AI")
    banner.get_by_role("link", name="Read the disclaimer before you use it.").click()
    expect(page).to_have_url(re.compile(r"/docs/#beta-software$"))
    expect(page.locator("h2#beta-software")).to_contain_text("Beta software")
    box = page.locator("article .admonition.warning")
    expect(box).to_contain_text("Made with AI.")
    expect(page.locator("h2#beta-software + .admonition.warning")).to_have_count(1)


def test_the_disclaimer_banner_text_is_centered(page: Page):
    page.set_viewport_size({"width": 1600, "height": 900})
    page.goto("/docs/reference/configuration/")
    text = page.locator("[data-disclaimer]")
    expect(text).to_have_css("text-align", "center")
    ## the middle of the text itself (a range over its lines), not of the paragraph that holds it
    offset = text.evaluate(
        """p => {
            const range = document.createRange()
            range.selectNodeContents(p)
            const box = range.getBoundingClientRect()
            const banner = p.closest(".md-banner").getBoundingClientRect()
            return (box.left + box.right) / 2 - (banner.left + banner.right) / 2
        }"""
    )
    assert abs(offset) < 5


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


def improve_link_checks(page: Page, source: str, public: str):
    link = page.get_by_role("link", name="Suggest an improvement to this page").locator(
        "visible=true"
    )
    expect(link).to_have_count(1)
    expect(link).to_have_attribute("target", "_blank")
    assert set((link.get_attribute("rel") or "").split()) >= {"noopener", "noreferrer"}
    href = link.get_attribute("href")
    assert href is not None
    parts = urlsplit(href)
    assert (parts.scheme, parts.netloc, parts.path) == (
        "https",
        "github.com",
        "/tnek213/bwtidy/issues/new",
    )
    query = parse_qs(parts.query, strict_parsing=True)
    assert query["template"] == ["docs_improvement.yml"]
    assert query["page"] == [source]
    assert query["url"] == [public]
    assert query["title"][0].startswith("Docs: ")
    assert len(query["title"][0]) > len("Docs: ")
    assert query["version"][0].startswith(VERSION)
    hosts = {urlsplit(value).netloc for value in query["url"]} | {parts.netloc}
    assert hosts == {"github.com", PAGES_HOST}
    assert "localhost" not in href and "127.0.0.1" not in href
    return link


def test_every_docs_page_links_to_the_docs_issue_form_in_the_right_sidebar(page: Page):
    for path, source, public in IMPROVE_PAGES:
        page.goto(path)
        link = improve_link_checks(page, source, public)
        sidebar = page.locator(".md-sidebar--secondary")
        expect(sidebar.get_by_text("Improve this page", exact=True)).to_be_visible()
        assert sidebar.get_by_role("link", name="Suggest an improvement to this page").count() == 1
        contents = sidebar.locator("nav").first
        top = link.evaluate("(a) => a.getBoundingClientRect().top")
        assert top >= contents.evaluate("(n) => n.getBoundingClientRect().bottom")


def test_the_docs_issue_link_sits_at_the_end_of_the_content_on_a_narrow_screen(page: Page):
    page.set_viewport_size({"width": 600, "height": 900})
    for path, source, public in IMPROVE_PAGES:
        page.goto(path)
        improve_link_checks(page, source, public)
        expect(page.locator(".md-improve--sidebar")).not_to_be_visible()
        article = page.locator("article.md-content__inner")
        assert article.get_by_role("link", name="Suggest an improvement to this page").count() == 1
        last = article.locator("> :last-child")
        assert last.get_by_role("link", name="Suggest an improvement to this page").count() == 1


def test_the_cards_and_boxes_draw_from_the_site_itself(page: Page):
    hosts: set[str] = set()

    def note(request: Request) -> None:
        if request.resource_type in ("script", "stylesheet", "image", "font"):
            hosts.add(urlsplit(request.url).netloc)

    page.on("request", note)
    page.goto("/docs/contributing/")
    cards = page.locator("article .grid.cards > ul > li")
    expect(cards).to_have_count(6)
    expect(cards.first.locator("svg").first).to_be_visible()
    bug = cards.first.get_by_role("link", name="Report a bug")
    expect(bug).to_have_attribute(
        "href", "https://github.com/tnek213/bwtidy/issues/new?template=bug_report.yml"
    )
    page.goto("/docs/restore/")
    warning = page.locator("article .admonition.warning")
    expect(warning).to_contain_text("Import into an empty account")
    expect(warning.locator(".admonition-title")).to_have_text("Warning")
    expect(warning).not_to_have_css("background-color", TRANSPARENT)
    assert hosts == {urlsplit(page.url).netloc}


## pages with the header, the links out and the cards, whose requests must all stay on the site
OWN_HOST_PAGES = ["/docs/", "/docs/tutorial/first-cleanup/", "/docs/contributing/"]


def test_the_docs_send_requests_only_to_bwtidy_itself(page: Page):
    """No request of any kind (fetch and XHR too) tells another site a self-hosted bwtidy's host."""
    hosts: set[str] = set()
    page.on("request", lambda request: hosts.add(urlsplit(request.url).netloc))
    for path in OWN_HOST_PAGES:
        page.goto(path)
        page.wait_for_load_state("networkidle")
        referrer = page.locator('meta[name="referrer"]')
        expect(referrer).to_have_count(1)
        expect(referrer).to_have_attribute("content", "same-origin")
    assert hosts == {urlsplit(page.url).netloc}


def test_the_header_shows_the_version_without_asking_github(page: Page):
    page.goto("/docs/")
    source = page.locator(".md-header .md-source")
    expect(source).to_have_attribute("href", "https://github.com/tnek213/bwtidy")
    expect(source.locator(".md-source__fact--version")).to_have_text(VERSION)


## the background behind an element: its own and its ancestors' colors, innermost first
BACKGROUNDS = """e => {
    const colors = []
    for (let node = e; node; node = node.parentElement) {
        colors.push(getComputedStyle(node).backgroundColor)
    }
    return colors
}"""

RGBA = tuple[float, float, float, float]


def rgba(text: str) -> RGBA:
    numbers = [float(n) for n in re.findall(r"[\d.]+", text)]
    assert text.startswith("rgb") and len(numbers) in (3, 4), text
    return (numbers[0], numbers[1], numbers[2], numbers[3] if len(numbers) == 4 else 1.0)


def over(top: RGBA, below: RGBA) -> RGBA:
    alpha = top[3]
    return (
        top[0] * alpha + below[0] * (1 - alpha),
        top[1] * alpha + below[1] * (1 - alpha),
        top[2] * alpha + below[2] * (1 - alpha),
        1.0,
    )


def background(element: Locator) -> RGBA:
    ## the canvas is white where nothing paints
    color: RGBA = (255, 255, 255, 1.0)
    for layer in reversed(element.evaluate(BACKGROUNDS)):
        color = over(rgba(layer), color)
    return color


def luminance(color: RGBA) -> float:
    def channel(value: float) -> float:
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(color[0]) + 0.7152 * channel(color[1]) + 0.0722 * channel(color[2])


def contrast(element: Locator, prop: str = "color") -> float:
    behind = background(element)
    front = over(rgba(element.evaluate(f"e => getComputedStyle(e).{prop}")), behind)
    lighter, darker = sorted((luminance(front), luminance(behind)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize(("scheme", "attribute"), [("light", "default"), ("dark", "slate")])
def test_text_links_and_the_selected_page_meet_wcag_aa_contrast(
    page: Page, scheme: Literal["light", "dark"], attribute: str
):
    """WCAG 2.2 AA: 4.5:1 for text and links, 3:1 for the focus indicator."""
    page.set_viewport_size({"width": 1440, "height": 900})
    page.emulate_media(color_scheme=scheme)
    page.goto("/docs/how-to/run/")
    expect(page.locator("body")).to_have_attribute("data-md-color-scheme", attribute)
    text = page.locator("article p").first
    link = page.locator("article p a[href]").first
    selected = page.locator(".md-nav--primary .md-nav__link--active").locator("visible=true")
    expect(selected).to_have_count(1)
    expect(selected).to_contain_text("Run bwtidy")
    for element in (text, link, selected):
        assert contrast(element) >= 4.5
    page.keyboard.press("Tab")
    link.focus()
    assert link.evaluate("a => a.matches(':focus-visible')")
    expect(link).not_to_have_css("outline-style", "none")
    assert float(link.evaluate("a => parseFloat(getComputedStyle(a).outlineWidth)")) >= 2
    assert contrast(link, "outlineColor") >= 3


def test_code_is_drawn_in_ibm_plex_mono_from_the_site_itself(page: Page):
    fonts: list[Response] = []
    page.on("response", lambda r: fonts.append(r) if r.request.resource_type == "font" else None)
    page.goto("/docs/how-to/run/")
    family = page.locator("article pre code").first.evaluate("c => getComputedStyle(c).fontFamily")
    assert family.split(",")[0].strip().strip('"') == "IBM Plex Mono"
    assert family.rstrip().endswith("monospace")
    page.wait_for_function(
        "[...document.fonts].some(f => f.family.replaceAll('\"', '') === 'IBM Plex Mono'"
        " && f.status === 'loaded')"
    )
    assert fonts
    for response in fonts:
        assert response.ok
        assert urlsplit(response.url).netloc == urlsplit(page.url).netloc
        assert "/docs/assets/fonts/ibm-plex-mono/" in response.url


FOOTER = "Made with Zensical · Documentation organized with the Diátaxis framework."


def test_the_footer_credits_zensical_and_diataxis(page: Page):
    page.goto("/docs/how-to/run/")
    credit = page.locator(".md-footer .md-copyright")
    assert " ".join(credit.inner_text().split()) == FOOTER
    for name, href in (
        ("Zensical", "https://zensical.org"),
        ("Diátaxis framework", "https://diataxis.fr"),
    ):
        link = credit.get_by_role("link", name=name, exact=True)
        expect(link).to_have_attribute("href", href)
        expect(link).to_have_attribute("target", "_blank")
        assert set((link.get_attribute("rel") or "").split()) >= {"noopener", "noreferrer"}
