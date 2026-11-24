import subprocess

import pytest
from devlib import gateway, health


@pytest.fixture
def calls(monkeypatch):
    """The run_backend collaborators patched out, in the order they were called."""
    log = []
    monkeypatch.setattr(gateway, "stack_up", lambda stack: True)
    monkeypatch.setattr(
        gateway, "dev_vault_run", lambda stack, *args, **kwargs: log.append(("dev-vault", *args))
    )
    monkeypatch.setattr(gateway, "app_port", lambda stack, name: "9999")
    monkeypatch.setattr(gateway, "open_page", lambda url: log.append(("open", url)))
    monkeypatch.setattr(gateway, "sync_venv", lambda stack: log.append(("sync",)))
    monkeypatch.setattr(
        gateway, "signal_backend", lambda stack, signal, *names: log.append(("signal", signal))
    )
    monkeypatch.setattr(
        gateway, "stop_backend", lambda stack, backend, label: log.append(("stop", label))
    )
    return log


class Output:
    def join(self):
        pass


def start_fake(monkeypatch, log, wait):
    class Backend:
        def wait(self):
            return wait()

    backend = Backend()

    def start(stack):
        log.append(("start",))
        return backend, Output()

    monkeypatch.setattr(gateway, "start_logged_backend", start)
    monkeypatch.setattr(health, "wait_healthy", lambda *args: log.append(("answers",)))


def test_a_running_backend_is_verified_and_opened(monkeypatch, calls):
    monkeypatch.setattr(health, "healthy", lambda url: True)
    urls = []
    assert gateway.run_backend(1, urls.append) == 0
    assert urls == ["http://127.0.0.1:9999"]
    assert calls == [("dev-vault", "up"), ("open", "http://127.0.0.1:9999")]


def test_a_running_backend_is_not_opened_without_open_browser(monkeypatch, calls):
    monkeypatch.setattr(health, "healthy", lambda url: True)
    assert gateway.run_backend(1, open_browser=False) == 0
    assert ("open", "http://127.0.0.1:9999") not in calls


def test_a_failed_verify_signals_the_backend_that_runs(monkeypatch, calls):
    monkeypatch.setattr(health, "healthy", lambda url: True)

    def verify(url):
        raise SystemExit("wrong version")

    with pytest.raises(SystemExit, match="wrong version"):
        gateway.run_backend(1, verify)
    assert calls == [("dev-vault", "up"), ("signal", 15)]


def test_a_stack_that_is_not_up_gets_setup(monkeypatch, calls):
    monkeypatch.setattr(gateway, "stack_up", lambda stack: False)
    monkeypatch.setattr(health, "healthy", lambda url: True)
    gateway.run_backend(1, open_browser=False)
    assert calls == [("dev-vault", "up"), ("dev-vault", "setup")]


def test_a_started_backend_is_verified_opened_and_waited_for(monkeypatch, calls):
    monkeypatch.setattr(health, "healthy", lambda url: False)
    start_fake(monkeypatch, calls, lambda: 7)
    urls = []
    assert gateway.run_backend(1, urls.append) == 7
    assert urls == ["http://127.0.0.1:9999"]
    assert calls == [
        ("dev-vault", "up"),
        ("sync",),
        ("start",),
        ("answers",),
        ("open", "http://127.0.0.1:9999"),
    ]


def test_a_failed_verify_stops_the_backend_it_started(monkeypatch, calls):
    monkeypatch.setattr(health, "healthy", lambda url: False)
    start_fake(monkeypatch, calls, lambda: pytest.fail("waited"))

    def verify(url):
        raise SystemExit("wrong version")

    with pytest.raises(SystemExit, match="wrong version"):
        gateway.run_backend(1, verify)
    assert calls[-1] == ("stop", "dev-app: the backend")
    assert ("open", "http://127.0.0.1:9999") not in calls


def test_ctrl_c_stops_the_backend_and_returns_zero(monkeypatch, calls):
    monkeypatch.setattr(health, "healthy", lambda url: False)

    def interrupted():
        raise KeyboardInterrupt

    start_fake(monkeypatch, calls, interrupted)
    assert gateway.run_backend(1, open_browser=False) == 0
    assert calls[-1] == ("stop", "dev-app: the backend")


def test_the_backend_log_gets_a_header_and_the_output(monkeypatch, tmp_path, capfdbinary):
    monkeypatch.setattr(gateway, "ROOT", tmp_path)
    monkeypatch.setattr(
        gateway,
        "start_backend",
        lambda stack, stdout: subprocess.Popen(["echo", "hello"], stdout=stdout),
    )
    backend, output = gateway.start_logged_backend(1)
    backend.wait()
    output.join()
    log = (tmp_path / ".dev-vault" / "backend.log").read_text()
    assert "scripts/dev-app" in log
    assert log.endswith("hello\n")
