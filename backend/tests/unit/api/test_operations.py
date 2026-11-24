"""GET /api/change-log and its filters, over rows recorded straight into the database."""

from typing import Any

import pytest

from bwtidy.api.operations import CHANGE_LOG_LIMIT
from bwtidy.db import Database
from bwtidy.protocol import MORE_HEADER
from tests.support.app_env import AppEnv


def record(db: Database, at: str, **fields: Any) -> int:
    """A change log row written at the time at, the other fields as given or a plain setLeaf."""
    row: dict[str, Any] = {
        "source": "ui",
        "operation_kind": "setLeaf",
        "item_ids": ["login-1"],
        "result": "ok",
        "backup": "20261026T120000Z.zip.age",
        "created_item_ids": [],
        "columns": ["login.username"],
        **fields,
    }
    entry_id = db.record(**row)
    with db._connection() as conn:
        conn.execute("UPDATE change_log SET at = ? WHERE id = ?", (at, entry_id))
    return entry_id


@pytest.fixture
def rows(env: AppEnv) -> dict[str, int]:
    db = env.writes.db
    return {
        "username": record(db, "2026-11-10T08:00:00+00:00"),
        "mcp folder": record(
            db,
            "2026-11-11T00:00:00+00:00",
            source="mcp",
            operation_kind="moveToFolder",
            item_ids=["login-1", "card-1"],
            columns=["folder"],
        ),
        "failed pin": record(
            db,
            "2026-11-11T23:59:59+00:00",
            source="mixed",
            item_ids=["card-1"],
            columns=["fields.Pin", "notes"],
            result="failed",
        ),
        "created": record(
            db,
            "2026-11-12T09:30:00+00:00",
            operation_kind="createItem",
            item_ids=[],
            created_item_ids=["new-1"],
            columns=[],
        ),
    }


def ids(env: AppEnv, query: str) -> list[int]:
    response = env.client.get(f"/api/change-log{query}")
    assert response.status_code == 200, response.text
    assert response.headers[MORE_HEADER] == "false"
    return [entry["id"] for entry in response.json()]


def test_without_a_filter_every_row_comes_newest_first(env: AppEnv, rows: dict[str, int]):
    assert ids(env, "") == list(reversed(rows.values()))


def test_from_and_to_are_inclusive_instants(env: AppEnv, rows: dict[str, int]):
    assert ids(env, "?from=2026-11-11T00:00:00Z") == [
        rows["created"],
        rows["failed pin"],
        rows["mcp folder"],
    ]
    assert ids(env, "?to=2026-11-11T23:59:59.999Z") == [
        rows["failed pin"],
        rows["mcp folder"],
        rows["username"],
    ]
    ## an offset other than UTC is the same instant, a fraction of a second past a row leaves it out
    assert ids(env, "?from=2026-11-11T01:00:00.5%2B01:00") == [rows["created"], rows["failed pin"]]


def test_source_matches_any_of_those_given(env: AppEnv, rows: dict[str, int]):
    assert ids(env, "?source=mcp") == [rows["mcp folder"]]
    assert ids(env, "?source=mcp&source=mixed") == [rows["failed pin"], rows["mcp folder"]]


def test_kind_matches_any_of_those_given(env: AppEnv, rows: dict[str, int]):
    assert ids(env, "?kind=moveToFolder") == [rows["mcp folder"]]
    assert ids(env, "?kind=createItem&kind=moveToFolder") == [rows["created"], rows["mcp folder"]]


def test_column_matches_a_row_that_wrote_any_of_them(env: AppEnv, rows: dict[str, int]):
    assert ids(env, "?column=notes") == [rows["failed pin"]]
    assert ids(env, "?column=folder&column=login.username") == [
        rows["mcp folder"],
        rows["username"],
    ]
    assert ids(env, "?column=fields.Recovery") == []


def test_item_matches_the_items_and_the_created_items(env: AppEnv, rows: dict[str, int]):
    assert ids(env, "?item=card-1") == [rows["failed pin"], rows["mcp folder"]]
    assert ids(env, "?item=new-1&item=nowhere") == [rows["created"]]


def test_result_is_ok_or_failed(env: AppEnv, rows: dict[str, int]):
    assert ids(env, "?result=failed") == [rows["failed pin"]]
    assert ids(env, "?result=ok") == [rows["created"], rows["mcp folder"], rows["username"]]


def test_the_filters_combine_with_and(env: AppEnv, rows: dict[str, int]):
    assert ids(env, "?item=card-1&source=mcp") == [rows["mcp folder"]]
    assert ids(env, "?item=card-1&result=ok&column=notes") == []
    assert ids(env, "?kind=setLeaf&from=2026-11-11T00:00:00Z") == [rows["failed pin"]]


@pytest.mark.parametrize(
    "query", ["?source=agent", "?result=applied", "?from=yesterday", "?to=2026-13-01T00:00:00Z"]
)
def test_an_unknown_value_is_refused(env: AppEnv, rows: dict[str, int], query: str):
    assert env.client.get(f"/api/change-log{query}").status_code == 422


def test_at_most_the_newest_200_matches_and_whether_more_matched(env: AppEnv):
    db = env.writes.db
    for minute in range(CHANGE_LOG_LIMIT + 1):
        record(db, f"2026-11-12T{minute // 60:02}:{minute % 60:02}:00+00:00")
    record(db, "2026-11-12T05:00:00+00:00", operation_kind="trashItem", columns=[])

    every = env.client.get("/api/change-log")
    assert len(every.json()) == CHANGE_LOG_LIMIT
    assert every.headers[MORE_HEADER] == "true"
    assert every.json()[0]["operationKind"] == "trashItem"

    ## the filter runs over the whole log, not over the newest 200
    matched = env.client.get("/api/change-log?kind=setLeaf")
    entries = matched.json()
    assert len(entries) == CHANGE_LOG_LIMIT
    assert matched.headers[MORE_HEADER] == "true"
    assert entries[0]["at"] == "2026-11-12T03:20:00+00:00"
    assert entries[-1]["at"] == "2026-11-12T00:01:00+00:00"

    exactly = env.client.get("/api/change-log?kind=setLeaf&from=2026-11-12T00:01:00Z")
    assert len(exactly.json()) == CHANGE_LOG_LIMIT
    assert exactly.headers[MORE_HEADER] == "false"
