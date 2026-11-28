"""The workers: one backend process per account, running as the account's uid."""

import asyncio
import errno
import logging
import os
import shutil
import signal
import stat
import time
from collections.abc import AsyncIterable, Awaitable, Callable
from pathlib import Path
from typing import Any, NoReturn

import httpx2 as httpx

from bwtidy.gateway.accounts import RUN_NAME_LENGTH, AccountKey, LimitError
from bwtidy.gateway.settings import GatewaySettings
from bwtidy.protocol import PENDING_MARKER, STATS_REPORT_ENV, WORKER_LOG

log = logging.getLogger(__name__)

HEALTH_POLL = 0.25
## a worker gets this long to end its session and stop bw serve after SIGTERM: a commit with a
## fresh backup, a background backup the session-end commit waits for, and that commit.
## compose.yaml's stop_grace_period must stay above it plus REQUEST_DRAIN (app.py) and bw
## serve's 5 seconds, else docker kills every bw at once.
STOP_TIMEOUT = 120.0
## how long opening an account waits for its worker that is on its way out
EXIT_WAIT = 30.0
TOO_MANY_WORKERS = "Too many accounts are open at once. Try again later."
## what a planted worker.log fails its open with: a link (ELOOP), a FIFO or a socket (ENXIO),
## a directory (EISDIR)
PLANTED_ERRNOS = frozenset({errno.ELOOP, errno.ENXIO, errno.EISDIR})
## the sockets of a worker and of its bw serve, in the account's socket directory
APP_SOCKET = "app.sock"
BW_SOCKET = "bw.sock"
## the bytes a Unix socket path holds on Linux: sun_path is 108 with its NUL
MAX_SOCKET_PATH = 107


class WorkerStartError(Exception):
    """The worker could not start or did not answer on its socket (503 for the browser)."""


class Unanswered(Exception):
    """A request over the worker's socket got no answer: httpx's error, and on which connection."""

    def __init__(self, error: httpx.TransportError, new_connection: bool) -> None:
        super().__init__(type(error).__name__)
        self.error = error
        self.new_connection = new_connection


class Worker:
    def __init__(
        self,
        key: AccountKey,
        path: Path,
        uid: int,
        proc: asyncio.subprocess.Process,
        run: Path,
    ) -> None:
        self.key = key
        self.path = path
        self.uid = uid
        self.proc = proc
        ## the socket directory, outside the account directory (run_directory)
        self.run = run
        self.started = time.monotonic()
        self.socket = run / APP_SOCKET
        ## the transport alone, never a client: a client keeps a cookie jar and default headers,
        ## which would carry one browser's session cookie into another browser's requests.
        ## No idle connection is kept: the worker's uvicorn closes one after 5 s, and a request
        ## sent on it just then is lost (a 502).
        self.transport = httpx.AsyncHTTPTransport(
            uds=str(self.socket),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=0),
        )
        ## set once the process exited and the gateway cleaned up after it
        self.gone = asyncio.Event()

    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def running(self) -> bool:
        return self.proc.returncode is None

    async def answers(self) -> bool:
        """Whether /api/health answers 200: a worker on its way out answers 503 or nothing."""
        try:
            response = await self.send("GET", "/api/health", timeout=5.0)
            await response.aclose()
        except Unanswered:
            return False
        return response.status_code == 200

    async def send(
        self,
        method: str,
        url: str,
        headers: list[tuple[str, str]] | None = None,
        content: AsyncIterable[bytes] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """A request over the socket, its body still to be read (and closed) by the caller.

        Unanswered when httpx fails to send it or to read the answer's head.
        """
        request = httpx.Request(method, "http://worker" + url, headers=headers, content=content)
        request.extensions["timeout"] = {"connect": 10.0, "read": timeout, "write": timeout}
        connected: list[str] = []

        async def trace(event: str, _info: dict[str, Any]) -> None:
            if event == "connection.connect_unix_socket.started":
                connected.append(event)

        request.extensions["trace"] = trace
        try:
            return await self.transport.handle_async_request(request)
        except httpx.TransportError as error:
            raise Unanswered(error, new_connection=bool(connected)) from error

    def describe(self) -> str:
        """The worker's pid, its age and whether its process still runs, for a log line."""
        age = time.monotonic() - self.started
        code = self.proc.returncode
        state = "running" if code is None else f"exited with code {code}"
        return f"pid {self.pid}, started {age:.1f} s ago, {state}"


def run_directory(settings: GatewaySettings, key: AccountKey) -> Path:
    """The account's socket directory: its path is as long for every host and email."""
    return settings.run_path / key.run_name


def longest_socket_path(settings: GatewaySettings) -> int:
    """The bytes of the longest socket path a worker gets, the same for every account."""
    run = settings.run_path / ("0" * RUN_NAME_LENGTH)
    return max(len(os.fsencode(run / name)) for name in (APP_SOCKET, BW_SOCKET))


def remove_entry(path: Path) -> None:
    """Remove a directory with what it holds, anything else (a symlink too) as itself."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def worker_env(settings: GatewaySettings, key: AccountKey, path: Path) -> dict[str, str]:
    """The worker's environment, built from scratch."""
    run = run_directory(settings, key)
    backups = {}
    if settings.backups_root is not None:
        backups["BWTIDY_BACKUPS_DIR"] = str(settings.backups_root / key.host_dir / key.email)
    ## only with the usage stats on does a worker report its names and its errors
    stats = {STATS_REPORT_ENV: "1"} if settings.stats else {}
    return {
        **settings.passed_on,
        "HOME": str(path / "home"),
        "BWTIDY_SOCKET": str(run / APP_SOCKET),
        "BWTIDY_BW_SOCKET": str(run / BW_SOCKET),
        "BWTIDY_DATA_DIR": str(path / "app"),
        "BITWARDENCLI_APPDATA_DIR": str(path / "bw"),
        "BWTIDY_ACCOUNT_SERVER": key.server,
        "BWTIDY_ACCOUNT_EMAIL": key.email,
        "BWTIDY_EXIT_AFTER_SESSION": "1",
        "BWTIDY_PENDING_MARKER": str(path / PENDING_MARKER),
        "BWTIDY_SECURE_COOKIES": "1" if settings.secure_cookies else "0",
        **backups,
        **stats,
    }


def open_log(log_path: Path, owner: int | None) -> int:
    """The worker's log opened for appending, WorkerStartError when it is not to be trusted.

    A link is not followed and the open never blocks. The file must be a regular file and,
    with an owner given, owned by it, unless the gateway just made the file (then it hands it
    to the owner). A planted link, FIFO, socket or directory is refused alike, as is whatever
    appears between a missing file and its creation. OSError when the open fails for another
    reason.
    """
    ## O_NONBLOCK makes a FIFO without a reader fail the open (ENXIO) instead of waiting for
    ## one, a FIFO with a reader opens at once and fails the regular file check. The flag
    ## changes nothing for the regular file the worker gets as its stdout.
    flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(log_path, flags)
        created = False
    except FileNotFoundError:
        try:
            fd = os.open(log_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            refuse_log(log_path)
        created = True
    except OSError as error:
        if error.errno not in PLANTED_ERRNOS:
            raise
        refuse_log(log_path)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            refuse_log(log_path)
        if owner is not None:
            if created:
                os.fchown(fd, owner, owner)
            elif info.st_uid != owner:
                refuse_log(log_path)
    except BaseException:
        os.close(fd)
        raise
    return fd


def refuse_log(log_path: Path) -> NoReturn:
    log.warning("Refused to start a worker, %s is not the account's regular file", log_path)
    raise WorkerStartError(f"The account's {WORKER_LOG} is not usable.")


class Workers:
    """One worker per account at a time, started as root and dropped to the account's uid."""

    def __init__(
        self, settings: GatewaySettings, on_exit: Callable[[Worker], Awaitable[None]]
    ) -> None:
        self._settings = settings
        self._on_exit = on_exit
        self._workers: dict[AccountKey, Worker] = {}
        ## accounts whose worker start is reserved but not yet in _workers
        self._starting: set[AccountKey] = set()
        self._watchers: set[asyncio.Task[None]] = set()
        ## the starts refused with TOO_MANY_WORKERS since the usage stats last took the count
        self._turned_away = 0

    def prepare(self) -> None:
        """The root of the socket directories, emptied: what is left in it serves no worker.

        Mode 0711 like the accounts directory, so a worker enters it to reach its own.
        """
        root = self._settings.run_path
        remove_entry(root)
        root.mkdir(mode=0o711, parents=True)
        root.chmod(0o711)

    def make_run(self, key: AccountKey, uid: int) -> Path:
        """The account's socket directory made anew, owned by its uid with mode 0700.

        Whatever was there goes first, as a former worker (or an account deleted and made
        again with a new uid) may have left it.
        """
        run = run_directory(self._settings, key)
        remove_entry(run)
        run.mkdir(mode=0o700)
        run.chmod(0o700)
        if self._settings.switch_uid:
            os.chown(run, uid, uid)
        return run

    def remove_run(self, key: AccountKey) -> None:
        """Remove the account's socket directory, unless another worker of it starts or runs."""
        if key in self._workers or key in self._starting:
            return
        try:
            remove_entry(run_directory(self._settings, key))
        except OSError as error:
            reason = error.strerror or type(error).__name__
            log.warning("The socket directory of %s could not be removed: %s", key.label, reason)

    def get(self, key: AccountKey) -> Worker | None:
        worker = self._workers.get(key)
        return worker if worker is not None and worker.running else None

    def at(self, path: Path) -> Worker | None:
        """The running worker of the account directory at this path."""
        return next((w for w in self._workers.values() if w.path == path and w.running), None)

    def count(self) -> int:
        """The workers that run or are starting."""
        running = sum(1 for worker in self._workers.values() if worker.running)
        return running + len(self._starting)

    def reserve(self, key: AccountKey) -> None:
        """Take a slot for the account's worker, LimitError when every slot is taken.

        The caller starts the worker right after (start uses the slot) or calls release. The
        count and the reservation happen without an await in between, so concurrent opens of
        different accounts cannot pass the cap together.
        """
        if self.get(key) is not None or key in self._starting:
            raise RuntimeError(f"A worker for {key.label} runs already")
        if self.count() >= self._settings.max_workers:
            log.warning("Refused to start a worker for %s, too many run", key.label)
            self._turned_away += 1
            raise LimitError(TOO_MANY_WORKERS)
        self._starting.add(key)

    def take_turned_away(self) -> int:
        """The starts refused with TOO_MANY_WORKERS since the last call."""
        count, self._turned_away = self._turned_away, 0
        return count

    def release(self, key: AccountKey) -> None:
        self._starting.discard(key)

    async def start(self, key: AccountKey, path: Path, uid: int) -> Worker:
        """Start the account's worker in its reserved slot, wait until /api/health answers."""
        if key not in self._starting:
            raise RuntimeError(f"No slot is reserved for the worker of {key.label}")
        try:
            proc = await self._spawn(key, path, uid)
        except OSError as error:
            self.release(key)
            self.remove_run(key)
            reason = error.strerror or type(error).__name__
            log.warning("The worker for %s could not start: %s", key.label, reason)
            raise WorkerStartError("The worker could not start.") from None
        except BaseException:
            self.release(key)
            self.remove_run(key)
            raise
        worker = Worker(key, path, uid, proc, run_directory(self._settings, key))
        ## from the reservation to the running worker without an await, so it is counted once
        self.release(key)
        self._workers[key] = worker
        log.info("Started the worker for %s (pid %d, uid %d)", key.label, proc.pid, uid)
        watcher = asyncio.create_task(self._watch(worker))
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watchers.discard)
        try:
            await self._wait_healthy(worker)
        except WorkerStartError:
            await self._kill(worker)
            raise
        return worker

    async def _spawn(self, key: AccountKey, path: Path, uid: int) -> asyncio.subprocess.Process:
        """Start the worker's process with the account's worker.log as its stdout and stderr.

        Its socket directory is made anew right before.

        The log is opened without following a link and must be the account's regular file: the
        account's uid owns the directory, so a worker could otherwise point root at any file.
        A worker never runs as root or below, whatever uid the account directory says.
        """
        switch = self._settings.switch_uid
        if switch and uid <= 0:
            log.warning("Refused to start a worker as uid %d for %s", uid, path)
            raise WorkerStartError("The worker's uid is not usable.")
        log_fd = open_log(path / WORKER_LOG, uid if switch else None)
        try:
            self.make_run(key, uid)
            return await asyncio.create_subprocess_exec(
                str(self._settings.worker_bin),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                cwd=path,
                env=worker_env(self._settings, key, path),
                umask=0o077,
                start_new_session=True,
                user=uid if switch else None,
                group=uid if switch else None,
                extra_groups=[] if switch else None,
            )
        finally:
            os.close(log_fd)

    async def _wait_healthy(self, worker: Worker) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._settings.start_timeout
        while loop.time() < deadline:
            if not worker.running:
                raise WorkerStartError("The worker exited before it answered.")
            if await worker.answers():
                return
            await asyncio.sleep(HEALTH_POLL)
        log.warning("The worker for %s did not answer, killing it", worker.key.label)
        raise WorkerStartError("The worker did not answer in time.")

    async def _watch(self, worker: Worker) -> None:
        try:
            code = await worker.proc.wait()
            log.info("The worker for %s exited with code %s", worker.key.label, code)
            if self._workers.get(worker.key) is worker:
                del self._workers[worker.key]
            ## before any await: a new worker of the account that starts after it keeps its own
            self.remove_run(worker.key)
            await worker.transport.aclose()
            try:
                await self._on_exit(worker)
            except Exception:
                log.exception("Cleaning up after the worker for %s failed", worker.key.label)
        finally:
            worker.gone.set()

    async def wait_gone(self, worker: Worker, timeout: float = STOP_TIMEOUT) -> bool:
        """Wait until the worker exited and its cleanup ran, False after the timeout."""
        try:
            await asyncio.wait_for(worker.gone.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def stop(self, worker: Worker) -> None:
        """Stop one worker cleanly (SIGTERM, then kill after STOP_TIMEOUT), wait for its cleanup."""
        if worker.running:
            worker.proc.terminate()
        if not await self.wait_gone(worker):
            log.warning("The worker for %s did not stop, killing it", worker.key.label)
            await self._kill(worker)
            await worker.gone.wait()

    async def _kill(self, worker: Worker) -> None:
        if worker.running:
            ## the whole group, so its bw serve (the unlocked vault in RAM) and a running CLI
            ## command die too. SIGKILL only: a SIGTERM's clean shutdown needs bw serve to commit.
            try:
                os.killpg(worker.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                worker.proc.kill()
        await worker.proc.wait()

    async def stop_all(self) -> None:
        """Stop every worker cleanly (SIGTERM, it ends its session), then wait for the cleanup."""
        workers = list(self._workers.values())
        for worker in workers:
            if worker.running:
                worker.proc.terminate()
        waits = [asyncio.ensure_future(w.proc.wait()) for w in workers]
        if waits:
            _, pending = await asyncio.wait(waits, timeout=STOP_TIMEOUT)
            for future in pending:
                future.cancel()
        for worker in workers:
            await self._kill(worker)
        if self._watchers:
            await asyncio.gather(*self._watchers, return_exceptions=True)
