import contextlib
import copy
import json
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.support import app_env
from tests.support.app_env import AppEnv
from tests.support.fake_bw import PASSWORD, FakeBwServe
from tests.support.normalize_data import EXTRA
from tests.support.sample_vault import COLLECTIONS, FOLDERS, ITEMS, ORGANIZATIONS, SECRETS


@contextlib.contextmanager
def app_with(
    data_dir: Path,
    items: list[dict],
    organizations: list[dict] = ORGANIZATIONS,
    collections: list[dict] = COLLECTIONS,
) -> Generator[TestClient]:
    bw = FakeBwServe(items)
    bw.folders = FOLDERS
    bw.organizations = organizations
    bw.collections = collections
    ## the grid reads the queue (the generator settings a save holds), so it needs a data directory
    with AppEnv(data_dir, bw=bw, fake_backups=False) as env:
        yield env.client


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with app_with(tmp_path, copy.deepcopy(ITEMS)) as client:
        yield client


@pytest.fixture
def logged_in(client: TestClient) -> TestClient:
    assert client.post("/api/session", json={"password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def env(tmp_path: Path) -> Iterator[AppEnv]:
    """The app logged in on the vault with normalize_data's EXTRA items."""
    yield from app_env.logged_in(tmp_path, extra=EXTRA)


def secret(client: TestClient, item_id: str, column: str):
    return client.get(f"/api/items/{item_id}/secret", params={"column": column})


def test_grid_needs_a_session(client: TestClient):
    assert client.get("/api/grid").status_code == 401
    assert secret(client, "login-1", "login.password").status_code == 401


def test_the_secret_answer_is_not_cached(logged_in: TestClient):
    response = secret(logged_in, "login-1", "login.password")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


def test_no_secret_value_in_the_grid(logged_in: TestClient):
    response = logged_in.get("/api/grid")
    assert response.status_code == 200
    dump = response.text
    ## the tool note holds the master password as backup-password
    leaked = [i for i, s in enumerate([*SECRETS, PASSWORD]) if s in dump or json.dumps(s) in dump]
    ## indexes only, so a failure never prints a secret
    assert leaked == []


def test_grid_organizations_hold_those_no_item_uses(tmp_path: Path):
    organizations = [*ORGANIZATIONS, {"object": "organization", "id": "o2", "name": "Book club"}]
    collections = [
        *COLLECTIONS,
        {"object": "collection", "id": "c3", "organizationId": "o1", "name": "empty"},
        {"object": "collection", "id": "c4", "organizationId": "o2", "name": "Novels"},
    ]
    with app_with(tmp_path, copy.deepcopy(ITEMS), organizations, collections) as client:
        assert client.post("/api/session", json={"password": PASSWORD}).status_code == 200
        listed = client.get("/api/grid").json()["organizations"]
    assert listed == [
        {"name": "Family", "collections": ["empty", "shared", "Youth"]},
        {"name": "Book club", "collections": ["Novels"]},
    ]


def test_grid_columns(logged_in: TestClient):
    columns = logged_in.get("/api/grid").json()["columns"]
    ids = [c["id"] for c in columns]
    assert ids[:11] == [
        "name",
        "type",
        "folder",
        "organization",
        "collections",
        "notes",
        "favorite",
        "reprompt",
        "attachments",
        "passkeys",
        "revisionDate",
    ]
    assert ids[11:15] == ["login.username", "login.password", "login.totp", "login.uris"]
    assert ids.index("card.code") < ids.index("identity.title") < ids.index("sshKey.privateKey")
    fields = [i for i in ids if i.startswith("fields.")]
    assert fields == [
        "fields.backup-password",
        "fields.dm.crypt.v2",
        "fields.Empty#2",
        "fields.Enabled",
        "fields.Encryption passphrase ",
        "fields.PIN",
        "fields.User",
        "fields.WPA key",
        "fields.WPA key#2",
    ]
    assert ids[-len(fields) :] == fields
    by_id = {c["id"]: c for c in columns}
    assert by_id["login.username"] == {
        "id": "login.username",
        "label": "login.username",
        "group": "login",
        "secret": False,
        "multi": False,
        "readOnly": False,
    }
    assert by_id["login.uris"]["multi"]
    assert by_id["collections"]["multi"]
    assert by_id["collections"]["group"] == "item"
    assert by_id["revisionDate"]["readOnly"]
    assert by_id["passkeys"]["readOnly"]
    assert by_id["fields.WPA key#2"]["secret"]
    assert by_id["fields.WPA key#2"]["group"] == "fields"
    assert not by_id["fields.dm.crypt.v2"]["secret"]
    assert by_id["card.number"]["secret"]
    assert "fields.Linked" not in by_id
    assert "identity.middleName" not in by_id


def test_favorite_and_reprompt_columns_without_any_item_set(tmp_path: Path):
    items = copy.deepcopy(ITEMS)
    for item in items:
        item["favorite"] = False
        item["reprompt"] = 0
    with app_with(tmp_path, items) as client:
        assert client.post("/api/session", json={"password": PASSWORD}).status_code == 200
        grid = client.get("/api/grid").json()
    by_id = {c["id"]: c for c in grid["columns"]}
    for name in ("favorite", "reprompt"):
        assert by_id[name] == {
            "id": name,
            "label": name,
            "group": "item",
            "secret": False,
            "multi": False,
            "readOnly": False,
        }
        assert all(name not in row["cells"] for row in grid["rows"])


def test_grid_rows(logged_in: TestClient):
    rows = {r["id"]: r for r in logged_in.get("/api/grid").json()["rows"]}
    login = rows["login-1"]
    assert login["revisionDate"] == "2026-10-26T10:00:00.000Z"
    assert login["cells"]["login.password"] == {"masked": True}
    assert login["cells"]["fields.PIN"] == {"masked": True}
    assert login["cells"]["fields.User"] == "dev"
    assert login["cells"]["login.uris"] == [
        "https://mail.example.test",
        "https://webmail.example.test",
    ]
    note = rows["note-1"]
    assert note["cells"]["collections"] == ["shared", "Youth"]
    assert note["cells"]["fields.WPA key#2"] == {"masked": True}
    assert "fields.Empty" not in note["cells"]
    assert {r["cells"]["type"] for r in rows.values()} == {
        "login",
        "note",
        "card",
        "identity",
        "sshKey",
    }
    ## the tool note is an ordinary row
    [tool] = [r for r in rows.values() if r["cells"]["name"] == "bwtidy"]
    assert tool["cells"]["fields.backup-password"] == {"masked": True}


def test_secret_route_returns_one_value(logged_in: TestClient):
    response = secret(logged_in, "login-1", "login.password")
    assert response.status_code == 200
    assert response.json() == {"value": "pw-login-secret-e0e7"}
    assert secret(logged_in, "note-1", "fields.WPA key#2").json() == {"value": "wpa-guest-secret"}
    assert secret(logged_in, "note-1", "fields.Encryption passphrase ").json() == {
        "value": "luks-secret-9f8e"
    }
    assert secret(logged_in, "card-1", "card.code").json() == {"value": "9871"}


@pytest.mark.parametrize(
    ("item_id", "column"),
    [
        ("login-1", "fields.Nope"),
        ("card-1", "login.password"),
        ("identity-1", "sshKey.privateKey"),
        ("unknown", "login.password"),
        ("login-1", "not.a.column"),
        ("login-1", "fields.User"),
        ("login-1", "name"),
    ],
)
def test_secret_route_404(logged_in: TestClient, item_id: str, column: str):
    response = secret(logged_in, item_id, column)
    assert response.status_code == 404
    assert "value" not in response.json()


def test_grid_lists_the_vault_fields_with_their_types(logged_in: TestClient):
    fields = {f["name"]: f["fieldType"] for f in logged_in.get("/api/grid").json()["fields"]}
    assert fields["User"] == 0
    assert fields["PIN"] == 1
    assert fields["Enabled"] == 2
    assert fields["Encryption passphrase "] == 1
    assert "Linked" not in fields
    assert "backup-password" not in fields


def test_a_row_names_its_text_fields_without_a_value(tmp_path: Path):
    items = copy.deepcopy(ITEMS)
    extra = [
        {"name": "Blank", "value": "", "type": 0},
        {"name": "Nothing", "value": None, "type": 0},
        {"name": "Nothing", "value": None, "type": 0},
        {"name": "Filled", "value": "a", "type": 0},
        {"name": "Empty hidden", "value": None, "type": 1},
        {"name": "Empty boolean", "value": None, "type": 2},
    ]
    items[0]["fields"] = [*items[0]["fields"], *extra]
    with app_with(tmp_path, items) as client:
        assert client.post("/api/session", json={"password": PASSWORD}).status_code == 200
        rows = {row["id"]: row for row in client.get("/api/grid").json()["rows"]}
    filled_id = items[0]["id"]
    assert rows[filled_id]["emptyTextFields"] == [
        "fields.Blank",
        "fields.Nothing",
        "fields.Nothing#2",
    ]
    assert "emptyTextFields" not in rows["card-1"]


def test_a_text_field_in_a_secret_column_is_plain_and_has_no_secret(tmp_path: Path):
    items = copy.deepcopy(ITEMS)
    card = next(item for item in items if item["id"] == "card-1")
    text_pin = {"name": "PIN", "value": "plain-pin", "type": 0, "linkedId": None}
    card["fields"] = [*(card.get("fields") or []), text_pin]
    with app_with(tmp_path, items) as client:
        assert client.post("/api/session", json={"password": PASSWORD}).status_code == 200
        grid = client.get("/api/grid").json()
        rows = {row["id"]: row for row in grid["rows"]}
        columns = {column["id"]: column for column in grid["columns"]}
        assert columns["fields.PIN"]["secret"] is True
        assert rows["login-1"]["cells"]["fields.PIN"] == {"masked": True}
        plain = rows["card-1"]["cells"]["fields.PIN"] == "plain-pin"
        assert plain
        response = secret(client, "card-1", "fields.PIN")
        assert response.status_code == 404
        assert "value" not in response.json()


def test_a_row_names_its_boolean_and_linked_fields_with_or_without_a_value(tmp_path: Path):
    items = copy.deepcopy(ITEMS)
    items[0]["fields"] = [
        {"name": "Enabled", "value": "true", "type": 2},
        {"name": "Off", "value": "false", "type": 2},
        {"name": "Empty boolean", "value": None, "type": 2},
        {"name": "Linked", "value": None, "type": 3, "linkedId": 100},
        {"name": "Text", "value": "a", "type": 0},
        {"name": "Hidden", "value": "s3cret", "type": 1},
    ]
    with app_with(tmp_path, items) as client:
        assert client.post("/api/session", json={"password": PASSWORD}).status_code == 200
        rows = {row["id"]: row for row in client.get("/api/grid").json()["rows"]}
    row = rows[items[0]["id"]]
    assert row["booleanFields"] == ["fields.Enabled", "fields.Off", "fields.Empty boolean"]
    assert row["linkedFields"] == ["fields.Linked"]
    assert "booleanFields" not in rows["card-1"]
    assert "linkedFields" not in rows["card-1"]


def test_grid_field_type_where_items_disagree(tmp_path: Path):
    items = copy.deepcopy(ITEMS)
    extra = [
        {"name": "Mixed", "value": "a", "type": 0},
        {"name": "Mixed", "value": "b", "type": 2},
        {"name": "Mixed", "value": "c", "type": 2},
        {"name": "Either", "value": "a", "type": 0},
        {"name": "Either", "value": "b", "type": 2},
        {"name": "Hides", "value": "a", "type": 0},
        {"name": "Hides", "value": "b", "type": 0},
        {"name": "Hides", "value": "c", "type": 1},
        {"name": "Blank", "value": "", "type": 0},
    ]
    items[0]["fields"] = [*items[0]["fields"], *extra]
    with app_with(tmp_path, items) as client:
        assert client.post("/api/session", json={"password": PASSWORD}).status_code == 200
        grid = client.get("/api/grid").json()
    fields = {f["name"]: f["fieldType"] for f in grid["fields"]}
    assert (fields["Mixed"], fields["Either"], fields["Hides"]) == (2, 0, 1)
    ## a field with no value in any item has no column but is still a field of the vault
    assert fields["Blank"] == 0
    assert "fields.Blank" not in {c["id"] for c in grid["columns"]}


def test_a_row_names_its_attachments_by_id_and_file_name_only(tmp_path: Path):
    items = copy.deepcopy(ITEMS)
    login = next(item for item in items if item["id"] == "login-1")
    login["attachments"] = [
        {
            "id": "att-b",
            "fileName": "b.txt",
            "size": "12",
            "sizeName": "12 Bytes",
            "url": "https://t?token=x",
        },
        {
            "id": "att-a",
            "fileName": "a.txt",
            "size": "3",
            "sizeName": "3 Bytes",
            "url": "https://t?token=y",
        },
    ]
    with app_with(tmp_path, items) as client:
        assert client.post("/api/session", json={"password": PASSWORD}).status_code == 200
        rows = {row["id"]: row for row in client.get("/api/grid").json()["rows"]}
    assert rows["login-1"]["attachments"] == [
        {"id": "att-b", "fileName": "b.txt"},
        {"id": "att-a", "fileName": "a.txt"},
    ]
    assert "attachments" not in rows["card-1"]


def test_near_duplicates_route(env: AppEnv):
    response = env.client.get("/api/near-duplicates")
    assert response.status_code == 200
    assert response.json() == [
        {
            "key": "encryption passphrase",
            "columns": ["fields.encryption passphrase", "fields.Encryption passphrase "],
        },
        {"key": "password", "columns": ["fields.Password", "fields.password"]},
        {"key": "user", "columns": ["fields.User", "fields.user"]},
    ]
    env.client.cookies.clear()
    assert env.client.get("/api/near-duplicates").status_code == 401
