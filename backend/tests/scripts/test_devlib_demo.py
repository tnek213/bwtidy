import os
import re
import subprocess
from pathlib import Path

import pytest
from devlib import demo

ROOT = Path("/work/My Tree.1")
PROJECT = "bwtidy-demo-my-tree-1"


def test_the_project_and_the_images_are_the_worktrees_own():
    assert demo.project_name(ROOT) == PROJECT
    assert demo.base_image(ROOT) == f"{PROJECT}-base:local"
    assert demo.demo_image(ROOT) == f"{PROJECT}:local"
    assert demo.project_name(Path("/work/---")) == "bwtidy-demo-root"
    assert demo.project_name(Path("/a/tree")) != demo.project_name(Path("/b/other"))


def test_compose_names_the_project_the_file_and_no_env_file_of_the_worktree():
    assert demo.compose(ROOT, "ps", "-q") == [
        "docker",
        "compose",
        "-p",
        PROJECT,
        "-f",
        str(ROOT / "compose.demo.yaml"),
        "--env-file",
        os.devnull,
        "ps",
        "-q",
    ]


def test_the_environment_sets_the_stacks_own_values_and_passes_the_settings_on():
    caller = {
        "PATH": "/bin",
        "DOCKER_CONFIG": "/tmp/config",
        "DEMO_MAX": "1",
        "DEMO_START_RATE": "1r/s",
        "BWTIDY_IMAGE": "docker.io/someone/else:main",
        "DEMO_IMAGE": "other:tag",
        "DEMO_BIND": "0.0.0.0",
        "DEMO_PORT": "80",
        "DEMO_BUILD_CONTEXT": "https://example.test/repo.git",
        "DEMO_PUBLIC_BRIDGE": "br-bwtidy-demo",
    }
    assert demo.environment(ROOT, 4321, caller) == {
        "PATH": "/bin",
        "DOCKER_CONFIG": "/tmp/config",
        "DEMO_MAX": "1",
        "DEMO_START_RATE": "1r/s",
        "BWTIDY_IMAGE": f"{PROJECT}-base:local",
        "DEMO_IMAGE": f"{PROJECT}:local",
        "DEMO_BIND": "127.0.0.1",
        "DEMO_PORT": "4321",
        "DEMO_PUBLIC_BRIDGE": demo.public_bridge(ROOT),
    }


def test_the_public_bridge_is_the_worktrees_own_and_a_valid_interface_name():
    bridge = demo.public_bridge(ROOT)
    assert re.fullmatch(r"bwd-[0-9a-f]{10}", bridge)
    assert bridge == demo.public_bridge(Path("/elsewhere/My Tree.1"))
    assert bridge != demo.public_bridge(Path("/work/other"))


@pytest.mark.parametrize(
    ("output", "port"),
    [("127.0.0.1:8770\n", 8770), ("0.0.0.0:49153", 49153), ("", None), ("no port\n", None)],
)
def test_published_port_reads_what_compose_prints(output, port):
    assert demo.published_port(output) == port


def test_a_second_up_keeps_the_running_port(monkeypatch):
    monkeypatch.setattr(demo, "running_port", lambda root: 5555)
    assert demo.pick_port(ROOT) == 5555
    monkeypatch.setattr(demo, "running_port", lambda root: None)
    monkeypatch.setattr(demo.stacks, "free_ports", lambda count: [6666])
    assert demo.pick_port(ROOT) == 6666
    assert demo.url(6666) == "http://127.0.0.1:6666"


def test_the_demo_image_is_built_on_the_normal_image_of_the_worktree():
    base, on_top = demo.build_commands(ROOT, "261126 abc1234")
    assert base == [
        "docker",
        "build",
        "--target",
        "app",
        "--build-arg",
        "BWTIDY_COMMIT=261126 abc1234",
        "--tag",
        f"{PROJECT}-base:local",
        str(ROOT),
    ]
    assert on_top == [
        "docker",
        "build",
        "--file",
        str(ROOT / "demo/Dockerfile"),
        "--build-arg",
        f"BWTIDY_IMAGE={PROJECT}-base:local",
        "--tag",
        f"{PROJECT}:local",
        str(ROOT),
    ]


@pytest.fixture
def ran(monkeypatch):
    """Every command up and down run, with the environment each got."""
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs.get("env")))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(demo.subprocess, "run", run)
    monkeypatch.setattr(demo, "pick_port", lambda root: 7777)
    monkeypatch.setattr(demo.git, "commit", lambda name, root: "261126 abc1234")
    return calls


def test_up_builds_both_images_then_starts_the_stack_and_waits(ran):
    assert demo.up(ROOT) == 7777
    commands = [command for command, _ in ran]
    assert commands[:2] == demo.build_commands(ROOT, "261126 abc1234")
    assert commands[2] == demo.compose(ROOT, "up", "--detach", "--wait", "--no-build")
    assert len(commands) == 3
    for _, env in ran:
        assert env["DEMO_PORT"] == "7777"
        assert env["BWTIDY_IMAGE"] == f"{PROJECT}-base:local"


def test_up_without_a_build_only_starts_the_stack(ran):
    assert demo.up(ROOT, build=False) == 7777
    assert [command for command, _ in ran] == [
        demo.compose(ROOT, "up", "--detach", "--wait", "--no-build")
    ]


def test_down_removes_the_volumes_and_the_two_images(ran):
    demo.down(ROOT)
    assert [command for command, _ in ran] == [
        demo.compose(ROOT, "down", "--volumes", "--remove-orphans"),
        ["docker", "image", "rm", "--force", f"{PROJECT}:local", f"{PROJECT}-base:local"],
    ]
