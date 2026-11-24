import json
from pathlib import Path

import pytest
from devlib import health, versions

PYTHON = "0.1.0a1"
SEMVER = "0.1.0-alpha.1"
DRIFTED = "0.1.0-alpha.2"


class FakeRepo:
    """The four files that carry the version, under a root of their own, all in agreement."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "backend").mkdir()
        (root / "frontend").mkdir()
        (root / "backend" / "pyproject.toml").write_text(
            f'[project]\nname = "bwtidy"\nversion = "{PYTHON}"\n'
        )
        self.uv_lock(PYTHON)
        self.package_json(SEMVER)
        self.package_lock(SEMVER, SEMVER)

    def uv_lock(self, version: str) -> None:
        (self.root / "backend" / "uv.lock").write_text(
            "version = 1\n\n"
            '[[package]]\nname = "anyio"\nversion = "4.11.0"\n\n'
            f'[[package]]\nname = "bwtidy"\nversion = "{version}"\n'
        )

    def package_json(self, version: str) -> None:
        (self.root / "frontend" / "package.json").write_text(
            json.dumps({"name": "bwtidy-frontend", "version": version})
        )

    def package_lock(self, top: str, root_package: str) -> None:
        lock = {
            "name": "bwtidy-frontend",
            "version": top,
            "lockfileVersion": 3,
            "packages": {"": {"name": "bwtidy-frontend", "version": root_package}},
        }
        (self.root / "frontend" / "package-lock.json").write_text(json.dumps(lock))


@pytest.fixture
def repo(tmp_path, monkeypatch) -> FakeRepo:
    ## semver loads bwtidy/version.py from ROOT once and keeps it, so load it before ROOT moves
    versions.semver(PYTHON)
    monkeypatch.setattr(versions, "ROOT", tmp_path)
    return FakeRepo(tmp_path)


def test_the_versions_of_the_repo_agree():
    assert versions.version_mismatch() is None


def test_the_fake_repo_agrees(repo):
    assert versions.version_mismatch() is None


def test_ci_names_a_package_json_that_drifted(repo):
    repo.package_json(DRIFTED)
    assert versions.version_mismatch() == (
        f"frontend/package.json says {DRIFTED} but backend/pyproject.toml says {PYTHON}, "
        f"which is {SEMVER} as semver spells it"
    )


def test_ci_names_a_uv_lock_that_drifted(repo):
    repo.uv_lock("0.1.0a2")
    assert versions.version_mismatch() == (
        f"backend/uv.lock (package bwtidy) says 0.1.0a2 but backend/pyproject.toml says {PYTHON}"
    )


def test_ci_names_the_top_level_version_of_a_package_lock_that_drifted(repo):
    repo.package_lock(DRIFTED, SEMVER)
    assert versions.version_mismatch() == (
        f"frontend/package-lock.json (version) says {DRIFTED} but backend/pyproject.toml says "
        f"{PYTHON}, which is {SEMVER} as semver spells it"
    )


def test_ci_names_the_root_package_of_a_package_lock_that_drifted(repo):
    repo.package_lock(SEMVER, DRIFTED)
    assert versions.version_mismatch() == (
        f'frontend/package-lock.json (packages[""].version) says {DRIFTED} but '
        f"backend/pyproject.toml says {PYTHON}, which is {SEMVER} as semver spells it"
    )


def test_ci_names_every_copy_that_drifted(repo):
    repo.package_json(DRIFTED)
    repo.uv_lock("0.1.0a2")
    mismatch = versions.version_mismatch()
    assert mismatch is not None
    assert [line.split(" says ")[0] for line in mismatch.splitlines()] == [
        "frontend/package.json",
        "backend/uv.lock (package bwtidy)",
    ]


def test_ci_names_a_uv_lock_without_bwtidy(repo):
    (repo.root / "backend" / "uv.lock").write_text('version = 1\n\n[[package]]\nname = "anyio"\n')
    mismatch = versions.version_mismatch()
    assert mismatch is not None
    assert "backend/uv.lock has no package bwtidy" in mismatch


def test_health_text_is_the_version_and_the_commit():
    assert health.health_text("261111 7db8830") == (
        f"{versions.semver(versions.pyproject_version())} (261111 7db8830)"
    )
