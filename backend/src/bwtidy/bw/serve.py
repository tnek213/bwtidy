"""Runs `bw serve` as a child process and restarts it when it exits."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from bwtidy.bw.client import BwClient, BwError
from bwtidy.bw.process import stop_bw

log = logging.getLogger(__name__)

MAX_RESTART_DELAY = 30.0


class BwServeProcess:
    def __init__(
        self,
        bw_bin: str,
        port: int,
        client: BwClient,
        on_restart: Callable[[], Awaitable[None]],
        ready_timeout: float = 30.0,
        poll_interval: float = 0.25,
        restart_delay: float = 1.0,
        socket: Path | None = None,
    ) -> None:
        self._bw_bin = bw_bin
        self._port = port
        self._socket = socket
        self._client = client
        self._on_restart = on_restart
        self._ready_timeout = ready_timeout
        self._poll_interval = poll_interval
        self._restart_delay = restart_delay
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._exited_early = asyncio.Event()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    async def start(self, wait: bool = True) -> None:
        """Start bw serve and wait until it answers. Keeps trying in the background on failure.

        bw serve exits at once with "You are not logged in." while the CLI is logged out, so
        it is retried with a growing delay until a login makes it stay up.
        """
        self._ready = asyncio.Event()
        self._exited_early = asyncio.Event()
        self._task = asyncio.create_task(self._supervise())
        if not wait:
            return
        ## a bw serve that exits before it answers (the CLI is logged out) ends the wait at once
        waits = [
            asyncio.ensure_future(self._ready.wait()),
            asyncio.ensure_future(self._exited_early.wait()),
        ]
        _, pending = await asyncio.wait(
            waits, timeout=self._ready_timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for future in pending:
            future.cancel()
        if not self._ready.is_set():
            log.warning("bw serve is not answering, still trying")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._proc is not None:
            await stop_bw(self._proc)
        self._ready.clear()

    def _argv(self) -> list[str]:
        """On a socket the port only feeds bw serve's Host check, which wants localhost:PORT."""
        hostname = "127.0.0.1" if self._socket is None else f"unix://{self._socket}"
        return [self._bw_bin, "serve", "--hostname", hostname, "--port", str(self._port)]

    def _remove_stale_socket(self) -> None:
        """Remove the socket file a killed bw serve left behind, else the new one cannot listen."""
        if self._socket is None:
            return
        try:
            if self._socket.is_socket():
                self._socket.unlink()
        except OSError as error:
            log.warning("Cannot remove the old bw serve socket: %s", error)

    async def _supervise(self) -> None:
        delay = self._restart_delay
        was_ready = False
        while True:
            self._remove_stale_socket()
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *self._argv(),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                )
            except OSError as error:
                log.error("Cannot start bw serve: %s", error)
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RESTART_DELAY)
                continue
            proc = self._proc
            log.info("Started bw serve (pid %d)", proc.pid)
            if await self._answers(proc):
                delay = self._restart_delay
                if was_ready:
                    try:
                        await self._on_restart()
                    except Exception:
                        log.exception("Restoring the session after a bw serve restart failed")
                was_ready = True
                self._ready.set()
            elif proc.returncode is None:
                log.warning("bw serve does not answer, stopping it")
                await stop_bw(proc)
            else:
                self._exited_early.set()
            code = await proc.wait()
            self._ready.clear()
            log.warning("bw serve exited with code %s, restarting in %.0f s", code, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RESTART_DELAY)

    async def _answers(self, proc: asyncio.subprocess.Process) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._ready_timeout
        while loop.time() < deadline:
            if proc.returncode is not None:
                return False
            try:
                await self._client.status()
                return True
            except BwError:
                await asyncio.sleep(self._poll_interval)
        return False
