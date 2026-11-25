import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def ci(load_script):
    return load_script("ci")


@pytest.fixture
def hook(tmp_path: Path, monkeypatch, ci) -> Path:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    monkeypatch.setattr(ci, "ROOT", tmp_path)
    return tmp_path / ".git" / "hooks" / "pre-commit"


def test_the_hook_is_installed_when_there_is_none(hook, ci):
    assert ci.main(["install-githooks"]) == 0
    assert hook.read_text() == ci.HOOK
    assert hook.stat().st_mode & 0o111


def test_our_own_hook_is_installed_again(hook, ci):
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(ci.HOOK)
    assert ci.main(["install-githooks"]) == 0
    assert hook.read_text() == ci.HOOK


def test_a_foreign_hook_is_printed_and_kept(hook, capsys, ci):
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n")
    assert ci.main(["install-githooks"]) == 1
    captured = capsys.readouterr()
    assert "echo mine" in captured.out
    assert "--force" in captured.err
    assert hook.read_text() == "#!/bin/sh\necho mine\n"


def test_force_overwrites_a_foreign_hook(hook, ci):
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n")
    assert ci.main(["install-githooks", "--force"]) == 0
    assert hook.read_text() == ci.HOOK
