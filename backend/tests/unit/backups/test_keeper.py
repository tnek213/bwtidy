"""The retention after each backup, in BackupKeeper: it keeps what Delete refuses to delete
(docs/spec.md, Backups)."""

import io
import json
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bwtidy.backups.files import BackupInfo
from bwtidy.backups.reader import BackupContents
from bwtidy.config import DEFAULT_RETENTION, KEEP_NEWEST, Policy, Retention
from bwtidy.protocol import backup_name
from tests.support.app_env import AppEnv

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


class KeeperEnv(AppEnv):
    """The app with backups faked at NOW and the old ones the retention tests write."""

    def __init__(
        self,
        tmp_path: Path,
        retention: Retention = DEFAULT_RETENTION,
        backups_dir: Path | None = None,
    ) -> None:
        super().__init__(
            tmp_path,
            policy=Policy(retention=retention),
            backup_dir=backups_dir or tmp_path / "backups",
        )
        self.directory = self.settings.backup_dir
        self.taken = 0

    async def fake_backup(
        self, session: Any, directory: Path, bw_bin: str, *, store: Any, stop: Any = None
    ) -> BackupInfo:
        """A file newer than every backup write_backup makes, with a stored fingerprint."""
        when = NOW + timedelta(seconds=self.taken)
        self.taken += 1
        name = backup_name(when)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(b"fake backup")
        store.set_backup_fingerprint(name, f"print-of-{name}")
        return BackupInfo(name, 11, when, f"print-of-{name}")

    def write_backup(self, age_in_days: float) -> str:
        self.directory.mkdir(exist_ok=True)
        name = backup_name(NOW - timedelta(days=age_in_days))
        (self.directory / name).write_bytes(b"old backup")
        self.keeper.db.set_backup_fingerprint(name, f"print-of-{name}")
        return name

    def recent_backups(self, count: int = KEEP_NEWEST) -> list[str]:
        """count backups of the last few days, so each older one is beyond the newest 40."""
        return [self.write_backup(i / 24) for i in range(1, count + 1)]

    def take_backup(self) -> str:
        response = self.client.post("/api/backups")
        assert response.status_code == 201
        return response.json()["name"]

    def files(self) -> set[str]:
        return {p.name for p in self.directory.iterdir()}

    def restore_entry(self, backup: str) -> int:
        operation = {
            "kind": "restoreItems",
            "backup": backup,
            "itemIds": ["login-1"],
            "revisionDates": {},
        }
        ## mcp, so the auto-commit never takes it
        return self.keeper.db.add_queue_entry(
            "mcp", f"Restore 1 item from {backup}", json.dumps(operation)
        )

    def failed_entry(self, backup: str) -> int:
        operation = {"kind": "setLeaf", "itemId": "login-1", "column": "login.username"}
        return self.keeper.db.add_queue_entry(
            "mcp",
            "Set login.username of 1 item",
            json.dumps(operation | {"value": "x"}),
            status="failed",
            result=json.dumps({"error": "writeFailed", "detail": "boom", "backup": backup}),
        )


@pytest.fixture
def env(tmp_path: Path) -> Iterator[KeeperEnv]:
    with KeeperEnv(tmp_path) as env:
        env.unlock()
        yield env


def test_the_retention_counts_come_from_the_settings(tmp_path: Path):
    with KeeperEnv(tmp_path, Retention(keep_newest=4, keep_days=30, keep_always=1)) as env:
        env.unlock()
        recent = [env.write_backup(day) for day in (1, 2, 3, 4, 5)]
        old = [env.write_backup(day) for day in (40, 50)]
        login_backup = backup_name(NOW)

        new = env.take_backup()

        ## the login backup and the new one are the newest 2, two recent ones fill the 4
        assert env.files() == {login_backup, new, recent[0], recent[1]}
        assert all(env.keeper.db.backup_fingerprint(name) is None for name in old)


def test_backups_the_retention_and_the_routes_use_a_backups_dir_apart_from_the_data_dir(
    tmp_path: Path,
):
    data, apart = tmp_path / "data", tmp_path / "elsewhere" / "backups"
    data.mkdir()
    with KeeperEnv(
        data, Retention(keep_newest=3, keep_days=30, keep_always=1), backups_dir=apart
    ) as env:
        env.unlock()
        old = [env.write_backup(day) for day in (1, 40, 50)]
        new = env.take_backup()

        login_backup = backup_name(NOW)
        assert env.files() == {login_backup, new, old[0]}
        assert not (data / "backups").exists()
        listed = [b["name"] for b in env.client.get("/api/backups").json()]
        assert listed == [new, login_backup, old[0]]
        download = env.client.get(f"/api/backups/{old[0]}")
        assert download.status_code == 200
        assert download.content == b"old backup"
        assert env.client.delete(f"/api/backups/{old[0]}").status_code == 204
        assert env.files() == {login_backup, new}


def test_retention_deletes_old_backups_with_their_fingerprints_and_decrypted_copy(env: KeeperEnv):
    old = [env.write_backup(100 + day) for day in range(10)]
    (env.directory / "keep-me.txt").write_text("not a backup")
    reader = env.keeper.reader
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("raw/items.json", "[]")
        archive.writestr("raw/trash.json", "[]")
        archive.writestr("raw/archived.json", "[]")
    reader._last = BackupContents(old[-1], buffer.getvalue())
    login_backup = backup_name(NOW)

    new = env.take_backup()

    ## the two new ones are among the newest 5, so the three newest old ones stay with them
    stay = set(old[:3])
    assert env.files() == stay | {login_backup, new, "keep-me.txt"}
    for name in old:
        assert (env.keeper.db.backup_fingerprint(name) is not None) == (name in stay)
    assert reader._last is None


def test_a_backup_a_pending_restore_reads_survives_forty_newer_ones(env: KeeperEnv):
    restored = env.write_backup(30)
    recent = env.recent_backups()
    entry_id = env.restore_entry(restored)

    env.take_backup()

    ## with the login's backup and the new one, the two oldest recent ones are beyond the 40
    assert not set(recent[-2:]) & env.files()
    assert restored in env.files()
    assert env.keeper.db.backup_fingerprint(restored) is not None
    assert "queued restore" in (env.keeper.in_use(restored) or "")
    assert f"#{entry_id}" in (env.keeper.in_use(restored) or "")


def test_a_backup_a_failed_entry_names_survives_ninety_days_until_dismissed(env: KeeperEnv):
    failed_with = env.write_backup(120)
    unused = env.write_backup(121)
    env.recent_backups(10)
    entry_id = env.failed_entry(failed_with)

    env.take_backup()
    assert failed_with in env.files()
    assert unused not in env.files()

    response = env.client.post("/api/queue/dismiss", json={"ids": [entry_id]})
    assert response.status_code == 200
    env.take_backup()
    assert failed_with not in env.files()
    assert env.keeper.db.backup_fingerprint(failed_with) is None


def test_retention_and_delete_ask_the_same_question(env: KeeperEnv):
    restored = env.write_backup(30)
    env.recent_backups()
    env.restore_entry(restored)
    env.take_backup()

    response = env.client.delete(f"/api/backups/{restored}")
    assert response.status_code == 409
    assert response.json()["detail"] == env.keeper.in_use(restored)


def test_the_backup_a_running_write_selected_stays_until_the_write_ends(tmp_path: Path):
    with KeeperEnv(tmp_path, Retention(keep_newest=2, keep_days=30, keep_always=1)) as env:
        env.unlock()
        selected, _ = env.call(env.keeper.backup_for_write)
        name = selected.name
        env.take_backup()
        env.take_backup()

        ## two newer ones fill the newest 2, the write keeps it
        assert name in env.files()
        assert (
            env.keeper.in_use(name) == f"Backup {name} is what the write under way was taken with."
        )

        env.keeper.release_write_backup()
        assert env.keeper.in_use(name) is None
        env.take_backup()
        assert name not in env.files()
        assert env.keeper.db.backup_fingerprint(name) is None
