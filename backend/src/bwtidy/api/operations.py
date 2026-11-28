"""Write routes: POST /api/operations applies a batch through the write path, GET the log.

GET /api/change-log answers the newest rows that match its filters, and says in a header
whether more matched (docs/spec.md, Writes).

GET /api/restore-plan says what restoring a change log row would do, item by item, and gives
the restoreItems operation to queue for it (docs/spec.md, Backups). A row of a folder kind gets
a restoreFolder and the plan of its folder instead, a row of an attachment rename the
renameAttachment that gives the attachment back the name its backup holds.
"""

import asyncio
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from bwtidy.api.app_state import AppState, app_state
from bwtidy.api.deps import Session
from bwtidy.db import ChangeLogFilter
from bwtidy.operations import (
    InvalidOperation,
    RenameAttachment,
    RestoreFolder,
    RestoreItems,
    attachment_of,
    parse_operations,
)
from bwtidy.protocol import MORE_HEADER
from bwtidy.queue.view import summarize
from bwtidy.writes import plan_folder_restore, plan_restore, restored_by, validate

router = APIRouter(prefix="/api")

CHANGE_LOG_LIMIT = 200


@router.post("/operations")
async def apply_operations(
    request: Request, body: dict[str, Any], session: Session
) -> dict[str, Any]:
    ops = parse_operations(body.get("operations"))
    result = await app_state(request).writes.apply(ops, "ui")
    return {"applied": result.applied, "backup": result.backup}


@router.get("/change-log")
async def change_log(
    request: Request,
    response: Response,
    session: Session,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
    source: Annotated[list[Literal["ui", "mcp", "mixed"]] | None, Query()] = None,
    kind: Annotated[list[str] | None, Query()] = None,
    column: Annotated[list[str] | None, Query()] = None,
    item: Annotated[list[str] | None, Query()] = None,
    result: Literal["ok", "failed"] | None = None,
) -> list[dict[str, Any]]:
    """The newest change log rows that match every filter given, at most CHANGE_LOG_LIMIT.

    from and to are inclusive. A repeated source or kind matches any of them, a column matches
    a row that wrote it, an item a row whose items or created items hold it.
    """
    rows_filter = ChangeLogFilter(
        since=since,
        until=until,
        sources=tuple(source or ()),
        kinds=tuple(kind or ()),
        columns=tuple(column or ()),
        item_ids=tuple(item or ()),
        result=result,
    )
    db = app_state(request).db
    entries, more = db.matching(rows_filter, CHANGE_LOG_LIMIT)
    response.headers[MORE_HEADER] = "true" if more else "false"
    return restored_by(entries, db.restores())


def restore_operation(entry: dict[str, Any], state: AppState) -> RestoreItems:
    """The restoreItems for a change log row: its items and the ones it created, with the
    revisionDate of every one that is active now (an archived one counts as active)."""
    item_ids = list(dict.fromkeys([*entry["itemIds"], *entry["createdItemIds"]]))
    cache = state.cache
    active: dict[str, str] = {
        item_id: i["revisionDate"]
        for item_id, i in {**cache.archived_by_id(), **cache.items_by_id()}.items()
        if isinstance(i.get("revisionDate"), str)
    }
    revision_dates = {i: active[i] for i in item_ids if i in active}
    return RestoreItems(
        kind="restoreItems",
        backup=entry["backup"],
        itemIds=item_ids,
        revisionDates=revision_dates,
        changeLogId=entry["id"],
    )


@router.get("/restore-plan")
async def restore_plan(request: Request, session: Session, changeLogId: int) -> dict[str, Any]:
    """What restoring the change log row would do per item, and the operation to queue.

    404 for an unknown row, 422 when the restore would be refused (the tool note, a backup
    that is missing or does not decrypt, no items), the same checks POST /api/queue makes.
    """
    state = app_state(request)
    entry = state.db.entry(changeLogId)
    if entry is None:
        detail = f"no change log entry with the id {changeLogId}"
        raise HTTPException(status_code=404, detail=detail)
    if entry.get("folderId"):
        return await _folder_restore_plan(entry, state)
    if entry["operationKind"] == "renameAttachment":
        return await _attachment_restore_plan(entry, state)
    op = restore_operation(entry, state)
    cache = state.cache
    try:
        ## the decrypt costs a second of scrypt, so it runs off the event loop
        contents = await asyncio.to_thread(state.backups.reader.read, op.backup)
        validate([op], cache, state.writes.remembered, backups=state.backups.reader)
        steps = plan_restore(op, contents, cache)
    except (InvalidOperation, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return {
        "changeLogId": changeLogId,
        "backup": op.backup,
        "summary": summarize(op, cache),
        "operation": op.model_dump(mode="json"),
        "items": [
            {
                "itemId": step.item_id,
                "itemName": step.name,
                "action": step.action,
                "text": step.text,
            }
            for step in steps
        ],
    }


async def _folder_restore_plan(entry: dict[str, Any], state: AppState) -> dict[str, Any]:
    """The plan of a folder row: its restoreFolder, and what it does with the folder."""
    op = RestoreFolder(
        kind="restoreFolder",
        backup=entry["backup"],
        folderId=entry["folderId"],
        changeLogId=entry["id"],
    )
    cache = state.cache
    try:
        contents = await asyncio.to_thread(state.backups.reader.read, op.backup)
        validate([op], cache, state.writes.remembered, backups=state.backups.reader)
        step = plan_folder_restore(op, contents, cache.folders)
    except (InvalidOperation, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return {
        "changeLogId": entry["id"],
        "backup": op.backup,
        "summary": summarize(op, cache),
        "operation": op.model_dump(mode="json"),
        "items": [],
        "folder": {
            "folderId": op.folderId,
            "name": step.now if step.now is not None else step.name,
            "action": step.action,
            "text": step.text,
        },
    }


## what the restore of an attachment rename does with its item
RENAME_BACK_ATTACHMENT = "renameAttachment"


async def _attachment_restore_plan(entry: dict[str, Any], state: AppState) -> dict[str, Any]:
    """The plan of an attachment rename's row: a renameAttachment of the copy the rename made
    back to the name the row's backup holds for the attachment it replaced.

    A restoreItems would PUT the whole item back, which leaves its attachments as they are and
    undoes every later change of the item instead. 422 when the copy is gone, the backup lacks
    the old attachment or the rename back is refused like any rename.
    """
    cache = state.cache
    ids = entry.get("attachmentIds") or {}
    try:
        if not entry["itemIds"] or not ids.get("before") or not ids.get("after"):
            raise ValueError("the change log row names no attachment the rename made")
        item_id = entry["itemIds"][0]
        item = cache.items_by_id().get(item_id)
        if item is None:
            raise ValueError(f"item {item_id} is not in the vault")
        copy = attachment_of(item, ids["after"])
        if copy is None:
            raise ValueError(f"the attachment {ids['after']} the rename made is gone")
        contents = await asyncio.to_thread(state.backups.reader.read, entry["backup"])
        old = attachment_of(contents.items.get(item_id) or {}, ids["before"])
        if old is None:
            raise ValueError(f"the backup holds no attachment {ids['before']} of the item")
        op = RenameAttachment(
            kind="renameAttachment",
            itemId=item_id,
            revisionDate=item["revisionDate"],
            attachmentId=ids["after"],
            fileName=old.get("fileName") or "",
            shownFileName=copy.get("fileName") or "",
        )
        validate([op], cache, state.writes.remembered, backups=state.backups.reader)
    except (InvalidOperation, ValueError) as error:
        reason = error.reason if isinstance(error, InvalidOperation) else str(error)
        raise HTTPException(status_code=422, detail=reason) from None
    text = f'rename the attachment "{op.shownFileName}" back to "{op.fileName}"'
    return {
        "changeLogId": entry["id"],
        "backup": entry["backup"],
        "summary": summarize(op, cache),
        "operation": op.model_dump(mode="json"),
        "items": [
            {
                "itemId": item_id,
                "itemName": item.get("name") or "",
                "action": RENAME_BACK_ATTACHMENT,
                "text": text,
            }
        ],
    }
