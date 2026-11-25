"""Queued hidden values (docs/spec.md, MCP and the queue): age-encrypted while they wait.

A UI edit of a secret column is queued like any other edit, but SQLite never holds a plain
secret. So the value is encrypted to an age X25519 identity before it is stored, and decrypted
only when the entry is written. The identity itself lives in the settings table, encrypted
with the master password like the backups (scrypt), and is decrypted into RAM on every unlock.
Encrypting a value to the identity's recipient costs nothing noticeable, so only the unlock
pays the scrypt work. A login with a new master password re-encrypts the identity, as it does
the backups.
"""

import asyncio
import base64
import logging
import sqlite3
from collections.abc import Sequence

import pyrage

from bwtidy import passphrase
from bwtidy.db import HIDDEN_IDENTITY, Database

log = logging.getLogger(__name__)


class HiddenValueError(Exception):
    """A queued hidden value cannot be decrypted (the identity is not the one it was made for)."""


class HiddenValues:
    """The age identity for queued hidden values, stored encrypted, unlocked into RAM."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._identity: pyrage.x25519.Identity | None = None

    @property
    def unlocked(self) -> bool:
        return self._identity is not None

    @property
    def recipient(self) -> str | None:
        """The public half, so a test can tell the identity survived a re-encryption."""
        return None if self._identity is None else str(self._identity.to_public())

    async def unlock(self, password: str, older: Sequence[str] = ()) -> bool:
        """Decrypt the stored identity with the master password, or create one.

        An identity that opens only with one of the older passwords (every master password the
        backups may still be encrypted with, newest first) is stored again under the new
        password, like the backups. One that opens with none is replaced: the hidden values
        queued under it can no longer be read and their entries fail at the commit. The scrypt
        work runs in a thread. When the settings table cannot be used, the identity lives in RAM
        only (the queue cannot store entries then either) and the answer is False: the stored
        one may still need an older password.
        """
        try:
            identity = await asyncio.to_thread(self._load_or_create, password, older)
        except (OSError, sqlite3.Error) as error:
            log.warning("Could not read or store the identity for hidden values: %s", error)
            self._identity = pyrage.x25519.Identity.generate()
            return False
        self._identity = identity
        return True

    def _load_or_create(self, password: str, older: Sequence[str]) -> pyrage.x25519.Identity:
        stored = self.db.get_setting(HIDDEN_IDENTITY)
        if stored is not None:
            ciphertext = base64.b64decode(stored)
            identity = _open(ciphertext, password)
            if identity is not None:
                return identity
            for old_password in older:
                if old_password == password:
                    continue
                identity = _open(ciphertext, old_password)
                if identity is not None:
                    self._store(identity, password)
                    log.info("The identity for queued hidden values was re-encrypted")
                    return identity
            log.warning(
                "The identity for queued hidden values opens with no known password, a new one"
                " replaces it"
            )
        identity = pyrage.x25519.Identity.generate()
        self._store(identity, password)
        return identity

    def _store(self, identity: pyrage.x25519.Identity, password: str) -> None:
        ciphertext = passphrase.encrypt(str(identity).encode(), password)
        self.db.set_setting(HIDDEN_IDENTITY, base64.b64encode(ciphertext).decode())

    def clear(self) -> None:
        self._identity = None

    def encrypt(self, value: str) -> str:
        """The armored age ciphertext of a value, for the identity in RAM."""
        if self._identity is None:
            raise HiddenValueError("no identity for hidden values, the vault is locked")
        recipient = self._identity.to_public()
        return pyrage.encrypt(value.encode(), [recipient], armored=True).decode()

    def decrypt(self, ciphertext: str) -> str:
        if self._identity is None:
            raise HiddenValueError("no identity for hidden values, the vault is locked")
        try:
            return pyrage.decrypt(ciphertext.encode(), [self._identity]).decode()
        except (pyrage.DecryptError, UnicodeDecodeError, ValueError):
            raise HiddenValueError(
                "the queued hidden value cannot be decrypted, it was queued under another identity"
            ) from None


def _open(ciphertext: bytes, password: str) -> pyrage.x25519.Identity | None:
    try:
        plain = passphrase.decrypt(ciphertext, password)
    except pyrage.DecryptError:
        return None
    try:
        return pyrage.x25519.Identity.from_str(plain.decode())
    except (pyrage.IdentityError, UnicodeDecodeError, ValueError):
        return None
