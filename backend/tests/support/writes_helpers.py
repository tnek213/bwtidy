"""Helpers for tests of the write path: other devices' edits, backups and operations."""

import copy
import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2 as httpx

from bwtidy.backups.files import BackupError
from tests.support.app_env import AppEnv
from tests.support.fake_bw import PASSWORD
from tests.support.sample_vault import LOGIN

SYNC = ("POST", "/sync")


def plain_decrypt(path: Path, password: str) -> bytes:
    """Stands in for decrypt_backup: the files are plain zips, the password is still checked.

    A real scrypt decrypt takes over a second, too slow for one per test. The real round trip
    is covered by test_crypto.py and the integration tests.
    """
    if password != PASSWORD:
        raise BackupError("decrypt", "wrong password or damaged file")
    return path.read_bytes()


PIN_SECRET = next(f["value"] for f in LOGIN["fields"] if f["name"] == "PIN")
PHONE = "phone@other.device"
PHONE_REVISION = "2026-10-26T16:30:00.000Z"


def edit_at_sync(env: AppEnv, number: int, item_id: str) -> None:
    """The sync with this number, counted from now, brings in another device's username edit.

    Sync 1 is the write path's own at the start, sync 2 the one after the backup, sync 3 the
    one a createItem runs.
    """
    real = env.bw.handler
    seen = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == SYNC:
            seen[0] += 1
            if seen[0] == number:
                item = env.item(item_id)
                item["login"] = {**item["login"], "username": PHONE}
                item["revisionDate"] = PHONE_REVISION
        return real(request)

    ## slow_handler looks the handler up on the instance
    vars(env.bw)["handler"] = handler


def change_on_server_at_put(env: AppEnv, item_id: str) -> None:
    """Right before the first PUT of the item, another device edits its username on the
    server, after bw serve's last sync, so the PUT goes out on bw serve's stale copy."""
    real = env.bw.handler
    path = f"/object/item/{item_id}"

    def handler(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == ("PUT", path) and not env.bw.calls("PUT", path):
            item = copy.deepcopy(env.item(item_id))
            item["login"] = {**item["login"], "username": PHONE}
            env.bw.change_on_server({**item, "revisionDate": PHONE_REVISION})
        return real(request)

    vars(env.bw)["handler"] = handler


def store_templates(env: AppEnv, *templates: dict[str, Any]) -> None:
    """Write templates straight into the fake vault's tool note and load the vault again."""
    note = next(i for i in env.bw.items if i.get("name") == "bwtidy")
    note["notes"] = json.dumps({"version": 1, "templates": list(templates)})
    env.reload()


def create(**fields: Any) -> dict[str, Any]:
    return {"kind": "createItem", **fields}


def raw_sqlite_text(env: AppEnv) -> str:
    return b"".join(p.read_bytes() for p in env.data_dir.glob("*.sqlite*")).decode(
        "utf-8", "replace"
    )


def without_members(plain: bytes, dropped: Callable[[str], bool]) -> bytes:
    """The backup zip without the members dropped says to leave out."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(plain)) as source, zipfile.ZipFile(buffer, "w") as target:
        for member in source.namelist():
            if not dropped(member):
                target.writestr(member, source.read(member))
    return buffer.getvalue()


def give_note_attachments(env: AppEnv) -> None:
    """Two attachments on the organization note, so a move has something to copy."""
    env.item("note-1")["attachments"] = [
        {"id": "a7", "fileName": "config.bin", "size": "140"},
        {"id": "a8", "fileName": "notes.txt", "size": "60"},
    ]
    env.bw.attachments[("note-1", "a7")] = b"binary config"
    env.bw.attachments[("note-1", "a8")] = b"plain notes"


def moved_elsewhere(env: AppEnv, item_id: str, **partial: Any) -> None:
    """Another device's partial update: folder, favorite or collections, the revisionDate
    stays as it is (docs/spec.md, Backups, the fingerprint)."""
    env.item(item_id).update(partial)


def move(env: AppEnv, item_id: str, folder: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": "moveToFolder",
        "itemIds": [item_id],
        "revisionDates": {item_id: env.revisions()[item_id]},
        "folderName": folder,
        **extra,
    }


def shown_conflict(env: AppEnv, item_id: str, column: str) -> dict[str, Any]:
    revision = env.item(item_id)["revisionDate"]
    return {"itemId": item_id, "expected": revision, "actual": revision, "column": column}


def uris_op(env: AppEnv, *uris: tuple[str, int | None], **extra: Any) -> dict[str, Any]:
    """A setUris of login-1, shown its URIs as the fake bw serve holds them now."""
    shown = [{"uri": u["uri"], "match": u["match"]} for u in env.item("login-1")["login"]["uris"]]
    entry = {
        "itemId": "login-1",
        "revisionDate": env.revisions()["login-1"],
        "shownUris": shown,
        "uris": [{"uri": uri, "match": match} for uri, match in uris],
        **extra,
    }
    return {"kind": "setUris", "items": [entry]}
