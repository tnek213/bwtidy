"""The in-process app against the dev stack, the items made elsewhere and the tool note helpers."""

import base64
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bwtidy.backups.keeper import BackupKeeper
from bwtidy.session import SessionManager
from bwtidy.vault.cache import list_until
from bwtidy.vault.tool_note import TOOL_NOTE_NAME
from bwtidy.writes.path import WritePath
from support.cli import PASSWORD, BwCli


class App:
    """The app running in-process against the dev stack, with its login session."""

    def __init__(
        self,
        client: TestClient,
        session: SessionManager,
        writes: WritePath,
        backups: BackupKeeper,
        data_dir: Path,
    ) -> None:
        self.client = client
        self.session = session
        self.writes = writes
        self.backups = backups
        self.data_dir = data_dir

    def login(self, password: str = PASSWORD):
        return self.client.post("/api/session", json={"password": password})

    def settle(self) -> None:
        """Wait for the background backup a login or a write started (seconds, with bw)."""
        assert self.client.portal is not None
        self.client.portal.call(self.backups.wait_for_background_backup)

    def state(self) -> str:
        return self.client.get("/api/session").json()["state"]


def tool_notes(bw_cli: BwCli, session: str) -> list[dict[str, Any]]:
    bw_cli.run("sync", session=session)
    items = bw_cli.json("list", "items", "--search", TOOL_NOTE_NAME, session=session)
    return [
        i
        for i in items
        if i["type"] == 2 and i["name"] == TOOL_NOTE_NAME and i.get("organizationId") is None
    ]


def purge_tool_notes(bw_cli: BwCli) -> None:
    key = bw_cli.unlock()
    try:
        for note in tool_notes(bw_cli, key):
            bw_cli.run("delete", "item", note["id"], "--permanent", session=key)
    finally:
        bw_cli.run("lock")


class Elsewhere:
    """bw as another device with the app's own session key: items made for one test."""

    def __init__(self, app: App, bw_cli: BwCli, tmp_path: Path) -> None:
        self.app = app
        self.bw_cli = bw_cli
        self.tmp_path = tmp_path
        self.key = app.session.session_key
        self.made: list[str] = []
        self.folders: list[str] = []
        ## (organizationId, collection id) of each collection made
        self.collections: list[tuple[str, str]] = []
        assert self.key

    def beat(self) -> None:
        assert self.app.client.post("/api/session/heartbeat").status_code == 204

    def create(self, kind: str, body: dict[str, Any], *options: str) -> dict[str, Any]:
        encoded = base64.b64encode(json.dumps(body).encode()).decode()
        return self.bw_cli.json("create", kind, encoded, *options, session=self.key)

    def item(self, **item: Any) -> str:
        """An item as given, its id."""
        item_id = self.create("item", item)["id"]
        self.made.append(item_id)
        return item_id

    def folder(self, name: str) -> str:
        folder_id = self.create("folder", {"name": name})["id"]
        self.folders.append(folder_id)
        return folder_id

    def collection(self, organization_id: str, name: str) -> str:
        body = {"organizationId": organization_id, "name": name, "groups": [], "users": []}
        made = self.create("org-collection", body, "--organizationid", organization_id)
        self.collections.append((organization_id, made["id"]))
        return made["id"]

    def note_with_attachment(
        self,
        name: str,
        file_name: str,
        content: bytes,
        notes: str = "made by a test",
        **place: Any,
    ) -> str:
        """A secure note with one attachment, place (organizationId, collectionIds) as given."""
        item_id = self.item(type=2, name=name, notes=notes, secureNote={"type": 0}, **place)
        path = self.tmp_path / item_id / file_name
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        self.bw_cli.run(
            "create", "attachment", "--file", str(path), "--itemid", item_id, session=self.key
        )
        return item_id

    def trash(self, item_id: str) -> None:
        self.bw_cli.run("delete", "item", item_id, session=self.key)

    def backup(self) -> str:
        """A backup by hand, its file name."""
        self.beat()
        response = self.app.client.post("/api/backups")
        assert response.status_code == 201, response.text
        return response.json()["name"]

    def clean_up(self) -> None:
        """Delete the made items, folders and collections for good and let the app see the
        vault as it was."""
        self.beat()
        for item_id in self.made:
            self.bw_cli.run("delete", "item", item_id, "--permanent", session=self.key)
        self.made.clear()
        for org_id, collection_id in self.collections:
            self.bw_cli.run(
                "delete",
                "org-collection",
                collection_id,
                "--organizationid",
                org_id,
                session=self.key,
            )
        self.collections.clear()
        for folder_id in self.folders:
            self.bw_cli.run("delete", "folder", folder_id, session=self.key)
        self.folders.clear()
        assert self.app.client.portal is not None
        self.app.client.portal.call(self.app.session.client.sync)
        self.app.client.portal.call(self.app.session.cache.load)


def wait_until_listed(app: App, item_id: str) -> None:
    """Sync and wait until bw serve lists an item made or changed elsewhere.

    bw serve knows such an item only after a sync, and right after a sync that fetched
    something it answers "Not found" for every object while it decrypts the items again
    (see WritePath._create). A read of the item before it is listed again fails.
    """
    assert app.client.portal is not None
    app.client.portal.call(app.session.client.sync)

    def listed(items: list[dict[str, Any]]) -> bool:
        return any(i.get("id") == item_id for i in items)

    app.client.portal.call(list_until, app.session.client.list_items, listed)
