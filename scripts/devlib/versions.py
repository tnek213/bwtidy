"""The version: written by hand in backend/pyproject.toml, repeated in frontend/package.json.

The lock files carry generated copies: backend/uv.lock (the bwtidy package, Python's spelling)
and frontend/package-lock.json (its top-level version and packages[""], semver's spelling).
docs/releases.md has a heading for it in semver's spelling, its release notes.
"""

import functools
import importlib.util
import json
import re
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


def uv_lock_version() -> str:
    """The version of the bwtidy package in backend/uv.lock."""
    lock = tomllib.loads((ROOT / "backend" / "uv.lock").read_text())
    for package in lock.get("package", []):
        if package.get("name") == "bwtidy":
            return str(package["version"])
    raise ValueError("backend/uv.lock has no package bwtidy")


def package_lock_versions() -> tuple[str, str]:
    """The top-level version and the version of packages[""] in frontend/package-lock.json."""
    lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text())
    return str(lock["version"]), str(lock["packages"][""]["version"])


RELEASE_NOTES = "docs/releases.md"


def release_notes_problem(version: str) -> str | None:
    """Why docs/releases.md has no notes for the version, else None.

    The notes of a version are under a heading that starts with it, such as `## 0.1.0-alpha.1`.
    """
    try:
        text = (ROOT / RELEASE_NOTES).read_text()
    except OSError as error:
        return f"cannot read {RELEASE_NOTES}: {error.strerror}"
    heading = re.compile(rf"^#+[ \t]+{re.escape(version)}(?:[ \t]|$)", re.MULTILINE)
    if heading.search(text):
        return None
    return (
        f"{RELEASE_NOTES} has no heading for {version}, the version as frontend/package.json "
        f"spells it: write its release notes under '## {version}' "
        "(dev/release.md, The release notes)"
    )


def version_mismatch() -> str | None:
    """Why a copy of backend/pyproject.toml's version differs from it, else None.

    The copies are frontend/package.json and the lock files backend/uv.lock and
    frontend/package-lock.json, one line for each copy that differs. A last line says when
    docs/releases.md has no heading for the version (semver's spelling, as in package.json).
    """
    python = pyproject_version()
    try:
        expected = semver(python)
    except ValueError as error:
        return str(error)
    source = f"backend/pyproject.toml says {python}"
    in_semver = f"{source}, which is {expected} as semver spells it"
    try:
        top, root_package = package_lock_versions()
        copies = [
            ("frontend/package.json", package_json_version(), expected, in_semver),
            ("backend/uv.lock (package bwtidy)", uv_lock_version(), python, source),
            ("frontend/package-lock.json (version)", top, expected, in_semver),
            (
                'frontend/package-lock.json (packages[""].version)',
                root_package,
                expected,
                in_semver,
            ),
        ]
    except (OSError, KeyError, ValueError) as error:
        return f"cannot read a version: {error}"
    problems = [
        f"{where} says {actual} but {why}"
        for where, actual, wanted, why in copies
        if actual != wanted
    ]
    notes = release_notes_problem(expected)
    if notes is not None:
        problems.append(notes)
    return "\n".join(problems) or None
