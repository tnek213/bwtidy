"""The password generator settings: the section, the query to bw, generateLeaf and the routes."""

import copy
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import bwtidy.queue.autocommit as autocommit
from bwtidy.operations import InvalidOperation
from bwtidy.queue.view import GENERATED, GENERATOR_SUMMARY
from bwtidy.sections.generator import (
    DEFAULT_OPTIONS,
    InvalidGenerator,
    PassphraseOptions,
    PasswordOptions,
    SetDefault,
    SetItem,
    bw_query,
    parse_generator,
    parse_options,
)
from tests.support.app_env import AppEnv, logged_in
from tests.support.queue_helpers import approve, commit_now, enqueue_ui, listing, vault_unreachable
from tests.support.quiet import assert_absent, assert_present
from tests.support.sample_vault import SECRETS
from tests.support.sections_helpers import note_sections, write_notes
from tests.support.writes_helpers import create

SHORT = {
    "type": "password",
    "length": 12,
    "uppercase": True,
    "lowercase": True,
    "number": True,
    "special": False,
    "minNumber": 2,
    "minSpecial": 0,
    "ambiguous": True,
}
WORDS = {
    "type": "passphrase",
    "words": 4,
    "separator": "-",
    "capitalize": True,
    "includeNumber": False,
}
SHORT_QUERY_LENGTH = "12"
TODAY = {
    "type": "password",
    "length": 24,
    "uppercase": True,
    "lowercase": True,
    "number": True,
    "special": True,
    "minNumber": 1,
    "minSpecial": 1,
    "ambiguous": False,
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppEnv]:
    ## the auto-commit task never gets a turn on its own here, the tests ask for each commit
    monkeypatch.setattr(autocommit, "TICK", 3600.0)
    yield from logged_in(tmp_path)


def store_generator(env: AppEnv, generator: dict[str, Any]) -> None:
    write_notes(env, {"version": 1, "templates": [], "generator": generator})
    env.reload()


def generate(env: AppEnv, item_id: str, column: str) -> dict[str, Any]:
    return env.op("generateLeaf", item_id, column=column)


def field_value(item: dict[str, Any], name: str) -> Any:
    return next(f["value"] for f in item["fields"] if f["name"] == name)


## the limits


@pytest.mark.parametrize(
    "options",
    [
        {"type": "password", "length": 5},
        {"type": "password", "length": 128},
        {"type": "password", "minNumber": 9, "minSpecial": 9},
        {"type": "password", "minNumber": 0, "minSpecial": 0},
        {"type": "password", "uppercase": False, "lowercase": False, "special": False},
        {"type": "passphrase", "words": 3},
        {"type": "passphrase", "words": 20},
        {"type": "passphrase", "separator": ""},
        {"type": "passphrase", "separator": " "},
    ],
)
def test_options_within_the_limits(options: dict[str, Any]):
    assert parse_options(options).type == options["type"]


@pytest.mark.parametrize(
    "options",
    [
        {"type": "password", "length": 4},
        {"type": "password", "length": 129},
        {"type": "password", "length": "12"},
        {"type": "password", "minNumber": 10},
        {"type": "password", "minNumber": -1},
        {"type": "password", "minSpecial": 10},
        {"type": "password", "uppercase": 1},
        {"type": "password", "uppercase": False, "lowercase": False}
        | {"number": False, "special": False},
        {"type": "password", "words": 4},
        {"type": "passphrase", "words": 2},
        {"type": "passphrase", "words": 21},
        {"type": "passphrase", "separator": "--"},
        {"type": "passphrase", "length": 12},
        {"type": "pin"},
        {},
    ],
)
def test_options_outside_the_limits(options: dict[str, Any]):
    with pytest.raises(InvalidGenerator):
        parse_options(options)


def test_minimums_that_do_not_fit_the_length_are_refused():
    ## bw would silently make this one longer than asked
    with pytest.raises(InvalidGenerator, match="cannot hold the minimums"):
        parse_options({"type": "password", "length": 8, "minNumber": 4, "minSpecial": 4})
    ## 1 + 1 + 4 + 2 fit exactly
    parse_options({"type": "password", "length": 8, "minNumber": 4, "minSpecial": 2})
    ## a minimum of a class that is off counts for nothing
    parse_options({"type": "password", "length": 5, "special": False, "minSpecial": 9})


def test_an_error_names_the_option_and_never_the_value():
    with pytest.raises(InvalidGenerator) as error:
        parse_options({"type": "passphrase", "separator": "tooLongSeparator"})
    assert "separator" in str(error.value)
    assert_absent("tooLongSeparator", str(error.value), "the error message")


def test_the_section_parses_and_dumps_as_it_was():
    section = {"default": WORDS, "items": {"login-1": SHORT}}
    assert parse_generator(section).dump() == section


def test_without_a_section_the_default_is_todays():
    settings = parse_generator(None)
    assert settings.default_options() == DEFAULT_OPTIONS
    assert settings.default_options().model_dump() == TODAY
    assert settings.dump() == {"items": {}}


def test_without_a_default_the_items_keep_their_own():
    settings = parse_generator({"items": {"login-1": SHORT}})
    assert settings.options_for("login-1").model_dump() == SHORT
    assert settings.options_for("note-1") == DEFAULT_OPTIONS


def test_the_item_setting_wins_over_the_default():
    settings = parse_generator({"default": WORDS, "items": {"login-1": SHORT}})
    assert settings.options_for("login-1").model_dump() == SHORT
    assert settings.options_for("note-1").model_dump() == WORDS


## the query to bw


def test_a_password_maps_to_the_query():
    options = PasswordOptions.model_validate(SHORT)
    assert bw_query(options) == {
        "length": "12",
        "uppercase": "true",
        "lowercase": "true",
        "number": "true",
        "special": "false",
        "minNumber": "2",
        "minSpecial": "0",
        "ambiguous": "true",
    }


def test_a_minimum_of_a_class_that_is_off_goes_as_zero():
    options = PasswordOptions(type="password", number=False, minNumber=3)
    assert bw_query(options)["minNumber"] == "0"


def test_a_passphrase_maps_to_the_query():
    options = PassphraseOptions.model_validate(WORDS)
    assert bw_query(options) == {
        "passphrase": "true",
        "words": "4",
        "separator": "-",
        "capitalize": "true",
        "includeNumber": "false",
    }


@pytest.mark.parametrize(
    ("separator", "sent"), [(" ", "space"), ("", "empty"), ("_", "_"), ("&", "&")]
)
def test_the_separator_goes_as_bw_wants_it(separator: str, sent: str):
    options = PassphraseOptions(type="passphrase", separator=separator)
    assert bw_query(options)["separator"] == sent


def test_the_query_reaches_bw_serve_encoded(env: AppEnv):
    response = env.client.post("/api/generator/example", json={**WORDS, "separator": "&"})
    assert response.status_code == 200, response.text
    assert env.bw.generate_queries == [
        bw_query(PassphraseOptions.model_validate({**WORDS, "separator": "&"}))
    ]


## createItem


def test_create_item_uses_todays_default_without_a_section(env: AppEnv):
    response = env.post(create(type=1, name="Generated", generatePassword=True))
    assert response.status_code == 200, response.text
    [query] = env.bw.generate_queries
    assert query == bw_query(DEFAULT_OPTIONS)


def test_create_item_uses_the_default(env: AppEnv):
    store_generator(env, {"default": WORDS, "items": {}})
    response = env.post(create(type=1, name="Generated", generatePassword=True))
    assert response.status_code == 200, response.text
    assert env.bw.generate_queries == [bw_query(PassphraseOptions.model_validate(WORDS))]
    stored = env.bw.posted[-1]["login"]["password"] == env.bw.generated[0]
    assert stored


def test_a_section_save_in_the_same_commit_counts_for_a_later_create(env: AppEnv):
    default = SetDefault(op="setDefault", options=WORDS)
    env.queue.enqueue_section_changes("generator", [default], GENERATOR_SUMMARY)
    enqueue_ui(env, create(type=1, name="Generated", generatePassword=True))
    assert commit_now(env).json()["failed"] == []
    assert env.bw.generate_queries == [bw_query(PassphraseOptions.model_validate(WORDS))]


## generateLeaf


def test_generate_leaf_on_the_password_with_the_default(env: AppEnv):
    store_generator(env, {"default": WORDS, "items": {}})
    response = env.post(generate(env, "login-1", "login.password"))
    assert response.status_code == 200, response.text
    assert env.bw.generate_queries == [bw_query(PassphraseOptions.model_validate(WORDS))]
    [value] = env.bw.generated
    item = env.item("login-1")
    written = item["login"]["password"] == value
    assert written
    assert field_value(item, "PIN") == "pin-secret-4711"
    assert_absent(value, response.text, "the response")


def test_generate_leaf_on_a_hidden_field_with_the_items_setting(env: AppEnv):
    store_generator(env, {"default": WORDS, "items": {"login-1": SHORT}})
    response = env.post(generate(env, "login-1", "fields.PIN"))
    assert response.status_code == 200, response.text
    assert env.bw.generate_queries[0]["length"] == SHORT_QUERY_LENGTH
    [value] = env.bw.generated
    item = env.item("login-1")
    written = field_value(item, "PIN") == value
    assert written
    assert next(f["type"] for f in item["fields"] if f["name"] == "PIN") == 1
    assert item["login"]["password"] == "pw-login-secret-e0e7"


def test_generate_leaf_on_the_second_occurrence_of_a_hidden_field(env: AppEnv):
    response = env.post(generate(env, "note-1", "fields.WPA key#2"))
    assert response.status_code == 200, response.text
    [value] = env.bw.generated
    values = [f["value"] for f in env.item("note-1")["fields"] if f["name"] == "WPA key"]
    written = values == ["wpa-main-secret", value]
    assert written


def test_generate_leaf_on_a_missing_hidden_field_creates_it_hidden(env: AppEnv):
    response = env.post(generate(env, "login-1", "fields.New key"))
    assert response.status_code == 200, response.text
    new = next(f for f in env.item("login-1")["fields"] if f["name"] == "New key")
    assert new["type"] == 1


@pytest.mark.parametrize(
    ("item_id", "column"),
    [
        ("login-1", "fields.User"),
        ("login-1", "fields.Linked"),
        ("login-1", "notes"),
        ("login-1", "name"),
        ("login-1", "login.username"),
        ("login-1", "login.totp"),
        ("login-1", "login.uris"),
        ("card-1", "card.number"),
        ("note-1", "notes"),
        ("note-1", "fields.Enabled"),
        ## not a login
        ("note-1", "login.password"),
    ],
)
def test_generate_leaf_is_refused_on_other_columns(env: AppEnv, item_id: str, column: str):
    op = generate(env, item_id, column)
    for response in (env.post(op), enqueue_ui(env, op)):
        assert response.status_code == 422, response.text
    assert env.bw.generate_queries == []
    assert listing(env) == []


def test_generate_leaf_is_refused_on_the_tool_note(env: AppEnv):
    note_id = env.tool_note_id
    op = generate(env, note_id, "fields.backup-password")
    for response in (env.post(op), enqueue_ui(env, op)):
        assert response.status_code == 422, response.text
    assert env.bw.generate_queries == []


def test_a_stale_revision_date_is_a_conflict(env: AppEnv):
    op = {**generate(env, "login-1", "login.password"), "revisionDate": "2020-01-01T00:00:00.000Z"}
    assert env.post(op).status_code == 409
    assert env.bw.generate_queries == []


def test_invalid_settings_in_the_note_give_the_default(
    env: AppEnv, caplog: pytest.LogCaptureFixture
):
    store_generator(env, {"default": {"type": "password", "length": 1}})
    response = env.post(generate(env, "login-1", "login.password"))
    assert response.status_code == 200, response.text
    assert env.bw.generate_queries == [bw_query(DEFAULT_OPTIONS)]
    assert_present("not valid", caplog.text, "the log")


def test_the_value_is_only_generated_in_the_queue_and_the_change_log(
    env: AppEnv, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    response = enqueue_ui(env, generate(env, "login-1", "login.password"))
    assert response.status_code == 201, response.text
    assert env.bw.generated == []
    [entry] = listing(env)
    assert entry["operation"] == {
        "kind": "generateLeaf",
        "itemId": "login-1",
        "revisionDate": entry["operation"]["revisionDate"],
        "column": "login.password",
    }
    [row] = entry["diff"]
    assert (row["column"], row["before"], row["after"]) == (
        "login.password",
        {"masked": True},
        GENERATED,
    )
    assert entry["summary"] == 'Generate a new login.password for "Example Mail"'

    assert commit_now(env).json()["failed"] == []
    [value] = env.bw.generated
    written = env.item("login-1")["login"]["password"] == value
    assert written

    [applied] = listing(env, "applied")
    assert applied["diff"] == [row]
    [log_row] = [r for r in env.change_log() if r["operationKind"] == "generateLeaf"]
    assert log_row["itemIds"] == ["login-1"]
    assert log_row["result"] == "ok"
    shown = json.dumps(listing(env)) + json.dumps(env.change_log())
    shown += env.client.get("/api/grid").text + caplog.text
    leaked = value in shown
    assert not leaked
    database = b"".join(p.read_bytes() for p in tmp_path.glob("*.db*") if p.is_file())
    in_database = value.encode() in database
    assert not in_database


## conversions


def test_a_conversion_moves_the_items_setting(env: AppEnv):
    store_generator(env, {"default": WORDS, "items": {"note-1": SHORT, "login-1": SHORT}})
    op = env.op("convertItem", "note-1", newType=1, mapping={})
    response = env.post(op)
    assert response.status_code == 200, response.text
    [row] = [r for r in env.change_log() if r["operationKind"] == "convertItem"]
    [new_id] = row["createdItemIds"]
    section = note_sections(env)
    assert section["generator"]["default"] == WORDS
    assert section["generator"]["items"] == {new_id: SHORT, "login-1": SHORT}
    assert section["templates"] == []
    answer = env.client.get("/api/generator").json()
    assert answer["items"] == {new_id: SHORT, "login-1": SHORT}


def test_a_move_to_personal_moves_the_items_setting(env: AppEnv):
    store_generator(env, {"items": {"note-1": SHORT}})
    assert env.post(env.op("moveToPersonal", "note-1")).status_code == 200
    [row] = [r for r in env.change_log() if r["operationKind"] == "moveToPersonal"]
    [new_id] = row["createdItemIds"]
    assert note_sections(env)["generator"]["items"] == {new_id: SHORT}


def test_a_conversion_of_an_item_without_a_setting_leaves_the_note_alone(env: AppEnv):
    store_generator(env, {"default": WORDS, "items": {"login-1": SHORT}})
    before = copy.deepcopy(env.item(env.tool_note_id))
    assert env.post(env.op("convertItem", "note-1", newType=1, mapping={})).status_code == 200
    assert env.item(env.tool_note_id)["notes"] == before["notes"]
    assert env.tool_note_id not in [item_id for item_id, _ in env.bw.put]


def set_item(env: AppEnv, item_id: str, options: dict[str, Any]) -> int:
    """Queue a setting of one item without starting a commit."""
    change = SetItem(op="setItem", itemId=item_id, options=options)
    return env.queue.enqueue_section_changes("generator", [change], GENERATOR_SUMMARY)


def test_a_setting_saved_before_a_conversion_lands_on_the_new_id(env: AppEnv):
    """The save must not write the setting back under the old id, where the next save would
    drop it."""
    store_generator(env, {"items": {"note-1": SHORT}})
    entry_id = set_item(env, "note-1", WORDS)
    assert env.post(env.op("convertItem", "note-1", newType=1, mapping={})).status_code == 200
    [row] = [r for r in env.change_log() if r["operationKind"] == "convertItem"]
    [new_id] = row["createdItemIds"]
    ## the conversion moved the stored setting, the queued one shows on the new id too
    assert note_sections(env)["generator"]["items"] == {new_id: SHORT}
    assert env.client.get("/api/generator").json()["items"] == {new_id: WORDS}
    assert approve(env, entry_id).json()["applied"] == [entry_id]
    assert note_sections(env)["generator"]["items"] == {new_id: WORDS}
    ## and the next save keeps it
    assert env.client.put("/api/generator/default", json=SHORT).status_code == 202
    assert commit_now(env).json()["failed"] == []
    assert note_sections(env)["generator"] == {"default": SHORT, "items": {new_id: WORDS}}


## the routes


def test_two_saves_built_on_the_same_settings_both_land(env: AppEnv):
    store_generator(env, {"items": {"login-1": SHORT}})
    ## two tabs, each with the settings as they were, the later one committed first
    first = set_item(env, "note-1", WORDS)
    default = SetDefault(op="setDefault", options=WORDS)
    second = env.queue.enqueue_section_changes("generator", [default], GENERATOR_SUMMARY)
    assert approve(env, second).json()["applied"] == [second]
    assert approve(env, first).json()["applied"] == [first]
    assert note_sections(env)["generator"] == {
        "default": WORDS,
        "items": {"login-1": SHORT, "note-1": WORDS},
    }


def test_the_saving_view_folds_every_pending_save(env: AppEnv):
    store_generator(env, {"items": {"login-1": SHORT}})
    vault_unreachable(env, True)
    assert env.client.put("/api/generator/items/note-1", json=WORDS).status_code == 202
    assert env.client.delete("/api/generator/items/login-1").status_code == 202
    response = env.client.put("/api/generator/default", json=SHORT)
    assert response.json()["items"] == {"note-1": WORDS}
    assert commit_now(env).status_code == 502
    answer = env.client.get("/api/generator").json()
    assert answer == {"default": SHORT, "items": {"note-1": WORDS}, "saving": True}
    ## the queue shows each save on top of those queued before it
    *_, last = listing(env)
    [row] = last["diff"]
    before, after = json.loads(row["before"]), json.loads(row["after"])
    assert before["generator"] == {"items": {"note-1": WORDS}}
    assert after["generator"] == {"default": SHORT, "items": {"note-1": WORDS}}
    vault_unreachable(env, False)
    assert commit_now(env).json()["failed"] == []
    assert note_sections(env)["generator"] == {"default": SHORT, "items": {"note-1": WORDS}}
    assert env.client.get("/api/generator").json()["saving"] is False


def test_get_fills_in_the_default(env: AppEnv):
    answer = env.client.get("/api/generator").json()
    assert answer == {"default": TODAY, "items": {}, "saving": False}


def test_put_default_queues_the_section_and_a_commit_writes_it(env: AppEnv):
    ## the commit the save starts cannot run yet, so the entry stays visible as saving
    vault_unreachable(env, True)
    response = env.client.put("/api/generator/default", json=WORDS)
    assert response.status_code == 202, response.text
    assert response.json()["default"] == WORDS
    assert commit_now(env).status_code == 502
    entry_id = response.json()["entryId"]
    [entry] = listing(env)
    assert (entry["id"], entry["source"]) == (entry_id, "ui")
    assert entry["summary"] == GENERATOR_SUMMARY
    assert entry["operation"]["kind"] == "setToolNoteSection"
    assert entry["operation"]["section"] == "generator"
    saving = env.client.get("/api/generator").json()
    assert (saving["default"], saving["saving"]) == (WORDS, True)
    vault_unreachable(env, False)
    assert commit_now(env).json()["applied"] == [entry_id]
    assert note_sections(env)["generator"] == {"default": WORDS, "items": {}}
    answer = env.client.get("/api/generator").json()
    assert (answer["default"], answer["saving"]) == (WORDS, False)


def grid_rows_with_own_generator(env: AppEnv) -> list[str]:
    rows = env.client.get("/api/grid").json()["rows"]
    return sorted(r["id"] for r in rows if r.get("ownGenerator"))


def test_the_grid_row_carries_ownGenerator_while_the_item_has_a_setting(env: AppEnv):
    rows = env.client.get("/api/grid").json()["rows"]
    assert all("ownGenerator" not in r for r in rows)

    assert env.client.put("/api/generator/items/login-1", json=SHORT).status_code == 202
    ## a save under way shows at once
    assert grid_rows_with_own_generator(env) == ["login-1"]
    assert commit_now(env).json()["failed"] == []
    assert grid_rows_with_own_generator(env) == ["login-1"]
    ## the default is no setting of an item
    assert env.client.put("/api/generator/default", json=WORDS).status_code == 202
    assert grid_rows_with_own_generator(env) == ["login-1"]

    assert env.client.delete("/api/generator/items/login-1").status_code == 202
    assert grid_rows_with_own_generator(env) == []
    assert commit_now(env).json()["failed"] == []
    assert grid_rows_with_own_generator(env) == []


def test_a_save_keeps_the_other_sections(env: AppEnv):
    write_notes(env, {"version": 1, "templates": [], "toFill": [1]})
    env.reload()
    assert env.client.put("/api/generator/default", json=WORDS).status_code == 202
    assert commit_now(env).json()["failed"] == []
    sections = note_sections(env)
    assert (sections["templates"], sections["toFill"]) == ([], [1])


def test_put_and_delete_of_an_item(env: AppEnv):
    response = env.client.put("/api/generator/items/login-1", json=SHORT)
    assert response.status_code == 202, response.text
    assert env.client.put("/api/generator/items/note-1", json=WORDS).status_code == 202
    assert commit_now(env).json()["failed"] == []
    assert listing(env, "failed") == []
    answer = env.client.get("/api/generator").json()
    assert answer["items"] == {"login-1": SHORT, "note-1": WORDS}
    assert answer["default"] == TODAY
    ## a save without a default does not store today's as one
    assert "default" not in note_sections(env)["generator"]

    response = env.client.delete("/api/generator/items/login-1")
    assert response.status_code == 202, response.text
    assert commit_now(env).json()["failed"] == []
    assert env.client.get("/api/generator").json()["items"] == {"note-1": WORDS}

    ## back to the default already: nothing is queued
    before = len(listing(env))
    response = env.client.delete("/api/generator/items/login-1")
    assert response.status_code == 202, response.text
    assert response.json()["entryId"] is None
    assert len(listing(env)) == before


def test_settings_of_items_that_are_gone_are_dropped_with_the_next_save(env: AppEnv):
    store_generator(env, {"items": {"gone": SHORT, "login-1": SHORT}})
    assert env.client.get("/api/generator").json()["items"] == {"gone": SHORT, "login-1": SHORT}
    assert env.client.put("/api/generator/default", json=WORDS).status_code == 202
    assert commit_now(env).json()["failed"] == []
    assert note_sections(env)["generator"]["items"] == {"login-1": SHORT}


def test_a_trashed_item_counts_as_gone(env: AppEnv):
    store_generator(env, {"items": {"login-1": SHORT}})
    assert env.post(env.op("trashItem", "login-1")).status_code == 200
    assert env.client.put("/api/generator/default", json=WORDS).status_code == 202
    assert commit_now(env).json()["failed"] == []
    assert note_sections(env)["generator"]["items"] == {}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("put", "/api/generator/default"),
        ("put", "/api/generator/items/login-1"),
        ("post", "/api/generator/example"),
    ],
)
def test_options_outside_the_limits_are_422(env: AppEnv, method: str, path: str):
    body = {"type": "password", "length": 3}
    response = env.client.request(method, path, json=body)
    assert response.status_code == 422
    assert "length" in response.json()["detail"]
    assert listing(env) == []
    assert env.bw.generate_queries == []


def test_a_setting_for_an_unknown_item_or_the_tool_note_is_refused(env: AppEnv):
    assert env.client.put("/api/generator/items/nope", json=SHORT).status_code == 404
    response = env.client.put(f"/api/generator/items/{env.tool_note_id}", json=SHORT)
    assert response.status_code == 422
    assert listing(env) == []


def test_invalid_stored_settings_read_as_none_and_say_so(env: AppEnv):
    store_generator(env, {"default": {"type": "password", "length": 1}})
    answer = env.client.get("/api/generator").json()
    assert answer["default"] == TODAY
    assert "length" in answer["error"]
    ## the next save starts over
    assert env.client.put("/api/generator/default", json=WORDS).status_code == 202
    assert commit_now(env).json()["failed"] == []
    assert note_sections(env)["generator"]["default"] == WORDS


def test_an_invalid_change_is_refused_when_queued(env: AppEnv):
    with pytest.raises(InvalidOperation, match="length"):
        default = SetDefault(op="setDefault", options={"type": "password", "length": 1})
        env.queue.enqueue_section_changes("generator", [default], GENERATOR_SUMMARY)
    assert listing(env) == []


def test_a_change_the_queue_refuses_is_422_with_the_reason_alone(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    def refuse(*_args: Any) -> int:
        raise InvalidOperation("Operation 1 (setDefault): too short", 0, "too short")

    monkeypatch.setattr(env.queue, "enqueue_section_changes", refuse)
    response = env.client.put("/api/generator/default", json=WORDS)
    assert response.status_code == 422
    assert response.json() == {"detail": "too short"}
    assert listing(env) == []


def test_the_example_is_answered_and_kept_nowhere(
    env: AppEnv, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    response = env.client.post("/api/generator/example", json=SHORT)
    assert response.status_code == 200, response.text
    [value] = env.bw.generated
    answered = response.json() == {"value": value}
    assert answered
    assert listing(env) == []
    leaked = value in caplog.text + env.client.get("/api/change-log").text
    assert not leaked
    database = b"".join(p.read_bytes() for p in tmp_path.glob("*.db*") if p.is_file())
    in_database = value.encode() in database
    assert not in_database


def test_a_failing_example_says_so_without_bws_words(env: AppEnv):
    env.bw.fail_at.add(("GET", "/generate"))
    response = env.client.post("/api/generator/example", json=SHORT)
    assert response.status_code == 502
    assert response.json()["detail"] == "bw serve generated no example"


def test_the_routes_need_a_session(env: AppEnv):
    assert env.client.delete("/api/session").status_code in (200, 204)
    assert env.client.get("/api/generator").status_code == 401
    assert env.client.post("/api/generator/example", json=SHORT).status_code == 401


def test_no_secret_of_the_vault_is_in_an_answer(env: AppEnv):
    store_generator(env, {"items": {"login-1": SHORT}})
    enqueue_ui(env, generate(env, "login-1", "fields.PIN"))
    shown = env.client.get("/api/generator").text + json.dumps(listing(env))
    leaked = [secret for secret in SECRETS if secret in shown]
    assert not leaked
