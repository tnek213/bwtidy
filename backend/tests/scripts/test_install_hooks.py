import os
from pathlib import Path

import pytest

from tests.support.git_repo import cloned, git

HOOK_TEXT = "#!/bin/sh\nexit 0\n"


def add_hook(checkout: Path) -> Path:
    hook = checkout / "scripts" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(HOOK_TEXT)
    hook.chmod(0o755)
    return hook


@pytest.fixture(scope="module")
def install_hooks(load_script):
    return load_script("install-hooks")


@pytest.fixture
def clone(tmp_path) -> Path:
    clone = cloned(tmp_path)
    add_hook(clone)
    return clone


def link_of(clone: Path) -> Path:
    return clone / ".git" / "hooks" / "pre-push"


def test_the_hook_is_linked_when_there_is_none(install_hooks, clone):
    assert install_hooks.install(clone, force=False) == 0
    link = link_of(clone)
    assert link.is_symlink()
    assert Path(os.readlink(link)) == clone / "scripts" / "hooks" / "pre-push"


def test_installing_again_changes_nothing(install_hooks, clone, capsys):
    assert install_hooks.install(clone, force=False) == 0
    assert install_hooks.install(clone, force=False) == 0
    assert "already links" in capsys.readouterr().out


def test_a_foreign_hook_is_printed_and_kept(install_hooks, clone, capsys):
    link_of(clone).write_text("#!/bin/sh\necho mine\n")
    assert install_hooks.install(clone, force=False) == 1
    captured = capsys.readouterr()
    assert "echo mine" in captured.out
    assert "not ours, kept" in captured.err
    assert "--force" in captured.err
    assert link_of(clone).read_text() == "#!/bin/sh\necho mine\n"


def test_a_foreign_link_is_named_and_kept(install_hooks, clone, tmp_path, capsys):
    link_of(clone).symlink_to(tmp_path / "elsewhere")
    assert install_hooks.install(clone, force=False) == 1
    assert f"a link to {tmp_path / 'elsewhere'}" in capsys.readouterr().out
    assert Path(os.readlink(link_of(clone))) == tmp_path / "elsewhere"


def test_force_replaces_a_foreign_hook(install_hooks, clone):
    link_of(clone).write_text("#!/bin/sh\necho mine\n")
    assert install_hooks.install(clone, force=True) == 0
    assert link_of(clone).is_symlink()


def test_a_link_of_ours_into_a_removed_worktree_is_moved(install_hooks, clone, tmp_path):
    link_of(clone).symlink_to(tmp_path / "gone" / "scripts" / "hooks" / "pre-push")
    assert install_hooks.install(clone, force=False) == 0
    assert Path(os.readlink(link_of(clone))) == clone / "scripts" / "hooks" / "pre-push"


def test_a_worktree_links_to_the_main_checkouts_hook(install_hooks, clone, tmp_path, capsys):
    git(clone, "add", "scripts")
    git(clone, "commit", "--quiet", "-m", "Add the hook")
    worktree = tmp_path / "worktree"
    git(clone, "worktree", "add", "--quiet", "-b", "feat/x", str(worktree))
    assert install_hooks.install(worktree, force=False) == 0
    assert Path(os.readlink(link_of(clone))) == clone / "scripts" / "hooks" / "pre-push"
    assert "no scripts/hooks/pre-push yet" not in capsys.readouterr().out


def test_a_worktree_links_to_its_own_hook_while_the_main_checkout_has_none(
    install_hooks, tmp_path, capsys
):
    clone = cloned(tmp_path)
    worktree = tmp_path / "worktree"
    git(clone, "worktree", "add", "--quiet", "-b", "feat/x", str(worktree))
    add_hook(worktree)
    assert install_hooks.install(worktree, force=False) == 0
    assert Path(os.readlink(link_of(clone))) == worktree / "scripts" / "hooks" / "pre-push"
    assert "breaks when it is removed" in capsys.readouterr().out


def test_a_hooks_path_in_the_config_is_named_and_left(install_hooks, clone, capsys):
    git(clone, "config", "core.hooksPath", "elsewhere")
    assert install_hooks.install(clone, force=False) == 1
    assert "core.hooksPath is elsewhere" in capsys.readouterr().err
    assert git(clone, "config", "core.hooksPath") == "elsewhere"
    assert link_of(clone).is_symlink()


def test_the_committed_hook_is_executable(install_hooks):
    assert os.access(install_hooks.ROOT / install_hooks.HOOK, os.X_OK)
