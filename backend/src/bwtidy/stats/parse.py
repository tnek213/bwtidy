"""What the gateway reads of a request for the usage stats (docs/spec.md, Usage stats).

Only names that come from the code are ever kept: a UI path becomes the template of the worker
route it matches, an MCP body gives its JSON-RPC methods and the names of the tools it calls,
each kept only when the worker names it, and a worker's error report gives an exception type
and code locations. Everything else becomes OTHER. Nothing here keeps an id, a value or an
argument, and nothing here raises on what a client sent.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

from starlette.routing import compile_path

OTHER = "other"
UI = "ui"
MCP = "mcp"
## an MCP body or answer beyond this is not read, its calls count by the HTTP status alone
BODY_LIMIT = 2 * 1024 * 1024
## the calls of one JSON-RPC batch that count, the frames of one error report that are kept
MAX_CALLS = 100
MAX_FRAMES = 30
## what a name from the worker may look like, so its report can carry nothing else
TEMPLATE = re.compile(r"^/[A-Za-z0-9_./{}:-]{0,200}$")
NAME = re.compile(r"^[A-Za-z0-9_./-]{1,100}$")
METHOD = re.compile(r"^[A-Z]{1,10}$")
TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,200}$")
FRAME_PATH = re.compile(r"^[A-Za-z0-9_]{1,100}(/[A-Za-z0-9_]{1,100}){0,5}\.py$")
FUNCTION = re.compile(r"^[A-Za-z0-9_.<>]{1,200}$")
## the MCP paths that carry no JSON-RPC body
MCP_REQUESTS = frozenset({"GET", "DELETE"})


@dataclass(frozen=True)
class Route:
    template: str
    methods: frozenset[str]
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Names:
    """The worker's route templates, tool names and the MCP methods it knows.

    Every worker runs the same code, so the names of one serve them all.
    """

    routes: tuple[Route, ...]
    tools: frozenset[str]
    methods: frozenset[str]

    @classmethod
    def from_report(cls, report: Any) -> "Names":
        """The names in the worker's answer to STATS_NAMES_PATH, ValueError when it has any
        that does not look like a name."""
        if not isinstance(report, dict):
            raise ValueError("not an object")
        routes = []
        for route in report.get("routes", []):
            if not isinstance(route, dict) or not isinstance(route.get("methods"), list):
                raise ValueError("not a route")
            template, methods = route.get("path"), route["methods"]
            if not isinstance(template, str) or not TEMPLATE.match(template):
                raise ValueError("not a route template")
            if not all(isinstance(m, str) and METHOD.match(m) for m in methods):
                raise ValueError("not an HTTP method")
            pattern, _, _ = compile_path(template)
            routes.append(Route(template, frozenset(methods), pattern))
        tools = report.get("tools", [])
        methods = report.get("methods", [])
        if not isinstance(tools, list) or not isinstance(methods, list):
            raise ValueError("not a list of names")
        for name in (*tools, *methods):
            if not isinstance(name, str) or not NAME.match(name):
                raise ValueError("not a name")
        return cls(tuple(routes), frozenset(tools), frozenset(methods))

    def ui_operation(self, method: str, path: str) -> str:
        """METHOD TEMPLATE of the first route that takes the request, else OTHER."""
        for route in self.routes:
            if method in route.methods and route.pattern.match(path):
                return f"{method} {route.template}"
        return OTHER

    def mcp_operation(self, message: Any) -> str:
        """The tool name of a tools/call, else the method, each only when the worker knows it."""
        if not isinstance(message, dict):
            return OTHER
        method = message.get("method")
        if method == "tools/call":
            params = message.get("params")
            name = params.get("name") if isinstance(params, dict) else None
            return name if isinstance(name, str) and name in self.tools else OTHER
        return method if isinstance(method, str) and method in self.methods else OTHER


@dataclass(frozen=True)
class Call:
    """One JSON-RPC call of an MCP request: its operation, and its id as JSON text (None for a
    notification), which only matches it to its answer and is never kept."""

    operation: str
    id: str | None


def json_id(message: Any) -> str | None:
    if not isinstance(message, dict) or "id" not in message:
        return None
    value = message["id"]
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    return json.dumps(value)


def messages(body: bytes) -> list[Any]:
    """The JSON-RPC messages of a body, a batch's each, an empty list for anything else."""
    try:
        found = json.loads(body)
    except ValueError:
        return []
    return found[:MAX_CALLS] if isinstance(found, list) else [found]


def mcp_calls(names: Names | None, method: str, body: bytes | None) -> list[Call]:
    """The calls of an MCP request: one per message of its body, a GET or DELETE as itself."""
    if method != "POST":
        return [Call(f"{method} /mcp" if method in MCP_REQUESTS else OTHER, None)]
    found = messages(body) if body is not None else []
    if not found:
        return [Call(OTHER, None)]
    if names is None:
        return [Call(OTHER, json_id(message)) for message in found]
    return [Call(names.mcp_operation(message), json_id(message)) for message in found]


def answer_messages(content_type: str, body: bytes) -> list[Any]:
    """The JSON-RPC messages of an answer: a JSON body, or the data of each server-sent event."""
    if content_type.startswith("text/event-stream"):
        found: list[Any] = []
        for event in body.replace(b"\r\n", b"\n").split(b"\n\n"):
            data = b"\n".join(
                line[5:].lstrip(b" ") for line in event.split(b"\n") if line.startswith(b"data:")
            )
            if data:
                found += messages(data)
        return found
    return messages(body)


def outcomes(content_type: str, body: bytes | None) -> dict[str, bool]:
    """Whether each answered id succeeded: a result that is no tool error, and no error object."""
    if body is None:
        return {}
    found = {}
    for message in answer_messages(content_type, body):
        key = json_id(message)
        if key is None:
            continue
        result = message.get("result")
        tool_error = isinstance(result, dict) and result.get("isError") is True
        found[key] = "error" not in message and "result" in message and not tool_error
    return found


@dataclass(frozen=True)
class ErrorReport:
    exception_type: str
    ## (path relative to the package, function, line), innermost last
    frames: tuple[tuple[str, str, int], ...]


def error_report(value: str | None) -> ErrorReport | None:
    """The worker's error header, None when it has anything but a type and code locations."""
    if not value:
        return None
    try:
        report = json.loads(value)
    except ValueError:
        return None
    if not isinstance(report, dict) or set(report) != {"type", "frames"}:
        return None
    kind, frames = report["type"], report["frames"]
    if not isinstance(kind, str) or not TYPE.match(kind) or not isinstance(frames, list):
        return None
    kept = []
    for frame in frames[-MAX_FRAMES:]:
        if not (isinstance(frame, list) and len(frame) == 3):
            return None
        path, function, line = frame
        if not (isinstance(path, str) and FRAME_PATH.match(path)):
            return None
        if not (isinstance(function, str) and FUNCTION.match(function)):
            return None
        if isinstance(line, bool) or not isinstance(line, int):
            return None
        kept.append((path, function, line))
    return ErrorReport(kind, tuple(kept))
