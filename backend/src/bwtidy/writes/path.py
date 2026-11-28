"""The one write path (docs/spec.md, Writes): sync, reload, check, back up, then write.

A batch of operations is one operation in the spec's sense: one sync, one check of every
touched item (check.validate: its revisionDate and the values the grid showed of its folder,
favorite flag and collections, see operations.shown), one backup, one more sync (the check
again when it brought in another vault), then the writes one by one, each item checked once
more right before its write. An item is written by GET of the full object, changing only the
touched leaves, and PUT of the whole object, so every key this code does not know survives.
An SSH key whose public key or fingerprint is empty gets them from its private key right before
its create or PUT (items/ssh_keys.py). Nothing here puts a value into an error message, a log
line or the change log.

WritePath holds the batch driver: the lock, the syncs, the backup or its reuse, the GET with
the checks against the chain of own writes, the PUT that records the write, the change log row
and the callbacks. What each kind of operation does with the item is a function in the module
of its kind (items.py, create.py, folders.py, attachments.py, note.py, restore.py), found in
APPLY by the operation's class, and what such a function may call on the path is WriteContext.

The backups themselves are kept by backups/keeper.py: the write path asks it for the backup
of a write (the newest one when it holds the vault as it is, else a fresh one) and starts a
background backup after every write.
"""

import asyncio
import contextlib
import functools
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from bwtidy.backups import BackupKeeper, BackupReader, BackupSession
from bwtidy.bw import BwClient, BwError, BwUnavailable, same_revision
from bwtidy.db import Database
from bwtidy.items.ssh_keys import fill_in
from bwtidy.operations import (
    COLLECTIONS,
    ApplyTemplate,
    ConvertItem,
    CreateItem,
    DeleteFolder,
    GenerateLeaf,
    MergeColumns,
    MergeItems,
    MoveToFolder,
    MoveToOrganization,
    MoveToPersonal,
    Operation,
    RemoveLeaf,
    RenameAttachment,
    RenameField,
    RenameFolder,
    RestoreFolder,
    RestoreItems,
    SetCollections,
    SetLeaf,
    SetToolNoteSection,
    SetUris,
    TrashItem,
    columns,
    folder_of,
    item_ids,
    touched,
)
from bwtidy.timing import Timings
from bwtidy.vault import (
    Partial,
    ToolNoteId,
    VaultCache,
    find_tool_note,
    list_items_settled,
    partial,
    template_named,
    tool_note_id,
)
from bwtidy.writes import attachments, create, folders, items, note, restore
from bwtidy.writes.check import ChangedDuringBatch, shown_conflict, validate
from bwtidy.writes.planners import changed_columns, plan_conversion, plan_template

log = logging.getLogger(__name__)

## how long a GET is asked again while bw serve answers an item as it was before bwtidy's
## own write (see WritePath.past_own_write), read at call time so tests can monkeypatch them
OWN_WRITE_RETRIES = 25
OWN_WRITE_DELAY = 0.2
## an operation syncs before its first read only when the batch's last sync is older than
## this: a sync after an own write is always a full one, about 1.5 s on the dev vault
SYNC_MAX_AGE = 5.0
## a stable part of the servers' answer to a write on a copy older than their own, lower case:
## Vaultwarden's "The client copy of this cipher is out of date. Resync the client and try
## again." and Bitwarden's "The item cannot be saved because it is out of date. ..."
OUT_OF_DATE = (
    "client copy of this cipher is out of date",
    "cannot be saved because it is out of date",
)


def out_of_date(error: BwError) -> bool:
    """Whether bw passed on the server's refusal of a write on a stale copy of the item."""
    text = str(error).casefold()
    return any(part in text for part in OUT_OF_DATE)


class WriteFailed(Exception):
    """A write failed midway. applied, failed and pending are operation indexes.

    reason is why the failed operation failed, without the batch around it. conflicts holds
    the item the failed operation found changed right before its write (ChangedDuringBatch),
    else it is empty.
    """

    def __init__(
        self,
        applied: list[int],
        failed: int,
        pending: list[int],
        backup: str,
        message: str,
        conflicts: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            f"Operation {failed + 1} failed: {message}. {len(applied)} applied before it, "
            f"{len(pending)} not applied. Backup {backup} holds the vault from before."
        )
        self.applied = applied
        self.failed = failed
        self.pending = pending
        self.backup = backup
        self.reason = message
        self.conflicts = conflicts or []


@dataclass(frozen=True)
class Applied:
    applied: int
    backup: str


def _tell_written(
    on_written: Callable[[Operation, list[str]], None], op: Operation, created: list[str]
) -> None:
    try:
        on_written(op, created)
    except Exception as error:
        ## the type only: a message could carry anything the listener touched
        log.warning("After writing %s: %s", op.kind, type(error).__name__)


def _with_derived(op: Operation, logged: list[str], derived: list[str]) -> list[str]:
    """The change log row's columns with those the write derived for an SSH key, unless the
    kind logs none (a create or a restore works on whole items)."""
    if not derived or isinstance(op, CreateItem | RestoreItems):
        return logged
    return list(dict.fromkeys([*logged, *derived]))


## the phases of an apply (docs/spec.md, the auto-commit status)
SYNCING = "syncing"
BACKING_UP = "backingUp"
WRITING = "writing"
RELOADING = "reloading"


@dataclass(frozen=True)
class Progress:
    """What an apply does now: its phase, and how many of its total operations are written."""

    phase: str
    done: int
    total: int


@dataclass
class _Batch:
    """The state of one apply: the durations of its steps, when its last sync started, by the
    write path's clock (see WritePath.fresh), and the folders its writes renamed, deleted
    (None) or created, which bw serve may list as before for a moment (see _folders_now)."""

    timings: Timings = field(default_factory=Timings)
    last_sync: float = float("-inf")
    folders: dict[str, str | None] = field(default_factory=dict)


@dataclass
class Write:
    """The state of one operation's write, in its batch.

    expected holds the revisionDates the operation was based on (see check_unchanged), found
    what get_to_write found of each item's folder, favorite and collections, which put
    records with what the write leaves (see OwnWrites.partials), fresh_checked whether
    fresh ran for the operation, folder_id the folder its change log row names (a restore
    that creates the folder again names the new one), derived the columns of an SSH key the
    write filled in from its private key (see WritePath.derive), attachment_ids the attachment
    a rename replaced and the one that replaced it (None until it is uploaded and kept), created
    the ids of the items the operation created so far, for its change log row.
    """

    batch: _Batch
    op: Operation
    expected: dict[str, str]
    found: dict[str, Partial] = field(default_factory=dict)
    fresh_checked: bool = False
    folder_id: str | None = None
    derived: list[str] = field(default_factory=list)
    attachment_ids: dict[str, str | None] | None = None
    created: list[str] = field(default_factory=list)


class WriteContext(Protocol):
    """What the write path hands the function of each kind of operation: the vault in RAM,
    bw serve, the decrypted backups, and the steps of a write that every kind shares.

    WritePath is the one implementation, this is the whole of what its modules (items.py,
    create.py, folders.py, attachments.py, note.py, restore.py) may call on it. Each method's
    detail is on WritePath's.
    """

    ## the vault in RAM, the tool note's remembered id
    cache: VaultCache
    remembered: ToolNoteId

    @property
    def client(self) -> BwClient:
        """bw serve."""
        ...

    @property
    def backups(self) -> BackupReader:
        """The decrypted backups in RAM, kept by the keeper."""
        ...

    async def sync(self, batch: _Batch) -> None:
        """POST /sync, remembering when it started for fresh."""
        ...

    async def fresh(self, write: Write) -> None:
        """Sync before the operation reads the first item it writes, when the batch's last
        sync is old."""
        ...

    async def item_now(self, item_id: str) -> dict[str, Any] | None:
        """The item as bw serve has it, None only when the settled listings lack it too."""
        ...

    async def past_own_write[T: dict[str, Any] | None](
        self, item_id: str, read: Callable[[str], Awaitable[T]]
    ) -> T:
        """read(item_id), asked again while bw serve answers the item as it was before one of
        bwtidy's own writes."""
        ...

    async def get_checked(self, write: Write, item_id: str) -> dict[str, Any]:
        """GET the full item to write it, after a sync, checked by check_unchanged."""
        ...

    async def get_to_write(self, write: Write, item_id: str) -> dict[str, Any]:
        """GET the full item to write it, after a sync, without check_unchanged."""
        ...

    def check_unchanged(self, write: Write, item_id: str, full: dict[str, Any]) -> None:
        """Raise ChangedDuringBatch when bw serve's item is not the one the batch checked."""
        ...

    def derive(self, write: Write, body: dict[str, Any]) -> None:
        """Fill an SSH key's empty public key and fingerprint from its private key."""
        ...

    async def put(self, write: Write, item_id: str, full: dict[str, Any]) -> dict[str, Any]:
        """PUT the whole item, recorded in the chain of own writes. Returns bw's answer."""
        ...

    async def unless_stale[T](
        self, write: Write, item_id: str, before: Any, call: Awaitable[T]
    ) -> T:
        """Await call, the write of the item, the server's refusal of it as out of date raised
        as ChangedDuringBatch."""
        ...

    def record(self, item_id: str, before: Any, answer: Any) -> None:
        """Record the own write that turned the item's revisionDate before into the answer's."""
        ...

    def record_collections(self, item_id: str, full: dict[str, Any], ids: list[str]) -> None:
        """Record an own write that gave the item full these collections."""
        ...


class WritePath:
    """Applies batches one at a time. Holds the lock and the change log.

    The rest of the app uses apply, reload, running, exclusive and remembered, nothing else.
    The functions in the modules of the kinds of operation get it as their WriteContext and
    call what that names. The driver's own steps stay underscored.

    The backup of each write comes from the keeper (see backups/keeper.py), which runs one
    backup at a time, so a write that arrives during a background backup waits for it and
    then reuses it. The state of the batch under way and of the operation being written is
    passed to the helpers (_Batch, Write), nothing of it lives on the path.
    """

    def __init__(
        self,
        session: BackupSession,
        cache: VaultCache,
        keeper: BackupKeeper,
        db: Database,
        *,
        remembered: ToolNoteId,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session = session
        self.cache = cache
        ## where the lookups of the tool note keep its id
        self.remembered = remembered
        self.keeper = keeper
        self.db = db
        self._lock = asyncio.Lock()
        self._clock = clock

    @property
    def running(self) -> bool:
        """Whether a batch is being applied (or the vault reloaded) under the lock."""
        return self._lock.locked()

    @contextlib.asynccontextmanager
    async def exclusive(self) -> AsyncGenerator[None]:
        """Hold the lock of apply, so no write runs meanwhile."""
        async with self._lock:
            yield

    @property
    def client(self) -> BwClient:
        return self.session.client

    @property
    def backups(self) -> BackupReader:
        """The decrypted backups in RAM, kept by the keeper."""
        return self.keeper.reader

    async def apply(
        self,
        ops: list[Operation],
        source: str,
        *,
        edits_tool_note: bool = False,
        tool_note_ops: frozenset[int] = frozenset(),
        on_written: Callable[[Operation, list[str]], None] | None = None,
        on_writing: Callable[[int], None] | None = None,
        on_applied: Callable[[int, str], None] | None = None,
        on_progress: Callable[[Progress], None] | None = None,
    ) -> Applied:
        """Sync, reload, validate, back up (or reuse one), sync and reload once more, then apply
        the operations one by one.

        Raises Conflict or InvalidOperation before anything is written: before the backup, or
        right after it when the sync after the backup brought in another vault than the check
        saw and the check run again fails. BackupError when the backup fails (nothing
        written), WriteFailed when a write fails midway. The cache is reloaded afterwards in
        every case, and a backup of the vault as it is then starts in the background. Only
        bwtidy's own writes to the tool note pass edits_tool_note (a section save,
        renaming it back) or name their indexes in tool_note_ops (a queued section save).
        on_written hears of every operation once it is written, with the ids of the items it
        created, before the reload. Its failure
        is only logged, it never fails the write. on_writing hears an operation's index right
        before its write starts, on_applied its index and the backup's name right after its
        write and its change log row are done, so a caller can settle each operation at once.
        on_progress hears each phase as it starts (SYNCING, BACKING_UP, SYNCING after the
        backup, WRITING once per operation, RELOADING). A failure of these three is not
        caught: it ends the batch and reaches the caller.
        """

        def report(phase: str, done: int = 0) -> None:
            if on_progress is not None:
                on_progress(Progress(phase, done, len(ops)))

        async with self._lock:
            batch = _Batch()
            timings = batch.timings
            report(SYNCING)
            await self.sync(batch)
            timings.lap("sync")
            await self.cache.load()
            timings.lap("load")
            await self._read_backups(ops)
            check = functools.partial(
                validate,
                ops,
                self.cache,
                self.remembered,
                edits_tool_note=edits_tool_note,
                tool_note_ops=tool_note_ops,
                backups=self.backups,
            )
            check()
            timings.lap("validate")
            validated = self.keeper.vault_fingerprint()
            report(BACKING_UP)
            info, reused = await self.keeper.backup_for_write()
            timings.lap("backup reused" if reused else "backup")
            try:
                ## a change of another client can reach the server during the backup (a fresh one
                ## takes long), and one without a new revisionDate would be lost under the PUT
                report(SYNCING)
                await self.sync(batch)
                timings.lap("sync after backup")
                await self.cache.load()
                timings.lap("load after backup")
                if self.keeper.vault_fingerprint() != validated:
                    await self._read_backups(ops)
                    check()
                    timings.lap("check after backup")
                applied: list[int] = []
                try:
                    for index, op in enumerate(ops):
                        logged = self._logged_ids(op)
                        logged_columns = self._logged_columns(op)
                        restore = isinstance(op, RestoreItems | RestoreFolder)
                        restored_backup = op.backup if restore else None
                        restored_change_id = op.changeLogId if restore else None
                        write = Write(batch, op, touched(op), folder_id=folder_of(op))
                        if isinstance(op, RenameAttachment):
                            write.attachment_ids = {"before": op.attachmentId, "after": None}
                        report(WRITING, index)
                        if on_writing is not None:
                            on_writing(index)
                        try:
                            await self._apply_one(write)
                        except Exception as error:
                            self.db.record(
                                source,
                                op.kind,
                                logged,
                                "failed",
                                info.name,
                                write.created,
                                _with_derived(op, logged_columns, write.derived),
                                restored_backup,
                                restored_change_id,
                                write.folder_id,
                                write.attachment_ids,
                            )
                            pending = list(range(index + 1, len(ops)))
                            ## ValueError comes from set_leaf or ssh_keys and carries no value,
                            ## BwError is bw's text
                            safe = isinstance(error, BwError | ValueError)
                            message = str(error) if safe else type(error).__name__
                            changed = isinstance(error, ChangedDuringBatch)
                            conflicts = [error.conflict] if changed else []
                            raise WriteFailed(
                                applied, index, pending, info.name, message, conflicts
                            ) from error
                        self.db.record(
                            source,
                            op.kind,
                            logged,
                            "ok",
                            info.name,
                            write.created,
                            _with_derived(op, logged_columns, write.derived),
                            restored_backup,
                            restored_change_id,
                            write.folder_id,
                            write.attachment_ids,
                        )
                        applied.append(index)
                        if on_applied is not None:
                            on_applied(index, info.name)
                        if on_written is not None:
                            _tell_written(on_written, op, write.created)
                finally:
                    timings.lap("writes")
                    report(RELOADING)
                    await self._reload(batch)
                    self.keeper.start_background_backup()
            finally:
                self.keeper.release_write_backup()
            log.info(
                "Applied %d operations from %s, backup %s, %.2f s: %s",
                len(ops),
                source,
                info.name,
                timings.total(),
                timings,
            )
            return Applied(len(ops), info.name)

    async def reload(self) -> None:
        """Sync and load the vault under the lock of apply, so the cache holds the vault as it
        is now (a retry that writes over a change made elsewhere reads it). Raises BwError."""
        async with self._lock:
            await self.client.sync()
            await self.cache.load()

    def _logged_ids(self, op: Operation) -> list[str]:
        """The item ids of the change log row. A section save names the tool note, so a
        restore of its row is refused like one of the note."""
        if isinstance(op, SetToolNoteSection):
            note = find_tool_note(self.cache, self.remembered)
            return [note["id"]] if note and note.get("id") else []
        return item_ids(op)

    def _logged_columns(self, op: Operation) -> list[str]:
        """The coordinates of the change log row: those the operation names (columns), and for
        a conversion or a template every one its plan changes on the item as the batch found
        it (the type, the old type's built-in leaves, the template's fields the item lacked).

        Taken before the write, so a failed write logs them too. A plan that is refused adds
        none, the write fails on it anyway.
        """
        found = columns(op)
        if not isinstance(op, ConvertItem | ApplyTemplate):
            return found
        item = self.cache.items_by_id().get(op.itemId)
        if item is None:
            return found
        lookups = self.cache.lookups()
        try:
            if isinstance(op, ConvertItem):
                planned = plan_conversion(item, op.newType, op.mapping, lookups)
            else:
                template = template_named(self.cache, self.remembered, op.template)
                planned = plan_template(item, template, op.mapping, lookups)
        except ValueError:
            return found
        return list(dict.fromkeys([*found, *changed_columns(item, planned, lookups)]))

    async def _read_backups(self, ops: list[Operation]) -> None:
        """Decrypt the backups the restores among ops name, off the event loop.

        validate reads them again from the reader's RAM. A backup that cannot be read is
        validate's to refuse, so nothing is raised here.
        """
        for op in ops:
            if isinstance(op, RestoreItems | RestoreFolder):
                with contextlib.suppress(ValueError):
                    await asyncio.to_thread(self.backups.read, op.backup)

    async def _reload(self, batch: _Batch) -> None:
        try:
            await self.sync(batch)
            batch.timings.lap("sync after writes")
            await self.cache.load()
            batch.timings.lap("load after writes")
        except BwError as error:
            log.warning("Reloading the vault after the writes failed: %s", error)

    async def put(self, write: Write, item_id: str, full: dict[str, Any]) -> dict[str, Any]:
        """PUT the whole item and remember which revisionDate this write turned into which.

        The "after" comes from bw serve's answer to this very PUT, never from a listing, so
        the chain never holds a revisionDate somebody else wrote. The answer is returned for
        a write that follows it (the collections of a restored item). The tool note's answer
        replaces the cached note at once.
        """
        self.derive(write, full)
        before = full.get("revisionDate")
        put = self.client.put_item(item_id, full)
        updated = await self.unless_stale(write, item_id, before, put)
        self.record(item_id, before, updated)
        self.cache.own.record_partial(item_id, write.found.pop(item_id, None), partial(full))
        ## a section save is applied right after this write, long before the reload after the
        ## batch, and the sections are read from the cached note in between
        if item_id == tool_note_id(self.cache, self.remembered) and isinstance(updated, dict):
            self.cache.replace_item(item_id, updated)
        return updated

    async def unless_stale[T](
        self, write: Write, item_id: str, before: Any, call: Awaitable[T]
    ) -> T:
        """Await call, the write of the item, the server's refusal of it as out of date raised
        as ChangedDuringBatch.

        bw serve's copy, which the write GETs, is only as fresh as its last sync, and bw sends
        that copy's revisionDate as lastKnownRevisionDate (the body's is ignored). The server
        refuses the write when its own is newer, which is another client's change made after
        that sync. actual is the item's revisionDate after a sync.
        """
        try:
            return await call
        except BwError as error:
            if not out_of_date(error):
                raise
            await self.sync(write.batch)
            now = await self.item_now(item_id)
            conflict = {
                "itemId": item_id,
                "expected": write.expected.get(item_id, before),
                "actual": now.get("revisionDate") if now else None,
            }
            raise ChangedDuringBatch(conflict) from error

    @staticmethod
    def derive(write: Write, body: dict[str, Any]) -> None:
        """Fill an SSH key's empty public key and fingerprint from its private key, right before
        the body is created or PUT (see ssh_keys.fill_in), remembering the columns for the
        change log row. bw refuses an SSH key without them. A key that cannot give them raises
        ValueError with a fixed text, so the operation fails with nothing of this item
        written."""
        for column in fill_in(body):
            if column not in write.derived:
                write.derived.append(column)

    def record(self, item_id: str, before: Any, answer: Any) -> None:
        """Record the own write that turned the item's revisionDate before into the answer's
        (see OwnWrites.record_write). An answer without one records nothing."""
        after = answer.get("revisionDate") if isinstance(answer, dict) else None
        if isinstance(before, str) and isinstance(after, str):
            self.cache.own.record_write(item_id, before, after)

    def record_collections(self, item_id: str, full: dict[str, Any], ids: list[str]) -> None:
        """Record an own write that gave the item full these collections (they change without
        a new revisionDate, see OwnWrites.partials)."""
        found = partial(full)
        self.cache.own.record_partial(item_id, found, {**found, COLLECTIONS: frozenset(ids)})

    def check_unchanged(self, write: Write, item_id: str, full: dict[str, Any]) -> None:
        """Raise ChangedDuringBatch when bw serve's item is not the one the batch checked.

        validate checked the revisionDates before the writes, but a sync inside the batch can
        bring in another client's change after that, and a PUT on top of it would pass. So
        every item is compared again right before its write, with the chain of own writes read
        now, so an earlier operation of the same batch that wrote the item counts as bwtidy's.
        An item the operation has no revisionDate for (a new one, the tool note of a section
        save, one in the trash) is not checked.
        """
        expected = write.expected.get(item_id)
        if expected is None:
            return
        actual = full.get("revisionDate")
        if not same_revision(actual, self.cache.own.current_revision(item_id, expected)):
            raise ChangedDuringBatch({"itemId": item_id, "expected": expected, "actual": actual})
        conflict = shown_conflict(self.cache, write.op, item_id, full)
        if conflict is not None:
            raise ChangedDuringBatch(conflict)

    async def past_own_write[T: dict[str, Any] | None](
        self, item_id: str, read: Callable[[str], Awaitable[T]]
    ) -> T:
        """read(item_id), asked again while bw serve answers the item as it was before one of
        bwtidy's own writes.

        Right after its own PUT, bw serve 2026.10.0 answers a GET of that item for a moment
        with the item as it was before the PUT (the PUT's answer and bw serve's data.json hold
        the new revisionDate, a GET 12 ms later the old one, a GET 0.3 s later the new one
        again). Its cache of the items reads the file again only
        after the write's file lock is released. Taken as it is, the batch's next write of
        the item would fail as a conflict with the batch's own earlier write, and a PUT on
        that copy would undo it. A revisionDate bwtidy wrote over is never another client's
        change (the server gives every write a new one), so only such an answer is asked
        again. Once the retries are spent the last answer is returned as it is, and
        check_unchanged fails it as a conflict.
        """
        full = await read(item_id)
        for _ in range(OWN_WRITE_RETRIES):
            if full is None or not self.cache.own.written_over(item_id, full.get("revisionDate")):
                break
            await asyncio.sleep(OWN_WRITE_DELAY)
            full = await read(item_id)
        return full

    async def sync(self, batch: _Batch) -> None:
        """POST /sync, remembering when it started for fresh."""
        started = self._clock()
        await self.client.sync()
        batch.last_sync = started

    async def fresh(self, write: Write) -> None:
        """Sync before the operation reads the first item it writes, when the batch's last
        sync is older than SYNC_MAX_AGE.

        bw serve's copy of an item is only as fresh as its last sync, and a change another
        client made after that is lost under the PUT when the server does not refuse it as out
        of date (a folder, favorite or collections change, docs/spec.md Writes). Not before
        every operation: right after bwtidy's own write a sync is always a full one, which
        makes a commit of 20 single edits about 6 times slower.
        """
        if write.fresh_checked:
            return
        write.fresh_checked = True
        batch = write.batch
        if self._clock() - batch.last_sync < SYNC_MAX_AGE:
            return
        batch.timings.lap("writes")
        await self.sync(batch)
        batch.timings.lap("sync before write")

    async def get_checked(self, write: Write, item_id: str) -> dict[str, Any]:
        """GET the full item to write it, after a sync, checked by check_unchanged."""
        full = await self.get_to_write(write, item_id)
        self.check_unchanged(write, item_id, full)
        return full

    async def get_to_write(self, write: Write, item_id: str) -> dict[str, Any]:
        """GET the full item to write it, after a sync, remembering what it holds for put.

        Without check_unchanged: for an item the operation checked before its own earlier
        write of it (see restore._put_back).
        """
        await self.fresh(write)
        full = await self.past_own_write(item_id, self.item_now)
        if full is None:
            raise ValueError(f"item {item_id} is gone")
        write.found[item_id] = partial(full)
        return full

    async def _apply_one(self, write: Write) -> None:
        """Apply the write's operation with the function of its kind. The ids of the items it
        creates go into write.created."""
        await APPLY[type(write.op)](self, write, write.op)

    async def item_now(self, item_id: str) -> dict[str, Any] | None:
        """The item as bw serve has it, None only when the settled listings (the trash and the
        archive among them) do not hold it either.

        Right after a sync bw serve answers Not found for every object while it decrypts the
        items again (see create.create), and a read can fail for other reasons. A failed read alone
        never means the item is gone: creating it anew would lose its passkeys and history.
        """
        try:
            return await self.client.get_item(item_id)
        except BwUnavailable:
            raise
        except BwError:
            pass
        items = await list_items_settled(self.client)
        trash = await self.client.list_trash()
        archived = [
            *await self.client.list_archived(),
            *await self.client.list_archived(trash=True),
        ]
        if any(i.get("id") == item_id for i in [*items, *trash, *archived]):
            return await self.client.get_item(item_id)
        return None


## what applies an operation: the context, the state of its write, the operation itself
Applier = Callable[[WriteContext, Write, Any], Awaitable[None]]


def _entry[O: Operation](
    cls: type[O], fn: Callable[[WriteContext, Write, O], Awaitable[None]]
) -> tuple[type[Operation], Applier]:
    """An entry of APPLY, typed so the function takes the operation class it is paired with."""
    return cls, fn


## the function that applies each kind of operation, in the module of its kind
## (tests/unit/writes/test_path.py checks that every kind of Operation has an entry)
APPLY: dict[type[Operation], Applier] = dict(
    [
        _entry(SetLeaf, items.write_set_leaf),
        _entry(RemoveLeaf, items.write_remove_leaf),
        _entry(GenerateLeaf, create.write_generate_leaf),
        _entry(MoveToFolder, items.write_move_to_folder),
        _entry(SetCollections, items.write_set_collections),
        _entry(MoveToOrganization, items.write_move_to_organization),
        _entry(MoveToPersonal, create.write_move_to_personal),
        _entry(TrashItem, items.write_trash_item),
        _entry(ConvertItem, create.write_convert_item),
        _entry(ApplyTemplate, create.write_apply_template),
        _entry(RenameField, items.write_rename_field),
        _entry(MergeColumns, items.write_merge_columns),
        _entry(MergeItems, items.write_merge_items),
        _entry(SetUris, items.write_set_uris),
        _entry(RestoreItems, restore.write_restore_items),
        _entry(CreateItem, create.write_create_item),
        _entry(RenameFolder, folders.write_rename_folder),
        _entry(DeleteFolder, folders.write_delete_folder),
        _entry(RestoreFolder, folders.write_restore_folder),
        _entry(RenameAttachment, attachments.write_rename_attachment),
        _entry(SetToolNoteSection, note.write_set_section),
    ]
)
