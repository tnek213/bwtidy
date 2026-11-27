"""Async client for the Vault Management API of `bw serve`."""

from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from bwtidy.bw.revision import Spellings


class BwError(Exception):
    """bw serve refused a request. The message is bw serve's own."""


class BwUnavailable(BwError):
    """bw serve did not answer at all."""


def _q(value: str) -> str:
    return quote(value, safe="")


class BwClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http
        ## one spelling per revision for every item answer, kept as long as the app runs
        self.spellings = Spellings()

    def _settled[T](self, answer: T) -> T:
        """answer, an item or a list of them, with each revisionDate in its known spelling
        (revision.Spellings)."""
        for item in answer if isinstance(answer, list) else [answer]:
            self.spellings.settle(item)
        return answer

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._http.request(method, path, **kwargs)
        except httpx.HTTPError:
            ## from None: the request in the cause carries the body, which can hold a password
            raise BwUnavailable("bw serve is not reachable") from None

    @staticmethod
    def _data(response: httpx.Response) -> Any:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.is_error or not isinstance(payload, dict) or payload.get("success") is not True:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise BwError(message or f"bw serve answered {response.status_code}")
        return payload.get("data")

    async def _request(
        self, method: str, path: str, body: Any = None, params: dict[str, str] | None = None
    ) -> Any:
        return self._data(await self._send(method, path, json=body, params=params))

    async def unlock(self, password: str) -> str | None:
        """Unlock the vault and return the session key, None when bw serve does not send it.

        bw serve 2026.10.0 sends the key in data.raw, and the CLI accepts it as BW_SESSION.
        Every unlock, by bw serve or by `bw unlock`, hands out a new key and makes the older
        ones useless to the CLI. POST /lock does that too. A wrong password is a 400 with the
        message "Cryptography error, The decryption operation failed".
        """
        data = await self._request("POST", "/unlock", {"password": password})
        return data.get("raw") if isinstance(data, dict) else None

    async def lock(self) -> None:
        await self._request("POST", "/lock")

    ## answers success even while the vault is locked
    async def sync(self) -> None:
        await self._request("POST", "/sync")

    async def status(self) -> dict[str, Any]:
        data = await self._request("GET", "/status")
        return data.get("template", {}) if isinstance(data, dict) else {}

    async def _list(self, kind: str) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/list/object/{kind}")
        return data.get("data", []) if isinstance(data, dict) else []

    ## items leave out every key whose value is null (organizationId, folderId, notes, totp)
    async def list_items(self) -> list[dict[str, Any]]:
        return self._settled(await self._list("items"))

    async def list_folders(self) -> list[dict[str, Any]]:
        return await self._list("folders")

    ## the items in the trash, what `bw list items --trash` lists
    async def list_trash(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/list/object/items", params={"trash": "true"})
        return self._settled(data.get("data", []) if isinstance(data, dict) else [])

    ## the archived items, which every other listing leaves out: what `bw list items --archived`
    ## lists, and with trash those archived items that are in the trash. For an account without
    ## premium bw drops archived=true and lists the items that are not archived instead, so
    ## only those with an archivedDate are kept.
    async def list_archived(self, trash: bool = False) -> list[dict[str, Any]]:
        params = {"archived": "true", **({"trash": "true"} if trash else {})}
        data = await self._request("GET", "/list/object/items", params=params)
        listed = self._settled(data.get("data", []) if isinstance(data, dict) else [])
        return [i for i in listed if i.get("archivedDate")]

    async def list_collections(self) -> list[dict[str, Any]]:
        return await self._list("collections")

    async def list_organizations(self) -> list[dict[str, Any]]:
        return await self._list("organizations")

    async def get_item(self, item_id: str) -> dict[str, Any]:
        return self._settled(await self._request("GET", f"/object/item/{_q(item_id)}"))

    async def create_item(self, obj: dict[str, Any]) -> dict[str, Any]:
        return self._settled(await self._request("POST", "/object/item", obj))

    async def put_item(self, item_id: str, obj: dict[str, Any]) -> dict[str, Any]:
        return self._settled(await self._request("PUT", f"/object/item/{_q(item_id)}", obj))

    ## DELETE without ?permanent moves the item to the trash, and permanent is never used here
    async def trash_item(self, item_id: str) -> None:
        await self._request("DELETE", f"/object/item/{_q(item_id)}")

    ## `bw restore item`: takes an item out of the trash
    async def restore_item(self, item_id: str) -> None:
        await self._request("POST", f"/restore/item/{_q(item_id)}")

    ## the body is the full list of collection ids, as `bw edit item-collections` takes it
    async def put_item_collections(self, item_id: str, collection_ids: list[str]) -> Any:
        answer = await self._request(
            "PUT", f"/object/item-collections/{_q(item_id)}", collection_ids
        )
        return self._settled(answer)

    ## `bw move` (once `bw share`): a personal item into an organization and its collections
    async def move_to_organization(
        self, item_id: str, organization_id: str, collection_ids: list[str]
    ) -> Any:
        answer = await self._request(
            "POST", f"/move/{_q(item_id)}/{_q(organization_id)}", collection_ids
        )
        return self._settled(answer)

    async def generate(self, query: dict[str, str]) -> str:
        """A new value from bw's generator with the options of this query (the generator
        section's bw_query). It goes into an item, or into the example of the generator dialogs,
        and nowhere else.

        An organization's generator policy can ask for more.
        """
        data = await self._request("GET", "/generate", params=query)
        value = data.get("data") if isinstance(data, dict) else None
        if not isinstance(value, str) or not value:
            raise BwError("bw serve generated no value")
        return value

    ## bw serve knows a created folder only after a sync: an edit or delete before it answers
    ## "Not found."
    async def create_folder(self, name: str) -> dict[str, Any]:
        return await self._request("POST", "/object/folder", {"name": name})

    async def edit_folder(self, folder_id: str, name: str) -> dict[str, Any]:
        return await self._request("PUT", f"/object/folder/{_q(folder_id)}", {"name": name})

    ## Bitwarden takes the items still in a deleted folder out of it, so the write path deletes
    ## only an empty one
    async def delete_folder(self, folder_id: str) -> None:
        await self._request("DELETE", f"/object/folder/{_q(folder_id)}")

    async def get_attachment(self, attachment_id: str, item_id: str) -> bytes:
        """The decrypted bytes of one attachment. bw serve streams them, not a JSON envelope."""
        response = await self._send(
            "GET", f"/object/attachment/{_q(attachment_id)}", params={"itemid": item_id}
        )
        if response.is_error:
            self._data(response)
        return response.content

    async def upload_attachment(self, item_id: str, file_name: str, data: bytes) -> Any:
        """Add an attachment to an item, as multipart form data in the field `file`."""
        response = await self._send(
            "POST",
            "/attachment",
            params={"itemid": item_id},
            files={"file": (file_name, data, "application/octet-stream")},
        )
        return self._data(response)

    ## deletes the attachment for good (Bitwarden has no trash for one), only a rename does it,
    ## once its copy under the new name is listed
    async def delete_attachment(self, attachment_id: str, item_id: str) -> None:
        await self._request(
            "DELETE", f"/object/attachment/{_q(attachment_id)}", params={"itemid": item_id}
        )
