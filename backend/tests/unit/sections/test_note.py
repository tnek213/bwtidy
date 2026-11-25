"""The note section of sections/note.py (docs/spec.md, The tool note): the expected name of the
tool note, bwtidy without the section, and its change."""

import json
from typing import Any

import pytest

from bwtidy.sections import SECTIONS, parse_changes, with_section
from bwtidy.sections.note import DEFAULT_NAME, InvalidNoteSettings, expected_name, parse_note


def notes(**sections: Any) -> str:
    return json.dumps({"version": 1, **sections})


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "",
        notes(),
        ## a note as an earlier bwtidy wrote it, without the section
        notes(templates=[], toFill=[]),
        notes(note={}),
        notes(note={"name": ""}),
        notes(note={"name": 7}),
        notes(note="Pass"),
        notes(note={"name": "Pass", "later": True}),
        "not JSON",
        json.dumps({"version": 2, "note": {"name": "Pass"}}),
    ],
)
def test_without_a_valid_section_the_expected_name_is_bwtidy(stored: str | None):
    assert expected_name(stored) == DEFAULT_NAME == "bwtidy"


def test_the_section_gives_the_expected_name():
    assert expected_name(notes(templates=[], note={"name": "Pass"})) == "Pass"


def test_parse_refuses_a_section_that_does_not_fit():
    assert parse_note(None).name == "bwtidy"
    with pytest.raises(InvalidNoteSettings, match="name"):
        parse_note({"name": ""})
    with pytest.raises(InvalidNoteSettings):
        parse_note({"name": "Pass", "other": 1})


def test_set_name_replaces_the_name_and_keeps_the_other_sections():
    rules = SECTIONS["note"]
    changes = parse_changes([{"op": "setName", "name": "Pass"}])
    rules.check_changes(changes)
    assert rules.changed(None, changes, lambda item_id: item_id) == {"name": "Pass"}
    assert rules.changed({"name": "Old"}, changes, lambda item_id: item_id) == {"name": "Pass"}
    ## a section that does not parse starts over
    assert rules.changed("broken", changes, lambda item_id: item_id) == {"name": "Pass"}
    saved = with_section(notes(templates=[], later=[1]), "note", {"name": "Pass"})
    assert json.loads(saved) == {
        "version": 1,
        "templates": [],
        "later": [1],
        "note": {"name": "Pass"},
    }


def test_check_changes_refuses_an_empty_name_and_a_change_of_another_section():
    rules = SECTIONS["note"]
    with pytest.raises(ValueError, match=r"changes\[0\]: name is empty"):
        rules.check_changes(parse_changes([{"op": "setName", "name": ""}]))
    with pytest.raises(ValueError, match="no change of the note section"):
        rules.check_changes(parse_changes([{"op": "removeItem", "itemId": "x"}]))
