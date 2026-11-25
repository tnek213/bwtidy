"""The docs tests of the tutorials run in the order of the docs on one vault, as a reader takes
them: each continues from the vault the one before left (backend/tests/e2e/docs/test_tutorials.py).

pytest runs the tests of a file in their order, and a file on one stack (scripts/e2e, loadfile),
so the tests of the marker tutorial are the tests of one file, in the order of the Tutorial
section of zensical.toml and docs/index.md, and only its module-scoped fixture puts the vault
back.
"""

import ast
import re
import tomllib
from pathlib import Path

from devlib.paths import ROOT

DOCS_TESTS = ROOT / "backend" / "tests" / "e2e" / "docs"


def nav_tutorials() -> list[str]:
    nav = tomllib.loads((ROOT / "zensical.toml").read_text())["project"]["nav"]
    return next(entry["Tutorial"] for entry in nav if "Tutorial" in entry)


def index_tutorials() -> list[str]:
    text = (ROOT / "docs" / "index.md").read_text()
    start = text.index("\n## Tutorial\n")
    end = text.index("\n## ", start + 1)
    return re.findall(r"\]\((tutorial/[^)#]+)\)", text[start:end])


def tutorial_page(function: ast.FunctionDef) -> str | None:
    """The page of the function's marker tutorial, None without one."""
    for decorator in function.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and ast.unparse(decorator.func) == "pytest.mark.tutorial"
            and isinstance(decorator.args[0], ast.Constant)
            and isinstance(page := decorator.args[0].value, str)
        ):
            return page
    return None


def functions(path: Path) -> list[ast.FunctionDef]:
    tree = ast.parse(path.read_text())
    return [node for node in tree.body if isinstance(node, ast.FunctionDef)]


def tutorial_files() -> dict[Path, list[tuple[str, str]]]:
    """The files of the docs tests with a tutorial test, their tests and pages in their order."""
    found: dict[Path, list[tuple[str, str]]] = {}
    for path in sorted(DOCS_TESTS.glob("test_*.py")):
        tests = [(f.name, page) for f in functions(path) if (page := tutorial_page(f))]
        if tests:
            found[path] = tests
    return found


def test_the_index_lists_the_tutorials_in_the_nav_order():
    assert index_tutorials() == nav_tutorials()


def test_the_tutorial_tests_are_one_file_in_the_docs_order():
    files = tutorial_files()
    assert len(files) == 1, f"the tutorials' tests are in several files: {sorted(files)}"
    [(path, tests)] = files.items()
    assert [page for _, page in tests] == nav_tutorials()
    names = [f.name for f in functions(path) if f.name.startswith("test_")]
    assert names == [name for name, _ in tests], "a test without the marker tutorial between them"


def test_only_the_module_fixture_puts_the_vault_back():
    [path] = tutorial_files()
    callers = []
    for function in functions(path):
        calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "put_back"
        ]
        if calls:
            callers.append(function)
    assert [function.name for function in callers] == ["one_vault"]
    [fixture] = callers[0].decorator_list
    assert ast.unparse(fixture) == "pytest.fixture(scope='module')"
