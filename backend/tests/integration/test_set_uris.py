"""URI match settings on the seeded dev vault: an agent proposes, the user approves, bw stores them.

The proposal goes through the MCP tool in-process, the approval through the queue, and the
CLI reads the item back with the app's own session key. The login is put back at the end of
the module. Nothing here prints an item or a value.
"""

import json
from collections.abc import Iterator
from typing import Any, cast

import pytest
from fastapi import FastAPI
from mcp import Client
from mcp.types import TextContent
from support.app import App
from support.cli import BwCli
from support.vault import Vault

pytestmark = pytest.mark.integration

ITEM = "Example Mail"


@pytest.fixture(scope="module")
def vault(app: App, bw_cli: BwCli) -> Iterator[Vault]:
    assert app.login().status_code == 200
    app.settle()
    vault = Vault(app, bw_cli)
    yield vault
    assert app.client.portal is not None
    vault.beat()
    vault.cli_run("sync")
    for undo in reversed(vault.undo):
        app.client.portal.call(undo)
    app.client.portal.call(vault.client.sync)
    app.client.portal.call(app.session.cache.load)


def call(vault: Vault, tool: str, **arguments: Any) -> dict[str, Any]:
    """An MCP tool's answer, called in-process like an agent calls it."""
    server = cast(FastAPI, vault.app.client.app).state.bw.mcp

    async def run() -> Any:
        async with Client(server) as client:
            return await client.call_tool(tool, arguments)

    assert vault.app.client.portal is not None
    result = vault.app.client.portal.call(run)
    texts = [c.text for c in result.content if isinstance(c, TextContent)]
    ## the tool's own error text names no value, so it is safe to show on a failure
    assert not result.is_error, texts
    return json.loads("".join(texts))


def test_an_approved_proposal_stores_both_match_settings(vault: Vault):
    vault.beat()
    assert vault.app.client.post("/api/mcp/token").status_code == 201
    assert vault.app.client.post("/api/mcp", json={"enabled": True}).status_code == 200
    item = vault.by_name(ITEM)
    vault.undo_put(vault.raw(item["id"]))
    uris = [
        {"uri": "https://mail.example.test", "match": "host"},
        {"uri": "https://old-mail.example.test", "match": "never"},
    ]
    answer = call(vault, "propose_set_uris", item_id=item["id"], uris=uris)
    assert answer["summary"] == f'Set the URIs of "{ITEM}" (2)'

    vault.beat()
    approved = vault.app.client.post("/api/queue/approve", json={"ids": [answer["entryId"]]})
    vault.app.settle()
    assert approved.status_code == 200, approved.text
    assert approved.json()["applied"] == [answer["entryId"]]

    vault.cli_run("sync")
    stored = vault.cli("get", "item", item["id"])["login"]["uris"]
    assert [(u["uri"], u["match"]) for u in stored] == [
        ("https://mail.example.test", 1),
        ("https://old-mail.example.test", 5),
    ]
    vault.beat()
    shown = call(vault, "get_item", item_id=item["id"])["values"]["login.uris"]
    assert shown == uris
