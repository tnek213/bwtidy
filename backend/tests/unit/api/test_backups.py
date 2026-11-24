"""GET /api/backups/{name} downloads a backup, DELETE /api/backups/{name} deletes one
(docs/spec.md, Backups)."""

import io
import json
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bwtidy.backups.files import BackupError
from bwtidy.backups.reader import BackupContents
from bwtidy.protocol import backup_name
from bwtidy.writes.path import WritePath
from tests.support.app_env import AppEnv
from tests.support.fake_bw import PASSWORD

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
## what an age file starts with, so a download is checked without decrypting it
AGE_HEADER = b"age-encryption.org/v1\n"


@pytest.fixture
def started(tmp_path: Path) -> Iterator[AppEnv]:
    with AppEnv(tmp_path, fake_backups=False) as env:
        yield env


@pytest.fixture
def app(started: AppEnv) -> FastAPI:
    return started.app


@pytest.fixture
def writes(started: AppEnv) -> WritePath:
    return started.writes


@pytest.fixture
def client(started: AppEnv) -> TestClient:
    return started.client


@pytest.fixture
def logged_in(client: TestClient) -> TestClient:
    assert client.post("/api/session", json={"password": PASSWORD}).status_code == 200
    return client


def write_backup(tmp_path: Path, age_in_hours: float, content: bytes = AGE_HEADER + b"x") -> str:
    """A file that looks like a backup in the backups directory, with a stored fingerprint."""
    directory = tmp_path / "backups"
    directory.mkdir(exist_ok=True)
    name = backup_name(NOW - timedelta(hours=age_in_hours))
    (directory / name).write_bytes(content)
    return name


@pytest.fixture
def backups(tmp_path: Path, logged_in: TestClient, writes: WritePath) -> list[str]:
    """Three backups, newest first, each with a fingerprint in SQLite."""
    names = [write_backup(tmp_path, age) for age in (1, 2, 3)]
    for name in names:
        writes.db.set_backup_fingerprint(name, f"print-of-{name}")
    return names


def test_the_routes_need_a_session(client: TestClient, tmp_path: Path):
    name = write_backup(tmp_path, 1)
    assert client.get(f"/api/backups/{name}").status_code == 401
    assert client.delete(f"/api/backups/{name}").status_code == 401


def test_a_known_backup_downloads_as_a_file(logged_in: TestClient, backups: list[str]):
    name = backups[1]
    response = logged_in.get(f"/api/backups/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == f'attachment; filename="{name}"'
    assert response.content == AGE_HEADER + b"x"


def test_an_unknown_name_or_a_path_is_404(
    logged_in: TestClient, backups: list[str], tmp_path: Path
):
    (tmp_path / "secret.txt").write_text("not a backup")
    assert logged_in.get("/api/backups/20260101T000000Z.zip.age").status_code == 404
    assert logged_in.get("/api/backups/secret.txt").status_code == 404
    assert logged_in.get("/api/backups/..%2Fsecret.txt").status_code == 404
    assert logged_in.get("/api/backups/../secret.txt").status_code in (404, 405)
    assert logged_in.delete("/api/backups/20260101T000000Z.zip.age").status_code == 404
    assert logged_in.delete("/api/backups/..%2Fsecret.txt").status_code == 404
    assert (tmp_path / "secret.txt").exists()


def test_deleting_an_old_backup_removes_the_file_and_its_fingerprint(
    logged_in: TestClient, backups: list[str], tmp_path: Path, writes: WritePath
):
    old = backups[2]
    response = logged_in.delete(f"/api/backups/{old}")
    assert response.status_code == 204
    assert not (tmp_path / "backups" / old).exists()
    assert writes.db.backup_fingerprint(old) is None
    assert writes.db.backup_fingerprint(backups[0]) is not None
    listed = [b["name"] for b in logged_in.get("/api/backups").json()]
    assert listed == backups[:2]


def test_the_newest_backup_cannot_be_deleted(
    logged_in: TestClient, backups: list[str], tmp_path: Path, writes: WritePath
):
    newest = backups[0]
    response = logged_in.delete(f"/api/backups/{newest}")
    assert response.status_code == 409
    assert "newest" in response.json()["detail"]
    assert (tmp_path / "backups" / newest).exists()
    assert writes.db.backup_fingerprint(newest) is not None


def test_the_backup_a_running_write_selected_cannot_be_deleted(
    started: AppEnv, logged_in: TestClient, backups: list[str], tmp_path: Path
):
    selected = backups[1]
    ## what backup_for_write leaves while its write runs
    started.keeper._write_backup = selected
    response = logged_in.delete(f"/api/backups/{selected}")
    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"Backup {selected} is what the write under way was taken with."
    )
    assert (tmp_path / "backups" / selected).exists()

    started.keeper.release_write_backup()
    assert logged_in.delete(f"/api/backups/{selected}").status_code == 204


def test_a_backup_a_failed_entry_was_taken_with_cannot_be_deleted(
    logged_in: TestClient, backups: list[str], tmp_path: Path, writes: WritePath
):
    old = backups[2]
    entry_id = writes.db.add_queue_entry(
        "mcp",
        "Set login.username of 1 item",
        json.dumps(
            {"kind": "setLeaf", "itemId": "login-1", "column": "login.username", "value": "x"}
        ),
        status="failed",
        result=json.dumps({"error": "writeFailed", "detail": "boom", "backup": old}),
    )
    response = logged_in.delete(f"/api/backups/{old}")
    assert response.status_code == 409
    assert f"#{entry_id}" in response.json()["detail"]
    assert (tmp_path / "backups" / old).exists()

    assert logged_in.post("/api/queue/dismiss", json={"ids": [entry_id]}).status_code == 200
    assert logged_in.delete(f"/api/backups/{old}").status_code == 204


def test_a_backup_a_queued_restore_reads_cannot_be_deleted(
    logged_in: TestClient, backups: list[str], tmp_path: Path, writes: WritePath
):
    old = backups[1]
    entry_id = writes.db.add_queue_entry(
        "ui",
        f"Restore 1 item from {old}",
        json.dumps(
            {"kind": "restoreItems", "backup": old, "itemIds": ["login-1"], "revisionDates": {}}
        ),
    )
    response = logged_in.delete(f"/api/backups/{old}")
    assert response.status_code == 409
    assert f"#{entry_id}" in response.json()["detail"]
    assert (tmp_path / "backups" / old).exists()


def test_the_decrypted_copy_in_ram_goes_with_the_file(
    logged_in: TestClient, backups: list[str], writes: WritePath
):
    old = backups[2]
    reader = writes.backups
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("raw/items.json", "[]")
        archive.writestr("raw/trash.json", "[]")
    reader._last = BackupContents(old, buffer.getvalue())
    assert logged_in.delete(f"/api/backups/{old}").status_code == 204
    assert reader._last is None


def test_a_failed_backup_by_hand_answers_500_with_its_text(
    app: FastAPI, logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
):
    async def fails() -> None:
        raise BackupError("export", "exit 1")

    monkeypatch.setattr(app.state.bw.backups, "take_backup", fails)
    response = logged_in.post("/api/backups")
    assert (response.status_code, response.json()) == (
        500,
        {"detail": "Backup failed at export: exit 1"},
    )
