"""The demo stack of a worktree (compose.demo.yaml): its project, images, port and commands.

A worktree has one demo stack, with a compose project and two images of its own (the normal
image the demo is built on, and the demo image) and a public bridge, so the stacks of several
worktrees never share a container, a volume, an image or a bridge.
"""

import hashlib
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

from devlib import git, stacks
from devlib.paths import ROOT

COMPOSE_FILE = "compose.demo.yaml"
DOCKERFILE = "demo/Dockerfile"
## the container port of the edge that compose.demo.yaml publishes
EDGE = ("edge", "80")
## what compose.demo.yaml reads from the environment and the stack sets itself
OWN = (
    "BWTIDY_IMAGE",
    "DEMO_IMAGE",
    "DEMO_BIND",
    "DEMO_PORT",
    "DEMO_BUILD_CONTEXT",
    "DEMO_PUBLIC_BRIDGE",
)


def slug(root: Path) -> str:
    """The worktree's directory name, reduced to what compose and an image name accept."""
    return re.sub(r"[^a-z0-9_-]+", "-", root.name.lower()).strip("-_") or "root"


def project_name(root: Path = ROOT) -> str:
    return f"bwtidy-demo-{slug(root)}"


def base_image(root: Path = ROOT) -> str:
    """The normal image built from this worktree, which the demo image starts FROM."""
    return f"{project_name(root)}-base:local"


def demo_image(root: Path = ROOT) -> str:
    return f"{project_name(root)}:local"


def public_bridge(root: Path = ROOT) -> str:
    """The bridge of the stack's network public: a worktree's own, at most 15 characters.

    Docker refuses a second network with a bridge name that one already has, so the stacks of
    two worktrees with the default name could not run at once.
    """
    return "bwd-" + hashlib.sha256(project_name(root).encode()).hexdigest()[:10]


def compose(root: Path, *args: str) -> list[str]:
    """docker compose ARGS of the worktree's demo stack, as a command.

    The empty env file keeps the worktree's .env (bwtidy's own, of compose.yaml) out of it.
    """
    path = root / COMPOSE_FILE
    project = project_name(root)
    return ["docker", "compose", "-p", project, "-f", str(path), "--env-file", os.devnull, *args]


def environment(root: Path, port: int, environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    """The environment of every compose command of the stack.

    The caller's DEMO_ settings (DEMO_MAX, DEMO_POOL_READY, the rates) pass through. The
    images, the context, the address and the public bridge are the stack's own: built here, on
    loopback, at port.
    """
    env = {name: value for name, value in environ.items() if name not in OWN}
    env["BWTIDY_IMAGE"] = base_image(root)
    env["DEMO_IMAGE"] = demo_image(root)
    env["DEMO_BIND"] = "127.0.0.1"
    env["DEMO_PORT"] = str(port)
    env["DEMO_PUBLIC_BRIDGE"] = public_bridge(root)
    return env


def published_port(output: str) -> int | None:
    """The host port in what `docker compose port` prints (127.0.0.1:8770), None for none."""
    match = re.fullmatch(r"\S*:(\d+)", output.strip())
    return int(match[1]) if match else None


def running_port(root: Path = ROOT) -> int | None:
    """The port the stack's edge is published on, None when it does not run."""
    command = compose(root, "port", *EDGE)
    env = environment(root, 0)
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, check=False)
    return published_port(result.stdout) if result.returncode == 0 else None


def pick_port(root: Path = ROOT) -> int:
    """The running stack's port, so a second up changes its settings in place, else a free one."""
    return running_port(root) or stacks.free_ports(1)[0]


def url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def build_commands(root: Path = ROOT, commit: str = "") -> list[list[str]]:
    """The two builds, in order: the normal image from this worktree, then the demo image on it."""
    base = [
        "docker",
        "build",
        "--target",
        "app",
        "--build-arg",
        f"BWTIDY_COMMIT={commit}",
        "--tag",
        base_image(root),
        str(root),
    ]
    ## plain docker build: compose build needs buildx 0.17 or later
    on_top = [
        "docker",
        "build",
        "--file",
        str(root / DOCKERFILE),
        "--build-arg",
        f"BWTIDY_IMAGE={base_image(root)}",
        "--tag",
        demo_image(root),
        str(root),
    ]
    return [base, on_top]


def up(root: Path = ROOT, build: bool = True) -> int:
    """Build (unless told not to) and start the stack, wait until it is healthy: its port."""
    port = pick_port(root)
    env = environment(root, port)
    if build:
        for command in build_commands(root, git.commit("demo-stack", root)):
            subprocess.run(command, cwd=root, env=env, check=True)
    ## the images are built above, or were by an earlier up
    start = compose(root, "up", "--detach", "--wait", "--no-build")
    subprocess.run(start, cwd=root, env=env, check=True)
    return port


def down(root: Path = ROOT) -> None:
    """Stop the stack, remove its volumes (the ledger too) and its two images."""
    env = environment(root, 0)
    command = compose(root, "down", "--volumes", "--remove-orphans")
    subprocess.run(command, cwd=root, env=env, check=True)
    images = [demo_image(root), base_image(root)]
    subprocess.run(["docker", "image", "rm", "--force", *images], capture_output=True, check=False)
