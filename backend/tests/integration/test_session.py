"""Unlock and the login session against a real bw serve in the dev stack (app fixture)."""

import secrets

import pytest
from support.app import App, tool_notes
from support.cli import EMAIL, PASSWORD, SERVER, BwCli

from bwtidy.vault.tool_note import BACKUP_PASSWORD_FIELD, HIDDEN_FIELD

pytestmark = pytest.mark.integration


def test_login_unlocks_and_creates_the_tool_note(app: App, bw_cli: BwCli):
    response = app.login()
    assert response.status_code == 200
    logged_in = {"loggedIn": True, "server": SERVER, "email": EMAIL}
    unlocked = {"state": "unlocked", "mcpEnabled": False, "mcpToken": False, "bwLogin": logged_in}
    assert response.json() == unlocked
    assert app.client.get("/api/session").json() == unlocked
    assert app.session.cache.loaded
    key = app.session.session_key
    assert key
    ## the key from bw serve's unlock must work for the CLI, backups run bw export with it
    [note] = tool_notes(bw_cli, key)
    [field] = note["fields"]
    assert field["name"] == BACKUP_PASSWORD_FIELD
    assert field["type"] == HIDDEN_FIELD
    ## compared without assert ==, so a mismatch never prints the value
    same = secrets.compare_digest(field["value"].encode(), PASSWORD.encode())
    assert same, "backup-password is not the master password"


def test_second_login_keeps_one_tool_note(app: App, bw_cli: BwCli):
    assert app.login().status_code == 200
    first = app.session.session_key
    assert app.login().status_code == 200
    key = app.session.session_key
    assert key
    assert key != first
    assert len(tool_notes(bw_cli, key)) == 1


def test_logout_locks_the_vault(app: App, bw_cli: BwCli):
    assert app.login().status_code == 200
    key = app.session.session_key
    assert key
    assert bw_cli.status(session=key) == "unlocked"
    assert app.client.delete("/api/session").status_code == 204
    assert app.state() == "locked"
    assert app.session.session_key is None
    ## bw serve's lock also invalidates the session key for the CLI
    assert bw_cli.status(session=key) == "locked"
    assert bw_cli.status() == "locked"


def test_wrong_password_gives_401(app: App, bw_cli: BwCli):
    response = app.login("not-the-master-password")
    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong master password"}
    assert app.state() == "locked"
    assert bw_cli.status() == "locked"


def test_a_wrong_password_while_a_session_runs_keeps_it(app: App, bw_cli: BwCli):
    """The check against the running session's password hash runs the real scrypt."""
    assert app.login().status_code == 200
    key = app.session.session_key
    assert key
    response = app.login("not-the-master-password")
    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong master password"}
    assert app.state() == "unlocked"
    ## compared into a bool first, so a mismatch never prints the key
    kept = app.session.session_key == key
    assert kept
    assert bw_cli.status(session=key) == "unlocked"
    assert app.login().status_code == 200
    replaced = app.session.session_key != key
    assert replaced
    assert app.client.delete("/api/session").status_code == 204
