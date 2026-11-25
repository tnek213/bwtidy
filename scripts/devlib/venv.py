"""The backend venv, for a script that imports bwtidy: whether it runs in it, else run it there."""

import os
from importlib import metadata

from devlib.paths import ROOT

## set before a script starts itself again in the backend venv, so it never loops
REEXEC = "BWTIDY_BACKEND_VENV_REEXEC"


def in_backend_venv() -> bool:
    try:
        metadata.distribution("bwtidy")
    except metadata.PackageNotFoundError:
        return False
    return True


def run_in_backend_venv(name: str, script: str, args: list[str]) -> None:
    """Return in the backend venv, else start the script again in it through `uv run`.

    SystemExit (from name) when the script was started again already and bwtidy is still missing.
    """
    if in_backend_venv():
        return
    if os.environ.get(REEXEC):
        raise SystemExit(f"{name}: bwtidy is not installed in the backend venv")
    os.environ[REEXEC] = "1"
    backend = str(ROOT / "backend")
    os.execvp(
        "uv", ["uv", "run", "--locked", "--quiet", "--project", backend, "python", script, *args]
    )
