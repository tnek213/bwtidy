"""The unit tests' app: a fake bw serve, the settings, create_app, a TestClient and the login.

`AppEnv(data_dir)` builds the app on the sample vault with its backups faked. The knobs that
tests differ in are its arguments, every other setting a keyword that goes to `Settings`.
`with env:` starts the app (its lifespan runs) without a login, `env.unlock()` logs in.
"""

import asyncio
import copy
import dataclasses
import io
import json
import zipfile
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Self

import httpx2 as httpx
from fastapi import Depends
from fastapi.testclient import TestClient

from bwtidy.api.deps import require_session
from bwtidy.backups.collect import BackupStopped, attachment_members
from bwtidy.backups.files import BackupError, BackupInfo, fingerprint
from bwtidy.backups.keeper import BackupKeeper
from bwtidy.bw.login import BwLogin
from bwtidy.config import Settings
from bwtidy.db import Database
from bwtidy.main import create_app
from bwtidy.mcp.server import BwtidyMcp
from bwtidy.protocol import SESSION_COOKIE, backup_name
from bwtidy.queue.review import ReviewQueue
from bwtidy.session import SessionManager
from bwtidy.vault.cache import VaultCache
from bwtidy.vault.tool_note import find_tool_note
from bwtidy.worker import WorkerLifetime
from bwtidy.writes.path import WritePath
from tests.support.fake_bw import PASSWORD, FakeBwServe, FakeClock
from tests.support.sample_vault import COLLECTIONS, FOLDERS, ITEMS, ORGANIZATIONS

NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)
## the TestClient's own host, and the one the MCP server's DNS rebinding check lets through
TEST_URL = "http://testserver"
MCP_URL = "http://127.0.0.1:8000"


def sample_bw(items: Sequence[dict[str, Any]] = ITEMS) -> FakeBwServe:
    """A fake bw serve with copies of the items and the sample vault's folders, organizations
    and collections, and the bytes of login-1's attachment."""
    bw = FakeBwServe(copy.deepcopy(list(items)))
    bw.folders = copy.deepcopy(FOLDERS)
    bw.organizations = copy.deepcopy(ORGANIZATIONS)
    bw.collections = copy.deepcopy(COLLECTIONS)
    bw.attachments[("login-1", "a1")] = b"recovery codes"
    return bw


class AppEnv:
    """The app over a data directory, with the parts tests reach into as attributes.

    bw is the fake bw serve (the sample vault unless given), extra items join its items.
    fake_backups puts fake_backup in place of the real backup (False leaves the backup that
    tests/conftest.py stubs), zips makes those fake backups real zips of the fake vault.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        bw: FakeBwServe | None = None,
        extra: Sequence[dict[str, Any]] = (),
        clock: FakeClock | None = None,
        http: httpx.AsyncClient | None = None,
        fake_backups: bool = True,
        zips: bool = False,
        base_url: str = TEST_URL,
        **settings: Any,
    ) -> None:
        self.bw = sample_bw() if bw is None else bw
        self.bw.items.extend(copy.deepcopy(list(extra)))
        self.data_dir = data_dir
        defaults: dict[str, Any] = {
            "host": "127.0.0.1",
            "port": 8000,
            "data_dir": data_dir,
            "static_dir": None,
            "bw_bin": "bw",
            "bw_serve_port": 8087,
            "backup_dir": data_dir / "backups",
        }
        self.settings = Settings(**(defaults | settings))
        self.clock = clock or FakeClock()
        ## the exits a worker asked for, in place of the signal that stops the process
        self.exits: list[bool] = []
        self.app = create_app(
            self.settings,
            start_bw_serve=False,
            http=http or self.bw.http(),
            clock=self.clock,
            request_exit=lambda: self.exits.append(True),
        )
        state = self.app.state.bw
        self.session: SessionManager = state.session
        self.cache: VaultCache = state.session.cache
        self.writes: WritePath = state.writes
        self.db: Database = state.db
        self.keeper: BackupKeeper = state.backups
        self.queue: ReviewQueue = state.queue
        self.mcp: BwtidyMcp = state.mcp
        self.worker: WorkerLifetime = state.worker
        self.bw_login: BwLogin = state.bw_login
        self.base_url = base_url
        self.backups: list[str] = []
        self.backups_active = 0
        self.backups_overlapped = False
        self.backup_error: BackupError | None = None
        ## while set, every fake backup waits for it before it finishes
        self.backup_gate: asyncio.Event | None = None
        self.zips = zips
        if fake_backups:
            self.keeper.backup = self.fake_backup
        self.client: TestClient
        self._started: TestClient | None = None

    def __enter__(self) -> Self:
        self._started = TestClient(self.app, base_url=self.base_url)
        self.client = self._started.__enter__()
        return self

    def __exit__(self, *exc: Any) -> None:
        assert self._started is not None
        self._started.__exit__(*exc)

    def restarted(self) -> "AppEnv":
        """A new app over the same settings and the same fake bw serve, not started yet."""
        settings = {f.name: getattr(self.settings, f.name) for f in dataclasses.fields(Settings)}
        return AppEnv(
            settings.pop("data_dir"), bw=self.bw, zips=self.zips, base_url=self.base_url, **settings
        )

    async def fake_backup(
        self, session: Any, directory: Path, bw_bin: str, *, store: Any, stop: Any = None
    ) -> BackupInfo:
        assert session.password == PASSWORD
        assert session.session_key
        self.backups_active += 1
        if self.backups_active > 1:
            self.backups_overlapped = True
        await asyncio.sleep(0.01)
        if self.backup_gate is not None:
            await self.backup_gate.wait()
        self.backups_active -= 1
        ## like create_backup, a stop asked for meanwhile ends it before its next step
        if stop is not None and stop():
            raise BackupStopped("personal export")
        if self.backup_error is not None:
            raise self.backup_error
        when = NOW + timedelta(seconds=len(self.backups))
        name = backup_name(when)
        directory.mkdir(parents=True, exist_ok=True)
        data = zip_backup(self.bw) if self.zips else b"fake backup"
        (directory / name).write_bytes(data)
        self.backups.append(name)
        print_ = fingerprint(
            self.bw.items, self.bw.folders, self.bw.collections, self.bw.organizations
        )
        store.set_backup_fingerprint(name, print_)
        return BackupInfo(name, len(data), when, print_)

    def login(self, password: str = PASSWORD) -> httpx.Response:
        """Post the master password, the answer as it came."""
        return self.client.post("/api/session", json={"password": password})

    def unlock(self) -> None:
        """Log in and wait for the backup the unlock starts."""
        assert self.login().status_code == 200
        self.settle()

    def settle(self) -> None:
        """Wait for the background backup, so the backup counts below are deterministic."""
        self.call(self.keeper.wait_for_background_backup)

    def call(self, func: Callable[[], Any]) -> Any:
        """Run func on the app's event loop and answer what it returns."""
        assert self.client.portal is not None
        return self.client.portal.call(func)

    def state(self) -> str:
        return self.session_status()["state"]

    def session_status(self) -> dict[str, Any]:
        return self.client.get("/api/session").json()

    def use_cookie(self, token: str) -> None:
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, token)

    def check_timeouts(self) -> None:
        self.call(self.session.check_timeouts)

    @property
    def tool_note_id(self) -> str:
        note = find_tool_note(self.cache, self.session.tool_note_id)
        assert note is not None
        return note["id"]

    @property
    def login_backup(self) -> str:
        return self.backups[0]

    def revisions(self) -> dict[str, str]:
        rows = self.client.get("/api/grid").json()["rows"]
        return {row["id"]: row["revisionDate"] for row in rows}

    def post(self, *operations: dict[str, Any]):
        response = self.client.post("/api/operations", json={"operations": list(operations)})
        self.settle()
        return response

    def set_leaf(self, item_id: str, column: str, value: Any, **extra: Any) -> dict[str, Any]:
        return {
            "kind": "setLeaf",
            "itemId": item_id,
            "revisionDate": self.revisions()[item_id],
            "column": column,
            "value": value,
            **extra,
        }

    def op(self, kind: str, item_id: str, **extra: Any) -> dict[str, Any]:
        return {"kind": kind, "itemId": item_id, "revisionDate": self.revisions()[item_id], **extra}

    def item(self, item_id: str) -> dict[str, Any]:
        return next(i for i in self.bw.items if i["id"] == item_id)

    def change_log(self) -> list[dict[str, Any]]:
        response = self.client.get("/api/change-log")
        assert response.status_code == 200
        return response.json()

    def backup_files(self) -> list[str]:
        directory = self.settings.backup_dir
        return sorted(p.name for p in directory.iterdir()) if directory.exists() else []

    def backup_items(self, name: str) -> dict[str, dict[str, Any]]:
        """raw/items.json of a backup written with zips on, by item id."""
        with zipfile.ZipFile(self.settings.backup_dir / name) as archive:
            return {i["id"]: i for i in json.loads(archive.read("raw/items.json"))}

    def reload(self) -> None:
        """Load the vault in RAM again, after a change made straight in the fake bw serve."""
        self.call(self.cache.load)

    def plan(self, change_log_id: int):
        return self.client.get("/api/restore-plan", params={"changeLogId": change_log_id})

    def restore(self, change_log_id: int):
        """Restore a change log row the way the UI does: plan it, then post the operation."""
        plan = self.plan(change_log_id)
        assert plan.status_code == 200, plan.text
        return self.post(plan.json()["operation"])


def logged_in(data_dir: Path, **knobs: Any) -> Iterator[AppEnv]:
    """An AppEnv started and logged in, its login's backup taken."""
    with AppEnv(data_dir, **knobs) as env:
        env.unlock()
        yield env


def session_app(**settings: Any) -> AppEnv:
    """The app of the session tests: the fake bw serve's own vault (one folder, no items), no
    data directory, no fake backups, and GET /api/dummy behind the session."""
    env = AppEnv(Path("/nonexistent"), bw=FakeBwServe(), fake_backups=False, **settings)

    @env.app.get("/api/dummy")
    async def dummy(session: Annotated[SessionManager, Depends(require_session)]) -> dict:
        return {"items": len(session.cache.items)}

    return env


def zip_backup(bw: FakeBwServe) -> bytes:
    """A backup zip of the fake bw serve's state, the members create_backup writes."""
    items = copy.deepcopy(bw.items)
    personal = io.BytesIO()
    with zipfile.ZipFile(personal, "w") as export:
        export.writestr("data.json", json.dumps({"encrypted": False, "items": []}))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("personal-export.zip", personal.getvalue())
        for item in items + bw.trashed:
            for attachment, member in attachment_members(item):
                archive.writestr(member, bw.attachments[(item["id"], attachment["id"])])
        archive.writestr("raw/items.json", json.dumps(items))
        archive.writestr("raw/trash.json", json.dumps(bw.trashed))
        archive.writestr("raw/folders.json", json.dumps(bw.folders))
        archive.writestr("raw/collections.json", json.dumps(bw.collections))
        archive.writestr("raw/organizations.json", json.dumps(bw.organizations))
    return buffer.getvalue()
