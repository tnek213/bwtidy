"""The MCP server: the toggle and the gating of every tool, the registered tools and their texts,
an unexpected error named by its type, streamable HTTP and the hosts it answers.
"""

import inspect
import json
import logging
import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import bwtidy.mcp.server as mcp_server
import bwtidy.mcp.vault as mcp_vault
from bwtidy.config import HEARTBEAT_TIMEOUT, Settings
from bwtidy.mcp import propose_tools, read_tools, session_tools
from bwtidy.mcp.session_tools import SessionActions
from bwtidy.mcp.texts import INSTRUCTIONS
from bwtidy.mcp.vault import AgentVault
from tests.support.app_env import MCP_URL, AppEnv
from tests.support.mcp_helpers import (
    READ_TOOLS,
    assert_no_secret,
    call,
    disabled,
    enable,
    error,
    list_tools,
    mcp_env,
    queued,
    settle,
)
from tests.support.mcp_http import create_token, http_call, raw_post, token_clock
from tests.support.quiet import assert_absent


@pytest.fixture
def env(tmp_path: Path) -> Iterator[AppEnv]:
    yield from mcp_env(tmp_path)


PROPOSE_TOOLS = [
    "propose_move_to_folder",
    "propose_set_collections",
    "propose_move_to_organization",
    "propose_move_to_personal",
    "propose_rename_item",
    "propose_rename_field",
    "propose_merge_columns",
    "propose_apply_template",
    "propose_convert_item",
    "propose_merge_items",
    "propose_trash_item",
    "propose_create_item",
    "propose_rename_folder",
    "propose_delete_folder",
    "propose_set_uris",
    "propose_set_uri_match",
    "propose_rename_attachment",
]


END_TOOLS = ["end_session", "log_out_of_bitwarden"]


def test_the_toggle_needs_a_session_and_every_login_disables_it(env: AppEnv):
    assert env.client.get("/api/session").json()["mcpEnabled"] is False
    enable(env)
    assert env.client.get("/api/session").json()["mcpEnabled"] is True
    enable(env, False)
    assert env.client.get("/api/session").json()["mcpEnabled"] is False
    enable(env)
    env.unlock()
    assert env.client.get("/api/session").json()["mcpEnabled"] is False
    env.client.cookies.clear()
    assert env.client.post("/api/mcp", json={"enabled": True}).status_code == 401
    assert env.app.state.bw.session.mcp_enabled is False


def test_enabling_needs_a_token_that_has_not_expired(env: AppEnv):
    clock = token_clock(env)
    refusal = env.client.post("/api/mcp", json={"enabled": True})
    assert refusal.status_code == 409
    assert refusal.json() == {"detail": "Create an MCP token first: MCP token menu, Create."}
    assert env.client.get("/api/session").json()["mcpEnabled"] is False
    ## disabling needs no token
    assert env.client.post("/api/mcp", json={"enabled": False}).status_code == 200

    assert env.client.post("/api/mcp/token", json={"days": 7}).status_code == 201
    clock.now += timedelta(days=7, seconds=-1)
    assert env.client.post("/api/mcp", json={"enabled": True}).json() == {"mcpEnabled": True}

    clock.now += timedelta(seconds=1)
    ## an expired token stops enabling again, and switching off still works
    assert env.client.post("/api/mcp", json={"enabled": False}).json() == {"mcpEnabled": False}
    expired = env.client.post("/api/mcp", json={"enabled": True})
    assert expired.status_code == 409
    assert expired.json() == {
        "detail": "The MCP token has expired. Replace it first: MCP token menu, Replace."
    }
    assert env.client.get("/api/session").json()["mcpEnabled"] is False

    assert env.client.post("/api/mcp/token", json={"days": None}).status_code == 201
    assert env.client.post("/api/mcp", json={"enabled": True}).json() == {"mcpEnabled": True}


def test_every_tool_is_disabled_until_enabled(env: AppEnv):
    for tool in READ_TOOLS + PROPOSE_TOOLS + END_TOOLS:
        assert disabled(call(env, tool, item_id="card-1")), tool
    ## not even an argument error or an unknown tool gets through
    assert disabled(call(env, "propose_trash_item"))
    assert disabled(call(env, "no_such_tool"))
    assert queued(env) == []
    settle(env)
    assert env.client.get("/api/session").json()["state"] == "unlocked"


def test_every_tool_is_disabled_when_locked(env: AppEnv):
    enable(env)
    assert not disabled(call(env, "list_folders"))
    assert env.client.delete("/api/session").status_code == 204
    for tool in READ_TOOLS + PROPOSE_TOOLS + END_TOOLS:
        assert disabled(call(env, tool, item_id="card-1")), tool


def test_disabled_as_soon_as_the_session_times_out(env: AppEnv):
    enable(env)
    env.app.state.bw.session._clock.advance(HEARTBEAT_TIMEOUT)
    assert disabled(call(env, "list_items"))


def test_an_unexpected_error_is_named_by_its_type_only(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    enable(env)
    caplog.set_level(logging.DEBUG)

    def broken(item: dict[str, Any], lookups: Any) -> Any:
        raise RuntimeError("pw-login-secret-e0e7")

    monkeypatch.setattr(mcp_vault, "split_values", broken)
    message = error(env, "list_items")
    assert "RuntimeError" in message
    assert_no_secret(message)
    assert_no_secret(caplog.text)


def test_the_registration_list_holds_every_tool_once_with_the_kind_of_its_module(env: AppEnv):
    names = [fn.__name__ for fn, _, _, _ in mcp_server.TOOLS]
    assert len(names) == len(set(names))
    assert set(names) == set(READ_TOOLS + PROPOSE_TOOLS + END_TOOLS)
    assert [tool.name for tool in list_tools(env)] == names
    kinds = [
        (read_tools, mcp_server.READ_ONLY, AgentVault),
        (propose_tools, mcp_server.PROPOSE, AgentVault),
        (session_tools, mcp_server.END, SessionActions),
    ]
    for module, annotations, interface in kinds:
        defined = {
            name
            for name, value in vars(module).items()
            if inspect.iscoroutinefunction(value)
            and value.__module__ == module.__name__
            and not name.startswith("_")
        }
        registered = {
            fn.__name__ for fn, _, _, _ in mcp_server.TOOLS if fn.__module__ == module.__name__
        }
        assert defined == registered, module.__name__
        for fn, _, kind, takes in mcp_server.TOOLS:
            if fn.__module__ == module.__name__:
                assert kind == annotations, fn.__name__
                assert takes is interface, fn.__name__
                first = next(iter(inspect.signature(fn).parameters.values()))
                assert first.annotation is interface, fn.__name__


def test_tool_descriptions_say_proposals_only_and_no_secrets(env: AppEnv):
    tools = {tool.name: tool for tool in list_tools(env)}
    assert set(tools) == set(READ_TOOLS + PROPOSE_TOOLS + END_TOOLS)
    for name in END_TOOLS:
        assert "The answer comes first, the session ends right after it" in (
            tools[name].description or ""
        )
        assert tools[name].annotations is not None
        assert tools[name].annotations.destructive_hint is True
    for name in READ_TOOLS:
        assert "Secret values are never available" in (tools[name].description or "")
        assert tools[name].annotations is not None
        assert tools[name].annotations.read_only_hint is True
    for name in PROPOSE_TOOLS:
        description = tools[name].description or ""
        assert "only proposes the change" in description
        assert "approves or rejects it in the bwtidy web UI" in description
        assert "Secret values are never available" in description
    for name in ("propose_set_uris", "propose_set_uri_match"):
        description = tools[name].description or ""
        assert "No secret value is read or written" in description
        assert "null is the client's default" in description
        for word in ("domain", "host", "startsWith", "exact", "regex", "never"):
            assert word in description, (name, word)


def all_descriptions(node: Any) -> list[str]:
    """Every description string in a tool's input schema, nested ones included."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                found.append(value)
            else:
                found += all_descriptions(value)
    elif isinstance(node, list):
        for item in node:
            found += all_descriptions(item)
    return found


def test_no_agent_text_names_the_maintainer(env: AppEnv):
    about = (Path(__file__).parents[4] / "frontend/src/about/AboutPage.tsx").read_text()
    found = re.search(r"Copyright \(C\) \d{4} (\w+)", about)
    assert found is not None
    first_name = found.group(1).lower()
    texts = [INSTRUCTIONS]
    for tool in list_tools(env):
        texts.append(tool.description or "")
        texts += all_descriptions(tool.input_schema)
    assert len(texts) > len(READ_TOOLS + PROPOSE_TOOLS)
    for description in texts:
        assert_absent(first_name, description.lower(), "a tool text")


def test_tool_descriptions_say_what_a_datatype_is_and_that_a_secret_shows_no_format(env: AppEnv):
    tools = {tool.name: tool.description or "" for tool in list_tools(env)}
    for name in ("list_items", "list_templates", "list_datatypes"):
        assert "A secret column never shows a format" in tools[name]
    assert "named formats the user defines" in tools["list_datatypes"]
    assert "matches format" in tools["list_datatypes"]
    assert "matches format" in tools["list_templates"]
    assert "matches format" in tools["list_items"]
    assert "no tool proposes either" in tools["list_datatypes"]
    for name, description in tools.items():
        ## list_proposals names the fixed reason invalid, which is about an operation
        if name != "list_proposals":
            assert "valid" not in description.casefold()
    ## read only: no tool proposes a datatype or a corrected value
    assert [name for name in tools if "datatype" in name] == ["list_datatypes"]
    assert [name for name in tools if "format" in name or "value" in name] == []


def test_mcp_is_served_over_streamable_http_at_mcp(env: AppEnv):
    enable(env)
    token = create_token(env)
    answer = json.loads(http_call(env, token))
    assert {"name": "Work", "items": 1, "itemsInTrash": 0} in answer["folders"]
    ## DNS rebinding: a request addressed to another host name is refused
    response = raw_post(env, {"Host": "attacker.example", "Authorization": f"Bearer {token}"})
    assert response.status_code in (400, 403, 421)


def test_mcp_hosts_let_that_host_through(tmp_path: Path):
    with AppEnv(tmp_path, base_url=MCP_URL, mcp_hosts=("bwtidy.example.test",)) as env:
        env.unlock()
        enable(env)
        token = create_token(env)
        bearer = {"Authorization": f"Bearer {token}"}
        for host in ("bwtidy.example.test", "bwtidy.example.test:443"):
            assert raw_post(env, {**bearer, "Host": host}).status_code == 200, host
        origin = {**bearer, "Host": "bwtidy.example.test"}
        assert raw_post(env, {**origin, "Origin": "https://bwtidy.example.test"}).status_code == 200
        assert raw_post(env, {**origin, "Origin": "https://evil.example"}).status_code == 403
        assert raw_post(env, {**bearer, "Host": "other.example.test"}).status_code in (400, 421)
        answer = http_call(env, token, host="https://bwtidy.example.test")
        assert "folders" in json.loads(answer)


def test_mcp_hosts_come_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BWTIDY_MCP_HOSTS", " bwtidy.example.test, ,other.example.test ")
    assert Settings.from_env().mcp_hosts == ("bwtidy.example.test", "other.example.test")
    monkeypatch.delenv("BWTIDY_MCP_HOSTS")
    assert Settings.from_env().mcp_hosts == ()
    security = mcp_server.streamable_http_security(["bwtidy.example.test"])
    assert "bwtidy.example.test" in security.allowed_hosts
    assert "bwtidy.example.test:*" in security.allowed_hosts
    assert "https://bwtidy.example.test" in security.allowed_origins
