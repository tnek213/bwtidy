"""Datatypes through the routes: the scores of the UI and of MCP, GET and PUT /api/datatypes, the
templates that use them and the checks of a pattern and a value."""

import copy
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import bwtidy.queue.autocommit as autocommit
from bwtidy.items.patterns import MAX_VALUE_LEN
from bwtidy.operations import InvalidOperation
from bwtidy.queue.state import UI
from bwtidy.sections.datatypes import MAX_EXAMPLES, SetDatatype
from tests.support.app_env import AppEnv, logged_in
from tests.support.datatypes_helpers import (
    BOOLEAN,
    CONFORMING,
    DATE,
    DATE_ID,
    HIDDEN,
    LOGIN,
    OFF_FORMAT,
    PIN,
    PIN_ID,
    TEXT,
    field,
    item,
    path,
    scores_of,
    store,
    stored,
    template,
)
from tests.support.mcp_helpers import data, enable
from tests.support.queue_helpers import commit_now, listing
from tests.support.section_changes import (
    add_datatype,
    add_template,
    changes,
    remove_datatype,
    set_datatype,
    set_template,
)
from tests.support.sections_helpers import note_sections, write_notes


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppEnv]:
    ## the auto-commit task never gets a turn on its own here, the tests ask for each commit
    monkeypatch.setattr(autocommit, "TICK", 3600.0)
    yield from logged_in(tmp_path)


# The results in memory, and the matches of the UI and of MCP


def test_the_ui_scores_a_secret_column_without_a_character_of_it(env: AppEnv):
    secret = "seven-secret-chars"
    store(
        env,
        [PIN],
        [template(field("PIN", HIDDEN, PIN_ID))],
        item(("PIN", secret, HIDDEN)),
    )
    response = env.client.get("/api/template-matches")
    entry = response.json()["dt-1"]["scores"][0]["formats"][0]
    assert entry["column"] == "fields.PIN"
    assert entry["score"] < 1
    assert entry["expected"] == "a digit"
    leaked = secret in response.text or any(part in response.text for part in secret.split("-"))
    assert not leaked


def test_get_templates_names_a_pattern_with_an_unknown_datatype_as_a_pattern(env: AppEnv):
    users = {"kind": "fieldPattern", "pattern": "pin_*", "fieldType": TEXT, "datatype": "ffffffff"}
    store(env, [], [template(users)])
    [warning] = env.client.get("/api/templates").json()["warnings"]
    assert warning.startswith("T: the pattern fields.pin_* names the unknown datatype")


def test_a_changed_pattern_is_scored_anew(env: AppEnv):
    store(env, [PIN], [template(field("PIN", TEXT, PIN_ID))], item(("PIN", OFF_FORMAT, TEXT)))
    assert scores_of(env, "dt-1")[0]["filled"] == 2.8
    changed = {**PIN, "pattern": r" \d+"}
    write_notes(
        env,
        {
            "version": 1,
            "templates": [template(field("PIN", TEXT, PIN_ID))],
            "datatypes": [changed],
        },
    )
    env.reload()
    assert scores_of(env, "dt-1")[0]["filled"] == 3


def mcp_answer(env: AppEnv, value: str | None, item_id: str, secret: bool = True) -> dict[str, Any]:
    """What MCP gets for one item, its value hidden or not, through the real tool."""
    kind = HIDDEN if secret else TEXT
    fields = [("PIN", value, kind)] if value is not None else []
    env.bw.items.append(item(*fields, item_id=item_id))
    env.reload()
    return data(env, "get_item", item_id=item_id)["template"]


def test_mcp_gets_the_same_answer_for_a_conforming_and_an_off_format_secret(env: AppEnv):
    enable(env)
    store(env, [PIN], [template(field("PIN", HIDDEN, PIN_ID))])
    conforming = mcp_answer(env, CONFORMING, "dt-good")
    off = mcp_answer(env, OFF_FORMAT, "dt-off")
    too_long = mcp_answer(env, "1" * (MAX_VALUE_LEN + 1), "dt-long")
    assert conforming == off == too_long
    assert conforming["match"] == "T"
    score = conforming["scores"][0]
    assert (score["filled"], score["total"]) == (3, 3)
    assert "formats" not in score


def test_mcp_sees_an_unset_secret_as_unset_and_without_a_format(env: AppEnv):
    enable(env)
    store(env, [PIN], [template(field("PIN", HIDDEN, PIN_ID))])
    score = mcp_answer(env, None, "dt-unset")["scores"][0]
    assert (score["filled"], score["total"]) == (1, 3)
    assert "formats" not in score


def test_mcp_sees_the_format_of_a_column_that_is_no_secret(env: AppEnv):
    enable(env)
    store(env, [PIN], [template(field("PIN", TEXT, PIN_ID))])
    answer = mcp_answer(env, OFF_FORMAT, "dt-text", secret=False)
    assert answer["match"] is None
    score = answer["scores"][0]
    assert score["filled"] == 2.8
    assert score["formats"][0] == {
        "column": "fields.PIN",
        "datatype": "PIN",
        "score": 0.8,
        "distance": 1,
        "firstEdit": 0,
        "expected": "a digit",
        "tooLong": False,
    }


def test_mcp_sees_no_format_of_a_builtin_secret(env: AppEnv):
    enable(env)
    store(env, [PIN], [template(path("login.password", PIN_ID), type_=LOGIN)])
    answer = data(env, "get_item", item_id="login-1")["template"]
    assert answer["match"] == "T"
    assert "formats" not in answer["scores"][0]


def test_mcp_answers_as_before_without_datatypes(env: AppEnv):
    enable(env)
    store(env, [], [template(field("PIN", HIDDEN))])
    score = mcp_answer(env, CONFORMING, "dt-plain")["scores"][0]
    assert score == {
        "template": "T",
        "score": 1,
        "typeMatches": True,
        "filled": 2,
        "total": 2,
        "missing": [],
    }


# The routes


def test_get_lists_the_datatypes_with_facts_and_marks_the_broken(env: AppEnv):
    bad = {**DATE, "pattern": "(?=x)"}
    store(env, [PIN, bad], [])
    body = env.client.get("/api/datatypes").json()
    assert body["saving"] is False
    good, broken = body["datatypes"]
    assert good == {
        **stored(PIN),
        "wholeValue": True,
        "states": good["states"],
        "maxRepeat": 8,
        "shortest": 4,
        "broken": None,
    }
    assert good["states"] > 0
    assert broken["id"] == DATE_ID
    assert "column" in broken["broken"]
    assert (broken["states"], broken["maxRepeat"], broken["shortest"]) == (None, None, None)


def test_get_without_a_section_and_with_one_that_cannot_be_read(env: AppEnv):
    assert env.client.get("/api/datatypes").json() == {"datatypes": [], "saving": False}
    store(env, {"no": "list"}, [])
    body = env.client.get("/api/datatypes").json()
    assert body["datatypes"] == []
    assert "not a list" in body["error"]


def test_put_queues_the_change_gives_a_new_id_and_keeps_other_sections(env: AppEnv):
    write_notes(env, {"version": 1, "templates": [], "later": [1]})
    env.reload()
    new = {"name": "PIN", "pattern": PIN["pattern"], "states": 99}
    response = env.client.put("/api/datatypes", json=changes(add_datatype(new)))
    assert response.status_code == 202, response.text
    body = response.json()
    made = body["datatypes"][0]["id"]
    assert len(made) == 8
    assert int(made, 16) >= 0
    entry = next(e for e in listing(env) if e["id"] == body["entryId"])
    assert entry["summary"] == "Save the datatypes"
    assert commit_now(env).status_code == 200
    sections = note_sections(env)
    assert sections["later"] == [1]
    assert sections["datatypes"] == [stored({**PIN, "id": made})]
    assert env.client.get("/api/datatypes").json()["saving"] is False


def test_a_save_under_way_is_what_get_shows(env: AppEnv, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(env.app.state.bw.auto_commit, "start_commit", lambda: None)
    new = {"name": "PIN", "pattern": PIN["pattern"]}
    put = env.client.put("/api/datatypes", json=changes(add_datatype(new)))
    made = put.json()["datatypes"][0]["id"]
    saving = env.client.get("/api/datatypes").json()
    assert saving["saving"] is True
    assert [d["id"] for d in saving["datatypes"]] == [made]
    assert commit_now(env).status_code == 200
    assert env.client.get("/api/datatypes").json()["saving"] is False


def test_put_keeps_an_id_and_two_new_ones_get_their_own(env: AppEnv):
    store(env, [PIN], [])
    body = changes(
        set_datatype({**PIN, "pattern": r"\d+"}),
        add_datatype({"name": "A", "pattern": "a"}),
        add_datatype({"name": "B", "pattern": "b"}),
    )
    response = env.client.put("/api/datatypes", json=body)
    assert response.status_code == 202, response.text
    ids = [d["id"] for d in response.json()["datatypes"]]
    assert ids[0] == PIN_ID
    assert len(set(ids)) == 3


def test_put_refuses_the_whole_list(env: AppEnv):
    response = env.client.put("/api/datatypes", json={"datatypes": [PIN]})
    assert response.status_code == 422
    assert "saved as changes" in response.json()["detail"]
    assert listing(env, "pending") == []


def new(datatype: dict[str, Any]) -> dict[str, Any]:
    return changes({"op": "addDatatype", "datatype": datatype})


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"changes": "no list"}, "must be a list"),
        ({"changes": [{"op": "addTemplate", "template": {}}]}, "no change of the datatypes"),
        (new({"name": "X", "pattern": "a", "id": "0123abcd"}), "makes the id"),
        (new({"name": "X", "pattern": "a", "id": ""}), "makes the id"),
        (changes(set_datatype({"name": "X", "pattern": "a", "id": 7})), "id is no text"),
        (changes(set_datatype({"name": "X", "pattern": "a", "id": "0123abcd"})), "is gone"),
        (new({"pattern": "a"}), "name"),
        (new({"name": "X", "pattern": "(?=a)"}), "column"),
        (new({"name": "X", "pattern": "a", "examples": ["b"]}), "example 1"),
        (new({"name": "X", "pattern": "a", "counterExamples": ["a"]}), "counter-example"),
        (
            changes(
                add_datatype({"name": "X", "pattern": "a"}),
                add_datatype({"name": " x", "pattern": "b"}),
            ),
            "more than one",
        ),
        (changes({"op": "addDatatype", "datatype": "no object"}), "changes.0.addDatatype.datatype"),
        (changes(remove_datatype("")), "id is empty"),
    ],
)
def test_put_refuses_and_queues_nothing(env: AppEnv, body: dict[str, Any], message: str):
    response = env.client.put("/api/datatypes", json=body)
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert listing(env, "pending") == []


def test_put_refuses_a_broken_datatype_until_it_is_fixed_or_removed(env: AppEnv):
    bad = {**DATE, "pattern": "(?=x)"}
    store(env, [PIN, bad], [])
    edited = changes(set_datatype({**PIN, "description": "four to eight digits"}))
    refused = env.client.put("/api/datatypes", json=edited)
    assert refused.status_code == 422
    assert "ISO date" in refused.json()["detail"]
    assert (
        env.client.put("/api/datatypes", json=changes(remove_datatype(DATE_ID))).status_code == 202
    )
    assert env.client.put("/api/datatypes", json=edited).status_code == 202


def test_put_refuses_deleting_what_a_template_uses_naming_the_templates(env: AppEnv):
    uses = template(field("PIN", TEXT, PIN_ID), name="Uses it")
    also = template(path("notes", PIN_ID), name="Also")
    other = template(field("When", TEXT, DATE_ID), name="Other")
    store(env, [PIN, DATE], [uses, also, other])
    response = env.client.put("/api/datatypes", json=changes(remove_datatype(PIN_ID)))
    assert response.status_code == 422
    assert "Uses it, Also" in response.json()["detail"]
    assert "Other" not in response.json()["detail"]
    assert listing(env, "pending") == []
    ## one no template uses goes
    write_notes(env, {"version": 1, "templates": [uses, also], "datatypes": [PIN, DATE]})
    env.reload()
    assert (
        env.client.put("/api/datatypes", json=changes(remove_datatype(DATE_ID))).status_code == 202
    )


def test_put_counts_the_templates_of_a_save_under_way(env: AppEnv):
    store(env, [PIN], [])
    uses = template(field("PIN", TEXT, PIN_ID), name="Uses it")
    assert env.client.put("/api/templates", json=changes(add_template(uses))).status_code == 202
    response = env.client.put("/api/datatypes", json=changes(remove_datatype(PIN_ID)))
    assert response.status_code == 422
    assert "Uses it" in response.json()["detail"]


def test_removing_a_datatype_leaves_every_item_as_it_was(env: AppEnv):
    store(env, [PIN], [], item(("PIN", OFF_FORMAT, TEXT)))
    before = copy.deepcopy(env.item("dt-1"))
    assert (
        env.client.put("/api/datatypes", json=changes(remove_datatype(PIN_ID))).status_code == 202
    )
    assert commit_now(env).status_code == 200
    assert note_sections(env)["datatypes"] == []
    assert env.item("dt-1") == before


def test_put_needs_a_session(env: AppEnv):
    assert env.client.delete("/api/session").status_code in (200, 204)
    assert env.client.get("/api/datatypes").status_code == 401
    assert (
        env.client.put("/api/datatypes", json=changes(remove_datatype(PIN_ID))).status_code == 401
    )
    assert env.client.post("/api/datatypes/check", json={"pattern": "a"}).status_code == 401
    answer = env.client.post(f"/api/datatypes/{PIN_ID}/check", json={"value": "a"})
    assert answer.status_code == 401


def test_a_section_save_is_refused_when_queued_and_when_written(env: AppEnv):
    queue = env.app.state.bw.queue
    with pytest.raises(InvalidOperation):
        change = SetDatatype(op="setDatatype", datatype={"id": PIN_ID, "name": "x"})
        queue.enqueue_section_changes("datatypes", [change], "Save the datatypes")
    operation = {
        "kind": "setToolNoteSection",
        "section": "datatypes",
        "changes": [set_datatype({**PIN, "counterExamples": [CONFORMING]})],
    }
    entry_id = queue.db.add_queue_entry(
        UI, "Save the datatypes", json.dumps(operation), edits_tool_note=True
    )
    before = env.item(env.tool_note_id)["notes"]
    assert commit_now(env).status_code == 200
    assert next(e for e in listing(env) if e["id"] == entry_id)["status"] == "failed"
    assert env.item(env.tool_note_id)["notes"] == before


def test_get_templates_warns_about_an_unknown_and_a_broken_datatype(env: AppEnv):
    bad = {**DATE, "pattern": "(?=x)"}
    uses = template(
        field("a", TEXT, PIN_ID),
        field("b", TEXT, "ffffffff"),
        field("c", TEXT, DATE_ID),
    )
    store(env, [PIN, bad], [uses])
    body = env.client.get("/api/templates").json()
    assert body["templates"] == [uses]
    assert len(body["warnings"]) == 2
    assert "fields.b" in body["warnings"][0]
    assert "ffffffff" in body["warnings"][0]
    assert "ISO date" in body["warnings"][1]
    assert "fields.c" in body["warnings"][1]


def test_get_templates_warns_about_a_template_that_scores_like_an_earlier_one(env: AppEnv):
    first = template(field("a", TEXT))
    second = template(field("a", HIDDEN), name="Second")
    store(env, [], [first, second])
    body = env.client.get("/api/templates").json()
    assert body["warnings"] == [
        "Second: same type and fields as T, which is listed first and takes every match."
    ]


def test_put_templates_takes_a_datatype_and_refuses_a_boolean(env: AppEnv):
    uses = template(field("a", TEXT, PIN_ID))
    response = env.client.put("/api/templates", json=changes(add_template(uses)))
    assert response.status_code == 202
    assert response.json()["templates"] == [uses]
    boolean = template(field("Enabled", BOOLEAN, PIN_ID))
    refused = env.client.put("/api/templates", json=changes(set_template(boolean)))
    assert refused.status_code == 422
    assert "Enabled" in refused.json()["detail"]


def test_a_template_saved_and_committed_scores_through_the_routes(env: AppEnv):
    env.bw.items.append(item(("PIN", OFF_FORMAT, TEXT)))
    env.reload()
    added = add_datatype({"name": "PIN", "pattern": PIN["pattern"]})
    saved = env.client.put("/api/datatypes", json=changes(added))
    made = saved.json()["datatypes"][0]["id"]
    uses = template(field("PIN", TEXT, made))
    assert env.client.put("/api/templates", json=changes(add_template(uses))).status_code == 202
    assert commit_now(env).status_code == 200
    assert "warnings" not in env.client.get("/api/templates").json()
    score = scores_of(env, "dt-1")[0]
    assert score["filled"] == 2.8
    assert score["total"] == 3
    assert score["formats"][0]["datatype"] == "PIN"


def test_check_answers_the_facts_and_a_result_for_everything(env: AppEnv):
    response = env.client.post(
        "/api/datatypes/check",
        json={
            "pattern": PIN["pattern"],
            "examples": [CONFORMING],
            "counterExamples": [OFF_FORMAT, "1" * (MAX_VALUE_LEN + 1)],
            "samples": ["12", CONFORMING],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["maxRepeat"], body["shortest"]) == (8, 4)
    assert body["states"] > 0
    assert body["examples"][0]["score"] == 1
    assert body["examples"][0]["distance"] == 0
    off, too_long = body["counterExamples"]
    assert (off["score"], off["distance"], off["firstEdit"]) == (0.8, 1, 0)
    assert off["expected"] == "a digit"
    assert off["repeatsUsed"] == [4]
    assert off["steps"] > 0
    assert off["tooLong"] is False
    assert (too_long["score"], too_long["distance"], too_long["tooLong"]) == (0, None, True)
    short, full = body["samples"]
    assert (short["distance"], short["score"]) == (2, 0.5)
    assert full["distance"] == 0


def test_check_takes_whole_value(env: AppEnv):
    body = {"pattern": r"\d{4}", "examples": ["id 1234"]}
    whole = env.client.post("/api/datatypes/check", json=body).json()
    assert whole["examples"][0]["distance"] == 3
    part = env.client.post("/api/datatypes/check", json={**body, "wholeValue": False}).json()
    assert part["examples"][0]["distance"] == 0


def test_a_save_stores_whole_value_false_only(env: AppEnv):
    store(env, [PIN], [])
    body = changes(
        set_datatype({**PIN, "wholeValue": False}),
        add_datatype({"name": "A", "pattern": "a", "wholeValue": True}),
    )
    response = env.client.put("/api/datatypes", json=body)
    assert response.status_code == 202, response.text
    assert [d["wholeValue"] for d in response.json()["datatypes"]] == [False, True]
    assert commit_now(env).status_code == 200
    pin, added = note_sections(env)["datatypes"]
    assert pin["wholeValue"] is False
    assert "wholeValue" not in added


def test_check_without_samples_and_with_ignore_case(env: AppEnv):
    body = {"pattern": "abc", "ignoreCase": True, "examples": ["ABC"]}
    answer = env.client.post("/api/datatypes/check", json=body).json()
    assert answer["samples"] == []
    assert answer["examples"][0]["distance"] == 0
    assert answer["counterExamples"] == []


@pytest.mark.parametrize(
    ("pattern", "column"),
    [("a(?=b)", 3), ("", None)],
)
def test_check_answers_a_pattern_error_with_its_column(
    env: AppEnv, pattern: str, column: int | None
):
    response = env.client.post("/api/datatypes/check", json={"pattern": pattern})
    assert response.status_code == 200
    error = response.json()["error"]
    assert error["column"] == column
    assert error["message"]


@pytest.mark.parametrize(
    "body",
    [
        "the-secret-being-typed",
        ["the-secret-being-typed"],
        {"pattern": 5, "samples": ["the-secret-being-typed"]},
        {"pattern": "a", "ignoreCase": "yes", "samples": ["the-secret-being-typed"]},
        {"pattern": "a", "wholeValue": "no", "samples": ["the-secret-being-typed"]},
        {"pattern": "a", "samples": "the-secret-being-typed"},
        {"pattern": "a", "samples": ["the-secret-being-typed", 5]},
        {"pattern": "a", "samples": ["the-secret-being-typed"] * (MAX_EXAMPLES + 1)},
        {"pattern": "a", "examples": "the-secret-being-typed"},
        {"pattern": "a", "counterExamples": ["the-secret-being-typed", 5]},
        {
            "pattern": "a",
            "examples": ["x"] * (MAX_EXAMPLES + 1),
            "samples": ["the-secret-being-typed"],
        },
    ],
)
def test_a_check_refused_never_echoes_what_it_got(env: AppEnv, body: Any):
    response = env.client.post("/api/datatypes/check", json=body)
    assert response.status_code == 422
    leaked = "the-secret-being-typed" in response.text
    assert not leaked


def test_a_body_that_is_no_json_is_refused(env: AppEnv):
    response = env.client.post("/api/datatypes/check", content=b"{no json")
    assert response.status_code == 422


def test_check_value_answers_the_result_of_a_stored_datatype(env: AppEnv):
    store(env, [PIN], [])
    response = env.client.post(f"/api/datatypes/{PIN_ID}/check", json={"value": OFF_FORMAT})
    assert response.status_code == 200
    body = response.json()
    assert (body["score"], body["distance"], body["firstEdit"]) == (0.8, 1, 0)
    assert body["expected"] == "a digit"
    good = env.client.post(f"/api/datatypes/{PIN_ID}/check", json={"value": CONFORMING}).json()
    assert (good["score"], good["distance"], good["firstEdit"]) == (1, 0, None)
    too_long = {"value": "1" * (MAX_VALUE_LEN + 1)}
    long_answer = env.client.post(f"/api/datatypes/{PIN_ID}/check", json=too_long).json()
    assert (long_answer["score"], long_answer["tooLong"]) == (0, True)


def test_check_value_of_an_unknown_and_a_broken_datatype_and_a_bad_value(env: AppEnv):
    store(env, [{**PIN, "pattern": "(?=x)"}], [])
    missing = env.client.post("/api/datatypes/ffffffff/check", json={"value": "x"})
    assert missing.status_code == 404
    broken = env.client.post(f"/api/datatypes/{PIN_ID}/check", json={"value": "x"})
    assert broken.status_code == 422
    assert "broken" in broken.json()["detail"]
    for body in ({"value": 5}, {}, "the-secret-being-typed"):
        refused = env.client.post(f"/api/datatypes/{PIN_ID}/check", json=body)
        assert refused.status_code == 422
        leaked = "the-secret-being-typed" in refused.text
        assert not leaked


def test_nothing_about_a_value_reaches_the_logs_or_the_database(
    env: AppEnv, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    typed = "a-secret-being-typed-99"
    in_vault = "a-secret-in-the-vault-77"
    store(env, [PIN], [template(field("PIN", HIDDEN, PIN_ID))], item(("PIN", in_vault, HIDDEN)))
    env.client.get("/api/template-matches")
    env.client.post(f"/api/datatypes/{PIN_ID}/check", json={"value": typed})
    env.client.post("/api/datatypes/check", json={"pattern": "a", "samples": [typed]})
    raw = b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    leaked = [
        typed in caplog.text,
        in_vault in caplog.text,
        typed.encode() in raw,
        in_vault.encode() in raw,
    ]
    assert not any(leaked)
