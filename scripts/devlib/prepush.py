"""What the pre-push hook checks: the refs git pushes, their new commits and the UI they touch.

git runs the hook with the remote's name and URL as arguments and one line per ref on stdin,
`LOCAL_REF LOCAL_SHA REMOTE_REF REMOTE_SHA`. A delete has a local sha of zeros and checks
nothing, a new ref a remote sha of zeros. A ref whose commit the remote already has (a tag on a
pushed commit, a branch rewound) brings no new commit and checks nothing either.

A ref with new commits gets scripts/ci on its commit, in full for a push to main or a tag
(full_ci), else scripts/ci --quick, the CI workflow running the rest on the pull request. It
touches the UI when a file under UI_PATHS differs between its commit and the remote's (for a new
ref: the merge base with the remote's main), and then the docs screenshots are taken again.
passed and record keep the trees a step passed on, in the git directory that the worktrees share.
"""

from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

from devlib.git import git
from devlib.paths import ROOT

## the files that change what a docs screenshot shows
UI_PATHS = ("frontend/src/", "backend/tests/e2e/docs/")
## a new branch is compared with this branch of the remote it goes to, else with origin's
MAIN = "main"
FALLBACK_REMOTE = "origin"
## in the git directory: one line `STEP TREE` per step that passed, so a second push of the same
## files (the one after the hook committed the screenshots) does not run it again
RECORD = "bwtidy-pre-push"
RECORD_LINES = 200


def is_zero(sha: str) -> bool:
    """A sha of zeros: git's name for no commit, 40 digits (SHA-1) or 64 (SHA-256)."""
    return set(sha) == {"0"}


class RefUpdate(NamedTuple):
    """One line of the hook's stdin."""

    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str

    @property
    def deleted(self) -> bool:
        return is_zero(self.local_sha)

    @property
    def created(self) -> bool:
        return is_zero(self.remote_sha)

    @property
    def name(self) -> str:
        """The remote ref without refs/heads/ or refs/tags/, as a person names it."""
        return self.remote_ref.removeprefix("refs/heads/").removeprefix("refs/tags/")


def full_ci(update: RefUpdate) -> bool:
    """Whether the update needs the full scripts/ci: a push to main or a tag, not other branches."""
    return update.remote_ref == f"refs/heads/{MAIN}" or update.remote_ref.startswith("refs/tags/")


def parse_updates(text: str) -> list[RefUpdate]:
    """The refs of the hook's stdin, ValueError naming a line that is not four fields."""
    updates = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise ValueError(f"not a pre-push line: {line}")
        updates.append(RefUpdate(*fields))
    return updates


def touches_ui(paths: Iterable[str]) -> bool:
    return any(path.startswith(UI_PATHS) for path in paths)


def _commit(rev: str, repo: Path) -> str | None:
    """The commit rev names (a tag peeled), None when it is no commit this repo has."""
    result = git("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", repo=repo, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def tip(update: RefUpdate, repo: Path = ROOT) -> str | None:
    """The commit the update pushes, None for a delete or a tag on something else than a commit."""
    return None if update.deleted else _commit(update.local_sha, repo)


def new_commits(update: RefUpdate, remote: str, repo: Path = ROOT) -> list[str]:
    """The commits the push brings that the remote does not have yet, as far as this repo knows.

    An update of a ref the remote has counts the commits after its remote sha, a new ref those
    that are on no remote-tracking branch of the remote.
    """
    commit = tip(update, repo)
    if commit is None:
        return []
    known = None if update.created else _commit(update.remote_sha, repo)
    exclude = [known] if known else [f"--remotes={remote}"]
    return git("rev-list", commit, "--not", *exclude, repo=repo).stdout.split()


def main_ref(remote: str, repo: Path = ROOT) -> str | None:
    """The remote's main as this repo last fetched it, else origin's, else None."""
    for name in (remote, FALLBACK_REMOTE):
        ref = f"refs/remotes/{name}/{MAIN}"
        if _commit(ref, repo):
            return ref
    return None


def base(update: RefUpdate, remote: str, repo: Path = ROOT) -> str | None:
    """What the update's commit is compared with for its changed files.

    The remote's commit when this repo has it, else the merge base with the remote's main, None
    when there is neither.
    """
    if not update.created:
        known = _commit(update.remote_sha, repo)
        if known:
            return known
    main = main_ref(remote, repo)
    commit = tip(update, repo)
    if main is None or commit is None:
        return None
    result = git("merge-base", main, commit, repo=repo, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def changed_files(update: RefUpdate, remote: str, repo: Path = ROOT) -> list[str] | None:
    """The files the update changes on the remote, None when there is nothing to compare with."""
    commit = tip(update, repo)
    against = base(update, remote, repo)
    if commit is None or against is None:
        return None
    return git("diff", "--name-only", against, commit, repo=repo).stdout.splitlines()


class Push(NamedTuple):
    """What the hook checks: the updates with new commits, and those of them that touch the UI."""

    checked: list[RefUpdate]
    ui: list[RefUpdate]


def plan(updates: Iterable[RefUpdate], remote: str, repo: Path = ROOT) -> Push:
    """The updates with new commits, and those that touch the UI.

    An update with nothing to compare with (no remote commit, no main to start from) counts as
    touching the UI, so the screenshots are taken rather than missed.
    """
    checked = [u for u in updates if new_commits(u, remote, repo)]
    ui = []
    for update in checked:
        files = changed_files(update, remote, repo)
        if files is None or touches_ui(files):
            ui.append(update)
    return Push(checked, ui)


def record_path(repo: Path = ROOT) -> Path:
    """The file of the trees that passed a step, shared by the worktrees of the repo."""
    common = git("rev-parse", "--git-common-dir", repo=repo).stdout.strip()
    return (repo / common).resolve() / RECORD


def passed(step: str, tree: str, repo: Path = ROOT) -> bool:
    """Whether STEP passed on TREE before, a tree being the same files whatever the commit."""
    path = record_path(repo)
    return path.exists() and f"{step} {tree}" in path.read_text().splitlines()


def record(step: str, tree: str, repo: Path = ROOT) -> None:
    """Note that STEP passed on TREE, keeping the last RECORD_LINES."""
    path = record_path(repo)
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [*lines, f"{step} {tree}"][-RECORD_LINES:]
    path.write_text("\n".join(lines) + "\n")
