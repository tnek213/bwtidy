"""The gateway's settings (docs/spec.md, Gateway): what it reads from the environment and passes on
to the workers."""

from pathlib import Path

import pytest

from bwtidy.config import SettingsError
from bwtidy.gateway.settings import GatewaySettings, passed_on_from_env
from tests.support.gateway_env import make_env


def test_the_mcp_hosts_are_passed_on_to_the_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("BWTIDY_MCP_HOSTS", "bwtidy.example.test")
    passed_on = passed_on_from_env()
    assert passed_on["BWTIDY_MCP_HOSTS"] == "bwtidy.example.test"
    for env in make_env(tmp_path, passed_on=passed_on):
        environment = env.session(env.started()).json()["env"]
        assert environment["BWTIDY_MCP_HOSTS"] == "bwtidy.example.test"


POLICY = {
    "BWTIDY_HEARTBEAT_TIMEOUT": "90",
    "BWTIDY_SESSION_IDLE_TIMEOUT": "1800",
    "BWTIDY_BACKUP_KEEP_NEWEST": "12",
    "BWTIDY_BACKUP_KEEP_DAYS": "30",
    "BWTIDY_BACKUP_KEEP_ALWAYS": "2",
    "BWTIDY_FAILED_KEEP_HOURS": "6",
}


def test_the_timeouts_and_the_retention_are_passed_on_to_the_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    for name, value in POLICY.items():
        monkeypatch.setenv(name, value)
    passed_on = passed_on_from_env()
    assert {name: passed_on.get(name) for name in POLICY} == POLICY
    for env in make_env(tmp_path, passed_on=passed_on):
        environment = env.session(env.started()).json()["env"]
        assert {name: environment.get(name) for name in POLICY} == POLICY


def test_a_bad_timeout_or_retention_stops_the_gateway_at_the_start(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("BWTIDY_BACKUP_KEEP_DAYS", "0")
    with pytest.raises(SettingsError, match="BWTIDY_BACKUP_KEEP_DAYS"):
        GatewaySettings.from_env()


@pytest.mark.parametrize("base", ["0", "-1"])
def test_a_uid_base_below_one_stops_the_gateway_at_the_start(
    monkeypatch: pytest.MonkeyPatch, base: str
):
    monkeypatch.setenv("BWTIDY_UID_BASE", base)
    with pytest.raises(SettingsError, match="BWTIDY_UID_BASE"):
        GatewaySettings.from_env()


@pytest.mark.parametrize("base", ["1", "70000"])
def test_a_uid_base_of_one_or_more_is_read(monkeypatch: pytest.MonkeyPatch, base: str):
    monkeypatch.setenv("BWTIDY_UID_BASE", base)
    assert GatewaySettings.from_env().uid_base == int(base)


def test_the_backups_root_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BWTIDY_BACKUPS_ROOT", raising=False)
    assert GatewaySettings.from_env().backups_root is None
    monkeypatch.setenv("BWTIDY_BACKUPS_ROOT", "/backups")
    assert GatewaySettings.from_env().backups_root == Path("/backups")


def test_the_usage_stats_are_off_unless_asked_and_their_file_follows_the_accounts(
    monkeypatch: pytest.MonkeyPatch,
):
    for name in ("BWTIDY_STATS", "BWTIDY_STATS_DB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BWTIDY_ACCOUNTS_DIR", "/srv/bwtidy/accounts")
    settings = GatewaySettings.from_env()
    assert settings.stats is False
    assert settings.stats_path == Path("/srv/bwtidy/stats.db")
    monkeypatch.setenv("BWTIDY_STATS", "1")
    monkeypatch.setenv("BWTIDY_STATS_DB", "/stats/stats.db")
    settings = GatewaySettings.from_env()
    assert settings.stats is True
    assert settings.stats_path == Path("/stats/stats.db")
    monkeypatch.delenv("BWTIDY_ACCOUNTS_DIR")
    monkeypatch.delenv("BWTIDY_STATS_DB")
    assert GatewaySettings.from_env().stats_path == Path("/data/stats.db")
