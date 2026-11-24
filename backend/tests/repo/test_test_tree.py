"""backend/tests is sorted by what a test covers, and no file imports a test or a conftest.

A unit test file is named after the module it tests, so the module's tests are found in one
place. Its folders are packages, so two of them can hold a test file of the same name
(tests/unit/api/test_sections.py and tests/unit/queue/test_sections.py).

A conftest.py is where pytest collects fixtures from, not a module to import: a helper a test
and a support module share goes into a support module, and a support module that imports the
conftest turns the dependency around.
"""

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
SUITES = ("unit", "scripts", "repo", "integration", "e2e")
UNIT = TESTS / "unit"
SOURCE = TESTS.parent / "src" / "bwtidy"
## unit tests of a module of tests/support, which no module of bwtidy is named after
SUPPORT_TESTS = {
    "test_passphrase_standin.py": "the scrypt stand-in of tests/support/passphrase_stand_in.py",
    "test_support_quiet.py": "the assertions of tests/support/quiet.py",
}


def python_files() -> list[Path]:
    return sorted(
        path for path in TESTS.rglob("*.py") if not {"__pycache__", ".venv"} & set(path.parts)
    )


def imported_modules(path: Path) -> list[str]:
    """Every dotted name the file imports, a name taken from a package included."""
    modules: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            modules.append(base)
            modules += [f"{base}.{alias.name}".strip(".") for alias in node.names]
    return modules


def test_no_file_imports_a_test_module():
    found = [
        f"{path.relative_to(TESTS)} imports {module}"
        for path in python_files()
        for module in imported_modules(path)
        if any(part.startswith("test_") for part in module.split("."))
    ]
    assert not found, f"shared helpers belong in a support module, not in a test: {found}"


def test_no_file_imports_a_conftest():
    found = [
        f"{path.relative_to(TESTS)} imports {module}"
        for path in python_files()
        for module in imported_modules(path)
        if "conftest" in module.split(".")
    ]
    assert not found, f"fixtures are collected, not imported: {found}"


def test_every_test_file_is_in_a_suite_directory():
    outside = [
        str(path.relative_to(TESTS))
        for path in python_files()
        if path.name.startswith("test_") and path.relative_to(TESTS).parts[0] not in SUITES
    ]
    assert not outside, f"a test file goes under one of {SUITES}: {outside}"


def test_every_unit_test_file_is_named_after_the_module_it_tests():
    """tests/unit/PKG/test_NAME.py tests bwtidy/PKG/NAME.py, tests/unit/test_NAME.py
    bwtidy/NAME.py."""
    stray = []
    for path in sorted(UNIT.rglob("test_*.py")):
        relative = path.relative_to(UNIT)
        if str(relative) in SUPPORT_TESTS:
            continue
        module = SOURCE / relative.parent / (path.stem.removeprefix("test_") + ".py")
        if not module.is_file():
            stray.append(f"{relative} names no {module.relative_to(SOURCE.parent)}")
    assert not stray, f"a unit test file is named after the module it tests: {stray}"


def test_every_folder_of_the_unit_tests_is_a_package():
    folders = [TESTS, UNIT, *(path for path in UNIT.iterdir() if path.is_dir())]
    missing = [
        str(folder.relative_to(TESTS.parent))
        for folder in folders
        if folder.name != "__pycache__" and not (folder / "__init__.py").is_file()
    ]
    assert not missing, f"two test files of one name clash outside a package: {missing}"
