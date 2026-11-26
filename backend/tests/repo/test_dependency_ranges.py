import re
import tomllib

import pytest
from devlib.paths import ROOT


def requirements(project: str) -> list[str]:
    pyproject = tomllib.loads((ROOT / project / "pyproject.toml").read_text())
    found = list(pyproject["project"]["dependencies"])
    for group in pyproject["dependency-groups"].values():
        found += [r for r in group if isinstance(r, str)]
    return found


@pytest.mark.parametrize("project", ["backend", "demo"])
def test_every_dependency_has_an_upper_bound(project):
    """A compatible range as npm's caret spells it, so uv lock --upgrade stays within it.

    The demo's bwtidy comes from a path source, so it is the one requirement without a bound.
    """
    unbounded = [r for r in requirements(project) if r != "bwtidy" and not re.search(r"<|==", r)]
    assert unbounded == []
