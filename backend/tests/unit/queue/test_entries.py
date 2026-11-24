"""The queue's rows against the fake bw serve: the listing, reject, dismiss, the expiry of
failed entries and what a start settles."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bwtidy.items.coordinates import MASKED
from tests.support.app_env import AppEnv
from tests.support.queue_helpers import (
    CHANGED,
    HIDDEN_VALUE,
    approve,
    commit_now,
    enqueue_dict,
    enqueue_ui,
    entry,
    hidden_setlers,
    listing,
    raw_sqlite,
    retry,
    vault_unreachable,
)
from tests.support.quiet import assert_absent, assert_present


def test_enqueue_and_list(env: AppEnv):
    op = env.set_leaf("login-1", "name", "Mail")
    entry_id = enqueue_dict(env, op, "Shorter name")
    [listed_entry] = listing(env)
    assert listed_entry["id"] == entry_id
    assert listed_entry["source"] == "mcp"
    assert listed_entry["summary"] == "Shorter name"
    assert listed_entry["status"] == "pending"
    assert listed_entry["createdAt"].startswith("2")
    assert listed_entry["operation"] == {**op, "fieldType": None, "shownFavorite": None}
    assert listed_entry["result"] is None
    assert listed_entry["diff"] == [
        {
            "itemId": "login-1",
            "itemName": "Example Mail",
            "column": "name",
            "before": "Example Mail",
            "after": "Mail",
        }
    ]
    assert listing(env, "pending") == [listed_entry]
    assert listing(env, "applied") == []
    assert env.client.get("/api/queue", params={"status": "maybe"}).status_code == 422
    ## queueing writes nothing
    assert env.bw.put == []
    assert env.backups == [env.login_backup]


def test_the_queue_needs_a_session(env: AppEnv):
    env.client.cookies.clear()
    assert env.client.get("/api/queue").status_code == 401
    assert approve(env, 1).status_code == 401
    assert env.client.post("/api/queue/reject", json={"ids": [1]}).status_code == 401


def test_reject(env: AppEnv):
    keep = enqueue_dict(env, env.set_leaf("login-1", "name", "Mail"), "rename mail")
    drop = enqueue_dict(env, env.op("trashItem", "card-1"), "old card")
    response = env.client.post("/api/queue/reject", json={"ids": [drop]})
    assert response.status_code == 200
    assert response.json() == {"rejected": [drop]}
    rejected = entry(env, drop)
    assert rejected["status"] == "rejected"
    assert rejected["decidedAt"]
    assert rejected["diff"] == [
        {
            "itemId": "card-1",
            "itemName": "Visa debit",
            "column": "trash",
            "before": False,
            "after": True,
        }
    ]
    assert [e["id"] for e in listing(env, "pending")] == [keep]
    assert env.client.post("/api/queue/reject", json={"ids": [drop]}).status_code == 422
    assert env.client.post("/api/queue/reject", json={"ids": [99]}).status_code == 422
    assert env.client.post("/api/queue/reject", json={"ids": []}).status_code == 422
    assert approve(env, drop).status_code == 422
    assert env.bw.trashed == []
    assert env.backups == [env.login_backup]


def test_an_entry_left_approved_fails_with_the_reason_at_the_next_start(
    env: AppEnv, tmp_path: Path
):
    [entry_id] = enqueue_ui(env, env.set_leaf("login-1", "login.username", "queued")).json()["ids"]
    ## what a killed process leaves behind
    env.queue.db.set_queue_status(entry_id, "approved")
    env.client.__exit__(None, None, None)
    again = env.restarted()
    with again:
        again.unlock()
        [shown] = listing(again, "failed")
        assert shown["id"] == entry_id
        assert shown["result"]["error"] == "interrupted"
        assert commit_now(again).json()["applied"] == []
    assert env.item("login-1")["login"]["username"] != "queued"


def age_decision(env: AppEnv, entry_id: int, hours: float) -> None:
    """Set when an entry got its status, in the format the database writes."""
    decided = datetime.now(UTC) - timedelta(hours=hours)
    with env.queue.db._connection() as conn:
        conn.execute(
            "UPDATE queue SET decided_at = ? WHERE id = ?",
            (decided.isoformat(timespec="seconds"), entry_id),
        )


def test_a_failed_entry_older_than_the_limit_is_deleted_and_the_others_stay(env: AppEnv):
    old_failed, new_failed, old_rejected, old_pending = (
        enqueue_dict(env, env.set_leaf("login-1", "name", f"Mail {n}"), f"rename {n}")
        for n in range(4)
    )
    for entry_id in (old_failed, new_failed):
        env.queue.db.set_queue_status(entry_id, "failed", result=json.dumps({"error": "x"}))
    env.queue.db.set_queue_status(old_rejected, "rejected")
    age_decision(env, old_failed, 31)
    age_decision(env, new_failed, 29)
    age_decision(env, old_rejected, 31)
    expired = asyncio.run(env.queue.expire_failed())
    assert expired == [old_failed]
    shown = {e["id"]: e["status"] for e in listing(env)}
    assert shown == {new_failed: "failed", old_rejected: "rejected", old_pending: "pending"}


def test_the_limit_in_hours_is_the_setting(env: AppEnv):
    entry_id = enqueue_dict(env, env.set_leaf("login-1", "name", "Mail"), "rename")
    env.queue.db.set_queue_status(entry_id, "failed")
    age_decision(env, entry_id, 5)
    env.queue.failed_keep_hours = 6
    assert asyncio.run(env.queue.expire_failed()) == []
    env.queue.failed_keep_hours = 4
    assert asyncio.run(env.queue.expire_failed()) == [entry_id]


def dismiss(env: AppEnv, *ids: int):
    return env.client.post("/api/queue/dismiss", json={"ids": list(ids)})


def test_dismiss_deletes_a_failed_entry_and_its_hidden_value(env: AppEnv, tmp_path: Path):
    [hidden] = enqueue_ui(env, hidden_setlers(env)[0]).json()["ids"]
    ## an agent's proposal, which the commit leaves pending
    pending = enqueue_dict(env, env.set_leaf("card-1", "name", "Visa"), "rename the card")
    env.item("login-1")["revisionDate"] = CHANGED
    assert commit_now(env).json()["failed"] == [hidden]
    [row] = env.queue.db.queue_entries(ids=[hidden])
    ## as the JSON of the operation stores it, its newlines escaped
    ciphertext = json.dumps(json.loads(row["operation_json"])["value"])[1:-1].encode()
    assert b"AGE ENCRYPTED FILE" in ciphertext
    assert ciphertext in raw_sqlite(tmp_path)

    response = dismiss(env, hidden, pending, 99)
    assert response.status_code == 200, response.text
    assert response.json() == {"dismissed": [hidden], "notFailed": [pending, 99]}
    assert listing(env, "failed") == []
    assert [e["id"] for e in listing(env, "pending")] == [pending]
    assert env.queue.db.queue_entries(ids=[hidden]) == []
    assert_absent(ciphertext, raw_sqlite(tmp_path), "the database file")
    assert dismiss(env, hidden).json() == {"dismissed": [], "notFailed": [hidden]}
    env.client.cookies.clear()
    assert dismiss(env, pending).status_code == 401


def test_a_start_deletes_the_hidden_value_of_a_failed_entry_and_keeps_a_pending_one(
    env: AppEnv, tmp_path: Path
):
    [failed] = enqueue_ui(env, hidden_setlers(env)[0]).json()["ids"]
    env.item("login-1")["revisionDate"] = CHANGED
    assert commit_now(env).json()["failed"] == [failed]
    [pending] = enqueue_ui(env, hidden_setlers(env)[1]).json()["ids"]
    ciphertexts = []
    for row in env.queue.db.queue_entries(ids=[failed, pending]):
        ## as the JSON of the operation stores it, its newlines escaped
        ciphertexts.append(json.dumps(json.loads(row["operation_json"])["value"])[1:-1].encode())
    gone, kept = ciphertexts
    assert gone in raw_sqlite(tmp_path)
    ## the commit at the session's end cannot run, so the pending entry waits
    vault_unreachable(env, True)
    env.client.__exit__(None, None, None)
    vault_unreachable(env, False)

    again = env.restarted()
    with again:
        again.unlock()
        raw = raw_sqlite(tmp_path)
        assert_absent(gone, raw, "the stored bytes")
        assert_present(kept, raw, "the stored bytes")
        shown = entry(again, failed)
        assert (shown["status"], shown["valueGone"]) == ("failed", True)
        assert shown["operation"]["value"] == MASKED
        assert not entry(again, pending)["valueGone"]
        response = retry(again, failed)
        assert response.status_code == 409
        assert "type it again" in response.json()["detail"]
        assert commit_now(again).json()["applied"] == [pending]
        [field] = [f for f in env.item("note-1")["fields"] if f["name"] == "Recovery"]
        written = field["value"] == HIDDEN_VALUE + "-b"
        assert written
