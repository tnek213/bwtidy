"""Spawning and stopping a bw process without breaking the CLI state all bw processes share.

The spec's rule for every bw the backend starts (docs/spec.md, Worker mode): SIGTERM first,
SIGKILL only when it is still there 5 seconds later. `run_cli` is the one place a bw command is
spawned, waited for and stopped on a timeout or a cancel.
"""

import asyncio
import contextlib
from asyncio.subprocess import DEVNULL, PIPE

## as long as a bw takes to finish its write and go, and short enough for a hanging one
STOP_TIMEOUT = 5.0


async def run_cli(
    bw_bin: str,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float,
    stdout: int = PIPE,
    stderr: int = PIPE,
) -> tuple[int, bytes, bytes]:
    """Run one bw command to its end and answer (exit code, stdout, stderr).

    stdin is closed and --nointeraction set, so bw can never wait on a prompt. A command that
    runs longer than the timeout, or whose caller is cancelled, is stopped with `stop_bw`
    before the TimeoutError or CancelledError goes on. OSError and TimeoutError are raised as
    they are, the caller decides what they mean. A stream given as DEVNULL answers b"".
    """
    proc = await asyncio.create_subprocess_exec(
        bw_bin,
        *args,
        "--nointeraction",
        env=env,
        stdin=DEVNULL,
        stdout=stdout,
        stderr=stderr,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError):
        await stop_bw(proc)
        raise
    assert proc.returncode is not None
    return proc.returncode, out or b"", err or b""


async def stop_bw(proc: asyncio.subprocess.Process, timeout: float = STOP_TIMEOUT) -> None:
    """End a bw process with SIGTERM, with SIGKILL only when it is still there after the timeout.

    bw rewrites the whole data.json for a change (truncate, then write). SIGKILL between the two
    leaves an empty file, so the CLI is logged out and a running bw serve hangs on its next
    unlock, and it leaves data.json.lock, which holds up the others until it counts as stale.
    On SIGTERM node finishes the synchronous write and removes the lock (docs/spec.md, Worker
    mode).
    """
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
