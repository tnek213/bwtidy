"""The writes of one item or a few: a leaf, a move to a folder, the collections, a move into
an organization, the trash, a field rename, a column or item merge, the URIs (docs/spec.md,
Writes).

Each function applies one kind of operation: GET the full item through the path, which checks
it against the batch's check and the chain of own writes, change only the touched leaves, PUT
the whole object back. Nothing here puts a value into an error message or a log line.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from bwtidy.items.coordinates import parse, remove_leaf, set_leaf, set_uris
from bwtidy.items.normalize import merge_columns, merge_columns_of, merge_items, rename_field
from bwtidy.operations import (
    MergeColumns,
    MergeItems,
    MoveToFolder,
    MoveToOrganization,
    RemoveLeaf,
    RenameField,
    SetCollections,
    SetLeaf,
    SetUris,
    TrashItem,
    uri_pairs,
)
from bwtidy.writes.folders import folder_id
from bwtidy.writes.planners import by_name, resolve_collections
from bwtidy.writes.rules import check_set_leaf_secrets, plain_matches_secret

if TYPE_CHECKING:
    from bwtidy.writes.path import Write, WriteContext


async def write_set_leaf(path: WriteContext, write: Write, op: SetLeaf) -> None:
    full = await path.get_checked(write, op.itemId)
    original = path.cache.items_by_id().get(op.itemId)
    check_set_leaf_secrets(op, full, path.cache.lookups(), original)
    set_leaf(full, parse(op.column), op.value, op.fieldType)
    await path.put(write, op.itemId, full)


async def write_remove_leaf(path: WriteContext, write: Write, op: RemoveLeaf) -> None:
    full = await path.get_checked(write, op.itemId)
    remove_leaf(full, parse(op.column))
    await path.put(write, op.itemId, full)


async def write_move_to_folder(path: WriteContext, write: Write, op: MoveToFolder) -> None:
    ## every item is checked before the folder is created and the first one written
    moved = {item_id: await path.get_checked(write, item_id) for item_id in op.itemIds}
    originals = path.cache.items_by_id()
    for item_id, full in moved.items():
        plain_matches_secret(
            full,
            full,
            path.cache.lookups(),
            folder=op.folderName,
            original=originals.get(item_id),
        )
    folder = await folder_id(path, op.folderName)
    for item_id, full in moved.items():
        full["folderId"] = folder
        await path.put(write, item_id, full)


async def write_set_collections(path: WriteContext, write: Write, op: SetCollections) -> None:
    full = await path.get_checked(write, op.itemId)
    ids = resolve_collections(path.cache, full.get("organizationId") or "", op.collectionNames)
    answer = await path.client.put_item_collections(op.itemId, ids)
    path.record(op.itemId, full.get("revisionDate"), answer)
    path.record_collections(op.itemId, full, ids)


async def write_move_to_organization(
    path: WriteContext, write: Write, op: MoveToOrganization
) -> None:
    organization = by_name(path.cache.organizations, op.organizationName)
    if organization is None:
        raise ValueError(f"no organization named {op.organizationName!r}")
    ids = resolve_collections(path.cache, organization["id"], op.collectionNames)
    full = await path.get_checked(write, op.itemId)
    before = full.get("revisionDate")
    move = path.client.move_to_organization(op.itemId, organization["id"], ids)
    answer = await path.unless_stale(write, op.itemId, before, move)
    path.record(op.itemId, before, answer)
    path.record_collections(op.itemId, full, ids)


async def write_trash_item(path: WriteContext, write: Write, op: TrashItem) -> None:
    await path.get_checked(write, op.itemId)
    await path.client.trash_item(op.itemId)


async def write_rename_field(path: WriteContext, write: Write, op: RenameField) -> None:
    for entry in op.items:
        full = await path.get_checked(write, entry.itemId)
        rename_field(full, op.from_, op.to)
        await path.put(write, entry.itemId, full)


async def write_merge_columns(path: WriteContext, write: Write, op: MergeColumns) -> None:
    sources, target = merge_columns_of(op.sources, op.target)
    for entry in op.items:
        full = await path.get_checked(write, entry.itemId)
        resolution = op.resolutions.get(entry.itemId)
        if merge_columns(full, sources, target, resolution, path.cache.lookups()):
            await path.put(write, entry.itemId, full)


async def write_merge_items(path: WriteContext, write: Write, op: MergeItems) -> None:
    source = await path.get_checked(write, op.fromItemId)
    full = await path.get_checked(write, op.toItemId)
    merge_items(source, full, op.mapping, path.cache.lookups(), op.append)
    await path.put(write, op.toItemId, full)
    await path.client.trash_item(op.fromItemId)


async def write_set_uris(path: WriteContext, write: Write, op: SetUris) -> None:
    """Every item checked (its revisionDate, the URIs shown, the rules) before the first
    one is written, like a move of several items."""
    originals = path.cache.items_by_id()
    lookups = path.cache.lookups()
    planned = []
    for entry in op.items:
        full = await path.get_checked(write, entry.itemId)
        after = copy.deepcopy(full)
        set_uris(after, uri_pairs(entry.uris))
        plain_matches_secret(after, full, lookups, original=originals.get(entry.itemId))
        planned.append((entry.itemId, after))
    for item_id, after in planned:
        await path.put(write, item_id, after)
