import json
import re
from pathlib import Path

from devlib.paths import ROOT, load_script

CLI = ROOT / "docker" / "bitwarden-cli"
REPOSITORY = "https://github.com/bitwarden/clients"
COMMIT = "b449ac1d255784380d75ef026343198891286be7"


def source_mismatch(cli: Path) -> str | None:
    """What keeps source.json in CLI from naming the source of the version package.json pins.

    The image builds the CLI from that source, so a bump of the npm package alone fails here
    until scripts/update-deps (or a hand edit) moves the tag and the commit along.
    """
    source = json.loads((cli / "source.json").read_text())
    version = json.loads((cli / "package.json").read_text())["dependencies"]["@bitwarden/cli"]
    if source.get("repository") != REPOSITORY:
        return f"source.json names the repository {source.get('repository')!r}, not {REPOSITORY}"
    if source.get("tag") != f"cli-v{version}":
        return (
            f"source.json names the tag {source.get('tag')!r} but package.json pins "
            f"@bitwarden/cli {version}, run scripts/update-deps --cli-pin to set the tag "
            f"cli-v{version} and its commit"
        )
    if not re.fullmatch(r"[0-9a-f]{40}", str(source.get("commit"))):
        return f"source.json names the commit {source.get('commit')!r}, not 40 hex characters"
    return None


def test_every_doc_that_states_the_cli_version_states_the_pinned_one():
    """scripts/update-deps --cli-pin writes them, and a bump that leaves one behind fails here."""
    deps = load_script("update-deps")
    errors: list[str] = []
    version = deps.pinned_cli_version(CLI, errors)
    assert version is not None
    assert deps.update_cli_docs(ROOT, version, True, errors) == []
    assert errors == []


def write_cli(root: Path, version: str, source: dict) -> Path:
    package = {"name": "x", "private": True, "dependencies": {"@bitwarden/cli": version}}
    (root / "package.json").write_text(json.dumps(package))
    (root / "source.json").write_text(json.dumps(source))
    return root


def test_the_source_of_the_repo_matches_the_cli_version():
    assert source_mismatch(CLI) is None


def test_a_matching_source_passes(tmp_path):
    source = {"repository": REPOSITORY, "tag": "cli-v2026.10.0", "commit": COMMIT}
    assert source_mismatch(write_cli(tmp_path, "2026.10.0", source)) is None


def test_a_tag_of_another_version_fails_naming_both(tmp_path):
    source = {"repository": REPOSITORY, "tag": "cli-v2026.10.0", "commit": COMMIT}
    mismatch = source_mismatch(write_cli(tmp_path, "2026.11.0", source))
    assert mismatch is not None
    assert "'cli-v2026.10.0'" in mismatch
    assert "@bitwarden/cli 2026.11.0" in mismatch


def test_a_commit_that_is_no_full_sha_fails(tmp_path):
    source = {"repository": REPOSITORY, "tag": "cli-v2026.10.0", "commit": COMMIT[:7]}
    mismatch = source_mismatch(write_cli(tmp_path, "2026.10.0", source))
    assert mismatch is not None
    assert "40 hex characters" in mismatch


def test_another_repository_fails(tmp_path):
    source = {"repository": f"{REPOSITORY}-fork", "tag": "cli-v2026.10.0", "commit": COMMIT}
    mismatch = source_mismatch(write_cli(tmp_path, "2026.10.0", source))
    assert mismatch is not None
    assert "-fork" in mismatch
