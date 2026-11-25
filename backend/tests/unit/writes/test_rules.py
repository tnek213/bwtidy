"""The secret rules (writes/rules.py) through POST /api/operations: no plain value equal to a
secret of the item, in batch order and against a folder name, a hidden value only into a hidden
field, and the rules run again on the item right before its write."""

import httpx2 as httpx
import pytest

from tests.support import fake_bw
from tests.support.app_env import AppEnv
from tests.support.quiet import assert_absent
from tests.support.sample_vault import LOGIN
from tests.support.writes_helpers import PIN_SECRET, create, raw_sqlite_text, uris_op


def test_secret_into_non_secret_is_refused_before_the_backup(env: AppEnv):
    password = LOGIN["login"]["password"]
    for op in [
        env.set_leaf("login-1", "notes", password),
        env.set_leaf("login-1", "fields.User", password),
        env.set_leaf("login-1", "fields.Copy", password),
        env.set_leaf("login-1", "fields.Copy", password, fieldType=0),
        env.set_leaf("login-1", "login.uris", ["https://a.test", password]),
        env.set_leaf("login-1", "name", "pin-secret-4711"),
    ]:
        response = env.post(op)
        assert response.status_code == 422, op["column"]
        detail = response.json()["detail"]
        assert "secret" in detail
        assert password not in detail
        assert "pin-secret-4711" not in detail
    assert env.bw.put == []
    assert env.backups == [env.login_backup]
    assert env.backup_files() == [env.login_backup]
    ## the same value into a hidden field is fine
    response = env.post(env.set_leaf("login-1", "fields.Copy", password, fieldType=1))
    assert response.status_code == 200
    assert env.bw.put[0][1]["fields"][-1]["type"] == 1


SAME = "same-value-4242"


def names_both(detail: str, plain: str, secret: str) -> bool:
    return f"non-secret column {plain} " in detail and f"secret column {secret} " in detail


@pytest.mark.parametrize("order", ["username first", "password first"])
def test_one_batch_never_makes_the_username_equal_the_password(env: AppEnv, order: str):
    username = env.set_leaf("login-1", "login.username", SAME)
    password = env.set_leaf("login-1", "login.password", SAME)
    ops = [username, password] if order == "username first" else [password, username]
    response = env.post(*ops)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert names_both(detail, "login.username", "login.password")
    assert detail.startswith("Operation 2 ")
    leaked = SAME in response.text
    assert not leaked
    assert env.bw.put == []
    assert env.backups == [env.login_backup]


@pytest.mark.parametrize(
    ("column", "field_type", "plain"),
    [
        ("login.password", None, "fields.User"),
        ("fields.PIN", None, "name"),
        ("fields.New", 1, "login.uris"),
        ("login.totp", None, "folder"),
    ],
)
def test_a_secret_value_equal_to_a_plain_one_is_refused(
    env: AppEnv, column: str, field_type: int | None, plain: str
):
    values = {
        "fields.User": "dev",
        "name": LOGIN["name"],
        "login.uris": LOGIN["login"]["uris"][1]["uri"],
        "folder": "Work",
    }
    extra = {} if field_type is None else {"fieldType": field_type}
    response = env.post(env.set_leaf("login-1", column, values[plain], **extra))
    assert response.status_code == 422
    assert names_both(response.json()["detail"], plain, column)
    assert env.bw.put == []


def test_a_generated_value_equal_to_a_plain_one_is_not_written(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(fake_bw, "generated_password", lambda serial, length: SAME)
    assert env.post(env.set_leaf("login-1", "fields.User", SAME)).status_code == 200
    puts = len(env.bw.put)
    response = env.post(env.op("generateLeaf", "login-1", column="login.password"))
    assert response.status_code == 500
    assert names_both(response.json()["detail"], "fields.User", "login.password")
    leaked = SAME in response.text
    assert not leaked
    assert len(env.bw.put) == puts
    assert env.item("login-1")["login"]["password"] == LOGIN["login"]["password"]
    created = len(env.bw.created)
    response = env.post(create(type=1, name="x", values={"notes": SAME}, generatePassword=True))
    assert response.status_code == 500
    assert names_both(response.json()["detail"], "notes", "login.password")
    assert len(env.bw.created) == created


def test_a_folder_named_like_a_secret_of_an_item_is_refused(env: AppEnv):
    folders = len(env.bw.folders)
    response = env.post(
        {
            "kind": "moveToFolder",
            "itemIds": ["card-1", "login-1"],
            "revisionDates": {i: env.revisions()[i] for i in ("card-1", "login-1")},
            "folderName": "pin-secret-4711",
        }
    )
    assert response.status_code == 422
    assert names_both(response.json()["detail"], "folder", "fields.PIN")
    assert_absent("pin-secret-4711", response.text, "the response")
    assert env.bw.put == []
    assert len(env.bw.folders) == folders


def test_an_existing_collision_stays_and_another_write_of_the_value_is_refused(env: AppEnv):
    login = env.item("login-1")["login"]
    login["username"] = login["password"] = SAME
    env.reload()
    assert env.post(env.set_leaf("login-1", "notes", "a note")).status_code == 200
    assert env.post(env.set_leaf("login-1", "login.username", SAME)).status_code == 200
    response = env.post(env.set_leaf("login-1", "fields.User", SAME))
    assert response.status_code == 422
    assert names_both(response.json()["detail"], "fields.User", "login.password")


@pytest.mark.parametrize(("field_type", "value"), [(0, "typed-plain-value"), (2, "true")])
def test_a_hidden_value_for_a_field_of_another_type_is_refused(
    env: AppEnv, field_type: int, value: str
):
    card = env.item("card-1")
    card["fields"] = [
        *(card.get("fields") or []),
        {"name": "Other", "value": None, "type": field_type},
    ]
    env.reload()
    response = env.post(env.set_leaf("card-1", "fields.Other", value, fieldType=1))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "fields.Other is not a hidden field" in detail
    assert value not in detail
    assert env.bw.put == []


TYPED_SECRET = "typed-secret-77"


def test_a_hidden_value_after_a_text_field_of_the_same_batch_is_refused_before_any_write(
    env: AppEnv,
):
    response = env.post(
        env.set_leaf("login-1", "fields.Code", "plain-start", fieldType=0),
        env.set_leaf("login-1", "fields.Code", TYPED_SECRET, fieldType=1),
    )
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Operation 2 (setLeaf): fields.Code is not a hidden field on this item, "
        "a hidden value cannot go there"
    )
    assert env.bw.put == []


def test_a_secret_removed_earlier_in_the_batch_still_stays_out_of_plain_columns(env: AppEnv):
    response = env.post(
        env.op("removeLeaf", "login-1", column="fields.PIN"),
        env.set_leaf("login-1", "notes", PIN_SECRET),
    )
    assert response.status_code == 422
    assert "may not be moved into the non-secret column notes" in response.json()["detail"]
    assert env.bw.put == []


def test_a_secret_removed_earlier_in_the_batch_stays_out_of_a_folder_name(env: AppEnv):
    folders = len(env.bw.folders)
    response = env.post(
        env.op("removeLeaf", "login-1", column="fields.PIN"),
        {
            "kind": "moveToFolder",
            "itemIds": ["login-1"],
            "revisionDates": {"login-1": env.revisions()["login-1"]},
            "folderName": PIN_SECRET,
        },
    )
    assert response.status_code == 422
    assert names_both(response.json()["detail"], "folder", "fields.PIN")
    leaked = PIN_SECRET in response.text
    assert not leaked
    assert env.bw.put == []
    assert len(env.bw.folders) == folders


def a_text_field_only_the_write_sees(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch, item_id: str, name: str
) -> None:
    """bw serve shows the field only to the write's own GET, as if it came after the check."""
    real = env.bw.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == ("GET", f"/object/item/{item_id}"):
            fields = env.item(item_id)["fields"]
            if all(f["name"] != name for f in fields):
                fields.append({"name": name, "value": "plain-start", "type": 0, "linkedId": None})
        return real(request)

    monkeypatch.setattr(env.bw, "handler", handler)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (TYPED_SECRET, "fields.Code is not a hidden field on this item"),
        (
            PIN_SECRET,
            "a secret value may not be moved into the non-secret column fields.Code",
        ),
    ],
    ids=["typed value", "value of a secret of the item"],
)
def test_the_hidden_field_rules_run_again_on_the_item_at_write_time(
    env: AppEnv,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    value: str,
    reason: str,
):
    caplog.set_level("DEBUG")
    a_text_field_only_the_write_sees(env, monkeypatch, "login-1", "Code")
    response = env.post(env.set_leaf("login-1", "fields.Code", value, fieldType=1))
    assert response.status_code == 500
    body = response.json()
    assert (body["applied"], body["failed"], body["pending"]) == ([], 0, [])
    assert reason in body["detail"]
    assert env.bw.put == []
    leaked = value in response.text or value in caplog.text or value in raw_sqlite_text(env)
    assert not leaked


def test_a_hidden_value_still_goes_into_a_hidden_field_and_a_new_one(env: AppEnv):
    response = env.post(
        env.set_leaf("login-1", "fields.PIN", "new-pin-value", fieldType=1),
        env.set_leaf("login-1", "fields.Brand new", "new-field-value", fieldType=1),
    )
    assert response.status_code == 200
    types = {f["name"]: f["type"] for f in env.bw.put[-1][1]["fields"]}
    assert (types["PIN"], types["Brand new"]) == (1, 1)


def test_set_uris_never_makes_a_uri_equal_a_secret(env: AppEnv):
    response = env.post(uris_op(env, ("pw-login-secret-e0e7", None)))
    assert response.status_code == 422
    assert "login.uris" in response.text
    leaked = "pw-login-secret-e0e7" in response.text
    assert not leaked
    assert env.bw.put == []
