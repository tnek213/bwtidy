import json

from devlib import health, versions


def test_the_two_versions_of_the_repo_agree():
    assert versions.version_mismatch() is None


def test_ci_names_a_package_json_that_drifted(tmp_path, monkeypatch):
    package = tmp_path / "package.json"
    package.write_text(json.dumps({"version": "0.1.0-alpha.2"}))
    monkeypatch.setattr(
        versions, "package_json_version", lambda: json.loads(package.read_text())["version"]
    )
    monkeypatch.setattr(versions, "pyproject_version", lambda: "0.1.0a1")
    mismatch = versions.version_mismatch()
    assert mismatch is not None
    assert "0.1.0-alpha.2" in mismatch
    assert "0.1.0-alpha.1" in mismatch


def test_health_text_is_the_version_and_the_commit():
    assert health.health_text("261111 7db8830") == (
        f"{versions.semver(versions.pyproject_version())} (261111 7db8830)"
    )
