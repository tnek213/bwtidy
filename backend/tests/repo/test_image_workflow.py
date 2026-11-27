"""The tags of .github/workflows/image.yml, which scripts/prune-tags must recognize."""

import os
import subprocess
from pathlib import Path

import yaml
from devlib import load_script

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "image.yml").read_text())
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


def run_meta(tmp_path: Path, event: str, ref: str) -> dict[str, str]:
    """Runs the step against a git whose short hash of HEAD is 8 characters long, returns its
    outputs."""
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
        "RELEASE_TAG": "",
    }
    subprocess.run(["bash", "-c", META], cwd=tmp_path, env=env, check=True)
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


def test_the_short_commit_tag_has_exactly_7_characters_when_git_would_print_more(tmp_path):
    outputs = run_meta(tmp_path, "push", "refs/heads/main")
    image = "docker.io/tnek213/bwtidy"
    assert outputs["tags"].split(",") == [f"{image}:main", f"{image}:{REVISION[:7]}"]
    assert outputs["revision"] == REVISION


def test_prune_tags_treats_the_short_commit_tag_as_a_short_commit(tmp_path):
    tag = run_meta(tmp_path, "push", "refs/heads/main")["tags"].split(",")[1].split(":")[1]
    prune = load_script("prune-tags")
    assert prune.SHORT.fullmatch(tag)
    assert tag == REVISION[:7]


def test_the_short_commit_is_cut_from_the_full_hash_not_abbreviated_by_git():
    assert "--short" not in META
    assert 'short="${revision::7}"' in META
