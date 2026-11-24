"""The Vault and the other helpers the integration tests share."""

import base64
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from bwtidy.protocol import DB_FILE
from support.app import App
from support.cli import BwCli

Undo = Callable[[], Awaitable[None]]


def same(a: Any, b: Any) -> bool:
    """Equality without assert ==, so a mismatch never prints the compared objects."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def same_secret(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


def attachment_ids(item: dict[str, Any]) -> list[tuple[str, str, str]]:
    """id, file name and size: the url carries a token that differs between two GETs."""
    return [(a["id"], a["fileName"], a["size"]) for a in item.get("attachments") or []]


class Vault:
    """The logged in app plus the CLI with the app's session key, and the undo list."""

    def __init__(self, app: App, bw_cli: BwCli) -> None:
        self.app = app
        self.bw_cli = bw_cli
        self.undo: list[Undo] = []

    @property
    def client(self):
        return self.app.session.client

    def call(self, function: Callable[..., Awaitable[Any]], *args: Any) -> Any:
        assert self.app.client.portal is not None
        return self.app.client.portal.call(function, *args)

    def beat(self) -> None:
        """A tab's heartbeat: without one the session ends after 120 seconds, backups are slow."""
        assert self.app.client.post("/api/session/heartbeat").status_code == 204

    def items(self) -> list[dict[str, Any]]:
        self.beat()
        return self.app.session.cache.items

    def by_name(self, name: str) -> dict[str, Any]:
        [item] = [i for i in self.items() if i["name"] == name]
        return item

    def raw(self, item_id: str) -> dict[str, Any]:
        self.beat()
        return self.call(self.client.get_item, item_id)

    ## bw serve's GET can answer the old item for a moment right after a PUT
    ## (see WritePath._past_own_write), so a test that asserts the new value waits for it
    def raw_until(
        self, item_id: str, shown: Callable[[dict[str, Any]], bool], wait: float = 5
    ) -> dict[str, Any]:
        """The item as bw serve holds it once shown(item) is true. After wait seconds the last
        answer is returned as it is, so the test's own assertion fails with the real answer."""
        item = self.raw(item_id)
        deadline = time.monotonic() + wait
        while not shown(item) and time.monotonic() < deadline:
            time.sleep(0.2)
            item = self.raw(item_id)
        return item

    def raw_after(self, item_id: str, before: str | None) -> dict[str, Any]:
        """The item once its revisionDate is not before, the one the test saw before the write."""
        return self.raw_until(item_id, lambda item: item["revisionDate"] != before)

    def cli(self, *args: str) -> Any:
        key = self.app.session.session_key
        assert key
        return self.bw_cli.json(*args, session=key)

    def cli_run(self, *args: str) -> None:
        key = self.app.session.session_key
        assert key
        self.bw_cli.run(*args, session=key)

    def post(self, *operations: dict[str, Any]):
        self.beat()
        response = self.app.client.post("/api/operations", json={"operations": list(operations)})
        ## the write started a backup of the vault as it is now, the counts below wait for it
        self.app.settle()
        self.beat()
        return response

    def backups(self) -> list[str]:
        response = self.app.client.get("/api/backups")
        assert response.status_code == 200
        return [b["name"] for b in response.json()]

    def change_log(self) -> list[dict[str, Any]]:
        return self.app.client.get("/api/change-log").json()

    def op(self, kind: str, item: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {"kind": kind, "itemId": item["id"], "revisionDate": item["revisionDate"], **extra}

    def set_leaf(self, item: dict[str, Any], column: str, value: Any) -> dict[str, Any]:
        return self.op("setLeaf", item, column=column, value=value)

    def undo_put(self, original: dict[str, Any]) -> None:
        """Undo by a PUT of the original item, with the revisionDate the server has now.

        bw sends its own copy's revisionDate as lastKnownRevisionDate (not the body's), and
        Vaultwarden refuses a stale one, so the module syncs before it undoes.
        """

        async def restore() -> None:
            current = await self.client.get_item(original["id"])
            body = {**original, "revisionDate": current["revisionDate"]}
            await self.client.put_item(original["id"], body)

        self.undo.append(restore)

    def undo_convert(self, old_id: str, created: list[str]) -> None:
        """Undo a convert: delete the new item for good, bring the old one back from the trash."""

        async def restore() -> None:
            for new_id in created:
                self.cli_run("delete", "item", new_id, "--permanent")
            self.cli_run("restore", "item", old_id)

        self.undo.append(restore)


def edit_by_cli(vault: Vault, note_id: str, **changes: Any) -> None:
    """Change the tool note with bw directly, as another Bitwarden client would."""
    vault.cli_run("sync")
    item = vault.cli("get", "item", note_id)
    item.update(changes)
    encoded = base64.b64encode(json.dumps(item).encode()).decode()
    vault.cli_run("edit", "item", note_id, encoded)


def raw_sqlite(vault: Vault) -> bytes:
    return b"".join(p.read_bytes() for p in vault.app.data_dir.glob(f"{DB_FILE}*"))
