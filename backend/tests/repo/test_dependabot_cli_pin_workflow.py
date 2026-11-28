"""The safety of .github/workflows/dependabot-cli-pin.yml, which writes to Dependabot's pull
requests with a token that can write (dev/release.md, The CLI pin of Dependabot's pull requests).

It must never run anything from the pull request: main's script runs on the pull request's files.
"""

import base64
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from devlib import load_script

ROOT = Path(__file__).resolve().parents[3]
TEXT = (ROOT / ".github" / "workflows" / "dependabot-cli-pin.yml").read_text()
WORKFLOW = yaml.safe_load(TEXT)
## YAML 1.1 reads the key on as true
TRIGGERS = WORKFLOW[True]
JOB = WORKFLOW["jobs"]["pin"]
STEPS = JOB["steps"]
NAMED = {step.get("name"): step for step in STEPS}
PREFIX = "dependabot/npm_and_yarn/docker/bitwarden-cli/"
REPO = "tnek213/bwtidy"
SHA = "a" * 40
## each way a step may use the pull request's checkout (head): read it as data or have main's
## script write it, never run a file of it
HEAD_USES = [
    r'jq -r \'\.dependencies\["@bitwarden/cli"\]\' head/docker/bitwarden-cli/package\.json',
    r'python3 main/scripts/update-deps --cli-pin --root head --pr "\$NUMBER"',
    r"git -C head (status|diff) [^|\n]*",
    r'base64 -w0 "head/\$path"',
]


def test_it_runs_only_for_dependabots_cli_pull_requests_or_by_hand_on_main():
    assert set(TRIGGERS) == {"pull_request_target", "workflow_dispatch"}
    assert TRIGGERS["pull_request_target"] == {
        "types": ["opened", "synchronize", "reopened"],
        "branches": ["main"],
    }
    condition = " ".join(JOB["if"].split())
    assert condition == (
        "(github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main') || "
        "(github.event.pull_request.user.login == 'dependabot[bot]' && "
        "github.event.pull_request.head.repo.full_name == github.repository && "
        f"startsWith(github.event.pull_request.head.ref, '{PREFIX}'))"
    )
    assert STEPS[0] is NAMED["Check the pull request"]


def test_its_token_can_only_commit_and_read_pull_requests():
    assert WORKFLOW["permissions"] == {"contents": "write", "pull-requests": "read"}
    assert "permissions" not in JOB
    assert "secrets." not in TEXT


def test_no_step_runs_anything_from_the_pull_request():
    checkouts = [step for step in STEPS if step.get("uses", "").startswith("actions/checkout@")]
    assert [step["with"]["path"] for step in checkouts] == ["main", "head"]
    assert checkouts[0]["with"]["ref"] == "main"
    assert checkouts[1]["with"]["ref"] == "${{ steps.pr.outputs.sha }}"
    for step in checkouts:
        assert step["with"]["persist-credentials"] is False
    for step in STEPS:
        assert "working-directory" not in step
        assert not step.get("uses", "").startswith("./")
        script = step.get("run", "")
        for use in HEAD_USES:
            script = re.sub(use, "", script)
        ## .head.ref and the like are fields of the pull request in the API's answer
        assert not re.search(r"(?<![.\w])head\b", script), step.get("name")


def fake_bin(tmp_path: Path, gh: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(f"#!/bin/sh\n{gh}\n")
    (bin_dir / "gh").chmod(0o755)
    output = tmp_path / "output"
    output.touch()
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_REPOSITORY": REPO,
        "RUNNER_TEMP": str(tmp_path),
        "HOME": str(tmp_path),
    }


def run_step(name: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", NAMED[name]["run"]],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def check_pull_request(tmp_path: Path, fields: list[str]) -> subprocess.CompletedProcess:
    listing = "\t".join(fields)
    env = fake_bin(tmp_path, f"printf '%s\\n' '{listing}'")
    return run_step("Check the pull request", tmp_path, {**env, "NUMBER": "108"})


PULL_REQUEST = ["dependabot[bot]", REPO, f"{PREFIX}bitwarden-cli-minor-and-patch", SHA, "main"]


def test_the_check_passes_dependabots_cli_pull_request(tmp_path):
    answer = check_pull_request(tmp_path, [*PULL_REQUEST, "open"])
    assert answer.returncode == 0, answer.stderr
    outputs = (tmp_path / "output").read_text().splitlines()
    assert outputs == [f"ref={PREFIX}bitwarden-cli-minor-and-patch", f"sha={SHA}"]


@pytest.mark.parametrize(
    ("index", "value"),
    [
        (0, "someone"),
        (1, "someone/bwtidy"),
        (1, ""),
        (2, "dependabot/npm_and_yarn/frontend/vite-8.0.0"),
        (2, f"{PREFIX}x;touch y"),
        (3, "abc"),
        (4, "feature"),
        (5, "closed"),
    ],
)
def test_the_check_refuses_any_other_pull_request(tmp_path, index, value):
    fields = [*PULL_REQUEST, "open"]
    fields[index] = value
    answer = check_pull_request(tmp_path, fields)
    assert answer.returncode == 1
    assert "not an open Dependabot bump of the Bitwarden CLI" in answer.stderr
    assert (tmp_path / "output").read_text() == ""


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", "-C", str(cwd), *args], env=env, capture_output=True, text=True, check=True
    ).stdout


def commit_step(tmp_path: Path) -> tuple[subprocess.CompletedProcess, Path]:
    request = tmp_path / "request.json"
    gh = f'cp "$4" {request}\necho https://github.com/{REPO}/commit/{"b" * 40}'
    env = fake_bin(tmp_path, gh)
    env.update(REF=f"{PREFIX}x", SHA=SHA, VERSION="2026.11.0")
    return run_step("Commit the CLI pin", tmp_path, env), request


@pytest.fixture
def head(tmp_path):
    head = tmp_path / "head"
    (head / "docs").mkdir(parents=True)
    (head / "docs" / "restore.md").write_text("@bitwarden/cli@2026.10.0\n")
    (head / "README.md").write_text("`cli-v2026.10.0`\n")
    git(head, "init", "-q")
    git(head, "add", ".")
    git(head, "-c", "user.name=t", "-c", "user.email=t@bwtidy.example", "commit", "-qm", "x")
    return head


def test_the_commit_sends_every_changed_file_to_the_api(tmp_path, head):
    (head / "docs" / "restore.md").write_text("@bitwarden/cli@2026.11.0\n")
    (head / "README.md").write_text("`cli-v2026.11.0`\n")
    answer, request = commit_step(tmp_path)
    assert answer.returncode == 0, answer.stderr
    sent = json.loads(request.read_text())
    assert "createCommitOnBranch" in sent["query"]
    assert sent["variables"]["input"] == {
        "branch": {"repositoryNameWithOwner": REPO, "branchName": f"{PREFIX}x"},
        "expectedHeadOid": SHA,
        "message": {"headline": "Pin the Bitwarden CLI source to cli-v2026.11.0"},
        "fileChanges": {
            "additions": [
                {"path": "README.md", "contents": base64.b64encode(b"`cli-v2026.11.0`\n").decode()},
                {
                    "path": "docs/restore.md",
                    "contents": base64.b64encode(b"@bitwarden/cli@2026.11.0\n").decode(),
                },
            ]
        },
    }


def test_the_commit_step_stops_when_nothing_changed(tmp_path, head):
    answer, request = commit_step(tmp_path)
    assert answer.returncode == 0, answer.stderr
    assert "pins the CLI source already" in answer.stdout
    assert not request.exists()


def test_the_commit_step_refuses_a_new_file(tmp_path, head):
    (head / "docs" / "restore.md").write_text("@bitwarden/cli@2026.11.0\n")
    (head / "new.md").write_text("x\n")
    answer, request = commit_step(tmp_path)
    assert answer.returncode == 1
    assert "new.md" in answer.stderr
    assert not request.exists()


## --- the bullet of the release notes (scripts/update-deps --cli-pin --pr N)

NOTES_REPO = "https://github.com/tnek213/bwtidy"
HEAD_NOTES = (
    "# Release notes\n\nIntro.\n\n## Unreleased\n\n"
    "Changes on main that are not yet in a versioned Docker image.\n\n"
)
OLDER = f"## 1.0.0-beta.1\n\n- Start ([#1]({NOTES_REPO}/pull/1))\n"
BULLET = f"- Run the Bitwarden CLI 2026.11.0 in the image\n  ([#108]({NOTES_REPO}/pull/108))\n"


@pytest.fixture(scope="module")
def deps():
    return load_script("update-deps")


def test_the_workflow_passes_the_number_of_the_pull_request():
    step = NAMED["Write the CLI pin"]
    assert step["env"]["NUMBER"] == "${{ github.event.pull_request.number || inputs.pull_request }}"
    assert 'update-deps --cli-pin --root head --pr "$NUMBER"' in step["run"]


def test_the_bullet_replaces_nothing_yet(deps):
    text = HEAD_NOTES + "Nothing yet.\n\n" + OLDER
    assert deps.with_cli_note(text, "2026.11.0", 108) == HEAD_NOTES + BULLET + "\n" + OLDER


def test_the_bullet_comes_after_the_other_bullets(deps):
    other = f"- Sort the grid\n  ([#90]({NOTES_REPO}/pull/90))\n"
    text = HEAD_NOTES + other + "\n" + OLDER
    assert deps.with_cli_note(text, "2026.11.0", 108) == HEAD_NOTES + other + BULLET + "\n" + OLDER


def test_the_bullet_is_written_once(deps):
    text = deps.with_cli_note(HEAD_NOTES + "Nothing yet.\n\n" + OLDER, "2026.11.0", 108)
    assert deps.with_cli_note(text, "2026.11.0", 108) is None
    ## another version in the same pull request takes the place of the bullet
    newer = deps.with_cli_note(text, "2026.11.1", 108)
    assert newer == text.replace("2026.11.0", "2026.11.1")
    assert newer.count("Run the Bitwarden CLI") == 1


def test_the_bullet_goes_only_into_unreleased(deps):
    text = HEAD_NOTES + "Nothing yet.\n\n" + OLDER.replace("- Start", "- Start\n\nNothing yet.")
    written = deps.with_cli_note(text, "2026.11.0", 108)
    assert written.index("Run the Bitwarden CLI") < written.index("## 1.0.0-beta.1")
    assert written.count("Nothing yet.") == 1


def cli_head(tmp_path: Path) -> Path:
    """The files --cli-pin reads, already pinned to 2026.11.0, with the release notes."""
    cli = tmp_path / "docker" / "bitwarden-cli"
    cli.mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    package = {"dependencies": {"@bitwarden/cli": "2026.11.0"}}
    (cli / "package.json").write_text(json.dumps(package))
    source = {"repository": "https://github.com/bitwarden/clients", "tag": "cli-v2026.11.0"}
    (cli / "source.json").write_text(json.dumps({**source, "commit": SHA}))
    (tmp_path / "README.md").write_text("at the tag `cli-v2026.11.0`\n")
    (tmp_path / "docker" / "hub-overview.md").write_text("at the tag `cli-v2026.11.0`\n")
    (tmp_path / "docs" / "restore.md").write_text("npm install -g @bitwarden/cli@2026.11.0\n")
    (tmp_path / "docs" / "releases.md").write_text(HEAD_NOTES + "Nothing yet.\n\n" + OLDER)
    return tmp_path


def test_the_cli_pin_with_pr_writes_the_bullet_once(deps, tmp_path, monkeypatch):
    root = cli_head(tmp_path)
    monkeypatch.setattr(deps, "run", lambda command, cwd: f"{SHA}\trefs/tags/cli-v2026.11.0\n")
    assert deps.main(["--cli-pin", "--root", str(root), "--pr", "108"]) == 0
    notes = (root / "docs" / "releases.md").read_text()
    assert notes == HEAD_NOTES + BULLET + "\n" + OLDER
    assert deps.main(["--cli-pin", "--root", str(root), "--pr", "108"]) == 0
    assert (root / "docs" / "releases.md").read_text() == notes
    ## without --pr it writes no bullet, as it has no pull request to link
    (root / "docs" / "releases.md").write_text(HEAD_NOTES + "Nothing yet.\n\n" + OLDER)
    assert deps.main(["--cli-pin", "--root", str(root)]) == 0
    assert "Nothing yet." in (root / "docs" / "releases.md").read_text()


@pytest.mark.parametrize("number", ["", "0", "108; touch x", "-1", "1e3"])
def test_the_number_of_the_pull_request_is_digits_only(deps, tmp_path, number):
    root = cli_head(tmp_path)
    assert deps.main(["--cli-pin", "--root", str(root), "--pr", number]) == 2
    assert "Nothing yet." in (root / "docs" / "releases.md").read_text()


def test_the_cli_pin_writes_no_release_notes_through_a_link(deps, tmp_path, tmp_path_factory):
    root = cli_head(tmp_path)
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "releases.md"
    elsewhere.write_text("x\n")
    (root / "docs" / "releases.md").unlink()
    (root / "docs" / "releases.md").symlink_to(elsewhere)
    errors: list[str] = []
    assert deps.update_cli_pin(root, False, errors, 108) == []
    assert "docs/releases.md" in errors[0]
    assert elsewhere.read_text() == "x\n"
