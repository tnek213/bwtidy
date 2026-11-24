"""The Change log's filter bar narrows the rows by operation and by field (docs/spec.md, Grid,
the Change log page).

Changes the username of Electricity company and moves Expense portal to the Home folder,
file_put_back puts both back.
"""

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import COMMIT_TIMEOUT, Api, rows_by_name
from support.pages import pick

pytestmark = pytest.mark.usefixtures("file_put_back")

USERNAME_ITEM = "Electricity company"
FOLDER_ITEM = "Expense portal"
DIFF_REPORT_HINT = "Lists what every shown change did. Filter the log first to narrow it down."


def change_rows(page: Page, item: str) -> Locator:
    """The Change log's rows of changes to the item."""
    log = page.get_by_role("table", name="Change log")
    return log.get_by_role("row").filter(has=page.get_by_role("cell", name=item, exact=True))


def test_the_rows_filter_by_operation_and_by_field(unlocked_page: Page, api: Api):
    page = unlocked_page
    rows = rows_by_name(api)
    username, folder = rows[USERNAME_ITEM], rows[FOLDER_ITEM]
    operations = [
        {
            "kind": "setLeaf",
            "itemId": username["id"],
            "revisionDate": username["revisionDate"],
            "column": "login.username",
            "value": "filters-e2e@home.example.test",
        },
        {
            "kind": "moveToFolder",
            "itemIds": [folder["id"]],
            "revisionDates": {folder["id"]: folder["revisionDate"]},
            "folderName": "Home",
        },
    ]
    api.post("/api/operations", {"operations": operations}, timeout=COMMIT_TIMEOUT * 1000)

    page.get_by_role("button", name="Change log", exact=True).click()
    expect(change_rows(page, USERNAME_ITEM).first).to_be_visible()
    expect(change_rows(page, FOLDER_ITEM).first).to_be_visible()

    pick(page, "Operation", "Move to folder")
    page.keyboard.press("Escape")
    expect(change_rows(page, FOLDER_ITEM).first).to_contain_text("moveToFolder")
    expect(change_rows(page, USERNAME_ITEM)).to_have_count(0)

    page.get_by_role("button", name="Clear filters").click()
    expect(change_rows(page, USERNAME_ITEM).first).to_be_visible()

    report = page.get_by_role("button", name="Diff report")
    report.hover()
    expect(page.get_by_role("tooltip")).to_have_text(DIFF_REPORT_HINT)

    field = page.get_by_role("combobox", name="Field")
    field.fill("login.user")
    page.get_by_role("option", name="login.username", exact=True).click()
    expect(change_rows(page, USERNAME_ITEM).first).to_contain_text("login.username")
    expect(change_rows(page, FOLDER_ITEM)).to_have_count(0)


INTRO = (
    "The latest changes written to the vault and the backups, newest first. Values are never "
    "logged. Items that are no longer in the vault are shown by id, and so are folders. "
    "Restore puts the items or the folder of a change back as they were before it, from its "
    "backup, and a clock with an arrow back marks a change that a later restore undid. Hover "
    "an operation for what it did. A backup row downloads the encrypted file."
)
BACKUP = (
    "A backup is age encrypted with the master password it was taken under. Decrypt it on a "
    "tmpfs such as /dev/shm (age -d -o backup.zip NAME.zip.age), so no plain copy touches the"
    " disk. The steps for restoring a lost vault from it are in RESTORE.md in every backup, "
    "whose source is docs/restore.md in the bwtidy repository."
)

## The line boxes of a paragraph: the characters of its text grouped by the line they sit on,
## with the left and right of the line's visible characters (a space at the end of a line hangs
## and is left out of the width).
LINES = """el => {
  const lines = []
  let line = null
  const range = document.createRange()
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    for (let i = 0; i < node.length; i++) {
      const char = node.data[i]
      range.setStart(node, i)
      range.setEnd(node, i + 1)
      const rect = [...range.getClientRects()].find((r) => r.width > 0)
      if (/\\s/.test(char) || !rect) {
        if (line) line.text += char
        continue
      }
      const overlap = (l) => Math.min(l.bottom, rect.bottom) - Math.max(l.top, rect.top)
      line = lines.find((l) => overlap(l) > rect.height / 2)
      if (!line) {
        line = {top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right, text: ''}
        lines.push(line)
      }
      line.left = Math.min(line.left, rect.left)
      line.right = Math.max(line.right, rect.right)
      line.text += char
    }
  }
  const box = el.getBoundingClientRect()
  return {
    hyphens: getComputedStyle(el).hyphens,
    text: el.textContent,
    right: box.right,
    width: box.width,
    lines: lines.map((l) => ({text: l.text, width: l.right - l.left, right: l.right})),
  }
}"""


def balanced(paragraph: Locator, source: str) -> float:
    """Fails unless the paragraph's lines are balanced, unhyphenated and settled."""
    breaks = paragraph.inner_html()
    paragraph.page.wait_for_timeout(500)
    assert paragraph.inner_html() == breaks
    shown = paragraph.evaluate(LINES)
    assert shown["hyphens"] == "manual"
    assert " ".join(shown["text"].split()) == source
    lines = shown["lines"]
    assert len(lines) >= 4, [line["text"] for line in lines]
    widest = max(line["width"] for line in lines)
    for line in lines[:-1]:
        assert line["width"] >= 0.85 * widest, [
            (line["text"], round(line["width"])) for line in lines
        ]
    for line in lines:
        assert not line["text"].rstrip().endswith("-"), line["text"]
        assert line["right"] <= shown["right"] + 0.5, line["text"]
    return shown["width"]


def settled(page: Page) -> None:
    """Waits two frames, for the ResizeObserver and the render of the new breaks."""
    page.evaluate(
        "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"
    )


def test_the_change_log_paragraphs_have_balanced_lines(unlocked_page: Page):
    page = unlocked_page
    page.set_viewport_size({"width": 1400, "height": 900})
    page.get_by_role("button", name="Change log", exact=True).click()
    intro = page.get_by_text("The latest changes written to the vault")
    backup = page.get_by_text("A backup is age encrypted")
    expect(intro).to_be_visible()
    expect(backup).to_be_visible()
    settled(page)
    wide = balanced(intro, INTRO)
    balanced(backup, BACKUP)
    page.set_viewport_size({"width": 900, "height": 900})
    settled(page)
    balanced(intro, INTRO)
    balanced(backup, BACKUP)
    ## at 900 px the paragraphs still get their 75 characters, narrower the breaks follow the width
    page.set_viewport_size({"width": 520, "height": 900})
    settled(page)
    assert balanced(intro, INTRO) < wide - 50
    balanced(backup, BACKUP)
