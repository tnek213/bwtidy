"""Datatypes in the Types page: the editor's live marks, the select in the template editor
(docs/spec.md, Datatypes).

The test ends by deleting its template and its datatype through the UI. The fixture puts both
lists back whatever happened, and the values here are made up, none is a secret.
"""

import re
import time
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import COMMIT_TIMEOUT, Api
from support.pages import add_new_field, coordinate_row, pick

from tests.support.section_changes import datatype_changes, template_changes

DATATYPE = "PIN"
TEMPLATE = "Datatype e2e"
FIELD = "Datatype e2e PIN"


## where each line of a textarea starts on the screen, as the line height and the scrolling put it
LINE_TOPS = """t => {
    const style = getComputedStyle(t)
    const top = t.getBoundingClientRect().top + parseFloat(style.borderTopWidth)
        + parseFloat(style.paddingTop) - t.scrollTop
    return t.value.split('\\n').map((_, i) => top + i * parseFloat(style.lineHeight))
}"""


def marks(editor: Locator, box: str) -> Locator:
    return editor.get_by_role("group", name=f"{box} marks").get_by_role("img")


def assert_marks_on_their_lines(editor: Locator, box: str) -> None:
    """Every mark of the box within 4 px of the top of its line, the box holding no empty line.
    The gutter follows a scroll at the next scroll event, so this waits a moment for it."""
    textarea = editor.get_by_role("textbox", name=box, exact=True)
    shown = marks(editor, box)
    deadline = time.monotonic() + 5
    while True:
        tops = textarea.evaluate(LINE_TOPS)
        expect(shown).to_have_count(len(tops))
        off = []
        for index, top in enumerate(tops):
            mark = shown.nth(index).bounding_box()
            if mark is None or abs(mark["y"] - top) > 4:
                off.append((index, mark and mark["y"], top))
        if not off:
            return
        assert time.monotonic() < deadline, (box, off)
        time.sleep(0.1)


def wait_saved(api: Api, path: str) -> dict[str, Any]:
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while (doc := api.get(path))["saving"]:
        assert time.monotonic() < deadline, f"the save of {path} was not committed"
        time.sleep(0.5)
    return doc


@pytest.fixture
def clean_lists(unlocked_page: Page, api: Api) -> Iterator[None]:
    templates = wait_saved(api, "/api/templates")["templates"]
    datatypes = wait_saved(api, "/api/datatypes")["datatypes"]
    yield
    api.wait_saved()
    changes = template_changes(wait_saved(api, "/api/templates")["templates"], templates)
    if changes:
        api.put("/api/templates", {"changes": changes})
        wait_saved(api, "/api/templates")
    changes = datatype_changes(wait_saved(api, "/api/datatypes")["datatypes"], datatypes)
    if changes:
        api.put("/api/datatypes", {"changes": changes})
        wait_saved(api, "/api/datatypes")


def test_make_a_datatype_assign_it_and_delete_both(
    unlocked_page: Page, api: Api, clean_lists: None
) -> None:
    page = unlocked_page
    page.get_by_role("button", name="Types", exact=True).click()
    page.get_by_role("tab", name="Datatypes").click()
    page.get_by_role("button", name="New datatype").click()
    editor = page.get_by_role("dialog", name="New datatype")
    save = editor.get_by_role("button", name="Save")
    warning = "MCP can read the pattern, the examples and the counter-examples"
    expect(editor.get_by_text(warning)).to_be_visible()

    editor.get_by_label("Name", exact=True).fill(DATATYPE)
    expect(editor.get_by_text(r". [ ] \d \w \s ( | ) * + ? {n,m}")).to_be_visible()
    editor.get_by_label("Regular expression", exact=True).fill(r"\d{4,8}(")
    error = editor.get_by_role("alert", name="Pattern error")
    expect(error).to_be_visible()
    expect(error.get_by_test_id("pattern-caret")).to_have_text(re.compile(r"\^$"))
    expect(save).to_be_disabled()

    editor.get_by_label("Regular expression", exact=True).fill(r"\d{4,8}")
    expect(error).to_be_hidden()
    expect(
        editor.get_by_text(re.compile(r"\d+ states, largest repeat 8, shortest match 4"))
    ).to_be_visible()

    ## three marks on their lines, the wrong one red, after a line too long for the box: lines
    ## never wrap, so the one after it stays on its own mark
    editor.get_by_label("Examples", exact=True).fill("1234\n12a4" + "5" * 120 + "\n5678")
    wrong = marks(editor, "Examples").nth(1)
    expect(wrong).to_have_attribute("data-mark", "wrong")
    expect(wrong).to_have_accessible_name("wrong: does not match but is an example")
    expect(wrong).to_have_accessible_description(
        re.compile(r"changes, the first at position 3, expected a digit")
    )
    red, green = wrong.evaluate(
        "m => getComputedStyle(m).color.match(/\\d+/g).slice(0, 2).map(Number)"
    )
    assert red > green + 100
    expect(marks(editor, "Examples").nth(0)).to_have_attribute("data-mark", "right")
    expect(marks(editor, "Examples").nth(2)).to_have_attribute("data-mark", "right")
    assert_marks_on_their_lines(editor, "Examples")
    expect(save).to_be_disabled()

    editor.get_by_label("Examples", exact=True).fill("1234")
    example = marks(editor, "Examples")
    expect(example).to_have_count(1)
    expect(example).to_have_accessible_name("matches")
    expect(example).to_have_accessible_description("matches format 100%, distance 0")

    editor.get_by_label("Counter-examples", exact=True).fill("12a4")
    counter = marks(editor, "Counter-examples")
    expect(counter).to_have_accessible_name("does not match")
    expect(counter).to_have_accessible_description(
        re.compile("1 change at position 3, expected a digit")
    )
    expect(counter).to_have_attribute("data-mark", "right")

    ## the Try box marks its lines too, also once it scrolls
    try_box = editor.get_by_label("Try", exact=True)
    try_box.fill("\n".join(str(1000 + n) for n in range(15)) + "\n123")
    expect(marks(editor, "Try").last).to_have_attribute("data-mark", "right")
    try_box.evaluate("t => { t.scrollTop = t.scrollHeight }")
    assert try_box.evaluate("t => t.scrollTop") > 0
    assert_marks_on_their_lines(editor, "Try")
    try_box.fill("1234\n123")
    expect(marks(editor, "Try")).to_have_count(2)
    expect(marks(editor, "Try").nth(1)).to_have_accessible_name("does not match")
    expect(marks(editor, "Try").nth(1)).to_have_accessible_description(
        re.compile("1 change at position 4, expected a digit")
    )
    assert_marks_on_their_lines(editor, "Try")

    expect(save).to_be_enabled()
    save.click()
    expect(editor).to_be_hidden()

    listed = (
        page.get_by_role("table", name="Datatypes").get_by_role("row").filter(has_text=DATATYPE)
    )
    expect(listed).to_contain_text(r"\d{4,8}")
    expect(listed.get_by_role("cell").nth(3)).to_have_text("0")
    doc = wait_saved(api, "/api/datatypes")
    stored = next(d for d in doc["datatypes"] if d["name"] == DATATYPE)
    assert stored["examples"] == ["1234"]
    assert stored["counterExamples"] == ["12a4"]

    page.get_by_role("tab", name="Templates").click()
    page.get_by_role("button", name="New template").click()
    template_editor = page.get_by_role("dialog", name="New template")
    template_editor.get_by_label("Name", exact=True).fill(TEMPLATE)
    number = add_new_field(template_editor, FIELD, "Hidden")
    row = coordinate_row(template_editor, number)
    expect(row.get_by_role("combobox", name="Datatype")).to_have_text("None")
    pick(row, "Datatype", DATATYPE)
    template_editor.get_by_role("button", name="Save").click()
    expect(template_editor).to_be_hidden()

    templates = page.get_by_role("table", name="Templates")
    expect(templates.get_by_text(f"fields.{FIELD}: {DATATYPE}")).to_be_visible()
    saved = next(t for t in wait_saved(api, "/api/templates")["templates"] if t["name"] == TEMPLATE)
    assert saved["coordinates"] == [
        {"kind": "field", "name": FIELD, "fieldType": 1, "datatype": stored["id"]}
    ]

    ## the editor opens on the datatype it saved
    page.get_by_role("button", name=f"Edit {TEMPLATE}").click()
    template_editor = page.get_by_role("dialog", name=f"Edit {TEMPLATE}")
    expect(
        coordinate_row(template_editor, 1).get_by_role("combobox", name="Datatype")
    ).to_have_text(DATATYPE)
    template_editor.get_by_role("button", name="Cancel").click()

    ## in use: the tab counts the coordinate and refuses the delete, naming the template
    page.get_by_role("tab", name="Datatypes").click()
    expect(listed.get_by_role("cell").nth(3)).to_have_text("1")
    page.get_by_role("button", name=f"Delete {DATATYPE}").click()
    expect(page.get_by_text(re.compile(f"used by the template {TEMPLATE}"))).to_be_visible()

    page.get_by_role("tab", name="Templates").click()
    page.get_by_role("button", name=f"Delete {TEMPLATE}").click()
    page.get_by_role("dialog", name="Delete template").get_by_role("button", name="Delete").click()
    expect(page.get_by_text(f"fields.{FIELD}: {DATATYPE}")).to_be_hidden()
    wait_saved(api, "/api/templates")

    page.get_by_role("tab", name="Datatypes").click()
    page.get_by_role("button", name=f"Delete {DATATYPE}").click()
    page.get_by_role("dialog", name="Delete datatype").get_by_role("button", name="Delete").click()
    expect(page.get_by_role("button", name=f"Delete {DATATYPE}")).to_be_hidden()
    names = [d["name"] for d in wait_saved(api, "/api/datatypes")["datatypes"]]
    assert DATATYPE not in names


def test_a_datatype_can_match_part_of_a_value(
    unlocked_page: Page, api: Api, clean_lists: None
) -> None:
    name = "Part e2e"
    page = unlocked_page
    page.get_by_role("button", name="Types", exact=True).click()
    page.get_by_role("tab", name="Datatypes").click()
    page.get_by_role("button", name="New datatype").click()
    editor = page.get_by_role("dialog", name="New datatype")
    whole = editor.get_by_role("checkbox", name="Whole value")
    expect(whole).to_be_checked()
    expect(editor.get_by_test_id("pattern-start-anchor")).to_have_text("^")
    expect(editor.get_by_test_id("pattern-end-anchor")).to_have_text("$")

    editor.get_by_label("Name", exact=True).fill(name)
    whole.uncheck()
    expect(editor.get_by_test_id("pattern-start-anchor")).to_be_hidden()
    editor.get_by_label("Regular expression", exact=True).fill(r"\d{4}")
    editor.get_by_label("Examples", exact=True).fill("id 1234")
    example = marks(editor, "Examples")
    expect(example).to_have_accessible_description("matches format 100%, distance 0")
    expect(example).to_have_attribute("data-mark", "right")
    save = editor.get_by_role("button", name="Save")
    expect(save).to_be_enabled()
    save.click()
    expect(editor).to_be_hidden()
    stored = next(d for d in wait_saved(api, "/api/datatypes")["datatypes"] if d["name"] == name)
    assert stored["wholeValue"] is False

    page.get_by_role("button", name=f"Edit {name}").click()
    editor = page.get_by_role("dialog", name=f"Edit {name}")
    whole = editor.get_by_role("checkbox", name="Whole value")
    expect(whole).not_to_be_checked()
    whole.check()
    expect(marks(editor, "Examples")).to_have_attribute("data-mark", "wrong")
    expect(editor.get_by_role("button", name="Save")).to_be_disabled()
    editor.get_by_role("button", name="Cancel").click()
