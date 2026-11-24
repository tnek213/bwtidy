"""Normalizing on the seeded dev vault through a real bw serve: merge columns, rename a field.

Every item touched is put back at the end of the module (PUT of the original), so the other
integration tests see the seeded vault again. Nothing here prints an item or a value.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from support.app import App
from support.cli import BwCli
from support.vault import Vault, attachment_ids, same_secret

from bwtidy.bw.client import BwError

pytestmark = pytest.mark.integration

SYNC_TRIES = 15


@pytest.fixture(scope="module")
def vault(app: App, bw_cli: BwCli) -> Iterator[Vault]:
    assert app.login().status_code == 200
    app.settle()
    vault = Vault(app, bw_cli)
    yield vault
    assert app.client.portal is not None
    vault.beat()
    for undo in reversed(vault.undo):
        app.client.portal.call(undo)
    app.client.portal.call(vault.client.sync)
    app.client.portal.call(app.session.cache.load)


async def sync_until_out_of_the_trash(vault: Vault, *item_ids: str) -> None:
    """Sync bw serve until it has the items again and none is in the trash.

    The CLI's restore does not reach bw serve's copy by itself, and right after a sync bw serve
    sometimes still answers "Not found" for an item. Without this the undo_put that follows
    is refused ("You may not edit a deleted item").
    """
    for _ in range(SYNC_TRIES):
        await vault.client.sync()
        try:
            items = [await vault.client.get_item(item_id) for item_id in item_ids]
        except BwError:
            items = []
        if len(items) == len(item_ids) and not any(item.get("deletedDate") for item in items):
            return
        await asyncio.sleep(1)
    raise AssertionError("bw serve still has an item in the trash or not at all after the sync")


def with_field(vault: Vault, name: str) -> list[dict[str, Any]]:
    return [i for i in vault.items() if any(f["name"] == name for f in i.get("fields") or [])]


def listed(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"itemId": i["id"], "revisionDate": i["revisionDate"]} for i in items]


def grid(vault: Vault) -> tuple[set[str], dict[str, dict[str, Any]]]:
    body = vault.app.client.get("/api/grid").json()
    return {c["id"] for c in body["columns"]}, {r["id"]: r["cells"] for r in body["rows"]}


def field_value(item: dict[str, Any], name: str) -> str:
    [value] = [f["value"] for f in item["fields"] if f["name"] == name]
    return value


def test_near_duplicates_name_the_seeded_spellings(vault: Vault):
    vault.beat()
    response = vault.app.client.get("/api/near-duplicates")
    assert response.status_code == 200
    groups = {g["key"]: g["columns"] for g in response.json()}
    assert groups["user"] == ["fields.User", "fields.user"]
    assert groups["password"] == ["fields.Password", "fields.password"]


def test_merge_user_columns_and_rename_password(vault: Vault):
    users = with_field(vault, "User") + with_field(vault, "user")
    passwords = with_field(vault, "Password")
    assert {i["name"] for i in users} == {"ThinkPad X1", "Youth laptop", "Media PC"}
    assert len(passwords) == 5
    originals = {i["id"]: vault.raw(i["id"]) for i in users + passwords}
    for original in originals.values():
        vault.undo_put(original)
    before = vault.backups()

    response = vault.post(
        {
            "kind": "mergeColumns",
            "sources": ["fields.User", "fields.user"],
            "target": "fields.user",
            "items": listed(users),
            "resolutions": {},
        },
        {"kind": "renameField", "from": "Password", "to": "password", "items": listed(passwords)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] == 2
    ## the newest backup from before held the vault as it was, and one more followed the write
    assert body["backup"] == before[0]
    assert len(vault.backups()) == len(before) + 1

    for item_id, original in originals.items():
        after = vault.raw_after(item_id, original["revisionDate"])
        names = [f["name"] for f in after["fields"]]
        assert "User" not in names
        assert "Password" not in names
        old = [f["name"] for f in original["fields"]]
        assert names == [{"User": "user", "Password": "password"}.get(n, n) for n in old]
        assert [f["type"] for f in after["fields"]] == [f["type"] for f in original["fields"]]
        assert attachment_ids(after) == attachment_ids(original)
        if item_id in {i["id"] for i in users}:
            user = next(f["value"] for f in original["fields"] if f["name"] in ("User", "user"))
            assert field_value(after, "user") == user
        else:
            secret = field_value(original, "Password")
            password_kept = same_secret(field_value(after, "password"), secret)
            assert password_kept, "the password field lost the value"
            ## bw keeps a renamed hidden field's old name and value in the password history
            [record, *_] = after["passwordHistory"]
            record_kept = same_secret(record["password"], f"Password: {secret}")
            assert record_kept, "the password history lost the old field"

    columns, rows = grid(vault)
    assert "fields.User" not in columns
    assert "fields.Password" not in columns
    assert {"fields.user", "fields.password"} <= columns
    for item in users:
        assert rows[item["id"]]["fields.user"]
    for item in passwords:
        assert rows[item["id"]]["fields.password"] == {"masked": True}
    groups = {g["key"] for g in vault.app.client.get("/api/near-duplicates").json()}
    assert "user" not in groups
    assert "password" not in groups
    log = vault.change_log()
    assert [e["operationKind"] for e in log[:2]] == ["renameField", "mergeColumns"]


def test_merging_into_login_username_of_a_note_is_refused(vault: Vault):
    note = vault.by_name("Switch admin")
    backups = vault.backups()
    response = vault.post(
        {
            "kind": "mergeColumns",
            "sources": ["fields.username"],
            "target": "login.username",
            "items": listed([note]),
        }
    )
    assert response.status_code == 422
    assert "template conversion" in response.json()["detail"]
    assert vault.backups() == backups


def uris(item: dict[str, Any]) -> list[tuple[str, int | None]]:
    return [(u["uri"], u.get("match")) for u in item["login"]["uris"]]


def test_merge_items_with_append_keeps_both_lists(vault: Vault):
    target = vault.raw(vault.by_name("Wiki")["id"])
    source = vault.raw(vault.by_name("Payroll")["id"])
    vault.undo_put(target)
    vault.undo_put(source)
    wiki, docs = (u["uri"] for u in target["login"]["uris"])
    payroll = source["login"]["uris"][0]["uri"]
    ## match settings on both sides, and one uri the target has already
    setup = {
        target["id"]: [{"match": None, "uri": wiki}, {"match": 3, "uri": docs}],
        source["id"]: [{"match": 1, "uri": docs}, {"match": 2, "uri": payroll}],
    }
    for item in (target, source):
        body = {**item, "login": {**item["login"], "uris": setup[item["id"]]}}
        vault.call(vault.client.put_item, item["id"], body)
    vault.call(vault.client.sync)
    vault.call(vault.app.session.cache.load)

    async def restore() -> None:
        vault.cli_run("restore", "item", source["id"])
        await sync_until_out_of_the_trash(vault, source["id"], target["id"])

    vault.undo.append(restore)
    target, source = vault.by_name("Wiki"), vault.by_name("Payroll")
    response = vault.post(
        {
            "kind": "mergeItems",
            "fromItemId": source["id"],
            "fromRevisionDate": source["revisionDate"],
            "toItemId": target["id"],
            "toRevisionDate": target["revisionDate"],
            "mapping": {"login.uris": "login.uris"},
            "append": ["login.uris"],
        }
    )
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 1

    assert uris(vault.raw_after(target["id"], target["revisionDate"])) == [
        (wiki, None),
        (docs, 3),
        (payroll, 2),
    ]
    assert vault.raw_after(source["id"], source["revisionDate"])["deletedDate"]
    _, rows = grid(vault)
    assert rows[target["id"]]["login.uris"] == [wiki, docs, payroll]
    assert source["id"] not in rows
    assert vault.change_log()[0]["operationKind"] == "mergeItems"
