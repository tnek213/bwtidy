import asyncio
import os
import signal
import socket
import sys
from pathlib import Path

import httpx2 as httpx

from bwtidy.bw.client import BwClient
from bwtidy.bw.serve import BwServeProcess
from tests.support.hooks import nothing_async

FAKE_BW = f"""#!{sys.executable}
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

args = sys.argv[1:]
assert args[0] == "serve"
assert args[args.index("--hostname") + 1] == "127.0.0.1"
port = int(args[args.index("--port") + 1])


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({{"success": True, "data": {{"template": {{"status": "locked"}}}}}})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_starts_restarts_and_stops(tmp_path: Path):
    bw_bin = tmp_path / "bw"
    bw_bin.write_text(FAKE_BW)
    bw_bin.chmod(0o755)
    port = free_port()

    async def scenario() -> None:
        restarts = []

        async def on_restart() -> None:
            restarts.append(True)

        http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=2)
        process = BwServeProcess(
            str(bw_bin),
            port,
            BwClient(http),
            on_restart=on_restart,
            ready_timeout=15,
            poll_interval=0.05,
            restart_delay=0.05,
        )
        await process.start()
        assert process.ready
        first = process.pid
        assert first is not None

        os.kill(first, signal.SIGKILL)
        for _ in range(300):
            await asyncio.sleep(0.05)
            if process.ready and process.pid != first:
                break
        assert process.pid != first
        assert process.ready
        assert restarts == [True]

        await process.stop()
        assert not process.ready
        await asyncio.sleep(0.2)
        assert restarts == [True]
        await http.aclose()

    asyncio.run(scenario())


## bw serve 2026.10.0 while the CLI is logged out: it prints this and exits at once
LOGGED_OUT_BW = f"""#!{sys.executable}
import sys
print("You are not logged in.", file=sys.stderr)
sys.exit(1)
"""


def test_start_returns_as_soon_as_bw_serve_exits_without_answering(tmp_path: Path):
    bw_bin = tmp_path / "bw"
    bw_bin.write_text(LOGGED_OUT_BW)
    bw_bin.chmod(0o755)

    async def scenario() -> None:
        http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{free_port()}", timeout=1)
        process = BwServeProcess(
            str(bw_bin),
            1,
            BwClient(http),
            on_restart=nothing_async,
            ready_timeout=30,
            poll_interval=0.05,
            restart_delay=5,
        )
        started = asyncio.get_running_loop().time()
        await process.start()
        assert asyncio.get_running_loop().time() - started < 5
        assert not process.ready
        await process.stop()
        await http.aclose()

    asyncio.run(scenario())


def test_missing_binary_does_not_crash_start(tmp_path: Path):
    async def scenario() -> None:
        http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{free_port()}", timeout=1)
        process = BwServeProcess(
            str(tmp_path / "missing"),
            1,
            BwClient(http),
            on_restart=nothing_async,
            ready_timeout=0.2,
            restart_delay=0.05,
        )
        await process.start()
        assert not process.ready
        await process.stop()
        await http.aclose()

    asyncio.run(scenario())
