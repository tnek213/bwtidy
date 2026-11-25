"""The tool note's notes in sections: read and dump, setToolNoteSection, each saved alone."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import bwtidy.queue.autocommit as autocommit
from bwtidy.operations import InvalidOperation, SetToolNoteSection
from bwtidy.sections import SECTIONS, InvalidToolNote, dump_sections, read_sections
from bwtidy.sections.generator import RemoveItem, SetDefault, SetItem
from bwtidy.sections.templates import AddTemplate, SetTemplate
from bwtidy.sections.to_fill import AddColumn, RemoveColumn
from bwtidy.writes.check import TOOL_NOTE_MANAGED
from tests.support.app_env import AppEnv
from tests.support.normalize_data import EXTRA
from tests.support.queue_helpers import commit_now, enqueue_dict, enqueue_ui, listing
from tests.support.section_changes import (
    add_datatype,
    add_template,
    changes,
    move_template,
    remove_datatype,
    remove_template,
    rename_template,
    set_datatype,
    set_template,
)
from tests.support.sections_helpers import note_sections, write_notes

COMPUTER = {
    "name": "Computer",
    "type": 2,
    "coordinates": [{"kind": "field", "name": "user", "fieldType": 0}],
}
ADD_COMPUTER = AddTemplate(op="addTemplate", template=COMPUTER)
SECTION_SAVE = {
    "kind": "setToolNoteSection",
    "section": "templates",
    "changes": [add_template(COMPUTER)],
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppEnv]:
    ## the auto-commit task never gets a turn on its own here, the tests ask for each commit
    monkeypatch.setattr(autocommit, "TICK", 3600.0)
    with AppEnv(tmp_path, extra=EXTRA) as env:
        env.unlock()
        ## nor the commit a save starts, so a test can change the note before it
        monkeypatch.setattr(env.app.state.bw.auto_commit, "start_commit", lambda: None)
        yield env


def test_read_and_dump_keep_every_key():
    notes = dump_sections({"version": 1, "templates": [], "later": {"ü": [1, 2]}})
    assert read_sections(notes) == {"version": 1, "templates": [], "later": {"ü": [1, 2]}}
    assert "ü" in notes
    assert notes.startswith('{\n  "version": 1')


def test_empty_notes_read_as_version_1():
    assert read_sections(None) == {"version": 1}
    assert read_sections("  \n") == {"version": 1}


@pytest.mark.parametrize(
    ("notes", "message"),
    [
        ("free text", "not JSON"),
        ("[1, 2]", "not a JSON object"),
        ('{"templates": []}', "not version 1"),
        ('{"version": 2, "templates": []}', "not version 1"),
    ],
)
def test_other_notes_are_refused(notes: str, message: str):
    with pytest.raises(InvalidToolNote, match=message):
        read_sections(notes)


def test_a_templates_save_keeps_the_other_sections(env: AppEnv):
    write_notes(env, {"version": 1, "templates": [], "toFill": [1]})
    env.reload()
    response = env.client.put("/api/templates", json=changes(add_template(COMPUTER)))
    assert response.status_code == 202, response.text
    assert commit_now(env).json()["failed"] == []
    assert note_sections(env) == {"version": 1, "templates": [COMPUTER], "toFill": [1]}
    assert env.client.get("/api/templates").json()["templates"] == [COMPUTER]
    [entry] = env.change_log()
    assert (entry["operationKind"], entry["itemIds"]) == ("setToolNoteSection", [env.tool_note_id])


def add_computer(env: AppEnv) -> int:
    return env.queue.enqueue_section_changes("templates", [ADD_COMPUTER], "Save the templates")


def test_the_write_reads_the_note_as_it_is_then(env: AppEnv):
    add_computer(env)
    ## another client changes the note after the save was queued: no conflict, nothing lost
    write_notes(env, {"version": 1, "templates": [], "generator": {"length": 30}})
    outcome = commit_now(env).json()
    assert outcome["failed"] == []
    assert note_sections(env) == {
        "version": 1,
        "templates": [COMPUTER],
        "generator": {"length": 30},
    }


def test_entries_for_different_sections_in_one_commit_all_land(env: AppEnv):
    entries = env.queue
    first = add_computer(env)
    listed = [{"itemId": "login-1", "columns": ["login.totp"], "since": "2026-10-30"}]
    mark = AddColumn(op="addColumn", itemId="login-1", column="login.totp", since="2026-10-30")
    to_fill = entries.enqueue_section_changes("toFill", [mark], "to fill")
    settings = {"type": "password", "length": 20}
    default = SetDefault(op="setDefault", options=settings)
    generator = entries.enqueue_section_changes("generator", [default], "generator")
    edited = {**COMPUTER, "type": 1}
    change = SetTemplate(op="setTemplate", template=edited)
    last = entries.enqueue_section_changes("templates", [change], "Save the templates")
    outcome = commit_now(env).json()
    assert outcome["applied"] == [first, to_fill, generator, last]
    sections = note_sections(env)
    assert sections["templates"] == [edited]
    assert sections["toFill"] == listed
    assert sections["generator"]["default"]["length"] == 20
    ## one backup for the whole commit
    assert len({e["result"]["backup"] for e in listing(env, "applied")}) == 1


def test_only_bwtidy_itself_saves_a_section(env: AppEnv):
    response = enqueue_ui(env, SECTION_SAVE)
    assert response.status_code == 422
    assert TOOL_NOTE_MANAGED in response.json()["detail"]
    response = env.post(SECTION_SAVE)
    assert response.status_code == 422
    assert TOOL_NOTE_MANAGED in response.json()["detail"]
    with pytest.raises(InvalidOperation, match="managed by bwtidy"):
        enqueue_dict(env, SECTION_SAVE, "an agent's try")
    unknown = {**SECTION_SAVE, "section": "secrets"}
    assert enqueue_ui(env, unknown).status_code == 422
    assert listing(env) == []
    assert env.bw.put == []


def test_an_invalid_template_change_is_refused_when_queued(env: AppEnv):
    entries = env.queue
    with pytest.raises(InvalidOperation, match="unknown item type"):
        change = AddTemplate(op="addTemplate", template={**COMPUTER, "type": 9})
        entries.enqueue_section_changes("templates", [change], "Save the templates")
    with pytest.raises(InvalidOperation, match="already a template named 'Computer'"):
        entries.enqueue_section_changes("templates", [ADD_COMPUTER, ADD_COMPUTER], "x")
    with pytest.raises(InvalidOperation, match="no template named 'Computer'"):
        change = SetTemplate(op="setTemplate", template=COMPUTER)
        entries.enqueue_section_changes("templates", [change], "Save the templates")
    assert listing(env) == []


## changes of a section

SHORT = {"type": "password", "length": 12}


def same(item_id: str) -> str:
    return item_id


WORDS = {"type": "passphrase", "words": 4}


def test_generator_changes_apply_one_by_one_and_follow_conversions():
    stored = {"items": {"old": SHORT, "kept": SHORT}}
    changes = [
        SetDefault(op="setDefault", options=WORDS),
        SetItem(op="setItem", itemId="old", options=WORDS),
        RemoveItem(op="removeItem", itemId="kept"),
    ]
    assert set(SECTIONS["generator"].changed(stored, changes, same)["items"]) == {"old"}
    value = SECTIONS["generator"].changed(stored, changes, lambda i: "new" if i == "old" else i)
    assert value["default"]["words"] == 4
    assert list(value["items"]) == ["new"]
    assert value["items"]["new"]["type"] == "passphrase"


def test_to_fill_changes_keep_since_and_drop_an_empty_entry():
    stored = [{"itemId": "old", "columns": ["login.totp"], "since": "2026-10-01"}]
    changes = [
        AddColumn(op="addColumn", itemId="old", column="login.password", since="2026-11-08"),
        AddColumn(op="addColumn", itemId="other", column="fields.PIN", since="2026-11-08"),
        RemoveColumn(op="removeColumn", itemId="other", column="fields.PIN"),
        RemoveColumn(op="removeColumn", itemId="old", column="login.totp"),
    ]
    value = SECTIONS["toFill"].changed(stored, changes, lambda i: "new" if i == "old" else i)
    assert value == [{"itemId": "new", "columns": ["login.password"], "since": "2026-10-01"}]


@pytest.mark.parametrize(("section", "broken"), [("generator", {"length": 30}), ("toFill", [1])])
def test_changes_to_a_section_that_does_not_parse_start_over(section: str, broken: Any):
    if section == "generator":
        value = SECTIONS[section].changed(
            broken, [SetDefault(op="setDefault", options=WORDS)], same
        )
        assert value == {"default": {**value["default"], "type": "passphrase"}, "items": {}}
    else:
        change = AddColumn(op="addColumn", itemId="a", column="login.totp", since="2026-11-08")
        value = SECTIONS[section].changed(broken, [change], same)
        assert value == [{"itemId": "a", "columns": ["login.totp"], "since": "2026-11-08"}]


def test_changes_that_do_not_fit_the_section_are_refused_when_queued(env: AppEnv):
    entries = env.queue
    with pytest.raises(InvalidOperation, match="no change of the toFill section"):
        entries.enqueue_section_changes("toFill", [RemoveItem(op="removeItem", itemId="a")], "x")
    with pytest.raises(InvalidOperation, match="length"):
        change = SetItem(op="setItem", itemId="login-1", options={"type": "password", "length": 1})
        entries.enqueue_section_changes("generator", [change], "x")
    with pytest.raises(InvalidOperation, match="no secret column"):
        change = AddColumn(op="addColumn", itemId="login-1", column="name", since="2026-11-08")
        entries.enqueue_section_changes("toFill", [change], "x")
    with pytest.raises(InvalidOperation, match="no change of the templates section"):
        entries.enqueue_section_changes("templates", [RemoveItem(op="removeItem", itemId="a")], "x")
    assert listing(env) == []


def test_a_row_in_sqlite_is_read_back_and_applied_as_its_changes(env: AppEnv):
    """A stored row (a restart in between) is read as the saves under way and written."""
    write_notes(env, {"version": 1, "templates": [], "generator": {"items": {"login-1": SHORT}}})
    env.reload()
    operation = {
        "kind": "setToolNoteSection",
        "section": "generator",
        "changes": [
            {"op": "setDefault", "options": {**WORDS, "separator": "-"}},
            {"op": "removeItem", "itemId": "login-1"},
        ],
    }
    ## stored with every option filled in, as the section's rules write it
    default = {**WORDS, "separator": "-", "capitalize": False, "includeNumber": False}
    saved = {"default": default, "items": {}}
    entry_id = env.queue.db.add_queue_entry(
        "ui", "Save the generator settings", json.dumps(operation), edits_tool_note=True
    )
    saving, value = env.queue.saving_section("generator")
    assert (saving, value) == (True, saved)
    [pending] = listing(env)
    assert pending["operation"] == operation
    assert pending["diffError"] is None
    assert commit_now(env).json()["applied"] == [entry_id]
    assert note_sections(env)["generator"] == saved


def test_a_section_save_with_a_value_and_no_changes_is_refused():
    """A section save carries changes, so a stored row with the whole section as its value
    does not parse."""
    with pytest.raises(ValidationError, match="changes"):
        SetToolNoteSection.model_validate(
            {"kind": "setToolNoteSection", "section": "templates", "value": [COMPUTER]}
        )


## templates and datatypes, changed by name and by id

ROUTER = {
    "name": "Router",
    "type": 1,
    "coordinates": [{"kind": "path", "path": "login.username"}],
}
PIN_ID = "0000aaaa"
PIN = {
    "id": PIN_ID,
    "name": "PIN",
    "pattern": r"\d{4,8}",
    "description": "",
    "ignoreCase": False,
    "examples": [],
    "counterExamples": [],
}


def put_templates(env: AppEnv, *listed: dict[str, Any]) -> Any:
    return env.client.put("/api/templates", json=changes(*listed))


def failure(env: AppEnv, entry_id: int) -> str:
    return next(e for e in listing(env) if e["id"] == entry_id)["result"]["detail"]


def test_two_template_saves_built_on_the_same_list_both_land(env: AppEnv):
    write_notes(env, {"version": 1, "templates": [COMPUTER]})
    env.reload()
    ## two tabs that both read [Computer]: one edits it, the other adds Router
    username = {"kind": "path", "path": "login.username"}
    edited = {
        **COMPUTER,
        "type": 1,
        "coordinates": [{"kind": "field", "name": "user", "fieldType": 0}, username],
    }
    assert put_templates(env, set_template(edited)).status_code == 202
    assert put_templates(env, add_template(ROUTER)).status_code == 202
    assert commit_now(env).json()["failed"] == []
    assert note_sections(env)["templates"] == [edited, ROUTER]


def test_a_rename_and_an_edit_of_the_renamed_template_land_in_order(env: AppEnv):
    write_notes(env, {"version": 1, "templates": [COMPUTER, ROUTER]})
    env.reload()
    renamed = {**COMPUTER, "name": "Laptop"}
    body = [rename_template("Computer", "Laptop"), set_template(renamed)]
    assert put_templates(env, *body).status_code == 202
    ## the page reads the list as the save under way leaves it and edits the renamed one
    shown = env.client.get("/api/templates").json()
    assert (shown["saving"], [t["name"] for t in shown["templates"]]) == (
        True,
        ["Laptop", "Router"],
    )
    edited = {**renamed, "type": 1}
    assert put_templates(env, set_template(edited), move_template("Laptop", 1)).status_code == 202
    assert commit_now(env).json()["failed"] == []
    assert note_sections(env)["templates"] == [ROUTER, edited]


def test_an_edit_of_a_template_another_save_deleted_fails_with_a_reason(env: AppEnv):
    write_notes(env, {"version": 1, "templates": [COMPUTER, ROUTER]})
    env.reload()
    assert put_templates(env, remove_template("Computer")).status_code == 202
    ## a tab that still shows Computer: refused at once, while the deletion waits
    refused = put_templates(env, set_template({**COMPUTER, "type": 1}))
    assert refused.status_code == 422
    assert "no template named 'Computer'" in refused.json()["detail"]
    assert commit_now(env).json()["failed"] == []
    ## queued in time, then another bwtidy deletes it before the commit
    write_notes(env, {"version": 1, "templates": [ROUTER, COMPUTER]})
    env.reload()
    entry_id = put_templates(env, set_template({**COMPUTER, "type": 1})).json()["entryId"]
    write_notes(env, {"version": 1, "templates": [{**ROUTER, "name": "Wifi"}]})
    outcome = commit_now(env).json()
    assert outcome["failed"] == [entry_id]
    assert "no template named 'Computer', a save before this one renamed" in failure(env, entry_id)
    assert note_sections(env)["templates"] == [{**ROUTER, "name": "Wifi"}]


def test_a_rename_to_a_taken_name_and_a_move_of_a_gone_one_fail(env: AppEnv):
    write_notes(env, {"version": 1, "templates": [COMPUTER, ROUTER]})
    env.reload()
    refused = put_templates(env, rename_template("Computer", " router "))
    assert refused.status_code == 422
    assert "already a template named 'router'" in refused.json()["detail"]
    refused = put_templates(env, move_template("Gone", 0))
    assert "no template named 'Gone'" in refused.json()["detail"]
    ## a case-only rename is no clash with itself
    assert put_templates(env, rename_template("Computer", "computer")).status_code == 202
    assert commit_now(env).json()["failed"] == []
    assert [t["name"] for t in note_sections(env)["templates"]] == ["computer", "Router"]


def test_a_templates_row_in_sqlite_and_a_save_after_it_both_land(env: AppEnv):
    write_notes(env, {"version": 1, "templates": [COMPUTER]})
    env.reload()
    operation = {
        "kind": "setToolNoteSection",
        "section": "templates",
        "changes": [remove_template("Computer"), add_template(ROUTER)],
    }
    entry_id = env.queue.db.add_queue_entry(
        "ui", "Save the templates", json.dumps(operation), edits_tool_note=True
    )
    assert put_templates(env, add_template(COMPUTER)).status_code == 202
    assert commit_now(env).json()["applied"][0] == entry_id
    assert note_sections(env)["templates"] == [ROUTER, COMPUTER]


def put_datatypes(env: AppEnv, *listed: dict[str, Any]) -> Any:
    return env.client.put("/api/datatypes", json=changes(*listed))


def test_two_datatype_saves_built_on_the_same_list_both_land(env: AppEnv):
    write_notes(env, {"version": 1, "datatypes": [PIN]})
    env.reload()
    edited = {**PIN, "description": "four to eight digits"}
    assert put_datatypes(env, set_datatype(edited)).status_code == 202
    added = put_datatypes(env, add_datatype({"name": "Word", "pattern": "[a-z]+"}))
    assert added.status_code == 202
    assert commit_now(env).json()["failed"] == []
    stored = note_sections(env)["datatypes"]
    assert stored[0] == edited
    assert stored[1]["name"] == "Word"
    assert len({d["id"] for d in stored}) == 2


def test_an_edit_of_a_deleted_datatype_fails_and_a_second_delete_does_nothing(env: AppEnv):
    write_notes(env, {"version": 1, "datatypes": [PIN]})
    env.reload()
    entry_id = put_datatypes(env, set_datatype({**PIN, "name": "Code"})).json()["entryId"]
    assert put_datatypes(env, remove_datatype(PIN_ID)).status_code == 202
    write_notes(env, {"version": 1, "datatypes": []})
    outcome = commit_now(env).json()
    assert outcome["failed"] == [entry_id]
    assert "the datatype 'Code' is gone" in failure(env, entry_id)
    assert len(outcome["applied"]) == 1
    assert note_sections(env)["datatypes"] == []


def test_deleting_a_datatype_a_template_took_meanwhile_is_refused_at_commit(env: AppEnv):
    write_notes(env, {"version": 1, "templates": [COMPUTER], "datatypes": [PIN]})
    env.reload()
    entry_id = put_datatypes(env, remove_datatype(PIN_ID)).json()["entryId"]
    ## another bwtidy gives Computer a coordinate of PIN before the commit
    coordinate = {"kind": "field", "name": "PIN", "fieldType": 0, "datatype": PIN_ID}
    uses = {**COMPUTER, "coordinates": [coordinate]}
    write_notes(env, {"version": 1, "templates": [uses], "datatypes": [PIN]})
    outcome = commit_now(env).json()
    assert outcome["failed"] == [entry_id]
    assert "the datatype 'PIN' is used by the templates Computer" in failure(env, entry_id)
    assert note_sections(env)["datatypes"] == [PIN]
