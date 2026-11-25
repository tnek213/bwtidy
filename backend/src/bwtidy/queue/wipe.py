"""The queue's part of the account's data: deleting it (docs/spec.md, Deleting data) and the
backups the queue still needs."""

import json
import logging
from typing import Any

from bwtidy.db import Database
from bwtidy.queue.agent import pending_mcp_ids
from bwtidy.queue.state import APPROVED, FAILED, PENDING, QueueState

log = logging.getLogger(__name__)


class WriteRunning(Exception):
    """A commit is running, so the account's data cannot be deleted right now (409). Nothing
    was deleted (docs/spec.md, Deleting data)."""


async def wipe_account_data(q: QueueState, *, keep_pending_mcp: bool) -> dict[str, Any]:
    """Delete the account's backups, change log, backup fingerprints and queue entries, for
    the account data deletion (docs/spec.md, Deleting data).

    Every queue entry goes, except the agent's proposals still pending when keep_pending_mcp
    (they cannot be written without approval, so Write first leaves them). A backup a
    remaining pending restore proposal still needs stays, its name among the kept ones. The
    decided entries (applied, failed, rejected) always go, the pending UI entries too (Write
    first committed them, Drop deleted nothing less).

    Raises WriteRunning when a commit or a direct write is running. Otherwise it takes the
    queue's lock and then the write path's (the order a commit takes them in) without an
    await in between the check and the first one, and holds both while it deletes, so no
    write starts meanwhile and none is half deleted.
    """
    if q.lock.locked() or q.writes.running:
        raise WriteRunning("a write is running, try again")
    async with q.lock, q.writes.exclusive():
        keep = pending_mcp_ids(q) if keep_pending_mcp else []
        ## delete the entries first, so backup_in_use sees only the kept pending ones
        queue_deleted = q.db.delete_queue_entries_except(keep)
        backups_deleted, kept_backups = await q.keeper.delete_all_backups()
        change_log_deleted = q.db.clear_change_log()
        q.db.clear_backup_fingerprints()
        log.info(
            "Deleted the account data: %d backups, %d change log rows, %d queue entries",
            backups_deleted,
            change_log_deleted,
            queue_deleted,
        )
        return {
            "backups": backups_deleted,
            "changeLog": change_log_deleted,
            "queue": queue_deleted,
            "keptBackups": kept_backups,
        }


def backup_in_use(db: Database, name: str) -> str | None:
    """Why the backup cannot be deleted because of the queue, None when it can: the keeper's
    queue_in_use hook, which main.create_app passes it.

    A pending or approved restoreItems or restoreFolder reads its backup when committed,
    and a failed entry names the backup its write was taken with (its result), the one to
    restore from. A pending entry of any other kind has no backup yet, it is taken at the
    commit.
    """
    for row in db.queue_entries(statuses=(PENDING, APPROVED, FAILED)):
        if row["status"] == FAILED:
            result = json.loads(row["result"]) if row["result"] else {}
            if result.get("backup") == name:
                return (
                    f"Backup {name} holds the vault from before the failed queue entry "
                    f"#{row['id']}. Dismiss the entry first."
                )
            continue
        operation = json.loads(row["operation_json"])
        restore = operation.get("kind") in ("restoreItems", "restoreFolder")
        if restore and operation.get("backup") == name:
            return f"Backup {name} is what the queued restore #{row['id']} reads."
    return None
