import socket
import subprocess
from pathlib import Path

import pytest
import yaml
from devlib import compose, git, snapshot, stacks
from devlib.paths import ROOT
from devlib.stacks import Stack


@pytest.fixture(scope="module")
def dev_vault(load_script):
    return load_script("dev-vault")


def test_free_ports_are_distinct_and_bindable():
    ports = stacks.free_ports(2)
    assert len(ports) == 2
    assert ports[0] != ports[1]
    for port in ports:
        assert 1024 <= port <= 65535
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))


def test_project_name_is_per_worktree_and_compose_safe():
    assert stacks.project_name(Path("/x/bwtidy-worktrees/1108")) == "bwtidy-1108"
    assert stacks.project_name(Path("/x/My Tree.v2")) == "bwtidy-my-tree-v2"
    assert stacks.project_name(Path("/x/...")) == "bwtidy-root"


def test_write_env_round_trips(tmp_path):
    path = tmp_path / ".dev-vault.env"
    env = stacks.write_env(path, "bwtidy-1108")
    assert set(env) == {
        "BWTIDY_VW_PORT",
        "BWTIDY_APP_PORT",
        "COMPOSE_PROJECT_NAME",
        "BWTIDY_TLS_DIR",
    }
    assert env["COMPOSE_PROJECT_NAME"] == "bwtidy-1108"
    assert env["BWTIDY_TLS_DIR"] == ".dev-vault"
    assert env["BWTIDY_VW_PORT"] != env["BWTIDY_APP_PORT"]
    assert stacks.read_env(path) == env
    assert path.read_text().endswith("\n")


def init_repo(path):
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_COMMITTER_DATE": "2026-10-28T12:00:00+00:00",
        "GIT_AUTHOR_DATE": "2026-10-28T12:00:00+00:00",
    }
    for cmd in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", *cmd], cwd=path, check=True, env={**env, "PATH": "/usr/bin:/bin"})
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=path, check=True, capture_output=True
    )
    return head.stdout.decode().strip()


def test_commit_is_the_committer_date_and_the_short_hash(tmp_path):
    short = init_repo(tmp_path)
    assert git.commit("dev-vault", tmp_path) == f"261028 {short}"


def test_commit_marks_a_dirty_worktree(tmp_path):
    short = init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("x")
    assert git.commit("dev-vault", tmp_path) == f"261028 {short}-dirty"


def test_stack_one_keeps_the_names_of_a_single_stack():
    root = Path("/x/bwtidy-worktrees/1401")
    assert stacks.env_file(root, 1) == root / ".dev-vault.env"
    assert stacks.tls_dir(root, 1) == root / ".dev-vault"
    assert stacks.project_name(root, 1) == "bwtidy-1401"


def test_stack_k_has_its_own_env_file_directory_and_project():
    root = Path("/x/bwtidy-worktrees/1401")
    assert stacks.env_file(root, 2) == root / ".dev-vault-2.env"
    assert stacks.tls_dir(root, 3) == root / ".dev-vault-3"
    assert stacks.project_name(root, 2) == "bwtidy-1401-2"
    assert stacks.project_name(root, 12) == "bwtidy-1401-12"


def test_write_env_names_the_stack_directory(tmp_path):
    env = stacks.write_env(tmp_path / ".dev-vault-2.env", "bwtidy-1401-2", tls=".dev-vault-2")
    assert env["BWTIDY_TLS_DIR"] == ".dev-vault-2"
    assert stacks.read_env(tmp_path / ".dev-vault-2.env")["BWTIDY_TLS_DIR"] == ".dev-vault-2"


def test_the_stack_comes_from_the_environment():
    assert stacks.stack_number({}) == 1
    assert stacks.stack_number({"BWTIDY_STACK": ""}) == 1
    assert stacks.stack_number({"BWTIDY_STACK": "1"}) == 1
    assert stacks.stack_number({"BWTIDY_STACK": "3"}) == 3
    for bad in ("0", "-2", "two", "2.0"):
        with pytest.raises(SystemExit):
            stacks.stack_number({"BWTIDY_STACK": bad})


def test_compose_mounts_the_certificate_of_the_stack_directory():
    compose = (ROOT / "compose.dev.yaml").read_text()
    assert "./.dev-vault/" not in compose
    assert compose.count("./${BWTIDY_TLS_DIR:-.dev-vault}/cert.pem:/tls/cert.pem:ro") == 2
    assert "./${BWTIDY_TLS_DIR:-.dev-vault}/key.pem:/tls/key.pem:ro" in compose


def test_stacks_present_reads_the_env_files(tmp_path):
    for name in (".dev-vault.env", ".dev-vault-2.env", ".dev-vault-10.env"):
        (tmp_path / name).write_text("")
    ## not a stack: a stack 1 under a second name, a stack 0, another file
    for name in (".dev-vault-1.env", ".dev-vault-0.env", ".dev-vault-x.env", ".dev-vault.env.bak"):
        (tmp_path / name).write_text("")
    assert stacks.stacks_present(tmp_path) == [1, 2, 10]


@pytest.fixture
def worktree(tmp_path, monkeypatch):
    """A worktree with stacks 1, 2 and 3 up, and the compose commands recorded."""
    calls = []
    monkeypatch.setattr(compose, "run", lambda cmd, **kwargs: calls.append(cmd))
    for stack in (1, 2, 3):
        stacks.env_file(tmp_path, stack).write_text(f"COMPOSE_PROJECT_NAME=p{stack}\n")
        stacks.tls_dir(tmp_path, stack).mkdir()
    return calls


def env_files_of(calls):
    return [Path(cmd[cmd.index("--env-file") + 1]).name for cmd in calls]


def test_down_without_a_stack_stops_every_stack_of_the_worktree(worktree, tmp_path, dev_vault):
    ## a directory left by a stack whose env file is gone
    (tmp_path / ".dev-vault-4").mkdir()
    assert dev_vault.down(tmp_path, None) == 0
    assert env_files_of(worktree) == [".dev-vault.env", ".dev-vault-2.env", ".dev-vault-3.env"]
    assert all(cmd[-4:] == ["down", "-v", "--rmi", "local"] for cmd in worktree)
    assert not list(tmp_path.glob(".dev-vault*"))


def test_down_with_a_stack_stops_only_that_one(worktree, tmp_path, dev_vault):
    assert dev_vault.down(tmp_path, 2) == 0
    assert env_files_of(worktree) == [".dev-vault-2.env"]
    assert sorted(p.name for p in tmp_path.glob(".dev-vault*")) == [
        ".dev-vault",
        ".dev-vault-3",
        ".dev-vault-3.env",
        ".dev-vault.env",
    ]


def test_reset_without_a_stack_resets_every_stack_that_is_up(
    worktree, tmp_path, monkeypatch, dev_vault
):
    runs = []
    monkeypatch.setattr(
        stacks, "run_stacks", lambda commands, numbers: runs.append((commands, numbers))
    )
    assert dev_vault.reset(tmp_path, None) == 0
    assert runs == [([["reset"]], [1, 2, 3])]
    assert worktree == []


def test_reset_with_a_stack_resets_only_that_one(worktree, tmp_path, monkeypatch, dev_vault):
    monkeypatch.setattr(stacks, "run_stacks", lambda *args: pytest.fail("ran other stacks"))
    steps = []
    monkeypatch.setattr(dev_vault, "up", lambda stack: steps.append(("up", stack)))
    monkeypatch.setattr(dev_vault, "setup", lambda stack: steps.append(("setup", stack)) or 0)
    assert dev_vault.reset(tmp_path, 2) == 0
    assert env_files_of(worktree) == [".dev-vault-2.env"]
    assert steps == [("up", Stack(2, tmp_path)), ("setup", Stack(2, tmp_path))]


class FakeStacks:
    """Runs no dev-vault, records which stack ran what and in which round."""

    def __init__(self, failing=()):
        self.started = []
        self.failing = failing

    def popen(self, cmd, env, **kwargs):
        stack = int(env["BWTIDY_STACK"])
        self.started.append((stack, cmd[1:]))
        code = 1 if stack in self.failing else 0
        lines = [f"{' '.join(cmd[1:])} output\n"]
        return type("P", (), {"stdout": iter(lines), "wait": lambda self: code})()


@pytest.mark.parametrize("usable", [True, False])
def test_run_stacks_starts_stack_one_first_without_a_snapshot(monkeypatch, capsys, usable):
    fake = FakeStacks()
    monkeypatch.setattr(stacks.subprocess, "Popen", fake.popen)
    monkeypatch.setattr(snapshot, "snapshot_usable", lambda image: usable)
    stacks.run_stacks([["up"], ["setup"]], [1, 2, 3])
    assert sorted(fake.started) == sorted(
        (stack, [step]) for stack in (1, 2, 3) for step in ("up", "setup")
    )
    if not usable:
        assert fake.started[:2] == [(1, ["up"]), (1, ["setup"])]
    out = capsys.readouterr().out
    assert "stack 2: setup output\n" in out


def test_run_stacks_names_the_failed_stacks(monkeypatch):
    fake = FakeStacks(failing=(1,))
    monkeypatch.setattr(stacks.subprocess, "Popen", fake.popen)
    monkeypatch.setattr(snapshot, "snapshot_usable", lambda image: False)
    with pytest.raises(SystemExit) as raised:
        stacks.run_stacks([["reset"]], [1, 2])
    assert "reset failed on stack 1" in str(raised.value.code)
    ## the others would start without the snapshot stack 1 was to save
    assert fake.started == [(1, ["reset"])]


def test_run_stacks_runs_one_stack_in_the_foreground(monkeypatch):
    calls = []
    monkeypatch.setattr(
        stacks.subprocess, "run", lambda cmd, env, check: calls.append((cmd[1:], env))
    )
    stacks.run_stacks([["reset"]], [2])
    assert [(cmd, env["BWTIDY_STACK"]) for cmd, env in calls] == [(["reset"], "2")]


@pytest.mark.parametrize(
    ("available", "cores", "expected"),
    [
        (10_000, 12, 3),
        (1536 + 2 * 1800, 12, 2),
        (2000, 12, 1),
        (500, 12, 1),
        (10_000, 4, 2),
        (10_000, 1, 1),
    ],
)
def test_default_stacks_leave_ram_available(available, cores, expected):
    assert stacks.default_stacks(available, cores) == expected


def test_a_stack_from_two_up_never_saves_a_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "save_snapshot", lambda stack, image: pytest.fail("saved"))
    monkeypatch.setattr(snapshot, "snapshot_usable", lambda image: False)
    snapshot.keep_snapshot(Stack(2, tmp_path), seeded=True)


def test_a_stack_from_two_up_fails_without_a_snapshot_nobody_builds(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "snapshot_usable", lambda image: False)
    with pytest.raises(SystemExit) as raised:
        snapshot.wait_for_snapshot(Stack(2, tmp_path), "bwtidy-dev-vaultwarden:abc")
    assert "starts only from the snapshot" in str(raised.value.code)


def test_a_stack_from_two_up_waits_while_stack_one_builds_the_snapshot(tmp_path, monkeypatch):
    ## stack 1 runs from a bare Vaultwarden, its setup saves the snapshot
    stacks.write_env(tmp_path / ".dev-vault.env", "bwtidy-x")
    checks = iter([False, False, False, True])
    monkeypatch.setattr(snapshot, "snapshot_usable", lambda image: next(checks))
    sleeps = []
    monkeypatch.setattr(snapshot.time, "sleep", sleeps.append)
    snapshot.wait_for_snapshot(Stack(2, tmp_path), "bwtidy-dev-vaultwarden:abc")
    assert len(sleeps) == 2


def test_the_cli_picks_the_stack_from_the_environment(monkeypatch, dev_vault):
    picked = []
    monkeypatch.setattr(dev_vault, "down", lambda root, number: picked.append(number) or 0)
    monkeypatch.setattr(dev_vault, "up", lambda stack: picked.append(stack) or 0)
    for environ in ({}, {"BWTIDY_STACK": ""}, {"BWTIDY_STACK": "2"}):
        assert dev_vault.main(["down"], environ) == 0
    assert dev_vault.main(["up"], {"BWTIDY_STACK": "3"}) == 0
    assert picked == [None, None, 2, Stack(3, ROOT)]
    with pytest.raises(SystemExit):
        dev_vault.main(["up"], {"BWTIDY_STACK": "two"})


@pytest.mark.parametrize(
    ("value", "arg"), [("https://kroki.bwtidy.example", "https://kroki.bwtidy.example"), (None, "")]
)
def test_up_builds_the_image_with_the_kroki_server_of_the_docs(
    worktree, tmp_path, monkeypatch, dev_vault, value, arg
):
    """So the image's docs stage renders the diagrams on the server scripts/docs-site uses."""
    if value is None:
        monkeypatch.delenv("BWTIDY_KROKI_URL", raising=False)
    else:
        monkeypatch.setenv("BWTIDY_KROKI_URL", value)
    monkeypatch.setattr(git, "commit", lambda name, root: "261111 7db8830")
    monkeypatch.setattr(compose, "make_cert", lambda stack: None)
    monkeypatch.setattr(compose, "vault_url", lambda env: "https://localhost:1")
    monkeypatch.setattr(compose, "wait_alive", lambda url, cert: None)
    stacks.env_file(tmp_path, 1).write_text("COMPOSE_PROJECT_NAME=p1\nBWTIDY_APP_PORT=1\n")
    assert dev_vault.up(Stack(1, tmp_path)) == 0
    [build] = [cmd for cmd in worktree if cmd[:2] == ["docker", "build"]]
    assert f"BWTIDY_KROKI_URL={arg}" in build
    assert build[build.index(f"BWTIDY_KROKI_URL={arg}") - 1] == "--build-arg"


@pytest.mark.parametrize(
    ("name", "service"), [("compose.dev.yaml", "app"), ("compose.build.yaml", "bwtidy")]
)
def test_the_compose_builds_pass_the_kroki_server_on(name, service):
    build = yaml.safe_load((ROOT / name).read_text())["services"][service]["build"]
    assert build["args"]["BWTIDY_KROKI_URL"] == "${BWTIDY_KROKI_URL:-}"
    docs_stage = (ROOT / "Dockerfile").read_text().split(" AS docs\n")[1].split("\nFROM ")[0]
    assert 'ARG BWTIDY_KROKI_URL=""' in docs_stage
