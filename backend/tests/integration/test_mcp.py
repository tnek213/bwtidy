"""MCP over real HTTP on the seeded dev vault: enable, list, propose a folder move, approve.

The app runs in uvicorn on a free loopback port, so the MCP client talks streamable HTTP to
/mcp like Claude Code does. The CLI runs with the app's own session key and is never
unlocked while the app runs, because a CLI unlock invalidates the app's key. The moved item
is moved back at the end. Nothing here prints an item or a value.
"""

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import httpx2 as httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent
from support.app import purge_tool_notes
from support.cli import PASSWORD, TEST_AUTO_COMMIT, BwCli

from bwtidy.config import Settings
from bwtidy.items.coordinates import Lookups, field_types, flatten, is_secret
from bwtidy.main import create_app
from bwtidy.mcp.texts import DISABLED

pytestmark = pytest.mark.integration

ITEM = "Example Mail"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    def __init__(self, app: FastAPI, url: str, bw_cli: BwCli) -> None:
        self.app = app
        self.url = url
        self.bw_cli = bw_cli
        self.http = httpx.Client(base_url=url, timeout=300)
        ## set after the login, every MCP call carries it
        self.mcp_token = ""

    def beat(self) -> None:
        assert self.http.post("/api/session/heartbeat").status_code == 204

    def cli(self, *args: str) -> Any:
        key = self.app.state.bw.session.session_key
        assert key
        return self.bw_cli.json(*args, session=key)

    def call(self, tool: str, **arguments: Any) -> CallToolResult:
        async def run() -> CallToolResult:
            headers = {"Authorization": f"Bearer {self.mcp_token}"}
            http = httpx.AsyncClient(headers=headers, timeout=300)
            transport = streamable_http_client(f"{self.url}/mcp", http_client=http)
            async with http, Client(transport) as client:
                return await client.call_tool(tool, arguments)

        return asyncio.run(run())

    def text(self, tool: str, **arguments: Any) -> str:
        result = self.call(tool, **arguments)
        texts = [c.text for c in result.content if isinstance(c, TextContent)]
        ## the tool's own error text names no value, so it is safe to show on a failure
        assert not result.is_error, texts
        return "".join(texts)


@pytest.fixture(scope="module")
def server(bw_cli: BwCli, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    """The app in uvicorn on a thread, with the real BwServeProcess, logged in."""
    purge_tool_notes(bw_cli)
    data_dir = tmp_path_factory.mktemp("data")
    settings = replace(
        Settings.from_env(),
        data_dir=data_dir,
        backup_dir=data_dir / "backups",
        static_dir=None,
    )
    app = create_app(settings)
    app.state.bw.auto_commit.configure(*TEST_AUTO_COMMIT)
    port = free_port()
    uvi = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=uvi.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 120
    while not uvi.started:
        assert thread.is_alive() and time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.1)
    served = Server(app, f"http://127.0.0.1:{port}", bw_cli)
    try:
        assert served.http.post("/api/session", json={"password": PASSWORD}).status_code == 200
        created = served.http.post("/api/mcp/token")
        assert created.status_code == 201
        served.mcp_token = created.json()["token"]
        yield served
    finally:
        served.http.close()
        uvi.should_exit = True
        thread.join(60)
        purge_tool_notes(bw_cli)


def secret_values(items: list[dict[str, Any]]) -> list[str]:
    found = []
    for item in items:
        types = field_types(item)
        for coordinate, value in flatten(item, Lookups()).items():
            if is_secret(coordinate, types.get(coordinate)) and isinstance(value, str):
                found.append(value)
    return found


def leaks(secret: str, answer: str) -> bool:
    """The secret as a whole JSON value, or inside one when it is too long to be a coincidence.

    Card codes of three digits turn up inside the seeded usernames and notes.
    """
    quoted = json.dumps(secret)
    return quoted in answer or (len(secret) >= 8 and quoted[1:-1] in answer)


def test_mcp_is_disabled_until_the_toggle(server: Server):
    server.beat()
    result = server.call("list_items")
    texts = [c.text for c in result.content if isinstance(c, TextContent)]
    assert texts == [DISABLED]


def test_list_propose_approve_moves_the_item(server: Server):
    server.beat()
    enabled = server.http.post("/api/mcp", json={"enabled": True})
    assert enabled.json() == {"mcpEnabled": True}

    answer = server.text("list_items")
    secrets = secret_values(server.cli("list", "items"))
    assert secrets
    assert sum(1 for secret in secrets if leaks(secret, answer)) == 0
    items = json.loads(answer)["items"]
    assert "bwtidy" not in {i["name"] for i in items}
    [item] = [i for i in items if i["name"] == ITEM]
    assert item["folder"] == "Work"
    assert "login.password" in item["secretColumns"]
    folders = {f["name"] for f in json.loads(server.text("list_folders"))["folders"]}
    assert {"Work", "Home", "Infra"} <= folders

    proposal = json.loads(
        server.text("propose_move_to_folder", item_ids=[item["id"]], folder="Home")
    )
    assert proposal["summary"] == f'Move "{ITEM}" to folder "Home"'
    [entry] = server.http.get("/api/queue", params={"status": "pending"}).json()
    assert entry["id"] == proposal["entryId"]
    assert entry["diff"] == [
        {
            "itemId": item["id"],
            "itemName": ITEM,
            "column": "folder",
            "before": "Work",
            "after": "Home",
        }
    ]

    server.beat()
    try:
        approved = server.http.post("/api/queue/approve", json={"ids": [entry["id"]]})
        assert approved.status_code == 200, approved.text
        assert approved.json()["applied"] == [entry["id"]]
        home = {f["name"]: f["id"] for f in server.cli("list", "folders")}["Home"]
        assert server.cli("get", "item", item["id"])["folderId"] == home
        server.beat()
        moved = json.loads(server.text("get_item", item_id=item["id"]))
        assert moved["folder"] == "Home"
    finally:
        server.beat()
        grid = server.http.get("/api/grid").json()
        [row] = [r for r in grid["rows"] if r["id"] == item["id"]]
        if row["cells"].get("folder") != "Work":
            back = {
                "kind": "moveToFolder",
                "itemIds": [item["id"]],
                "revisionDates": {item["id"]: row["revisionDate"]},
                "folderName": "Work",
            }
            response = server.http.post("/api/operations", json={"operations": [back]})
            assert response.status_code == 200, response.text
