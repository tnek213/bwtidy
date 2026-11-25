"""The `bwtidy` secure note in the personal vault (see docs/spec.md, The tool note)."""

import json
import logging
import sqlite3
from typing import Any

from bwtidy.bw import BwClient
from bwtidy.db import TOOL_NOTE_ID, Database
from bwtidy.sections import SECTIONS_VERSION, dump_sections
from bwtidy.vault.cache import VaultCache

log = logging.getLogger(__name__)

TOOL_NOTE_NAME = "bwtidy"
BACKUP_PASSWORD_FIELD = "backup-password"
## the master passwords before the newest that some backup may still be encrypted with
OLDER_PASSWORDS_FIELD = "older-backup-passwords"
SECURE_NOTE = 2
HIDDEN_FIELD = 1
NOT_PERSONAL = (
    "the bwtidy note is not a personal secure note any more, move it back to your personal vault"
)


class ToolNoteError(Exception):
    """The tool note cannot take the backup passwords, the message says why."""


def _personal_note(item: dict[str, Any]) -> bool:
    return item.get("type") == SECURE_NOTE and item.get("organizationId") is None


class ToolNoteId:
    """The tool note's id, in RAM and in the settings table, so a rename of the note cannot
    lose it (see find_tool_note).

    The lookups read and set last_found, the id they last found, kept across loads and
    sessions. keep sets it to the stored id while it holds none and writes
    back the id the lookups settled on when it changed: at the unlock (ensure_tool_note) and
    after every load of the vault, the only times another note can become the tool note.
    Without a database (tests) the id lives in RAM only. Only the id, never a vault value.
    """

    def __init__(self, db: Database | None = None) -> None:
        self._db = db
        ## the id as the settings table holds it, as far as this process knows
        self._stored: str | None = None
        self.last_found: str | None = None

    def keep(self, cache: VaultCache) -> None:
        if self.last_found is None:
            self.last_found = self._stored = self._read()
        found = tool_note_id(cache, self)
        if found is not None and found != self._stored:
            self._stored = found
            self._write(found)

    def _read(self) -> str | None:
        if self._db is None:
            return None
        try:
            return self._db.get_setting(TOOL_NOTE_ID)
        except (OSError, sqlite3.Error) as error:
            log.warning("Could not read the setting %s: %s", TOOL_NOTE_ID, error)
            return None

    def _write(self, value: str) -> None:
        if self._db is None:
            return
        try:
            self._db.set_setting(TOOL_NOTE_ID, value)
        except (OSError, sqlite3.Error) as error:
            log.warning("Could not save the setting %s: %s", TOOL_NOTE_ID, error)


def find_tool_note(cache: VaultCache, remembered: ToolNoteId) -> dict[str, Any] | None:
    """The tool note among the cached items, None when there is none.

    The remembered id comes first, whatever the note is named now, then the name. The id of
    the note found is remembered (last_found, and across sessions in the settings table), so a
    later rename (in Bitwarden or elsewhere) cannot lose it.
    """
    note = None
    last = remembered.last_found
    if last:
        note = next((i for i in cache.items if i.get("id") == last and _personal_note(i)), None)
    if note is None:
        note = next(
            (i for i in cache.items if i.get("name") == TOOL_NOTE_NAME and _personal_note(i)),
            None,
        )
    if note is not None and note.get("id"):
        remembered.last_found = note["id"]
    return note


def tool_note_id(cache: VaultCache, remembered: ToolNoteId) -> str | None:
    """The id of the tool note, None when the cache has none (or it has no id)."""
    note = find_tool_note(cache, remembered)
    return note.get("id") if note else None


def new_tool_note(password: str) -> dict[str, Any]:
    return {
        "organizationId": None,
        "collectionIds": None,
        "folderId": None,
        "type": SECURE_NOTE,
        "name": TOOL_NOTE_NAME,
        "notes": dump_sections({"version": SECTIONS_VERSION, "templates": []}),
        "favorite": False,
        "fields": [{"name": BACKUP_PASSWORD_FIELD, "value": password, "type": HIDDEN_FIELD}],
        "secureNote": {"type": 0},
        "reprompt": 0,
    }


async def ensure_tool_note(
    client: BwClient, cache: VaultCache, remembered: ToolNoteId, password: str
) -> dict[str, Any]:
    """Return the tool note from the loaded cache, creating it in bw serve when missing.

    A note is created only when neither the remembered id nor the name finds one. The id of
    the note is remembered either way.
    """
    remembered.keep(cache)
    note = find_tool_note(cache, remembered)
    if note is None:
        note = await client.create_item(new_tool_note(password))
        cache.items.append(note)
    remembered.keep(cache)
    return note


def backup_password(note: dict[str, Any]) -> str | None:
    """The value of the backup-password field, None when the note has no such field."""
    for field in note.get("fields") or []:
        if field.get("name") == BACKUP_PASSWORD_FIELD:
            return field.get("value")
    return None


def _older_passwords(note: dict[str, Any]) -> list[str]:
    for field in note.get("fields") or []:
        if field.get("name") == OLDER_PASSWORDS_FIELD:
            try:
                listed = json.loads(field.get("value") or "[]")
            except ValueError:
                return []
            if not isinstance(listed, list):
                return []
            return [value for value in listed if isinstance(value, str)]
    return []


def backup_passwords(note: dict[str, Any]) -> list[str]:
    """Every master password the backups may still be encrypted with, newest first.

    The newest is the backup-password field, the older ones (left by a re-encryption that was
    cut off) a JSON list in a second hidden field. A note with only today's single field reads
    as a list of one, a note without it as an empty list.
    """
    newest = backup_password(note)
    passwords = [] if newest is None else [newest]
    for password in _older_passwords(note):
        if password not in passwords:
            passwords.append(password)
    return passwords


def backup_fields_hidden(note: dict[str, Any]) -> bool:
    """Whether the fields a write of the list makes hidden are all hidden already.

    Those are the first backup-password field and every older-backup-passwords field. A second
    backup-password field the user made keeps its type, so it is not checked.
    """
    first = True
    for field in note.get("fields") or []:
        name = field.get("name")
        if name == BACKUP_PASSWORD_FIELD and first:
            first = False
            if field.get("type") != HIDDEN_FIELD:
                return False
        elif name == OLDER_PASSWORDS_FIELD and field.get("type") != HIDDEN_FIELD:
            return False
    return True


def with_backup_passwords(note: dict[str, Any], passwords: list[str]) -> dict[str, Any]:
    """A copy of the note with the list of backup passwords in its fields, newest first.

    The first goes into backup-password (added when missing), the rest into the field of older
    passwords, which is dropped when there are none. Both are hidden fields whatever type they
    had. Every other field stays as it was.
    """
    newest, *older = passwords
    fields: list[dict[str, Any]] = []
    found = False
    for field in note.get("fields") or []:
        name = field.get("name")
        if name == OLDER_PASSWORDS_FIELD:
            continue
        if name == BACKUP_PASSWORD_FIELD and not found:
            found = True
            fields.append({**field, "value": newest, "type": HIDDEN_FIELD})
        else:
            fields.append(dict(field))
    if not found:
        fields.append({"name": BACKUP_PASSWORD_FIELD, "value": newest, "type": HIDDEN_FIELD})
    if older:
        fields.append(
            {"name": OLDER_PASSWORDS_FIELD, "value": json.dumps(older), "type": HIDDEN_FIELD}
        )
    return {**note, "fields": fields}


async def set_backup_passwords(
    client: BwClient, cache: VaultCache, note_id: str, passwords: list[str]
) -> dict[str, Any]:
    """Write the list of backup passwords: GET the full note, change those fields, PUT it.

    The note in the cache is replaced by what bw serve answered. Raises ToolNoteError, and
    writes nothing, when the note is no personal secure note any more (moved into an
    organization since the cache load, say).
    """
    full = await client.get_item(note_id)
    if not _personal_note(full):
        raise ToolNoteError(NOT_PERSONAL)
    updated = await client.put_item(note_id, with_backup_passwords(full, passwords))
    if not isinstance(updated, dict):
        updated = with_backup_passwords(full, passwords)
    cache.replace_item(note_id, updated)
    return updated
