"""Several queued edits of one item, committed in one batch, through a real bw serve.

What only a real server can tell: right after its own PUT, bw serve 2026.10.0 answers a GET
of the item for a moment as it was before that PUT, and the batch's next write of the item
must not take that for another client's change (see WritePath._past_own_write). Each round
queues an add field and an edit of it for three fields, all on the revisionDate the grid
showed (as the secret dialog's e2e test does), commits them and expects every entry applied.
Taken for another client's change, about one commit in four would fail on its own earlier
write, so two rounds of five such writes catch it. The item is put back at the end of the
module. Nothing here prints an item or a value.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from support.app import App
from support.cli import BwCli
from support.vault import Vault

pytestmark = pytest.mark.integration

ITEM = "Youth bank"
ROUNDS = 2
## an add field as a text field, then an edit of it that leaves the type as it is
EDITS = [("queued-start", 0), ("queued-edit", None)]


@pytest.fixture(scope="module")
def vault(app: App, bw_cli: BwCli) -> Iterator[Vault]:
    assert app.login().status_code == 200
    app.settle()
    vault = Vault(app, bw_cli)
    vault.undo_put(vault.raw(vault.by_name(ITEM)["id"]))
    settings = app.client.get("/api/queue/auto-commit").json()
    ## the auto-commit must not take the first entries before the last one is queued
    later = {"afterSeconds": 3600, "maxEntries": 1000}
    assert app.client.put("/api/queue/auto-commit", json=later).status_code == 200
    yield vault
    vault.beat()
    app.client.put(
        "/api/queue/auto-commit",
        json={"afterSeconds": settings["afterSeconds"], "maxEntries": settings["maxEntries"]},
    )
    assert app.client.portal is not None
    for undo in reversed(vault.undo):
        app.client.portal.call(undo)
    app.client.portal.call(vault.client.sync)
    app.client.portal.call(app.session.cache.load)


def set_leaf(item: dict[str, Any], column: str, value: str, field_type: int | None) -> dict:
    op = {
        "kind": "setLeaf",
        "itemId": item["id"],
        "revisionDate": item["revisionDate"],
        "column": column,
        "value": value,
    }
    if field_type is not None:
        op["fieldType"] = field_type
    return op


def outcome(found: dict[str, Any]) -> str:
    """The entry's status and error, never its values."""
    if found["status"] == "applied":
        return "applied"
    result = found.get("result") or {}
    conflicts = result.get("conflicts") or []
    revisions = [(c.get("expected"), c.get("actual")) for c in conflicts]
    return f"{found['status']} {result.get('error')} {revisions}"


@pytest.mark.parametrize("round_number", range(ROUNDS))
def test_queued_edits_of_one_item_all_apply_in_one_commit(vault: Vault, round_number: int):
    client = vault.app.client
    item = vault.by_name(ITEM)
    ids: list[int] = []
    for field in ("a", "b", "c"):
        column = f"fields.queued{round_number}{field}"
        for value, field_type in EDITS:
            vault.beat()
            response = client.post(
                "/api/queue", json={"operations": [set_leaf(item, column, value, field_type)]}
            )
            assert response.status_code == 201, response.status_code
            ids += response.json()["ids"]
    vault.beat()
    assert client.post("/api/queue/commit").status_code == 200
    entries = {e["id"]: e for e in client.get("/api/queue").json()}
    outcomes = [outcome(entries[i]) for i in ids]
    assert outcomes == ["applied"] * len(ids)
