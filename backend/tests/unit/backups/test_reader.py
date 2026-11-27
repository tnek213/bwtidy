"""BackupReader keeps the last backup it read and drops one that arrives after a clear."""

import io
import json
import threading
import zipfile
from pathlib import Path

import pytest

from bwtidy.backups import reader as reader_module
from bwtidy.backups.reader import BackupContents, BackupReader

NAME = "20261026T120000Z.zip.age"
OTHER = "20261027T120000Z.zip.age"


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("raw/items.json", json.dumps([{"id": "i1", "name": "One"}]))
        archive.writestr("raw/trash.json", "[]")
        archive.writestr("raw/archived.json", json.dumps([{"id": "i2", "name": "Two"}]))
    return buffer.getvalue()


class PausedDecrypt:
    """Stands in for decrypt_backup: waits for release() once it has started."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.proceed = threading.Event()

    def __call__(self, path: Path, password: str) -> bytes:
        self.started.set()
        assert self.proceed.wait(5)
        return _zip()


@pytest.fixture
def reader(tmp_path: Path) -> BackupReader:
    for name in (NAME, OTHER):
        (tmp_path / name).write_bytes(b"age")
    return BackupReader(tmp_path, lambda: "pw")


def _read_paused(reader: BackupReader, paused: PausedDecrypt, name: str = NAME) -> threading.Thread:
    thread = threading.Thread(target=reader.read, args=(name,))
    thread.start()
    assert paused.started.wait(5)
    return thread


def test_a_read_without_a_clear_keeps_the_contents(reader: BackupReader, monkeypatch):
    monkeypatch.setattr(reader_module, "decrypt_backup", lambda path, password: _zip())
    contents = reader.read(NAME)
    assert reader._last is contents


@pytest.mark.parametrize("ender", ["clear", "forget"])
def test_a_read_in_flight_across_a_clear_is_dropped(reader: BackupReader, monkeypatch, ender):
    paused = PausedDecrypt()
    monkeypatch.setattr(reader_module, "decrypt_backup", paused)
    thread = _read_paused(reader, paused)
    if ender == "clear":
        reader.clear()
    else:
        reader.forget(NAME)
    paused.proceed.set()
    thread.join(5)
    assert reader._last is None


def test_a_read_after_a_clear_is_kept(reader: BackupReader, monkeypatch):
    monkeypatch.setattr(reader_module, "decrypt_backup", lambda path, password: _zip())
    reader.clear()
    contents = reader.read(NAME)
    assert reader._last is contents


def test_the_contents_hold_the_archived_items_apart():
    contents = BackupContents(NAME, _zip())
    assert list(contents.items) == ["i1"]
    assert list(contents.archived) == ["i2"]


def test_a_backup_without_its_archived_listing_is_damaged():
    """The backup layout has one current format (docs/spec.md, Backups)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("raw/items.json", "[]")
        archive.writestr("raw/trash.json", "[]")
    with pytest.raises(ValueError, match="is damaged"):
        BackupContents(NAME, buffer.getvalue())
