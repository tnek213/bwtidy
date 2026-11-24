"""A changed master password re-encrypts a real backup and updates the tool note.

The setup pretends the backups were made with an older password: it re-encrypts a fresh
backup with OTHER by hand and writes OTHER into backup-password through the CLI. The login
with the dev master password must then bring both back in line. Nothing here prints an item.
"""

import base64
import io
import json
import secrets
import zipfile
from pathlib import Path

import pytest
from support.app import App, tool_notes
from support.cli import EMAIL, PASSWORD, SERVER, BwCli

from bwtidy.backups.crypto import decrypt_backup, write_encrypted
from bwtidy.backups.files import BackupError
from bwtidy.vault.tool_note import (
    BACKUP_PASSWORD_FIELD,
    HIDDEN_FIELD,
    backup_password,
    find_tool_note,
)

pytestmark = pytest.mark.integration

## the pretend older master password, dev only
OTHER = "bwtidy-dev-password-0"
CORRUPT = "20200101T000000Z.zip.age"


def same(value: str, expected: str) -> bool:
    return secrets.compare_digest(value.encode(), expected.encode())


def set_field_by_cli(bw_cli: BwCli, note_id: str, password: str) -> None:
    key = bw_cli.unlock()
    try:
        item = bw_cli.json("get", "item", note_id, session=key)
        for field in item["fields"]:
            if field["name"] == BACKUP_PASSWORD_FIELD:
                field["value"] = password
        encoded = base64.b64encode(json.dumps(item).encode()).decode()
        bw_cli.run("edit", "item", note_id, encoded, session=key)
    finally:
        bw_cli.run("lock")


@pytest.fixture(scope="module")
def prepared(app: App, bw_cli: BwCli) -> dict:
    """A backup encrypted with OTHER, a corrupt file, and backup-password set to OTHER."""
    assert app.login().status_code == 200
    ## the login's background backup is in the way of the scenario, this takes one by hand
    app.settle()
    for path in (app.data_dir / "backups").iterdir():
        path.unlink()
    note = find_tool_note(app.session.cache, app.session.tool_note_id)
    assert note is not None
    response = app.client.post("/api/backups")
    assert response.status_code == 201, response.text
    name = response.json()["name"]
    directory = app.data_dir / "backups"
    plaintext = decrypt_backup(directory / name, PASSWORD)
    write_encrypted(directory, name, plaintext, OTHER)
    (directory / CORRUPT).write_bytes(b"not an age file")
    assert app.client.delete("/api/session").status_code == 204
    set_field_by_cli(bw_cli, note["id"], OTHER)
    return {"name": name, "note_id": note["id"], "directory": directory}


def test_login_reencrypts_the_backup_and_updates_the_field(app: App, bw_cli: BwCli, prepared: dict):
    response = app.login()
    assert response.status_code == 200
    assert response.json() == {
        "state": "unlocked",
        "mcpEnabled": False,
        "mcpToken": False,
        "bwLogin": {"loggedIn": True, "server": SERVER, "email": EMAIL},
        "backupReencryption": {"done": 1, "failed": [CORRUPT]},
    }
    assert app.client.get("/api/session").json()["backupReencryption"] == {
        "done": 1,
        "failed": [CORRUPT],
    }
    path: Path = prepared["directory"] / prepared["name"]
    with zipfile.ZipFile(io.BytesIO(decrypt_backup(path, PASSWORD))) as archive:
        assert archive.testzip() is None
        assert "raw/items.json" in archive.namelist()
    with pytest.raises(BackupError):
        decrypt_backup(path, OTHER)
    assert (prepared["directory"] / CORRUPT).read_bytes() == b"not an age file"
    ## the login started a backup of the vault as it is now (the field changed), nothing else
    app.settle()
    [*kept, fresh] = sorted(p.name for p in prepared["directory"].iterdir())
    assert kept == [CORRUPT, prepared["name"]]
    assert fresh > prepared["name"]
    assert decrypt_backup(prepared["directory"] / fresh, PASSWORD)

    key = app.session.session_key
    assert key
    [note] = tool_notes(bw_cli, key)
    assert note["id"] == prepared["note_id"]
    [field] = note["fields"]
    assert field["name"] == BACKUP_PASSWORD_FIELD
    assert field["type"] == HIDDEN_FIELD
    is_master = same(field["value"], PASSWORD)
    assert is_master, "backup-password is not the master password"
    ## the rest of the note went back untouched
    assert json.loads(note["notes"]) == {"version": 1, "templates": []}
    cached = find_tool_note(app.session.cache, app.session.tool_note_id)
    assert cached is not None
    cached_is_master = same(backup_password(cached) or "", PASSWORD)
    assert cached_is_master, "the cached note was not updated"


def test_a_second_login_finds_nothing_to_do(app: App, prepared: dict):
    response = app.login()
    assert response.status_code == 200
    assert response.json() == {
        "state": "unlocked",
        "mcpEnabled": False,
        "mcpToken": False,
        "bwLogin": {"loggedIn": True, "server": SERVER, "email": EMAIL},
    }
    path: Path = prepared["directory"] / prepared["name"]
    assert decrypt_backup(path, PASSWORD)
