import re
import shutil
import time
from pathlib import Path

import pytest
from devlib import compose, git, seed, snapshot, stacks
from devlib.paths import ROOT
from devlib.stacks import Stack


@pytest.fixture(scope="module")
def dev_vault(load_script):
    return load_script("dev-vault")


REPO = ROOT


@pytest.fixture
def inputs(tmp_path):
    """A copy of the snapshot's inputs in a root of their own."""
    for path in snapshot.snapshot_inputs(REPO):
        dest = tmp_path / path.relative_to(REPO)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    return tmp_path


def test_inputs_are_the_seeding_code_the_stack_and_the_fixtures_and_attachments():
    names = {p.relative_to(REPO).as_posix() for p in snapshot.snapshot_inputs(REPO)}
    assert {
        "scripts/dev-vault",
        "scripts/devlib/compose.py",
        "scripts/devlib/paths.py",
        "scripts/devlib/seed.py",
        "scripts/devlib/snapshot.py",
        "scripts/devlib/stacks.py",
        "compose.dev.yaml",
        "Dockerfile",
        "docker/unref-share-timers.js",
        "docker/bitwarden-cli/package.json",
        "docker/bitwarden-cli/package-lock.json",
    } <= names
    assert "dev/fixture-personal.json" in names
    assert "dev/fixture-org.json" in names
    assert "dev/fixture-org-club.json" in names
    for file_name in seed.ATTACHMENTS:
        assert f"dev/attachments/{file_name}" in names


def test_same_inputs_give_the_same_hash(inputs):
    assert snapshot.snapshot_hash(inputs) == snapshot.snapshot_hash(REPO)
    assert snapshot.snapshot_hash(inputs) == snapshot.snapshot_hash(inputs)


@pytest.mark.parametrize(
    "name",
    [p.relative_to(REPO).as_posix() for p in snapshot.snapshot_inputs(REPO)],
)
def test_a_change_in_any_input_changes_the_hash(inputs, name):
    before = snapshot.snapshot_hash(inputs)
    path = inputs / name
    path.write_bytes(path.read_bytes() + b"\n")
    assert snapshot.snapshot_hash(inputs) != before


def test_a_guide_under_dev_is_not_an_input(inputs):
    before = snapshot.snapshot_hash(inputs)
    guide = inputs / "dev" / "README.md"
    guide.parent.mkdir(parents=True, exist_ok=True)
    guide.write_text("a guide")
    assert snapshot.snapshot_hash(inputs) == before
    guide.write_text("an edited guide")
    assert snapshot.snapshot_hash(inputs) == before
    names = {p.relative_to(REPO).as_posix() for p in snapshot.snapshot_inputs(REPO)}
    assert not {n for n in names if n.startswith("dev/") and n.endswith(".md")}


def test_a_new_or_renamed_file_under_dev_changes_the_hash(inputs):
    before = snapshot.snapshot_hash(inputs)
    (inputs / "dev" / "attachments" / "new.txt").write_text("x")
    added = snapshot.snapshot_hash(inputs)
    assert added != before
    (inputs / "dev" / "attachments" / "new.txt").rename(inputs / "dev" / "attachments" / "z.txt")
    assert snapshot.snapshot_hash(inputs) not in (before, added)


def test_the_image_is_named_after_the_hash(inputs):
    image = snapshot.snapshot_image(inputs)
    assert image == f"bwtidy-dev-vaultwarden:{snapshot.snapshot_hash(inputs)}"


def test_compose_takes_vaultwarden_from_the_snapshot_variable():
    compose = (REPO / "compose.dev.yaml").read_text()
    assert "image: ${BWTIDY_VW_IMAGE:-vaultwarden/server:" in compose


class FakeDocker:
    """Records the commands the script runs, docker and compose alike, and runs none of them."""

    def __init__(self):
        ## each image's age in seconds
        self.images = {}
        self.calls = []
        self.dockerfile = None
        self.restored = []

    def run(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[:2] == ["docker", "cp"]:
            Path(cmd[3]).mkdir()
        ## the snapshot's build, the app image's is a build of the repo
        if cmd[:2] == ["docker", "build"] and "-q" in cmd:
            self.dockerfile = (Path(cmd[-1]) / "Dockerfile").read_text()
            self.images[cmd[cmd.index("-t") + 1]] = 0
        stdout = ""
        if "inspect" in cmd:
            stdout = "vaultwarden/server:1.37.4\n"
        elif "ps" in cmd:
            stdout = f"{cmd[-1]}-id\n"
        return type("Done", (), {"stdout": stdout, "returncode": 0})()

    def commands(self):
        """Each call shortened to its verb: docker build, compose up, and so on."""
        result = []
        for cmd in self.calls:
            if cmd[:2] == ["docker", "compose"]:
                result.append(" ".join(["compose", *cmd[6:8]]))
            else:
                result.append(" ".join(cmd[:2]))
        return result


@pytest.fixture
def stack(tmp_path):
    """Stack 1 of a worktree in tmp_path."""
    return Stack(1, tmp_path)


@pytest.fixture
def docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(compose, "run", fake.run)
    monkeypatch.setattr(snapshot, "snapshot_age", fake.images.get)
    monkeypatch.setattr(compose, "make_cert", lambda stack: None)
    monkeypatch.setattr(compose, "wait_alive", lambda url, cert: None)
    monkeypatch.setattr(git, "commit", lambda name, repo: "261107 abc1234")
    monkeypatch.setattr(snapshot, "restore_cli_state", lambda stack: fake.restored.append(True))
    return fake


def test_up_starts_from_the_snapshot_when_its_image_exists(docker, stack, dev_vault):
    image = snapshot.snapshot_image()
    docker.images[image] = 60
    dev_vault.up(stack)
    env = stack.env()
    assert env["BWTIDY_VW_IMAGE"] == image
    assert docker.restored == [True]
    assert docker.commands() == ["docker build", "compose up -d"]


def test_up_starts_a_bare_vaultwarden_without_a_snapshot(docker, stack, dev_vault):
    docker.images["bwtidy-dev-vaultwarden:0000000000000000"] = 60
    dev_vault.up(stack)
    env = stack.env()
    assert "BWTIDY_VW_IMAGE" not in env
    assert docker.restored == []
    assert docker.commands() == ["docker build", "compose up -d"]


def test_up_keeps_the_image_of_a_running_stack(docker, stack, dev_vault):
    stacks.write_env(stack.env_file, "bwtidy-x")
    ## a snapshot saved since, the stack keeps running what it started with
    docker.images[snapshot.snapshot_image()] = 60
    dev_vault.up(stack)
    assert "BWTIDY_VW_IMAGE" not in stack.env()
    assert docker.restored == []


def test_write_env_names_the_snapshot_image(tmp_path):
    path = tmp_path / ".dev-vault.env"
    env = stacks.write_env(path, "bwtidy-1108", "bwtidy-dev-vaultwarden:abc")
    assert env["BWTIDY_VW_IMAGE"] == "bwtidy-dev-vaultwarden:abc"
    assert stacks.read_env(path) == env


def test_a_seeded_stack_is_saved_when_no_snapshot_exists(docker, stack):
    stacks.write_env(stack.env_file, "bwtidy-x")
    image = snapshot.snapshot_image()
    snapshot.keep_snapshot(stack, seeded=True)
    assert image in docker.images
    assert docker.commands() == [
        "compose ps -a",
        "compose ps -a",
        "docker inspect",
        "compose stop vaultwarden",
        "docker cp",
        "docker cp",
        "docker build",
        "compose start vaultwarden",
    ]
    cps = [c for c in docker.calls if c[:2] == ["docker", "cp"]]
    assert cps[0][2] == "vaultwarden-id:/data"
    assert cps[1][2] == "app-id:/data/bw"
    assert docker.dockerfile is not None
    assert docker.dockerfile.startswith("FROM vaultwarden/server:1.37.4\n")
    [created] = re.findall(rf"{snapshot.SNAPSHOT_CREATED}=(\d+)", docker.dockerfile)
    assert abs(int(created) - time.time()) < 60
    assert "COPY data/ /data/\n" in docker.dockerfile
    assert f"COPY --chown=1000:1000 bw/ {snapshot.SNAPSHOT_BW_DIR}/\n" in docker.dockerfile


def test_vaultwarden_starts_again_when_the_build_fails(docker, stack, monkeypatch):
    stacks.write_env(stack.env_file, "bwtidy-x")
    fake_run = docker.run

    def failing_build(cmd, **kwargs):
        if cmd[:2] == ["docker", "build"]:
            docker.calls.append(cmd)
            raise RuntimeError("build failed")
        return fake_run(cmd, **kwargs)

    monkeypatch.setattr(compose, "run", failing_build)
    with pytest.raises(RuntimeError):
        snapshot.keep_snapshot(stack, seeded=True)
    assert docker.commands()[-2:] == ["docker build", "compose start vaultwarden"]


def test_up_starts_a_bare_vaultwarden_when_the_snapshot_is_too_old(docker, stack, dev_vault):
    docker.images[snapshot.snapshot_image()] = snapshot.SNAPSHOT_MAX_AGE + 1
    dev_vault.up(stack)
    assert "BWTIDY_VW_IMAGE" not in stack.env()
    assert docker.restored == []


def test_a_too_old_snapshot_is_built_again(docker, stack):
    stacks.write_env(stack.env_file, "bwtidy-x")
    image = snapshot.snapshot_image()
    docker.images[image] = snapshot.SNAPSHOT_MAX_AGE + 1
    snapshot.keep_snapshot(stack, seeded=True)
    assert docker.images[image] == 0


def test_the_age_comes_from_the_label(monkeypatch):
    answers = {}

    def inspect(cmd, **kwargs):
        assert cmd[:3] == ["docker", "image", "inspect"]
        assert snapshot.SNAPSHOT_CREATED in cmd[-2]
        out, code = answers[cmd[-1]]
        return type("Done", (), {"stdout": out, "returncode": code})()

    monkeypatch.setattr(snapshot.subprocess, "run", inspect)
    answers["saved"] = (f"{int(time.time()) - 100}\n", 0)
    answers["unlabeled"] = ("<no value>\n", 0)
    answers["missing"] = ("", 1)
    age = snapshot.snapshot_age("saved")
    assert age is not None and 99 <= age < 160
    assert snapshot.snapshot_usable("saved")
    assert snapshot.snapshot_age("unlabeled") == float("inf")
    assert not snapshot.snapshot_usable("unlabeled")
    assert snapshot.snapshot_age("missing") is None
    assert not snapshot.snapshot_usable("missing")


def test_an_existing_snapshot_is_not_built_again(docker, stack):
    docker.images[snapshot.snapshot_image()] = 60
    snapshot.keep_snapshot(stack, seeded=True)
    assert docker.calls == []


def test_a_stack_setup_did_not_seed_is_never_saved(docker, stack):
    ## its vault may hold what tests wrote to it
    snapshot.keep_snapshot(stack, seeded=False)
    assert docker.calls == []


def status(state, server="https://vaultwarden", email="dev@bwtidy.test"):
    return {"status": state, "serverUrl": server, "userEmail": email}


@pytest.mark.parametrize(
    ("env", "bw_status", "expected"),
    [
        ({"BWTIDY_VW_IMAGE": "bwtidy-dev-vaultwarden:abc"}, status("locked"), True),
        ({}, status("locked"), False),
        ({"BWTIDY_VW_IMAGE": "bwtidy-dev-vaultwarden:abc"}, status("unauthenticated"), False),
        ({"BWTIDY_VW_IMAGE": "bwtidy-dev-vaultwarden:abc"}, status("unlocked"), False),
        (
            {"BWTIDY_VW_IMAGE": "bwtidy-dev-vaultwarden:abc"},
            status("locked", email="restore@bwtidy.test"),
            False,
        ),
        (
            {"BWTIDY_VW_IMAGE": "bwtidy-dev-vaultwarden:abc"},
            status("locked", server="https://vault.bitwarden.com"),
            False,
        ),
    ],
)
def test_setup_only_checks_a_stack_from_the_snapshot(monkeypatch, env, bw_status, expected):
    monkeypatch.setattr(seed, "bw_json", lambda *args, **kwargs: bw_status)
    assert seed.from_snapshot_in_place(Stack(1), env) is expected
