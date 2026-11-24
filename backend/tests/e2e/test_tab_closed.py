"""A worker whose tab is closed ends its session and exits, also while bw serve answers nothing
(docs/spec.md, Session and access and Worker mode).

The test waits for the heartbeat timeout, so it takes about four minutes. The file puts the
items back at its end (file_put_back): the edit the session's end gave up on is committed at the
next unlock.
"""

import signal
import subprocess
import time

import pytest
from playwright.sync_api import BrowserContext, Page
from support.api import Api, rows_by_name
from support.elsewhere import DEV_VAULT
from support.login import enter_account, log_in
from support.pages import grid, search, type_into

pytestmark = pytest.mark.usefixtures("file_put_back")

ACCOUNT_DIR = "/data/accounts/vaultwarden/dev@bwtidy.test"
## the gateway of scripts/e2e sets no BWTIDY_HEARTBEAT_TIMEOUT, the default holds
HEARTBEAT_TIMEOUT = 120
## the watcher's check interval, the session end's commit limit, the wait for the cancelled
## commit, the wait for the background backup, the lock's limit and the stop of bw serve
## (docs/spec.md, Worker mode), with a margin
GONE_WITHIN = HEARTBEAT_TIMEOUT + 10 + 60 + 10 + 30 + 10 + 5 + 25
## long enough that nothing commits on its own while the test runs
NEVER = 3600

## the account's worker and its bw serve, found in /proc like scripts/devlib/gateway.py does. The
## worker runs as the account directory's uid (another uid's environ needs a ptrace right root
## lacks in the container).
PROCESSES = """
import os, sys
account = sys.argv[1].encode()
uid = os.stat(account).st_uid
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        args = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\\0")
        owner = os.stat(f"/proc/{pid}").st_uid
    except OSError:
        continue
    if b"/home/app/venv/bin/bwtidy" in args and owner == uid:
        print("worker", pid)
    elif b"serve" in args and any(a.startswith(b"unix://" + account + b"/") for a in args):
        print("bwserve", pid)
"""
## a process that is gone already is no error
SIGNAL = """
import contextlib, os, sys
with contextlib.suppress(ProcessLookupError):
    os.kill(int(sys.argv[2]), int(sys.argv[1]))
"""


def as_root(*args: str) -> str:
    result = subprocess.run(
        [str(DEV_VAULT), "exec", "--root", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"scripts/dev-vault exec exited with {result.returncode}"
    return result.stdout


def processes() -> dict[str, int]:
    words = as_root("python3", "-c", PROCESSES, ACCOUNT_DIR).split()
    return dict(zip(words[::2], map(int, words[1::2]), strict=True))


def send(sig: int, pid: int) -> None:
    as_root("python3", "-c", SIGNAL, str(sig), str(pid))


def worker_gone(api: Api) -> bool:
    return "worker" not in processes() and not api.get("/gateway/account")["running"]


def test_a_closed_tab_ends_the_session_and_the_worker_exits_while_bw_serve_hangs(
    unlocked_page: Page, api: Api, context: BrowserContext
):
    page = unlocked_page
    before = api.get("/api/queue/auto-commit")
    settings = {"afterSeconds": before["afterSeconds"], "maxEntries": before["maxEntries"]}
    api.put("/api/queue/auto-commit", {**settings, "afterSeconds": NEVER})
    item_id = rows_by_name(api)["Task board"]["id"]
    search(page, "Task board")
    type_into(page, item_id, "login.username", "tasks-tab-closed")
    deadline = time.monotonic() + 30
    while not api.get("/api/queue?status=pending") and time.monotonic() < deadline:
        time.sleep(0.5)
    assert api.get("/api/queue?status=pending"), "the grid edit was not queued"
    running = processes()
    assert set(running) == {"worker", "bwserve"}
    ## the heartbeats stop with the page, the context keeps the routing cookie
    gateway = Api(context.request)
    page.close()
    send(signal.SIGSTOP, running["bwserve"])
    try:
        deadline = time.monotonic() + GONE_WITHIN
        while not worker_gone(gateway) and time.monotonic() < deadline:
            time.sleep(5)
        assert worker_gone(gateway), f"the worker still runs {GONE_WITHIN} s after the tab closed"
    finally:
        send(signal.SIGCONT, running["bwserve"])
    ## the next worker commits the edit at its unlock, the file puts it back at its end
    page = context.new_page()
    enter_account(page)
    log_in(page)
    grid(page)
    Api(page.request).put("/api/queue/auto-commit", settings)
