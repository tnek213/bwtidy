"""GET /api/queue: the entries of the review queue and their diffs (docs/spec.md, Review queue)."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from bwtidy.queue.state import as_operation
from tests.support.app_env import AppEnv
from tests.support.normalize_data import merge_items, with_shop_uris


@pytest.fixture
def uris_env(tmp_path: Path) -> Iterator[AppEnv]:
    yield from with_shop_uris(tmp_path)


def test_the_review_queue_diff_of_an_append_shows_the_union(uris_env: AppEnv):
    op = merge_items(
        uris_env, "login-2", "login-1", {"login.uris": "login.uris"}, append=["login.uris"]
    )
    entry_id = uris_env.app.state.bw.queue.enqueue(as_operation(op), "one login for the mail")
    [entry] = [e for e in uris_env.client.get("/api/queue").json() if e["id"] == entry_id]
    rows = [(r["itemId"], r["column"], r["before"], r["after"]) for r in entry["diff"]]
    mail, webmail, shop = (f"https://{h}.example.test" for h in ("mail", "webmail", "shop"))
    assert ("login-1", "login.uris", [mail, webmail], [mail, webmail, shop]) in rows
    assert ("login-2", "trash", False, True) in rows
