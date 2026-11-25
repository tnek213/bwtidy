"""The in-memory statistics of the datatype checks and GET /api/statistics."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import bwtidy.items.patterns as patterns
import bwtidy.queue.autocommit as autocommit
from bwtidy.items.coordinates import Lookups, flatten
from bwtidy.items.patterns import MAX_VALUE_LEN, FormatResult, compile_pattern
from bwtidy.sections.datatypes import load_datatypes
from bwtidy.vault.statistics import MAX_ROWS, Measure, Statistics
from tests.support.app_env import AppEnv, logged_in
from tests.support.datatypes_helpers import (
    DATE,
    HIDDEN,
    LOGIN,
    PIN,
    PIN_ID,
    TEXT,
    URL,
    URL_ID,
    a_login,
    field,
    item,
    path,
    store,
    template,
)
from tests.support.mcp_helpers import (
    NOTE_WITH_USER,
    PROPOSALS,
    READ_TOOLS,
    data,
    enable,
    list_tools,
)
from tests.support.normalize_data import ALL_SECRETS, EXTRA
from tests.support.sections_helpers import write_notes

## the sample vault has a field called PIN
FIELD = "Pin code"
OFF_FORMAT = "off-format-text-41"
HIDDEN_VALUE = "hidden-text-value-58"
SHORTEST_COMPARED = 6
## what only the statistics hold: an MCP answer with one of these has read them
STATISTICS_KEYS = (
    "statistics",
    "repeatsUsed",
    "steps",
    "valuesScored",
    "valuesTooLong",
    "slowestMs",
    "lastReloadMs",
)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppEnv]:
    monkeypatch.setattr(autocommit, "TICK", 3600.0)
    yield from logged_in(tmp_path)


def answer(env: AppEnv) -> dict[str, Any]:
    response = env.client.get("/api/statistics")
    assert response.status_code == 200
    return response.json()


def rows_of(env: AppEnv, item_id: str) -> list[dict[str, Any]]:
    return [row for row in answer(env)["rows"] if row["itemId"] == item_id]


def counters_of(env: AppEnv, name: str) -> dict[str, Any]:
    return next(d for d in answer(env)["datatypes"] if d["name"] == name)


def pin_template(*extra: dict[str, Any]) -> dict[str, Any]:
    return template(field(FIELD, TEXT, PIN_ID), *extra)


def test_scoring_fills_the_collector_without_any_value(env: AppEnv):
    store(
        env,
        [PIN, URL],
        [
            template(field(FIELD, TEXT, PIN_ID), field("Code", HIDDEN, PIN_ID)),
            template(path("login.uris", URL_ID), name="U", type_=LOGIN),
        ],
        item((FIELD, OFF_FORMAT, TEXT), ("Code", HIDDEN_VALUE, HIDDEN), item_id="dt-1"),
    )
    [code, pin] = sorted(rows_of(env, "dt-1"), key=lambda r: r["column"])
    assert pin["item"] == "Item dt-1"
    assert pin["column"] == f"fields.{FIELD}"
    assert pin["datatype"] == "PIN"
    assert pin["length"] == len(OFF_FORMAT)
    assert pin["distance"] > 0
    assert pin["steps"] > 0
    assert pin["ms"] >= 0
    assert pin["tooLong"] is False
    assert len(pin["repeatsUsed"]) == 1
    facts = compile_pattern(PIN["pattern"])
    assert pin["states"] == facts.states
    assert (pin["maxRepeat"], pin["shortest"]) == (8, 4)
    assert code["length"] == len(HIDDEN_VALUE)

    text = json.dumps(answer(env))
    values = [
        value
        for vault_item in env.bw.items
        for coordinate, value in flatten(vault_item, Lookups()).items()
        if coordinate.display != "name"
        for value in (value if isinstance(value, list) else [value])
        if isinstance(value, str) and len(value) >= SHORTEST_COMPARED
    ]
    assert OFF_FORMAT in values
    assert HIDDEN_VALUE in values
    ## a bool, so a failure never prints the value
    found = any(value in text for value in [*values, *ALL_SECRETS])
    assert not found


def test_the_counters_and_a_reload(env: AppEnv):
    store(env, [PIN], [pin_template()], item((FIELD, "1234", TEXT)))
    first = counters_of(env, "PIN")
    assert first["valuesScored"] == 1
    assert first["valuesTooLong"] == 0
    assert first["slowestMs"] >= 0
    assert first["lastReloadMs"] >= first["slowestMs"]
    before = answer(env)["reloads"]

    env.reload()
    ## the rows are for the last scoring only, the counters go on
    assert len(rows_of(env, "dt-1")) == 1
    assert counters_of(env, "PIN")["valuesScored"] == 2
    assert answer(env)["reloads"] == before + 1


def test_reading_the_scores_again_counts_nothing(env: AppEnv):
    store(env, [PIN], [pin_template()], item((FIELD, "1234", TEXT)))
    for _ in range(2):
        assert env.client.get("/api/template-matches").status_code == 200
    assert counters_of(env, "PIN")["valuesScored"] == 1


def test_the_copies_used_of_a_conforming_value(env: AppEnv):
    store(env, [PIN], [pin_template()], item((FIELD, "123456", TEXT)))
    [row] = rows_of(env, "dt-1")
    assert (row["distance"], row["repeatsUsed"], row["length"]) == (0, [6], 6)


def test_a_value_too_long_is_counted(env: AppEnv):
    length = MAX_VALUE_LEN + 1
    store(env, [PIN], [pin_template()], item((FIELD, "1" * length, TEXT)))
    [row] = rows_of(env, "dt-1")
    assert row["tooLong"] is True
    assert row["length"] == length
    assert row["distance"] is None
    assert row["steps"] == 0
    assert row["repeatsUsed"] == []
    counters = counters_of(env, "PIN")
    assert (counters["valuesScored"], counters["valuesTooLong"]) == (1, 1)


def test_every_uri_counts_and_they_share_one_row(env: AppEnv):
    good, off = "https://a.test/long-enough", "ftp://b"
    store(
        env,
        [URL],
        [template(path("login.uris", URL_ID), type_=LOGIN)],
        a_login(good, off),
    )
    [row] = rows_of(env, "dt-uris")
    assert row["length"] == len(good)
    assert row["distance"] > 0
    uris = sum(len(i.get("login", {}).get("uris") or []) for i in env.bw.items)
    assert counters_of(env, "Web address")["valuesScored"] == uris


def test_an_item_without_a_value_has_no_row(env: AppEnv):
    store(env, [PIN], [pin_template()], item(("Other", "1234", TEXT)))
    assert rows_of(env, "dt-1") == []


def test_the_limits_are_listed(env: AppEnv):
    assert answer(env)["limits"] == {
        "maxPatternLen": patterns.MAX_PATTERN_LEN,
        "maxRepeat": patterns.MAX_REPEAT,
        "maxStates": patterns.MAX_STATES,
        "maxDepth": patterns.MAX_DEPTH,
        "maxValueLen": patterns.MAX_VALUE_LEN,
    }


def test_a_datatype_nothing_uses_is_listed_with_its_facts(env: AppEnv):
    store(env, [PIN, DATE], [pin_template()], item((FIELD, "1234", TEXT)))
    date = counters_of(env, "ISO date")
    assert date["valuesScored"] == 0
    assert date["states"] > 0
    assert date["shortest"] == 10


def test_a_broken_datatype_is_left_out(env: AppEnv):
    store(env, [PIN, {**DATE, "pattern": "a(?=b)"}], [pin_template()], item((FIELD, "1", TEXT)))
    assert [d["name"] for d in answer(env)["datatypes"]] == ["PIN"]


def test_two_datatypes_with_one_pattern_get_a_row_each(env: AppEnv):
    twin = {**PIN, "id": "0000dddd", "name": "Code"}
    store(
        env,
        [PIN, twin],
        [pin_template(), template(field(FIELD, TEXT, "0000dddd"), name="Other")],
        item((FIELD, "1234", TEXT)),
    )
    assert {r["datatype"] for r in rows_of(env, "dt-1")} == {"PIN", "Code"}
    assert counters_of(env, "Code")["valuesScored"] == 1


def test_another_pattern_starts_the_counters_over(env: AppEnv):
    store(env, [PIN], [pin_template()], item((FIELD, "1234", TEXT)))
    env.reload()
    assert counters_of(env, "PIN")["valuesScored"] == 2
    changed = {**PIN, "name": "Longer PIN", "pattern": r"\d{4,12}"}
    write_notes(env, {"version": 1, "templates": [pin_template()], "datatypes": [changed]})
    env.reload()
    counters = counters_of(env, "Longer PIN")
    assert counters["valuesScored"] == 1
    assert counters["maxRepeat"] == 12


def test_whole_value_unticked_starts_the_counters_over(env: AppEnv):
    store(env, [PIN], [pin_template()], item((FIELD, "1234", TEXT)))
    env.reload()
    assert counters_of(env, "PIN")["valuesScored"] == 2
    partial = {**PIN, "wholeValue": False}
    write_notes(env, {"version": 1, "templates": [pin_template()], "datatypes": [partial]})
    env.reload()
    assert counters_of(env, "PIN")["valuesScored"] == 1


def test_a_rename_keeps_the_counters(env: AppEnv):
    store(env, [PIN], [pin_template()], item((FIELD, "1234", TEXT)))
    renamed = {**PIN, "name": "Code"}
    write_notes(env, {"version": 1, "templates": [pin_template()], "datatypes": [renamed]})
    env.reload()
    assert counters_of(env, "Code")["valuesScored"] == 2
    assert [r["datatype"] for r in rows_of(env, "dt-1")] == ["Code"]


def test_a_removed_datatype_and_its_rows_go(env: AppEnv):
    store(env, [PIN], [pin_template()], item((FIELD, "1234", TEXT)))
    write_notes(env, {"version": 1, "templates": [], "datatypes": []})
    env.reload()
    numbers = answer(env)
    assert numbers["datatypes"] == []
    assert numbers["rows"] == []


def test_it_needs_a_session_and_a_logout_empties_it(env: AppEnv):
    store(env, [PIN], [pin_template()], item((FIELD, "1234", TEXT)))
    assert env.client.delete("/api/session").status_code in (200, 204)
    assert env.client.get("/api/statistics").status_code == 401
    numbers = env.app.state.bw.formats.statistics.answer()
    assert (numbers["rows"], numbers["datatypes"], numbers["reloads"]) == ([], [], 0)


def scored(length: int, seconds: float, steps: int = 1, repeats=()) -> Measure:
    result = FormatResult(0, 1.0, None, None, repeats, steps)
    return Measure(length, seconds, result)


def test_the_rows_are_sorted_slowest_first_and_capped():
    statistics = Statistics()
    [datatype] = load_datatypes([PIN])
    pattern = compile_pattern(PIN["pattern"])
    for number in range(MAX_ROWS + 1):
        measure = scored(4, number / 1000)
        statistics.record(
            f"i{number}", "Item", "name", datatype, pattern, [measure], measure.result
        )
    numbers = statistics.answer()
    assert len(numbers["rows"]) == MAX_ROWS
    assert numbers["rowCount"] == MAX_ROWS + 1
    assert numbers["rows"][0]["itemId"] == f"i{MAX_ROWS}"
    times = [row["ms"] for row in numbers["rows"]]
    assert times == sorted(times, reverse=True)
    assert numbers["datatypes"][0]["slowestMs"] == MAX_ROWS


def test_several_values_add_up_in_one_row():
    statistics = Statistics()
    [datatype] = load_datatypes([PIN])
    pattern = compile_pattern(PIN["pattern"])
    one, other = scored(4, 0.002, 5, (2, 9)), scored(7, 0.003, 6, (3, 1))
    statistics.record("i", "Item", "login.uris", datatype, pattern, [one, other], one.result)
    numbers = statistics.answer()
    [row] = numbers["rows"]
    assert row["length"] == 7
    assert row["steps"] == 11
    assert row["ms"] == 5
    assert row["repeatsUsed"] == [3, 9]
    counters = numbers["datatypes"][0]
    assert (counters["valuesScored"], counters["slowestMs"], counters["lastReloadMs"]) == (2, 3, 5)


def test_no_mcp_tool_answers_with_a_statistics_key(env: AppEnv):
    store(
        env,
        [PIN],
        [NOTE_WITH_USER, pin_template()],
        item((FIELD, OFF_FORMAT, TEXT)),
        *EXTRA,
    )
    assert answer(env)["rows"]
    enable(env)
    for tool in list_tools(env):
        ## the description of a tool does not promise them either
        assert not any(key in tool.description for key in STATISTICS_KEYS)
        assert "statistic" not in tool.name
    answers = [
        data(env, tool, **({"item_id": "dt-1"} if tool == "get_item" else {}))
        for tool in READ_TOOLS
    ]
    answers += [data(env, tool, **arguments) for tool, arguments, _ in PROPOSALS]
    for one in answers:
        text = json.dumps(one)
        assert not any(key in text for key in STATISTICS_KEYS)
