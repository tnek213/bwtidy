"""What the modules of the queue share: the queue's state, the statuses and sources of an entry,
and an entry's operation as SQLite stores it.

ReviewQueue (review.py) is a QueueState, and each module's functions take it as their first
argument. view.py and sections.py load without the rest of the queue, so what they read of an
entry is here.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from bwtidy.backups import BackupKeeper
from bwtidy.config import FAILED_KEEP_HOURS
from bwtidy.db import Database
from bwtidy.operations import Operation, parse_operations
from bwtidy.vault import HiddenValues, VaultCache
from bwtidy.writes import Progress, WritePath

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
APPLIED = "applied"
FAILED = "failed"
STATUSES = (PENDING, APPROVED, REJECTED, APPLIED, FAILED)

UI = "ui"
MCP = "mcp"


def as_operation(op: dict[str, Any]) -> Operation:
    """The operation of its JSON shape, as stored in SQLite. Raises InvalidOperation."""
    [parsed] = parse_operations([op])
    return parsed


def dump_operation(op: Operation) -> dict[str, Any]:
    ## by_alias: renameField's "from" is stored under its JSON name
    return op.model_dump(mode="json", by_alias=True)


class QueueState:
    """The queue in SQLite, the vault in RAM and the decrypted backups to check and diff
    against, the write path and the backup keeper."""

    def __init__(
        self,
        db: Database,
        cache: VaultCache,
        writes: WritePath,
        keeper: BackupKeeper,
        hidden: HiddenValues,
        *,
        on_ui_entry: Callable[[int], None],
        on_written: Callable[[Operation, list[str]], None],
        failed_keep_hours: int = FAILED_KEEP_HOURS,
    ) -> None:
        self.db = db
        self.failed_keep_hours = failed_keep_hours
        self.cache = cache
        self.writes = writes
        self.remembered = writes.remembered
        self.keeper = keeper
        self.backups = keeper.reader
        self.hidden = hidden
        ## the decisions, the commits and the wipe hold it, and take the write path's lock only
        ## after it
        self.lock = asyncio.Lock()
        ## told about every new UI entry, so the auto-commit knows when it was queued
        self.on_ui_entry = on_ui_entry
        ## told about every operation a commit wrote, with the ids of the items it created
        self.on_written = on_written
        ## the entry whose write runs, from its start until it is settled
        self.in_flight: int | None = None
        ## the approves that ended since the worker started (not those that left every entry
        ## pending), which the frontend watches to reload after one
        self.commits = 0
        ## true while an approve runs (the auto-commit's or the Queue page's)
        self.approving = False
        ## the phase of the write path in the running approve, None outside one
        self.progress: Progress | None = None
