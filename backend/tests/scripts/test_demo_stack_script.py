import subprocess

import pytest


@pytest.fixture
def script(load_script, monkeypatch):
    module = load_script("demo-stack")
    calls = []
    monkeypatch.setattr(
        module.demo, "up", lambda root, build=True: calls.append(("up", build)) or 81
    )
    monkeypatch.setattr(module.demo, "down", lambda root: calls.append(("down",)))
    monkeypatch.setattr(module.demo, "running_port", lambda root: 81)

    def run(command, **kwargs):
        calls.append(("run", command, kwargs))
        return subprocess.CompletedProcess(command, 3)

    monkeypatch.setattr(module.subprocess, "run", run)
    module.calls = calls
    return module


def test_help_prints_the_usage_and_starts_nothing(script, capsys):
    assert script.main(["--help"]) == 0
    assert "scripts/demo-stack up" in capsys.readouterr().out
    assert script.main([]) == 2
    assert script.main(["sideways"]) == 2
    assert script.main(["down", "now"]) == 2
    assert "scripts/demo-stack up" in capsys.readouterr().err
    assert script.calls == []


def test_up_prints_the_address(script, capsys):
    assert script.main(["up"]) == 0
    assert script.main(["up", "--no-build"]) == 0
    assert capsys.readouterr().out == "http://127.0.0.1:81\nhttp://127.0.0.1:81\n"
    assert script.calls == [("up", True), ("up", False)]


def test_test_runs_the_stack_tests_of_the_demo_project_against_the_stack(script):
    assert script.main(["test", "-k", "edge", "-x"]) == 3
    assert script.calls[0] == ("up", True)
    _, command, kwargs = script.calls[1]
    assert command[:5] == ["uv", "run", "--locked", "--quiet", "pytest"]
    assert command[5:] == ["tests/stack", "-k", "edge", "-x"]
    assert kwargs["cwd"] == script.ROOT / "demo"
    env = kwargs["env"]
    assert env["DEMO_STACK"] == "1"
    assert env["DEMO_STACK_URL"] == "http://127.0.0.1:81"
    assert env["DEMO_STACK_PROJECT"] == script.demo.project_name(script.ROOT)


def test_test_builds_the_demo_on_the_normal_image_built_from_the_worktree(load_script, monkeypatch):
    module = load_script("demo-stack")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs.get("env")))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.demo, "pick_port", lambda root: 82)
    monkeypatch.setattr(module.demo.git, "commit", lambda name, root: "261128 abc1234")
    assert module.main(["test"]) == 0
    base = module.demo.base_image(module.ROOT)
    build_base, build_demo, start, tests = (command for command, _ in calls)
    assert build_base[:4] == ["docker", "build", "--target", "app"]
    assert build_base[-3:] == ["--tag", base, str(module.ROOT)]
    assert f"BWTIDY_IMAGE={base}" in build_demo
    assert build_demo[-1] == str(module.ROOT)
    assert "docker.io/tnek213/bwtidy" not in " ".join(build_base + build_demo)
    assert start == module.demo.compose(module.ROOT, "up", "--detach", "--wait", "--no-build")
    assert calls[2][1]["BWTIDY_IMAGE"] == base
    assert tests[4:6] == ["pytest", "tests/stack"]


def test_url_needs_a_running_stack(script, capsys, monkeypatch):
    assert script.main(["url"]) == 0
    assert capsys.readouterr().out == "http://127.0.0.1:81\n"
    monkeypatch.setattr(script.demo, "running_port", lambda root: None)
    assert script.main(["url"]) == 1
    assert "not up" in capsys.readouterr().err


def test_compose_passes_its_arguments_and_its_exit_status_on(script):
    assert script.main(["compose", "logs", "demo"]) == 3
    _, command, kwargs = script.calls[0]
    assert command == script.demo.compose(script.ROOT, "logs", "demo")
    assert kwargs["env"]["DEMO_PORT"] == "81"
    assert script.main(["compose"]) == 2


def test_down_takes_the_stack_down(script):
    assert script.main(["down"]) == 0
    assert script.calls == [("down",)]


def test_a_failed_docker_command_is_named_without_a_traceback(script, capsys, monkeypatch):
    def fail(root, build=True):
        raise subprocess.CalledProcessError(17, ["docker", "build", "--target", "app"])

    monkeypatch.setattr(script.demo, "up", fail)
    assert script.main(["up"]) == 17
    assert capsys.readouterr().err == "demo-stack: docker build failed\n"
