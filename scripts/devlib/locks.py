"""The npm lock files: each lists what its package.json asks for, else npm ci refuses it.

scripts/ci runs npm ci only in a fresh worktree, so this check finds a package.json changed
without npm install before a build or the CI workflow does. The backend needs no such check:
every uv call of the scripts runs with --locked, which fails on a stale backend/uv.lock.
"""

import json
from pathlib import Path

from devlib.paths import ROOT

## the npm projects of the repo: the frontend and the image's Bitwarden CLI
NPM_PROJECTS = (Path("frontend"), Path("docker") / "bitwarden-cli")
FIELDS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")


def npm_lock_mismatch(project: Path, root: Path = ROOT) -> str | None:
    """Why PROJECT/package-lock.json does not match PROJECT/package.json, else None."""
    package = json.loads((root / project / "package.json").read_text())
    lock_path = root / project / "package-lock.json"
    if not lock_path.exists():
        return f"{project.as_posix()}/package-lock.json is missing, run npm install in {project}"
    locked = json.loads(lock_path.read_text()).get("packages", {}).get("", {})
    stale = [field for field in FIELDS if package.get(field, {}) != locked.get(field, {})]
    if not stale:
        return None
    return (
        f"{project.as_posix()}/package-lock.json is out of date: its {', '.join(stale)} differ "
        f"from package.json, run npm install in {project.as_posix()}"
    )


def lock_mismatch(root: Path = ROOT) -> str | None:
    """Every npm lock file that does not match its package.json, one line each, else None."""
    problems = [npm_lock_mismatch(project, root) for project in NPM_PROJECTS]
    return "\n".join(p for p in problems if p) or None
