"""The plan of a restoreItems: what the write path does per item of a change log row, read from
the decrypted backup and the vault as the cache holds it (docs/spec.md, Backups).

plan_restore says per item: put the backup's item back over the current one (an archived one
stays archived), take it out of the trash first, create it anew when the trash was emptied,
move it back into or out of its organization, move an item the row's operation created to the
trash, or leave it. The write path follows the plan (writes/restore.py), the queue shows it as
the diff and GET /api/restore-plan shows it before anything is queued. restored_by marks the
change log rows a later restore undid. Nothing here puts a value into an error message.
"""

import copy
from dataclasses import dataclass
from typing import Any, Literal

from bwtidy.backups import BackupContents
from bwtidy.operations import RestoreItems
from bwtidy.protocol import backup_time
from bwtidy.vault import VaultCache

Action = Literal["put", "untrash", "create", "recreate", "toOrganization", "trash", "leave"]

PUT: Action = "put"
UNTRASH: Action = "untrash"
CREATE: Action = "create"
RECREATE: Action = "recreate"
TO_ORGANIZATION: Action = "toOrganization"
TRASH: Action = "trash"
LEAVE: Action = "leave"

TEXTS: dict[Action, str] = {
    PUT: "put back as before",
    UNTRASH: "take out of the trash and put back as before",
    CREATE: "create anew (passkeys and password history are lost)",
    RECREATE: (
        "create anew in its old place and trash the current one "
        "(passkeys and password history are lost)"
    ),
    TO_ORGANIZATION: "move back into the organization and put back as before",
    TRASH: "move to the trash",
    LEAVE: "leave as it is",
}

TOOL_NOTE_RESTORE = (
    "the bwtidy note is not restored this way, its templates are restored by saving them "
    "on the Types page"
)

## keys a copy of a backup item does not take along: the server assigns them, the attachments
## are uploaded again, and passkeys and password history cannot be copied
NOT_COPIED = ("id", "revisionDate", "attachments", "passwordHistory")


## what the plan says of an item that is archived now, which the PUT and the untrash leave
## archived
STAYS_ARCHIVED = " (it stays archived)"


@dataclass(frozen=True)
class RestoreStep:
    item_id: str
    action: Action
    ## the item's name in the vault now, else in the backup, for the plan and the diff
    name: str | None
    archived: bool = False

    @property
    def text(self) -> str:
        return TEXTS[self.action] + (STAYS_ARCHIVED if self.archived else "")


def plan_copy(item: dict[str, Any]) -> dict[str, Any]:
    """The object to POST for a new item with the values of item, in the same place.

    A listing leaves out null keys, so the organization and collections are set explicitly.
    """
    new = {key: copy.deepcopy(value) for key, value in item.items() if key not in NOT_COPIED}
    new["organizationId"] = item.get("organizationId") or None
    new["collectionIds"] = list(item.get("collectionIds") or [])
    if isinstance(new.get("login"), dict):
        new["login"].pop("fido2Credentials", None)
    return new


def placed_collections(
    saved: dict[str, Any],
    now: dict[str, Any] | None,
    contents: BackupContents,
    collections: list[dict[str, Any]],
) -> list[str]:
    """The collections an organization item of the backup goes back into.

    A saved id its organization still has stays, one it no longer has goes by its name in the
    backup to the first collection of that name there, else it is left out. With none left the
    item keeps the ones it has now (now is the item, None when it has no place yet).
    """
    organization_id = saved.get("organizationId")
    here = [c for c in collections if c.get("id") and c.get("organizationId") == organization_id]
    known = {c["id"] for c in here}
    placed: list[str] = []
    for saved_id in saved.get("collectionIds") or []:
        if saved_id in known:
            placed.append(saved_id)
            continue
        name = contents.collection_names.get(saved_id)
        placed += [c["id"] for c in here if name and c.get("name") == name][:1]
    return list(dict.fromkeys(placed)) or list((now or {}).get("collectionIds") or [])


def _same_organization(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (a.get("organizationId") or None) == (b.get("organizationId") or None)


def plan_restore(
    op: RestoreItems, contents: BackupContents, cache: VaultCache
) -> list[RestoreStep]:
    """What the restore does per item id, against the backup and the vault as the cache holds it.

    An archived item counts as active and stays archived, one in the trash as in the trash
    (docs/spec.md, Restoring a change log row). Raises ValueError for everything refused before
    anything is written: no item ids, an item active now that was not when the restore was
    planned (its revisionDate was never checked), an archived item whose organization changed
    since the backup, and an item to create anew whose attachments the backup lacks or does
    not hold by item id.
    """
    if not op.itemIds:
        raise ValueError("itemIds is empty")
    if len(set(op.itemIds)) < len(op.itemIds):
        raise ValueError("itemIds names an item twice")
    archived = cache.archived_by_id()
    active = {**archived, **cache.items_by_id()}
    archived_ids = set(archived) | {i["id"] for i in cache.archived_trash if i.get("id")}
    steps = []
    for item_id in op.itemIds:
        now = active.get(item_id)
        if now is not None and item_id not in op.revisionDates:
            raise ValueError(
                f"item {item_id} is active now but was not when the restore was planned, "
                "plan it again"
            )
        saved = contents.items.get(item_id)
        name = (now or saved or contents.trash.get(item_id) or {}).get("name")
        if saved is not None:
            if now is None:
                action = UNTRASH if item_id in cache.trash_ids else CREATE
                if action == CREATE:
                    _check_attachments(saved, contents)
            elif _same_organization(saved, now):
                action = PUT
            elif item_id in archived:
                raise ValueError(
                    f"item {item_id} is archived and its organization changed since the "
                    "backup, take it out of the archive first"
                )
            elif not now.get("organizationId"):
                action = TO_ORGANIZATION
            else:
                action = RECREATE
        else:
            action = TRASH if now is not None else LEAVE
        stays_archived = item_id in archived_ids and action in (PUT, UNTRASH)
        steps.append(RestoreStep(item_id, action, name, stays_archived))
    return steps


def restored_by(
    entries: list[dict[str, Any]], restores: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The change log entries, each with restoredBy: the first later restore that undid it.

    A restore puts whole items back as its backup holds them, so it undoes a change X of those
    items when its backup is X's own backup or an older one (docs/spec.md, Restoring a change
    log row). X is a written row that is not itself a restore, restores are db.restores().
    restoredBy is {id, at, itemIds} with the items of X that the restore put back, else None.
    """
    for entry in entries:
        entry["restoredBy"] = None
        before = backup_time(entry["backup"] or "")
        if entry["result"] != "ok" or entry["operationKind"] == "restoreItems" or before is None:
            continue
        touched = list(dict.fromkeys([*entry["itemIds"], *entry["createdItemIds"]]))
        for restore in restores:
            ## a backup taken in the same second as X's row may already hold X, so the restored
            ## backup is compared with X's own backup and not with X's time
            restored = backup_time(restore["restoredBackup"])
            if restore["id"] <= entry["id"] or restored is None or restored > before:
                continue
            item_ids = [i for i in touched if i in restore["itemIds"]]
            if item_ids:
                entry["restoredBy"] = {
                    "id": restore["id"],
                    "at": restore["at"],
                    "itemIds": item_ids,
                }
                break
    return entries


def _check_attachments(saved: dict[str, Any], contents: BackupContents) -> None:
    for attachment in saved.get("attachments") or []:
        if contents.attachment_member(saved, attachment) is None:
            raise ValueError(
                f"item {saved['id']} cannot be created anew: the backup lacks one of its "
                "attachments"
            )
