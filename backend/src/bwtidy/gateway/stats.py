"""The usage stats next to the proxy (docs/spec.md, Usage stats), only with BWTIDY_STATS=1.

`StatsMiddleware` wraps the proxy: for each request that reaches a worker it notes the status,
the time it took and, for an MCP POST, a copy of the body both ways up to BODY_LIMIT, and hands
that to the recorder, which makes the rows off the request path. Every message goes on as it
comes, so a streamed answer keeps streaming. The proxy marks the worker a request reached and
takes the worker's error header out of the answer (gateway/proxy.py).
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bwtidy.gateway.accounts import AccountKey
from bwtidy.gateway.proxy import ERROR_REPORT, REACHED, is_mcp_path, is_worker_path
from bwtidy.gateway.workers import Worker
from bwtidy.protocol import STATS_NAMES_PATH
from bwtidy.stats.db import stamp
from bwtidy.stats.parse import BODY_LIMIT, MCP, UI, Names
from bwtidy.stats.recorder import Recorder, Seen, utc_now

log = logging.getLogger(__name__)

## how long the names of a worker may take, the requests do not wait for them
NAMES_TIMEOUT = 5.0

type AccountLabel = Callable[[AccountKey], str]


class Copy:
    """A copy of a body up to BODY_LIMIT, None once it grew past it."""

    def __init__(self) -> None:
        self.data: bytearray | None = bytearray()

    def add(self, chunk: bytes) -> None:
        if self.data is None:
            return
        if len(self.data) + len(chunk) > BODY_LIMIT:
            self.data = None
            return
        self.data += chunk

    def value(self) -> bytes | None:
        return None if self.data is None else bytes(self.data)


class Answer:
    status = 500
    content_type = ""


class StatsMiddleware:
    def __init__(self, app: ASGIApp, recorder: Recorder, account: AccountLabel | None) -> None:
        self.app = app
        self.recorder = recorder
        self.account = account
        self._learning: asyncio.Task[None] | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        mcp = is_mcp_path(path)
        if not mcp and not is_worker_path(path):
            await self.app(scope, receive, send)
            return
        at = stamp(utc_now())
        started = time.monotonic()
        ## a GET /mcp is a long stream of server events, which counts as itself unread
        request_body = Copy() if mcp and scope["method"] == "POST" else None
        response_body = Copy() if request_body is not None else None
        ## without an answer the proxy raised, which the browser gets as a 500
        answer = Answer()

        async def receive_copied() -> Message:
            message = await receive()
            if request_body is not None and message["type"] == "http.request":
                request_body.add(message.get("body", b""))
            return message

        async def send_noted(message: Message) -> None:
            if message["type"] == "http.response.start":
                answer.status = message["status"]
                answer.content_type = content_type(message)
                self.learn_names(scope.get(REACHED))
            elif message["type"] == "http.response.body" and response_body is not None:
                response_body.add(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive_copied, send_noted)
        finally:
            worker = scope.get(REACHED)
            if isinstance(worker, Worker):
                self.note(
                    Seen(
                        at=at,
                        account=self.account_id(worker.key),
                        channel=MCP if mcp else UI,
                        method=scope["method"],
                        path=path,
                        status=answer.status,
                        duration_ms=round((time.monotonic() - started) * 1000),
                        request_body=request_body.value() if request_body else None,
                        content_type=answer.content_type,
                        response_body=response_body.value() if response_body else None,
                        error_header=scope.get(ERROR_REPORT),
                    )
                )

    def account_id(self, key: AccountKey) -> str:
        if self.account is None:
            return self.recorder.account_id(f"{key.server} {key.email}")
        try:
            return str(self.account(key))[:64]
        except Exception as error:
            log.warning("The usage stats' account label failed: %s", type(error).__name__)
            return self.recorder.account_id(f"{key.server} {key.email}")

    def note(self, seen: Seen) -> None:
        try:
            self.recorder.seen(seen)
        except Exception as error:
            log.warning("The usage stats could not note a request: %s", type(error).__name__)

    def learn_names(self, worker: object) -> None:
        """Ask the worker for its names once, in the background, until one answers."""
        if not isinstance(worker, Worker) or self.recorder.names is not None:
            return
        if self._learning is not None and not self._learning.done():
            return
        self._learning = asyncio.create_task(self.fetch_names(worker))

    async def fetch_names(self, worker: Worker) -> None:
        try:
            response = await worker.send("GET", STATS_NAMES_PATH, timeout=NAMES_TIMEOUT)
            try:
                body = await response.aread()
            finally:
                await response.aclose()
            if response.status_code != 200:
                raise ValueError(f"status {response.status_code}")
            self.recorder.names = Names.from_report(json.loads(body))
        except Exception as error:
            log.warning("The usage stats got no names from a worker: %s", type(error).__name__)


def content_type(message: Message) -> str:
    for name, value in message.get("headers", []):
        if name.lower() == b"content-type":
            return value.decode("latin-1").lower()
    return ""
