"""A repo of its own with the files that carry the version and the release notes, for the tests of
scripts/devlib/versions.py and scripts/devlib/release.py."""

import json
import os
import subprocess
from pathlib import Path

from tests.support.git_repo import git

PYTHON = "0.1.0a1"
SEMVER = "0.1.0-alpha.1"
DRIFTED = "0.1.0-alpha.2"
REPO = "https://github.com/tnek213/bwtidy"
RULES = "(dev/release.md, The release notes)"
INTRO = (
    "# Release notes\n\n"
    "Versions follow Semantic Versioning with -alpha.N, -beta.N and -rc.N pre-releases.\n"
    "[Road to 1.0](explanation/road-to-1.0.md) says what comes before 1.0.\n\n"
)
UNRELEASED = (
    "## Unreleased\n\nChanges on main that are not yet in a versioned Docker image.\n\n"
    "Nothing yet.\n\n"
)


def pull(*numbers: int) -> str:
    """The pull request links that end a bullet."""
    return "(" + ", ".join(f"[#{n}]({REPO}/pull/{n})" for n in numbers) + ")"


def metadata(version: str, date: str, previous: str | None) -> str:
    line = f"{date} · [GitHub release]({REPO}/releases/tag/v{version})"
    if previous is not None:
        line += f" · [All changes]({REPO}/compare/v{previous}...v{version})"
    return line


def withdrawn(date: str, gone: str, by: str) -> str:
    """The metadata line of a version withdrawn on gone in favor of the version by."""
    slug = by.replace(".", "")
    return f"{date} · Withdrawn on {gone}, see [{by}](#{slug})"


## the newest is tagged on 2026-03-04 at 23:30 in New York, which is 2026-03-05 in Sweden
TAGS = {SEMVER: "2026-03-04T23:30:00-05:00", "0.0.2": "2026-02-03T12:00:00+00:00"}
TAGGED = (
    INTRO
    + UNRELEASED
    + f"## {SEMVER}\n\n{metadata(SEMVER, '2026-03-05', '0.0.2')}\n\n"
    + "The first alpha brings a lot.\n\n"
    + "### Breaking changes\n\n"
    + f"- Run the migration before you start this version {pull(7)}\n\n"
    + "### Security\n\n"
    + f"- Update, as a backup could reach the log {pull(8)}\n\n"
    + "### Deprecated\n\n"
    + f"- Stop using the old setting, it goes away in 1.0 {pull(9)}\n\n"
    + "### Other changes\n\n"
    + f"- Use the web UI with a keyboard {pull(3, 4)}\n"
    + "- Find archived items and their attachments in every backup again, a long line\n"
    + f"  that wraps {pull(5)}\n\n"
    + f"## 0.0.2\n\n{metadata('0.0.2', '2026-02-03', None)}\n\n"
    + f"- Start bwtidy {pull(1)}\n"
)


class FakeRepo:
    """The four files that carry the version and the release notes, under a root of their
    own, all in agreement. The root is a git repo of its own without tags, the notes have
    one untagged version."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "backend").mkdir()
        (root / "frontend").mkdir()
        (root / "docs").mkdir()
        git(root, "init", "--quiet", "-b", "main")
        for key, value in (
            ("user.name", "t"),
            ("user.email", "t@t.test"),
            ("commit.gpgsign", "false"),
            ("tag.gpgsign", "false"),
        ):
            git(root, "config", key, value)
        self.release_notes(INTRO + UNRELEASED + f"## {SEMVER}\n\n- Start bwtidy {pull(1)}\n")
        (root / "backend" / "pyproject.toml").write_text(
            f'[project]\nname = "bwtidy"\nversion = "{PYTHON}"\n'
        )
        self.uv_lock(PYTHON)
        self.package_json(SEMVER)
        self.package_lock(SEMVER, SEMVER)

    def uv_lock(self, version: str) -> None:
        (self.root / "backend" / "uv.lock").write_text(
            "version = 1\n\n"
            '[[package]]\nname = "anyio"\nversion = "4.11.0"\n\n'
            f'[[package]]\nname = "bwtidy"\nversion = "{version}"\n'
        )

    def release_notes(self, text: str) -> None:
        (self.root / "docs" / "releases.md").write_text(text)

    def tag(self, version: str, committed: str) -> None:
        """Tag vVERSION (annotated, as a release is) on a new commit made at committed."""
        env = {**os.environ, "GIT_COMMITTER_DATE": committed, "GIT_AUTHOR_DATE": committed}
        subprocess.run(
            ["git", "commit", "--quiet", "--allow-empty", "-m", f"Release {version}"],
            cwd=self.root,
            env=env,
            check=True,
        )
        git(self.root, "tag", "-a", "-m", f"bwtidy {version}", f"v{version}")

    def commit(self, committed: str, message: str = "Change the files") -> str:
        """Commit every file as it is now, at committed, and return the commit's sha."""
        env = {**os.environ, "GIT_COMMITTER_DATE": committed, "GIT_AUTHOR_DATE": committed}
        git(self.root, "add", "--all")
        subprocess.run(
            ["git", "commit", "--quiet", "--allow-empty", "-m", message],
            cwd=self.root,
            env=env,
            check=True,
        )
        return git(self.root, "rev-parse", "HEAD")

    def tagged(self, text: str = TAGGED) -> None:
        """The notes in text with the tags of TAGS."""
        for version, committed in TAGS.items():
            self.tag(version, committed)
        self.release_notes(text)

    def package_json(self, version: str) -> None:
        (self.root / "frontend" / "package.json").write_text(
            json.dumps({"name": "bwtidy-frontend", "version": version})
        )

    def package_lock(self, top: str, root_package: str) -> None:
        lock = {
            "name": "bwtidy-frontend",
            "version": top,
            "lockfileVersion": 3,
            "packages": {"": {"name": "bwtidy-frontend", "version": root_package}},
        }
        (self.root / "frontend" / "package-lock.json").write_text(json.dumps(lock))
