"""The Vault plus the restore round trip and its checks."""

from typing import Any

from bwtidy.backups.crypto import decrypt_backup
from bwtidy.backups.reader import BackupContents
from bwtidy.operations import SetLeaf
from support.cli import PASSWORD
from support.vault import Vault, same

## leaves a restore cannot bring back for an item it keeps the id of
NOT_RESTORED = {"revisionDate", "passwordHistory"}
## nor for an item it creates anew (passwordRevisionDate is no leaf, the spec says)
NOT_RECREATED = NOT_RESTORED | {"id", "creationDate", "attachments", "object"}


def differing(a: Any, b: Any, path: str = "") -> list[str]:
    """The paths where two JSON values differ, never the values themselves."""
    if isinstance(a, dict) and isinstance(b, dict):
        found = []
        for key in sorted(a.keys() | b.keys()):
            found += differing(a.get(key), b.get(key), f"{path}.{key}" if path else key)
        return found
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path} (length)"]
        found = []
        for index, (x, y) in enumerate(zip(a, b, strict=True)):
            found += differing(x, y, f"{path}[{index}]")
        return found
    return [] if same(a, b) else [path]


def without(item: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    stripped = {k: v for k, v in item.items() if k not in keys and v is not None}
    if "attachments" in stripped:
        stripped["attachments"] = [
            {"id": a["id"], "fileName": a["fileName"], "size": a["size"]}
            for a in stripped["attachments"]
        ]
    if isinstance(stripped.get("login"), dict):
        stripped["login"] = {
            k: v
            for k, v in stripped["login"].items()
            if v is not None and not (keys is NOT_RECREATED and k == "passwordRevisionDate")
        }
    return stripped


class Restorer(Vault):
    """The Vault of test_writes plus the restore round trip and its checks."""

    def row(self) -> dict[str, Any]:
        return self.change_log()[0]

    def backup_item(self, name: str, item_id: str) -> dict[str, Any]:
        contents = BackupContents(name, self.decrypted(name))
        return contents.items[item_id]

    def decrypted(self, name: str) -> bytes:
        return decrypt_backup(self.app.data_dir / "backups" / name, PASSWORD)

    def attachment_bytes(self, item: dict[str, Any]) -> dict[str, bytes]:
        return {
            a["fileName"]: self.call(self.client.get_attachment, a["id"], item["id"])
            for a in item.get("attachments") or []
        }

    def apply(self, *operations: dict[str, Any]) -> dict[str, Any]:
        response = self.post(*operations)
        assert response.status_code == 200, response.text
        return self.row()

    def restore(self, row: dict[str, Any], actions: list[str]) -> dict[str, Any]:
        """Plan and apply the restore of a change log row, checking the plan's actions.

        Returns the restore's own change log row.
        """
        plan = self.app.client.get("/api/restore-plan", params={"changeLogId": row["id"]})
        assert plan.status_code == 200, plan.text
        body = plan.json()
        assert [i["action"] for i in body["items"]] == actions
        assert body["backup"] == row["backup"]
        assert body["operation"]["itemIds"] == [*row["itemIds"], *row["createdItemIds"]]
        assert body["summary"].startswith("Restore ")
        response = self.post(body["operation"])
        assert response.status_code == 200, response.text
        restored = self.row()
        assert restored["operationKind"] == "restoreItems"
        assert restored["itemIds"] == body["operation"]["itemIds"]
        assert restored["result"] == "ok"
        ## the restore has a backup of the vault as it was right before it
        assert restored["backup"] != row["backup"]
        assert (self.app.data_dir / "backups" / restored["backup"]).is_file()
        return restored

    def check_same_id(
        self, kind: str, row: dict[str, Any], item_id: str, attachments: dict[str, bytes]
    ) -> dict[str, Any]:
        """The item now equals the backup's version, the attachments too."""
        saved = self.backup_item(row["backup"], item_id)
        ## bw serve may still answer the item as it was before the restore's PUT, so wait
        ## until it equals the backup's version
        current = self.raw_until(
            item_id,
            lambda item: not differing(without(saved, NOT_RESTORED), without(item, NOT_RESTORED)),
        )
        diff = differing(without(saved, NOT_RESTORED), without(current, NOT_RESTORED))
        assert diff == [], f"{kind}: the restored item differs from its backup in {diff}"
        assert self.attachment_bytes(current) == attachments, f"{kind}: attachments differ"
        assert item_id in {i["id"] for i in self.items()}
        return current

    def check_new_id(
        self,
        kind: str,
        row: dict[str, Any],
        item_id: str,
        new_id: str,
        attachments: dict[str, bytes],
    ) -> dict[str, Any]:
        """An item created anew equals the backup's version except what a new item cannot
        keep, with the same attachment names and bytes under new attachment ids."""
        saved = self.backup_item(row["backup"], item_id)
        current = self.raw(new_id)
        diff = differing(without(saved, NOT_RECREATED), without(current, NOT_RECREATED))
        assert diff == [], f"{kind}: the recreated item differs from its backup in {diff}"
        assert sorted(a["fileName"] for a in current.get("attachments") or []) == sorted(
            a["fileName"] for a in saved.get("attachments") or []
        )
        assert self.attachment_bytes(current) == attachments, f"{kind}: attachments differ"
        return current

    def in_trash(self, item_id: str) -> bool:
        return item_id in {i["id"] for i in self.cli("list", "items", "--trash")}

    def undo_delete_for_good(self, item_ids: list[str]) -> None:
        async def delete() -> None:
            for item_id in item_ids:
                self.cli_run("delete", "item", item_id, "--permanent")

        self.undo.append(delete)

    def set_templates(self, notes: str) -> None:
        """Write the tool note's notes the way the templates save does, through the write path."""
        note = self.by_name("bwtidy")
        op = SetLeaf(
            kind="setLeaf",
            itemId=note["id"],
            revisionDate=note["revisionDate"],
            column="notes",
            value=notes,
        )

        async def write() -> None:
            await self.app.writes.apply([op], "ui", edits_tool_note=True)

        self.beat()
        self.call(write)
        self.app.settle()
