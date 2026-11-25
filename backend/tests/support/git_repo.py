"""A git clone with a bare origin, for the tests of the scripts that read git."""

import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    """git ARGS in repo, its output without the trailing newline."""
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def commit(repo: Path, path: str, text: str | None = None) -> str:
    """Commit PATH with TEXT, else with a line added, and return the new commit's sha."""
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        text = f"{file.read_text() if file.exists() else ''}{path}\n"
    file.write_text(text)
    git(repo, "add", path)
    git(repo, "commit", "--quiet", "-m", f"Change {path}")
    return git(repo, "rev-parse", "HEAD")


def cloned(place: Path) -> Path:
    """A clone in PLACE/clone on main with one commit pushed to its bare origin PLACE/origin.git.

    Its own config names a committer and signs nothing, whatever the user's global config says.
    """
    origin = place / "origin.git"
    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(origin)], check=True)
    clone = place / "clone"
    subprocess.run(
        ["git", "clone", "--quiet", str(origin), str(clone)], check=True, stderr=subprocess.DEVNULL
    )
    for key, value in (
        ("user.name", "t"),
        ("user.email", "t@t.test"),
        ("commit.gpgsign", "false"),
        ("tag.gpgsign", "false"),
    ):
        git(clone, "config", key, value)
    git(clone, "checkout", "--quiet", "-B", "main")
    commit(clone, "README.md")
    git(clone, "push", "--quiet", "-u", "origin", "main")
    return clone
