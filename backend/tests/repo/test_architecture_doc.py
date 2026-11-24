"""docs/architecture.md names every backend module and frontend directory, and its paths exist."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs" / "architecture.md"
PACKAGE = ROOT / "backend" / "src" / "bwtidy"
FRONTEND = ROOT / "frontend" / "src"
## a path in the document is relative to one of these
BASES = (ROOT, ROOT / "backend", PACKAGE, FRONTEND, ROOT / "docs")
TOP_DIRS = ("backend/", "frontend/", "docs/", "dev/", "scripts/")
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".md", ".yaml", ".yml", ".toml", ".json", ".html")
PATH = re.compile(r"[\w.-]+(?:/[\w.-]+)*/?")


def inline_code() -> set[str]:
    """The backtick spans of the document outside its code blocks."""
    text = re.sub(r"^```.*?^```", "", DOC.read_text(), flags=re.DOTALL | re.MULTILINE)
    return set(re.findall(r"`([^`\n]+)`", text))


def looks_like_file(span: str) -> bool:
    if not PATH.fullmatch(span):
        return False
    return span.startswith(TOP_DIRS) or span.endswith(SOURCE_SUFFIXES)


def test_every_backend_module_has_a_line() -> None:
    named = inline_code()
    modules = sorted(
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*.py")
        if path.name != "__init__.py"
    )
    assert modules
    missing = [module for module in modules if module not in named]
    assert not missing, f"docs/architecture.md does not name {missing}"


def test_every_frontend_directory_has_a_line() -> None:
    named = inline_code()
    directories = sorted(f"{path.name}/" for path in FRONTEND.iterdir() if path.is_dir())
    assert directories
    missing = [directory for directory in directories if directory not in named]
    assert not missing, f"docs/architecture.md does not name {missing}"


def test_every_path_in_the_document_exists() -> None:
    paths = sorted(span for span in inline_code() if looks_like_file(span))
    assert paths
    missing = [path for path in paths if not any((base / path).exists() for base in BASES)]
    assert not missing, f"docs/architecture.md names paths that do not exist: {missing}"
