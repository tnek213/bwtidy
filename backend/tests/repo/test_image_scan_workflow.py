"""What .github/workflows/image-scan.yml scans: the newest release (latest) alone, every day."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "image-scan.yml").read_text())
## YAML 1.1 reads the key on as true
TRIGGERS = WORKFLOW[True]
STEPS = WORKFLOW["jobs"]["scan"]["steps"]


def test_it_scans_every_day_and_never_after_a_push_to_main():
    assert set(TRIGGERS) == {"schedule", "pull_request", "workflow_dispatch"}
    assert TRIGGERS["schedule"] == [{"cron": "17 4 * * *"}]


def test_it_scans_latest_alone():
    assert "strategy" not in WORKFLOW["jobs"]["scan"]
    scout = next(step for step in STEPS if "scout-action" in step.get("uses", ""))
    assert scout["with"]["image"] == "registry://docker.io/tnek213/bwtidy:latest"
    upload = next(step for step in STEPS if "upload-sarif" in step.get("uses", ""))
    assert upload["with"]["sarif_file"] == scout["with"]["sarif-file"]
    assert upload["with"]["category"] == "image-latest"
