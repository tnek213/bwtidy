"""What a worker tells the gateway for the usage stats (docs/spec.md, Usage stats).

Only a worker the gateway started with stats on does this (STATS_REPORT_ENV). It answers
STATS_NAMES_PATH, which the gateway never proxies, with its route templates, its tool names
and the MCP methods it knows, and it gives an unhandled exception's 500 the header
STATS_ERROR_HEADER with the exception's type and its frames inside the bwtidy package. Never
its text, its arguments, its locals or a log line: those can hold what a person typed.
"""

import json
import logging
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.routing import iter_route_contexts
from mcp.types.methods import SPEC_CLIENT_METHODS, SPEC_CLIENT_NOTIFICATION_METHODS
from starlette.routing import Route

from bwtidy.protocol import STATS_ERROR_HEADER, STATS_NAMES_PATH
from bwtidy.stats.parse import MAX_FRAMES

log = logging.getLogger(__name__)

PACKAGE = Path(__file__).resolve().parents[1]
## the paths of the browser's door, the names of whose routes the gateway keeps
UI_PREFIX = "/api/"


def type_name(error: BaseException) -> str:
    kind = type(error)
    if kind.__module__ == "builtins":
        return kind.__qualname__
    return f"{kind.__module__}.{kind.__qualname__}"


def frames(error: BaseException) -> list[tuple[str, str, int]]:
    """(path relative to the package, function, line) of each frame inside the package, from
    the code objects alone."""
    kept = []
    for frame, line in traceback.walk_tb(error.__traceback__):
        path = Path(frame.f_code.co_filename)
        if path.is_absolute() and path.is_relative_to(PACKAGE):
            kept.append((path.relative_to(PACKAGE).as_posix(), frame.f_code.co_qualname, line))
    return kept[-MAX_FRAMES:]


def error_header(error: BaseException) -> str:
    report = {"type": type_name(error), "frames": frames(error)}
    return json.dumps(report, separators=(",", ":"))


def report_to_gateway(app: FastAPI, tool_names: Callable[[], Awaitable[list[str]]]) -> None:
    """Register the names route and the 500 with the error header. Call it before a mount at
    /, which would take the route's path."""

    async def answer_with_report(_request: Request, error: Exception) -> Response:
        headers = {}
        try:
            headers[STATS_ERROR_HEADER] = error_header(error)
        except Exception:
            log.warning("The error report for the usage stats failed")
        return PlainTextResponse("Internal Server Error", status_code=500, headers=headers)

    async def names(_request: Request) -> JSONResponse:
        ## the routes of every included router too, with their prefixes, in the order the
        ## worker matches them
        routes = [
            {"path": route.path, "methods": sorted(route.methods)}
            for route in iter_route_contexts(app.router.routes)
            if route.path and route.methods and route.path.startswith(UI_PREFIX)
        ]
        methods = sorted(SPEC_CLIENT_METHODS | SPEC_CLIENT_NOTIFICATION_METHODS)
        return JSONResponse(
            {"routes": routes, "tools": sorted(await tool_names()), "methods": methods}
        )

    app.add_exception_handler(Exception, answer_with_report)
    app.router.routes.append(Route(STATS_NAMES_PATH, names, methods=["GET"]))
