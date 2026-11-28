"""The unlocked vault, held in RAM while a session lasts."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from bwtidy.bw import BwClient
from bwtidy.items.coordinates import Lookups
from bwtidy.vault.own_writes import OwnWrites

log = logging.getLogger(__name__)

## how long a listing waits for bw serve to decrypt the items again after a sync
## (see list_items_settled), read at call time so tests can monkeypatch them
EMPTY_LIST_RETRIES = 25
EMPTY_LIST_DELAY = 0.2

Objects = list[dict[str, Any]]
Listing = Callable[[], Awaitable[Objects]]


async def list_until(listing: Listing, settled: Callable[[Objects], bool]) -> Objects:
    """Call the listing again, with the retry budget, until settled(result) holds.

    The first answer is returned at once when it is settled, so nothing waits in the
    normal case. Once the budget is spent the last answer is returned, settled or not.
    """
    result = await listing()
    for _ in range(EMPTY_LIST_RETRIES):
        if settled(result):
            break
        await asyncio.sleep(EMPTY_LIST_DELAY)
        result = await listing()
    return result


async def list_items_settled(client: BwClient) -> list[dict[str, Any]]:
    """GET /list/object/items, asked again while it answers an empty list.

    Right after a POST /sync that fetched something, bw serve 2026.10.0 answers an empty
    item list for a moment while it decrypts the ciphers again. Taking that at face value
    would make the write path call every item vanished, a login create a second tool note
    and a backup hold no items. A vault is only ever empty before the first login created
    the tool note, so an empty list is retried for a few seconds before it is believed.
    """
    return await list_until(client.list_items, bool)


class VaultCache:
    """The vault in RAM: its items, folders, collections, organizations, the items in the
    trash and the archived items, and own, the chain of bwtidy's own writes this session
    (own_writes.py)."""

    def __init__(self, client: BwClient, on_load: Callable[[], None]) -> None:
        self._client = client
        ## runs after every load, its failure is only logged
        self._on_load = on_load
        self.items: list[dict[str, Any]] = []
        self.folders: list[dict[str, Any]] = []
        self.collections: list[dict[str, Any]] = []
        self.organizations: list[dict[str, Any]] = []
        ## the items in the trash, so a folder that still holds one is not deleted, and the ids
        ## of every item in the trash (the archived ones too), so a restore knows what it can
        ## take out of it
        self.trashed: list[dict[str, Any]] = []
        self.trash_ids: set[str] = set()
        ## the archived items, which every other listing leaves out: those that are not in the
        ## trash (archived) and those that are (archived_trash). The backups' fingerprint, the
        ## restores and the folder checks read them (docs/spec.md, Backups)
        self.archived: list[dict[str, Any]] = []
        self.archived_trash: list[dict[str, Any]] = []
        self.loaded = False
        self.own = OwnWrites()
        ## how many loads replaced the items, so what is computed from them (formats.py) knows
        ## when it was computed from items that are gone
        self.loads = 0

    def lookups(self) -> Lookups:
        """Names for the ids the items refer to, as the cache holds them now."""
        return Lookups.build(self.folders, self.organizations, self.collections)

    def items_by_id(self) -> dict[str, dict[str, Any]]:
        """The items by id, an item without an id left out."""
        return {item["id"]: item for item in self.items if item.get("id")}

    def archived_by_id(self) -> dict[str, dict[str, Any]]:
        """The archived items that are not in the trash by id, an item without an id left out."""
        return {item["id"]: item for item in self.archived if item.get("id")}

    def set_aside(self) -> list[dict[str, Any]]:
        """The items the item listing leaves out: those in the trash and the archived ones."""
        return [*self.trashed, *self.archived, *self.archived_trash]

    def replace_item(self, item_id: str, item: dict[str, Any]) -> None:
        """Put bw serve's answer to an own PUT in place of the cached item, until the next
        load. An answer of another id changes nothing."""
        if item.get("id") != item_id:
            return
        self.items = [item if i.get("id") == item_id else i for i in self.items]

    async def load(self) -> None:
        self.items = await list_items_settled(self._client)
        self.loads += 1
        self.folders = await self._client.list_folders()
        self.collections = await self._client.list_collections()
        self.organizations = await self._client.list_organizations()
        self.trashed = await self._client.list_trash()
        self.archived = await self._client.list_archived()
        self.archived_trash = await self._client.list_archived(trash=True)
        self.trash_ids = {i["id"] for i in [*self.trashed, *self.archived_trash] if i.get("id")}
        self.loaded = True
        try:
            self._on_load()
        except Exception as error:
            ## the type only: a message could carry a value the listener touched
            log.warning("After loading the vault: %s", type(error).__name__)

    def clear(self) -> None:
        self.items = []
        self.folders = []
        self.collections = []
        self.organizations = []
        self.trashed = []
        self.trash_ids = set()
        self.archived = []
        self.archived_trash = []
        self.loaded = False
        self.own.clear()
