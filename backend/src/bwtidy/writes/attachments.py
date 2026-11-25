"""renameAttachment: upload a copy of the attachment under the new name, then delete the old
one (docs/spec.md, Writes).

bw serve cannot rename an attachment in place, so a rename is an upload and then a delete,
with the checks and the clean-up that leave the item exactly one copy whatever fails. The
bytes stay in RAM for the rename only.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bwtidy.bw import BwError
from bwtidy.operations import RenameAttachment, attachment_of
from bwtidy.writes.check import ChangedDuringBatch
from bwtidy.writes.planners import attachment_conflict, check_attachment_rename

if TYPE_CHECKING:
    from bwtidy.writes.path import Write, WriteContext

log = logging.getLogger(__name__)


async def write_rename_attachment(path: WriteContext, write: Write, op: RenameAttachment) -> None:
    """Give the attachment its new name: download it, upload it again under the new name,
    check that the copy is listed with the same size, then delete the old one.

    bw serve cannot rename an attachment in place: a PUT of the item with another fileName
    in its attachments answers success and leaves the name as it was (docs/spec.md,
    Writes). When the upload or anything after it
    fails, the copy is deleted again, so the item keeps exactly one: always before the delete
    of the old one started, after that only while the item still lists the old one. The
    bytes stay in this function's RAM only.
    """
    client = path.client
    full = await path.get_checked(write, op.itemId)
    conflict = attachment_conflict(full, op)
    if conflict is not None:
        raise ChangedDuringBatch(conflict)
    old = check_attachment_rename(full, op)
    before = {a.get("id") for a in full.get("attachments") or [] if isinstance(a, dict)}
    data = await client.get_attachment(op.attachmentId, op.itemId)
    try:
        answer = await client.upload_attachment(op.itemId, op.fileName, data)
    except BaseException:
        ## the server may have stored the copy although bw serve answered an error
        await _drop_lost_copy(path, op, before)
        raise
    finally:
        del data
    new_id: str | None = None
    deleting = False
    try:
        new = await _uploaded_copy(path, op, answer, before)
        new_id = new["id"]
        if str(new.get("size")) != str(old.get("size")):
            raise ValueError(
                f"the new copy of attachment {op.attachmentId} has another size than the old one"
            )
        path.record(op.itemId, full.get("revisionDate"), answer)
        deleting = True
        await client.delete_attachment(op.attachmentId, op.itemId)
    except BaseException:
        await _drop_copy(path, op, new_id, checked=deleting)
        raise
    write.attachment_ids = {"before": op.attachmentId, "after": new_id}
    await _record_attachment_delete(path, op.itemId, answer)


async def _uploaded_copy(
    path: WriteContext, op: RenameAttachment, answer: Any, before: set[Any]
) -> dict[str, Any]:
    """The attachment the upload added: the one with the new name that was not on the item
    before, from bw serve's answer to the upload, else from a GET of the item. ValueError
    when there is not exactly one, then nobody knows which to delete again."""
    listed = answer.get("attachments") if isinstance(answer, dict) else None
    if not isinstance(listed, list):
        item = await path.item_now(op.itemId)
        listed = (item or {}).get("attachments") or []
    added = [
        a
        for a in listed
        if isinstance(a, dict)
        and a.get("id") not in before
        and a.get("fileName") == op.fileName
        and a.get("id")
    ]
    if len(added) != 1:
        raise ValueError(
            f"the upload of the new copy of attachment {op.attachmentId} is not listed on "
            "the item, look at the item in Bitwarden"
        )
    return added[0]


async def _drop_lost_copy(path: WriteContext, op: RenameAttachment, before: set[Any]) -> None:
    """Delete the copy a failed upload may have left, when the item lists exactly one."""
    try:
        new = await _uploaded_copy(path, op, None, before)
        await path.client.delete_attachment(new["id"], op.itemId)
    except ValueError:
        return
    except BwError as error:
        log.warning(
            "Could not check item %s for a copy of attachment %s: %s",
            op.itemId,
            op.attachmentId,
            error,
        )


async def _drop_copy(
    path: WriteContext, op: RenameAttachment, new_id: str | None, *, checked: bool
) -> None:
    """Delete the new copy of a rename that failed after its upload, so the item keeps one.

    checked says the delete of the old one was under way: then the copy goes only when the
    item still lists the old one, as a delete that failed late may have happened, and the
    copy is then the only one left. A failure here is logged, both copies stay.
    """
    if new_id is None:
        log.warning("A copy of attachment %s may be left on item %s", op.attachmentId, op.itemId)
        return
    try:
        if checked:
            item = await path.item_now(op.itemId)
            if item is None or attachment_of(item, op.attachmentId) is None:
                log.warning(
                    "Attachment %s of item %s is gone, its copy %s stays",
                    op.attachmentId,
                    op.itemId,
                    new_id,
                )
                return
        await path.client.delete_attachment(new_id, op.itemId)
    except BwError as error:
        log.warning(
            "Could not delete the copy %s of attachment %s on item %s: %s",
            new_id,
            op.attachmentId,
            op.itemId,
            error,
        )


async def _record_attachment_delete(path: WriteContext, item_id: str, answer: Any) -> None:
    """Follow the revisionDate the delete of the old attachment gave the item, if any.

    bw serve answers the delete without the item. No sync ran since, so its copy of the
    item changed only by bwtidy's own upload and delete, which makes it an own write like
    a PUT's answer. On Vaultwarden neither changes the revisionDate.
    """
    before = answer.get("revisionDate") if isinstance(answer, dict) else None
    try:
        now = await path.past_own_write(item_id, path.client.get_item)
    except BwError as error:
        log.warning("Could not read item %s after renaming its attachment: %s", item_id, error)
        return
    path.record(item_id, before, now)
