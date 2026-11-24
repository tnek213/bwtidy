"""Helpers for tests that enqueue, list and commit review queue entries."""

from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from bwtidy.protocol import DB_FILE
from bwtidy.queue.autocommit import AutoCommit
from bwtidy.queue.state import as_operation
from tests.support.app_env import AppEnv


def enqueue_dict(env: AppEnv, op: dict[str, Any], summary: str, source: str = "mcp") -> int:
    return env.queue.enqueue(as_operation(op), summary, source)


def listing(env: AppEnv, status: str | None = None) -> list[dict[str, Any]]:
    params = {} if status is None else {"status": status}
    response = env.client.get("/api/queue", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def approve(env: AppEnv, *ids: int):
    response = env.client.post("/api/queue/approve", json={"ids": list(ids)})
    env.settle()
    return response


def enqueue_ui(env: AppEnv, *operations: dict[str, Any]):
    return env.client.post("/api/queue", json={"operations": list(operations)})


def commit_now(env: AppEnv):
    response = env.client.post("/api/queue/commit")
    env.settle()
    return response


def vault_unreachable(env: AppEnv, unreachable: bool) -> None:
    """A commit that cannot run: the write path's sync fails, so the entries stay pending."""
    if unreachable:
        env.bw.fail_at.add(("POST", "/sync"))
    else:
        env.bw.fail_at.discard(("POST", "/sync"))


CHANGED = "2026-10-26T16:00:00.000Z"


LOGIN_USERNAME = "dev@mail.example.test"


def quick_auto_commit(env: AppEnv) -> None:
    """Commit after 10 s like the e2e stacks: the default 180 s outlasts the heartbeat timeout."""
    auto_commit(env).configure(10, 20)


def entry(env: AppEnv, entry_id: int) -> dict[str, Any]:
    return next(e for e in listing(env) if e["id"] == entry_id)


## the least that parses, for every kind of the Operation union
MINIMAL_OPERATIONS: dict[str, dict[str, Any]] = {
    "setLeaf": {"itemId": "login-1", "revisionDate": "", "column": "name", "value": "x"},
    "removeLeaf": {"itemId": "login-1", "revisionDate": "", "column": "notes"},
    "generateLeaf": {"itemId": "login-1", "revisionDate": "", "column": "login.password"},
    "moveToFolder": {"itemIds": ["login-1"], "revisionDates": {}, "folderName": "Work"},
    "setCollections": {"itemId": "login-1", "revisionDate": "", "collectionNames": []},
    "moveToOrganization": {
        "itemId": "login-1",
        "revisionDate": "",
        "organizationName": "Org",
        "collectionNames": [],
    },
    "moveToPersonal": {"itemId": "login-1", "revisionDate": ""},
    "trashItem": {"itemId": "login-1", "revisionDate": ""},
    "convertItem": {"itemId": "login-1", "revisionDate": "", "newType": 2, "mapping": {}},
    "applyTemplate": {"itemId": "login-1", "revisionDate": "", "template": "T", "mapping": {}},
    "renameField": {"from": "a", "to": "b", "items": []},
    "mergeColumns": {"sources": ["fields.a"], "target": "fields.b", "items": []},
    "mergeItems": {
        "fromItemId": "login-1",
        "fromRevisionDate": "",
        "toItemId": "login-2",
        "toRevisionDate": "",
        "mapping": {},
    },
    "setUris": {"items": []},
    "restoreItems": {"backup": "b.zip", "itemIds": ["login-1"], "revisionDates": {}},
    "createItem": {"name": "New", "type": 1},
    "renameFolder": {"folderId": "f1", "shownName": "Work", "newName": "Job"},
    "deleteFolder": {"folderId": "f1", "shownName": "Work"},
    "restoreFolder": {"backup": "b.zip", "folderId": "f1"},
    "renameAttachment": {
        "itemId": "login-1",
        "revisionDate": "",
        "attachmentId": "a1",
        "fileName": "new.txt",
        "shownFileName": "codes.txt",
    },
    "setToolNoteSection": {"section": "templates", "changes": []},
}


def raw_sqlite(tmp_path: Path) -> bytes:
    raw = b"".join(p.read_bytes() for p in tmp_path.glob(f"{DB_FILE}*"))
    assert raw
    return raw


HIDDEN_VALUE = "queued-hidden-value-7731"


def auto_commit(env: AppEnv) -> AutoCommit:
    return env.app.state.bw.auto_commit


def commit_if_due(env: AppEnv) -> dict[str, Any] | None:
    assert env.client.portal is not None
    outcome = env.client.portal.call(auto_commit(env).commit_if_due)
    env.settle()
    return outcome


def status(env: AppEnv) -> dict[str, Any]:
    response = env.client.get("/api/queue/auto-commit")
    assert response.status_code == 200, response.text
    return response.json()


def statuses(env: AppEnv, *ids: int) -> list[str]:
    return [entry(env, i)["status"] for i in ids]


def hidden_setlers(env: AppEnv) -> list[dict[str, Any]]:
    return [
        env.set_leaf("login-1", "login.password", HIDDEN_VALUE),
        env.set_leaf("note-1", "fields.Recovery", HIDDEN_VALUE + "-b", fieldType=1),
    ]


COMPUTER = {
    "name": "Computer",
    "type": 2,
    "coordinates": [{"kind": "field", "name": "user", "fieldType": 0}],
}


def before_first(env: AppEnv, monkeypatch: pytest.MonkeyPatch, method: str, step: Any) -> None:
    """Run step(client) once, right before the write path's method first runs, so it asks
    the API in the middle of a commit."""
    original = getattr(env.writes, method)
    done = False

    async def hooked(*args: Any) -> Any:
        nonlocal done
        if not done:
            done = True
            transport = httpx.ASGITransport(app=env.app)
            cookies = dict(env.client.cookies)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver", cookies=cookies
            ) as client:
                await step(client)
        return await original(*args)

    monkeypatch.setattr(env.writes, method, hooked)


def before_the_reload(env: AppEnv, monkeypatch: pytest.MonkeyPatch, step: Any) -> None:
    """Run step(client) once, at the first reload after a commit's writes, so it asks the API
    while the entries are already applied and the cache was not loaded again yet."""
    before_first(env, monkeypatch, "_reload", step)


def retry(env: AppEnv, *ids: int, overwrite: bool = False):
    response = env.client.post("/api/queue/retry", json={"ids": list(ids), "overwrite": overwrite})
    env.settle()
    return response


def pending_ui_edit(env: AppEnv) -> int:
    operation = env.set_leaf("login-1", "login.username", "queued@x.test")
    response = env.client.post("/api/queue", json={"operations": [operation]})
    assert response.status_code == 201, response.text
    return response.json()["ids"][0]
