"""The triggers and tags of .github/workflows/image.yml: a release comes only from release.yml,
and scripts/prune-tags must recognize the short commit tag."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml
from devlib import load_script

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "image.yml").read_text())
RELEASE = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
## YAML 1.1 reads the key on as true
TRIGGERS = WORKFLOW[True]
IMAGE = "docker.io/tnek213/bwtidy"
STEPS = {step.get("name"): step for step in WORKFLOW["jobs"]["publish"]["steps"]}
META = STEPS["Work out the tags and labels"]["run"]
REVISION = "c3894f34c1d2c1b1629cd6b99aa0b23bfadb119b"
## what git rev-parse --short=7 prints when another object of the checkout starts with c3894f3
AMBIGUOUS = "c3894f34"
FAKE_GIT = f"""#!/bin/sh
case "$*" in
  "rev-parse HEAD") echo {REVISION} ;;
  "rev-parse --short"*) echo {AMBIGUOUS} ;;
  "log -1 "*) echo "261127 {AMBIGUOUS}" ;;
  *) echo "unexpected git $*" >&2; exit 1 ;;
esac
"""


def run_meta(
    tmp_path: Path, event: str, ref: str, version: str = "", check: bool = True
) -> dict[str, str]:
    """Runs the step against a git whose short hash of HEAD is 8 characters long, returns its
    outputs, with version as the input of a call from release.yml."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("git", FAKE_GIT), ("node", "#!/bin/sh\necho 1.0.0-beta.1\n")):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    output = tmp_path / "output"
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        "EVENT_NAME": event,
        "REF": ref,
        "RELEASE_VERSION": version,
    }
    subprocess.run(["bash", "-c", META], cwd=tmp_path, env=env, check=check, capture_output=True)
    if not output.exists():
        return {}
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


def test_a_release_comes_only_from_the_call_of_release_yml():
    assert set(TRIGGERS) == {"push", "workflow_call", "workflow_dispatch"}
    assert TRIGGERS["workflow_call"]["inputs"]["version"]["required"] is True
    assert TRIGGERS["workflow_call"]["inputs"]["version"]["type"] == "string"
    env = STEPS["Work out the tags and labels"]["env"]
    assert env["RELEASE_VERSION"] == "${{ inputs.version }}"
    assert "release.tag_name" not in str(WORKFLOW)


def test_release_yml_calls_it_with_the_version_and_the_secrets():
    job = RELEASE["jobs"]["image"]
    assert job["uses"] == "./.github/workflows/image.yml"
    assert job["with"] == {"version": "${{ needs.release.outputs.version }}"}
    assert job["secrets"] == "inherit"
    assert job["needs"] == "release"
    assert job["permissions"] == WORKFLOW["jobs"]["publish"]["permissions"] | {"contents": "read"}


def test_a_call_with_the_version_tags_it_and_latest(tmp_path):
    outputs = run_meta(tmp_path, "push", "refs/tags/v1.0.0-beta.1", "1.0.0-beta.1")
    assert outputs["tags"].split(",") == [f"{IMAGE}:1.0.0-beta.1", f"{IMAGE}:latest"]
    assert outputs["version"] == "1.0.0-beta.1"


@pytest.mark.parametrize(
    ("ref", "version"),
    [("refs/tags/v1.0.0-beta.2", "1.0.0-beta.2"), ("refs/tags/v1.0.0-beta.2", "1.0.0-beta.1")],
)
def test_a_call_with_another_version_than_package_json_fails(tmp_path, ref, version):
    assert run_meta(tmp_path, "push", ref, version, check=False).get("tags") is None


def test_release_yml_is_ignored_by_a_push_to_main():
    ignored = TRIGGERS["push"]["paths-ignore"]
    assert ".github/workflows/release.yml" in ignored
    assert "scripts/release" in ignored


def test_the_short_commit_tag_has_exactly_7_characters_when_git_would_print_more(tmp_path):
    outputs = run_meta(tmp_path, "push", "refs/heads/main")
    assert outputs["tags"].split(",") == [f"{IMAGE}:main", f"{IMAGE}:{REVISION[:7]}"]
    assert outputs["revision"] == REVISION


def test_prune_tags_treats_the_short_commit_tag_as_a_short_commit(tmp_path):
    tag = run_meta(tmp_path, "push", "refs/heads/main")["tags"].split(",")[1].split(":")[1]
    prune = load_script("prune-tags")
    assert prune.SHORT.fullmatch(tag)
    assert tag == REVISION[:7]


def test_the_short_commit_is_cut_from_the_full_hash_not_abbreviated_by_git():
    assert "--short" not in META
    assert 'short="${revision::7}"' in META
