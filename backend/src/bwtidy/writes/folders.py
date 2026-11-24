"""The folder writes: the folder of a move or a new item, created when the name is new, and
renameFolder, deleteFolder and restoreFolder, which write a folder and no item (docs/spec.md,
Writes).

A folder write is checked against bw serve's folders right before it, like an item: the name
the client showed still the folder's, the new one free, no secret of an item in the folder
(the trash included) equal to it, no item left in a folder to delete. Every folder a batch
renames, creates or deletes is remembered with _wrote_folder, so a later operation of the
batch sees it as written while bw serve may still list it as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bwtidy.operations import DeleteFolder, Operation, RenameFolder, RestoreFolder
from bwtidy.vault import list_items_settled
from bwtidy.writes.check import ChangedDuringBatch
from bwtidy.writes.planners import (
    CREATE_AGAIN,
    RENAME_BACK,
    by_name,
    check_folder_empty,
    check_folder_name,
    check_folder_name_secrets,
    folder_conflict,
    plan_folder_restore,
)

if TYPE_CHECKING:
    from bwtidy.writes.path import Write, WriteContext


async def _folders_now(path: WriteContext, write: Write) -> list[dict[str, Any]]:
    """The folders as bw serve lists them, after a sync when the batch's last one is old,
    with this batch's own folder writes applied.

    Right after an own write bw serve answers a folder as it was before it for a moment, like
    an item (see WriteContext.past_own_write), and it knows a folder it created only after a
    sync.
    """
    await path.fresh(write)
    own = write.batch.folders
    listed = [
        {**f, "name": own[f["id"]]} if f.get("id") in own else f
        for f in await path.client.list_folders()
        if f.get("id") and own.get(f["id"], "") is not None
    ]
    known = {f["id"] for f in listed}
    listed += [
        {"object": "folder", "id": i, "name": n}
        for i, n in own.items()
        if n is not None and i not in known
    ]
    return listed


def _wrote_folder(path: WriteContext, write: Write, folder_id: str, name: str | None) -> None:
    """Remember a folder this batch renamed, created or deleted (None), in the batch and in
    the cache, so a later operation of the batch sees it as written."""
    write.batch.folders[folder_id] = name
    folders = [f for f in path.cache.folders if f.get("id") != folder_id]
    if name is not None:
        folders.append({"object": "folder", "id": folder_id, "name": name})
    path.cache.folders = folders


async def _contents_of_folders(
    path: WriteContext,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The active items and the items in the trash as bw serve lists them now."""
    return await list_items_settled(path.client), await path.client.list_trash()


def _check_folder_unchanged(
    path: WriteContext, op: Operation, folders: list[dict[str, Any]]
) -> None:
    conflict = folder_conflict(folders, op)
    if conflict is not None:
        raise ChangedDuringBatch(conflict)


async def write_rename_folder(path: WriteContext, write: Write, op: RenameFolder) -> None:
    """Rename the folder, checked again on bw serve's folders and items: the name it showed
    still the folder's, the new one free, no secret of an item in it equal to the new one."""
    folders = await _folders_now(path, write)
    _check_folder_unchanged(path, op, folders)
    check_folder_name(folders, op.folderId, op.newName)
    await _check_name_secrets(path, op.folderId, op.newName)
    await path.client.edit_folder(op.folderId, op.newName)
    _wrote_folder(path, write, op.folderId, op.newName)


async def _check_name_secrets(path: WriteContext, folder_id: str, name: str) -> None:
    items, trashed = await _contents_of_folders(path)
    originals = path.cache.items_by_id()
    lookups = path.cache.lookups()
    check_folder_name_secrets([*items, *trashed], folder_id, name, lookups, originals)


async def write_delete_folder(path: WriteContext, write: Write, op: DeleteFolder) -> None:
    """Delete the folder when bw serve lists no item in it, in the trash neither."""
    folders = await _folders_now(path, write)
    _check_folder_unchanged(path, op, folders)
    items, trashed = await _contents_of_folders(path)
    check_folder_empty(items, trashed, op.folderId)
    await path.client.delete_folder(op.folderId)
    _wrote_folder(path, write, op.folderId, None)


async def write_restore_folder(path: WriteContext, write: Write, op: RestoreFolder) -> None:
    """Follow the plan of plan_folder_restore on bw serve's folders: rename the folder back
    or create it again, the new folder's id going into the change log row."""
    contents = path.backups.read(op.backup)
    step = plan_folder_restore(op, contents, await _folders_now(path, write))
    if step.action == RENAME_BACK:
        await _check_name_secrets(path, op.folderId, step.name)
        await path.client.edit_folder(op.folderId, step.name)
        _wrote_folder(path, write, op.folderId, step.name)
    elif step.action == CREATE_AGAIN:
        created = await path.client.create_folder(step.name)
        write.folder_id = created["id"]
        _wrote_folder(path, write, created["id"], step.name)


async def folder_id(path: WriteContext, name: str) -> str | None:
    """The id of the folder with this name, created when there is none, None for no name."""
    if not name:
        return None
    folder = by_name(path.cache.folders, name)
    if folder is None:
        folder = await path.client.create_folder(name)
        path.cache.folders.append(folder)
    return folder["id"]
