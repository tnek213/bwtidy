"""A decrypted backup in RAM: BackupReader decrypts one with the session's password and keeps
the last one read, BackupContents holds its raw listings by item id and its attachments on
demand (docs/spec.md, Backups).

The reader keeps the last backup because the queue asks for the diff of a pending entry on
every listing and a decrypt costs about a second of scrypt. Nothing here puts a value into an
error message.
"""

import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bwtidy.backups.collect import attachment_members
from bwtidy.backups.crypto import decrypt_backup
from bwtidy.backups.files import BackupError
from bwtidy.protocol import BACKUP_NAME


def _names(archive: zipfile.ZipFile, member: str) -> dict[str, str]:
    """Id to name of a listing in a backup, empty when the backup lacks the listing."""
    if member not in archive.namelist():
        return {}
    listed = json.loads(archive.read(member))
    return {o["id"]: o["name"] for o in listed if o.get("id") and o.get("name")}


class BackupContents:
    """A decrypted backup: the raw listings by item id and the attachments on demand."""

    def __init__(self, name: str, plaintext: bytes) -> None:
        self.name = name
        self._zip = plaintext
        try:
            with zipfile.ZipFile(io.BytesIO(plaintext)) as archive:
                self._members = set(archive.namelist())
                items = json.loads(archive.read("raw/items.json"))
                trash = json.loads(archive.read("raw/trash.json"))
                archived = json.loads(archive.read("raw/archived.json"))
                self.folder_names = _names(archive, "raw/folders.json")
                self.collection_names = _names(archive, "raw/collections.json")
        except (zipfile.BadZipFile, KeyError, ValueError):
            raise ValueError(f"the backup {name} is damaged") from None
        self.items: dict[str, dict[str, Any]] = {i["id"]: i for i in items if i.get("id")}
        self.trash: dict[str, dict[str, Any]] = {i["id"]: i for i in trash if i.get("id")}
        ## the archived items, those in the trash too
        self.archived: dict[str, dict[str, Any]] = {i["id"]: i for i in archived if i.get("id")}

    def attachment_member(self, item: dict[str, Any], attachment: dict[str, Any]) -> str | None:
        """Where the backup holds an attachment of a backup item, None when it does not.

        item-attachments/<itemId>/<fileName>, with the attachment id in front of a file name
        used twice in one item, the way the backup wrote it (collect.attachment_members).
        """
        for listed, member in attachment_members(item):
            if listed.get("id") == attachment.get("id"):
                return member if member in self._members else None
        return None

    def attachment_bytes(self, member: str) -> bytes:
        with zipfile.ZipFile(io.BytesIO(self._zip)) as archive:
            return archive.read(member)


class BackupReader:
    """Decrypts backups with the session's password, keeping the last one read in RAM."""

    def __init__(self, directory: Path, password: Callable[[], str | None]) -> None:
        self.directory = directory
        self._password = password
        self._last: BackupContents | None = None
        self._generation = 0

    def read(self, name: str) -> BackupContents:
        """The contents of the named backup. Raises ValueError saying why it cannot be read."""
        if self._last is not None and self._last.name == name:
            return self._last
        if BACKUP_NAME.match(name) is None:
            raise ValueError(f"{name!r} is not a backup name")
        path = self.directory / name
        if not path.is_file():
            raise ValueError(f"the backup {name} does not exist")
        password = self._password()
        if password is None:
            raise ValueError("the vault is locked")
        generation = self._generation
        try:
            plaintext = decrypt_backup(path, password)
        except BackupError as error:
            if error.step == "decrypt":
                raise ValueError(
                    f"the backup {name} does not decrypt with the session's password"
                ) from None
            raise ValueError(f"the backup {name} cannot be read: {error.detail}") from None
        contents = BackupContents(name, plaintext)
        ## read runs in a thread: a clear or forget during the decrypt must not get the copy back
        if generation == self._generation:
            self._last = contents
        return contents

    def clear(self) -> None:
        self._generation += 1
        self._last = None

    def forget(self, name: str) -> None:
        """Drop the decrypted copy of the named backup, if it is the one in RAM."""
        self._generation += 1
        if self._last is not None and self._last.name == name:
            self._last = None
