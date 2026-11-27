"""The npm lock files: each lists what its package.json asks for, else npm ci refuses it.

scripts/ci runs npm ci in a fresh worktree and whenever the lock file of the frontend or of
tools/markdownlint differs from the one node_modules was installed from (a stamp file, see
npm_install_needed), so this check finds a package.json changed without npm install before a
build or the CI workflow does.
The backend and the demo need no such check: every uv call of the scripts runs with --locked,
which fails on a stale backend/uv.lock or demo/uv.lock.
"""

import hashlib
import json
from pathlib import Path

from devlib.paths import ROOT

## the npm projects of the repo: the frontend, the image's Bitwarden CLI and markdownlint-cli2
NPM_PROJECTS = (Path("frontend"), Path("docker") / "bitwarden-cli", Path("tools") / "markdownlint")
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


## npm's hidden lockfile omits packages skipped for the platform, so it cannot be compared
STAMP = Path("node_modules") / ".bwtidy-lock-sha256"


def _lock_sha256(project: Path, root: Path) -> str:
    return hashlib.sha256((root / project / "package-lock.json").read_bytes()).hexdigest()


def npm_install_needed(project: Path, root: Path = ROOT) -> bool:
    """Whether npm ci must run: no node_modules, or no stamp of the current lock file."""
    stamp = root / project / STAMP
    if not stamp.is_file():
        return True
    return stamp.read_text().strip() != _lock_sha256(project, root)


def write_npm_stamp(project: Path, root: Path = ROOT) -> None:
    """Record the lock file PROJECT/node_modules was just installed from."""
    (root / project / STAMP).write_text(_lock_sha256(project, root) + "\n")
