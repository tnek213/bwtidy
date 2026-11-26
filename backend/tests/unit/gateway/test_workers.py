"""The gateway's workers (docs/spec.md, Gateway): their start, environment, log, kill and cap, with
a fake worker on the socket."""

import asyncio
import dataclasses
import json
import logging
import os
import re
import signal
import stat
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import bwtidy.bw.process as bwprocess
from bwtidy.gateway import workers as workers_module
from bwtidy.gateway.accounts import LimitError
from bwtidy.gateway.app import REQUEST_DRAIN
from bwtidy.gateway.workers import STOP_TIMEOUT
from bwtidy.protocol import ACCOUNT_COOKIE as COOKIE
from bwtidy.protocol import PENDING_MARKER, WORKER_LOG
from tests.support.gateway_env import (
    ALLOWED,
    EMAIL,
    FAKE_WORKER,
    KEEP_ALIVE_WORKER,
    LOGIN,
    STARTED,
    UNLOCK,
    GatewayEnv,
    account,
    existing_account,
    make_env,
    wait_for,
    write_script,
)

## a worker that never answers
SLEEPING_WORKER = f"""#!{sys.executable}
import time
time.sleep(3600)
"""


## a worker that never answers and has a child in its process group, as bw serve is, whose pid
## it writes to the file at CHILD_PID (outside the account directory, which goes with the worker)
SLEEPING_WITH_CHILD_WORKER = f"""#!{sys.executable}
import subprocess, time
child = subprocess.Popen(["sleep", "3600"])
with open("CHILD_PID", "w") as file:
    file.write(str(child.pid))
time.sleep(3600)
"""


def test_a_login_click_starts_the_worker_and_is_passed_on(env: GatewayEnv):
    token = env.cookie(env.open())
    response = env.request("POST", LOGIN, {COOKIE: token}, json={"password": "x"})
    assert response.status_code == 200, response.text
    assert response.json()["path"] == LOGIN
    assert json.loads(response.json()["body"]) == {"password": "x"}
    [pid] = env.worker_pids()
    assert response.json()["pid"] == pid
    assert env.seen() == [f"POST {LOGIN}"]
    assert env.account(token) == {
        "account": {"server": ALLOWED, "email": EMAIL},
        "running": True,
        "exists": True,
        "pending": True,
        "loggedIn": False,
        "firstRun": True,
    }
    ## the running worker gets the next requests, a click starts no second one
    assert env.session(token).json()["pid"] == pid
    assert env.click(token, UNLOCK).json()["pid"] == pid
    assert env.worker_pids() == [pid]


def test_an_unlock_click_starts_the_worker_of_an_existing_account(env: GatewayEnv):
    token = env.started()
    (env.directory() / PENDING_MARKER).unlink()
    [pid] = env.worker_pids()
    os.kill(pid, signal.SIGKILL)
    wait_for(lambda: env.worker_pids() == [], "the worker to exit")
    assert env.account(token)["running"] is False
    response = env.request("POST", UNLOCK, {COOKIE: token}, json={"password": "x"})
    assert response.status_code == 200, response.text
    assert json.loads(response.json()["body"]) == {"password": "x"}
    [new_pid] = env.worker_pids()
    assert response.json()["pid"] == new_pid != pid
    assert env.account(token)["running"] is True


def test_the_worker_gets_an_environment_built_from_scratch(env: GatewayEnv):
    token = env.started()
    environment = env.session(token).json()["env"]
    path = env.directory()
    ## python adds this one when it coerces the C locale at its start
    environment.pop("LC_CTYPE", None)
    assert environment == {
        "PATH": environment["PATH"],
        "HOME": str(path / "home"),
        "BWTIDY_SOCKET": str(path / "run" / "app.sock"),
        "BWTIDY_BW_SOCKET": str(path / "run" / "bw.sock"),
        "BWTIDY_DATA_DIR": str(path / "app"),
        "BITWARDENCLI_APPDATA_DIR": str(path / "bw"),
        "BWTIDY_ACCOUNT_SERVER": ALLOWED,
        "BWTIDY_ACCOUNT_EMAIL": EMAIL,
        "BWTIDY_EXIT_AFTER_SESSION": "1",
        "BWTIDY_PENDING_MARKER": str(path / PENDING_MARKER),
        "BWTIDY_SECURE_COOKIES": "0",
    }


def test_with_the_usage_stats_on_the_worker_is_told_to_report(tmp_path: Path):
    for env in make_env(tmp_path, stats=True):
        environment = env.session(env.started()).json()["env"]
        assert environment["BWTIDY_STATS_REPORT"] == "1"


def test_a_worker_that_never_answers_is_killed_and_503(tmp_path: Path):
    sleeping = write_script(tmp_path / "sleeping", SLEEPING_WORKER)
    for env in make_env(tmp_path, worker_bin=sleeping, start_timeout=0.3):
        response = env.click(env.cookie(env.open()))
        assert response.status_code == 503
        assert response.json() == {"detail": "The worker did not answer in time."}
        wait_for(env.directory_gone, "the pending directory to go")
        assert env.worker_pids() == []


def process_gone(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return True
    return state == "Z"


class Spawned:
    """Every worker process the gateway spawns, and the pid of each one whose kill() ran."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.procs: list[asyncio.subprocess.Process] = []
        self.kills: list[int] = []
        original = workers_module.Workers._spawn

        async def spawn(workers: Any, *args: Any) -> asyncio.subprocess.Process:
            proc = await original(workers, *args)
            real_kill = proc.kill

            def kill() -> None:
                self.kills.append(proc.pid)
                real_kill()

            monkeypatch.setattr(proc, "kill", kill)
            self.procs.append(proc)
            return proc

        monkeypatch.setattr(workers_module.Workers, "_spawn", spawn)


def test_the_kill_goes_to_the_worker_s_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The health timeout's kill takes the worker's children (its bw serve) with it."""
    child_pid = tmp_path / "child-pid"
    worker = write_script(
        tmp_path / "sleeping-with-child",
        SLEEPING_WITH_CHILD_WORKER.replace("CHILD_PID", str(child_pid)),
    )
    spawned = Spawned(monkeypatch)
    killed: list[tuple[int, int]] = []
    real_killpg = os.killpg

    def killpg(pgid: int, sig: int) -> None:
        killed.append((pgid, sig))
        real_killpg(pgid, sig)

    monkeypatch.setattr(os, "killpg", killpg)
    for env in make_env(tmp_path, worker_bin=worker, start_timeout=0.3):
        response = env.click(env.cookie(env.open()))
        assert response.status_code == 503
        wait_for(env.directory_gone, "the pending directory to go")
    child = int(child_pid.read_text())
    [proc] = spawned.procs
    assert killed == [(proc.pid, signal.SIGKILL)]
    assert proc.returncode == -signal.SIGKILL
    assert spawned.kills == []
    wait_for(lambda: process_gone(child), "the worker's child to go")


def test_the_kill_falls_back_to_the_pid_when_its_group_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sleeping = write_script(tmp_path / "sleeping", SLEEPING_WORKER)
    spawned = Spawned(monkeypatch)

    def killpg(pgid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", killpg)
    for env in make_env(tmp_path, worker_bin=sleeping, start_timeout=0.3):
        response = env.click(env.cookie(env.open()))
        assert response.status_code == 503
        wait_for(env.directory_gone, "the pending directory to go")
    [proc] = spawned.procs
    assert spawned.kills == [proc.pid]
    assert proc.returncode == -signal.SIGKILL


def test_every_request_gets_a_connection_of_its_own(tmp_path: Path):
    """The worker may close an idle connection just as the gateway sends on it, which was a 502."""
    keep_alive = write_script(tmp_path / "keep-alive", KEEP_ALIVE_WORKER)
    for env in make_env(tmp_path, worker_bin=keep_alive):
        token = env.started()
        statuses = [env.session(token).status_code for _ in range(3)]
        assert statuses == [200, 200, 200]
        assert env.seen() == [f"POST {LOGIN}"] + [f"GET {UNLOCK}"] * 3


def not_executable(tmp_path: Path) -> Path:
    worker = write_script(tmp_path / "not-executable", FAKE_WORKER)
    worker.chmod(0o644)
    return worker


@pytest.mark.parametrize(
    "worker_bin", [lambda tmp: tmp / "missing", not_executable], ids=["missing", "not_executable"]
)
def test_a_worker_that_cannot_start_leaves_no_pending_directory(tmp_path: Path, worker_bin):
    for env in make_env(tmp_path, worker_bin=worker_bin(tmp_path)):
        ## the third create proves the pending cap (2) is not reached
        for email in ("a@example.test", "b@example.test", "c@example.test"):
            response = env.click(env.cookie(env.open(email=email)))
            assert response.status_code == 503
            assert response.json() == {"detail": "The worker could not start."}
            assert not env.directory(email=email).exists()
        assert env.gateway.accounts.pending_count() == 0
        assert env.worker_pids() == []
        assert env.gateway.workers.count() == 0


def test_an_account_past_its_first_unlock_keeps_its_directory_when_its_worker_cannot_start(
    env: GatewayEnv,
):
    token = env.started()
    (env.directory() / PENDING_MARKER).unlink()
    [pid] = env.worker_pids()
    os.kill(pid, signal.SIGKILL)
    wait_for(lambda: env.worker_pids() == [], "the worker to exit")
    workers = env.gateway.workers
    workers._settings = dataclasses.replace(workers._settings, worker_bin=env.root / "missing")
    response = env.click(token, UNLOCK)
    assert response.status_code == 503
    assert env.directory().is_dir()
    assert workers.count() == 0


def test_a_symlinked_worker_log_refuses_the_worker_and_leaves_its_target_alone(tmp_path: Path):
    path = existing_account(tmp_path)
    target = tmp_path / "elsewhere.sqlite"
    target.write_bytes(b"another account's database")
    (path / WORKER_LOG).symlink_to(target)
    for env in make_env(tmp_path):
        refused_log(env, path)
    assert (path / WORKER_LOG).is_symlink()
    assert target.read_bytes() == b"another account's database"
    assert target.stat().st_uid == os.getuid()


def test_a_worker_uid_of_root_is_refused_before_its_log_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    path = existing_account(tmp_path)
    for env in make_env(tmp_path):
        workers = env.gateway.workers
        workers._settings = dataclasses.replace(workers._settings, switch_uid=True)
        monkeypatch.setattr(env.gateway.accounts, "uid_of", lambda path: 0)
        with caplog.at_level(logging.WARNING, logger="bwtidy"):
            response = env.click(env.cookie(env.open(create=False)), UNLOCK)
        assert response.status_code == 503
        assert response.json() == {"detail": "The worker's uid is not usable."}
        assert env.worker_pids() == []
        assert workers.count() == 0
        assert f"Refused to start a worker as uid 0 for {path}" in caplog.text
    assert not (path / WORKER_LOG).exists()


def refused_log(env: GatewayEnv, path: Path) -> None:
    response = env.click(env.cookie(env.open(create=False)), UNLOCK)
    assert response.status_code == 503
    assert response.json() == {"detail": f"The account's {WORKER_LOG} is not usable."}
    assert env.worker_pids() == []
    assert env.gateway.workers.count() == 0
    assert path.is_dir()


def test_a_fifo_at_worker_log_is_refused_at_once(tmp_path: Path):
    path = existing_account(tmp_path)
    os.mkfifo(path / WORKER_LOG)
    for env in make_env(tmp_path):
        started = time.monotonic()
        refused_log(env, path)
        assert time.monotonic() - started < 1.0
    assert stat.S_ISFIFO((path / WORKER_LOG).lstat().st_mode)


def test_a_directory_at_worker_log_is_refused_like_a_symlink(tmp_path: Path):
    path = existing_account(tmp_path)
    (path / WORKER_LOG).mkdir()
    for env in make_env(tmp_path):
        refused_log(env, path)
    assert (path / WORKER_LOG).is_dir()


def test_a_directory_planted_between_the_two_opens_of_worker_log_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = existing_account(tmp_path)
    log_path = path / WORKER_LOG
    assert not log_path.exists()
    real_open = os.open

    def planting_open(target: Any, *args: Any, **kwargs: Any) -> int:
        try:
            return real_open(target, *args, **kwargs)
        except FileNotFoundError:
            if Path(target) == log_path:
                log_path.mkdir()
            raise

    monkeypatch.setattr(workers_module.os, "open", planting_open)
    for env in make_env(tmp_path):
        refused_log(env, path)
    assert log_path.is_dir()


def test_a_missing_worker_log_is_made_with_mode_600_and_the_worker_starts(tmp_path: Path):
    path = existing_account(tmp_path)
    assert not (path / WORKER_LOG).exists()
    for env in make_env(tmp_path):
        response = env.click(env.cookie(env.open(create=False)), UNLOCK)
        assert response.status_code == 200, response.text
        assert len(env.worker_pids()) == 1
        info = (path / WORKER_LOG).lstat()
        assert stat.S_ISREG(info.st_mode)
        assert info.st_mode & 0o777 == 0o600
        wait_for(lambda: STARTED in (path / WORKER_LOG).read_text(), "the worker's first line")


def test_a_regular_worker_log_is_appended_to(tmp_path: Path):
    path = existing_account(tmp_path)
    (path / WORKER_LOG).write_text("an earlier worker's lines\n")
    for env in make_env(tmp_path):
        assert env.click(env.cookie(env.open(create=False)), UNLOCK).status_code == 200
        wait_for(lambda: STARTED in (path / WORKER_LOG).read_text(), "the worker's first line")
    assert (path / WORKER_LOG).read_text() == f"an earlier worker's lines\n{STARTED}\n"


@pytest.fixture
def capped(tmp_path: Path) -> Iterator[GatewayEnv]:
    """At most two workers, and room for more pending accounts than that."""
    yield from make_env(tmp_path, max_workers=2, max_pending=10)


def test_the_worker_cap_counts_running_and_starting_workers(capped: GatewayEnv):
    env = capped
    emails = ["a@example.test", "b@example.test", "c@example.test"]
    env.started(email=emails[0])
    env.started(email=emails[1])
    third = env.cookie(env.open(email=emails[2]))
    response = env.click(third)
    assert response.status_code == 429
    assert response.json()["detail"] == "Too many accounts are open at once. Try again later."
    ## checked before the directory is made, so a refused login leaves nothing behind
    assert not env.directory(email=emails[2]).exists()
    ## a slot frees when a worker exits, the account stays past its first unlock
    (env.directory(email=emails[0]) / PENDING_MARKER).unlink()
    [pid] = [w.pid for w in env.gateway.workers._workers.values() if w.key == account(emails[0])]
    os.kill(pid, signal.SIGKILL)
    wait_for(lambda: len(env.worker_pids()) == 1, "the worker to exit")
    assert env.click(third).status_code == 200
    ## an existing account's worker needs a slot too, for its unlock
    first = env.cookie(env.open(email=emails[0], create=False))
    response = env.click(first, UNLOCK)
    assert response.status_code == 429
    assert response.json()["detail"] == "Too many accounts are open at once. Try again later."
    assert env.directory(email=emails[0]).is_dir()


def test_concurrent_opens_cannot_pass_the_worker_cap_together(capped: GatewayEnv):
    env = capped
    emails = ["a@example.test", "b@example.test", "c@example.test"]

    async def open_all() -> list[Any]:
        opens = (env.gateway.open(account(email), True) for email in emails)
        return await asyncio.gather(*opens, return_exceptions=True)

    assert env.client.portal is not None
    results = env.client.portal.call(open_all)
    refused = [r for r in results if isinstance(r, LimitError)]
    assert len(refused) == 1
    assert len(env.worker_pids()) == 2
    assert sum(env.directory(email=email).exists() for email in emails) == 2


def test_docker_waits_longer_than_a_clean_stop_takes():
    ## docker kills every bw of the container at once after the grace period (docs/spec.md)
    assert STOP_TIMEOUT == 120.0
    needed = REQUEST_DRAIN + STOP_TIMEOUT + bwprocess.STOP_TIMEOUT
    root = Path(__file__).resolve().parents[4]
    for name in ("compose.yaml", "compose.dev.yaml"):
        graces = re.findall(r"^\s*stop_grace_period: (\d+)s$", (root / name).read_text(), re.M)
        assert graces == ["150"], name
        assert int(graces[0]) > needed
