import asyncio
import json
from typing import Any

import httpx2 as httpx
import pytest

from bwtidy.bw.client import BwClient, BwError, BwUnavailable


class Recorder:
    def __init__(self, data: Any = None) -> None:
        self.data = data
        self.requests: list[tuple[str, str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.raw_path.decode(), body))
        return httpx.Response(200, json={"success": True, "data": self.data})

    def client(self) -> BwClient:
        return BwClient(
            httpx.AsyncClient(base_url="http://bw", transport=httpx.MockTransport(self))
        )


def mock_client(handler) -> BwClient:
    return BwClient(httpx.AsyncClient(base_url="http://bw", transport=httpx.MockTransport(handler)))


def run(coro):
    return asyncio.run(coro)


def test_unlock_posts_password_and_returns_session_key():
    rec = Recorder({"object": "message", "raw": "key"})
    assert run(rec.client().unlock("pw")) == "key"
    assert rec.requests == [("POST", "/unlock", {"password": "pw"})]


def test_unlock_without_raw_returns_none():
    rec = Recorder({"object": "message"})
    assert run(rec.client().unlock("pw")) is None


@pytest.mark.parametrize(
    ("method_name", "path"),
    [("lock", "/lock"), ("sync", "/sync")],
)
def test_post_commands(method_name: str, path: str):
    rec = Recorder({"object": "message"})
    run(getattr(rec.client(), method_name)())
    assert rec.requests == [("POST", path, None)]


def test_status_returns_the_template():
    rec = Recorder({"object": "template", "template": {"status": "locked"}})
    assert run(rec.client().status()) == {"status": "locked"}
    assert rec.requests == [("GET", "/status", None)]


@pytest.mark.parametrize(
    ("method_name", "path"),
    [
        ("list_items", "/list/object/items"),
        ("list_folders", "/list/object/folders"),
        ("list_collections", "/list/object/collections"),
        ("list_organizations", "/list/object/organizations"),
    ],
)
def test_lists(method_name: str, path: str):
    rec = Recorder({"object": "list", "data": [{"id": "a"}]})
    assert run(getattr(rec.client(), method_name)()) == [{"id": "a"}]
    assert rec.requests == [("GET", path, None)]


def test_get_item():
    rec = Recorder({"id": "abc"})
    assert run(rec.client().get_item("abc")) == {"id": "abc"}
    assert rec.requests == [("GET", "/object/item/abc", None)]


def test_create_item_posts_the_object():
    rec = Recorder({"id": "new"})
    obj = {"type": 2, "name": "n"}
    assert run(rec.client().create_item(obj)) == {"id": "new"}
    assert rec.requests == [("POST", "/object/item", obj)]


def test_put_item_puts_the_full_object():
    rec = Recorder({"id": "abc"})
    obj = {"id": "abc", "type": 1, "name": "n"}
    run(rec.client().put_item("abc", obj))
    assert rec.requests == [("PUT", "/object/item/abc", obj)]


def test_every_item_answer_keeps_the_first_spelling_of_a_revision():
    at = "2026-10-26T10:15:30"
    revisions = {"list": f"{at}.337Z", "get": f"{at}.336Z", "put": "2026-10-26T10:15:33.000Z"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/list/object/items":
            data: Any = {"data": [{"id": "a", "revisionDate": revisions["list"]}]}
        else:
            kind = "put" if request.method == "PUT" else "get"
            data = {"id": "a", "revisionDate": revisions[kind]}
        return httpx.Response(200, json={"success": True, "data": data})

    async def go() -> list[str]:
        client = mock_client(handler)
        seen = [(await client.list_items())[0]["revisionDate"]]
        seen.append((await client.get_item("a"))["revisionDate"])
        seen.append((await client.put_item("a", {"id": "a"}))["revisionDate"])
        ## bw serve answers the PUT's revision cut where the PUT rounded it
        revisions["get"] = "2026-10-26T10:15:32.999Z"
        seen.append((await client.get_item("a"))["revisionDate"])
        return seen

    assert run(go()) == [f"{at}.337Z", f"{at}.337Z", revisions["put"], revisions["put"]]


def test_item_id_is_quoted():
    rec = Recorder({})
    run(rec.client().get_item("a/b"))
    assert rec.requests[0][1] == "/object/item/a%2Fb"


def failing(response: httpx.Response) -> BwClient:
    return mock_client(lambda request: response)


def test_error_carries_bw_serve_message():
    client = failing(httpx.Response(400, json={"success": False, "message": "Vault is locked."}))
    with pytest.raises(BwError, match="Vault is locked."):
        run(client.sync())


def test_success_false_with_200_is_an_error():
    client = failing(httpx.Response(200, json={"success": False, "message": "Nope."}))
    with pytest.raises(BwError, match="Nope."):
        run(client.lock())


def test_non_json_error_names_the_status():
    client = failing(httpx.Response(500, text="boom"))
    with pytest.raises(BwError, match="500"):
        run(client.status())


def test_unreachable_raises_bw_unavailable_without_the_password():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = BwClient(
        httpx.AsyncClient(base_url="http://bw", transport=httpx.MockTransport(refuse))
    )
    with pytest.raises(BwUnavailable) as info:
        run(client.unlock("secret-pw"))
    assert info.value.__cause__ is None
    assert "secret-pw" not in str(info.value)


def test_trash_item_deletes_without_permanent():
    rec = Recorder(None)
    run(rec.client().trash_item("abc"))
    assert rec.requests == [("DELETE", "/object/item/abc", None)]


def test_put_item_collections_sends_the_id_list():
    rec = Recorder({"id": "abc"})
    run(rec.client().put_item_collections("abc", ["c1", "c2"]))
    assert rec.requests == [("PUT", "/object/item-collections/abc", ["c1", "c2"])]


def test_move_to_organization_posts_the_collection_ids():
    rec = Recorder({"id": "abc"})
    run(rec.client().move_to_organization("abc", "org1", ["c1"]))
    assert rec.requests == [("POST", "/move/abc/org1", ["c1"])]


def test_create_folder_posts_the_name():
    rec = Recorder({"object": "folder", "id": "f9", "name": "New"})
    assert run(rec.client().create_folder("New")) == {"object": "folder", "id": "f9", "name": "New"}
    assert rec.requests == [("POST", "/object/folder", {"name": "New"})]


def test_edit_folder_puts_the_new_name():
    rec = Recorder({"object": "folder", "id": "f 9", "name": "Renamed"})
    assert run(rec.client().edit_folder("f 9", "Renamed"))["name"] == "Renamed"
    assert rec.requests == [("PUT", "/object/folder/f%209", {"name": "Renamed"})]


def test_delete_folder_deletes_it_by_id():
    rec = Recorder(None)
    run(rec.client().delete_folder("f9"))
    assert rec.requests == [("DELETE", "/object/folder/f9", None)]


def test_get_attachment_returns_the_raw_bytes():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.raw_path.decode()))
        return httpx.Response(
            200, content=b"file bytes", headers={"content-type": "application/octet-stream"}
        )

    assert run(mock_client(handler).get_attachment("att1", "item1")) == b"file bytes"
    assert seen == [("GET", "/object/attachment/att1?itemid=item1")]


def test_get_attachment_error_carries_the_message():
    client = failing(httpx.Response(400, json={"success": False, "message": "Not found."}))
    with pytest.raises(BwError, match="Not found."):
        run(client.get_attachment("att1", "item1"))


def test_upload_attachment_is_multipart_in_the_file_field():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (request.url.raw_path.decode(), request.headers["content-type"], request.content)
        )
        return httpx.Response(200, json={"success": True, "data": {"id": "item1"}})

    assert run(mock_client(handler).upload_attachment("item1", "codes.txt", b"123")) == {
        "id": "item1"
    }
    path, content_type, content = seen[0]
    assert path == "/attachment?itemid=item1"
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="file"; filename="codes.txt"' in content
    assert b"\r\n123\r\n" in content


def test_delete_attachment_deletes_it_by_id_of_its_item():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.raw_path.decode()))
        ## what bw serve 2026.10.0 answers, without a data key
        return httpx.Response(200, json={"success": True})

    run(mock_client(handler).delete_attachment("att 1", "item1"))
    assert seen == [("DELETE", "/object/attachment/att%201?itemid=item1")]


def test_delete_attachment_error_carries_the_message():
    message = "Attachment `nope` was not found."
    client = failing(httpx.Response(400, json={"success": False, "message": message}))
    with pytest.raises(BwError, match="was not found"):
        run(client.delete_attachment("nope", "item1"))
