"""What the Queue page shows of an entry: its summary line and its diff, never a secret value.

_shown masks every secret value in a diff (docs/spec.md, Secret values). The summaries of MCP
proposals are written by the tools themselves (mcp/propose_tools.py), only the UI entries, the
restore plans and the section saves are summarized here.
"""

import contextlib
import copy
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any, assert_never

from bwtidy.backups import BackupContents, BackupReader
from bwtidy.items.coordinates import (
    ITEM_TYPES,
    MASKED,
    URIS,
    Coordinate,
    Lookups,
    Value,
    field_types,
    flatten,
    is_secret,
    parse,
    remove_leaf,
    set_leaf,
    set_uris,
    sort_key,
    uri_entries,
    uris_shown,
)
from bwtidy.items.normalize import merge_columns, merge_columns_of, merge_items, rename_field
from bwtidy.items.ssh_keys import fill_in
from bwtidy.operations import (
    ApplyTemplate,
    ConvertItem,
    CreateItem,
    DeleteFolder,
    GenerateLeaf,
    InvalidOperation,
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
    uri_pairs,
)
from bwtidy.queue.sections import folded_section
from bwtidy.sections import with_section
from bwtidy.vault import ToolNoteId, VaultCache, find_tool_note, template_named
from bwtidy.writes import (
    LEAVE_FOLDER,
    PUT,
    TRASH,
    check_attachment_rename,
    check_generate_leaf,
    folder_with_id,
    generated_coordinates,
    placed_collections,
    plan_conversion,
    plan_create,
    plan_folder_restore,
    plan_restore,
    plan_template,
)

TEMPLATES_SUMMARY = "Save the templates"
GENERATOR_SUMMARY = "Save the generator settings"
DATATYPES_SUMMARY = "Save the datatypes"

## the pseudo column of a diff row that moves an item to the trash
TRASH_COLUMN = "trash"
## the pseudo column of a diff row that says what a restore does with an item
RESTORE_COLUMN = "restore"
## the column of the one diff row of a folder kind, which names the folder and no item
FOLDER_COLUMN = "folder"
## what the diff of a createItem or a generateLeaf shows for a value generated at write time
GENERATED = "(generated)"
## the words a summary names a new item's type with
TYPE_WORDS = {1: "login", 2: "secure note", 3: "card", 4: "identity", 5: "SSH key"}
## info columns that change with every write or come back on their own, never worth a row
NOT_DIFFED = frozenset({Coordinate.at("revisionDate"), Coordinate.at("attachments")})


def _shown(value: Value | None, secret: bool) -> Any:
    if value is None:
        return None
    return MASKED if secret else value


def row(item: dict[str, Any], column: str, before: Any, after: Any) -> dict[str, Any]:
    return {
        "itemId": item.get("id"),
        "itemName": item.get("name"),
        "column": column,
        "before": before,
        "after": after,
    }


def _cells(before: dict[str, Any], after: dict[str, Any], lookups: Lookups) -> list[dict]:
    """One row per coordinate whose value differs between the two states of an item."""
    old, new = flatten(before, lookups), flatten(after, lookups)
    old_types, new_types = field_types(before), field_types(after)
    rows = []
    for coordinate in sorted(old.keys() | new.keys(), key=sort_key):
        if coordinate in NOT_DIFFED:
            continue
        was, now = old.get(coordinate), new.get(coordinate)
        if was == now:
            continue
        secret = is_secret(coordinate, old_types.get(coordinate)) or is_secret(
            coordinate, new_types.get(coordinate)
        )
        rows.append(row(before, coordinate.display, _shown(was, secret), _shown(now, secret)))
    return rows


def _uris_row(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """The row of a setUris: login.uris as the grid shows it (the uri strings, None for none),
    plus uris with both lists and every match setting, which the grid's cell does not show."""
    was, now = uri_entries(before), uri_entries(after)
    shown = row(before, URIS, [u for u, _ in was] or None, [u for u, _ in now] or None)
    shown["uris"] = {"before": uris_shown(was), "after": uris_shown(now)}
    return shown


def _put_back_cells(
    now: dict[str, Any],
    saved: dict[str, Any],
    contents: BackupContents,
    cache: VaultCache,
    lookups: Lookups,
) -> list[dict[str, Any]]:
    """The cell rows of an item a restore puts back, in the place the write path puts it.

    A deleted folder shows under its name in the backup, a collection left out does not show.
    """
    shown = dict(saved)
    folders = {**contents.folder_names, **lookups.folders}
    if saved.get("folderId") not in folders:
        shown["folderId"] = None
    if saved.get("organizationId"):
        shown["collectionIds"] = placed_collections(saved, now, contents, cache.collections)
    return _cells(now, shown, replace(lookups, folders=folders))


def diff(
    op: Operation,
    cache: VaultCache,
    remembered: ToolNoteId,
    backups: BackupReader | None = None,
    earlier: Sequence[SetToolNoteSection] = (),
    hidden: bool = False,
) -> list[dict[str, Any]]:
    """Before and after of every cell the operation changes, against the vault in RAM.

    Secret values are {"masked": true}, a missing value is None. A trashed item is one row in
    the pseudo column "trash". A restore has one row per item in the pseudo column "restore"
    saying what happens to it, plus the cell rows of the items it puts back as they were. A
    folder kind has one row of the folder in the column folder, without an item id, from its
    name now to the new one (None for a delete), a restore of a folder its row in "restore"
    first. An attachment rename is one row of the item in the column attachments, its file names
    before and after. A section save shows the note's notes with the saves of earlier (those
    queued before it) applied as its before. A setUris has one row per item in login.uris (see
    _uris_row). hidden says a setLeaf's value is a queued hidden value (an age ciphertext), its
    cells shown masked whatever type the field has in the vault now. Raises ValueError when the
    operation no longer fits the vault.
    """
    items = cache.items_by_id()
    lookups = cache.lookups()
    rows: list[dict[str, Any]] = []

    def item(item_id: str) -> dict[str, Any]:
        found = items.get(item_id)
        if found is None:
            raise ValueError(f"item {item_id} is not in the vault")
        return found

    def edit(item_id: str, change: Callable[[dict[str, Any]], Any]) -> None:
        before = item(item_id)
        after = copy.deepcopy(before)
        planned = change(after)
        written = planned if isinstance(planned, dict) else after
        ## the write fills an SSH key's public key and fingerprint in (items/ssh_keys.py), shown
        ## here when the private key is in RAM in plain (a queued hidden value is a
        ## ciphertext), a key that cannot give them fails the write, not the diff
        with contextlib.suppress(ValueError):
            fill_in(written)
        rows.extend(_cells(before, written, lookups))

    def organizational(item_id: str, column: str, now: Value | None) -> None:
        before = item(item_id)
        was = flatten(before, lookups).get(Coordinate.at(column))
        if was != now:
            rows.append(row(before, column, was, now))

    def names(collections: list[str]) -> list[str] | None:
        return sorted(collections, key=str.casefold) or None

    def folder_name(folder_id: str) -> str:
        found = folder_with_id(cache.folders, folder_id)
        if found is None:
            raise ValueError(f"the folder {folder_id} is not in the vault")
        return found.get("name") or ""

    def folder_row(name: str, before: str | None, after: str | None) -> None:
        rows.append(row({"id": None, "name": name}, FOLDER_COLUMN, before, after))

    if isinstance(op, SetLeaf):
        edit(op.itemId, lambda i: set_leaf(i, parse(op.column), op.value, op.fieldType))
        if hidden:
            for cell in rows:
                cell["before"] = _shown(cell["before"], True)
                cell["after"] = _shown(cell["after"], True)
    elif isinstance(op, RemoveLeaf):
        edit(op.itemId, lambda i: remove_leaf(i, parse(op.column)))
    elif isinstance(op, GenerateLeaf):
        before = item(op.itemId)
        coordinate = check_generate_leaf(before, op.column)
        was = flatten(before, lookups).get(coordinate)
        rows.append(row(before, coordinate.display, _shown(was, True), GENERATED))
    elif isinstance(op, MoveToFolder):
        for item_id in op.itemIds:
            organizational(item_id, "folder", op.folderName or None)
    elif isinstance(op, SetCollections):
        organizational(op.itemId, "collections", names(op.collectionNames))
    elif isinstance(op, MoveToOrganization):
        organizational(op.itemId, "organization", op.organizationName)
        organizational(op.itemId, "collections", names(op.collectionNames))
    elif isinstance(op, MoveToPersonal):
        organizational(op.itemId, "organization", None)
        organizational(op.itemId, "collections", None)
    elif isinstance(op, TrashItem):
        rows.append(row(item(op.itemId), TRASH_COLUMN, False, True))
    elif isinstance(op, ConvertItem):
        edit(op.itemId, lambda i: plan_conversion(i, op.newType, op.mapping, lookups))
    elif isinstance(op, ApplyTemplate):
        template = template_named(cache, remembered, op.template)
        edit(op.itemId, lambda i: plan_template(i, template, op.mapping, lookups))
    elif isinstance(op, RenameField):
        for entry in op.items:
            edit(entry.itemId, lambda i: rename_field(i, op.from_, op.to))
    elif isinstance(op, MergeColumns):
        sources, target = merge_columns_of(op.sources, op.target)
        for entry in op.items:
            resolution = op.resolutions.get(entry.itemId)
            edit(
                entry.itemId,
                lambda i, r=resolution: merge_columns(i, sources, target, r, lookups),
            )
    elif isinstance(op, MergeItems):
        source = item(op.fromItemId)
        edit(op.toItemId, lambda i: merge_items(source, i, op.mapping, lookups, op.append))
        rows.append(row(source, TRASH_COLUMN, False, True))
    elif isinstance(op, SetUris):
        for entry in op.items:
            before = item(entry.itemId)
            after = copy.deepcopy(before)
            set_uris(after, uri_pairs(entry.uris))
            rows.append(_uris_row(before, after))
    elif isinstance(op, RestoreItems):
        if backups is None:
            raise ValueError("restoring needs the backups")
        contents = backups.read(op.backup)
        for step in plan_restore(op, contents, cache):
            shown = {"id": step.item_id, "name": step.name}
            rows.append(row(shown, RESTORE_COLUMN, None, step.text))
            if step.action == PUT:
                saved = contents.items[step.item_id]
                rows.extend(_put_back_cells(item(step.item_id), saved, contents, cache, lookups))
            elif step.action == TRASH:
                rows.append(row(item(step.item_id), TRASH_COLUMN, False, True))
    elif isinstance(op, CreateItem):
        rows.extend(_created_rows(op, cache, remembered, lookups))
    elif isinstance(op, RenameFolder):
        now = folder_name(op.folderId)
        folder_row(now, now, op.newName)
    elif isinstance(op, DeleteFolder):
        now = folder_name(op.folderId)
        folder_row(now, now, None)
    elif isinstance(op, RestoreFolder):
        if backups is None:
            raise ValueError("restoring needs the backups")
        step = plan_folder_restore(op, backups.read(op.backup), cache.folders)
        shown = step.now if step.now is not None else step.name
        rows.append(row({"id": None, "name": shown}, RESTORE_COLUMN, None, step.text))
        if step.action != LEAVE_FOLDER:
            folder_row(shown, step.now, step.name)
    elif isinstance(op, RenameAttachment):
        before = item(op.itemId)
        renamed = check_attachment_rename(before, op)
        listed = before.get("attachments") or []
        was = [a.get("fileName") or "" for a in listed]
        now = [op.fileName if a is renamed else a.get("fileName") or "" for a in listed]
        rows.append(row(before, "attachments", was, now))
    elif isinstance(op, SetToolNoteSection):
        note = find_tool_note(cache, remembered)
        if note is None or not note.get("id"):
            raise ValueError("the bwtidy note is missing")
        before = item(note["id"])
        notes = before.get("notes")
        section = op.section
        if earlier:
            notes = with_section(notes, section, folded_section(section, notes, earlier, cache))
        after = with_section(notes, section, folded_section(section, notes, [op], cache))
        rows.extend(_cells({**before, "notes": notes}, {**before, "notes": after}, lookups))
    else:
        assert_never(op)
    return rows


def _created_rows(
    op: CreateItem, cache: VaultCache, remembered: ToolNoteId, lookups: Lookups
) -> list[dict[str, Any]]:
    """The rows of a new item: one per value given, before empty, the item without an id.

    A secretValues column is always masked, its value here is a ciphertext or the value itself.
    """
    planned = plan_create(op, cache, remembered)
    types = field_types(planned)
    shown = {"id": None, "name": op.name}
    given: dict[Coordinate, Value] = {Coordinate.at("name"): op.name}
    given[Coordinate.at("type")] = ITEM_TYPES.get(planned["type"], str(planned["type"]))
    if op.folderName:
        given[Coordinate.at("folder")] = op.folderName
    if op.organizationName:
        given[Coordinate.at("organization")] = op.organizationName
        given[Coordinate.at("collections")] = sorted(op.collectionNames, key=str.casefold)
    for column, value in op.values.items():
        given[parse(column)] = value
    masked = set()
    for column, value in op.secretValues.items():
        masked.add(parse(column))
        given[parse(column)] = value
    rows = []
    for coordinate in sorted(given, key=sort_key):
        secret = coordinate in masked or is_secret(coordinate, types.get(coordinate))
        rows.append(row(shown, coordinate.display, None, _shown(given[coordinate], secret)))
    for coordinate in generated_coordinates(op):
        rows.append(row(shown, coordinate.display, None, GENERATED))
    return rows


def safe_diff(
    op: Operation,
    cache: VaultCache,
    remembered: ToolNoteId,
    backups: BackupReader | None = None,
    earlier: Sequence[SetToolNoteSection] = (),
    hidden: bool = False,
) -> dict[str, Any]:
    """The diff, or no rows and the reason when the operation no longer fits the vault."""
    try:
        return {"diff": diff(op, cache, remembered, backups, earlier, hidden), "error": None}
    except (ValueError, InvalidOperation) as error:
        return {"diff": [], "error": str(error)}


def plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _quoted(item: dict[str, Any] | None) -> str:
    return f'"{(item or {}).get("name") or ""}"'


def summarize(op: Operation, cache: VaultCache) -> str:
    """One line for a UI entry, naming items and columns but never a value."""
    items = cache.items_by_id()
    if isinstance(op, SetLeaf):
        return f"Set {op.column} of {_quoted(items.get(op.itemId))}"
    if isinstance(op, RemoveLeaf):
        return f"Remove {op.column} of {_quoted(items.get(op.itemId))}"
    if isinstance(op, GenerateLeaf):
        return f"Generate a new {op.column} for {_quoted(items.get(op.itemId))}"
    if isinstance(op, MoveToFolder):
        count = plural(len(op.itemIds), "item", "items")
        if not op.folderName:
            return f"Move {count} out of their folder"
        return f'Move {count} to the folder "{op.folderName}"'
    if isinstance(op, SetCollections):
        return f"Set the collections of {_quoted(items.get(op.itemId))}"
    if isinstance(op, MoveToOrganization):
        return f'Move {_quoted(items.get(op.itemId))} into "{op.organizationName}"'
    if isinstance(op, MoveToPersonal):
        return f"Move {_quoted(items.get(op.itemId))} to the personal vault"
    if isinstance(op, TrashItem):
        return f"Trash {_quoted(items.get(op.itemId))}"
    if isinstance(op, ConvertItem):
        kind = ITEM_TYPES.get(op.newType, str(op.newType))
        return f"Convert {_quoted(items.get(op.itemId))} to a {kind}"
    if isinstance(op, ApplyTemplate):
        return f'Apply the template "{op.template}" to {_quoted(items.get(op.itemId))}'
    if isinstance(op, RenameField):
        count = plural(len(op.items), "item", "items")
        return f"Rename the field {op.from_} to {op.to} in {count}"
    if isinstance(op, MergeColumns):
        count = plural(len(op.items), "item", "items")
        return f"Merge {', '.join(op.sources)} into {op.target} in {count}"
    if isinstance(op, MergeItems):
        source, target = items.get(op.fromItemId), items.get(op.toItemId)
        return f"Merge {_quoted(source)} into {_quoted(target)} and trash {_quoted(source)}"
    if isinstance(op, SetUris):
        if len(op.items) == 1:
            return f"Set the URIs of {_quoted(items.get(op.items[0].itemId))}"
        return f"Set the URIs of {plural(len(op.items), 'item', 'items')}"
    if isinstance(op, RestoreItems):
        return f"Restore {plural(len(op.itemIds), 'item', 'items')} from {op.backup}"
    if isinstance(op, CreateItem):
        return summarize_create(op)
    if isinstance(op, RenameFolder):
        return f'Rename the folder "{op.shownName}" to "{op.newName}"'
    if isinstance(op, DeleteFolder):
        return f'Delete the folder "{op.shownName}"'
    if isinstance(op, RestoreFolder):
        folder = folder_with_id(cache.folders, op.folderId)
        if folder is None:
            return f"Restore a deleted folder from {op.backup}"
        return f'Restore the folder "{folder.get("name") or ""}" from {op.backup}'
    if isinstance(op, RenameAttachment):
        return summarize_rename_attachment(op, items.get(op.itemId))
    if isinstance(op, SetToolNoteSection):
        return f"Save the {op.section} section of the bwtidy note"
    assert_never(op)


def summarize_rename_attachment(op: RenameAttachment, item: dict[str, Any] | None) -> str:
    """Rename attachment "OLD" of "ITEM" to "NEW", OLD the name the proposer saw."""
    return f'Rename attachment "{op.shownFileName}" of {_quoted(item)} to "{op.fileName}"'


def summarize_create(op: CreateItem) -> str:
    """Create login "NAME", or Create "NAME" from template "TEMPLATE", and whether a password
    or other values are generated."""
    if op.template is not None:
        summary = f'Create "{op.name}" from template "{op.template}"'
    else:
        word = TYPE_WORDS.get(op.type or 0, "item")
        summary = f'Create {word} "{op.name}"'
    if op.generateColumns:
        return f"{summary} with generated values"
    return f"{summary} with a generated password" if op.generatePassword else summary
