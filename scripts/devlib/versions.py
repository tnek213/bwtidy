"""The version: written by hand in backend/pyproject.toml, repeated in frontend/package.json."""

import functools
import importlib.util
import json
import tomllib
from types import ModuleType

from devlib.paths import ROOT


@functools.cache
def _version_module() -> ModuleType:
    ## backend/src/bwtidy/version.py is stdlib only, so it loads here without the backend venv
    spec = importlib.util.spec_from_file_location(
        "bwtidy_version", ROOT / "backend" / "src" / "bwtidy" / "version.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def semver(version: str) -> str:
    """The version as semver.org spells it: 0.1.0a1 is 0.1.0-alpha.1, b is beta, rc is rc."""
    return str(_version_module().semver(version))


def pyproject_version() -> str:
    """The version in backend/pyproject.toml, the one place it is written."""
    pyproject = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text())
    return str(pyproject["project"]["version"])


def package_json_version() -> str:
    """The version in frontend/package.json."""
    return str(json.loads((ROOT / "frontend" / "package.json").read_text())["version"])


def version_mismatch() -> str | None:
    """Why frontend/package.json does not carry backend/pyproject.toml's version, else None."""
    python = pyproject_version()
    try:
        expected = semver(python)
    except ValueError as error:
        return str(error)
    actual = package_json_version()
    if actual != expected:
        return (
            f"frontend/package.json says {actual} but backend/pyproject.toml says {python}, "
            f"which is {expected} as semver spells it"
        )
    return None
