"""What the gateway reads of a request for the usage stats: route templates instead of paths,
the JSON-RPC methods and tool names the worker knows and nothing else, the error report."""

import json

import pytest

from bwtidy.stats.parse import (
    BODY_LIMIT,
    OTHER,
    Call,
    Names,
    error_report,
    mcp_calls,
    outcomes,
)

NAMES = Names.from_report(
    {
        "routes": [
            {"path": "/api/grid/secret", "methods": ["POST"]},
            {"path": "/api/items/{item_id}", "methods": ["PUT"]},
            {"path": "/api/backups/{name:path}", "methods": ["GET", "HEAD"]},
        ],
        "tools": ["list_items"],
        "methods": ["initialize", "tools/call", "tools/list"],
    }
)


def body(message: object) -> bytes:
    return json.dumps(message).encode()


@pytest.mark.parametrize(
    ("method", "path", "operation"),
    [
        ("PUT", "/api/items/0b3f-secret-id", "PUT /api/items/{item_id}"),
        ("POST", "/api/grid/secret", "POST /api/grid/secret"),
        ("GET", "/api/backups/a/b.zip.age", "GET /api/backups/{name:path}"),
        ("GET", "/api/items/0b3f", OTHER),
        ("PUT", "/api/items/a/b", OTHER),
        ("BREW", "/api/items/a", OTHER),
    ],
)
def test_a_path_becomes_its_route_template(method: str, path: str, operation: str):
    assert NAMES.ui_operation(method, path) == operation


def test_a_tool_call_keeps_the_tool_name_and_never_its_arguments():
    message = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "list_items", "arguments": {"search": "secret"}},
    }
    assert mcp_calls(NAMES, "POST", body(message)) == [Call("list_items", "4")]


@pytest.mark.parametrize(
    "message",
    [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "secret_tool"}},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": "list_items"},
        {"jsonrpc": "2.0", "id": 1, "method": "secret/method"},
        {"jsonrpc": "2.0", "id": 1, "method": ["initialize"]},
        "initialize",
    ],
)
def test_an_unknown_name_becomes_other(message: object):
    assert [call.operation for call in mcp_calls(NAMES, "POST", body(message))] == [OTHER]


def test_a_batch_counts_each_call():
    batch = [
        {"jsonrpc": "2.0", "id": "a", "method": "initialize"},
        {"jsonrpc": "2.0", "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "nope"},
    ]
    assert mcp_calls(NAMES, "POST", body(batch)) == [
        Call("initialize", '"a"'),
        Call("tools/list", None),
        Call(OTHER, "2"),
    ]


def test_a_body_that_is_no_json_or_too_long_is_one_other_call():
    assert mcp_calls(NAMES, "POST", b"secret{") == [Call(OTHER, None)]
    assert mcp_calls(NAMES, "POST", None) == [Call(OTHER, None)]
    assert mcp_calls(NAMES, "GET", None) == [Call("GET /mcp", None)]
    assert mcp_calls(NAMES, "PATCH", None) == [Call(OTHER, None)]
    assert BODY_LIMIT >= 1024 * 1024


def test_without_the_names_every_call_is_other():
    message = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    assert mcp_calls(None, "POST", body(message)) == [Call(OTHER, "1")]


def test_an_answer_tells_each_id_whether_it_succeeded():
    answers = [
        {"jsonrpc": "2.0", "id": 1, "result": {"content": []}},
        {"jsonrpc": "2.0", "id": 2, "error": {"code": -1, "message": "no"}},
        {"jsonrpc": "2.0", "id": 3, "result": {"isError": True}},
    ]
    assert outcomes("application/json", body(answers)) == {"1": True, "2": False, "3": False}
    events = b"event: message\r\ndata: " + body(answers[1]) + b"\r\n\r\n"
    assert outcomes("text/event-stream", events) == {"2": False}
    assert outcomes("application/json", None) == {}


def test_names_that_do_not_look_like_names_are_refused():
    for report in (
        {"routes": [{"path": "/api/a b", "methods": ["GET"]}]},
        {"routes": [{"path": "/api/a", "methods": ["get it"]}]},
        {"tools": ["a tool with spaces"]},
        {"methods": "tools/call"},
        ["list_items"],
    ):
        with pytest.raises(ValueError):
            Names.from_report(report)


def test_an_error_report_keeps_a_type_and_code_locations_only():
    value = json.dumps({"type": "KeyError", "frames": [["api/grid.py", "grid", 12]]})
    report = error_report(value)
    assert report is not None
    assert (report.exception_type, report.frames) == ("KeyError", (("api/grid.py", "grid", 12),))
    for refused in (
        json.dumps({"type": "KeyError", "frames": [], "message": "secret"}),
        json.dumps({"type": "Key Error: secret", "frames": []}),
        json.dumps({"type": "KeyError", "frames": [["/srv/x/a.py", "f", 1]]}),
        json.dumps({"type": "KeyError", "frames": [["a.py", "f(secret)", 1]]}),
        json.dumps({"type": "KeyError", "frames": [["a.py", "f", "1"]]}),
        "not json",
        None,
    ):
        assert error_report(refused) is None
