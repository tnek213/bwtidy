"""git of a checkout: a command, the commit a build shows and the check of a clean checkout."""

import subprocess
from pathlib import Path

from devlib.paths import ROOT


def git(*args: str, repo: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    """git ARGS in repo, its output captured as text."""
    return subprocess.run(["git", *args], cwd=repo, check=check, capture_output=True, text=True)


def output(*args: str, name: str, repo: Path = ROOT) -> str:
    """What git ARGS prints, SystemExit (from name) with git's error when it fails."""
    result = git(*args, repo=repo, check=False)
    if result.returncode != 0:
        raise SystemExit(f"{name}: git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.rstrip()


def commit(name: str, repo: Path = ROOT) -> str:
    """Committer date as YYMMDD, a space and the short hash, plus -dirty when uncommitted.

    name is the script's, a git that fails exits naming it.
    """
    version = output("log", "-1", "--format=%cd %h", "--date=format:%y%m%d", name=name, repo=repo)
    dirty = output("status", "--porcelain", name=name, repo=repo)
    return f"{version}-dirty" if dirty else version


def check_checkout(
    name: str, repo: Path = ROOT, branch: str | None = None, list_changes: bool = False
) -> None:
    """Refuse (SystemExit from name) unless the checkout is clean, and on branch when one is given.

    list_changes names each uncommitted file in the message, on a line of its own.
    """
    if branch is not None:
        current = output("branch", "--show-current", repo=repo, name=name)
        if current != branch:
            shown = current or "a detached HEAD"
            raise SystemExit(f"{name}: this checkout is on {shown}, switch to {branch} first")
    changes = output("status", "--porcelain", repo=repo, name=name).splitlines()
    if changes:
        message = f"{name}: this checkout has uncommitted changes, commit or stash them first"
        if list_changes:
            message += ":\n" + "\n".join(f"  {line}" for line in changes)
        raise SystemExit(message)
