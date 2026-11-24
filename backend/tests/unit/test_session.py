"""The session of session.py (docs/spec.md, Session): the login, the heartbeat and the timeouts,
the logout, the limits of the session's end and of a login after a lock without an answer, and
the re-encryption of the backups when the backup password changed."""

import asyncio
import base64
import copy
import dataclasses
import functools
import json
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pyrage
import pytest

import bwtidy.backups.crypto as crypto
from bwtidy import session as session_module
from bwtidy.backups.collect import create_backup
from bwtidy.backups.crypto import decrypt_backup
from bwtidy.backups.files import BackupError
from bwtidy.config import HEARTBEAT_TIMEOUT, Policy
from bwtidy.db import HIDDEN_IDENTITY, Database
from bwtidy.protocol import DB_FILE
from bwtidy.protocol import SESSION_COOKIE as COOKIE
from bwtidy.vault.tool_note import backup_password, backup_passwords
from tests.support import fake_bw, login_env
from tests.support.app_env import AppEnv, session_app
from tests.support.fake_bw import PASSWORD, FakeBwServe
from tests.support.login_env import (
    BOUND,
    IDLE_EXIT,
    SHORT_LIMIT,
    CliEnv,
    queue_an_edit,
    stop_answering,
)
from tests.support.quiet import assert_absent
from tests.support.sample_vault import LOGIN

## what the fake bw serve's status says about the CLI
BW_LOGIN = {"loggedIn": True, "server": "https://vaultwarden", "email": "dev@bwtidy.test"}


@pytest.fixture
def env() -> Iterator[AppEnv]:
    with session_app() as env:
        yield env


def test_login_sets_cookie_and_unlocks(env: AppEnv):
    response = env.login()
    assert response.status_code == 200
    assert response.json() == {
        "state": "unlocked",
        "mcpEnabled": False,
        "mcpToken": False,
        "bwLogin": BW_LOGIN,
    }
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie
    assert "Max-Age" not in cookie
    assert "expires" not in cookie.lower()
    token = env.client.cookies[COOKIE]
    assert len(token) >= 43
    assert env.state() == "unlocked"
    assert env.bw.unlocked
    assert env.session.password == PASSWORD
    assert env.session.session_key == env.bw.session_key
    assert env.session.session_key is not None


def test_a_failing_unlock_hook_still_sets_the_cookie(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    def fail() -> None:
        raise OSError("/srv/backups/secret-path")

    monkeypatch.setattr(env.keeper, "start_background_backup", fail)
    with caplog.at_level(logging.WARNING, logger="bwtidy"):
        response = env.login()
    assert response.status_code == 200
    assert response.headers["set-cookie"].startswith(f"{COOKIE}=")
    assert env.state() == "unlocked"
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.name == "bwtidy.session" and r.levelno == logging.WARNING
    ]
    assert warnings == ["Login: the on_unlocked hook failed (OSError)"]
    assert_absent("secret-path", caplog.text, "the log")


def test_login_loads_the_vault(env: AppEnv):
    env.login()
    assert env.bw.calls("POST", "/sync") == 1
    assert env.session.cache.loaded
    assert env.session.cache.folders[0]["name"] == "Work"


def test_login_waits_for_the_items_bw_serve_lists_empty_after_a_sync(env: AppEnv):
    """The real bw serve lists no items for a moment after a sync, that must not make a
    second tool note."""
    env.bw.items.append(
        {"object": "item", "id": "note-9", "type": 2, "name": "bwtidy", "fields": []}
    )
    env.bw.empty_lists_after_sync = 3
    env.login()
    ## three empty listings, the settled one, then the trash listing on the same path
    assert env.bw.calls("GET", "/list/object/items") == 5
    assert env.bw.posted == []
    assert [i["id"] for i in env.session.cache.items] == ["note-9"]


def test_a_login_logs_its_steps_and_never_the_password(
    env: AppEnv, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.INFO, logger="bwtidy"):
        assert env.login().status_code == 200
    messages = [record.getMessage() for record in caplog.records]
    steps = [message for message in messages if message.startswith("Login: ")]
    assert steps == [
        "Login: started",
        "Login: bw serve locked",
        "Login: unlocked",
        "Login: synced",
        "Login: cache loaded",
        "Login: unlock checked",
        "Login: note ensured",
        "Login: backups checked",
    ]
    assert_absent(PASSWORD, caplog.text, "the log")


def test_wrong_password_gives_401(env: AppEnv):
    response = env.login("wrong")
    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong master password"}
    assert COOKIE not in env.client.cookies
    assert env.state() == "locked"


def test_no_cookie_is_locked_not_401(env: AppEnv):
    response = env.client.get("/api/session")
    assert response.status_code == 200
    assert response.json() == {
        "state": "locked",
        "mcpEnabled": False,
        "mcpToken": False,
        "bwLogin": BW_LOGIN,
    }


def test_invalid_cookie_is_locked(env: AppEnv):
    env.login()
    env.use_cookie("forged")
    assert env.state() == "locked"


def test_heartbeat_keeps_the_session_alive(env: AppEnv):
    env.login()
    for _ in range(10):
        env.clock.advance(100)
        assert env.client.post("/api/session/heartbeat").status_code == 204
        env.check_timeouts()
    assert env.state() == "unlocked"
    assert env.bw.unlocked


def test_heartbeat_without_session_gives_401(env: AppEnv):
    assert env.client.post("/api/session/heartbeat").status_code == 401


def test_121_seconds_without_heartbeat_ends_the_session(env: AppEnv):
    env.login()
    env.clock.advance(121)
    env.check_timeouts()
    assert not env.session.active
    assert not env.bw.unlocked
    assert env.session.password is None
    assert env.session.session_key is None
    assert not env.session.cache.loaded
    assert env.state() == "locked"
    assert env.client.post("/api/session/heartbeat").status_code == 401


def test_idle_for_two_hours_with_heartbeats_ends_the_session(env: AppEnv):
    env.login()
    for _ in range(7201 // 30):
        env.clock.advance(30)
        env.client.post("/api/session/heartbeat")
        env.check_timeouts()
    env.clock.advance(30)
    env.client.post("/api/session/heartbeat")
    env.check_timeouts()
    assert not env.session.active
    assert not env.bw.unlocked


def test_logout_locks(env: AppEnv):
    env.login()
    assert env.client.delete("/api/session").status_code == 204
    assert not env.bw.unlocked
    assert not env.session.active
    assert env.state() == "locked"


def test_logout_without_session_is_204(env: AppEnv):
    assert env.client.delete("/api/session").status_code == 204


def test_second_login_replaces_the_first_token(env: AppEnv):
    env.login()
    first = env.client.cookies[COOKIE]
    env.client.cookies.clear()
    env.login()
    second = env.client.cookies[COOKIE]
    assert first != second
    env.use_cookie(first)
    assert env.state() == "locked"
    env.use_cookie(second)
    assert env.state() == "unlocked"


def test_health_needs_no_session(env: AppEnv):
    assert env.client.get("/api/health").status_code == 200


def test_mcp_disabled_after_login(env: AppEnv):
    env.login()
    env.session.mcp_enabled = True
    assert env.client.get("/api/session").json()["mcpEnabled"] is True
    env.login()
    assert env.client.get("/api/session").json() == {
        "state": "unlocked",
        "mcpEnabled": False,
        "mcpToken": False,
        "bwLogin": BW_LOGIN,
    }


def test_bw_serve_down_gives_503():
    def refuse(request):
        raise httpx.ConnectError("refused")

    http = httpx.AsyncClient(
        base_url="http://127.0.0.1:8087", transport=httpx.MockTransport(refuse)
    )
    with session_app(http=http) as env:
        response = env.login()
    assert response.status_code == 503
    assert_absent(PASSWORD, response.text, "the response")


def test_restart_unlocks_again(env: AppEnv):
    env.login()
    first = env.session.session_key
    env.bw.unlocked = False
    env.call(env.session.restore_after_restart)
    assert env.bw.unlocked
    assert env.session.active
    assert env.session.session_key == env.bw.session_key != first


def test_the_heartbeat_timeout_comes_from_the_settings():
    with session_app(policy=Policy(heartbeat_timeout=300.0)) as env:
        env.login()
        env.clock.advance(299)
        env.check_timeouts()
        assert env.session.active
        env.clock.advance(1)
        env.check_timeouts()
        assert not env.session.active


def test_the_idle_timeout_comes_from_the_settings():
    with session_app(policy=Policy(idle_timeout=600.0)) as env:
        env.login()
        for _ in range(600 // 30 - 1):
            env.clock.advance(30)
            env.client.post("/api/session/heartbeat")
            env.check_timeouts()
        assert env.session.active
        env.clock.advance(30)
        env.client.post("/api/session/heartbeat")
        env.check_timeouts()
        assert not env.session.active


def record_ends(env: AppEnv) -> list[str]:
    """Stands in for the commit of the pending UI entries that runs before a session ends."""
    ends: list[str] = []

    async def before_end() -> None:
        ends.append("commit")

    env.session.hooks = dataclasses.replace(env.session.hooks, before_end=before_end)
    return ends


def test_a_wrong_password_while_a_session_runs_leaves_it_untouched(env: AppEnv):
    """A routing cookie and a guess must not end the session (docs/spec.md, Session and access)."""
    assert env.login().status_code == 200
    ends = record_ends(env)
    token = env.client.cookies[COOKIE]
    key = env.session.session_key
    locks, unlocks = env.bw.calls("POST", "/lock"), env.bw.calls("POST", "/unlock")
    env.client.cookies.clear()
    response = env.login("wrong")
    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong master password"}
    assert "set-cookie" not in response.headers
    assert ends == []
    assert env.bw.calls("POST", "/lock") == locks
    assert env.bw.calls("POST", "/unlock") == unlocks
    assert env.bw.unlocked
    assert env.session.session_key == key
    env.use_cookie(token)
    assert env.state() == "unlocked"
    assert env.client.get("/api/dummy").status_code == 200


def test_the_right_password_while_a_session_runs_replaces_it(env: AppEnv):
    assert env.login().status_code == 200
    ends = record_ends(env)
    first = env.client.cookies[COOKIE]
    env.client.cookies.clear()
    assert env.login().status_code == 200
    assert ends == ["commit"]
    assert env.client.cookies[COOKIE] != first
    assert env.state() == "unlocked"
    env.use_cookie(first)
    assert env.state() == "locked"


def test_after_three_wrong_passwords_each_try_waits_longer(env: AppEnv):
    assert env.login().status_code == 200
    env.client.cookies.clear()
    for _ in range(3):
        assert env.login("wrong").status_code == 401
    for wait in (1, 2, 4):
        response = env.login("wrong")
        assert response.status_code == 429
        assert response.headers["retry-after"] == str(wait)
        assert response.json() == {"detail": f"Too many wrong passwords. Try again in {wait} s."}
        ## the right password waits too
        assert env.login().status_code == 429
        env.clock.advance(wait - 0.5)
        assert env.login("wrong").status_code == 429
        env.clock.advance(0.5)
        assert env.login("wrong").status_code == 401
    for _ in range(10):
        response = env.login("wrong")
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) <= 60
        env.clock.advance(int(response.headers["retry-after"]))
        assert env.login("wrong").status_code == 401
    assert response.headers["retry-after"] == "60"


def test_the_right_password_resets_the_wait(env: AppEnv):
    assert env.login().status_code == 200
    env.client.cookies.clear()
    for _ in range(3):
        assert env.login("wrong").status_code == 401
    env.clock.advance(1)
    assert env.login().status_code == 200
    for _ in range(3):
        assert env.login("wrong").status_code == 401
    assert env.login("wrong").status_code == 429


## the re-encryption's tests are marked real_scrypt: they need real age files, the scrypt
## stand-in of conftest.py would test nothing there
OLD = "the password before the change"


NAMES = ["20260601T120000Z.zip.age", "20261026T120000Z.zip.age"]


UNLOCKED = {"state": "unlocked", "mcpEnabled": False, "mcpToken": False, "bwLogin": BW_LOGIN}


def tool_note(password: str) -> dict:
    return {
        "id": "tool",
        "object": "item",
        "type": 2,
        "name": "bwtidy",
        "notes": json.dumps({"version": 1, "templates": [{"name": "Computer"}]}),
        "favorite": False,
        "fields": [
            {"name": "note", "value": "keep me", "type": 0},
            {"name": "backup-password", "value": password, "type": 1},
        ],
        "secureNote": {"type": 0},
        "reprompt": 0,
        "collectionIds": [],
        "passwordHistory": [],
        "attachments": [],
        "revisionDate": "2026-10-26T13:08:59.304Z",
    }


def opens_with(env: AppEnv, name: str, password: str) -> bool:
    try:
        return decrypt_backup(env.settings.backup_dir / name, password) == name.encode()
    except BackupError:
        return False


def field_in_bw(env: AppEnv) -> str | None:
    return backup_password(env.bw.items[0])


## encrypted once per name, the scrypt work is what makes these tests slow
@functools.cache
def ciphertext(name: str) -> bytes:
    return pyrage.passphrase.encrypt(name.encode(), OLD)


def write_backups(directory: Path, names: list[str] = NAMES) -> None:
    directory.mkdir(parents=True)
    for name in names:
        (directory / name).write_bytes(ciphertext(name))


def make_env(tmp_path: Path, stored_password: str, apart: bool = False) -> Iterator[AppEnv]:
    """apart puts the backups in a directory of their own, outside the data directory."""
    bw = FakeBwServe([tool_note(stored_password)])
    data = tmp_path / "data" if apart else tmp_path
    data.mkdir(exist_ok=True)
    backups = tmp_path / "apart" if apart else tmp_path / "backups"
    write_backups(backups)
    with AppEnv(data, bw=bw, fake_backups=False, backup_dir=backups) as env:
        yield env


@pytest.fixture
def changed(tmp_path: Path) -> Iterator[AppEnv]:
    yield from make_env(tmp_path, OLD)


@pytest.fixture
def unchanged(tmp_path: Path) -> Iterator[AppEnv]:
    yield from make_env(tmp_path, PASSWORD)


@pytest.mark.real_scrypt
def test_a_changed_password_reencrypts_and_updates_the_field(changed: AppEnv):
    response = changed.login()
    assert response.status_code == 200
    assert response.json() == {**UNLOCKED, "backupReencryption": {"done": 2, "failed": []}}
    assert changed.session_status() == {**UNLOCKED, "backupReencryption": {"done": 2, "failed": []}}
    for name in NAMES:
        assert opens_with(changed, name, PASSWORD)
        assert not opens_with(changed, name, OLD)
    assert field_in_bw(changed) == PASSWORD
    assert changed.bw.calls("GET", "/object/item/tool") == 2
    [(first_id, first), (item_id, body)] = changed.bw.put
    assert first_id == item_id == "tool"
    ## the new password went first before anything was re-encrypted, the old one kept
    assert backup_passwords(first) == [PASSWORD, OLD]
    ## the full note went back with only the one field value changed (and the revisionDate of
    ## the first save)
    expected = tool_note(PASSWORD)
    assert {**body, "revisionDate": None} == {**expected, "revisionDate": None}
    assert backup_password(changed.session.cache.items[0]) == PASSWORD
    assert changed.session.cache.items[0]["revisionDate"] != expected["revisionDate"]


@pytest.mark.real_scrypt
def test_the_backups_in_a_directory_apart_from_the_data_are_reencrypted(tmp_path: Path):
    for changed in make_env(tmp_path, OLD, apart=True):
        response = changed.login()
        assert response.json()["backupReencryption"] == {"done": 2, "failed": []}
        for name in NAMES:
            assert opens_with(changed, name, PASSWORD)
        assert not (tmp_path / "data" / "backups").exists()


@pytest.mark.real_scrypt
def test_an_equal_password_triggers_nothing(unchanged: AppEnv):
    response = unchanged.login()
    assert response.status_code == 200
    assert response.json() == UNLOCKED
    assert unchanged.session_status() == UNLOCKED
    assert unchanged.bw.put == []
    assert unchanged.bw.calls("GET", "/object/item/tool") == 0
    ## the files were made with OLD and must not have been touched
    for name in NAMES:
        assert opens_with(unchanged, name, OLD)


@pytest.mark.real_scrypt
def test_an_equal_password_in_a_text_field_writes_the_fields_hidden(tmp_path: Path):
    note = tool_note(PASSWORD)
    note["fields"][1]["type"] = 0
    note["fields"].append({"name": "older-backup-passwords", "value": "[]", "type": 0})
    bw = FakeBwServe([note])
    write_backups(tmp_path / "backups")
    with AppEnv(tmp_path, bw=bw, fake_backups=False, backup_dir=tmp_path / "backups") as env:
        response = env.login()
        assert response.status_code == 200
        assert response.json() == UNLOCKED
        [(item_id, body)] = bw.put
        assert item_id == "tool"
        assert body["fields"] == tool_note(PASSWORD)["fields"]
        assert backup_passwords(env.session.cache.items[0]) == [PASSWORD]
        assert all(opens_with(env, name, OLD) for name in NAMES)


@pytest.mark.real_scrypt
def test_a_login_fails_when_the_note_moved_into_an_organization_meanwhile(
    changed: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    bw = changed.bw
    listed = bw.handler

    ## the cache load still lists the note as personal, the GET before the PUT finds it shared
    def handler(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == ("GET", "/object/item/tool"):
            bw.items[0]["organizationId"] = "org1"
        return listed(request)

    monkeypatch.setattr(bw, "handler", handler)
    response = changed.login()
    assert response.status_code == 409
    assert response.json() == {
        "detail": "the bwtidy note is not a personal secure note any more, "
        "move it back to your personal vault"
    }
    assert bw.put == []
    assert bw.items[0]["fields"] == tool_note(OLD)["fields"]
    assert changed.session_status()["state"] == "locked"
    assert all(opens_with(changed, name, OLD) for name in NAMES)


@pytest.mark.real_scrypt
def test_the_report_ends_with_the_session_and_a_second_login_is_quiet(changed: AppEnv):
    changed.login()
    assert changed.client.delete("/api/session").status_code == 204
    assert changed.session.backup_reencryption is None
    assert changed.login().json() == UNLOCKED
    assert len(changed.bw.put) == 2


@pytest.mark.real_scrypt
def test_an_unreadable_backup_is_named_and_the_field_still_updated(changed: AppEnv):
    corrupt = "20200101T000000Z.zip.age"
    (changed.settings.backup_dir / corrupt).write_bytes(b"not an age file")
    response = changed.login()
    assert response.json()["backupReencryption"] == {"done": 2, "failed": [corrupt]}
    assert (changed.settings.backup_dir / corrupt).read_bytes() == b"not an age file"
    assert field_in_bw(changed) == PASSWORD


@pytest.mark.real_scrypt
def test_a_run_that_stops_leaves_the_field_and_reports_nothing(
    changed: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    def refuse(fd: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("os.fsync", refuse)
    response = changed.login()
    assert response.status_code == 200
    assert response.json() == UNLOCKED
    assert len(changed.bw.put) == 1
    assert backup_passwords(changed.bw.items[0]) == [PASSWORD, OLD]
    assert all(opens_with(changed, name, OLD) for name in NAMES)


@pytest.mark.real_scrypt
def test_a_missing_field_is_added_and_every_backup_reported(tmp_path: Path):
    note = tool_note(OLD)
    note["fields"] = [note["fields"][0]]
    bw = FakeBwServe([note])
    write_backups(tmp_path / "backups", NAMES[:1])
    with AppEnv(tmp_path, bw=bw, fake_backups=False) as env:
        response = env.login()
    assert response.json()["backupReencryption"] == {"done": 0, "failed": [NAMES[0]]}
    assert [f["name"] for f in bw.items[0]["fields"]] == ["note", "backup-password"]
    assert backup_password(bw.items[0]) == PASSWORD


def stored_identity(data_dir: Path) -> bytes:
    db = Database(data_dir / DB_FILE)
    try:
        stored = db.get_setting(HIDDEN_IDENTITY)
    finally:
        db.close()
    assert stored is not None
    return base64.b64decode(stored)


@pytest.mark.real_scrypt
def test_a_changed_password_reencrypts_the_identity_for_hidden_values(tmp_path: Path):
    """The identity queued hidden values are encrypted to opens with the new password after."""
    identity = pyrage.x25519.Identity.generate()
    db = Database(tmp_path / DB_FILE)
    db.set_setting(
        HIDDEN_IDENTITY,
        base64.b64encode(pyrage.passphrase.encrypt(str(identity).encode(), OLD)).decode(),
    )
    db.close()
    for env in make_env(tmp_path, OLD):
        assert env.login().status_code == 200
        hidden = env.session.hidden
        assert hidden is not None
        assert hidden.recipient == str(identity.to_public())
        ciphertext = stored_identity(tmp_path)
        with pytest.raises(pyrage.DecryptError):
            pyrage.passphrase.decrypt(ciphertext, OLD)
        assert pyrage.passphrase.decrypt(ciphertext, PASSWORD) == str(identity).encode()
        ## a value encrypted before the change is still readable
        sealed = pyrage.encrypt(b"hidden", [identity.to_public()], armored=True).decode()
        assert hidden.decrypt(sealed) == "hidden"
        assert env.client.delete("/api/session").status_code == 204
        assert not hidden.unlocked


@pytest.mark.real_scrypt
def test_the_first_unlock_creates_the_identity_and_the_next_one_reads_it(unchanged: AppEnv):
    assert unchanged.login().status_code == 200
    hidden = unchanged.session.hidden
    assert hidden is not None
    first = hidden.recipient
    assert first is not None
    ciphertext = stored_identity(unchanged.settings.backup_dir.parent)
    assert pyrage.passphrase.decrypt(ciphertext, PASSWORD).startswith(b"AGE-SECRET-KEY-1")
    assert unchanged.login().status_code == 200
    assert hidden.recipient == first
    assert stored_identity(unchanged.settings.backup_dir.parent) == ciphertext


## the master password of a change from OLD whose re-encryption was cut off, before PASSWORD
BETWEEN = "the password of the cut off change"


def store_identity(data_dir: Path, identity: pyrage.x25519.Identity, password: str) -> None:
    db = Database(data_dir / DB_FILE)
    try:
        sealed = pyrage.passphrase.encrypt(str(identity).encode(), password)
        db.set_setting(HIDDEN_IDENTITY, base64.b64encode(sealed).decode())
    finally:
        db.close()


def login_with(env: AppEnv, monkeypatch: pytest.MonkeyPatch, password: str):
    monkeypatch.setattr(fake_bw, "PASSWORD", password)
    response = env.client.post("/api/session", json={"password": password})
    assert response.status_code == 200
    return response


def cut_after(monkeypatch: pytest.MonkeyPatch, files: int) -> None:
    """The next re-encryption stops after this many files, like a full disk."""
    write = crypto.write_encrypted
    written = 0

    def write_one(directory: Path, name: str, plaintext: bytes, password: str) -> Path:
        nonlocal written
        written += 1
        if written > files:
            raise BackupError("write", "No space left on device")
        return write(directory, name, plaintext, password)

    monkeypatch.setattr(crypto, "write_encrypted", write_one)


@pytest.mark.real_scrypt
def test_a_cut_off_change_and_the_next_one_leave_every_backup_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """OLD to BETWEEN cut off halfway, then PASSWORD: the next login brings everything along."""
    identity = pyrage.x25519.Identity.generate()
    store_identity(tmp_path, identity, OLD)
    for env in make_env(tmp_path, OLD):
        with monkeypatch.context() as cut:
            cut_after(cut, 1)
            assert "backupReencryption" not in login_with(env, cut, BETWEEN).json()
        assert backup_passwords(env.bw.items[0]) == [BETWEEN, OLD]
        assert opens_with(env, NAMES[0], BETWEEN)
        assert opens_with(env, NAMES[1], OLD)
        assert env.client.delete("/api/session").status_code == 204

        response = login_with(env, monkeypatch, PASSWORD)
        assert response.json()["backupReencryption"] == {"done": 2, "failed": []}
        assert all(opens_with(env, name, PASSWORD) for name in NAMES)
        assert backup_passwords(env.bw.items[0]) == [PASSWORD]
        assert [f["name"] for f in env.bw.items[0]["fields"]] == ["note", "backup-password"]
        assert backup_passwords(env.session.cache.items[0]) == [PASSWORD]
        hidden = env.session.hidden
        assert hidden is not None
        assert hidden.recipient == str(identity.to_public())
        sealed = stored_identity(tmp_path)
        assert pyrage.passphrase.decrypt(sealed, PASSWORD) == str(identity).encode()


@pytest.mark.real_scrypt
def test_a_run_cut_off_again_keeps_every_password_still_needed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    newest = "20261101T120000Z.zip.age"
    names = [*NAMES, newest]
    for env in make_env(tmp_path, OLD):
        (env.settings.backup_dir / newest).write_bytes(ciphertext(newest))
        with monkeypatch.context() as cut:
            cut_after(cut, 1)
            login_with(env, cut, BETWEEN)
        assert env.client.delete("/api/session").status_code == 204
        with monkeypatch.context() as cut:
            cut_after(cut, 0)
            login_with(env, cut, PASSWORD)
        assert backup_passwords(env.bw.items[0]) == [PASSWORD, BETWEEN, OLD]
        assert opens_with(env, names[0], BETWEEN)
        assert all(opens_with(env, name, OLD) for name in names[1:])
        assert env.client.delete("/api/session").status_code == 204

        response = login_with(env, monkeypatch, PASSWORD)
        assert response.json()["backupReencryption"] == {"done": 3, "failed": []}
        assert all(opens_with(env, name, PASSWORD) for name in names)
        assert backup_passwords(env.bw.items[0]) == [PASSWORD]


@pytest.mark.real_scrypt
def test_the_identity_opens_with_any_listed_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An identity still under the oldest listed password is found and stored anew."""
    identity = pyrage.x25519.Identity.generate()
    store_identity(tmp_path, identity, OLD)
    for env in make_env(tmp_path, BETWEEN):
        note = env.bw.items[0]
        note["fields"].append(
            {"name": "older-backup-passwords", "value": json.dumps([OLD]), "type": 1}
        )
        login_with(env, monkeypatch, PASSWORD)
        hidden = env.session.hidden
        assert hidden is not None
        assert hidden.recipient == str(identity.to_public())
        assert pyrage.passphrase.decrypt(stored_identity(tmp_path), PASSWORD)
        assert backup_passwords(env.bw.items[0]) == [PASSWORD]


def bounded(work: Callable[[], Awaitable[Any]]) -> Callable[[], Awaitable[Any]]:
    async def run() -> Any:
        return await asyncio.wait_for(work(), BOUND)

    return run


@pytest.fixture
def with_login(tmp_path: Path) -> Iterator[CliEnv]:
    for env in login_env.make_env(tmp_path, exit_after_session=True, idle_exit=IDLE_EXIT):
        env.bw.items = [copy.deepcopy(LOGIN)]
        assert env.login().status_code == 200
        yield env
        if env.bw.answering is not None:
            env.call(env.bw.answering.set)


@pytest.mark.usefixtures("short_limits")
def test_a_session_end_whose_commit_gets_no_answer_gives_up_and_the_worker_exits(
    with_login: CliEnv, caplog: pytest.LogCaptureFixture
):
    env = with_login
    caplog.set_level(logging.INFO)
    entry_id = queue_an_edit(env)
    stop_answering(env)
    env.clock.advance(HEARTBEAT_TIMEOUT + 1)
    env.call(bounded(env.app.state.bw.session.check_timeouts))
    assert env.exits == [True]
    assert not env.app.state.bw.session.active
    assert env.app.state.bw.queue.pending_ui_ids() == [entry_id]
    messages = [record.getMessage() for record in caplog.records]
    assert "Session end: started, timeout" in messages
    assert any(m.startswith("Session end: commit given up after") for m in messages)
    assert any(m.startswith("Locking bw serve gave no answer") for m in messages)
    assert "The session ended, exit requested" in messages
    assert_absent("someone-else", caplog.text, "the log")


@pytest.mark.usefixtures("short_limits")
def test_a_logout_whose_lock_gets_no_answer_still_ends_the_session_and_exits(with_login: CliEnv):
    env = with_login
    answering = stop_answering(env)
    with ThreadPoolExecutor(max_workers=1) as pool:
        logout = pool.submit(env.client.delete, "/api/session")
        try:
            assert logout.result(BOUND).status_code == 204
        finally:
            if not logout.done():
                env.call(answering.set)
    assert env.exits == [True]
    assert not env.app.state.bw.session.active


SLOW_BW = """#!{python}
import io, signal, sys, time, zipfile
from pathlib import Path

marks = Path({marks!r})


def mark(text):
    with marks.open("a") as file:
        file.write(text + "\\n")


def term(signum, frame):
    mark("SIGTERM")
    sys.exit(143)


signal.signal(signal.SIGTERM, term)
if "export" in sys.argv:
    mark("export started")
    time.sleep({seconds})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data.json", "{{}}")
    mark("export ended")
    sys.stdout.buffer.write(buffer.getvalue())
else:
    print("[]")
"""


def write_slow_bw(tmp_path: Path, marks: Path, seconds: float) -> Path:
    """A bw whose export takes its time and notes in marks when it starts, ends or gets SIGTERM."""
    path = tmp_path / "slow-bw"
    path.write_text(SLOW_BW.format(python=sys.executable, marks=str(marks), seconds=seconds))
    path.chmod(0o755)
    return path


def read_marks(marks: Path) -> str:
    return marks.read_text() if marks.exists() else ""


def start_a_slow_backup(env: CliEnv, tmp_path: Path, seconds: float) -> Path:
    """Start a background backup whose bw export runs seconds, answer its marks file once the
    export runs."""
    marks = tmp_path / "marks"
    keeper = env.app.state.bw.backups
    keeper.bw_bin = str(write_slow_bw(tmp_path, marks, seconds))
    keeper.backup = create_backup
    env.call(keeper.start_background_backup)
    deadline = time.monotonic() + BOUND
    while "export started" not in read_marks(marks) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "export started" in read_marks(marks)
    return marks


def test_a_session_end_waits_for_the_background_backups_bw_and_sends_it_no_sigterm(
    with_login: CliEnv,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """A bw cut off in its write can leave the CLI's lock file behind, which holds up the next
    POST /lock for 10 s (see Worker mode)."""
    env = with_login
    caplog.set_level(logging.INFO)
    marks_at_lock: list[str] = []
    handle = env.bw.handler
    marks = start_a_slow_backup(env, tmp_path, seconds=1.0)

    def note_the_lock(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == ("POST", "/lock"):
            marks_at_lock.append(read_marks(marks))
        return handle(request)

    monkeypatch.setattr(env.bw, "handler", note_the_lock)

    assert env.client.delete("/api/session").status_code == 204

    assert "SIGTERM" not in read_marks(marks)
    assert "export ended" in read_marks(marks)
    ## the lock came only once that bw had ended
    assert ["export ended" in text for text in marks_at_lock] == [True]
    messages = [record.getMessage() for record in caplog.records]
    assert "Background backup stopped between its steps, before the trash listing" in messages
    assert any(m.startswith("Session end: background backup ended, waited") for m in messages)
    assert "Background backup cancelled" not in messages
    assert env.exits == [True]


def test_a_session_end_cancels_a_background_backup_still_running_after_the_limit(
    with_login: CliEnv,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr(session_module, "BACKUP_STOP_LIMIT", SHORT_LIMIT, raising=False)
    env = with_login
    caplog.set_level(logging.INFO)
    marks = start_a_slow_backup(env, tmp_path, seconds=BOUND * 3)
    started = time.monotonic()

    assert env.client.delete("/api/session").status_code == 204

    assert time.monotonic() - started < BOUND
    assert env.exits == [True]
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        m.startswith("Session end: the background backup did not end within") for m in messages
    )
    assert "Background backup cancelled" in messages
    deadline = time.monotonic() + BOUND
    while "SIGTERM" not in read_marks(marks) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "SIGTERM" in read_marks(marks)


def last(env: CliEnv, request: tuple[str, str]) -> int:
    """The index of bw serve's last such request (a late lock counts when it lands)."""
    return len(env.bw.requests) - 1 - env.bw.requests[::-1].index(request)


@pytest.mark.usefixtures("short_limits")
def test_a_login_after_a_lock_without_an_answer_unlocks_once_that_lock_landed(
    with_login: CliEnv, caplog: pytest.LogCaptureFixture
):
    """bw serve still runs a lock the session's end gave up on, and it must not land right
    after the new login's unlock and lock the vault again."""
    env = with_login
    caplog.set_level(logging.INFO)
    env.bw.lock_delay = SHORT_LIMIT * 3

    assert env.login().status_code == 200

    assert last(env, ("POST", "/lock")) < last(env, ("POST", "/unlock"))
    ## no lock is left that could still land
    time.sleep(env.bw.lock_delay)
    assert env.bw.unlocked
    assert env.client.get("/api/grid").status_code == 200
    messages = [record.getMessage() for record in caplog.records]
    assert any(m.startswith("Locking bw serve gave no answer") for m in messages)
    assert any(m.startswith("Login: the lock of bw serve ended, waited") for m in messages)


@pytest.mark.usefixtures("short_limits")
def test_a_login_unlocks_nothing_while_a_lock_without_an_answer_can_still_land(
    with_login: CliEnv, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(session_module, "LATE_LOCK_LIMIT", SHORT_LIMIT, raising=False)
    env = with_login
    env.bw.lock_delay = SHORT_LIMIT * 4
    unlocks = env.bw.calls("POST", "/unlock")

    response = env.login()

    assert response.status_code == 503
    assert response.json()["detail"] == "bw serve has not finished locking the vault, try again"
    assert env.bw.calls("POST", "/unlock") == unlocks
    assert not env.app.state.bw.session.active
    ## once that lock landed, the next login unlocks
    time.sleep(env.bw.lock_delay)
    env.bw.lock_delay = 0.0
    assert env.login().status_code == 200
    assert env.bw.unlocked


def test_a_login_whose_vault_locks_again_during_the_cache_load_fails(
    with_login: CliEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """A lock that lands during the cache load must not leave a session that answers 200 on a
    locked bw serve."""
    env = with_login
    caplog.set_level(logging.INFO)
    session = env.app.state.bw.session
    load = session.cache.load

    async def load_then_lock() -> None:
        await load()
        env.bw.unlocked = False

    monkeypatch.setattr(session.cache, "load", load_then_lock)

    response = env.login()

    assert response.status_code == 502
    assert (
        response.json()["detail"] == "bw serve locked the vault again during the login, try again"
    )
    assert not session.active
    messages = [record.getMessage() for record in caplog.records]
    assert "Login: unlock check failed, bw serve does not report the vault unlocked" in messages
    assert env.client.get("/api/session").json()["state"] == "locked"
