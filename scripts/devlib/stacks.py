"""The dev stacks of a worktree: their numbers, env files, ports and compose projects.

Also how a script runs scripts/dev-vault on a stack, or a command on several stacks side by side,
and how many stacks a whole suite runs on.
"""

import os
import re
import socket
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from devlib.paths import DEV_VAULT, ROOT

## which of the worktree's stacks every command works on, 1 when unset
STACK_VAR = "BWTIDY_STACK"

## what one more stack of scripts/e2e or scripts/integration costs in RAM, with its browser:
## three e2e stacks with their Chromium use 3.9 to 5.2 GB more than none
STACK_MB = 1800
## RAM that stays available beside the stacks, for the desktop and the tests on the host
RESERVE_MB = 1536
## more stacks than this were not measured to be faster
MAX_STACKS = 3


def stack_number(environ: Mapping[str, str] = os.environ) -> int:
    value = environ.get(STACK_VAR, "").strip() or "1"
    if not value.isdigit() or int(value) < 1:
        raise SystemExit(f"dev-vault: {STACK_VAR} must be a stack number from 1 up, not {value!r}")
    return int(value)


def stack_suffix(stack: int) -> str:
    return "" if stack == 1 else f"-{stack}"


def env_file(root: Path, stack: int) -> Path:
    return root / f".dev-vault{stack_suffix(stack)}.env"


def tls_dir(root: Path, stack: int) -> Path:
    """The stack's own directory: its certificate, and the gateway's backend.log."""
    return root / f".dev-vault{stack_suffix(stack)}"


def project_name(root: Path, stack: int = 1) -> str:
    """bwtidy-<worktree dir name>, reduced to what compose accepts, -K for stack K from 2 up."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", root.name.lower()).strip("-_")
    return f"bwtidy-{slug or 'root'}{stack_suffix(stack)}"


@dataclass(frozen=True)
class Stack:
    """One dev stack of a worktree, with the names of its env file, directory and project."""

    number: int
    root: Path = ROOT

    @property
    def env_file(self) -> Path:
        return env_file(self.root, self.number)

    @property
    def dir(self) -> Path:
        return tls_dir(self.root, self.number)

    @property
    def cert(self) -> Path:
        return self.dir / "cert.pem"

    @property
    def key(self) -> Path:
        return self.dir / "key.pem"

    @property
    def project(self) -> str:
        return project_name(self.root, self.number)

    def env(self) -> dict[str, str]:
        """What the env file holds, the stack must have one."""
        return read_env(self.env_file)


def free_ports(count: int) -> list[int]:
    """Distinct ports that are free on 127.0.0.1 right now."""
    sockets = []
    try:
        ## all sockets stay open until every port is read, so the kernel never hands out one twice
        for _ in range(count):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", 0))
            sockets.append(s)
        return [s.getsockname()[1] for s in sockets]
    finally:
        for s in sockets:
            s.close()


def stacks_present(root: Path) -> list[int]:
    """The worktree's stacks that have an env file, so are up or were not taken down."""
    found = []
    for path in root.glob(".dev-vault*.env"):
        match = re.fullmatch(r"\.dev-vault(?:-(\d+))?\.env", path.name)
        if match and (match[1] is None or int(match[1]) >= 2):
            found.append(int(match[1] or 1))
    return sorted(found)


def write_env(
    path: Path, project: str, vw_image: str | None = None, tls: str = ".dev-vault"
) -> dict[str, str]:
    """Pick two free ports and write them with the project name to the env file.

    vw_image is the snapshot image Vaultwarden starts from, none means compose.dev.yaml's own.
    tls is the stack's directory relative to the worktree, compose.dev.yaml mounts its certificate.
    """
    vw_port, app_port = free_ports(2)
    env = {
        "BWTIDY_VW_PORT": str(vw_port),
        "BWTIDY_APP_PORT": str(app_port),
        "COMPOSE_PROJECT_NAME": project,
        "BWTIDY_TLS_DIR": tls,
    }
    if vw_image:
        env["BWTIDY_VW_IMAGE"] = vw_image
    path.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    return env


def read_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            env[key.strip()] = value.strip()
    return env


def stack_env(stack: int) -> dict[str, str]:
    """This process's environment with BWTIDY_STACK set to the stack."""
    return dict(os.environ, **{STACK_VAR: str(stack)})


def dev_vault_run(stack: int, *args: str, **kwargs) -> subprocess.CompletedProcess:
    """scripts/dev-vault ARGS on one stack of the worktree."""
    return subprocess.run([str(DEV_VAULT), *args], env=stack_env(stack), **kwargs)


def stack_up(stack: int | None = None) -> bool:
    """Whether the stack's app container runs, None for the stack of this process's environment."""
    ## fails when there is no env file or the app container is not running
    env = None if stack is None else stack_env(stack)
    result = subprocess.run([str(DEV_VAULT), "exec", "true"], capture_output=True, env=env)
    return result.returncode == 0


def app_port(stack: int, name: str, root: Path = ROOT) -> str:
    """The stack's BWTIDY_APP_PORT, SystemExit (from name) when its env file has none."""
    path = env_file(root, stack)
    port = read_env(path).get("BWTIDY_APP_PORT")
    if not port:
        raise SystemExit(f"{name}: no BWTIDY_APP_PORT in {path.name}")
    return port


def run_stacks(commands: list[list[str]], stacks: list[int]) -> None:
    """Run each command (scripts/dev-vault ARGS) in turn on every stack, the stacks side by side.

    Stack 1 goes first on its own when there is no usable snapshot, its setup saves the one the
    others start from. With several stacks every line of a stack's output starts with
    "stack K: ". Raises SystemExit naming the stacks that failed.
    """
    ## snapshot imports this module, so it is imported where it is needed
    from devlib import snapshot

    script = str(DEV_VAULT)
    if len(stacks) == 1:
        for command in commands:
            subprocess.run([script, *command], env=stack_env(stacks[0]), check=True)
        return
    lock = threading.Lock()
    failed = []

    def one(stack: int) -> None:
        for command in commands:
            process = subprocess.Popen(
                [script, *command],
                env=stack_env(stack),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert process.stdout is not None
            for line in process.stdout:
                with lock:
                    print(f"stack {stack}: {line}", end="", flush=True)
            if process.wait() != 0:
                failed.append(stack)
                return

    def side_by_side(group: list[int]) -> None:
        threads = [threading.Thread(target=one, args=(stack,)) for stack in group]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    usable = snapshot.snapshot_usable(snapshot.snapshot_image())
    first = [] if usable or 1 not in stacks else [1]
    if first:
        side_by_side(first)
    if not failed:
        side_by_side([stack for stack in stacks if stack not in first])
    if failed:
        names = ", ".join(str(stack) for stack in sorted(failed))
        steps = " then ".join(" ".join(command) for command in commands)
        raise SystemExit(f"dev-vault: {steps} failed on stack {names}")


def mem_available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        if key == "MemAvailable":
            return int(value.split()[0]) // 1024
    return 0


def default_stacks(available_mb: int | None = None, cores: int | None = None) -> int:
    """How many stacks a whole suite runs on: MAX_STACKS when the RAM and the cores allow it.

    Each stack costs STACK_MB and RESERVE_MB stays available. A stack waits on bw and
    Vaultwarden most of the time, about one core per stack is plenty.
    """
    available = mem_available_mb() if available_mb is None else available_mb
    if cores is None:
        cores = os.cpu_count() or 1
    fit = (available - RESERVE_MB) // STACK_MB
    return max(1, min(MAX_STACKS, fit, cores // 2))
