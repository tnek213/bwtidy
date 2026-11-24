"""Helpers for tests that call the MCP server's tools."""

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.types import CallToolResult, TextContent

from bwtidy.mcp.texts import DISABLED
from tests.support.app_env import MCP_URL, AppEnv, logged_in
from tests.support.fake_bw import PASSWORD
from tests.support.normalize_data import ALL_SECRETS, EXTRA
from tests.support.quiet import assert_absent
from tests.support.sections_helpers import write_notes

## the tool note's backup-password field holds the master password
SECRETS = [*ALL_SECRETS, PASSWORD]
READ_TOOLS = [
    "list_items",
    "get_item",
    "list_folders",
    "list_collections",
    "list_templates",
    "list_datatypes",
    "list_near_duplicates",
    "list_proposals",
]
NOTE_WITH_USER = {
    "name": "Note with user",
    "type": 2,
    "coordinates": [{"kind": "field", "name": "user", "fieldType": 0}],
}


def call(env: AppEnv, tool: str, **arguments: Any) -> CallToolResult:
    async def run() -> CallToolResult:
        async with Client(env.app.state.bw.mcp) as client:
            return await client.call_tool(tool, arguments)

    assert env.client.portal is not None
    return env.client.portal.call(run)


def text(result: CallToolResult) -> str:
    assert all(isinstance(c, TextContent) for c in result.content)
    return "".join(c.text for c in result.content if isinstance(c, TextContent))


def data(env: AppEnv, tool: str, **arguments: Any) -> Any:
    result = call(env, tool, **arguments)
    assert not result.is_error, text(result)
    return json.loads(text(result))


def error(env: AppEnv, tool: str, **arguments: Any) -> str:
    result = call(env, tool, **arguments)
    assert result.is_error
    return text(result)


def enable(env: AppEnv, enabled: bool = True) -> None:
    ## enabling needs a token, so a test that never made one gets a throwaway
    token = env.app.state.bw.mcp_token
    record = token.record()
    if enabled and (record is None or record.expired(token.now())):
        assert env.client.post("/api/mcp/token").status_code == 201
    response = env.client.post("/api/mcp", json={"enabled": enabled})
    assert response.status_code == 200
    assert response.json() == {"mcpEnabled": enabled}


def assert_no_secret(answer: str) -> None:
    for secret in SECRETS:
        assert_absent(secret, answer, "the answer")
        ## a secret with a newline or quote shows escaped in JSON
        assert_absent(json.dumps(secret)[1:-1], answer, "the answer")
    assert_absent("ssh-private-secret", answer, "the answer")


def queued(env: AppEnv) -> list[dict[str, Any]]:
    response = env.client.get("/api/queue")
    assert response.status_code == 200
    return response.json()


def list_tools(env: AppEnv) -> list[Any]:
    async def run() -> list[Any]:
        async with Client(env.app.state.bw.mcp) as client:
            return (await client.list_tools()).tools

    assert env.client.portal is not None
    return env.client.portal.call(run)


PROPOSALS: list[tuple[str, dict[str, Any], str]] = [
    ("propose_move_to_folder", {"item_ids": ["card-1"], "folder": "Work"}, "moveToFolder"),
    ("propose_set_collections", {"item_id": "note-1", "collections": ["Youth"]}, "setCollections"),
    (
        "propose_move_to_organization",
        {"item_id": "card-1", "organization": "Family", "collections": ["shared"]},
        "moveToOrganization",
    ),
    ("propose_move_to_personal", {"item_id": "note-1"}, "moveToPersonal"),
    ("propose_rename_item", {"item_id": "login-1", "name": "Mail"}, "setLeaf"),
    ("propose_rename_field", {"from_name": "Shoe size", "to_name": "shoe size"}, "renameField"),
    (
        "propose_merge_columns",
        {
            "sources": ["fields.User", "fields.user"],
            "target": "fields.user",
            "resolutions": {"note-3": "fields.user"},
        },
        "mergeColumns",
    ),
    (
        "propose_apply_template",
        {"item_id": "note-5", "template": "Note with user", "mapping": {"fields.user": "notes"}},
        "applyTemplate",
    ),
    (
        "propose_convert_item",
        {"item_id": "note-1", "type": "sshKey", "mapping": {"sshKey.privateKey": "notes"}},
        "convertItem",
    ),
    (
        "propose_merge_items",
        {
            "from_item_id": "note-5",
            "to_item_id": "login-1",
            "mapping": {"fields.Recovery code": "fields.Recovery code"},
        },
        "mergeItems",
    ),
    ("propose_trash_item", {"item_id": "card-1"}, "trashItem"),
    (
        "propose_set_uris",
        {"item_id": "login-1", "uris": [{"uri": "https://mail.example.test", "match": "host"}]},
        "setUris",
    ),
    ("propose_set_uri_match", {"item_ids": ["login-1"], "match": "never"}, "setUris"),
    (
        "propose_create_item",
        {"name": "Spare note", "template": "Note with user", "values": {"fields.user": "admin"}},
        "createItem",
    ),
    (
        "propose_rename_attachment",
        {"item_id": "login-1", "attachment_id": "a1", "new_name": "recovery codes.txt"},
        "renameAttachment",
    ),
]


def mcp_env(tmp_path: Path) -> Iterator[AppEnv]:
    """A logged in app with the extra items of the normalize data, its backups faked."""
    yield from logged_in(tmp_path, extra=EXTRA, base_url=MCP_URL)


def disabled(result: CallToolResult) -> bool:
    return (
        not result.is_error
        and len(result.content) == 1
        and text(result) == DISABLED
        and result.structured_content is None
    )


def settle(env: AppEnv) -> None:
    """Wait for what an ending tool does after its answer."""
    assert env.client.portal is not None
    env.client.portal.call(env.app.state.bw.mcp.settle)


PIN_ID, DATE_ID, BROKEN_ID = "0000aaaa", "0000bbbb", "0000cccc"


PIN = {
    "id": PIN_ID,
    "name": "PIN",
    "pattern": r"\d{4,8}",
    "description": "Digits only",
    "ignoreCase": False,
    "examples": ["1234"],
    "counterExamples": ["12"],
}


DATE = {"id": DATE_ID, "name": "ISO date", "pattern": r"\d{4}-\d{2}-\d{2}"}


BROKEN = {"id": BROKEN_ID, "name": "Broken", "pattern": "(?=x)"}


def store_datatypes(
    env: AppEnv, datatypes: Any, templates: list[dict[str, Any]], *items: dict[str, Any]
) -> None:
    """Datatypes, templates and items as another Bitwarden client would have left them."""
    env.bw.items.extend(copy.deepcopy(list(items)))
    write_notes(env, {"version": 1, "templates": templates, "datatypes": datatypes})
    env.reload()


def typed_field(name: str, field_type: int, datatype: str | None = None) -> dict[str, Any]:
    coordinate = {"kind": "field", "name": name, "fieldType": field_type}
    return {**coordinate, "datatype": datatype} if datatype else coordinate


def typed_item(item_id: str, value: str, field_type: int) -> dict[str, Any]:
    """A login with this value in its password and in its custom field Code."""
    return {
        "object": "item",
        "id": item_id,
        "type": 1,
        "name": f"Item {item_id}",
        "login": {
            "username": "dev",
            "password": value,
            "uris": [{"match": None, "uri": "https://a.example.test"}],
        },
        "fields": [{"name": "Code", "value": value, "type": field_type, "linkedId": None}],
        "revisionDate": "2026-10-30T09:00:00.000Z",
    }
