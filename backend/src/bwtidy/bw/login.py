"""Logs the Bitwarden CLI in from the web UI (docs/spec.md, Session and access).

One login at a time runs `bw config server` (when the server changes) and `bw login` in a
pseudo-terminal, so the CLI asks its questions (the one-time code bitwarden.com mails for a
new device) the way it does at a terminal, and the answers typed into the web UI are written
to it. The master password reaches the CLI only through the child's environment and stays in
RAM for this one login. Everything the CLI prints goes through Console, which masks the
password and the session key before the text is stored. Nothing of it is ever logged.

What bw 2026.10.0 does, checked against the dev stack: `bw serve` exits at once with
"You are not logged in." while the CLI is logged out, so its status can never say
unauthenticated and the CLI's own `bw status` is asked instead when bw serve is not
reachable. A bw serve that runs while the CLI logs out and in again keeps working. A bw
serve that starts while bw login runs reads the logged out state and writes it back, which
can drop the fresh login (bw login exits 0 and the CLI is unauthenticated afterwards), so bw
serve is stopped while the CLI runs.
`bw config server URL` while logged in fails with "Logout required before server config
update." The prompts are inquirer prompts on stderr that redraw their line for every typed
character with "erase line" and "cursor to column 1" sequences.
"""

import asyncio
import codecs
import contextlib
import json
import logging
import os
import pty
import re
import secrets
import time
from asyncio.subprocess import DEVNULL
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bwtidy.bw.client import BwClient, BwError
from bwtidy.bw.process import run_cli, stop_bw
from bwtidy.bw.serve import BwServeProcess
from bwtidy.login_state import LoginStateFile

log = logging.getLogger(__name__)

PASSWORD_ENV = "BWTIDY_LOGIN_PASSWORD"
DEFAULT_SERVER = "https://vault.bitwarden.com"
MASK = "••••••••"
LOGIN_TIMEOUT = 600.0
CLI_TIMEOUT = 60.0
LOGOUT_TIMEOUT = 60.0
## how long an unfinished line waits for more output before it is shown, so that a prompt
## redrawn for every typed character is seen once, with the whole answer
SETTLE_DELAY = 0.05

## the key as `bw login` prints it: twice as BW_SESSION="..." and once after --session
SESSION_KEY = re.compile(r'(?<=BW_SESSION=")[^"\s]+|(?<=--session )\S+')
## a CSI control sequence: ESC [ parameters, final byte
CSI = re.compile(r"\x1b\[([0-9;?]*)([@-~])")
## an escape sequence cut off by the end of a chunk
CSI_PREFIX = re.compile(r"\x1b(\[[0-9;?]*)?\Z")


class LoginRefused(Exception):
    """A login cannot start now (one runs, or the CLI is logged in already)."""


class WrongAccount(Exception):
    """A worker's account is fixed, and the login names another server or email."""


class NoLogin(Exception):
    """No login with that token."""


class NotRunning(Exception):
    """The CLI is not running, so there is nothing to write to."""


class NotFinished(Exception):
    """The CLI has not exited with code 0 (yet)."""


@dataclass
class BwLoginState:
    logged_in: bool
    server: str
    email: str
    ## a worker's server and email come from its settings and cannot be changed
    account_fixed: bool = False


def _normalized(url: str | None) -> str:
    return (url or "").strip().rstrip("/").lower()


def _normalized_email(email: str | None) -> str:
    return (email or "").strip().lower()


class Console:
    """The console text of one login, built from the pty bytes the way a terminal shows them.

    The text is append-only, so the web UI polls it by offset. Within the current line, a
    carriage return, an erased line and cursor moves are applied before the line is shown:
    commit() shows the line as it looks then. A line redrawn with other content than the
    part already shown goes below it. Every line is masked before it is stored.
    """

    def __init__(self, secrets: list[str]) -> None:
        self._secrets = [s for s in secrets if s]
        self._found_keys: set[str] = set()
        self.text = ""
        ## where the current line begins in text, and the line as the terminal shows it
        self._line_start = 0
        self._line = ""
        self._col = 0
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._escape = ""

    def feed(self, data: bytes) -> None:
        chunk = self._escape + self._decoder.decode(data)
        self._escape = ""
        i = 0
        while i < len(chunk):
            char = chunk[i]
            if char == "\x1b":
                if CSI_PREFIX.match(chunk, i):
                    self._escape = chunk[i:]
                    return
                match = CSI.match(chunk, i)
                if match:
                    self._csi(match.group(1), match.group(2))
                    i = match.end()
                else:
                    ## another escape: drop it with its one argument
                    i += 2
                continue
            i += 1
            if char == "\n":
                self.commit()
                self.text += "\n"
                self._line_start = len(self.text)
                self._line = ""
                self._col = 0
            elif char == "\r":
                self._col = 0
            elif char == "\x08":
                self._col = max(0, self._col - 1)
            elif char == "\t" or char >= " ":
                self._put(char)

    def _put(self, char: str) -> None:
        line, col = self._line, self._col
        if col < len(line):
            self._line = line[:col] + char + line[col + 1 :]
        else:
            self._line = line + " " * (col - len(line)) + char
        self._col = col + 1

    def _csi(self, params: str, final: str) -> None:
        count = int(params) if params.isdigit() else 1
        if final == "K":
            if params in ("", "0"):
                self._line = self._line[: self._col]
            elif params == "1":
                self._line = " " * self._col + self._line[self._col :]
            elif params == "2":
                self._line = ""
        elif final == "G":
            self._col = count - 1
        elif final == "D":
            self._col = max(0, self._col - count)
        elif final == "C":
            self._col += count

    def commit(self) -> None:
        """Show the current line, masked, as far as it is not shown yet."""
        line = self._mask(self._line)
        shown = self.text[self._line_start :]
        if line.startswith(shown):
            self.text += line[len(shown) :]
        elif not shown.startswith(line):
            self.text += "\n" + line
            self._line_start = len(self.text) - len(line)

    def _mask(self, line: str) -> str:
        for key in SESSION_KEY.findall(line):
            self._found_keys.add(key)
        for secret in [*self._secrets, *self._found_keys]:
            line = line.replace(secret, MASK)
        return line


class BwLogin:
    def __init__(
        self,
        bw_bin: str,
        client: BwClient,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = LOGIN_TIMEOUT,
        settle: float = SETTLE_DELAY,
        bw_serve: BwServeProcess | None = None,
        account: tuple[str, str] | None = None,
        login_state: LoginStateFile | None = None,
    ) -> None:
        self._bw_bin = bw_bin
        self._account = account
        self._login_state = login_state
        self._client = client
        self._clock = clock
        self._timeout = timeout
        self._settle = settle
        self.bw_serve = bw_serve
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._password: str | None = None
        self._console: Console | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._master: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._settle_handle: asyncio.TimerHandle | None = None
        self._exit_code: int | None = None
        self._deadline = 0.0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def in_progress(self) -> bool:
        """A login was started and is neither finished, failed nor dropped yet."""
        return self._token is not None or self.running

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    @property
    def password(self) -> str | None:
        return self._password

    async def cli_status(self) -> dict[str, Any]:
        """`bw status` of the CLI itself: {} when bw cannot be run or prints no JSON."""
        try:
            _, stdout, _ = await run_cli(
                self._bw_bin, ["status"], timeout=CLI_TIMEOUT, stderr=DEVNULL
            )
        except (OSError, TimeoutError) as error:
            log.warning("bw status failed: %s", type(error).__name__)
            return {}
        try:
            status = json.loads(stdout)
        except ValueError:
            return {}
        return status if isinstance(status, dict) else {}

    async def _status(self) -> dict[str, Any]:
        """The CLI's status, from bw serve when it answers and knows a server, else `bw status`.

        bw serve is cheap but runs only while logged in. A bw serve that saw the CLI log out
        answers with serverUrl null, so that is asked again at the CLI.
        """
        try:
            status = await self._client.status()
        except BwError:
            status = {}
        if status.get("status") == "unauthenticated" or not status.get("serverUrl"):
            status = await self.cli_status()
        return status

    @staticmethod
    def _logged_in(status: dict[str, Any]) -> bool:
        return bool(status) and status.get("status") != "unauthenticated"

    def note(self, logged_in: bool) -> None:
        """Tell the gateway whether the CLI is logged in (login_state.py)."""
        if self._login_state is not None:
            self._login_state.record(logged_in)

    def _note_status(self, status: dict[str, Any]) -> None:
        ## no status at all (bw could not run) says nothing about the login
        if status:
            self.note(self._logged_in(status))

    def _state_of(self, status: dict[str, Any]) -> BwLoginState:
        logged_in = self._logged_in(status)
        if self._account is not None:
            server, email = self._account
            return BwLoginState(logged_in, server, email, account_fixed=True)
        ## the browser prefills the form from its own memory first, this is the fallback
        server = status.get("serverUrl") or DEFAULT_SERVER
        email = status.get("userEmail") or "" if logged_in else ""
        return BwLoginState(logged_in=logged_in, server=server, email=email)

    async def state(self) -> BwLoginState:
        """Whether the CLI is logged in, and the server and email to prefill the login form."""
        status = await self._status()
        self._note_status(status)
        return self._state_of(status)

    async def log_out(self) -> int:
        """Log the CLI out and answer bw logout's exit code. The session must have ended before.

        bw serve is stopped first: one that sees the CLI log out stays up but hangs on the next
        unlock. It is started again afterwards and keeps retrying until the next login.
        """
        if self.bw_serve is not None:
            await self.bw_serve.stop()
        try:
            code = await run_logout(self._bw_bin)
            if code == 0:
                self.note(False)
            return code
        finally:
            if self.bw_serve is not None:
                await self.bw_serve.start(wait=False)

    async def start(self, server: str, email: str, password: str) -> str:
        """Start the login and return its token.

        Raises WrongAccount for another account than a worker's, LoginRefused when it cannot
        run now.
        """
        self._check_account(server, email)
        async with self._lock:
            await self.expire_if_due()
            if self._token is not None:
                raise LoginRefused("A Bitwarden login is already running")
            status = await self._status()
            self._note_status(status)
            if self._logged_in(status):
                raise LoginRefused("The Bitwarden CLI is logged in already")
            self._token = secrets.token_urlsafe(32)
            self._password = password
            self._console = Console([password])
            self._exit_code = None
            self._deadline = self._clock() + self._timeout
            self._task = asyncio.create_task(self._run(server, email, status.get("serverUrl")))
            log.info("Bitwarden login started")
            return self._token

    def _check_account(self, server: str, email: str) -> None:
        if self._account is None:
            return
        fixed_server, fixed_email = self._account
        if _normalized(server) != _normalized(fixed_server) or _normalized_email(
            email
        ) != _normalized_email(fixed_email):
            raise WrongAccount(f"This bwtidy worker belongs to {fixed_email} on {fixed_server}.")

    def check(self, token: str | None) -> None:
        """Raise NoLogin unless token is the running login's."""
        if (
            token is None
            or self._token is None
            or not secrets.compare_digest(token.encode(), self._token.encode())
        ):
            raise NoLogin()

    def output(self, after: int) -> dict[str, Any]:
        """The console from offset after on. A failed login is forgotten once it is reported."""
        assert self._console is not None
        text = self._console.text
        result = {
            "text": text[max(after, 0) :],
            "next": len(text),
            "running": self.running,
            "exitCode": self._exit_code,
        }
        if not self.running and self._exit_code != 0:
            self.forget()
        return result

    def write(self, text: str) -> None:
        self._write_line(text)

    def write_password(self) -> None:
        assert self._password is not None
        self._write_line(self._password)

    def _write_line(self, text: str) -> None:
        if not self.running or self._proc is None or self._master is None:
            raise NotRunning()
        os.write(self._master, (text + "\n").encode())

    async def cancel(self) -> None:
        """Kill the CLI if it runs and forget the password and the token."""
        await self._kill()
        self.forget()
        log.info("Bitwarden login cancelled")

    def password_for_finish(self) -> str:
        """The kept password, only once the CLI exited with code 0."""
        if self.running or self._exit_code != 0 or self._password is None:
            raise NotFinished()
        return self._password

    def forget(self) -> None:
        self._token = None
        self._password = None
        self._exit_code = None
        if not self.running:
            self._console = None
            self._task = None

    async def expire_if_due(self) -> None:
        if self._token is not None and self._clock() >= self._deadline:
            log.info("Bitwarden login timed out")
            await self._kill()
            self.forget()

    async def close(self) -> None:
        await self._kill()
        self.forget()

    async def _kill(self) -> None:
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self, server: str, email: str, current_server: str | None) -> None:
        bw_serve = self.bw_serve
        try:
            if bw_serve is not None:
                await bw_serve.stop()
            await self._run_cli(server, email, current_server)
        finally:
            if bw_serve is not None:
                await bw_serve.start(wait=False)

    async def _run_cli(self, server: str, email: str, current_server: str | None) -> None:
        loop = asyncio.get_running_loop()
        master, slave = pty.openpty()
        os.set_blocking(master, False)
        self._master = master
        loop.add_reader(master, self._on_readable)
        try:
            code = 0
            if _normalized(server) != _normalized(current_server):
                code = await self._command([self._bw_bin, "config", "server", server], slave)
            if code == 0:
                env = {**os.environ, PASSWORD_ENV: self._password or ""}
                argv = [self._bw_bin, "login", email, "--passwordenv", PASSWORD_ENV]
                code = await self._command(argv, slave, env)
                if code == 0:
                    self.note(True)
            self._exit_code = code
            log.info("Bitwarden login exited with code %d", code)
        except asyncio.CancelledError:
            proc = self._proc
            if proc is not None:
                await stop_bw(proc)
            raise
        except OSError as error:
            ## str(error) names the binary, never anything the CLI printed
            log.warning("Bitwarden login could not run the CLI: %s", error)
            self._exit_code = 127
        finally:
            self._cancel_settle()
            loop.remove_reader(master)
            self._master = None
            os.close(master)
            os.close(slave)
            self._proc = None

    async def _command(self, argv: list[str], slave: int, env: dict[str, str] | None = None) -> int:
        self._proc = await asyncio.create_subprocess_exec(
            *argv, stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True
        )
        code = await self._proc.wait()
        self._drain()
        return code

    def _on_readable(self) -> None:
        if self._master is None or self._console is None:
            return
        try:
            data = os.read(self._master, 65536)
        except BlockingIOError:
            return
        except OSError:
            return
        self._console.feed(data)
        self._cancel_settle()
        self._settle_handle = asyncio.get_running_loop().call_later(
            self._settle, self._console.commit
        )

    def _drain(self) -> None:
        """Read what the CLI printed before it exited, then show the last line."""
        if self._master is None or self._console is None:
            return
        while True:
            try:
                data = os.read(self._master, 65536)
            except (BlockingIOError, OSError):
                break
            if not data:
                break
            self._console.feed(data)
        self._cancel_settle()
        self._console.commit()

    def _cancel_settle(self) -> None:
        if self._settle_handle is not None:
            self._settle_handle.cancel()
            self._settle_handle = None


async def run_logout(bw_bin: str) -> int:
    try:
        code, _, _ = await run_cli(
            bw_bin, ["logout"], timeout=LOGOUT_TIMEOUT, stdout=DEVNULL, stderr=DEVNULL
        )
    ## TimeoutError is an OSError, so it goes first
    except TimeoutError:
        log.warning("bw logout did not finish within %.0f s", LOGOUT_TIMEOUT)
        return 124
    except OSError as error:
        log.warning("bw logout could not run: %s", error)
        return 127
    return code
