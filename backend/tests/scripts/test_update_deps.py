import json
import os
import subprocess
import sys

import pytest

OLD = "sha256:" + "1" * 64
NEW = "sha256:" + "2" * 64
SHA = "a" * 40
SHA2 = "b" * 40

DOCKERFILE = f"""# syntax=docker/dockerfile:1@{OLD}

FROM ghcr.io/astral-sh/uv:0.11@{OLD} AS uv

FROM node:24-trixie-slim AS node

FROM node AS frontend
FROM --platform=linux/amd64 python:3.13-slim-trixie@{OLD} AS python
"""

WORKFLOW = f"""jobs:
  ci:
    steps:
      # v7
      - uses: actions/checkout@{SHA}
      # setup-uv publishes no major version tag, only full versions
      # v10.3.3
      - uses: astral-sh/setup-uv@{SHA}
      - uses: actions/setup-node@v7
      - id: deployment
        # v5
        uses: actions/deploy-pages@{SHA}
      # v1.30.0
      - uses: docker/scout-action@{SHA}
      # v4.39.5
      - uses: github/codeql-action/upload-sarif@{SHA}
"""


@pytest.fixture(scope="module")
def deps(load_script):
    return load_script("update-deps")


def test_image_refs_are_the_syntax_line_and_every_from_with_a_tag(deps):
    refs = deps.image_refs(DOCKERFILE)
    assert [(r.image, r.tag, r.digest) for r in refs] == [
        ("docker/dockerfile", "1", OLD),
        ("ghcr.io/astral-sh/uv", "0.11", OLD),
        ("node", "24-trixie-slim", None),
        ("python", "3.13-slim-trixie", OLD),
    ]


def test_a_new_digest_keeps_the_tag_and_the_rest_of_the_line(deps):
    refs = deps.image_refs(DOCKERFILE)
    text = DOCKERFILE
    for ref in refs:
        text = deps.with_digest(text, ref, NEW)
    assert text.splitlines()[0] == f"# syntax=docker/dockerfile:1@{NEW}"
    assert f"FROM ghcr.io/astral-sh/uv:0.11@{NEW} AS uv" in text
    assert f"FROM node:24-trixie-slim@{NEW} AS node" in text
    assert "FROM node AS frontend" in text
    assert f"FROM --platform=linux/amd64 python:3.13-slim-trixie@{NEW} AS python" in text
    assert OLD not in text


@pytest.mark.parametrize(
    ("image", "where"),
    [
        ("node", ("registry-1.docker.io", "library/node")),
        ("docker/dockerfile", ("registry-1.docker.io", "docker/dockerfile")),
        ("ghcr.io/astral-sh/uv", ("ghcr.io", "astral-sh/uv")),
        ("localhost:5000/app", ("localhost:5000", "app")),
    ],
)
def test_registry_of_an_image(deps, image, where):
    assert deps.registry_of(image) == where


def test_newer_tags_have_the_same_form_and_a_higher_version(deps):
    tags = [
        "3.12-slim-trixie",
        "3.13-slim-trixie",
        "3.14-slim-trixie",
        "3.14-alpine",
        "3.14.1-slim-trixie",
        "3.15-slim-trixie",
        "latest",
    ]
    assert deps.newer_tags("3.13-slim-trixie", tags) == [
        "3.14-slim-trixie",
        "3.15-slim-trixie",
    ]
    assert deps.newer_tags("0.11", ["0.9", "0.10", "0.11", "0.12", "0.13-alpine", "1.0"]) == [
        "0.12",
        "1.0",
    ]
    assert deps.newer_tags("latest", tags) == []


def test_action_pins_need_a_sha_and_the_tag_in_the_comment_above(deps):
    pins = deps.action_pins(WORKFLOW)
    assert [(p.action, p.tag, p.sha) for p in pins] == [
        ("actions/checkout", "v7", SHA),
        ("astral-sh/setup-uv", "v10.3.3", SHA),
        ("actions/deploy-pages", "v5", SHA),
        ("docker/scout-action", "v1.30.0", SHA),
        ("github/codeql-action/upload-sarif", "v4.39.5", SHA),
    ]


def test_the_tags_of_an_action_in_a_subdirectory_come_from_its_repository(deps, monkeypatch):
    asked = []
    listing = f"{SHA}\trefs/tags/v4.39.5\n{SHA2}\trefs/tags/v4.39.5^{{}}\n"
    monkeypatch.setattr(deps, "run", lambda command, cwd: asked.append(command) or listing)
    assert deps.remote_tags("github/codeql-action/upload-sarif") == {"v4.39.5": SHA2}
    assert asked[0][-1] == "https://github.com/github/codeql-action"


def test_a_new_action_tag_rewrites_the_sha_and_the_comment_only(deps):
    pin = deps.action_pins(WORKFLOW)[2]
    text = deps.with_action(WORKFLOW, pin, "v5.0.1", SHA2)
    lines = text.splitlines()
    assert lines[pin.line] == f"        uses: actions/deploy-pages@{SHA2}"
    assert lines[pin.line - 1] == "        # v5.0.1"
    assert text.count(SHA2) == 1
    assert "      # v7" in lines


def test_newest_in_major_prefers_a_full_version_and_stays_in_the_major(deps):
    tags = ["v6.9.0", "v7", "v7.0.0", "v7.1", "v7.1.0", "v7.0.9", "v8.0.0", "v8", "nightly"]
    assert deps.newest_in_major("v7", tags) == "v7.1.0"
    assert deps.newest_in_major("v7", ["v7", "v7.1", "v7.0.9"]) == "v7.1"
    assert deps.newest_in_major("v10.3.3", ["v10.3.3", "v10.4.0", "v11.0.0"]) == "v10.4.0"
    assert deps.newest_in_major("v5", ["v5"]) == "v5"
    assert deps.newer_majors("v7", tags) == ["v8.0.0"]
    assert deps.newer_majors("v8", tags) == []


def test_changes_of_two_lock_listings(deps):
    before = {"a": "1.0", "b": "2.0", "gone": "1"}
    after = {"a": "1.1", "b": "2.0", "new": "3"}
    assert deps.changes(before, after) == [
        ("a", "1.0", "1.1"),
        ("gone", "1", None),
        ("new", None, "3"),
    ]


def test_lock_listings_leave_out_the_project_itself(deps):
    uv_lock = """version = 1

[[package]]
name = "bwtidy"
version = "0.1.0a1"
source = { editable = "." }

[[package]]
name = "anyio"
version = "4.15.1"
"""
    assert deps.uv_versions(uv_lock) == {"anyio": "4.15.1"}
    npm_lock = {
        "packages": {
            "": {"name": "bwtidy-frontend", "version": "0.1.0-alpha.1"},
            "node_modules/vite": {"version": "8.3.5"},
            "node_modules/knip/node_modules/yaml": {"version": "2.9.1"},
        }
    }
    assert deps.npm_versions(json.dumps(npm_lock)) == {
        "vite": "8.3.5",
        "knip/node_modules/yaml": "2.9.1",
    }


def test_uv_dry_run_and_outdated_output(deps):
    dry_run = """Resolved 62 packages in 249ms
Update anyio v4.15.1 -> v4.16.0
Remove pyee v14.0.0
Add newdep v1.0.0
"""
    assert deps.uv_dry_run_changes(dry_run) == [
        ("anyio", "4.15.1", "4.16.0"),
        ("newdep", None, "1.0.0"),
        ("pyee", "14.0.0", None),
    ]
    tree = """bwtidy v0.1.0a1
├── fastapi v0.162.0 (latest: v0.165.0)
├── mcp v2.3.0
└── ty v0.0.92 (group: dev) (latest: v0.0.109)
"""
    assert deps.uv_outdated(tree) == [
        ("fastapi", "0.162.0", "0.165.0"),
        ("ty", "0.0.92", "0.0.109"),
    ]


def test_uv_outdated_reads_a_line_of_many_groups_without_latest_at_once():
    ## in a child process: a regex that backtracks holds the GIL, so only a kill stops it
    code = (
        "from devlib import load_script\n"
        "line = 'x v1' + ' (a) (' * 40\n"
        "assert load_script('update-deps').uv_outdated(line) == []\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    done = subprocess.run([sys.executable, "-c", code], env=env, timeout=10, check=False)
    assert done.returncode == 0


def test_npm_outside_lists_a_latest_beyond_the_range(deps):
    outdated = {
        "vite": {"current": "8.3.5", "wanted": "8.3.8", "latest": "8.3.8"},
        "typescript": {"current": "6.0.3", "wanted": "6.0.3", "latest": "7.0.2"},
        "@bitwarden/cli": {"wanted": "2026.10.0", "latest": "2026.11.1"},
    }
    assert deps.npm_outside(outdated) == [
        ("@bitwarden/cli", "2026.10.0", "2026.11.1"),
        ("typescript", "6.0.3", "7.0.2"),
    ]


def test_the_repo_pins_every_image_by_digest_and_every_action_by_sha(deps):
    refs = deps.image_refs(deps.DOCKERFILE.read_text())
    assert refs
    assert [f"{r.image}:{r.tag}" for r in refs if r.digest is None] == []
    for workflow in deps.WORKFLOWS.glob("*.yml"):
        text = workflow.read_text()
        uses = [
            line
            for line in text.splitlines()
            if line.strip().removeprefix("- ").startswith("uses:")
        ]
        assert len(deps.action_pins(text)) == len(uses), workflow.name


def test_the_demo_is_a_uv_project_beside_the_backend(deps):
    assert deps.DEMO == deps.ROOT / "demo"
    assert (deps.DEMO / "uv.lock").is_file()
    assert (deps.DEMO / "pyproject.toml").is_file()


def test_update_uv_upgrades_the_lock_of_the_project_it_is_given(deps, monkeypatch, tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text('version = 1\n\n[[package]]\nname = "anyio"\nversion = "4.15.1"\n')
    calls = []

    def fake_run(command, cwd, check=True):
        calls.append((command, cwd))
        if "--dry-run" in command:
            return "Update anyio v4.15.1 -> v4.16.0\n"
        lock.write_text('version = 1\n\n[[package]]\nname = "anyio"\nversion = "4.16.0"\n')
        return ""

    monkeypatch.setattr(deps, "run", fake_run)
    assert deps.update_uv(tmp_path, dry_run=True) == [("anyio", "4.15.1", "4.16.0")]
    assert "4.15.1" in lock.read_text()
    assert deps.update_uv(tmp_path, dry_run=False) == [("anyio", "4.15.1", "4.16.0")]
    assert calls == [
        (["uv", "lock", "--upgrade", "--dry-run"], tmp_path),
        (["uv", "lock", "--upgrade"], tmp_path),
    ]
