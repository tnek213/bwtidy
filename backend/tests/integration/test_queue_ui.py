"""Queued UI edits against the seeded dev vault through a real bw serve.

What only a real server can tell: the auto-commit writes a queued edit on its own, a hidden
value queued only as an age ciphertext arrives in the vault as the value, and an edit made
through the CLI before the commit makes the entry fail with a revisionDate conflict. Every
write is undone at the end of the module. Nothing here prints an item or a value.
"""

import base64
import json
import secrets
import time
from collections.abc import Iterator
from typing import Any

import pytest
from support.app import App
from support.cli import BwCli
from support.vault import Vault, raw_sqlite, same_secret

from tests.support.quiet import assert_absent

pytestmark = pytest.mark.integration

## a commit may wait for the background backup the login started (up to half a minute)
COMMIT_WAIT = 120.0


@pytest.fixture(scope="module")
def vault(app: App, bw_cli: BwCli) -> Iterator[Vault]:
    assert app.login().status_code == 200
    vault = Vault(app, bw_cli)
    yield vault
    assert app.client.portal is not None
    vault.beat()
    vault.cli_run("sync")
    for undo in reversed(vault.undo):
        app.client.portal.call(undo)
    app.client.portal.call(vault.client.sync)
    app.client.portal.call(app.session.cache.load)


def enqueue(vault: Vault, *operations: dict[str, Any]):
    vault.beat()
    return vault.app.client.post("/api/queue", json={"operations": list(operations)})


def entry(vault: Vault, entry_id: int) -> dict[str, Any]:
    entries = vault.app.client.get("/api/queue").json()
    return next(e for e in entries if e["id"] == entry_id)


def wait_until_decided(vault: Vault, entry_id: int) -> dict[str, Any]:
    deadline = time.monotonic() + COMMIT_WAIT
    while time.monotonic() < deadline:
        vault.beat()
        found = entry(vault, entry_id)
        if found["status"] not in ("pending", "approved"):
            return found
        time.sleep(1)
    raise AssertionError(f"entry {entry_id} was not committed within {COMMIT_WAIT:.0f} s")


def auto_commit_after(vault: Vault, seconds: int) -> None:
    vault.beat()
    body = {"afterSeconds": seconds, "maxEntries": 1000}
    response = vault.app.client.put("/api/queue/auto-commit", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["afterSeconds"] == seconds


def test_a_queued_edit_is_committed_on_its_own(vault: Vault):
    client = vault.app.client
    note = vault.raw(vault.by_name("SIM PIN")["id"])
    vault.undo_put(note)
    auto_commit_after(vault, 1)
    log_size = len(vault.change_log())

    response = enqueue(vault, vault.set_leaf(note, "notes", "queued in the grid"))
    assert response.status_code == 201, response.text
    [entry_id] = response.json()["ids"]
    status = client.get("/api/queue/auto-commit").json()
    assert status["pending"] == 1
    assert status["nextCommitAt"] is not None
    ## nothing is written by the queueing itself
    assert vault.raw(note["id"])["revisionDate"] == note["revisionDate"]

    applied = wait_until_decided(vault, entry_id)
    assert applied["status"] == "applied", applied["result"]
    assert applied["source"] == "ui"
    assert applied["result"]["backup"] in vault.backups()
    after = vault.raw_after(note["id"], note["revisionDate"])
    assert after["notes"] == "queued in the grid"
    assert after["revisionDate"] != note["revisionDate"]
    log = vault.change_log()
    assert len(log) == log_size + 1
    assert (log[0]["operationKind"], log[0]["source"]) == ("setLeaf", "ui")
    assert client.get("/api/queue/auto-commit").json()["pending"] == 0

    ## the grid has not reloaded: an edit with the revisionDate from before the commit is fine
    response = enqueue(vault, vault.set_leaf(note, "notes", "second edit, old revision"))
    assert response.status_code == 201, response.text
    [second] = response.json()["ids"]
    assert wait_until_decided(vault, second)["status"] == "applied"
    assert (
        vault.raw_after(note["id"], after["revisionDate"])["notes"] == "second edit, old revision"
    )
    ## the tests below commit by hand, the task must not get there first
    auto_commit_after(vault, 3600)


def test_a_hidden_value_arrives_in_the_vault_and_never_in_sqlite(vault: Vault):
    """A new hidden field: a login.password change would leave a password history entry
    behind that test_seed counts, a hidden field's record it leaves out."""
    client = vault.app.client
    auto_commit_after(vault, 3600)
    forge = vault.raw(vault.by_name("Example Forge")["id"])
    vault.undo_put(forge)
    value = f"queued-hidden-{secrets.token_hex(8)}"
    current = vault.by_name("Example Forge")
    op = vault.op("setLeaf", current, column="fields.Recovery", value=value, fieldType=1)
    response = enqueue(vault, op)
    assert response.status_code == 201, response.text
    [entry_id] = response.json()["ids"]
    queued = entry(vault, entry_id)
    assert queued["operation"]["value"] == {"masked": True}
    assert queued["diff"][0]["after"] == {"masked": True}
    assert_absent(value.encode(), raw_sqlite(vault), "the database file")
    assert_absent(value, client.get("/api/queue").text, "the queue response")

    vault.beat()
    response = client.post("/api/queue/commit")
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == [entry_id]
    after = vault.raw_after(forge["id"], forge["revisionDate"])
    [field] = [f for f in after["fields"] if f["name"] == "Recovery"]
    assert field["type"] == 1
    value_stored = same_secret(field["value"], value)
    assert value_stored, "the hidden field does not hold the value"
    password_kept = same_secret(after["login"]["password"], forge["login"]["password"])
    assert password_kept, "the login password changed"
    assert_absent(value.encode(), raw_sqlite(vault), "the database file")
    assert_absent(value, client.get("/api/queue").text, "the queue response")
    assert_absent(value, client.get("/api/change-log").text, "the change log response")


def test_an_edit_made_through_bw_before_the_commit_fails_the_entry(vault: Vault):
    client = vault.app.client
    auto_commit_after(vault, 3600)
    switch = vault.raw(vault.by_name("Switch admin")["id"])
    vault.undo_put(switch)
    current = vault.by_name("Switch admin")
    response = enqueue(vault, vault.set_leaf(current, "notes", "typed in the grid"))
    assert response.status_code == 201, response.text
    [entry_id] = response.json()["ids"]

    ## another device edits the item (the CLI with the app's own session key) first
    vault.cli_run("sync")
    edited = {**vault.cli("get", "item", switch["id"]), "notes": "edited elsewhere"}
    encoded = base64.b64encode(json.dumps(edited).encode()).decode()
    vault.cli_run("edit", "item", switch["id"], encoded)

    vault.beat()
    response = client.post("/api/queue/commit")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "applied": [],
        "failed": [entry_id],
        "pending": [],
        "backup": None,
    }
    failed = entry(vault, entry_id)
    assert failed["status"] == "failed"
    assert failed["result"]["error"] == "conflicts"
    [conflict] = failed["result"]["conflicts"]
    assert conflict["itemId"] == switch["id"]
    assert conflict["expected"] == current["revisionDate"]
    assert conflict["actual"] not in (None, current["revisionDate"])
    after = vault.raw_after(switch["id"], switch["revisionDate"])
    assert after["notes"] == "edited elsewhere"
    assert after["revisionDate"] == conflict["actual"]
    ## the write path's sync reloaded the vault, so the fresh revisionDate goes through
    response = enqueue(vault, vault.set_leaf(after, "notes", "typed in the grid"))
    assert response.status_code == 201, response.text
    assert client.post("/api/queue/commit").json()["failed"] == []
    assert vault.raw_after(switch["id"], after["revisionDate"])["notes"] == "typed in the grid"
