"""The gateway in a dev stack's app container: how it starts and stops safely, and its run."""

import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from typing import IO

from devlib import health, seed
from devlib.paths import DEV_VAULT, ROOT
from devlib.stacks import app_port, dev_vault_run, stack_env, stack_up, tls_dir

## the venv belongs to uid 1000, so it is synced as that user before root runs the gateway from it
SYNC = "cd /src/backend && uv sync --locked --quiet"
GATEWAY_BIN = "/home/app/venv/bin/bwtidy-gateway"
WORKER_BIN = "/home/app/venv/bin/bwtidy"
## as root, so each account's worker runs as its own uid (docs/spec.md, Gateway). Bytecode would
## land root-owned in the mounted worktree.
BACKEND = (
    "cd /src/backend && export PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1"
    f" BWTIDY_ALLOWED_SERVERS=https://vaultwarden && exec {GATEWAY_BIN}"
)
## a clean stop commits pending edits first, which can wait for a backup
STOP_TIMEOUT = 150

## the container has no ps and no kill binary, so its own Python finds the processes in /proc:
## those with an argument equal to a name given, or starting with one that ends in *
SIGNAL_BACKEND = """
import os, sys
names = [name.encode() for name in sys.argv[2:]]
def match(arg):
    return any(arg.startswith(n[:-1]) if n.endswith(b"*") else arg == n for n in names)
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        args = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\\0")
    except OSError:
        continue
    if any(match(arg) for arg in args):
        os.kill(int(pid), int(sys.argv[1]))
"""
## what a kill leaves behind besides the gateway: the workers and their bw serve
LEFTOVERS = [WORKER_BIN, "unix:///data/accounts/*"]


def start_backend(stack: int, stdout: int | IO) -> subprocess.Popen:
    """Start the gateway as root in the stack's app container, its output to stdout."""
    ## its own session, so a Ctrl+C reaches only the script, which then stops it cleanly
    return subprocess.Popen(
        [str(DEV_VAULT), "exec", "--root", "sh", "-c", BACKEND],
        env=stack_env(stack),
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def signal_backend(stack: int, signal: int, *names: str) -> None:
    """Signal the stack's gateway (or the processes named), as root like the gateway itself."""
    dev_vault_run(
        stack,
        "exec",
        "--root",
        "python3",
        "-c",
        SIGNAL_BACKEND,
        str(signal),
        *(names or [GATEWAY_BIN]),
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


def sync_venv(stack: int) -> None:
    dev_vault_run(stack, "exec", "sh", "-c", SYNC, stdin=subprocess.DEVNULL, check=True)


def stop_backend(stack: int, backend: subprocess.Popen, label: str) -> None:
    """SIGTERM in the container: killing the docker exec client leaves the backend running.

    label starts the message of a backend that did not stop, "dev-app: the backend" say.
    """
    if backend.poll() is not None:
        return
    signal_backend(stack, 15)
    try:
        backend.wait(STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"{label} did not stop in {STOP_TIMEOUT} s, killing it", file=sys.stderr)
        signal_backend(stack, 9, GATEWAY_BIN, *LEFTOVERS)
        backend.kill()
        backend.wait()


def start_logged_backend(stack: int) -> tuple[subprocess.Popen, threading.Thread]:
    """Start the backend, its output to the terminal and appended to the stack's backend.log."""
    log_path = tls_dir(ROOT, stack) / "backend.log"
    log_path.parent.mkdir(exist_ok=True)
    log = log_path.open("ab")
    log.write(
        f"\n=== {datetime.now().astimezone():%Y-%m-%d %H:%M:%S %z} scripts/dev-app\n".encode()
    )
    log.flush()
    backend = start_backend(stack, subprocess.PIPE)

    def tee() -> None:
        assert backend.stdout is not None
        with log:
            for line in backend.stdout:
                sys.stdout.buffer.write(line)
                sys.stdout.flush()
                log.write(line)
                log.flush()

    thread = threading.Thread(target=tee, daemon=True)
    thread.start()
    return backend, thread


def open_page(url: str) -> None:
    if shutil.which("xdg-open") is None:
        print(f"dev-app: no xdg-open, open {url} yourself")
        return
    subprocess.Popen(
        ["xdg-open", url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def run_backend(
    stack: int,
    verify: Callable[[str], None] | None = None,
    *,
    open_browser: bool = True,
) -> int:
    """Run bwtidy's backend in the stack until Ctrl+C, and return the exit code.

    Brings the stack up (setup too when it was not up), then uses the backend that already
    answers or starts one, its output to the terminal and backend.log. verify(url) runs once it
    answers, a SystemExit from it stops the backend (only when this call started it, else it
    signals the one that runs) and goes on. The page opens with xdg-open when open_browser.
    """
    fresh = not stack_up(stack)
    if fresh:
        print("dev-app: the dev stack is not up, starting it", flush=True)
    ## up rebuilds the image, and compose recreates the app container only when it changed,
    ## so a git pull needs nothing else (a recreated container drops a running backend)
    dev_vault_run(stack, "up", check=True)
    if fresh:
        dev_vault_run(stack, "setup", check=True)
    url = f"http://127.0.0.1:{app_port(stack, 'dev-app')}"
    if health.healthy(url):
        print(f"dev-app: the backend already runs on {url}")
        if verify is not None:
            try:
                verify(url)
            except SystemExit:
                signal_backend(stack, 15)
                raise
        if open_browser:
            open_page(url)
        return 0
    sync_venv(stack)
    backend, output = start_logged_backend(stack)
    try:
        health.wait_healthy(stack, url, backend, "dev-app")
        print(f"\ndev-app: bwtidy runs on {url}")
        account = f"account {seed.EMAIL} on {seed.SERVER_URL}"
        print(f"dev-app: {account}, master password {seed.PASSWORD}")
        print(
            "dev-app: Ctrl+C stops the backend, scripts/dev-vault down removes the stack\n",
            flush=True,
        )
        if verify is not None:
            try:
                verify(url)
            except SystemExit:
                stop_backend(stack, backend, "dev-app: the backend")
                output.join()
                raise
        if open_browser:
            open_page(url)
        code = backend.wait()
        output.join()
        return code
    except KeyboardInterrupt:
        print("\ndev-app: stopping the backend", flush=True)
        stop_backend(stack, backend, "dev-app: the backend")
        output.join()
        print("dev-app: backend stopped, the stack is still up")
        return 0
