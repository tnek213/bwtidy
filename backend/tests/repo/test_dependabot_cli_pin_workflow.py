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
    r"python3 main/scripts/update-deps --cli-pin --root head",
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
