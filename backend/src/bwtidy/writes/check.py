"""The check of a batch before anything is written: validate, with the check of each operation
(_check_op) planned on a copy of the cached item, the conflicts of what the client showed, and
ChangedDuringBatch, the conflict a write finds when it checks its item again right before the
PUT (docs/spec.md, Writes).

A batch is checked in order: the tool note guard, each revisionDate against the chain of own
writes, the folder, favorite flag, collections and attachment names the client showed, then
the merge conflicts, then each operation on the items as the operations before it leave them
(_Planned) and a section save on the note as the saves before it leave it (_PlannedNote). The
write path runs it after its sync and again after the backup, the queue on arrival. Nothing
here puts a value into an error message.
"""

import copy
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from typing import Any, assert_never

from bwtidy.backups import BackupReader
from bwtidy.bw import same_revision
from bwtidy.items.coordinates import (
    HIDDEN_FIELD,
    Coordinate,
    Lookups,
    parse,
    remove_leaf,
    set_leaf,
    set_uris,
)
from bwtidy.items.normalize import (
    merge_columns,
    merge_columns_of,
    merge_conflict,
    merge_items,
    rename_conflict,
    rename_field,
)
from bwtidy.operations import (
    ApplyTemplate,
    Conflict,
    ConvertItem,
    CreateItem,
    DeleteFolder,
    GenerateLeaf,
    InvalidOperation,
    MergeColumns,
    MergeConflicts,
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
    attachment_of,
    item_ids,
    shown,
    shown_now,
    touched,
    uri_pairs,
)
from bwtidy.vault import ToolNoteId, VaultCache, find_tool_note, template_named, tool_note_id
from bwtidy.writes.planners import (
    RENAME_BACK,
    attachment_conflict,
    by_name,
    check_attachment_rename,
    check_folder_empty,
    check_folder_name,
    check_folder_name_secrets,
    check_generate_leaf,
    folder_conflict,
    plan_conversion,
    plan_create,
    plan_folder_restore,
    plan_template,
    resolve_collections,
)
from bwtidy.writes.restore_plan import TOOL_NOTE_RESTORE, plan_restore
from bwtidy.writes.rules import SecretIntoPlain, check_hidden_fits, plain_matches_secret
from bwtidy.writes.saves import check_section, saved_notes


class ChangedDuringBatch(ValueError):
    """An item changed between the batch's check and its write: a sync inside the batch (a
    create, a restore, a backup by hand) brought in another client's change, or the server
    refused the write as out of date (a change after bw serve's last sync).

    conflict is {itemId, expected, actual} like a Conflict's (a folder's {folderId, expected,
    actual}), the message names the id only.
    """

    def __init__(self, conflict: dict[str, Any]) -> None:
        if "folderId" in conflict:
            what = f"folder {conflict['folderId']}"
        else:
            what = f"item {conflict['itemId']}"
        super().__init__(f"{what} changed since the batch was checked")
        self.conflict = conflict


class _Planned:
    """The items as the setLeafs, removeLeafs, generateLeafs and field renames before an
    operation leave them, so a rule also
    holds across a batch and, for the rule of equal values, against the queue's pending
    entries."""

    def __init__(self, items: dict[str, dict[str, Any]]) -> None:
        self.items = items
        self.changed: dict[str, dict[str, Any]] = {}

    def item(self, item_id: str) -> dict[str, Any]:
        return self.changed.get(item_id) or self.items[item_id]

    def _rename_attachment(self, op: RenameAttachment) -> None:
        """The attachment under its new name in the planned item, its id kept. A rename its own
        check refuses leaves the item as it is."""
        item = copy.deepcopy(self.item(op.itemId))
        try:
            check_attachment_rename(item, op)
        except ValueError:
            return
        attachment = attachment_of(item, op.attachmentId)
        assert attachment is not None
        attachment["fileName"] = op.fileName
        self.changed[op.itemId] = item

    def apply(self, op: Operation) -> None:
        """Plan a setLeaf, removeLeaf, generateLeaf, setUris, renameField or renameAttachment. One
        that does not fit is left out, its own check refuses it."""
        if isinstance(op, RenameAttachment):
            if op.itemId in self.items:
                self._rename_attachment(op)
            return
        if isinstance(op, SetUris):
            for entry in op.items:
                if entry.itemId not in self.items:
                    continue
                item = copy.deepcopy(self.item(entry.itemId))
                try:
                    set_uris(item, uri_pairs(entry.uris))
                except ValueError:
                    continue
                self.changed[entry.itemId] = item
            return
        if isinstance(op, RenameField):
            for entry in op.items:
                if entry.itemId not in self.items:
                    continue
                item = copy.deepcopy(self.item(entry.itemId))
                try:
                    rename_field(item, op.from_, op.to)
                except ValueError:
                    continue
                self.changed[entry.itemId] = item
            return
        if not isinstance(op, SetLeaf | RemoveLeaf | GenerateLeaf) or op.itemId not in self.items:
            return
        item = copy.deepcopy(self.item(op.itemId))
        try:
            if isinstance(op, SetLeaf):
                set_leaf(item, parse(op.column), op.value, op.fieldType)
            elif isinstance(op, GenerateLeaf):
                ## the value is only made at write time, the field it goes into is hidden
                set_leaf(item, parse(op.column), "", HIDDEN_FIELD)
            else:
                remove_leaf(item, parse(op.column))
        except ValueError:
            return
        self.changed[op.itemId] = item


def planned_item(item: dict[str, Any], earlier: Sequence[Operation]) -> dict[str, Any]:
    """The item as the setLeafs, removeLeafs, generateLeafs, setUris, field renames and
    attachment renames of earlier on it leave it (see _Planned)."""
    planned = _Planned({item["id"]: item})
    for op in earlier:
        planned.apply(op)
    return planned.item(item["id"])


class _PlannedNote:
    """The tool note's notes as the section saves before an operation leave them, so a change
    that no longer applies (a template an earlier entry renamed) fails on its own in the check
    and the rest of the batch goes on without it."""

    def __init__(self, cache: VaultCache, remembered: ToolNoteId) -> None:
        self.cache = cache
        note = find_tool_note(cache, remembered)
        self.notes = note.get("notes") if note is not None else None

    def apply(self, op: Operation, *, strict: bool) -> None:
        """Plan a section save. Raises ValueError for one that does not apply when strict,
        else leaves it out like its write would fail it."""
        if not isinstance(op, SetToolNoteSection):
            return
        try:
            self.notes = saved_notes(self.notes, op, self.cache)
        except ValueError:
            if strict:
                raise


def _check_equal_values(
    op: Operation, planned: _Planned, batch: _Planned, lookups: Lookups
) -> None:
    """plain_matches_secret for an operation that sets a value the request carries, on the item
    as planned leaves it (the queue's pending entries and the batch before it). The secrets of
    the item as it is still count.

    A createItem checks itself in plan_create, a generated value is only known at write time.
    """
    if isinstance(op, SetLeaf):
        before = planned.item(op.itemId)
        after = copy.deepcopy(before)
        try:
            set_leaf(after, parse(op.column), op.value, op.fieldType)
        except ValueError:
            ## a pending entry leaves the item so the setLeaf no longer fits, the batch did
            ## (_check_op)
            before = batch.item(op.itemId)
            after = copy.deepcopy(before)
            set_leaf(after, parse(op.column), op.value, op.fieldType)
        plain_matches_secret(after, before, lookups, original=planned.items[op.itemId])
    elif isinstance(op, MoveToFolder):
        for item_id in op.itemIds:
            before = planned.item(item_id)
            original = planned.items[item_id]
            plain_matches_secret(before, before, lookups, folder=op.folderName, original=original)
    elif isinstance(op, RenameFolder):
        items = [planned.item(item_id) for item_id in planned.items]
        check_folder_name_secrets(items, op.folderId, op.newName, lookups, planned.items)
    elif isinstance(op, SetUris):
        for entry in op.items:
            before = planned.item(entry.itemId)
            after = copy.deepcopy(before)
            set_uris(after, uri_pairs(entry.uris))
            plain_matches_secret(after, before, lookups, original=planned.items[entry.itemId])


def _check_op(
    op: Operation,
    items: dict[str, dict[str, Any]],
    cache: VaultCache,
    remembered: ToolNoteId,
    tool_note_id: str | None,
    backups: BackupReader | None,
    batch: _Planned,
    planned: _Planned,
) -> None:
    """Check one operation of a batch. batch holds the items as the operations before it in the
    batch leave them (see _Planned.apply), planned the same with the queue's pending
    entries applied first (see validate)."""
    lookups = cache.lookups()
    if isinstance(op, SetLeaf):
        item = batch.item(op.itemId)
        coordinate = parse(op.column)
        if op.shownFavorite is not None and coordinate != Coordinate.at("favorite"):
            raise ValueError("shownFavorite goes only with the column favorite")
        set_leaf(copy.deepcopy(item), coordinate, op.value, op.fieldType)
        _check_equal_values(op, planned, batch, lookups)
        check_hidden_fits(item, coordinate, op.fieldType)
    elif isinstance(op, RemoveLeaf):
        remove_leaf(copy.deepcopy(batch.item(op.itemId)), parse(op.column))
    elif isinstance(op, GenerateLeaf):
        check_generate_leaf(items[op.itemId], op.column)
    elif isinstance(op, MoveToFolder):
        missing = [i for i in op.itemIds if i not in op.revisionDates]
        if missing:
            raise ValueError("revisionDates must name every item in itemIds")
        if not op.itemIds:
            raise ValueError("itemIds is empty")
        if set(op.shownFolderIds or {}) - set(op.itemIds):
            raise ValueError("shownFolderIds names an item that is not in itemIds")
        _check_equal_values(op, planned, batch, lookups)
    elif isinstance(op, SetCollections):
        item = items[op.itemId]
        organization_id = item.get("organizationId")
        if not organization_id:
            raise ValueError("only an organization item has collections, use moveToOrganization")
        resolve_collections(cache, organization_id, op.collectionNames)
    elif isinstance(op, MoveToOrganization):
        item = items[op.itemId]
        if item.get("organizationId"):
            raise ValueError("the item is already in an organization")
        organization = by_name(cache.organizations, op.organizationName)
        if organization is None:
            raise ValueError(f"no organization named {op.organizationName!r}")
        resolve_collections(cache, organization["id"], op.collectionNames)
    elif isinstance(op, MoveToPersonal):
        if not items[op.itemId].get("organizationId"):
            raise ValueError("the item is already in the personal vault")
    elif isinstance(op, TrashItem):
        pass
    elif isinstance(op, ConvertItem):
        plan_conversion(items[op.itemId], op.newType, op.mapping, lookups)
    elif isinstance(op, ApplyTemplate):
        plan_template(
            items[op.itemId], template_named(cache, remembered, op.template), op.mapping, lookups
        )
    elif isinstance(op, RenameField):
        for entry in op.items:
            rename_field(copy.deepcopy(items[entry.itemId]), op.from_, op.to)
    elif isinstance(op, MergeColumns):
        sources, target = merge_columns_of(op.sources, op.target)
        for entry in op.items:
            item = copy.deepcopy(items[entry.itemId])
            merge_columns(item, sources, target, op.resolutions.get(entry.itemId), lookups)
    elif isinstance(op, MergeItems):
        if op.fromItemId == op.toItemId:
            raise ValueError("an item cannot be merged into itself")
        target = copy.deepcopy(items[op.toItemId])
        merge_items(items[op.fromItemId], target, op.mapping, lookups, op.append)
    elif isinstance(op, SetUris):
        if not op.items:
            raise ValueError("items is empty")
        if len({entry.itemId for entry in op.items}) < len(op.items):
            raise ValueError("items names an item twice")
        ## every shape check before the rule of equal values, so an error never tells whether
        ## a URI equals a secret
        for entry in op.items:
            set_uris(copy.deepcopy(batch.item(entry.itemId)), uri_pairs(entry.uris))
        _check_equal_values(op, planned, batch, lookups)
    elif isinstance(op, RestoreItems):
        if backups is None:
            raise ValueError("restoring needs the backups")
        plan_restore(op, backups.read(op.backup), cache)
    elif isinstance(op, CreateItem):
        plan_create(op, cache, remembered)
    elif isinstance(op, RenameFolder):
        check_folder_name(cache.folders, op.folderId, op.newName)
        _check_equal_values(op, planned, batch, lookups)
        ## an item in the trash or the archive gets the name too
        check_folder_name_secrets(cache.set_aside(), op.folderId, op.newName, lookups, {})
    elif isinstance(op, DeleteFolder):
        trashed = [*cache.trashed, *cache.archived_trash]
        check_folder_empty(cache.items, trashed, cache.archived, op.folderId)
    elif isinstance(op, RenameAttachment):
        ## on the item as the renames before it leave it (the request's, as enqueue_ui passes
        ## them in earlier), so two renames of two attachments of one item to one name in one
        ## request are refused at once. Entries queued by another request are not in earlier,
        ## the write path's check on the fresh item fails the second of those.
        check_attachment_rename(planned.item(op.itemId), op)
    elif isinstance(op, RestoreFolder):
        if backups is None:
            raise ValueError("restoring needs the backups")
        step = plan_folder_restore(op, backups.read(op.backup), cache.folders)
        if step.action == RENAME_BACK:
            every = [*cache.items, *cache.set_aside()]
            check_folder_name_secrets(every, op.folderId, step.name, lookups, {})
    elif isinstance(op, SetToolNoteSection):
        if tool_note_id is None:
            raise ValueError("the bwtidy note is missing, log in again")
        check_section(op)
    else:
        assert_never(op)


def _merge_conflicts(
    op: Operation, items: dict[str, dict[str, Any]], lookups: Lookups
) -> list[dict[str, Any]]:
    """The items of a rename or column merge that need a manual choice first.

    Checks the operation's own shape before, so a malformed one is a ValueError, not a
    conflict.
    """
    if isinstance(op, RenameField | MergeColumns):
        if not op.items:
            raise ValueError("items is empty")
        if len({entry.itemId for entry in op.items}) < len(op.items):
            raise ValueError("items names an item twice")
    found: list[dict[str, Any] | None] = []
    if isinstance(op, RenameField):
        if not op.to:
            raise ValueError("the new field name is empty")
        if op.to == op.from_:
            raise ValueError("the new field name is the old one")
        found = [rename_conflict(items[e.itemId], op.from_, op.to) for e in op.items]
    elif isinstance(op, MergeColumns):
        sources, target = merge_columns_of(op.sources, op.target)
        if set(op.resolutions) - {entry.itemId for entry in op.items}:
            raise ValueError("resolutions name an item that is not in items")
        merged = {target, *sources}
        if any(parse(column) not in merged for column in op.resolutions.values()):
            raise ValueError("a resolution names a column that is not merged")
        found = [
            merge_conflict(items[e.itemId], sources, target, op.resolutions.get(e.itemId), lookups)
            for e in op.items
        ]
    return [conflict for conflict in found if conflict is not None]


TOOL_NOTE_MANAGED = "the bwtidy note is managed by bwtidy, edit the templates on the Types page"


def check_tool_note(
    ops: list[Operation], tool_note_id: str | None, allowed: frozenset[int] = frozenset()
) -> None:
    """Refuse every operation that touches the tool note (InvalidOperation naming the first).

    allowed holds the indexes of bwtidy's own writes to the note (a queued section save). A
    setToolNoteSection names no item but always touches the note.
    """
    for index, op in enumerate(ops):
        if index in allowed:
            continue
        on_note = tool_note_id is not None and tool_note_id in item_ids(op)
        if on_note or isinstance(op, SetToolNoteSection):
            reason = TOOL_NOTE_RESTORE if isinstance(op, RestoreItems) else TOOL_NOTE_MANAGED
            message = f"Operation {index + 1} ({op.kind}): {reason}"
            raise InvalidOperation(message, index, reason)


def validate(
    ops: list[Operation],
    cache: VaultCache,
    remembered: ToolNoteId,
    *,
    edits_tool_note: bool = False,
    tool_note_ops: frozenset[int] = frozenset(),
    backups: BackupReader | None = None,
    earlier: Sequence[Operation] = (),
) -> None:
    """Check a batch against the freshly reloaded cache before anything is written.

    Raises InvalidOperation when an operation touches the tool note, unless edits_tool_note
    says the caller is bwtidy itself (a section save, renaming the note back) or the
    operation's index is in tool_note_ops (a queued section save). Then Conflict when a
    touched item changed or vanished since the client loaded it, then MergeConflicts when a
    rename or merge needs a manual choice first, else InvalidOperation for the first operation
    that is not allowed. An expected revisionDate that bwtidy's own write replaced is checked
    as the one it became (see OwnWrites.revisions), and so is the cached one. A restoreItems
    needs backups to read its backup, without them it is refused. Each operation is checked on
    the items as the setLeafs, removeLeafs, generateLeafs and field renames before it in the
    batch leave them, and a section
    save on the tool note as the section saves before it (earlier's too) leave it. The rule of
    equal values (plain_matches_secret) also sees those of earlier (the queue's pending
    entries, their hidden values decrypted), and the secrets of the items as they are still
    count.
    """
    note_id = tool_note_id(cache, remembered)
    if not edits_tool_note:
        check_tool_note(ops, note_id, tool_note_ops)
    items = cache.items_by_id()
    ## a restore puts an archived item back too (plan_restore), no other operation sees one
    restorable = {**cache.archived_by_id(), **items}
    conflicts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for op in ops:
        found = restorable if isinstance(op, RestoreItems) else items
        for item_id, expected in touched(op).items():
            if (item_id, expected) in seen:
                continue
            seen.add((item_id, expected))
            actual = found.get(item_id, {}).get("revisionDate")
            ## an entry is applied right after its write, and until the reload after the batch
            ## the cache still holds the item from before that write
            now = cache.own.current_revision(item_id, actual) if isinstance(actual, str) else actual
            if not same_revision(now, cache.own.current_revision(item_id, expected)):
                conflicts.append({"itemId": item_id, "expected": expected, "actual": actual})
    conflicting = {c["itemId"] for c in conflicts}
    conflicts += _shown_conflicts(ops, items, cache, conflicting)
    conflicts += _attachment_conflicts(ops, items, conflicting)
    conflicts += _folder_conflicts(ops, cache)
    if conflicts:
        raise Conflict(conflicts)
    lookups = cache.lookups()
    merge_conflicts: list[dict[str, Any]] = []
    for index, op in enumerate(ops):
        with _invalid(index, op):
            merge_conflicts += _merge_conflicts(op, items, lookups)
    if merge_conflicts:
        raise MergeConflicts(merge_conflicts)
    batch = _Planned(items)
    planned = _Planned(items)
    note = _PlannedNote(cache, remembered)
    for op in earlier:
        planned.apply(op)
        note.apply(op, strict=False)
    for index, op in enumerate(ops):
        with _invalid(index, op):
            _check_op(op, items, cache, remembered, note_id, backups, batch, planned)
            note.apply(op, strict=True)
        batch.apply(op)
        planned.apply(op)


def shown_conflict(
    cache: VaultCache, op: Operation, item_id: str, item: dict[str, Any]
) -> dict[str, Any] | None:
    """The conflict of a value op says the grid showed (operations.shown) that item no longer
    holds, None when it holds or op carries none for the item.

    It is like a revisionDate conflict (both revisionDates, which can be equal), plus the
    column that changed elsewhere.
    """
    found = shown(op).get(item_id)
    if found is None:
        return None
    column, value = found
    if cache.own.shown_holds(item_id, column, value, shown_now(item, column)):
        return None
    expected = touched(op).get(item_id, "")
    actual = item.get("revisionDate")
    return {"itemId": item_id, "expected": expected, "actual": actual, "column": column}


def _shown_conflicts(
    ops: list[Operation],
    items: dict[str, dict[str, Any]],
    cache: VaultCache,
    conflicting: set[str],
) -> list[dict[str, Any]]:
    """The conflicts of shown values in a batch, checked on the cache. An item that vanished
    or conflicts on its revisionDate already is left out, and so is one the operation does
    not touch (_check_op refuses that)."""
    conflicts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for op in ops:
        for item_id, (column, _) in shown(op).items():
            if item_id in conflicting or item_id not in items or item_id not in touched(op):
                continue
            conflict = shown_conflict(cache, op, item_id, items[item_id])
            if conflict is not None and (item_id, column) not in seen:
                seen.add((item_id, column))
                conflicts.append(conflict)
    return conflicts


def _attachment_conflicts(
    ops: list[Operation], items: dict[str, dict[str, Any]], conflicting: set[str]
) -> list[dict[str, Any]]:
    """The attachment renames whose attachment has another name than the one the proposer
    saw, checked on the cache, each once. An item that vanished or conflicts on its
    revisionDate already is left out."""
    conflicts: list[dict[str, Any]] = []
    for op in ops:
        if not isinstance(op, RenameAttachment) or op.itemId in conflicting:
            continue
        item = items.get(op.itemId)
        conflict = attachment_conflict(item, op) if item is not None else None
        if conflict is not None and conflict not in conflicts:
            conflicts.append(conflict)
    return conflicts


def _folder_conflicts(ops: list[Operation], cache: VaultCache) -> list[dict[str, Any]]:
    """The folders of renames and deletes that are gone or no longer have the name the client
    showed, each once."""
    conflicts: list[dict[str, Any]] = []
    for op in ops:
        conflict = folder_conflict(cache.folders, op)
        if conflict is not None and conflict not in conflicts:
            conflicts.append(conflict)
    return conflicts


@contextmanager
def _invalid(index: int, op: Operation) -> Generator[None]:
    """Turn the ValueError of one operation into InvalidOperation naming it."""
    try:
        yield
    except ValueError as error:
        message = f"Operation {index + 1} ({op.kind}): {error}"
        secret_rule = isinstance(error, SecretIntoPlain)
        raise InvalidOperation(message, index, str(error), secret_rule) from None
