"""The backup files' encryption on real age files (every scrypt run costs over a second)."""

import functools
import os
from pathlib import Path

import pyrage
import pytest

from bwtidy.backups.crypto import decrypt_backup, reencrypt_all, write_encrypted
from bwtidy.backups.files import BackupError, list_backups
from tests.support.quiet import assert_absent

## real age files, the scrypt stand-in of conftest.py would test nothing here
pytestmark = pytest.mark.real_scrypt

PASSWORD = "correct horse battery staple"
NAME = "20261026T120000Z.zip.age"
DATA = b"PK\x03\x04 not really a zip but good enough " * 100

OLD = "old master password"
NEW = "new master password"
## a master password between OLD and NEW whose re-encryption run was cut off
BETWEEN = "the master password in between"
NAMES = ["20260101T000000Z.zip.age", "20260601T120000Z.zip.age", "20261026T120000Z.zip.age"]


def test_round_trip(tmp_path: Path):
    path = write_encrypted(tmp_path, NAME, DATA, PASSWORD)
    assert path == tmp_path / NAME
    raw = path.read_bytes()
    assert raw.startswith(b"age-encryption.org/v1\n-> scrypt ")
    assert_absent(DATA, raw, "the stored bytes")
    assert decrypt_backup(path, PASSWORD) == DATA
    assert pyrage.passphrase.decrypt(raw, PASSWORD) == DATA


def test_wrong_password_fails(tmp_path: Path):
    path = write_encrypted(tmp_path, NAME, DATA, PASSWORD)
    with pytest.raises(BackupError) as error:
        decrypt_backup(path, "not the password")
    assert error.value.step == "decrypt"
    assert_absent(PASSWORD, str(error.value), "the error message")
    assert "not the password" not in str(error.value)


def test_damaged_file_fails(tmp_path: Path):
    path = write_encrypted(tmp_path, NAME, DATA, PASSWORD)
    raw = bytearray(path.read_bytes())
    raw[-10] ^= 0xFF
    path.write_bytes(raw)
    with pytest.raises(BackupError):
        decrypt_backup(path, PASSWORD)


def test_a_forced_error_leaves_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def refuse(fd: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", refuse)
    with pytest.raises(BackupError) as error:
        write_encrypted(tmp_path, NAME, DATA, PASSWORD)
    assert error.value.step == "write"
    assert "No space left on device" in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_the_directory_is_created(tmp_path: Path):
    directory = tmp_path / "backups"
    write_encrypted(directory, NAME, DATA, PASSWORD)
    assert [b.name for b in list_backups(directory)] == [NAME]


def content(name: str) -> bytes:
    return f"PK zip of {name}".encode() * 20


## encrypted once per name and password, the scrypt work is what makes these tests slow
@functools.cache
def ciphertext(name: str, password: str) -> bytes:
    return pyrage.passphrase.encrypt(content(name), password)


def write_all(directory: Path, password: str = OLD, names: list[str] = NAMES) -> None:
    for name in names:
        (directory / name).write_bytes(ciphertext(name, password))


def opens_with(directory: Path, name: str, password: str) -> bool:
    try:
        return decrypt_backup(directory / name, password) == content(name)
    except BackupError:
        return False


def files(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def test_every_backup_is_readable_with_the_new_password_only(tmp_path: Path):
    write_all(tmp_path)
    result = reencrypt_all(tmp_path, [OLD], NEW)
    assert result.done == 3
    assert result.failed == []
    assert files(tmp_path) == NAMES
    for name in NAMES:
        assert opens_with(tmp_path, name, NEW)
        assert not opens_with(tmp_path, name, OLD)


def test_a_corrupt_file_is_left_as_is_and_reported(tmp_path: Path):
    write_all(tmp_path, names=NAMES[:2])
    corrupt = tmp_path / NAMES[2]
    corrupt.write_bytes(b"age-encryption.org/v1\n-> scrypt garbage\n" + b"\xff" * 200)
    (tmp_path / "README.txt").write_text("not a backup")
    result = reencrypt_all(tmp_path, [OLD], NEW)
    assert result.done == 2
    assert result.failed == [NAMES[2]]
    assert corrupt.read_bytes().endswith(b"\xff" * 200)
    assert files(tmp_path) == [*NAMES, "README.txt"]
    for name in NAMES[:2]:
        assert opens_with(tmp_path, name, NEW)


def test_a_crash_midway_leaves_every_file_readable_with_exactly_one_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    write_all(tmp_path)
    encrypt = pyrage.passphrase.encrypt
    calls = 0

    def crash_on_second(plaintext: bytes, passphrase: str) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced crash")
        return encrypt(plaintext, passphrase)

    monkeypatch.setattr(pyrage.passphrase, "encrypt", crash_on_second)
    with pytest.raises(RuntimeError):
        reencrypt_all(tmp_path, [OLD], NEW)
    assert files(tmp_path) == NAMES
    readable_with = [
        (opens_with(tmp_path, name, OLD), opens_with(tmp_path, name, NEW)) for name in NAMES
    ]
    assert all(with_old != with_new for with_old, with_new in readable_with)
    ## oldest first, so the oldest file was done and the two newer ones still wait
    assert readable_with == [(False, True), (True, False), (True, False)]


def test_a_second_run_after_a_crash_finishes_without_reporting_the_done_files(tmp_path: Path):
    write_all(tmp_path, NEW, NAMES[:1])
    write_all(tmp_path, OLD, NAMES[1:2])
    result = reencrypt_all(tmp_path, [OLD], NEW)
    assert result.done == 1
    assert result.failed == []
    assert all(opens_with(tmp_path, name, NEW) for name in NAMES[:2])


def test_a_write_failure_stops_the_run_and_keeps_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    write_all(tmp_path, names=NAMES[:1])

    def refuse(fd: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("os.fsync", refuse)
    with pytest.raises(BackupError) as error:
        reencrypt_all(tmp_path, [OLD], NEW)
    assert error.value.step == "write"
    assert files(tmp_path) == NAMES[:1]
    assert opens_with(tmp_path, NAMES[0], OLD)


def test_an_empty_or_missing_directory_does_nothing(tmp_path: Path):
    assert reencrypt_all(tmp_path / "nowhere", [OLD], NEW).done == 0
    assert reencrypt_all(tmp_path, [OLD], NEW).failed == []


def test_each_older_password_is_tried_newest_first(tmp_path: Path):
    """A run cut off from A to B, then C: the backups in B and in A all end in C."""
    write_all(tmp_path, OLD, NAMES[:1])
    write_all(tmp_path, BETWEEN, NAMES[1:2])
    write_all(tmp_path, NEW, NAMES[2:])
    result = reencrypt_all(tmp_path, [BETWEEN, OLD], NEW)
    assert result.done == 2
    assert result.failed == []
    assert all(opens_with(tmp_path, name, NEW) for name in NAMES)


def test_a_backup_in_an_unlisted_password_is_reported(tmp_path: Path):
    write_all(tmp_path, OLD, NAMES[:1])
    write_all(tmp_path, BETWEEN, NAMES[1:2])
    result = reencrypt_all(tmp_path, [BETWEEN], NEW)
    assert result.done == 1
    assert result.failed == [NAMES[0]]
    assert opens_with(tmp_path, NAMES[0], OLD)
    assert opens_with(tmp_path, NAMES[1], NEW)


def test_without_older_passwords_every_backup_not_in_the_new_one_is_reported(tmp_path: Path):
    write_all(tmp_path, OLD, NAMES[:1])
    write_all(tmp_path, NEW, NAMES[1:2])
    result = reencrypt_all(tmp_path, [], NEW)
    assert result.done == 0
    assert result.failed == [NAMES[0]]
