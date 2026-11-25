"""The formats of the vault's values: the results kept by item, column and pattern, and the
matches of every cached item (vault/formats.py)."""

from collections.abc import Iterator
from pathlib import Path

import pytest

import bwtidy.queue.autocommit as autocommit
from bwtidy.sections.templates import parse_templates
from bwtidy.vault.formats import template_matches
from tests.support.app_env import AppEnv, logged_in
from tests.support.datatypes_helpers import (
    CONFORMING,
    HIDDEN,
    OFF_FORMAT,
    PIN,
    PIN_ID,
    TEXT,
    field,
    item,
    path,
    scores_of,
    store,
    template,
)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppEnv]:
    ## the auto-commit task never gets a turn on its own here, the tests ask for each commit
    monkeypatch.setattr(autocommit, "TICK", 3600.0)
    yield from logged_in(tmp_path)


def test_results_are_kept_by_item_column_and_pattern_and_go_with_a_reload(env: AppEnv):
    store(env, [PIN], [template(field("PIN", TEXT, PIN_ID))], item(("PIN", OFF_FORMAT, TEXT)))
    formats = env.app.state.bw.formats
    ## computed by the load already, before anyone asks
    key = ("dt-1", "fields.PIN", PIN["pattern"], False, True)
    result = formats.results[key]
    assert result is not None
    assert result.distance == 1
    assert scores_of(env, "dt-1")[0]["filled"] == 2.8
    env.item("dt-1")["fields"][0]["value"] = CONFORMING
    env.reload()
    assert scores_of(env, "dt-1")[0]["filled"] == 3


def test_a_field_name_pattern_keeps_a_result_per_field_and_shows_the_lowest(env: AppEnv):
    users = {"kind": "fieldPattern", "pattern": "pin_*", "fieldType": TEXT, "datatype": PIN_ID}
    fields = (("pin_a", CONFORMING, TEXT), ("pin_b", OFF_FORMAT, TEXT))
    store(env, [PIN], [template(users)], item(*fields))
    formats = env.app.state.bw.formats
    for column in ("fields.pin_a", "fields.pin_b"):
        assert formats.results[("dt-1", column, PIN["pattern"], False, True)] is not None
    [score] = scores_of(env, "dt-1")
    assert (score["filled"], score["total"], score["missing"]) == (2.8, 3, [])
    assert [entry["column"] for entry in score["formats"]] == ["fields.pin_b"]


def test_results_go_with_the_session(env: AppEnv):
    store(env, [PIN], [template(field("PIN", TEXT, PIN_ID))], item(("PIN", OFF_FORMAT, TEXT)))
    formats = env.app.state.bw.formats
    assert formats.results
    assert env.client.delete("/api/session").status_code == 204
    assert formats.results == {}


def test_the_tool_note_is_never_scored(env: AppEnv):
    coordinate = path("notes", PIN_ID)
    store(env, [PIN], [template(coordinate)])
    formats = env.app.state.bw.formats
    note_id = env.tool_note_id
    assert all(key[0] != note_id for key in formats.results)


def test_mcp_goes_by_the_type_in_the_item_for_a_field_the_template_calls_text(env: AppEnv):
    store(env, [PIN], [template(field("PIN", TEXT, PIN_ID))], item(("PIN", OFF_FORMAT, HIDDEN)))
    cache = env.app.state.bw.session.cache
    formats = env.app.state.bw.formats
    templates = parse_templates([template(field("PIN", TEXT, PIN_ID))])
    score = template_matches(cache, formats, templates, for_mcp=True)["dt-1"]["scores"][0]
    assert (score["filled"], score["total"]) == (3, 3)
    assert "formats" not in score
    ui = template_matches(cache, formats, templates)["dt-1"]["scores"][0]
    assert ui["filled"] == 2.8
    assert ui["formats"][0]["score"] == 0.8
