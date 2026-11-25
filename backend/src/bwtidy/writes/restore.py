"""restoreItems: the items of a change log row put back as its backup holds them, step by
step as restore_plan.plan_restore says (docs/spec.md, Backups).

Per item: PUT the backup's item over the current one (then its collections, which a PUT
leaves alone), take it out of the trash first, create it anew with its attachments from the
backup when it is gone, move it back into its organization, or create it anew in the personal
vault and trash the organization's copy. Whether an item that is not active is in the trash or
gone is asked of bw serve again right before it is brought back.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from bwtidy.backups import BackupContents, ids, list_covering
from bwtidy.operations import RestoreItems
from bwtidy.vault import list_until
from bwtidy.writes.create import convert, create
from bwtidy.writes.folders import folder_id
from bwtidy.writes.restore_plan import (
    CREATE,
    PUT,
    RECREATE,
    TO_ORGANIZATION,
    TRASH,
    UNTRASH,
    placed_collections,
    plan_copy,
    plan_restore,
)

if TYPE_CHECKING:
    from bwtidy.writes.path import Write, WriteContext


async def write_restore_items(path: WriteContext, write: Write, op: RestoreItems) -> None:
    """Follow the plan of plan_restore, item by item (see restore_plan.py).

    Whether an item that is not active is in the trash or gone is asked of bw serve again
    right before it is brought back, the plan's answer came from the listing in the cache.
    """
    contents = path.backups.read(op.backup)
    steps = plan_restore(op, contents, path.cache)
    client = path.client
    await _cover_places(path, op, contents)
    for step in steps:
        saved = contents.items.get(step.item_id)
        if step.action == PUT and saved is not None:
            await _put_back(path, write, step.item_id, saved, contents)
        elif step.action in (UNTRASH, CREATE) and saved is not None:
            await path.fresh(write)
            await _bring_back(path, write, saved, contents)
        elif step.action == TO_ORGANIZATION and saved is not None:
            placed = await _placed(path, saved, contents)
            full = await path.get_checked(write, step.item_id)
            before = full.get("revisionDate")
            move = client.move_to_organization(
                step.item_id, saved["organizationId"], placed["collectionIds"]
            )
            answer = await path.unless_stale(write, step.item_id, before, move)
            ## else the check of the PUT after the sync takes bwtidy's own move for a change
            path.record(step.item_id, before, answer)
            path.record_collections(step.item_id, full, placed["collectionIds"])
            await path.sync(write.batch)
            await _put_back(path, write, step.item_id, saved, contents)
        elif step.action == RECREATE and saved is not None:
            placed = await _placed(path, saved, contents)
            write.created.append(
                await convert(
                    path, write, step.item_id, lambda full, placed=placed: plan_copy(placed)
                )
            )
        elif step.action == TRASH:
            await path.get_checked(write, step.item_id)
            await client.trash_item(step.item_id)


async def _cover_places(path: WriteContext, op: RestoreItems, contents: BackupContents) -> None:
    """Ask bw serve again when a backup item names a folder or collection the cache lacks.

    Right after a sync a listing can miss what exists (see collect.list_covering), and
    taking that at face value would make a second folder or drop a collection. One that
    is really deleted costs the retry budget once per restore.
    """
    saved = [contents.items[i] for i in op.itemIds if i in contents.items]
    folders = {s["folderId"] for s in saved if s.get("folderId")}
    if folders - ids(path.cache.folders):
        path.cache.folders, _ = await list_covering(path.client.list_folders, folders)
    collections = {
        c for s in saved if s.get("organizationId") for c in s.get("collectionIds") or []
    }
    if collections - ids(path.cache.collections):
        path.cache.collections, _ = await list_covering(path.client.list_collections, collections)


async def _placed(
    path: WriteContext,
    saved: dict[str, Any],
    contents: BackupContents,
    now: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A copy of the backup's item in the folder and collections the vault has now.

    The server refuses a folder id or collection id it no longer has. An id the vault has
    stays. Else a folder goes by its name in the backup, like moveToFolder: the first of
    that name, a new one when there is none, no folder when the backup has no name for
    it. For the collections see placed_collections. now is the item as it is, None for one
    that has no place yet (to create or to move in), which cannot go without a collection.
    """
    placed = dict(saved)
    if saved.get("organizationId"):
        placed["collectionIds"] = placed_collections(saved, now, contents, path.cache.collections)
        if now is None and not placed["collectionIds"]:
            raise ValueError(
                f"item {saved['id']} cannot go back into its organization: none of its "
                "collections exists any more"
            )
    saved_folder = saved.get("folderId")
    if saved_folder and saved_folder not in ids(path.cache.folders):
        placed["folderId"] = await folder_id(path, contents.folder_names.get(saved_folder, ""))
    return placed


async def _put_back(
    path: WriteContext,
    write: Write,
    item_id: str,
    saved: dict[str, Any],
    contents: BackupContents,
    *,
    check: bool = True,
) -> None:
    """PUT the backup's item over the current one, then its collections when they differ.

    The body carries the current revisionDate, not the backup's. bw 2026.10.0 ignores it
    and sends its own copy's as lastKnownRevisionDate, but a client that took it from the
    body would have the backup's old one refused as out of date.

    A PUT of the item leaves its collections as they are (bw edit item ignores them,
    bw edit item-collections is the way), so they get their own call. check=False skips
    check_unchanged, for an item the caller checked before it took it out of the trash
    (the untrash answers no revisionDate to follow).
    """
    if check:
        full = await path.get_checked(write, item_id)
    else:
        full = await path.get_to_write(write, item_id)
    placed = await _placed(path, saved, contents, full)
    body = {**placed, "revisionDate": full.get("revisionDate")}
    updated = await path.put(write, item_id, body)
    wanted = sorted(placed.get("collectionIds") or [])
    if placed.get("organizationId") and wanted != sorted(full.get("collectionIds") or []):
        answer = await path.client.put_item_collections(item_id, wanted)
        after = updated.get("revisionDate") if isinstance(updated, dict) else None
        path.record(item_id, after, answer)


async def _bring_back(
    path: WriteContext, write: Write, saved: dict[str, Any], contents: BackupContents
) -> None:
    """Take a backup item out of the trash and put it back, or create it anew when it is
    gone, with its attachments from the backup."""
    client = path.client
    item_id = saved["id"]
    current = await path.item_now(item_id)
    if current is not None:
        path.check_unchanged(write, item_id, current)
        untrash = bool(current.get("deletedDate"))
        if untrash:
            await client.restore_item(item_id)
            await path.sync(write.batch)
            await list_until(
                client.list_items, lambda items: any(i.get("id") == item_id for i in items)
            )
        await _put_back(path, write, item_id, saved, contents, check=not untrash)
        return
    attachments = []
    for attachment in saved.get("attachments") or []:
        member = contents.attachment_member(saved, attachment)
        if member is None:
            raise ValueError(f"the backup lacks an attachment of item {item_id}")
        attachments.append((attachment["fileName"], _from_backup(contents, member)))
    placed = await _placed(path, saved, contents)
    write.created.append(await create(path, write, plan_copy(placed), attachments))


def _from_backup(contents: BackupContents, member: str) -> Callable[[], Awaitable[bytes]]:
    async def fetch() -> bytes:
        return contents.attachment_bytes(member)

    return fetch
