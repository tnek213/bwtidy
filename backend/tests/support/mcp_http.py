"""Helpers for tests that reach the MCP server over HTTP with a token."""

from datetime import datetime

import httpx2 as httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from bwtidy.mcp_token import UNAUTHORIZED, utc_now
from tests.support.app_env import AppEnv
from tests.support.mcp_helpers import text

HOST = "http://127.0.0.1:8765"


def create_token(env: AppEnv) -> str:
    response = env.client.post("/api/mcp/token")
    assert response.status_code == 201
    return response.json()["token"]


def http_call(env: AppEnv, token: str | None, tool: str = "list_folders", host: str = HOST) -> str:
    """Call a tool over streamable HTTP at /mcp, with `Authorization: Bearer token`."""
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}

    async def run() -> str:
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=env.app), base_url=host, headers=headers
        )
        async with http, Client(streamable_http_client(f"{host}/mcp", http_client=http)) as mcp:
            return text(await mcp.call_tool(tool, {}))

    assert env.client.portal is not None
    return env.client.portal.call(run)


def raw_post(env: AppEnv, headers: dict[str, str]) -> httpx.Response:
    return env.client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream", **headers},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )


def refused(response: httpx.Response) -> bool:
    return response.status_code == 401 and response.text == UNAUTHORIZED


class Clock:
    """The MCP token's wall clock, moved by hand."""

    def __init__(self) -> None:
        self.now = utc_now().replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now


def token_clock(env: AppEnv) -> Clock:
    clock = Clock()
    env.app.state.bw.mcp_token.now = clock
    return clock


def authorized(env: AppEnv, token: str) -> int:
    return raw_post(env, {"Authorization": f"Bearer {token}"}).status_code
