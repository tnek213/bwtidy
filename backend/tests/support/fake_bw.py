"""An in-memory stand-in for `bw serve`, served through httpx MockTransport.

Responses copy what bw serve 2026.10.0 sends against Vaultwarden (see bw/client.py).
"""

import asyncio
import email
import json
from datetime import datetime
from typing import Any

import httpx2 as httpx

from bwtidy.backups.collect import encrypted_size

PASSWORD = "correct horse battery staple"
WRONG_PASSWORD_MESSAGE = "Cryptography error, The decryption operation failed"
## Vaultwarden's answer to a write on a copy older than its own, which bw passes on
OUT_OF_DATE_MESSAGE = (
    "The client copy of this cipher is out of date. Resync the client and try again."
)


def ok(data: Any = None) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "data": data})


def fail(message: str, status: int = 400) -> httpx.Response:
    return httpx.Response(status, json={"success": False, "message": message})


def message(title: str, text: str | None = None, **extra: Any) -> httpx.Response:
    return ok({"noColor": False, "object": "message", "title": title, "message": text, **extra})


def listing(objects: list[dict[str, Any]]) -> httpx.Response:
    return ok({"object": "list", "data": objects})


def created_item(body: dict[str, Any], item_id: str) -> dict[str, Any]:
    """What bw serve answers to POST /object/item: null keys left out, lists filled in."""
    item = {k: v for k, v in body.items() if v is not None}
    return {
        "collectionIds": [],
        "passwordHistory": [],
        "attachments": [],
        **item,
        "id": item_id,
        "object": "item",
        "creationDate": "2026-10-26T13:08:59.304Z",
        "revisionDate": "2026-10-26T13:08:59.304Z",
    }


def seconds_newer(server: dict[str, Any], local: dict[str, Any]) -> int:
    """How many whole seconds the server's revisionDate is ahead of the local one, truncated
    like Vaultwarden's check."""
    ahead = datetime.fromisoformat(server["revisionDate"]) - datetime.fromisoformat(
        local["revisionDate"]
    )
    return int(ahead.total_seconds())


def generated_password(serial: int, length: int) -> str:
    """A password like bw's generator makes one, with every character class, unique per call."""
    head = f"Gen-{serial}-pw!"
    return (head + "x7" * length)[:length]


class FakeBwServe:
    def __init__(self, items: list[dict[str, Any]] | None = None) -> None:
        self.unlocked = False
        ## the real bw serve does not even run while the CLI is logged out, this fake answers
        ## GET /status with "unauthenticated" instead so the fallback to `bw status` is tested
        ## (but "unlocked" once it unlocked, as the login's check of the unlock wants)
        self.logged_in = True
        self.unlocks = 0
        self.session_key: str | None = None
        self.items: list[dict[str, Any]] = list(items or [])
        ## items another client changed on the server after bw serve's last sync, by id, as the
        ## server holds them: the next POST /sync brings them into items
        self.on_server: dict[str, dict[str, Any]] = {}
        self.folders: list[dict[str, Any]] = [{"object": "folder", "id": "f1", "name": "Work"}]
        self.collections: list[dict[str, Any]] = []
        self.organizations: list[dict[str, Any]] = []
        self.requests: list[tuple[str, str]] = []
        ## posted holds the bodies of POST /object/item, created what bw serve answered
        self.posted: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []
        ## the bodies of PUT /object/item/<id>, as (id, body)
        self.put: list[tuple[str, dict[str, Any]]] = []
        ## items moved to the trash by DELETE, in order, and the ids taken out of it again
        self.trashed: list[dict[str, Any]] = []
        self.restored: list[str] = []
        ## the archived items, those in the trash with a deletedDate: only archived=true lists
        ## them, with trash=true those in the trash
        self.archived: list[dict[str, Any]] = []
        ## without premium the real bw drops archived=true and lists what is not archived
        self.premium = True
        ## attachment bytes by (item id, attachment id), and the uploads as (item id, file name).
        ## Like Vaultwarden, an upload or a delete of an attachment leaves the revisionDate as
        ## it is, unless attachment_revisions asks for a new one each, as a server could
        self.attachments: dict[tuple[str, str], bytes] = {}
        self.uploads: list[tuple[str, str]] = []
        self.attachment_revisions = False
        ## (method, path) pairs that answer a bw error instead of working
        self.fail_at: set[tuple[str, str]] = set()
        ## seconds every request waits before it is handled, to make calls overlap
        self.delay = 0.0
        ## an unset event makes every request wait until it is set: a bw serve that does not
        ## answer (the real client gives up after 120 s, this one never does)
        self.answering: asyncio.Event | None = None
        ## an unset event makes every PUT of an item wait until it is set, put_waiting is True
        ## while one waits
        self.put_gate: asyncio.Event | None = None
        self.put_waiting = False
        ## seconds a POST /lock runs before it locks, also when the client stopped waiting for
        ## it: the real bw serve waits for the CLI's lock file (stale after 10 s) and then locks
        self.lock_delay = 0.0
        self._late_locks: set[asyncio.Future[httpx.Response]] = set()
        ## how many item lists after each sync come back empty, like the real bw serve while it
        ## decrypts the ciphers again (see VaultCache)
        self.empty_lists_after_sync = 0
        self._empty_lists_left = 0
        ## every write hands out a new revisionDate, like a real server
        self._revisions = 0
        ## the passwords GET /generate handed out, and the query of each call
        self.generated: list[str] = []
        self.generate_queries: list[dict[str, str]] = []

    def _next_revision(self) -> str:
        self._revisions += 1
        minutes, seconds = divmod(self._revisions, 60)
        return f"2026-10-26T14:{minutes:02d}:{seconds:02d}.000Z"

    def change_on_server(self, item: dict[str, Any]) -> None:
        """Another client writes the item to the server, bw serve sees it at its next sync."""
        self.on_server[item["id"]] = item

    def calls(self, method: str, path: str) -> int:
        return self.requests.count((method, path))

    def handler(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.requests.append((method, path))
        if (method, path) in self.fail_at:
            return fail("forced failure")
        is_json = request.headers.get("content-type", "").startswith("application/json")
        body = json.loads(request.content) if request.content and is_json else None
        if (method, path) == ("POST", "/unlock"):
            password = (body or {}).get("password")
            if not password:
                return fail(
                    "Master password is required. Try again in interactive mode or provide a "
                    "password file or environment variable."
                )
            if password != PASSWORD:
                return fail(WRONG_PASSWORD_MESSAGE)
            ## every real unlock hands out a new key and invalidates the old one
            self.unlocked = True
            self.unlocks += 1
            self.session_key = f"fake-session-key-{self.unlocks}"
            return message(
                "Your vault is now unlocked!",
                f"\nTo unlock your vault, set your session key ... {self.session_key}",
                raw=self.session_key,
            )
        if (method, path) == ("POST", "/lock"):
            self.unlocked = False
            self.session_key = None
            return message("Your vault is locked.")
        if (method, path) == ("GET", "/status"):
            if not self.logged_in and not self.unlocked:
                template = {"serverUrl": None, "lastSync": None, "status": "unauthenticated"}
                return ok({"object": "template", "template": template})
            status = "unlocked" if self.unlocked else "locked"
            return ok(
                {
                    "object": "template",
                    "template": {
                        "serverUrl": "https://vaultwarden",
                        "lastSync": "2026-10-26T12:57:21.511Z",
                        "userEmail": "dev@bwtidy.test",
                        "userId": "d343ef26-c5af-4758-a134-6bf26a63e436",
                        "status": status,
                    },
                }
            )
        ## real bw serve answers success to a sync while locked
        if (method, path) == ("POST", "/sync"):
            self._empty_lists_left = self.empty_lists_after_sync
            for index, item in enumerate(self.items):
                if item.get("id") in self.on_server:
                    self.items[index] = self.on_server.pop(item["id"])
            return message("Syncing complete.")
        if not self.unlocked:
            return fail("Vault is locked.")
        if (method, path) == ("GET", "/list/object/items"):
            trash = request.url.params.get("trash") == "true"
            if request.url.params.get("archived") == "true" and self.premium:
                return listing([i for i in self.archived if bool(i.get("deletedDate")) == trash])
            if trash:
                return listing(self.trashed)
            if self._empty_lists_left > 0:
                self._empty_lists_left -= 1
                return listing([])
            return listing(self.items)
        ## while it decrypts, the real bw serve knows no object at all, not only no list
        if self._empty_lists_left > 0 and (
            path.startswith(("/object/item/", "/object/attachment/")) or path == "/attachment"
        ):
            return fail("Not found.")
        if (method, path) == ("GET", "/list/object/folders"):
            return listing(self.folders)
        if (method, path) == ("GET", "/list/object/collections"):
            return listing(self.collections)
        if (method, path) == ("GET", "/list/object/organizations"):
            return listing(self.organizations)
        if (method, path) == ("POST", "/object/item"):
            self.posted.append(body or {})
            item = created_item(body or {}, f"new-{len(self.created) + 1}")
            self.created.append(item)
            self.items.append(item)
            return ok(item)
        ## like the real bw serve, GET answers a trashed or an archived item too
        if method == "GET" and path.startswith("/object/item/"):
            item_id = path.removeprefix("/object/item/")
            found = [i for i in self.items + self.trashed + self.archived if i.get("id") == item_id]
            return ok(found[0]) if found else fail("Not found.")
        if method == "DELETE" and path.startswith("/object/item/"):
            item_id = path.removeprefix("/object/item/")
            found = [i for i in self.items if i.get("id") == item_id]
            if not found:
                return fail("Not found.")
            self.items.remove(found[0])
            self.trashed.append({**found[0], "deletedDate": "2026-10-26T15:00:00.000Z"})
            return ok(None)
        ## like the real bw serve, a restore gives the item a new revisionDate at once
        if method == "POST" and path.startswith("/restore/item/"):
            item_id = path.removeprefix("/restore/item/")
            found = [i for i in self.trashed if i.get("id") == item_id]
            if not found:
                return fail("Not found.")
            self.trashed.remove(found[0])
            restored = {k: v for k, v in found[0].items() if k != "deletedDate"}
            restored["revisionDate"] = self._next_revision()
            self.items.append(restored)
            self.restored.append(item_id)
            return ok(None)
        if method == "PUT" and path.startswith("/object/item-collections/"):
            item_id = path.removeprefix("/object/item-collections/")
            found = [i for i in self.items if i.get("id") == item_id]
            if not found or not isinstance(body, list):
                return fail("Not found.")
            found[0]["collectionIds"] = list(body)
            found[0]["revisionDate"] = self._next_revision()
            ## the server takes it without a check, over another client's pending change
            if item_id in self.on_server:
                self.on_server[item_id]["collectionIds"] = list(body)
            return ok(found[0])
        if method == "POST" and path.startswith("/move/"):
            item_id, _, org_id = path.removeprefix("/move/").partition("/")
            found = [i for i in self.items if i.get("id") == item_id]
            if not found or not isinstance(body, list):
                return fail("Not found.")
            found[0]["organizationId"] = org_id
            found[0]["collectionIds"] = list(body)
            found[0]["revisionDate"] = self._next_revision()
            return ok(found[0])
        if (method, path) == ("GET", "/generate"):
            query = dict(request.url.params)
            self.generate_queries.append(query)
            password = generated_password(len(self.generated) + 1, int(query.get("length", 14)))
            self.generated.append(password)
            return ok({"object": "string", "data": password})
        if (method, path) == ("POST", "/object/folder"):
            folder = {
                "object": "folder",
                "id": f"folder-{len(self.folders) + 1}",
                "name": (body or {}).get("name"),
            }
            self.folders.append(folder)
            return ok(folder)
        if method in ("PUT", "DELETE") and path.startswith("/object/folder/"):
            folder_id = path.removeprefix("/object/folder/")
            found = [f for f in self.folders if f.get("id") == folder_id]
            if not found:
                return fail("Not found.")
            if method == "DELETE":
                self.folders.remove(found[0])
                ## like Bitwarden, the items still in it lose their folder
                for item in self.items + self.trashed:
                    if item.get("folderId") == folder_id:
                        item["folderId"] = None
                return ok(None)
            found[0]["name"] = (body or {}).get("name")
            return ok(found[0])
        if method == "GET" and path.startswith("/object/attachment/"):
            attachment_id = path.removeprefix("/object/attachment/")
            item_id = request.url.params.get("itemid", "")
            data = self.attachments.get((item_id, attachment_id))
            if data is None:
                return fail("Attachment `" + attachment_id + "` was not found.")
            return httpx.Response(
                200, content=data, headers={"content-type": "application/octet-stream"}
            )
        ## like the real bw serve: {"success": true} and no item, the revisionDate stays
        if method == "DELETE" and path.startswith("/object/attachment/"):
            attachment_id = path.removeprefix("/object/attachment/")
            item_id = request.url.params.get("itemid", "")
            found = [i for i in self.items if i.get("id") == item_id]
            listed = found[0].get("attachments") or [] if found else []
            if not any(a.get("id") == attachment_id for a in listed):
                return fail("Attachment `" + attachment_id + "` was not found.")
            found[0]["attachments"] = [a for a in listed if a.get("id") != attachment_id]
            self.attachments.pop((item_id, attachment_id), None)
            if self.attachment_revisions:
                found[0]["revisionDate"] = self._next_revision()
            return httpx.Response(200, json={"success": True})
        if (method, path) == ("POST", "/attachment"):
            item_id = request.url.params.get("itemid", "")
            found = [i for i in self.items if i.get("id") == item_id]
            if not found:
                return fail("Not found.")
            file_name, data = parse_upload(request)
            attachment_id = f"up-{len(self.uploads) + 1}"
            self.attachments[(item_id, attachment_id)] = data
            self.uploads.append((item_id, file_name))
            found[0].setdefault("attachments", []).append(
                {"id": attachment_id, "fileName": file_name, "size": str(encrypted_size(len(data)))}
            )
            if self.attachment_revisions:
                found[0]["revisionDate"] = self._next_revision()
            return ok(found[0])
        if method == "PUT" and path.startswith("/object/item/"):
            item_id = path.removeprefix("/object/item/")
            self.put.append((item_id, body or {}))
            ## bw sends its own copy's revisionDate as lastKnownRevisionDate, never the body's
            local = next((i for i in self.items if i.get("id") == item_id), None)
            server = self.on_server.get(item_id)
            if local is not None and server is not None:
                if seconds_newer(server, local) > 1:
                    return fail(OUT_OF_DATE_MESSAGE)
                del self.on_server[item_id]
            ## like the real bw serve, a PUT of the item leaves its collections alone
            changes = {k: v for k, v in (body or {}).items() if k != "collectionIds"}
            for index, item in enumerate(self.items):
                if item.get("id") == item_id:
                    updated = {**item, **changes, "revisionDate": self._next_revision()}
                    self.items[index] = updated
                    return ok(updated)
            return fail("Not found.")
        return httpx.Response(404, text="Not Found")

    async def slow_handler(self, request: httpx.Request) -> httpx.Response:
        if self.answering is not None:
            await self.answering.wait()
        if self.put_gate is not None and (
            request.method == "PUT" and request.url.path.startswith("/object/item/")
        ):
            self.put_waiting = True
            try:
                await self.put_gate.wait()
            finally:
                self.put_waiting = False
        if self.lock_delay and (request.method, request.url.path) == ("POST", "/lock"):
            landing = asyncio.ensure_future(self._late_lock(request, self.lock_delay))
            self._late_locks.add(landing)
            landing.add_done_callback(self._late_locks.discard)
            return await asyncio.shield(landing)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.handler(request)

    async def _late_lock(self, request: httpx.Request, delay: float) -> httpx.Response:
        await asyncio.sleep(delay)
        return self.handler(request)

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://127.0.0.1:8087", transport=httpx.MockTransport(self.slow_handler)
        )


def parse_upload(request: httpx.Request) -> tuple[str, bytes]:
    """The file name and bytes of the `file` part of a multipart upload."""
    head = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
    message = email.message_from_bytes(head + request.content)
    for part in message.walk():
        if part.get_filename():
            payload = part.get_payload(decode=True)
            return part.get_filename() or "", payload if isinstance(payload, bytes) else b""
    raise AssertionError("no file part in the upload")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
