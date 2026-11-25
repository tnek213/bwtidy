"""The queue's rows: an entry, the one write of its status (of one entry or a batch), the entries
as the Queue page lists them and the decisions that end an entry without a write (reject,
dismiss, the expiry of failed entries and what a start settles)."""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bwtidy.db import QueueStatus
from bwtidy.items.coordinates import HIDDEN_FIELD, MASKED
from bwtidy.operations import CreateItem, InvalidOperation, Operation, SetLeaf
from bwtidy.queue.sections import earlier_saves
from bwtidy.queue.state import APPROVED, FAILED, PENDING, REJECTED, UI, QueueState, as_operation
from bwtidy.queue.view import safe_diff

log = logging.getLogger(__name__)

## how often the worker deletes the failed entries that are too old
EXPIRY_INTERVAL = 3600.0

## the result of an entry whose commit was cut off: a write of it may be done, so no retry
INTERRUPTED = {
    "error": "interrupted",
    "detail": (
        "The commit was cut off before it finished. Check the item, it may or may not hold this"
        " change, then make the change again."
    ),
}

## what a failed entry's hidden value holds once its ciphertext was deleted at a start
VALUE_GONE = "(gone after a restart)"


class QueueError(Exception):
    """A decision names entries that are unknown or no longer pending. Nothing was decided."""


@dataclass
class Entry:
    id: int
    op: Operation
    source: str
    ## a setLeaf's value or a createItem's secretValues in op are age ciphertexts, decrypted
    ## right before the write
    encrypted: bool = False
    ## bwtidy's own section save, the one kind of entry allowed to touch the tool note
    edits_tool_note: bool = False


def with_values_decrypted(q: QueueState, op: Operation) -> Operation:
    """The operation with its hidden values decrypted into RAM. Raises HiddenValueError."""
    if isinstance(op, SetLeaf):
        ## a queued removal in the same commit can make the field new again at write time
        value = q.hidden.decrypt(str(op.value))
        return op.model_copy(update={"value": value, "fieldType": HIDDEN_FIELD})
    if isinstance(op, CreateItem):
        values = {c: q.hidden.decrypt(v) for c, v in op.secretValues.items()}
        return op.model_copy(update={"secretValues": values})
    return op


def pending_ui_ids(q: QueueState) -> list[int]:
    return [row["id"] for row in q.db.queue_entries(statuses=(PENDING,), source=UI)]


def entries(q: QueueState, status: str | None = None) -> list[dict[str, Any]]:
    """The entries in id order, all or those with the status.

    A pending entry's diff is computed against the vault now, a decided entry shows the
    diff as it was when decided.
    """
    return [present(q, row) for row in q.db.queue_entries(None if status is None else (status,))]


def present(q: QueueState, row: dict[str, Any]) -> dict[str, Any]:
    operation = json.loads(row["operation_json"])
    ## the ciphertext is no secret, but nothing of a hidden value leaves the backend
    hidden = bool(row["value_encrypted"] or row["value_gone"])
    if row["status"] == PENDING:
        try:
            op = as_operation(operation)
            earlier = earlier_saves(q, row["id"], op)
            shown = safe_diff(op, q.cache, q.remembered, q.backups, earlier, hidden)
        except InvalidOperation as error:
            shown = {"diff": [], "error": str(error)}
    elif row["diff_json"]:
        shown = json.loads(row["diff_json"])
    else:
        shown = {"diff": [], "error": None}
    if hidden and operation.get("kind") == "createItem":
        operation["secretValues"] = dict.fromkeys(operation["secretValues"], MASKED)
    elif hidden:
        operation["value"] = MASKED
    return {
        "id": row["id"],
        "createdAt": row["created_at"],
        "source": row["source"],
        "summary": row["summary"],
        "status": row["status"],
        "decidedAt": row["decided_at"],
        "operation": operation,
        "diff": shown["diff"],
        "diffError": shown["error"],
        "result": json.loads(row["result"]) if row["result"] else None,
        "valueGone": bool(row["value_gone"]),
    }


def pending(q: QueueState, ids: list[int]) -> list[Entry]:
    """The entries with these ids, in id order. Raises QueueError unless all are pending."""
    if not ids:
        raise QueueError("ids is empty")
    rows = {row["id"]: row for row in q.db.queue_entries(ids=sorted(set(ids)))}
    missing = sorted(set(ids) - rows.keys())
    if missing:
        raise QueueError(f"no queue entry with the id {', '.join(map(str, missing))}")
    decided = sorted(i for i, row in rows.items() if row["status"] != PENDING)
    if decided:
        raise QueueError(f"entry {', '.join(map(str, decided))} is no longer pending")
    found = []
    for entry_id, row in sorted(rows.items()):
        try:
            op = as_operation(json.loads(row["operation_json"]))
        except InvalidOperation as error:
            raise QueueError(f"entry {entry_id} holds no valid operation: {error}") from None
        found.append(
            Entry(
                entry_id,
                op,
                row["source"],
                bool(row["value_encrypted"]),
                bool(row["edits_tool_note"]),
            )
        )
    return found


def decide(
    q: QueueState,
    entry_id: int,
    status: str,
    result: dict[str, Any] | None = None,
    diff: dict[str, Any] | None = None,
) -> None:
    """The one write of a queue entry's status, with its result and diff when given."""
    q.db.set_queue_status(
        entry_id,
        status,
        None if result is None else json.dumps(result),
        None if diff is None else json.dumps(diff),
    )


def decide_all(q: QueueState, status: str, diffs: dict[int, dict[str, Any]]) -> None:
    """decide for every entry of diffs with this status and its diff, in one transaction, so
    a failure decides none."""
    q.db.set_queue_statuses(
        [
            QueueStatus(entry_id, status, diff_json=json.dumps(diff))
            for entry_id, diff in diffs.items()
        ]
    )


def shown_now(q: QueueState, entry: Entry) -> dict[str, Any]:
    """The entry's diff against the vault now, kept with its decision."""
    earlier = earlier_saves(q, entry.id, entry.op)
    return safe_diff(entry.op, q.cache, q.remembered, q.backups, earlier, entry.encrypted)


async def reject(q: QueueState, ids: list[int]) -> dict[str, Any]:
    async with q.lock:
        rejected = pending(q, ids)
        for entry in rejected:
            decide(q, entry.id, REJECTED, diff=shown_now(q, entry))
        return {"rejected": [entry.id for entry in rejected]}


async def dismiss(q: QueueState, ids: list[int]) -> dict[str, Any]:
    """Delete the failed entries among the ids, a hidden value's ciphertext with them.

    Nobody would read a record of a dismissed failure.
    Ids that are unknown or not failed are left alone and listed as notFailed.
    """
    async with q.lock:
        wanted = sorted(set(ids))
        dismissed = q.db.delete_queue_entries(wanted, FAILED)
        if dismissed:
            log.info("Dismissed failed entries %s", ", ".join(map(str, dismissed)))
        gone = set(dismissed)
        return {"dismissed": dismissed, "notFailed": [i for i in wanted if i not in gone]}


async def expire_failed(q: QueueState) -> list[int]:
    """Delete the failed entries older than failed_keep_hours (decided_at), as Dismiss does."""
    cutoff = datetime.now(UTC) - timedelta(hours=q.failed_keep_hours)
    async with q.lock:
        expired = q.db.delete_failed_before(cutoff.isoformat(timespec="seconds"))
        if expired:
            log.info("Deleted failed entries %s as too old", ", ".join(map(str, expired)))
        return expired


async def run_failed_expiry(q: QueueState) -> None:
    """Delete the failed entries that are too old, now and then every EXPIRY_INTERVAL."""
    while True:
        try:
            await expire_failed(q)
        except Exception:
            log.exception("Deleting the old failed entries failed")
        await asyncio.sleep(EXPIRY_INTERVAL)


def fail_interrupted(q: QueueState) -> None:
    """Fail what a killed process left approved. Call it only while no commit can run."""
    rows = q.db.queue_entries(statuses=(APPROVED,))
    for row in rows:
        decide(q, row["id"], FAILED, INTERRUPTED)
    if rows:
        log.warning("%d queue entries were left approved by a stop and failed", len(rows))


def forget_failed_values(q: QueueState) -> None:
    """Delete the ciphertexts of the hidden values of failed entries, each replaced by a
    marker, and mark the value gone (such an entry cannot be retried). Pending entries keep
    theirs. Call it at the start, after fail_interrupted."""
    forgotten = {}
    for row in q.db.queue_entries(statuses=(FAILED,)):
        if not row["value_encrypted"]:
            continue
        operation = json.loads(row["operation_json"])
        if operation.get("kind") == "createItem":
            operation["secretValues"] = dict.fromkeys(operation["secretValues"], VALUE_GONE)
        else:
            operation["value"] = VALUE_GONE
        forgotten[row["id"]] = json.dumps(operation)
    q.db.forget_queue_values(forgotten)
    if forgotten:
        log.info("Deleted the hidden values of %d failed queue entries", len(forgotten))
