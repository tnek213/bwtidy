"""What comes into the queue: an agent's proposal, the UI's edits and a section save, each checked
against the vault in RAM before it is stored.

Nothing that goes into SQLite carries a secret value: an agent's setLeaf into a secret column or
createItem with secretValues is never queued, a UI edit of a secret column and the secretValues
of a UI createItem are stored as the age ciphertexts of their values (see vault/hidden.py). An
agent's setLeaf, moveToFolder, renameFolder or setUris whose value (a URI) equals a secret of the
item (also one queued, for a folder of an item in it) is stored already rejected with the value
masked, and the agent gets the same answer as for an accepted proposal, so a refusal cannot
confirm a guess.
"""

import json
import logging
from collections.abc import Sequence
from typing import Any

from bwtidy.backups import BackupReader
from bwtidy.db import NewQueueEntry
from bwtidy.items.coordinates import MASKED, URIS, field_types, flatten, is_secret, parse
from bwtidy.operations import (
    CreateItem,
    InvalidOperation,
    MoveToFolder,
    Operation,
    RenameFolder,
    RestoreFolder,
    RestoreItems,
    SetLeaf,
    SetToolNoteSection,
    SetUris,
    item_ids,
)
from bwtidy.queue.entries import with_values_decrypted
from bwtidy.queue.sections import section_saves
from bwtidy.queue.state import (
    APPROVED,
    MCP,
    PENDING,
    REJECTED,
    UI,
    QueueState,
    as_operation,
    dump_operation,
)
from bwtidy.queue.view import plural, row, summarize
from bwtidy.sections import Section, SectionChange
from bwtidy.vault import HiddenValueError, ToolNoteId, VaultCache, find_tool_note
from bwtidy.writes import TOOL_NOTE_RESTORE, in_folder, planned_item, validate

log = logging.getLogger(__name__)

SECRET_REFUSED = "the proposal would put a secret value of the item into a non-secret column"


class VaultLocked(Exception):
    """No unlocked vault to check an operation against."""


def _targets_secret(op: SetLeaf, item: dict[str, Any] | None, earlier: Sequence[Operation]) -> bool:
    """Whether op sets a secret column of the item, as the cache has it or as the operations of
    earlier leave it (see planned_item: a field a queued setLeaf or generateLeaf adds hidden, a
    hidden field a queued renameField gives the column's name)."""
    try:
        coordinate = parse(op.column)
    except ValueError as error:
        raise InvalidOperation(str(error)) from None

    def secret_on(shape: dict[str, Any] | None) -> bool:
        existing = field_types(shape).get(coordinate) if shape is not None else None
        return is_secret(coordinate, existing if existing is not None else op.fieldType or 0)

    ## the cached item counts too: a hidden field a queued removeLeaf takes away is added
    ## back hidden when the commit writes the encrypted value (with_values_decrypted)
    return secret_on(item) or (item is not None and secret_on(planned_item(item, earlier)))


def check_enqueue(
    op: Operation,
    cache: VaultCache,
    remembered: ToolNoteId,
    *,
    source: str = MCP,
    edits_tool_note: bool = False,
    backups: BackupReader | None = None,
    earlier: Sequence[Operation] = (),
) -> bool:
    """The checks an entry passes before it is stored. True when its value needs encrypting.

    Raises InvalidOperation for an operation on the tool note (unless edits_tool_note names
    bwtidy's own section save), a setLeaf into a secret column or a createItem with
    secretValues from an agent and anything the write path would refuse, Conflict for an item
    that changed or vanished, MergeConflicts for a merge that needs a choice. A UI setLeaf into
    a secret column and a UI createItem's secretValues pass, stored only encrypted. earlier
    holds the edits queued before it (see queued_leaves and validate), for the rule of equal
    values and for the field types a setLeaf's column has after them.
    """
    items = cache.items_by_id()
    note = find_tool_note(cache, remembered)
    if not edits_tool_note and note is not None and note.get("id") in item_ids(op):
        if isinstance(op, RestoreItems):
            raise InvalidOperation(TOOL_NOTE_RESTORE)
        raise InvalidOperation("the bwtidy note cannot be changed through the queue")
    secret = isinstance(op, SetLeaf) and _targets_secret(op, items.get(op.itemId), earlier)
    ## the queue stores the operation, and an agent's value may never be stored as a secret
    if secret and source != UI:
        raise InvalidOperation(
            f"{op.column} is a secret column, the queue never takes a secret value"
        )
    if isinstance(op, CreateItem) and op.secretValues:
        secret = True
        if source != UI:
            raise InvalidOperation(
                f"{', '.join(op.secretValues)}: the queue never takes a secret value"
            )
    validate(
        [op], cache, remembered, edits_tool_note=edits_tool_note, backups=backups, earlier=earlier
    )
    return secret


def enqueue(q: QueueState, op: Operation, summary: str, source: str = MCP) -> int:
    """Check an agent's operation and store it as a pending entry. Returns the entry id.

    An agent's setLeaf that breaks the secret rule is stored as rejected instead and its id
    returned like any other. Raises VaultLocked without an unlocked vault, else what
    check_enqueue raises.
    """
    if not q.cache.loaded:
        raise VaultLocked("the vault is locked")
    ## before the check, so a refused and an accepted proposal pay for it alike
    agent_summary = q.hidden.encrypt(summary) if source == MCP else None
    earlier = queued_leaves(q, [op])
    try:
        check_enqueue(
            op,
            q.cache,
            q.remembered,
            source=source,
            backups=q.backups,
            earlier=earlier,
        )
    except InvalidOperation as error:
        silent = isinstance(op, SetLeaf | MoveToFolder | RenameFolder | SetUris)
        if not (error.secret_rule and source == MCP and silent):
            raise
        entry_id = store_refused_secret(q, op, source, agent_summary)
    else:
        entry_id = q.db.add_queue_entry(
            source, summary, json.dumps(dump_operation(op)), agent_summary=agent_summary
        )
    log.info("Queued entry %d (%s) from %s", entry_id, op.kind, source)
    return entry_id


def enqueue_ui(q: QueueState, ops: list[Operation]) -> list[int]:
    """Queue the UI's operations, one entry each in order. Returns the ids.

    Every operation is checked first and nothing is stored when one fails (the errors of
    check_enqueue). A setLeaf into a secret column and a createItem's secretValues are
    stored encrypted to the identity for hidden values, so SQLite never sees the value.
    """
    if not q.cache.loaded:
        raise VaultLocked("the vault is locked")
    backups = q.backups
    earlier = queued_leaves(q, ops)
    secret = [
        check_enqueue(
            op,
            q.cache,
            q.remembered,
            source=UI,
            backups=backups,
            earlier=[*earlier, *ops[:index]],
        )
        for index, op in enumerate(ops)
    ]
    rows = [
        ui_row(q, op, summarize(op, q.cache), encrypt)
        for op, encrypt in zip(ops, secret, strict=True)
    ]
    return store_ui(q, ops, rows)


def enqueue_section_changes(
    q: QueueState, section: Section, changes: list[SectionChange], summary: str
) -> int:
    """Queue changes of one section of the tool note, the one UI entry allowed to touch it,
    applied to the note as it is when they are written (see SetToolNoteSection).

    Raises InvalidOperation when a change does not fit the section or does not apply to it
    as the saves still under way leave the note (a template one of them deleted),
    VaultLocked without an unlocked vault.
    """
    if not q.cache.loaded:
        raise VaultLocked("the vault is locked")
    op = SetToolNoteSection(kind="setToolNoteSection", section=section, changes=changes)
    ## every section's saves, as a datatype's removal is checked against the templates
    earlier = section_saves(q, None)
    check_enqueue(op, q.cache, q.remembered, source=UI, edits_tool_note=True, earlier=earlier)
    shown = ui_row(q, op, summary, False, edits_tool_note=True)
    return store_ui(q, [op], [shown])[0]


def queued_leaves(q: QueueState, ops: list[Operation]) -> list[Operation]:
    """The pending and approved setLeafs, removeLeafs, generateLeafs, setUris and renameFields
    of the items ops touch, in id order, their hidden values decrypted into RAM only. One that
    cannot be read is left out, its commit fails it."""
    wanted = {item_id for op in ops for item_id in item_ids(op)}
    ## a folder's new name goes into every item in it
    for op in ops:
        if isinstance(op, RenameFolder | RestoreFolder):
            wanted |= {i["id"] for i in in_folder(q.cache.items, op.folderId)}
    found = []
    for stored in q.db.queue_entries(statuses=(PENDING, APPROVED)):
        operation = json.loads(stored["operation_json"])
        if operation.get("kind") in ("setUris", "renameField"):
            ids = {entry.get("itemId") for entry in operation.get("items") or []}
        elif operation.get("kind") in ("setLeaf", "removeLeaf", "generateLeaf"):
            ids = {operation.get("itemId")}
        else:
            continue
        if not ids & wanted:
            continue
        try:
            op = as_operation(operation)
            found.append(with_values_decrypted(q, op) if stored["value_encrypted"] else op)
        except (InvalidOperation, HiddenValueError):
            continue
    return found


def ui_row(
    q: QueueState, op: Operation, summary: str, encrypt: bool, *, edits_tool_note: bool = False
) -> NewQueueEntry:
    stored = dump_operation(op)
    if encrypt and isinstance(op, CreateItem):
        stored["secretValues"] = {
            column: q.hidden.encrypt(value) for column, value in op.secretValues.items()
        }
    elif encrypt:
        assert isinstance(op, SetLeaf)
        value = op.value if isinstance(op.value, str) else json.dumps(op.value)
        stored["value"] = q.hidden.encrypt(value)
    return NewQueueEntry(
        UI,
        summary,
        json.dumps(stored),
        value_encrypted=encrypt,
        edits_tool_note=edits_tool_note,
    )


def store_ui(q: QueueState, ops: list[Operation], rows: list[NewQueueEntry]) -> list[int]:
    """Store the prepared rows in one transaction, then tell on_ui_entry of each id."""
    ids = q.db.add_queue_entries(rows)
    for op, entry_id in zip(ops, ids, strict=True):
        log.info("Queued entry %d (%s) from %s", entry_id, op.kind, UI)
    for entry_id in ids:
        q.on_ui_entry(entry_id)
    return ids


def store_refused_secret(
    q: QueueState,
    op: SetLeaf | MoveToFolder | RenameFolder | SetUris,
    source: str,
    agent_summary: str | None,
) -> int:
    """Store a setLeaf, moveToFolder, renameFolder or setUris that broke the secret rule as
    an entry rejected at once.

    The agent hears the same answer as for an accepted proposal, so a refusal cannot
    confirm a guessed secret. The user sees the refusal in the queue. Neither the agent's value
    (a setLeaf's value, a folder name, the URIs) nor its reason is stored in plain, the value
    could be the secret: only agent_summary holds them, the age ciphertext of the summary the
    agent was answered.
    """
    items = q.cache.items_by_id()
    lookups = q.cache.lookups()
    operation = dump_operation(op)
    if isinstance(op, SetLeaf):
        column = op.column
        operation["value"] = MASKED
        moved = [items[op.itemId]] if op.itemId in items else []
    elif isinstance(op, SetUris):
        column = URIS
        operation["items"] = [{**entry, "uris": MASKED} for entry in operation["items"]]
        moved = [items[entry.itemId] for entry in op.items if entry.itemId in items]
    elif isinstance(op, MoveToFolder):
        column = "folder"
        operation["folderName"] = MASKED
        moved = [items[item_id] for item_id in op.itemIds if item_id in items]
    else:
        ## an item in the trash would get the folder's name too
        column = "folder"
        operation["newName"] = MASKED
        moved = in_folder([*q.cache.items, *q.cache.trashed], op.folderId)
    rows = [row(item, column, flatten(item, lookups).get(parse(column)), MASKED) for item in moved]
    if isinstance(op, RenameFolder):
        summary = (
            f'Refused proposal for the folder "{op.shownName}": it would put a secret value'
            " of an item in it into its name"
        )
    elif len(moved) == 1:
        summary = (
            f'Refused proposal for "{moved[0].get("name") or ""}": it would put a secret'
            f" value of the item into {column}"
        )
    else:
        summary = (
            f"Refused proposal for {plural(len(moved), 'item', 'items')}: it would put a"
            f" secret value of an item into {column}"
        )
    shown = {"diff": rows, "error": None}
    result = {"error": "secretValue", "detail": SECRET_REFUSED}
    return q.db.add_queue_entry(
        source,
        summary,
        json.dumps(operation),
        status=REJECTED,
        result=json.dumps(result),
        diff_json=json.dumps(shown),
        agent_summary=agent_summary,
        refused_secret=True,
    )
