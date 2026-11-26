import json
from pathlib import Path

from devlib import locks

CLI = Path("docker") / "bitwarden-cli"
MARKDOWNLINT = Path("tools") / "markdownlint"


def write_project(root: Path, project: Path, wanted: dict, locked: dict | None) -> None:
    (root / project).mkdir(parents=True)
    (root / project / "package.json").write_text(json.dumps({"name": "x", **wanted}))
    if locked is not None:
        lock = {"name": "x", "lockfileVersion": 3, "packages": {"": {"name": "x", **locked}}}
        (root / project / "package-lock.json").write_text(json.dumps(lock))


def agreeing_repo(root: Path) -> None:
    deps = {"dependencies": {"react": "^19.2.0"}, "devDependencies": {"vite": "^8.0.0"}}
    write_project(root, Path("frontend"), deps, deps)
    cli = {"dependencies": {"@bitwarden/cli": "2026.10.0"}}
    write_project(root, CLI, cli, cli)
    lint = {"devDependencies": {"markdownlint-cli2": "0.24.2"}}
    write_project(root, MARKDOWNLINT, lint, lint)


def test_the_lock_files_of_the_repo_match_their_package_json():
    assert locks.lock_mismatch() is None


def test_agreeing_lock_files_pass(tmp_path):
    agreeing_repo(tmp_path)
    assert locks.lock_mismatch(tmp_path) is None


def test_a_dependency_changed_without_npm_install_is_named(tmp_path):
    agreeing_repo(tmp_path)
    package = tmp_path / CLI / "package.json"
    package.write_text(json.dumps({"name": "x", "dependencies": {"@bitwarden/cli": "2026.11.0"}}))
    assert locks.lock_mismatch(tmp_path) == (
        "docker/bitwarden-cli/package-lock.json is out of date: its dependencies differ from "
        "package.json, run npm install in docker/bitwarden-cli"
    )


def test_a_new_dev_dependency_is_named(tmp_path):
    agreeing_repo(tmp_path)
    package = tmp_path / "frontend" / "package.json"
    wanted = json.loads(package.read_text())
    wanted["devDependencies"]["knip"] = "^6.0.0"
    package.write_text(json.dumps(wanted))
    mismatch = locks.lock_mismatch(tmp_path)
    assert mismatch is not None
    assert mismatch.startswith("frontend/package-lock.json is out of date: its devDependencies")


def test_a_missing_lock_file_is_named(tmp_path):
    write_project(tmp_path, Path("frontend"), {"dependencies": {"react": "^19.2.0"}}, None)
    write_project(tmp_path, CLI, {}, {})
    write_project(tmp_path, MARKDOWNLINT, {}, {})
    assert locks.lock_mismatch(tmp_path) == (
        "frontend/package-lock.json is missing, run npm install in frontend"
    )


def test_a_changed_markdownlint_without_npm_install_is_named(tmp_path):
    agreeing_repo(tmp_path)
    package = tmp_path / MARKDOWNLINT / "package.json"
    package.write_text(
        json.dumps({"name": "x", "devDependencies": {"markdownlint-cli2": "0.25.0"}})
    )
    assert locks.lock_mismatch(tmp_path) == (
        "tools/markdownlint/package-lock.json is out of date: its devDependencies differ from "
        "package.json, run npm install in tools/markdownlint"
    )
