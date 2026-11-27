"""docs/restore.md runs on a stock python3: Python 3.8 or later and its standard library only.

It is for a lost vault, on any Linux or macOS machine, so it can count on neither bwtidy nor a
package to install. What it does against a real account is test_backup_import.py's.
"""

import ast
import re
import sys
from pathlib import Path

import pytest

from tests.support.restore_doc import programs

ROOT = Path(__file__).resolve().parents[3]
TEXT = (ROOT / "docs" / "restore.md").read_text()
## the oldest python3 still found on a stock machine (macOS has 3.9 with its developer tools)
OLDEST = (3, 8)
PROGRAMS = programs(TEXT)
SHELL = re.compile(r"^```sh\n(.*?)^```$", re.DOTALL | re.MULTILINE)
RUNS = re.compile(r"\bpython3 (\S+\.py)\b")


def imported(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_the_document_has_its_programs():
    assert set(PROGRAMS) == {"unpack.py", "orgs.py", "attach.py", "show.py", "trash.py", "check.py"}


def test_the_shell_blocks_hold_no_python():
    ## the programs are files of their own, so each one is shown once as Python
    assert "python3 -c" not in TEXT
    assert "<<" not in TEXT


def test_the_shell_blocks_run_the_programs_of_the_page():
    run = {name for block in SHELL.findall(TEXT) for name in RUNS.findall(block)}
    assert run == set(PROGRAMS)


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_each_program_opens_its_files_as_utf_8(name):
    ## Windows opens a file in its code page, PYTHONUTF8 aside
    calls = [
        node
        for node in ast.walk(ast.parse(PROGRAMS[name]))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"
    ]
    assert all(any(k.arg == "encoding" for k in call.keywords) for call in calls), name


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_each_program_is_python_3_8(name):
    ast.parse(PROGRAMS[name], feature_version=OLDEST)


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_each_program_needs_only_the_standard_library(name):
    tree = ast.parse(PROGRAMS[name])
    assert imported(tree) <= sys.stdlib_module_names


def test_the_document_needs_no_jq_or_unzip():
    ## the old commands of the document, a stock machine lacks them
    words = TEXT.replace("`", " ").split()
    assert "jq" not in words
    assert "unzip" not in words


def test_the_document_says_what_each_system_needs():
    for heading in ("Linux", "macOS", "Windows"):
        assert f"\n### {heading}\n" in TEXT
    commands_needed = ("brew install age", "winget install --id FiloSottile.age", "winpty")
    for needed in (*commands_needed, "alias python3=python"):
        assert needed in TEXT


def test_the_shell_commands_run_in_bash_3_and_without_dev_shm():
    ## macOS has bash 3.2 and no /dev/shm, Git Bash has no tmpfs either
    for bash4 in ("mapfile", "readarray", "declare -A", "${D,,}", "${D^^}"):
        assert bash4 not in TEXT
    assert "mktemp -d -p" not in TEXT
    assert "PYTHONUTF8=1" in TEXT
