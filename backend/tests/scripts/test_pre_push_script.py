"""scripts/pre-push in a clone whose scripts/ci and scripts/docs-shots are stand-ins.

The stand-ins note where they ran in a log outside the clone. scripts/ci exits with $CI_EXIT,
scripts/docs-shots writes $SHOT into docs/images/shot.png when it is set.
"""

import subprocess
from pathlib import Path

import pytest

from tests.support.git_repo import cloned, commit, git

ZERO = "0" * 40
CI = '#!/bin/sh\necho "ci $* $PWD" >> "$PRE_PUSH_LOG"\nexit "${CI_EXIT:-0}"\n'
SHOTS = (
    '#!/bin/sh\necho "docs-shots $PWD" >> "$PRE_PUSH_LOG"\n'
    'if [ -n "$SHOT" ]; then printf %s "$SHOT" > docs/images/shot.png; fi\n'
)


@pytest.fixture
def clone(tmp_path, monkeypatch, load_script):
    clone = cloned(tmp_path)
    for name, text in (("ci", CI), ("docs-shots", SHOTS)):
        (clone / "scripts").mkdir(exist_ok=True)
        (clone / "scripts" / name).write_text(text)
        (clone / "scripts" / name).chmod(0o755)
    commit(clone, "docs/images/shot.png", "old")
    git(clone, "add", "scripts")
    git(clone, "commit", "--quiet", "-m", "Add the stand-ins")
    git(clone, "push", "--quiet", "origin", "main")
    monkeypatch.setenv("PRE_PUSH_LOG", str(tmp_path / "log"))
    monkeypatch.delenv("SHOT", raising=False)
    monkeypatch.delenv("CI_EXIT", raising=False)
    monkeypatch.chdir(clone)
    return clone


@pytest.fixture
def pre_push(clone, load_script, monkeypatch):
    script = load_script("pre-push")
    monkeypatch.setattr(script, "ROOT", clone)
    monkeypatch.setattr(script, "SCRIPTS", clone / "scripts")
    return script


def ci(where: Path, *args: str) -> str:
    """The stand-in's log line for a run of scripts/ci with ARGS in WHERE."""
    return f"ci {' '.join(args)} {where}"


def log(clone: Path) -> list[str]:
    path = clone.parent / "log"
    return path.read_text().splitlines() if path.exists() else []


def line(sha: str, remote_sha: str, branch: str = "main") -> str:
    return f"refs/heads/{branch} {sha} refs/heads/{branch} {remote_sha}\n"


def push(pre_push, clone: Path, stdin: str) -> int:
    return pre_push.main(["origin", str(clone.parent / "origin.git")], stdin)


def remote(clone: Path) -> str:
    return git(clone, "rev-parse", "origin/main")


def test_a_push_without_new_commits_runs_nothing(pre_push, clone, capsys):
    tag = f"refs/tags/v1 {remote(clone)} refs/tags/v1 {ZERO}\n"
    delete = f"(delete) {ZERO} refs/heads/old {remote(clone)}\n"
    assert push(pre_push, clone, tag + delete + line(remote(clone), ZERO, "feat/x")) == 0
    assert log(clone) == []
    assert "nothing to check" in capsys.readouterr().out


def test_a_malformed_line_stops_the_push(pre_push, clone):
    assert push(pre_push, clone, "refs/heads/main abc\n") == 1


def test_the_clean_head_is_tested_here_and_no_ui_change_takes_no_shot(pre_push, clone, capsys):
    head = commit(clone, "backend/app.py")
    assert push(pre_push, clone, line(head, remote(clone))) == 0
    assert log(clone) == [ci(clone)]
    out = capsys.readouterr().out
    assert "step 1 of 2" in out
    assert "no UI change" in out


def test_a_failing_ci_stops_the_push(pre_push, clone, monkeypatch, capsys):
    monkeypatch.setenv("CI_EXIT", "1")
    head = commit(clone, "frontend/src/App.tsx")
    assert push(pre_push, clone, line(head, remote(clone))) == 1
    assert log(clone) == [ci(clone)]
    assert "push stopped" in capsys.readouterr().out


def test_a_commit_that_is_not_the_clean_head_is_tested_in_its_own_worktree(pre_push, clone):
    pushed = commit(clone, "backend/app.py")
    (clone / "scratch.txt").write_text("not committed")
    assert push(pre_push, clone, line(pushed, remote(clone))) == 0
    [entry] = log(clone)
    assert entry.startswith("ci ")
    assert entry != ci(clone)
    assert not Path(entry.split()[-1]).exists()
    assert git(clone, "worktree", "list").count("\n") == 0


def test_a_tree_that_passed_ci_before_is_not_tested_again(pre_push, clone, capsys):
    head = commit(clone, "backend/app.py")
    assert push(pre_push, clone, line(head, remote(clone))) == 0
    assert push(pre_push, clone, line(head, remote(clone), "feat/x")) == 0
    assert log(clone) == [ci(clone)]
    assert "skipped" in capsys.readouterr().out


def test_a_ui_change_with_current_shots_pushes(pre_push, clone, monkeypatch):
    monkeypatch.setenv("SHOT", "old")
    head = commit(clone, "frontend/src/App.tsx")
    assert push(pre_push, clone, line(head, remote(clone))) == 0
    assert log(clone) == [ci(clone), f"docs-shots {clone}"]
    assert git(clone, "rev-parse", "HEAD") == head


def test_changed_shots_are_committed_and_the_next_push_finds_them_current(
    pre_push, clone, monkeypatch, capsys
):
    monkeypatch.setenv("SHOT", "new")
    head = commit(clone, "frontend/src/App.tsx")
    assert push(pre_push, clone, line(head, remote(clone))) == 1
    assert git(clone, "log", "-1", "--format=%s") == "Take the docs screenshots again"
    assert git(clone, "show", "--name-only", "--format=", "HEAD") == "docs/images/shot.png"
    assert git(clone, "rev-parse", "HEAD~1") == head
    assert git(clone, "status", "--porcelain") == ""
    assert "push again" in capsys.readouterr().out

    again = git(clone, "rev-parse", "HEAD")
    assert push(pre_push, clone, line(again, remote(clone))) == 0
    assert log(clone) == [ci(clone), f"docs-shots {clone}", ci(clone)]


def test_a_new_branch_with_a_ui_change_takes_the_shots(pre_push, clone, monkeypatch):
    monkeypatch.setenv("SHOT", "old")
    git(clone, "checkout", "--quiet", "-b", "feat/x")
    head = commit(clone, "backend/tests/e2e/docs/test_tutorial.py")
    assert push(pre_push, clone, line(head, ZERO, "feat/x")) == 0
    assert log(clone) == [ci(clone, "--quick"), f"docs-shots {clone}"]


def test_a_ui_change_that_is_not_checked_out_stops_the_push(pre_push, clone, capsys):
    git(clone, "checkout", "--quiet", "-b", "feat/x")
    other = commit(clone, "frontend/src/App.tsx")
    git(clone, "checkout", "--quiet", "main")
    assert push(pre_push, clone, line(other, ZERO, "feat/x")) == 1
    assert "check it out" in capsys.readouterr().out
    assert [entry.split()[0] for entry in log(clone)] == ["ci"]


def test_a_ui_change_elsewhere_whose_shots_were_taken_pushes(pre_push, clone, monkeypatch):
    monkeypatch.setenv("SHOT", "old")
    head = commit(clone, "frontend/src/App.tsx")
    assert push(pre_push, clone, line(head, remote(clone))) == 0
    git(clone, "checkout", "--quiet", "-b", "feat/y", "origin/main")
    assert push(pre_push, clone, line(head, ZERO, "feat/x")) == 0
    assert log(clone) == [ci(clone), f"docs-shots {clone}"]


def test_a_ui_change_in_a_checkout_with_changes_stops_the_push(pre_push, clone, capsys):
    head = commit(clone, "frontend/src/App.tsx")
    (clone / "scratch.txt").write_text("not committed")
    assert push(pre_push, clone, line(head, remote(clone))) == 1
    assert "commit or stash" in capsys.readouterr().out
    assert [entry.split()[0] for entry in log(clone)] == ["ci"]


def test_a_push_to_main_runs_the_full_ci(pre_push, clone, capsys):
    head = commit(clone, "backend/app.py")
    assert push(pre_push, clone, line(head, remote(clone))) == 0
    assert log(clone) == [ci(clone)]
    assert "--quick" not in capsys.readouterr().out


def test_a_push_of_a_new_feature_branch_runs_the_quick_ci(pre_push, clone, capsys):
    git(clone, "checkout", "--quiet", "-b", "feat/x")
    head = commit(clone, "backend/app.py")
    assert push(pre_push, clone, line(head, ZERO, "feat/x")) == 0
    assert log(clone) == [ci(clone, "--quick")]
    assert "scripts/ci --quick on" in capsys.readouterr().out


def test_main_and_a_feature_branch_on_the_same_commit_run_the_full_ci_once(pre_push, clone):
    head = commit(clone, "backend/app.py")
    stdin = line(head, remote(clone)) + line(head, ZERO, "feat/x")
    assert push(pre_push, clone, stdin) == 0
    assert log(clone) == [ci(clone)]


def test_a_tree_that_passed_the_full_ci_is_not_tested_again_on_a_feature_branch(pre_push, clone):
    head = commit(clone, "backend/app.py")
    assert push(pre_push, clone, line(head, remote(clone))) == 0
    assert push(pre_push, clone, line(head, ZERO, "feat/x")) == 0
    assert log(clone) == [ci(clone)]


def test_a_tree_that_passed_only_the_quick_ci_is_tested_in_full_on_main(pre_push, clone):
    head = commit(clone, "backend/app.py")
    assert push(pre_push, clone, line(head, ZERO, "feat/x")) == 0
    assert push(pre_push, clone, line(head, ZERO, "feat/y")) == 0
    assert push(pre_push, clone, line(head, remote(clone))) == 0
    assert log(clone) == [ci(clone, "--quick"), ci(clone)]


HOOK = Path(__file__).resolve().parents[3] / "scripts" / "hooks" / "pre-push"
STAND_IN = '#!/bin/sh\necho "pre-push $*" >> "$PRE_PUSH_LOG"\ncat >> "$PRE_PUSH_LOG"\nexit 0\n'


def test_the_hook_hands_the_push_to_the_script_of_the_checkout(clone):
    (clone / ".git" / "hooks" / "pre-push").symlink_to(HOOK)
    (clone / "scripts" / "pre-push").write_text(STAND_IN)
    (clone / "scripts" / "pre-push").chmod(0o755)
    git(clone, "add", "scripts")
    git(clone, "commit", "--quiet", "-m", "Add the pre-push stand-in")
    before, head = remote(clone), git(clone, "rev-parse", "HEAD")
    git(clone, "push", "--quiet", "origin", "main")
    origin = str(clone.parent / "origin.git")
    assert log(clone) == [f"pre-push origin {origin}", line(head, before).strip()]


def test_the_hook_lets_a_checkout_without_the_script_push(clone):
    (clone / ".git" / "hooks" / "pre-push").symlink_to(HOOK)
    head = commit(clone, "backend/app.py")
    result = subprocess.run(
        ["git", "push", "--quiet", "origin", "main"], cwd=clone, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "has no scripts/pre-push, nothing checked" in result.stderr
    assert remote(clone) == head
