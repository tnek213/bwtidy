"""The encryption of the backup files (see docs/spec.md, Backups).

A backup is encrypted with age (scrypt passphrase, the master password) and written atomically,
so a failed write leaves no .zip.age behind. After a new master password reencrypt_all moves
every backup to it.
"""

import contextlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pyrage

from bwtidy import passphrase
from bwtidy.backups.files import BackupError, list_backups

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReencryptResult:
    """What reencrypt_all did: how many files it re-encrypted and which it could not read."""

    done: int
    failed: list[str]


def write_encrypted(directory: Path, name: str, plaintext: bytes, password: str) -> Path:
    """Encrypt plaintext with the password and write it to directory/name atomically.

    The data goes to a temporary name in the same directory, is fsynced and then renamed,
    so a reader never sees a partial .zip.age and a failure leaves nothing behind.
    """
    try:
        ciphertext = passphrase.encrypt(plaintext, password)
    except pyrage.EncryptError as error:
        raise BackupError("encrypt", str(error)) from None
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    fd, temp = tempfile.mkstemp(dir=directory, prefix=".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(ciphertext)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except OSError as error:
        remove_quietly(temp)
        raise BackupError("write", error.strerror or str(error)) from None
    ## the file is complete once renamed, a failed directory sync is only worth a warning
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as error:
        log.warning("Syncing the backup directory failed: %s", error.strerror or error)
    return target


def remove_quietly(path: str) -> None:
    with contextlib.suppress(OSError):
        os.unlink(path)


def decrypt_backup(path: Path, password: str) -> bytes:
    """The zip inside a backup file. Raises BackupError on a wrong password or a damaged file."""
    try:
        return passphrase.decrypt(path.read_bytes(), password)
    except pyrage.DecryptError:
        raise BackupError("decrypt", "wrong password or damaged file") from None
    except OSError as error:
        raise BackupError("read", error.strerror or str(error)) from None


def decrypts_with(path: Path, password: str) -> bool:
    try:
        decrypt_backup(path, password)
    except BackupError as error:
        if error.step != "decrypt":
            raise
        return False
    return True


def reencrypt_all(directory: Path, older: list[str], new: str) -> ReencryptResult:
    """Re-encrypt every backup in the directory that does not open with the new password.

    older holds every earlier master password a backup may still be encrypted with, newest
    first, and each is tried in that order. One file at a time, each replaced atomically, so a
    crash midway leaves every file readable with one of the passwords. A file that decrypts
    with none of them is left untouched and reported by name. A file that already decrypts
    with the new password (an earlier run stopped halfway) is left alone and not reported. Any
    other failure, such as a full disk, raises BackupError and stops the run.
    """
    done = 0
    failed: list[str] = []
    for info in sorted(list_backups(directory), key=lambda b: b.time):
        path = directory / info.name
        plaintext = _decrypt_with_any(path, older)
        if plaintext is None:
            if not decrypts_with(path, new):
                failed.append(info.name)
            continue
        write_encrypted(directory, info.name, plaintext, new)
        done += 1
    log.info("Re-encrypted %d backups, %d could not be decrypted", done, len(failed))
    return ReencryptResult(done, failed)


def _decrypt_with_any(path: Path, passwords: list[str]) -> bytes | None:
    for password in passwords:
        try:
            return decrypt_backup(path, password)
        except BackupError as error:
            if error.step != "decrypt":
                raise
    return None
