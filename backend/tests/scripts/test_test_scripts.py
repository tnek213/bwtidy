import importlib.machinery
import importlib.util
import os
import subprocess
import time
from pathlib import Path

import pytest
from devlib import health, stacks, suites


@pytest.fixture(scope="module")
def integration(load_script):
    return load_script("integration")


@pytest.fixture(scope="module")
def e2e(load_script):
    return load_script("e2e")


@pytest.fixture(scope="module")
def docs_shots(load_script):
    return load_script("docs-shots")


@pytest.fixture
def script(request, load_script):
    """The script a test is parametrized with, by name."""
    return load_script(request.param)


both = pytest.mark.parametrize(
    ("script", "suite"),
    [("integration", "tests/integration"), ("e2e", "tests/e2e")],
    indirect=["script"],
)


@both
def test_no_arguments_keep_the_default_directory(script, suite):
    assert script.pytest_args([]) == [suite]
    assert script.pytest_args(["-x", "--lf"]) == [suite, "-x", "--lf"]


@both
def test_a_file_path_replaces_the_default_directory(script, suite):
    path = f"{suite}/test_x.py"
    assert script.pytest_args([path]) == [path]
    assert script.pytest_args(["-x", path, "-v"]) == ["-x", path, "-v"]
    assert script.pytest_args([suite]) == [suite]


@both
def test_backend_prefix_is_dropped(script, suite):
    assert script.pytest_args([f"backend/{suite}/test_x.py", "-x"]) == [f"{suite}/test_x.py", "-x"]


@both
def test_name_after_k_stays_an_argument(script, suite):
    assert script.pytest_args(["-k", "tests_seed"]) == [suite, "-k", "tests_seed"]
    assert script.pytest_args(["-k", "trash and not undo"]) == [suite, "-k", "trash and not undo"]


@both
def test_node_id_runs_one_test(script, suite):
    node = f"backend/{suite}/test_x.py::test_one[a::b]"
    assert script.pytest_args([node]) == [node.removeprefix("backend/")]


@pytest.mark.parametrize(
    ("script", "path", "runner"),
    [
        ("integration", "tests/e2e/test_accounts.py", "scripts/e2e"),
        ("integration", "backend/tests/e2e", "scripts/e2e"),
        ("e2e", "tests/integration/test_seed.py::test_x", "scripts/integration"),
        ("integration", "tests/test_x.py", "uv run --locked pytest"),
        ("e2e", "tests/integration_x.py", "uv run --locked pytest"),
    ],
    indirect=["script"],
)
def test_wrong_suite_exits_naming_the_right_script(script, path, runner):
    with pytest.raises(SystemExit) as raised:
        script.pytest_args(["-x", path])
    assert runner in str(raised.value.code)


def test_root_still_comes_first(monkeypatch, integration):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(integration.subprocess, "run", run)
    path = "tests/integration/test_seed.py"
    assert integration.pytest_args(["--root", "-k", "gateway"]) == [
        "tests/integration",
        "-k",
        "gateway",
    ]
    assert integration.main(["--root", path]) == 0
    exec_call = calls[-1]
    assert exec_call[1:3] == ["exec", "--root"]
    assert "no:cacheprovider" in exec_call[5]
    assert exec_call[6:] == ["sh", path]
    assert integration.main([path]) == 0
    assert calls[-1][1:3] == ["exec", "sh"]
    assert calls[-1][5:] == ["sh", path]


def test_no_reset_still_comes_first(e2e):
    path = "tests/e2e/test_accounts.py"
    assert e2e.pytest_args(["--no-reset", "-k", "logout"]) == ["tests/e2e", "-k", "logout"]
    assert e2e.pytest_args(["--no-reset", path]) == [path]


def test_pytest_prints_its_summary(integration, e2e):
    ## addopts in pyproject.toml already has -q, a second one drops the summary line
    assert "-q" not in e2e.PYTEST
    assert " -q " not in integration.PYTEST


def test_reexec_runs_under_systemd_inhibit(monkeypatch):
    probes = []
    execs = []

    def run(command, **kwargs):
        probes.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.delenv(suites.INHIBITED, raising=False)
    monkeypatch.setattr(suites.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(suites.subprocess, "run", run)
    monkeypatch.setattr(suites.os, "execve", lambda *args: execs.append(args))
    monkeypatch.setattr(suites.sys, "argv", ["script", "-k", "x"])
    suites.reexec_inhibited("name", "/path/script")
    lock = ["/usr/bin/systemd-inhibit", "--what=sleep:idle", "--why=bwtidy tests"]
    assert probes == [[*lock, "true"]]
    ((path, argv, env),) = execs
    assert path == lock[0]
    assert argv[:3] == lock
    assert argv[3:] == [suites.sys.executable, "/path/script", "-k", "x"]
    assert env[suites.INHIBITED] == "1"


def test_reexec_happens_once(monkeypatch):
    monkeypatch.setenv(suites.INHIBITED, "1")
    monkeypatch.setattr(suites.os, "execve", lambda *args: pytest.fail("re-executed"))
    suites.reexec_inhibited("name", "/path/script")


def test_reexec_falls_back_without_systemd_inhibit(monkeypatch, capsys):
    monkeypatch.delenv(suites.INHIBITED, raising=False)
    monkeypatch.setattr(suites.shutil, "which", lambda name: None)
    monkeypatch.setattr(suites.os, "execve", lambda *args: pytest.fail("re-executed"))
    suites.reexec_inhibited("name", "/path/script")
    assert "no systemd-inhibit" in capsys.readouterr().err


def test_reexec_falls_back_when_the_lock_is_refused(monkeypatch, capsys):
    monkeypatch.delenv(suites.INHIBITED, raising=False)
    monkeypatch.setattr(suites.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        suites.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 1)
    )
    monkeypatch.setattr(suites.os, "execve", lambda *args: pytest.fail("re-executed"))
    suites.reexec_inhibited("name", "/path/script")
    assert "refused the lock" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "options", "rest"),
    [
        (["--stacks", "3", "-x"], {"--stacks": "3"}, ["-x"]),
        (["tests/e2e/test_x.py", "--stacks=2"], {"--stacks": "2"}, ["tests/e2e/test_x.py"]),
        (
            ["-k", "x", "--no-reset", "--stacks", "4"],
            {"--no-reset": None, "--stacks": "4"},
            ["-k", "x"],
        ),
    ],
)
def test_e2e_options_go_anywhere(args, options, rest, e2e):
    assert suites.split_options(args, e2e.OPTIONS) == (options, rest)


def test_stacks_anywhere_is_never_a_pytest_argument(integration, e2e):
    assert e2e.pytest_args(["tests/e2e/test_x.py", "--stacks", "3"]) == ["tests/e2e/test_x.py"]
    assert integration.pytest_args(["--durations=30", "--stacks", "3"]) == [
        "tests/integration",
        "--durations=30",
    ]
    assert suites.split_options(["--stacks", "2", "--root", "-k", "x"], integration.OPTIONS) == (
        {"--stacks": "2", "--root": None},
        ["-k", "x"],
    )


def test_e2e_runs_a_test_path_or_k_on_one_stack(monkeypatch, e2e):
    monkeypatch.setattr(stacks, "default_stacks", lambda: 3)
    assert e2e.stack_count(None, ["tests/e2e"]) == 3
    assert e2e.stack_count(None, ["tests/e2e", "--durations=30"]) == 3
    assert e2e.stack_count(None, ["tests/e2e/test_x.py"]) == 1
    assert e2e.stack_count(None, ["tests/e2e", "-k", "logout"]) == 1
    assert e2e.stack_count(None, ["tests/e2e", "-klogout"]) == 1
    assert e2e.stack_count("2", ["tests/e2e/test_x.py", "tests/e2e/test_y.py"]) == 2
    with pytest.raises(SystemExit):
        e2e.stack_count("0", ["tests/e2e"])


def test_e2e_runs_several_stacks_with_xdist_by_file(e2e):
    assert e2e.xdist_args(1) == []
    assert e2e.xdist_args(3) == [
        "-n",
        "3",
        "--dist",
        "loadfile",
        "--no-loadscope-reorder",
        "--max-worker-restart",
        "0",
    ]
    assert e2e.stacks_for(3) == [1, 2, 3]


def test_e2e_passes_one_url_or_all_of_them(monkeypatch, e2e):
    monkeypatch.setenv(e2e.URLS_VAR, "stale")
    one = e2e.pytest_env(["http://127.0.0.1:1"])
    assert one[e2e.URL_VAR] == "http://127.0.0.1:1"
    assert e2e.URLS_VAR not in one
    monkeypatch.setenv(e2e.URL_VAR, "stale")
    several = e2e.pytest_env(["http://127.0.0.1:1", "http://127.0.0.1:2"])
    assert several[e2e.URLS_VAR] == "http://127.0.0.1:1,http://127.0.0.1:2"
    assert e2e.URL_VAR not in several


def test_e2e_logs_each_gateway_in_its_stack_directory(e2e):
    assert e2e.log_path(1) == e2e.ROOT / ".dev-vault" / "backend.log"
    assert e2e.log_path(2) == e2e.ROOT / ".dev-vault-2" / "backend.log"


def load_e2e_conftest(monkeypatch: pytest.MonkeyPatch):
    e2e_dir = Path(__file__).resolve().parents[1] / "e2e"
    ## the conftest imports the support package that sits beside it
    monkeypatch.syspath_prepend(str(e2e_dir))
    path = e2e_dir / "conftest.py"
    loader = importlib.machinery.SourceFileLoader("e2e_conftest_for_test", str(path))
    spec = importlib.util.spec_from_loader("e2e_conftest_for_test", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_each_xdist_worker_takes_its_own_stack(monkeypatch: pytest.MonkeyPatch):
    conftest = load_e2e_conftest(monkeypatch)
    urls = "http://127.0.0.1:1,http://127.0.0.1:2,http://127.0.0.1:3"
    for worker, url, stack in [("gw0", ":1", "1"), ("gw2", ":3", "3")]:
        environ = {"BWTIDY_E2E_URLS": urls, "PYTEST_XDIST_WORKER": worker}
        conftest.pick_stack(environ)
        assert environ["BWTIDY_E2E_URL"] == f"http://127.0.0.1{url}"
        assert environ["BWTIDY_STACK"] == stack
    ## without xdist the first stack
    environ = {"BWTIDY_E2E_URLS": urls}
    conftest.pick_stack(environ)
    assert environ["BWTIDY_STACK"] == "1"
    ## one stack: scripts/e2e sets the URL itself, the stack stays what it was
    environ = {"BWTIDY_E2E_URL": "http://127.0.0.1:1"}
    conftest.pick_stack(environ)
    assert environ == {"BWTIDY_E2E_URL": "http://127.0.0.1:1"}
    ## a worker xdist started in place of a crashed one has no stack
    with pytest.raises(pytest.UsageError):
        conftest.pick_stack({"BWTIDY_E2E_URLS": urls, "PYTEST_XDIST_WORKER": "gw3"})


def test_the_scheduler_starts_the_longest_file_first_and_unknown_ones_last(integration):
    files = ["test_grid.py", "test_new_b.py", "test_gateway.py", "test_new_a.py"]
    files.append("test_restore.py")
    order = [job.key for job in integration.jobs(files)]
    assert order == [
        "test_restore.py",
        "test_gateway.py --root",
        "test_grid.py",
        "test_gateway.py",
        "test_new_a.py",
        "test_new_b.py",
    ]


def test_every_suite_file_has_a_duration(integration):
    ## a new file runs last until its duration is added, which can make the run longer
    keys = {job.key for job in integration.jobs(integration.suite_files())}
    assert keys == set(integration.DURATIONS)


def test_a_root_run_goes_only_to_a_stack_after_a_plain_run(integration):
    root = integration.Job("test_gateway.py", root=True)
    plain = integration.Job("test_grid.py")
    assert integration.next_job([root, plain], had_plain_run=False) == plain
    assert integration.next_job([root, plain], had_plain_run=True) == root
    assert integration.next_job([root], had_plain_run=False) is None
    assert root.args() == ["--root", "tests/integration/test_gateway.py"]
    assert root.log_name == "test_gateway-root.log"
    assert plain.log_name == "test_grid.log"


def test_a_whole_run_on_one_stack_is_one_pytest_and_then_the_root_run(integration):
    files = integration.suite_files()
    whole, root = integration.plan(files, 1, whole=True)
    assert whole == integration.Job("tests/integration")
    assert root == integration.Job("test_gateway.py", root=True)
    assert whole.args() == [f"tests/integration/{name}" for name in files]
    assert whole.log_name == "integration.log"
    assert integration.plan(files, 3, whole=True) == integration.jobs(files)
    assert integration.plan(["test_gateway.py"], 1, whole=False) is None
    assert integration.plan(["test_gateway.py"], 2, whole=False) == integration.jobs(
        ["test_gateway.py"]
    )


def test_the_report_fails_a_one_stack_run_without_its_root_run(
    tmp_path, monkeypatch, capsys, integration
):
    todo = integration.plan(integration.suite_files(), 1, whole=True)
    monkeypatch.setattr(integration, "ROOT", tmp_path)
    monkeypatch.setattr(integration, "LOG_DIR", tmp_path / ".integration-logs")
    integration.LOG_DIR.mkdir()
    results = [integration.Result(todo[0], 1, 0, {"passed": 160}, 900.0)]
    assert integration.report(todo, results, [1], 900.0) == 1
    out = capsys.readouterr().out
    assert "integration: FAILED test_gateway.py --root, no result" in out
    assert "integration: 1 run on 1 stack, each run's log in .integration-logs/" in out


def test_the_schedule_runs_every_job_once_one_at_a_time_per_stack(integration):
    files = ["test_restore.py", "test_gateway.py", "test_grid.py", "test_seed.py"]
    todo = integration.jobs(files)
    lock = integration.threading.Lock()
    running = set()
    ran = []

    def run_job(stack, job):
        with lock:
            assert stack not in running
            running.add(stack)
            ran.append((stack, job))
        integration.time.sleep(0.01)
        with lock:
            running.remove(stack)
        return integration.Result(job, stack, 0, {"passed": 1}, 1.0)

    results = []
    integration.schedule(todo, [1, 2, 3], run_job, integration.threading.Event(), results)
    assert sorted(job.key for _, job in ran) == sorted(job.key for job in todo)
    assert len(results) == len(todo)
    for stack, job in ran:
        if job.root:
            before = ran[: ran.index((stack, job))]
            assert any(s == stack and not j.root for s, j in before)


def test_a_set_stop_starts_no_more_runs(integration):
    stop = integration.threading.Event()
    stop.set()
    results = []
    todo = integration.jobs(["test_grid.py"])
    integration.schedule(todo, [1, 2], lambda s, j: pytest.fail("ran"), stop, results)
    assert results == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("============ 4 passed in 39.60s ============", ({"passed": 4}, 39.6)),
        (
            "1 failed, 166 passed, 2 skipped, 1 error in 5591.57s (1:33:11)",
            ({"failed": 1, "passed": 166, "skipped": 2, "error": 1}, 5591.57),
        ),
        ("== 2 errors, 3 warnings in 1.00s ==", ({"error": 2, "warning": 3}, 1.0)),
        ("1 skipped in 5.12s", ({"skipped": 1}, 5.12)),
        ("no tests ran in 0.01s", ({}, 0.01)),
    ],
)
def test_the_summary_line_of_each_run_is_read(line, expected, integration):
    text = f"collected 4 items\ntests/integration/test_x.py ....\n2 passed in 1.00s?\n{line}\n"
    assert integration.parse_summary(text) == expected


def test_a_run_without_a_summary_has_none(integration):
    assert integration.parse_summary("Traceback (most recent call last):\n  boom\n") is None


def test_the_summary_adds_every_run_in_pytests_form(integration):
    job = integration.Job("test_x.py")
    results = [
        integration.Result(job, 1, 0, {"passed": 4, "skipped": 1}, 10.0),
        integration.Result(job, 2, 1, {"passed": 2, "failed": 1}, 20.0),
        integration.Result(job, 3, 1, {"error": 1, "warning": 1}, 1.0),
        ## a crash without a summary is an error
        integration.Result(job, 1, 1, {}, None),
    ]
    counts = integration.total(results)
    assert counts == {"passed": 6, "skipped": 1, "failed": 1, "error": 2, "warning": 1}
    assert integration.summary_line(counts, 1234.567) == (
        "1 failed, 6 passed, 1 skipped, 1 warning, 2 errors in 1234.57s (0:20:34)"
    )
    assert integration.summary_line({"passed": 1, "error": 1}, 5) == "1 passed, 1 error in 5.00s"
    assert integration.summary_line({}, 0.5) == "no tests ran in 0.50s"


def test_the_report_names_each_failed_file_with_its_log(tmp_path, monkeypatch, capsys, integration):
    monkeypatch.setattr(integration, "ROOT", tmp_path)
    monkeypatch.setattr(integration, "LOG_DIR", tmp_path / ".integration-logs")
    integration.LOG_DIR.mkdir()
    bad = integration.Job("test_bad.py")
    (integration.LOG_DIR / bad.log_name).write_text(
        "setup noise\n==== FAILURES ====\n____ test_one ____\nE   assert False\n"
        "==== short test summary info ====\nFAILED tests/integration/test_bad.py::test_one\n"
        "==== 1 failed, 2 passed in 3.00s ====\n"
    )
    good = integration.Job("test_good.py")
    results = [
        integration.Result(good, 1, 0, {"passed": 5}, 9.0),
        integration.Result(bad, 2, 1, {"failed": 1, "passed": 2}, 3.0),
    ]
    assert integration.report([good, bad], results, [1, 2], 61.0) == 1
    out = capsys.readouterr().out
    assert "E   assert False" in out
    assert "setup noise" not in out
    assert "FAILED test_bad.py on stack 2, log .integration-logs/test_bad.log" in out
    assert out.rstrip().splitlines()[-1].strip("= ") == "1 failed, 7 passed in 61.00s (0:01:01)"
    assert integration.report([good], results[:1], [1, 2], 9.0) == 0


def test_a_run_that_raises_is_a_failed_result(integration):
    todo = integration.jobs(["test_a.py", "test_b.py", "test_c.py"])

    def run_job(stack, job):
        if job.file == "test_b.py":
            raise OSError("cannot open the log")
        return integration.Result(job, stack, 0, {"passed": 1}, 1.0)

    results = []
    integration.schedule(todo, [1, 2], run_job, integration.threading.Event(), results)
    assert len(results) == 3
    (bad,) = (r for r in results if r.failed)
    assert bad.job.file == "test_b.py"
    assert bad.code is None
    assert bad.failure == "OSError: cannot open the log"


def test_the_report_fails_and_names_a_job_without_a_result(
    tmp_path, monkeypatch, capsys, integration
):
    monkeypatch.setattr(integration, "ROOT", tmp_path)
    monkeypatch.setattr(integration, "LOG_DIR", tmp_path / ".integration-logs")
    integration.LOG_DIR.mkdir()
    ran = integration.Job("test_ran.py")
    lost = integration.Job("test_lost.py")
    results = [integration.Result(ran, 1, 0, {"passed": 5}, 9.0)]
    assert integration.report([ran, lost], results, [1], 9.0) == 1
    out = capsys.readouterr().out
    assert "integration: FAILED test_lost.py, no result" in out
    assert "test_ran.py, no result" not in out
    assert out.rstrip().splitlines()[-1].strip("= ") == "5 passed in 9.00s"


def test_the_report_shows_the_exception_of_a_run_that_raised(
    tmp_path, monkeypatch, capsys, integration
):
    monkeypatch.setattr(integration, "ROOT", tmp_path)
    monkeypatch.setattr(integration, "LOG_DIR", tmp_path / ".integration-logs")
    integration.LOG_DIR.mkdir()
    job = integration.Job("test_x.py")
    result = integration.Result(job, 2, None, {}, None, "OSError: no log")
    assert integration.report([job], [result], [2], 1.0) == 1
    out = capsys.readouterr().out
    assert "OSError: no log" in out
    assert "FAILED test_x.py on stack 2, no run" in out
    assert "no result" not in out
    assert out.rstrip().splitlines()[-1].strip("= ") == "1 error in 1.00s"


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_integration_help_starts_no_stack(monkeypatch, capsys, flag, integration):
    def fail(*args, **kwargs):
        pytest.fail("started stack work")

    monkeypatch.setattr(stacks, "stack_up", fail)
    monkeypatch.setattr(stacks, "run_stacks", fail)
    monkeypatch.setattr(integration, "run_scheduled", fail)
    monkeypatch.setattr(integration.subprocess, "run", fail)
    assert integration.main(["--stacks", "3", flag]) == 0
    assert "scripts/integration" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_e2e_help_starts_no_stack(monkeypatch, capsys, flag, e2e):
    def fail(*args, **kwargs):
        pytest.fail("started stack work")

    monkeypatch.setattr(e2e, "prepare", fail)
    monkeypatch.setattr(stacks, "stack_up", fail)
    monkeypatch.setattr(stacks, "run_stacks", fail)
    monkeypatch.setattr(e2e.subprocess, "run", fail)
    assert e2e.main(["--no-reset", flag]) == 0
    assert "scripts/e2e" in capsys.readouterr().out


def test_help_after_a_double_dash_is_not_a_request():
    assert suites.wants_help(["-k", "x", "--help"])
    assert not suites.wants_help(["--", "--help"])
    assert not suites.wants_help(["-k", "help"])


def test_the_scheduler_runs_the_whole_suite_or_files_given_with_stacks(integration):
    suite = integration.suite_files()
    assert integration.scheduled_files({}, []) == suite
    assert integration.scheduled_files({}, ["--durations=30"]) == suite
    assert integration.scheduled_files({}, ["tests/integration"]) == suite
    files = ["tests/integration/test_grid.py", "backend/tests/integration/test_seed.py"]
    assert integration.scheduled_files({"--stacks": "3"}, files) == [
        "test_grid.py",
        "test_seed.py",
    ]
    ## a file without --stacks, -k, --root or a node id runs as one pytest
    assert integration.scheduled_files({}, files) is None
    assert integration.scheduled_files({"--stacks": "3"}, ["-k", "grid"]) is None
    assert integration.scheduled_files({"--root": None}, []) is None
    node = "tests/integration/test_seed.py::test_x"
    assert integration.scheduled_files({"--stacks": "3"}, [node]) is None


def test_one_stack_runs_the_whole_suite_as_one_pytest_and_then_the_root_run(
    monkeypatch, integration
):
    scheduled = []
    calls = []

    def run_scheduled(todo, numbers, extra):
        scheduled.append((todo, numbers, extra))
        return 0

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(integration, "run_scheduled", run_scheduled)
    monkeypatch.setattr(integration.subprocess, "run", run)
    monkeypatch.setattr(integration.stacks, "default_stacks", lambda: 1)
    for args in (["--durations=5"], ["--stacks", "1", "--durations=5"]):
        assert integration.main(args) == 0
        todo, numbers, extra = scheduled.pop()
        assert [job.key for job in todo] == ["tests/integration", "test_gateway.py --root"]
        assert numbers == [1]
        assert extra == ["--durations=5"]
    path = "tests/integration/test_gateway.py"
    assert integration.main(["--stacks", "1", path]) == 0
    assert calls[-1][1:3] == ["exec", "sh"]
    assert calls[-1][5:] == ["sh", path]
    assert integration.main(["--stacks", "1", "-k", "gateway"]) == 0
    assert calls[-1][5:] == ["sh", "tests/integration", "-k", "gateway"]
    ## the whole run's own job is one pytest, also where the machine would give several stacks
    monkeypatch.setattr(integration.stacks, "default_stacks", lambda: 3)
    whole = integration.Job("tests/integration")
    assert integration.main(whole.args()) == 0
    assert calls[-1][5:] == ["sh", *whole.args()]
    assert scheduled == []


def test_several_stacks_schedule_the_suite_with_the_other_arguments(monkeypatch, integration):
    runs = []
    monkeypatch.setattr(integration, "run_scheduled", lambda *args: runs.append(args) or 0)
    monkeypatch.setattr(stacks, "default_stacks", lambda: 3)
    assert integration.main(["--durations=30"]) == 0
    assert runs == [(integration.jobs(integration.suite_files()), [1, 2, 3], ["--durations=30"])]


@pytest.mark.parametrize(
    ("log", "message"),
    [
        (None, "dev-app: the backend exited before it answered"),
        (Path("backend.log"), "dev-app: the backend exited before it answered, see backend.log"),
    ],
)
def test_wait_healthy_names_the_log_of_a_backend_that_exited(monkeypatch, log, message):
    monkeypatch.setattr(health, "healthy", lambda url: False)
    exited = subprocess.Popen(["true"])
    exited.wait()
    with pytest.raises(SystemExit) as raised:
        health.wait_healthy(1, "http://x", exited, "dev-app", log)
    assert raised.value.code == message


def test_docs_shots_runs_the_docs_tests_or_the_tests_named(docs_shots):
    assert docs_shots.e2e_args([]) == ["tests/e2e/docs"]
    assert docs_shots.e2e_args(["--no-reset", "-k", "more_menu"]) == [
        "tests/e2e/docs",
        "--no-reset",
        "-k",
        "more_menu",
    ]
    path = "backend/tests/e2e/docs/test_more_menu.py"
    assert docs_shots.e2e_args([path, "-x"]) == [path, "-x"]


@pytest.mark.parametrize(
    ("args", "full"),
    [
        ([], True),
        (["--no-reset", "-x"], True),
        (["-k", "more_menu"], False),
        (["-kmore_menu"], False),
        (["tests/e2e/docs/test_more_menu.py"], False),
        (["backend/tests/e2e/docs"], False),
    ],
)
def test_docs_shots_deletes_the_images_only_in_a_full_run(args, full, docs_shots):
    assert docs_shots.partial(args) is not full


@pytest.mark.parametrize(
    "args",
    [["-m", "docs"], ["--lf"], ["--sw"], ["--collect-only"], ["--co"], ["--deselect", "x"]],
)
def test_docs_shots_reads_every_selection_as_partial(args, docs_shots):
    assert docs_shots.partial(args)


def test_docs_shots_does_not_read_color_as_a_selection(docs_shots):
    assert not docs_shots.partial(["--color=yes", "-x"])


class ShotsRun:
    """docs-shots with scripts/e2e and scripts/docs-lint replaced: what ran, what the e2e wrote.

    The fake e2e writes into the staging directory the script names in BWTIDY_DOCS_SHOTS_DIR.
    """

    def __init__(
        self, monkeypatch, tmp_path: Path, docs_shots, e2e_code: int = 0, writes: tuple = ()
    ):
        self.images = tmp_path / "images"
        self.images.mkdir()
        self.commands: list[list[str]] = []
        self.e2e_code = e2e_code
        self.writes = writes
        monkeypatch.setattr(docs_shots, "IMAGES", self.images)
        monkeypatch.setattr(docs_shots.subprocess, "run", self.run)

    def image(self, name: str, age: float = 0) -> Path:
        path = self.images / name
        path.write_bytes(b"png")
        if age:
            old = time.time() - age
            os.utime(path, (old, old))
        return path

    def run(self, command, **kwargs):
        self.commands.append(command)
        if command[0].endswith("/e2e"):
            staging = Path(kwargs["env"]["BWTIDY_DOCS_SHOTS_DIR"])
            assert staging.parent == self.images
            for name in self.writes:
                (staging / name).write_bytes(b"new " + name.encode())
            return subprocess.CompletedProcess(command, self.e2e_code)
        return subprocess.CompletedProcess(command, 0)

    def names(self) -> list[str]:
        return sorted(p.name for p in self.images.glob("*.png"))

    def entries(self) -> list[str]:
        return sorted(p.name for p in self.images.iterdir())


def test_docs_shots_leaves_the_images_as_they_were_when_the_e2e_run_fails(
    monkeypatch, tmp_path, docs_shots
):
    shots = ShotsRun(monkeypatch, tmp_path, docs_shots, e2e_code=1, writes=("new.png", "old.png"))
    old = shots.image("old.png", age=3600)
    assert docs_shots.main([]) == 1
    assert shots.entries() == ["old.png"]
    assert old.read_bytes() == b"png"
    assert len(shots.commands) == 1


def test_docs_shots_moves_the_images_over_docs_images_after_a_pass(
    monkeypatch, tmp_path, docs_shots
):
    shots = ShotsRun(monkeypatch, tmp_path, docs_shots, writes=("new.png", "old.png"))
    shots.image("old.png", age=3600)
    assert docs_shots.main(["-k", "x"]) == 0
    assert shots.entries() == ["new.png", "old.png"]
    assert (shots.images / "old.png").read_bytes() == b"new old.png"


def test_docs_shots_removes_its_staging_directory_when_interrupted(
    monkeypatch, tmp_path, docs_shots
):
    shots = ShotsRun(monkeypatch, tmp_path, docs_shots, writes=("new.png",))

    def interrupted(command, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(docs_shots.subprocess, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        docs_shots.main([])
    assert shots.entries() == []


def test_docs_shots_deletes_a_stale_image_after_a_passing_full_run(
    monkeypatch, tmp_path, capsys, docs_shots
):
    shots = ShotsRun(monkeypatch, tmp_path, docs_shots, writes=("kept.png",))
    shots.image("stale.png", age=3600)
    shots.image("kept.png", age=3600)
    assert docs_shots.main([]) == 0
    assert shots.names() == ["kept.png"]
    assert "deleted stale.png" in capsys.readouterr().out
    assert shots.commands[-1][0].endswith("/docs-lint")


def test_docs_shots_deletes_nothing_in_a_partial_run(monkeypatch, tmp_path, docs_shots):
    shots = ShotsRun(monkeypatch, tmp_path, docs_shots, writes=("one.png",))
    shots.image("other.png", age=3600)
    assert docs_shots.main(["-k", "one"]) == 0
    assert shots.names() == ["one.png", "other.png"]


def test_docs_shots_deletes_nothing_when_the_run_wrote_no_image(
    monkeypatch, tmp_path, capsys, docs_shots
):
    shots = ShotsRun(monkeypatch, tmp_path, docs_shots)
    shots.image("a.png", age=3600)
    shots.image("b.png", age=3600)
    assert docs_shots.main([]) == 0
    assert shots.names() == ["a.png", "b.png"]
    assert "wrote no image" in capsys.readouterr().out
