"""The triggers and tags of .github/workflows/image.yml: it publishes only a release, and a
release comes only from release.yml."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "image.yml").read_text())
RELEASE = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
## YAML 1.1 reads the key on as true
TRIGGERS = WORKFLOW[True]
IMAGE = "docker.io/tnek213/bwtidy"
STEPS = {step.get("name"): step for step in WORKFLOW["jobs"]["publish"]["steps"]}
META = STEPS["Work out the tags and labels"]["run"]
REVISION = "c3894f34c1d2c1b1629cd6b99aa0b23bfadb119b"
FAKE_GIT = f"""#!/bin/sh
case "$*" in
  "rev-parse HEAD") echo {REVISION} ;;
  "log -1 "*) echo "261127 c3894f3" ;;
  *) echo "unexpected git $*" >&2; exit 1 ;;
esac
"""


def run_meta(tmp_path: Path, ref: str, version: str = "", check: bool = True) -> dict[str, str]:
    """Runs the step on ref and returns its outputs, with version as the input of a call from
    release.yml."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, text in (("git", FAKE_GIT), ("node", "#!/bin/sh\necho 1.0.0-beta.1\n")):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    output = tmp_path / "output"
    output.unlink(missing_ok=True)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        "REF": ref,
        "RELEASE_VERSION": version,
    }
    subprocess.run(["bash", "-c", META], cwd=tmp_path, env=env, check=check, capture_output=True)
    if not output.exists():
        return {}
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


def test_a_release_comes_only_from_the_call_of_release_yml():
    assert set(TRIGGERS) == {"workflow_call"}
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
    outputs = run_meta(tmp_path, "refs/tags/v1.0.0-beta.1", "1.0.0-beta.1")
    assert outputs["tags"].split(",") == [f"{IMAGE}:1.0.0-beta.1", f"{IMAGE}:latest"]
    assert outputs["version"] == "1.0.0-beta.1"
    assert outputs["revision"] == REVISION


@pytest.mark.parametrize(
    ("ref", "version"),
    [("refs/tags/v1.0.0-beta.2", "1.0.0-beta.2"), ("refs/tags/v1.0.0-beta.2", "1.0.0-beta.1")],
)
def test_a_call_with_another_version_than_package_json_fails(tmp_path, ref, version):
    assert run_meta(tmp_path, ref, version, check=False).get("tags") is None


def test_a_push_to_main_publishes_nothing(tmp_path):
    assert "push" not in TRIGGERS
    assert "paths-ignore" not in str(WORKFLOW)
    assert run_meta(tmp_path, "refs/heads/main", check=False) == {}
    assert run_meta(tmp_path, "refs/heads/main", "1.0.0-beta.1", check=False) == {}


def test_no_tag_but_the_version_and_latest_is_pushed():
    assert ":main" not in META
    assert "short" not in META
    assert '"tags=$image:$version,$image:latest"' in META


def test_a_release_image_gets_its_provenance_and_sbom():
    steps = WORKFLOW["jobs"]["publish"]["steps"]
    build = next(step for step in steps if step.get("id") == "build")
    assert build["with"]["tags"] == "${{ steps.meta.outputs.tags }}"
    assert build["with"]["push"] is True
    assert build["with"]["sbom"] is True
    assert build["with"]["provenance"] == "mode=max"
    attest = next(step for step in steps if "attest-build-provenance" in step.get("uses", ""))
    assert attest["with"]["subject-digest"] == "${{ steps.build.outputs.digest }}"
