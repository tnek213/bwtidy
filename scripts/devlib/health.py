"""/api/health of a running bwtidy: whether it answers, what it answers and what it should."""

import json
import subprocess
import time
import urllib.request
from pathlib import Path

from devlib import gateway, versions

## the first start installs the backend venv inside the container, which takes a while
START_TIMEOUT = 300


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def health_text(commit: str) -> str:
    """What /api/health answers for this checkout built with BWTIDY_COMMIT=commit."""
    return f"{versions.semver(versions.pyproject_version())} ({commit})"


def health_version(url: str) -> str:
    """The version /api/health answers."""
    with urllib.request.urlopen(f"{url}/api/health", timeout=5) as response:
        return str(json.load(response)["version"])


def wait_healthy(
    stack: int,
    url: str,
    backend: subprocess.Popen,
    name: str,
    log: Path | None = None,
    label: str | None = None,
) -> None:
    """Wait until the backend answers, else stop it and raise SystemExit (from name).

    That is when it exits first, a given log is then named in the message, or takes too long.
    label starts the message of a backend that does not stop, name's "the backend" by default.
    """
    deadline = time.monotonic() + START_TIMEOUT
    while not healthy(url):
        if backend.poll() is not None:
            see = f", see {log}" if log else ""
            failure = f"{name}: the backend exited before it answered{see}"
        elif time.monotonic() > deadline:
            failure = f"{name}: the backend did not answer on {url} in {START_TIMEOUT} s"
        else:
            time.sleep(1)
            continue
        ## returns at once for a backend that exited
        gateway.stop_backend(stack, backend, label or f"{name}: the backend")
        raise SystemExit(failure)
