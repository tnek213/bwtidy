from pathlib import Path

import pytest
from devlib import prepush
from devlib.prepush import RefUpdate

from tests.support.git_repo import cloned, commit, git

ZERO = "0" * 40


@pytest.fixture
def clone(tmp_path):
    return cloned(tmp_path)


def update(local_sha: str, remote_sha: str, branch: str = "main") -> RefUpdate:
    return RefUpdate(f"refs/heads/{branch}", local_sha, f"refs/heads/{branch}", remote_sha)


def pushed(clone: Path) -> str:
    return git(clone, "rev-parse", "origin/main")


def test_the_lines_of_stdin_are_the_updates():
    sha, other = "a" * 40, "b" * 40
    text = f"refs/heads/x {sha} refs/heads/x {other}\n\nrefs/tags/v1 {sha} refs/tags/v1 {ZERO}\n"
    assert prepush.parse_updates(text) == [
        RefUpdate("refs/heads/x", sha, "refs/heads/x", other),
        RefUpdate("refs/tags/v1", sha, "refs/tags/v1", ZERO),
    ]


def test_an_empty_stdin_is_no_update():
    assert prepush.parse_updates("") == []


def test_a_line_that_is_not_four_fields_is_refused():
    with pytest.raises(ValueError, match="not a pre-push line: refs/heads/x abc"):
        prepush.parse_updates("refs/heads/x abc\n")


def test_zeros_of_either_hash_are_no_commit():
    assert prepush.is_zero(ZERO)
    assert prepush.is_zero("0" * 64)
    assert not prepush.is_zero("0" * 39 + "1")


def test_a_delete_and_a_new_ref_are_told_by_their_zeros():
    delete = RefUpdate("(delete)", ZERO, "refs/heads/x", "a" * 40)
    created = RefUpdate("refs/heads/x", "a" * 40, "refs/heads/x", ZERO)
    assert delete.deleted
    assert not delete.created
    assert created.created
    assert not created.deleted


def test_the_name_drops_the_ref_prefix():
    assert update("a", "b", "feat/x").name == "feat/x"
    assert RefUpdate("refs/tags/v1", "a", "refs/tags/v1", ZERO).name == "v1"


@pytest.mark.parametrize(
    ("paths", "ui"),
    [
        (["frontend/src/App.tsx"], True),
        (["backend/tests/e2e/docs/test_tutorial.py"], True),
        (["README.md", "frontend/src/grid/Grid.tsx"], True),
        (["frontend/package.json", "frontend/srcs/x.ts"], False),
        (["backend/tests/e2e/test_grid.py", "docs/images/grid.png"], False),
        ([], False),
    ],
)
def test_only_the_frontend_source_and_the_docs_tests_are_the_ui(paths, ui):
    assert prepush.touches_ui(paths) is ui


def test_an_update_counts_the_commits_after_the_remote_one(clone):
    before = pushed(clone)
    first = commit(clone, "a.txt")
    second = commit(clone, "b.txt")
    assert prepush.new_commits(update(second, before), "origin", clone) == [second, first]


def test_a_delete_brings_no_commit(clone):
    delete = RefUpdate("(delete)", ZERO, "refs/heads/old", pushed(clone))
    assert prepush.new_commits(delete, "origin", clone) == []
    assert prepush.plan([delete], "origin", clone) == prepush.Push([], [])


def test_a_new_branch_on_a_pushed_commit_brings_no_commit(clone):
    assert prepush.new_commits(update(pushed(clone), ZERO, "feat/x"), "origin", clone) == []


def test_a_tag_on_a_pushed_commit_brings_no_commit(clone):
    git(clone, "tag", "-a", "-m", "v1", "v1")
    tag_object = git(clone, "rev-parse", "v1")
    tag = RefUpdate("refs/tags/v1", tag_object, "refs/tags/v1", ZERO)
    assert prepush.tip(tag, clone) == pushed(clone)
    assert prepush.plan([tag], "origin", clone) == prepush.Push([], [])


def test_a_rewound_branch_brings_no_commit(clone):
    old = pushed(clone)
    newer = commit(clone, "a.txt")
    git(clone, "push", "--quiet", "origin", "main")
    assert prepush.new_commits(update(old, newer), "origin", clone) == []


def test_a_new_branch_counts_the_commits_on_no_remote_branch(clone):
    git(clone, "checkout", "--quiet", "-b", "feat/x")
    first = commit(clone, "a.txt")
    assert prepush.new_commits(update(first, ZERO, "feat/x"), "origin", clone) == [first]


def test_an_update_compares_with_the_remote_commit(clone):
    before = commit(clone, "frontend/src/App.tsx")
    git(clone, "push", "--quiet", "origin", "main")
    after = commit(clone, "README.md")
    assert prepush.changed_files(update(after, before), "origin", clone) == ["README.md"]
    assert prepush.plan([update(after, before)], "origin", clone).ui == []


def test_a_new_branch_compares_with_the_merge_base_with_main(clone):
    git(clone, "checkout", "--quiet", "-b", "feat/x")
    branch = commit(clone, "frontend/src/App.tsx")
    git(clone, "checkout", "--quiet", "main")
    commit(clone, "backend/tests/e2e/docs/test_tutorial.py")
    git(clone, "push", "--quiet", "origin", "main")
    git(clone, "checkout", "--quiet", "-b", "feat/y", "origin/main")
    other = commit(clone, "README.md")

    ui = update(branch, ZERO, "feat/x")
    plain = update(other, ZERO, "feat/y")
    assert prepush.changed_files(ui, "origin", clone) == ["frontend/src/App.tsx"]
    assert prepush.changed_files(plain, "origin", clone) == ["README.md"]
    assert prepush.plan([ui, plain], "origin", clone) == prepush.Push([ui, plain], [ui])


def test_a_remote_commit_this_repo_lacks_compares_with_main(clone):
    first = commit(clone, "frontend/src/App.tsx")
    unknown = update(first, "f" * 40)
    assert prepush.base(unknown, "origin", clone) == pushed(clone)
    assert prepush.plan([unknown], "origin", clone).ui == [unknown]


def test_an_update_with_nothing_to_compare_with_counts_as_the_ui(clone):
    git(clone, "update-ref", "-d", "refs/remotes/origin/main")
    git(clone, "checkout", "--quiet", "-b", "feat/x")
    first = commit(clone, "README.md")
    created = update(first, ZERO, "feat/x")
    assert prepush.changed_files(created, "elsewhere", clone) is None
    assert prepush.plan([created], "elsewhere", clone).ui == [created]


def test_a_remote_without_main_compares_with_origins(clone):
    git(clone, "checkout", "--quiet", "-b", "feat/x")
    first = commit(clone, "README.md")
    assert prepush.main_ref("fork", clone) == "refs/remotes/origin/main"
    assert prepush.changed_files(update(first, ZERO, "feat/x"), "fork", clone) == ["README.md"]


def test_a_step_that_passed_on_a_tree_is_recorded(clone):
    tree = git(clone, "rev-parse", "HEAD^{tree}")
    assert not prepush.passed("ci", tree, clone)
    prepush.record("ci", tree, clone)
    assert prepush.passed("ci", tree, clone)
    assert not prepush.passed("docs-shots", tree, clone)
    assert prepush.record_path(clone) == clone / ".git" / prepush.RECORD


def test_the_record_keeps_the_last_lines(clone, monkeypatch):
    monkeypatch.setattr(prepush, "RECORD_LINES", 2)
    for tree in ("t1", "t2", "t3"):
        prepush.record("ci", tree, clone)
    assert not prepush.passed("ci", "t1", clone)
    assert prepush.passed("ci", "t3", clone)
