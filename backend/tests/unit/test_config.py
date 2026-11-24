from pathlib import Path

import pytest

from bwtidy.config import (
    DEFAULT_IDLE_EXIT,
    FAILED_KEEP_HOURS,
    HEARTBEAT_TIMEOUT,
    IDLE_TIMEOUT,
    KEEP_ALWAYS,
    KEEP_DAYS,
    KEEP_NEWEST,
    Retention,
    Settings,
    SettingsError,
)
from tests.support.quiet import assert_absent

WORKER_VARS = [
    "BWTIDY_SOCKET",
    "BWTIDY_BW_SOCKET",
    "BWTIDY_ACCOUNT_SERVER",
    "BWTIDY_ACCOUNT_EMAIL",
    "BWTIDY_EXIT_AFTER_SESSION",
    "BWTIDY_IDLE_EXIT",
    "BWTIDY_PENDING_MARKER",
    "BWTIDY_SECURE_COOKIES",
    "BWTIDY_HEARTBEAT_TIMEOUT",
    "BWTIDY_SESSION_IDLE_TIMEOUT",
    "BWTIDY_BACKUP_KEEP_NEWEST",
    "BWTIDY_BACKUP_KEEP_DAYS",
    "BWTIDY_BACKUP_KEEP_ALWAYS",
    "BWTIDY_FAILED_KEEP_HOURS",
    "BWTIDY_BACKUPS_DIR",
    "BWTIDY_DATA_DIR",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in WORKER_VARS:
        monkeypatch.delenv(name, raising=False)


def test_without_the_worker_settings_nothing_changes():
    settings = Settings.from_env()
    assert settings.socket is None
    assert settings.bw_socket is None
    assert settings.account is None
    assert settings.exit_after_session is False
    assert settings.idle_exit == DEFAULT_IDLE_EXIT == 600
    assert settings.pending_marker is None
    assert settings.secure_cookies is False


def test_every_worker_setting_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BWTIDY_SOCKET", str(tmp_path / "run" / "app.sock"))
    monkeypatch.setenv("BWTIDY_BW_SOCKET", str(tmp_path / "run" / "bw.sock"))
    monkeypatch.setenv("BWTIDY_ACCOUNT_SERVER", "https://vault.example.test")
    monkeypatch.setenv("BWTIDY_ACCOUNT_EMAIL", "peer@example.test")
    monkeypatch.setenv("BWTIDY_EXIT_AFTER_SESSION", "1")
    monkeypatch.setenv("BWTIDY_IDLE_EXIT", "30")
    monkeypatch.setenv("BWTIDY_PENDING_MARKER", str(tmp_path / "pending"))
    monkeypatch.setenv("BWTIDY_SECURE_COOKIES", "1")
    settings = Settings.from_env()
    assert settings.socket == tmp_path / "run" / "app.sock"
    assert settings.bw_socket == tmp_path / "run" / "bw.sock"
    assert settings.account == ("https://vault.example.test", "peer@example.test")
    assert settings.exit_after_session is True
    assert settings.idle_exit == 30
    assert settings.pending_marker == tmp_path / "pending"
    assert settings.secure_cookies is True


def test_the_backups_are_in_the_data_dir_unless_a_backups_dir_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("BWTIDY_DATA_DIR", str(tmp_path / "app"))
    assert Settings.from_env().backup_dir == tmp_path / "app" / "backups"
    monkeypatch.setenv("BWTIDY_BACKUPS_DIR", " ")
    assert Settings.from_env().backup_dir == tmp_path / "app" / "backups"
    monkeypatch.setenv("BWTIDY_BACKUPS_DIR", str(tmp_path / "backups" / "h" / "e"))
    assert Settings.from_env().backup_dir == tmp_path / "backups" / "h" / "e"


def test_relative_socket_paths_become_absolute(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BWTIDY_BW_SOCKET", "run/bw.sock")
    assert Settings.from_env().bw_socket == tmp_path / "run" / "bw.sock"


@pytest.mark.parametrize("value", ["0", "", "yes"])
def test_flags_are_on_only_for_1(monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv("BWTIDY_EXIT_AFTER_SESSION", value)
    monkeypatch.setenv("BWTIDY_SECURE_COOKIES", value)
    settings = Settings.from_env()
    assert settings.exit_after_session is False
    assert settings.secure_cookies is False


@pytest.mark.parametrize(
    "name", ["BWTIDY_ACCOUNT_SERVER", "BWTIDY_ACCOUNT_EMAIL"], ids=["server", "email"]
)
def test_the_account_server_and_email_only_together(monkeypatch: pytest.MonkeyPatch, name: str):
    monkeypatch.setenv(name, "x")
    with pytest.raises(SettingsError, match="together"):
        Settings.from_env()


def test_the_timeouts_and_the_retention_default_to_todays_values():
    settings = Settings.from_env()
    assert settings.policy.heartbeat_timeout == HEARTBEAT_TIMEOUT == 120
    assert settings.policy.idle_timeout == IDLE_TIMEOUT == 7200
    assert settings.policy.retention == Retention(KEEP_NEWEST, KEEP_DAYS, KEEP_ALWAYS)
    assert (KEEP_NEWEST, KEEP_DAYS, KEEP_ALWAYS) == (40, 90, 5)
    assert settings.policy.failed_keep_hours == FAILED_KEEP_HOURS == 30


def test_blank_timeouts_and_retention_are_the_defaults(monkeypatch: pytest.MonkeyPatch):
    for name in WORKER_VARS[-6:]:
        monkeypatch.setenv(name, "  ")
    settings = Settings.from_env()
    assert settings.policy.heartbeat_timeout == HEARTBEAT_TIMEOUT
    assert settings.policy.retention == Retention()
    assert settings.policy.failed_keep_hours == FAILED_KEEP_HOURS


def test_the_timeouts_and_the_retention_are_read(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BWTIDY_HEARTBEAT_TIMEOUT", "90.5")
    monkeypatch.setenv("BWTIDY_SESSION_IDLE_TIMEOUT", "1800")
    monkeypatch.setenv("BWTIDY_BACKUP_KEEP_NEWEST", "12")
    monkeypatch.setenv("BWTIDY_BACKUP_KEEP_DAYS", "30")
    monkeypatch.setenv("BWTIDY_BACKUP_KEEP_ALWAYS", "2")
    monkeypatch.setenv("BWTIDY_FAILED_KEEP_HOURS", "6")
    settings = Settings.from_env()
    assert settings.policy.heartbeat_timeout == 90.5
    assert settings.policy.idle_timeout == 1800
    assert settings.policy.retention == Retention(keep_newest=12, keep_days=30, keep_always=2)
    assert settings.policy.failed_keep_hours == 6


def test_a_heartbeat_timeout_of_60_seconds_is_the_least(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BWTIDY_HEARTBEAT_TIMEOUT", "60")
    assert Settings.from_env().policy.heartbeat_timeout == 60


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BWTIDY_HEARTBEAT_TIMEOUT", "59.9"),
        ("BWTIDY_HEARTBEAT_TIMEOUT", "-120"),
        ("BWTIDY_HEARTBEAT_TIMEOUT", "soon"),
        ("BWTIDY_HEARTBEAT_TIMEOUT", "nan"),
        ("BWTIDY_SESSION_IDLE_TIMEOUT", "0"),
        ("BWTIDY_SESSION_IDLE_TIMEOUT", "-1"),
        ("BWTIDY_SESSION_IDLE_TIMEOUT", "inf"),
        ("BWTIDY_SESSION_IDLE_TIMEOUT", "two hours"),
        ("BWTIDY_BACKUP_KEEP_NEWEST", "0"),
        ("BWTIDY_BACKUP_KEEP_NEWEST", "4.5"),
        ("BWTIDY_BACKUP_KEEP_DAYS", "-3"),
        ("BWTIDY_BACKUP_KEEP_DAYS", "many"),
        ("BWTIDY_BACKUP_KEEP_ALWAYS", "0"),
        ("BWTIDY_BACKUP_KEEP_ALWAYS", "1e2"),
        ("BWTIDY_FAILED_KEEP_HOURS", "0"),
        ("BWTIDY_FAILED_KEEP_HOURS", "-30"),
        ("BWTIDY_FAILED_KEEP_HOURS", "a day"),
    ],
)
def test_an_unusable_value_is_refused_by_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
):
    monkeypatch.setenv(name, value)
    with pytest.raises(SettingsError) as error:
        Settings.from_env()
    assert name in str(error.value)
    assert_absent(value, str(error.value), "the error message")
