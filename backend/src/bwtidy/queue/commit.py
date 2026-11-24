"""The commit: approving a batch applies its operations as one call of the write path (one sync,
one backup), and every entry ends up applied or failed with the reason.

A conflict blames only the entries that touch the changed items, the others are applied without
them. A failed entry can be retried, a conflict only by writing over the change made elsewhere.
"""

import asyncio
import functools
import json
import logging
from typing import Any

from bwtidy.backups import BackupError
from bwtidy.bw import BwError
from bwtidy.operations import (
    ATTACHMENTS,
    Conflict,
    InvalidOperation,
    MergeConflicts,
    refreshed,
    shown,
    shown_attachment,
    shown_folder,
    touched,
)
from bwtidy.queue.entries import (
    INTERRUPTED,
    Entry,
    QueueError,
    decide,
    decide_all,
    pending,
    shown_now,
    with_values_decrypted,
)
from bwtidy.queue.state import (
    APPLIED,
    APPROVED,
    FAILED,
    PENDING,
    QueueState,
    as_operation,
    dump_operation,
)
from bwtidy.queue.view import plural
from bwtidy.vault import HiddenValueError
from bwtidy.writes import Progress, WriteFailed

log = logging.getLogger(__name__)


class RetryRefused(Exception):
    """A retry names an entry that cannot be retried (not failed, cut off, a merge conflict, a
    conflict without overwrite or on an item that is gone, its value gone). Nothing was
    retried."""


def _failure(row: dict[str, Any]) -> str | None:
    """The error of a failed entry's result."""
    return (json.loads(row["result"]) if row["result"] else {}).get("error")


def _retry_refusal(row: dict[str, Any], overwrite: bool) -> str | None:
    """Why the entry cannot be retried, None when it can."""
    if row["status"] != FAILED:
        return "is not failed"
    if row["value_gone"]:
        return "lost its value at a restart, type it again in the grid"
    error = _failure(row)
    if error == INTERRUPTED["error"]:
        return "was cut off during its commit and may be written, it is never retried"
    if error == "mergeConflicts":
        return "needs a choice per item, propose the merge again with one"
    if error == "conflicts" and not overwrite:
        return "found its item changed elsewhere, retry it with overwrite to write over that change"
    return None


def _undecided(entries: list[Entry], outcome: dict[str, Any]) -> list[Entry]:
    decided = {*outcome["applied"], *outcome["failed"], *outcome["pending"]}
    return [entry for entry in entries if entry.id not in decided]


async def approve(q: QueueState, ids: list[int]) -> dict[str, Any]:
    """Apply the pending entries as one operation of the write path.

    An entry blamed for a conflict or an invalid operation fails and the rest is tried
    again without it, so the batch still gets one backup. Each entry counts as applied as
    soon as its write is done, through the write path's on_applied callback (_written),
    the only way an entry becomes applied. When a write fails midway its entry fails and
    those after it go back to pending. A failed backup or an unreachable vault puts every
    entry not decided yet back to pending and raises. A cancel fails the entry whose write
    ran as interrupted, puts those not started back to pending and raises. An unexpected
    error fails every entry not decided yet as interrupted and raises.
    """
    async with q.lock:
        return await _approve_marked(q, ids)


async def retry(q: QueueState, ids: list[int], overwrite: bool = False) -> dict[str, Any]:
    """Put failed entries back to pending and approve them at once, as approve answers.

    A conflict is retried only with overwrite: its revisionDates and shown values (a folder's
    name too) are then taken from the vault as it is now (after a sync), so it writes over
    the change made elsewhere, the backup of the write holding that change. Raises QueueError
    for ids that are unknown, RetryRefused for an entry that is not failed, was cut off (its
    write may be done), is a merge conflict (it needs a choice per item), lost its value at
    a start, is a conflict without overwrite or names an item or folder that is gone. Then
    nothing is retried.
    """
    async with q.lock:
        if not ids:
            raise QueueError("ids is empty")
        wanted = sorted(set(ids))
        rows = {row["id"]: row for row in q.db.queue_entries(ids=wanted)}
        missing = [i for i in wanted if i not in rows]
        if missing:
            raise QueueError(f"no queue entry with the id {', '.join(map(str, missing))}")
        for entry_id in wanted:
            refusal = _retry_refusal(rows[entry_id], overwrite)
            if refusal is not None:
                raise RetryRefused(f"entry {entry_id} {refusal}")
        operations = {i: rows[i]["operation_json"] for i in wanted}
        conflicted = [i for i in wanted if _failure(rows[i]) == "conflicts"]
        if conflicted:
            await q.writes.reload()
        items = q.cache.items_by_id()
        folders = {f["id"]: f["name"] for f in q.cache.folders if f.get("id")}
        for entry_id in conflicted:
            op = as_operation(json.loads(operations[entry_id]))
            if any(item_id not in items for item_id in touched(op)):
                raise RetryRefused(f"entry {entry_id} changes an item that is gone")
            if any(folder_id not in folders for folder_id in shown_folder(op)):
                raise RetryRefused(f"entry {entry_id} changes a folder that is gone")
            operations[entry_id] = json.dumps(dump_operation(refreshed(op, items, folders)))
        ## no await from here on, so no commit can take an entry while it is pending
        q.db.reset_queue_entries(operations)
        log.info("Retrying failed entries %s", ", ".join(map(str, wanted)))
        return await _approve_marked(q, wanted)


async def _approve_marked(q: QueueState, ids: list[int]) -> dict[str, Any]:
    """_approve, with approving set while it runs so the status route shows the write."""
    q.approving = True
    try:
        return await _approve(q, ids)
    finally:
        q.approving = False
        q.progress = None


async def _approve(q: QueueState, ids: list[int]) -> dict[str, Any]:
    """approve, under the lock."""
    remaining = pending(q, ids)
    ## every diff before the first status, so a diff that raises leaves the batch pending
    diffs = {entry.id: shown_now(q, entry) for entry in remaining}
    decide_all(q, APPROVED, diffs)
    outcome: dict[str, Any] = {"applied": [], "failed": [], "pending": [], "backup": None}
    remaining = _decrypted(q, remaining, outcome)
    q.in_flight = None
    try:
        await _apply(q, remaining, outcome)
    except (BackupError, BwError):
        for entry in _undecided(remaining, outcome):
            decide(q, entry.id, PENDING)
        raise
    except BaseException as error:
        _cut_off(q, remaining, outcome, isinstance(error, asyncio.CancelledError))
        q.commits += 1
        raise
    finally:
        q.in_flight = None
    q.commits += 1
    return outcome


def _cut_off(
    q: QueueState, remaining: list[Entry], outcome: dict[str, Any], cancelled: bool
) -> None:
    """Settle the entries a commit left undecided when it was cut off.

    The entry whose write ran may or may not be written, so it fails as interrupted. Those
    not started hold nothing yet: after a cancel (a stop) they wait for the next commit,
    after an unexpected error they fail too, since pending would make the auto-commit
    repeat that error on every tick.
    """
    failed = waiting = 0
    for entry in _undecided(remaining, outcome):
        if cancelled and entry.id != q.in_flight:
            decide(q, entry.id, PENDING)
            waiting += 1
        else:
            decide(q, entry.id, FAILED, INTERRUPTED)
            failed += 1
    log.warning(
        "A commit was cut off, %d queue entries failed, %d back to pending", failed, waiting
    )


def _decrypted(q: QueueState, entries: list[Entry], outcome: dict[str, Any]) -> list[Entry]:
    """The entries with their hidden values decrypted into RAM for the write.

    One whose value cannot be decrypted (queued under an identity that is gone) fails here
    and is left out, the write path never sees it.
    """
    ready = []
    for entry in entries:
        if not entry.encrypted:
            ready.append(entry)
            continue
        try:
            op = with_values_decrypted(q, entry.op)
        except HiddenValueError as error:
            decide(q, entry.id, FAILED, {"error": "invalid", "detail": str(error)})
            outcome["failed"].append(entry.id)
            continue
        ready.append(Entry(entry.id, op, entry.source, True, entry.edits_tool_note))
    return ready


async def _apply(q: QueueState, remaining: list[Entry], outcome: dict[str, Any]) -> None:
    while remaining:
        sources = {entry.source for entry in remaining}
        source = sources.pop() if len(sources) == 1 else "mixed"
        tool_note_ops = frozenset(
            index for index, entry in enumerate(remaining) if entry.edits_tool_note
        )
        try:
            result = await q.writes.apply(
                [entry.op for entry in remaining],
                source,
                tool_note_ops=tool_note_ops,
                on_written=q.on_written,
                on_writing=functools.partial(_writing, q, remaining),
                on_applied=functools.partial(_written, q, remaining, outcome),
                on_progress=functools.partial(_progressed, q),
            )
        except Conflict as error:
            blamed = _blame_conflicts(remaining, error.conflicts, "conflicts")
        except MergeConflicts as error:
            blamed = _blame_conflicts(remaining, error.conflicts, "mergeConflicts")
        except InvalidOperation as error:
            guilty = remaining if error.index is None else [remaining[error.index]]
            blamed = [(e, {"error": "invalid", "detail": error.reason}) for e in guilty]
        except WriteFailed as error:
            _settle_write_failed(q, remaining, error, outcome)
            return
        else:
            outcome["backup"] = result.backup
            return
        ## an error nobody can be blamed for would retry forever, so it fails them all
        if not blamed:
            detail = "the write path refused the batch"
            blamed = [(entry, {"error": "invalid", "detail": detail}) for entry in remaining]
        for entry, reason in blamed:
            decide(q, entry.id, FAILED, reason)
            outcome["failed"].append(entry.id)
        gone = {entry.id for entry, _ in blamed}
        remaining = [entry for entry in remaining if entry.id not in gone]


def _progressed(q: QueueState, progress: Progress) -> None:
    q.progress = progress


def _writing(q: QueueState, batch: list[Entry], index: int) -> None:
    q.in_flight = batch[index].id


def _written(
    q: QueueState, batch: list[Entry], outcome: dict[str, Any], index: int, backup: str
) -> None:
    """The write path's on_applied: settle an entry as applied the moment its write is done,
    so a cut-off commit keeps it. The one place an entry becomes applied."""
    entry = batch[index]
    decide(q, entry.id, APPLIED, {"backup": backup})
    outcome["applied"].append(entry.id)
    q.in_flight = None


def _blame_conflicts(
    remaining: list[Entry], conflicts: list[dict[str, Any]], key: str
) -> list[tuple[Entry, dict[str, Any]]]:
    blamed = []
    for entry in remaining:
        items = touched(entry.op)
        ## a value the grid showed blames only the entries that carry it for that column
        on_shown = {i: column for i, (column, _) in shown(entry.op).items()}
        on_shown |= {i: ATTACHMENTS for i in shown_attachment(entry.op)}
        folders = shown_folder(entry.op)
        own = [
            c
            for c in conflicts
            if c.get("itemId") in items
            and ("column" not in c or on_shown.get(c["itemId"]) == c["column"])
        ]
        own += [c for c in conflicts if c.get("folderId") in folders]
        if not own:
            continue
        if key == "conflicts" and all("folderId" in c for c in own):
            detail = "the folder changed since the change was queued"
        elif key == "conflicts":
            noun = plural(len(own), "item", "items")
            detail = f"{noun} changed since the change was queued"
        else:
            noun = plural(len(own), "item holds", "items hold")
            detail = f"{noun} values in more than one merged column, a choice is needed"
        blamed.append((entry, {"error": key, "detail": detail, key: own}))
    return blamed


def _settle_write_failed(
    q: QueueState, remaining: list[Entry], error: WriteFailed, outcome: dict[str, Any]
) -> None:
    outcome["backup"] = error.backup
    failed = remaining[error.failed]
    result = {"error": "writeFailed", "detail": error.reason, "backup": error.backup}
    ## found right before its write, marked in the grid like a conflict of the check
    if error.conflicts:
        noun = plural(len(error.conflicts), "item", "items")
        detail = f"{noun} changed since the change was queued"
        if all("folderId" in c for c in error.conflicts):
            detail = "the folder changed since the change was queued"
        result = {"error": "conflicts", "detail": detail, "conflicts": error.conflicts}
        result["backup"] = error.backup
    decide(q, failed.id, FAILED, result)
    outcome["failed"].append(failed.id)
    for index in error.pending:
        decide(q, remaining[index].id, PENDING)
        outcome["pending"].append(remaining[index].id)
