"""The gateway with the usage stats on and a fake worker that reports to it, for the tests of
gateway/stats.py and the recorder: the worker, the marker that must never reach stats.db and
the helpers to flush and read the file."""

import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bwtidy.gateway.app import create_gateway_app
from bwtidy.stats.recorder import Recorder
from tests.support.gateway_env import GatewayEnv, settings, wait_for, write_script

## what a person could have typed: no row of stats.db may hold it
MARKER = "zq7marker"
## the routes the fake worker names, in its order
ROUTES = [
    {"path": "/api/session", "methods": ["GET", "POST"]},
    {"path": "/api/bw-login", "methods": ["POST"]},
    {"path": "/api/grid", "methods": ["GET"]},
    {"path": "/api/items/{item_id}", "methods": ["PUT"]},
    {"path": "/api/fail", "methods": ["GET"]},
]
TOOLS = ["list_items", "propose_rename_item"]
METHODS = ["initialize", "notifications/initialized", "tools/call", "tools/list"]
## the fake worker answers this tool with a JSON-RPC error whose message holds the marker
FAILING_TOOL = "propose_rename_item"

## Serves its names, the routes above and /mcp, the way a real worker started with the stats on
## does: GET /api/fail raises an exception whose text and a local hold the marker and answers
## the 500 with the error header of bwtidy.stats.report. /mcp answers each JSON-RPC call with a
## result that holds the marker and the sha256 of the body it got, or with an error for
## FAILING_TOOL, a batch of notifications with 202.
## Every other request echoes its body and path.
STATS_WORKER = f"""#!{sys.executable}
import hashlib, json, os, signal, sys
from http.server import BaseHTTPRequestHandler
from socketserver import UnixStreamServer

from bwtidy.protocol import STATS_ERROR_HEADER, STATS_NAMES_PATH
from bwtidy.stats.report import error_header

path = os.environ["BWTIDY_SOCKET"]
signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))
NAMES = {{"routes": {ROUTES!r}, "tools": {TOOLS!r}, "methods": {METHODS!r}}}


def fail(secret):
    hidden = "{MARKER} local"
    raise KeyError("{MARKER} " + secret + hidden)


class Handler(BaseHTTPRequestHandler):
    wbufsize = -1

    def do_GET(self):
        if self.path == "/api/health":
            self.answer({{"status": "ok"}})
        elif self.path == STATS_NAMES_PATH:
            self.answer(NAMES)
        elif self.path == "/api/fail":
            try:
                fail("{MARKER} argument")
            except KeyError as error:
                header = error_header(error)
            self.answer({{"detail": "Internal Server Error"}}, 500, [(STATS_ERROR_HEADER, header)])
        else:
            self.answer({{"path": self.path, "{MARKER}": "{MARKER} value"}})

    def do_PUT(self):
        self.do_POST()

    def do_DELETE(self):
        self.answer({{}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if self.path != "/mcp":
            self.answer({{"path": self.path, "body": body.decode()}})
            return
        self.received = hashlib.sha256(body).hexdigest()
        message = json.loads(body)
        answers = [self.call(m) for m in (message if isinstance(message, list) else [message])]
        answers = [a for a in answers if a is not None]
        if not answers:
            self.answer(None, 202)
        else:
            self.answer(answers if isinstance(message, list) else answers[0])

    def call(self, message):
        if "id" not in message:
            return None
        name = (message.get("params") or {{}}).get("name")
        if name == "{FAILING_TOOL}":
            error = {{"code": -32000, "message": "{MARKER} refused"}}
            return {{"jsonrpc": "2.0", "id": message["id"], "error": error}}
        content = [{{"type": "text", "text": "{MARKER} item"}}]
        result = {{"content": content, "received": self.received}}
        return {{"jsonrpc": "2.0", "id": message["id"], "result": result}}

    def answer(self, data, status=200, extra=()):
        body = b"" if data is None else json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def address_string(self):
        return "unix"

    def log_message(self, *args):
        pass


if os.path.exists(path):
    os.unlink(path)
UnixStreamServer(path, Handler).serve_forever()
"""


class StatsEnv(GatewayEnv):
    @property
    def recorder(self) -> Recorder:
        recorder = self.gateway.recorder
        assert recorder is not None
        return recorder

    @property
    def stats_db(self) -> Path:
        return self.recorder.path

    def flush(self) -> None:
        """Wait for the worker's names, then write what was seen."""
        wait_for(lambda: self.recorder.names is not None, "the worker's names")
        self.recorder.flush()

    def rows(self, query: str) -> list[tuple[Any, ...]]:
        with sqlite3.connect(self.stats_db) as conn:
            return conn.execute(query).fetchall()

    def dump(self) -> str:
        with sqlite3.connect(self.stats_db) as conn:
            return "\n".join(conn.iterdump())


def make_stats_env(tmp_path: Path, **extra: Any) -> Iterator[StatsEnv]:
    worker = write_script(tmp_path / "stats-worker", STATS_WORKER)
    options: dict[str, Any] = {"stats": True, "worker_bin": worker, **extra}
    create_options = {
        name: options.pop(name) for name in ("stats_account", "stats_extra") if name in options
    }
    app = create_gateway_app(settings(tmp_path, **options), **create_options)
    with TestClient(app) as client:
        yield StatsEnv(client, app, tmp_path / "a")
