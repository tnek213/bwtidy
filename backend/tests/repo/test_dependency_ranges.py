import re
import tomllib

from devlib.paths import ROOT


def requirements() -> list[str]:
    pyproject = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text())
    found = list(pyproject["project"]["dependencies"])
    for group in pyproject["dependency-groups"].values():
        found += [r for r in group if isinstance(r, str)]
    return found


def test_every_backend_dependency_has_an_upper_bound():
    """A compatible range as npm's caret spells it, so uv lock --upgrade stays within it."""
    unbounded = [r for r in requirements() if not re.search(r"<|==", r)]
    assert unbounded == []
