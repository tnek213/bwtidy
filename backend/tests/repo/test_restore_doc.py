"""docs/restore.md runs on a stock python3: Python 3.8 or later and its standard library only.

It is for a lost vault, on any Linux or macOS machine, so it can count on neither bwtidy nor a
package to install. What it does against a real account is test_backup_import.py's.
"""

import ast
import sys
from pathlib import Path

import pytest

from tests.support.restore_doc import attach_program, commands

ROOT = Path(__file__).resolve().parents[3]
TEXT = (ROOT / "docs" / "restore.md").read_text()
## the oldest python3 still found on a stock machine (macOS has 3.9 with its developer tools)
OLDEST = (3, 8)
PROGRAMS = {"attach.py": attach_program(TEXT)} | {
    f"python3 -c #{i}": source for i, source in enumerate(commands(TEXT), 1)
}


def imported(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_the_document_has_its_programs():
    ## attach.py, step 1 (data.json, the counts, the note), 4, 5 (by hand), 6 and 7
    assert len(PROGRAMS) == 8


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
