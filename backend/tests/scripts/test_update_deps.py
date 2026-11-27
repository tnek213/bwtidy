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

DEMO_DOCKERFILE = f"""# syntax=docker/dockerfile:1@{OLD}

ARG BWTIDY_IMAGE=docker.io/tnek213/bwtidy:main

FROM ghcr.io/astral-sh/uv:0.11@{OLD} AS uv

FROM ${{BWTIDY_IMAGE}}
"""

COMPOSE = f"""x-demo-image: &demo-image
  image: ${{DEMO_IMAGE:-bwtidy-demo:local}}

services:
  bwtidy:
    image: ${{BWTIDY_IMAGE:?set it}}:${{BWTIDY_VERSION:-latest}}
  vaultwarden:
    image: vaultwarden/server:1.37.4@{OLD}
  edge:
    ## an image line in a comment is no image: nginx:1.0
    image: nginx:1.30-alpine
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


def test_image_refs_of_a_compose_file_are_its_image_lines_without_a_variable(deps):
    refs = deps.image_refs(COMPOSE)
    assert [(r.image, r.tag, r.digest) for r in refs] == [
        ("vaultwarden/server", "1.37.4", OLD),
        ("nginx", "1.30-alpine", None),
    ]
    text = COMPOSE
    for ref in refs:
        text = deps.with_digest(text, ref, NEW)
    assert f"    image: vaultwarden/server:1.37.4@{NEW}\n" in text
    assert f"    image: nginx:1.30-alpine@{NEW}\n" in text
    assert "  image: ${DEMO_IMAGE:-bwtidy-demo:local}\n" in text
    assert "image: ${BWTIDY_IMAGE:?set it}:${BWTIDY_VERSION:-latest}\n" in text
    assert OLD not in text


def test_a_from_of_a_build_argument_is_no_image_ref(deps):
    refs = deps.image_refs(DEMO_DOCKERFILE)
    assert [(r.image, r.tag, r.digest) for r in refs] == [
        ("docker/dockerfile", "1", OLD),
        ("ghcr.io/astral-sh/uv", "0.11", OLD),
    ]


class FakeRegistry:
    """A registry where every tag points to NEW and nginx has a newer tag."""

    lookups: list[str] = []

    def __init__(self, image: str) -> None:
        self.image = image

    def digest(self, tag: str) -> str:
        if self.image == "broken/image":
            raise OSError("no answer")
        FakeRegistry.lookups.append(f"{self.image}:{tag}")
        return NEW

    def tags(self) -> list[str]:
        return ["1.30-alpine", "1.32-alpine"] if self.image == "nginx" else []


@pytest.fixture
def image_files(deps, monkeypatch, tmp_path):
    files = {
        "Dockerfile": DOCKERFILE,
        "demo.Dockerfile": DEMO_DOCKERFILE,
        "compose.demo.yaml": COMPOSE + "    image: broken/image:1\n    image: broken/image:1\n",
    }
    paths = [tmp_path / name for name in files]
    for path, text in zip(paths, files.values(), strict=True):
        path.write_text(text)
    monkeypatch.setattr(deps, "IMAGE_FILES", paths)
    monkeypatch.setattr(deps, "Registry", FakeRegistry)
    monkeypatch.setattr(FakeRegistry, "lookups", [])
    return paths


def test_update_images_pins_every_file_and_lists_a_shared_image_once(deps, image_files):
    outside: list[str] = []
    errors: list[str] = []
    found = deps.update_images(False, outside, errors)
    assert found == [
        ("docker/dockerfile:1", OLD, NEW),
        ("ghcr.io/astral-sh/uv:0.11", OLD, NEW),
        ("node:24-trixie-slim", None, NEW),
        ("python:3.13-slim-trixie", OLD, NEW),
        ("vaultwarden/server:1.37.4", OLD, NEW),
        ("nginx:1.30-alpine", None, NEW),
    ]
    assert len(FakeRegistry.lookups) == len(set(FakeRegistry.lookups)) == 6
    assert outside == ["image nginx:1.30-alpine: newer tag nginx:1.32-alpine"]
    ## an image that cannot be looked up is an error once, and its lines stay
    assert errors == ["broken/image:1: no answer"]
    for path in image_files:
        text = path.read_text()
        assert OLD not in text, path.name
        assert NEW in text, path.name
    assert image_files[2].read_text().count("image: broken/image:1\n") == 2
    assert "FROM ${BWTIDY_IMAGE}\n" in image_files[1].read_text()


def test_update_images_writes_nothing_in_a_dry_run(deps, image_files):
    before = [path.read_text() for path in image_files]
    found = deps.update_images(True, [], [])
    assert len(found) == 6
    assert [path.read_text() for path in image_files] == before


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
    assert [path.relative_to(deps.ROOT).as_posix() for path in deps.IMAGE_FILES] == [
        "Dockerfile",
        "demo/Dockerfile",
        "compose.demo.yaml",
    ]
    for path in deps.IMAGE_FILES:
        refs = deps.image_refs(path.read_text())
        assert refs, path.name
        assert [f"{r.image}:{r.tag}" for r in refs if r.digest is None] == [], path.name
    for workflow in deps.WORKFLOWS.glob("*.yml"):
        text = workflow.read_text()
        ## a workflow of this repo (uses: ./.github/workflows/...) is no action to pin
        uses = [
            line
            for line in text.splitlines()
            if line.strip().removeprefix("- ").startswith("uses:")
            and not line.strip().removeprefix("- ").startswith("uses: ./")
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


README = """The image builds it from
Bitwarden's source (<https://github.com/bitwarden/clients>) at the tag
`cli-v2026.10.0`. Bitwarden's npm package is not used.
"""
RESTORE = """These commands install the version that bwtidy uses.

```sh
npm install --prefix "$CLI" --no-fund --no-audit @bitwarden/cli@2026.10.0
```

bw 2026.10.0 refuses an SSH key item without its public key.
"""


@pytest.fixture
def cli_root(deps, tmp_path):
    """A copy of the files --cli-pin reads, with package.json at 2026.11.0 and the rest at
    2026.10.0."""
    cli = tmp_path / "docker" / "bitwarden-cli"
    cli.mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    package = {"name": "x", "private": True, "dependencies": {"@bitwarden/cli": "2026.11.0"}}
    (cli / "package.json").write_text(json.dumps(package))
    source = {
        "repository": "https://github.com/bitwarden/clients",
        "tag": "cli-v2026.10.0",
        "commit": SHA,
    }
    (cli / "source.json").write_text(json.dumps(source, indent=2) + "\n")
    (tmp_path / "README.md").write_text(README)
    (tmp_path / "docker" / "hub-overview.md").write_text(README)
    (tmp_path / "docs" / "restore.md").write_text(RESTORE)
    (tmp_path / "docs" / "releases.md").write_text("# Release notes\n\n## Unreleased\n")
    return tmp_path


def tree(root):
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def ls_remote(deps, monkeypatch, listing):
    asked = []
    monkeypatch.setattr(deps, "run", lambda command, cwd: asked.append(command) or listing)
    return asked


## an annotated tag lists its own object first and the commit it names as ^{}
ANNOTATED = f"{'c' * 40}\trefs/tags/cli-v2026.11.0\n{SHA2}\trefs/tags/cli-v2026.11.0^{{}}\n"


def test_the_cli_pin_writes_the_tag_the_commit_and_every_doc(deps, cli_root, monkeypatch):
    asked = ls_remote(deps, monkeypatch, ANNOTATED)
    errors: list[str] = []
    before = tree(cli_root)
    found = deps.update_cli_pin(cli_root, True, errors)
    assert found == [
        (
            "https://github.com/bitwarden/clients",
            f"cli-v2026.10.0 {SHA[:7]}",
            f"cli-v2026.11.0 {SHA2[:7]}",
        ),
        ("README.md", "2026.10.0", "2026.11.0"),
        ("docker/hub-overview.md", "2026.10.0", "2026.11.0"),
        ("docs/restore.md", "2026.10.0", "2026.11.0"),
    ]
    assert tree(cli_root) == before
    assert deps.update_cli_pin(cli_root, False, errors) == found
    assert errors == []
    source = json.loads((cli_root / "docker" / "bitwarden-cli" / "source.json").read_text())
    assert source == {
        "repository": "https://github.com/bitwarden/clients",
        "tag": "cli-v2026.11.0",
        "commit": SHA2,
    }
    assert (cli_root / "README.md").read_text() == README.replace("2026.10.0", "2026.11.0")
    restore = (cli_root / "docs" / "restore.md").read_text()
    assert "@bitwarden/cli@2026.11.0\n" in restore
    ## what one version did stays an observation of that version
    assert "bw 2026.10.0 refuses an SSH key item" in restore
    assert asked[0][:3] == ["git", "ls-remote", "https://github.com/bitwarden/clients"]
    assert "refs/tags/cli-v2026.11.0" in asked[0]


def test_a_second_cli_pin_changes_nothing(deps, cli_root, monkeypatch):
    ls_remote(deps, monkeypatch, ANNOTATED)
    errors: list[str] = []
    assert deps.update_cli_pin(cli_root, False, errors)
    written = tree(cli_root)
    assert deps.update_cli_pin(cli_root, False, errors) == []
    assert tree(cli_root) == written
    ## a lightweight tag names the commit itself
    ls_remote(deps, monkeypatch, f"{SHA2}\trefs/tags/cli-v2026.11.0\n")
    assert deps.update_cli_pin(cli_root, False, errors) == []
    assert errors == []


def test_a_cli_version_without_its_tag_fails_naming_it(deps, cli_root, monkeypatch):
    ls_remote(deps, monkeypatch, "")
    before = tree(cli_root)
    errors: list[str] = []
    assert deps.update_cli_pin(cli_root, False, errors) == []
    assert len(errors) == 1
    assert "no tag cli-v2026.11.0" in errors[0]
    assert "@bitwarden/cli 2026.11.0" in errors[0]
    assert tree(cli_root) == before


def test_the_cli_pin_command_fails_naming_a_missing_tag(deps, cli_root, monkeypatch, capsys):
    ls_remote(deps, monkeypatch, "")
    assert deps.main(["--cli-pin", "--root", str(cli_root)]) == 1
    assert "no tag cli-v2026.11.0" in capsys.readouterr().err
    source = json.loads((cli_root / "docker" / "bitwarden-cli" / "source.json").read_text())
    assert source["tag"] == "cli-v2026.10.0"


@pytest.mark.parametrize("version", ["^2026.11.0", "2026.11.0; touch x", "2026.11", "v2026.11.0"])
def test_a_cli_version_that_is_not_x_y_z_writes_nothing(deps, cli_root, monkeypatch, version):
    asked = ls_remote(deps, monkeypatch, ANNOTATED)
    package = {"dependencies": {"@bitwarden/cli": version}}
    (cli_root / "docker" / "bitwarden-cli" / "package.json").write_text(json.dumps(package))
    before = tree(cli_root)
    errors: list[str] = []
    assert deps.update_cli_pin(cli_root, False, errors) == []
    assert "not X.Y.Z" in errors[0]
    assert asked == []
    assert tree(cli_root) == before


def test_the_cli_pin_asks_only_bitwardens_repository(deps, cli_root, monkeypatch):
    asked = ls_remote(deps, monkeypatch, ANNOTATED)
    path = cli_root / "docker" / "bitwarden-cli" / "source.json"
    source = json.loads(path.read_text())
    path.write_text(json.dumps({**source, "repository": "ext::sh -c touch% x"}))
    before = tree(cli_root)
    errors: list[str] = []
    assert deps.update_cli_pin(cli_root, False, errors) == []
    assert "not https://github.com/bitwarden/clients" in errors[0]
    assert asked == []
    assert tree(cli_root) == before


def test_the_cli_pin_writes_through_no_link(deps, cli_root, monkeypatch, tmp_path_factory):
    ls_remote(deps, monkeypatch, ANNOTATED)
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "restore.md"
    elsewhere.write_text(RESTORE)
    (cli_root / "docs" / "restore.md").unlink()
    (cli_root / "docs" / "restore.md").symlink_to(elsewhere)
    errors: list[str] = []
    assert deps.update_cli_pin(cli_root, False, errors) == []
    assert "docs/restore.md" in errors[0]
    assert elsewhere.read_text() == RESTORE
    assert "cli-v2026.10.0" in (cli_root / "docker" / "bitwarden-cli" / "source.json").read_text()


def test_a_doc_that_no_longer_states_the_version_fails_naming_it(deps, cli_root, monkeypatch):
    ls_remote(deps, monkeypatch, ANNOTATED)
    (cli_root / "README.md").write_text("The image builds the CLI from Bitwarden's source.\n")
    errors: list[str] = []
    found = deps.update_cli_pin(cli_root, False, errors)
    assert [name for name, _, _ in found][1:] == ["docker/hub-overview.md", "docs/restore.md"]
    assert errors == ["README.md no longer states the CLI version, update CLI_PIN_DOCS"]
