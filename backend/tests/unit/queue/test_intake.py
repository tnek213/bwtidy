"""What the queue takes in, against the fake bw serve: the checks on arrival, a UI batch stored
in one transaction, secret values stored only encrypted, the silent refusals and equal values."""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import pyrage
import pytest

from bwtidy.items.coordinates import MASKED
from bwtidy.operations import Conflict, InvalidOperation, MergeConflicts, Operation
from bwtidy.protocol import DB_FILE
from bwtidy.queue.intake import VaultLocked
from bwtidy.queue.state import as_operation
from bwtidy.vault.hidden import HiddenValueError, HiddenValues
from tests.support.app_env import AppEnv
from tests.support.normalize_data import ALL_SECRETS, listed, merge, rename
from tests.support.queue_helpers import (
    CHANGED,
    COMPUTER,
    HIDDEN_VALUE,
    LOGIN_USERNAME,
    approve,
    commit_now,
    enqueue_dict,
    enqueue_ui,
    entry,
    hidden_setlers,
    listing,
    raw_sqlite,
    status,
)
from tests.support.quiet import assert_absent, assert_present
from tests.support.section_changes import add_template, changes
from tests.support.writes_helpers import PIN_SECRET, raw_sqlite_text, store_templates


def test_enqueue_checks_the_shape_and_the_revision(env: AppEnv):
    with pytest.raises(InvalidOperation):
        enqueue_dict(env, {"kind": "setLeaf", "itemId": "login-1"}, "broken")
    with pytest.raises(InvalidOperation, match="no field named"):
        enqueue_dict(env, rename(env, "Nope", "nope", "login-1"), "rename a missing field")
    stale = {**env.op("trashItem", "card-1"), "revisionDate": "2026-01-01T00:00:00.000Z"}
    with pytest.raises(Conflict):
        enqueue_dict(env, stale, "trash the card")
    with pytest.raises(MergeConflicts):
        enqueue_dict(env, rename(env, "Password", "password", "note-4"), "merge spellings")
    assert listing(env) == []


def test_a_locked_vault_answers_401_with_its_text(env: AppEnv, monkeypatch: pytest.MonkeyPatch):
    def locked(*args: Any) -> int:
        raise VaultLocked("the vault is locked")

    monkeypatch.setattr(env.queue, "enqueue_ui", locked)
    monkeypatch.setattr(env.queue, "enqueue_section_changes", locked)
    good = env.set_leaf("login-1", "name", "Mail")
    response = enqueue_ui(env, good)
    assert (response.status_code, response.json()) == (401, {"detail": "the vault is locked"})
    stored = env.client.get("/api/templates").json()
    body = {**changes(add_template(COMPUTER)), "revisionDate": stored["revisionDate"]}
    response = env.client.put("/api/templates", json=body)
    assert (response.status_code, response.json()) == (401, {"detail": "the vault is locked"})


def test_tool_note_ops_are_rejected(env: AppEnv):
    note_id = env.tool_note_id
    with pytest.raises(InvalidOperation, match="bwtidy note"):
        enqueue_dict(env, env.set_leaf(note_id, "name", "renamed"), "rename the note")
    with pytest.raises(InvalidOperation, match="bwtidy note"):
        enqueue_dict(env, env.op("trashItem", note_id), "trash the note")
    with pytest.raises(InvalidOperation, match="bwtidy note"):
        enqueue_dict(
            env, rename(env, "backup-password", "pw", note_id, "login-1"), "rename everywhere"
        )
    assert listing(env) == []


def test_secret_set_leaf_from_mcp_is_rejected(env: AppEnv):
    for op in (
        env.set_leaf("login-1", "login.password", "guessed-secret-1"),
        env.set_leaf("login-1", "fields.PIN", "guessed-secret-1"),
        env.set_leaf("login-1", "fields.New", "guessed-secret-1", fieldType=1),
    ):
        with pytest.raises(InvalidOperation, match="secret column") as raised:
            enqueue_dict(env, op, "set a secret")
        assert_absent("guessed-secret-1", str(raised.value), "the error message")
    assert listing(env) == []


def test_a_secret_into_name_from_mcp_is_stored_rejected(env: AppEnv, tmp_path: Path):
    name = env.item("login-1")["name"]
    op = env.set_leaf("login-1", "name", "pw-login-secret-e0e7")
    entry_id = enqueue_dict(env, op, 'Rename to "pw-login-secret-e0e7". Reason: tidier')
    stored = entry(env, entry_id)
    assert stored["status"] == "rejected"
    assert stored["decidedAt"] is not None
    assert stored["source"] == "mcp"
    assert stored["summary"] == (
        f'Refused proposal for "{name}": it would put a secret value of the item into name'
    )
    assert stored["operation"] == {**op, "value": MASKED, "fieldType": None, "shownFavorite": None}
    assert stored["diff"] == [
        {"itemId": "login-1", "itemName": name, "column": "name", "before": name, "after": MASKED}
    ]
    assert stored["result"] == {
        "error": "secretValue",
        "detail": "the proposal would put a secret value of the item into a non-secret column",
    }
    assert listing(env, "pending") == []
    assert_absent("tidier", json.dumps(stored), "the stored entry")
    raw = raw_sqlite(tmp_path)
    for secret in ALL_SECRETS:
        assert_absent(secret.encode(), raw, "the stored bytes")
    assert_absent(b"tidier", raw, "the stored bytes")


def test_a_secret_into_name_from_the_ui_still_raises(env: AppEnv):
    op = env.set_leaf("login-1", "name", "pw-login-secret-e0e7")
    with pytest.raises(InvalidOperation, match="non-secret column") as raised:
        enqueue_dict(env, op, "rename", source="ui")
    assert raised.value.secret_rule
    assert listing(env) == []


def test_a_name_that_is_no_secret_is_queued_pending(env: AppEnv):
    entry_id = enqueue_dict(env, env.set_leaf("login-1", "name", "Mail"), "rename")
    stored = entry(env, entry_id)
    assert stored["status"] == "pending"
    assert stored["operation"]["value"] == "Mail"
    assert stored["result"] is None


def test_the_sqlite_file_holds_no_secret_value(env: AppEnv, tmp_path: Path):
    """A run through every path that stores something, then a search of the raw file."""
    moved = enqueue_dict(
        env, merge(env, ["fields.Password"], "login.password", "login-2"), "password into the slot"
    )
    renamed = enqueue_dict(env, rename(env, "WPA key", "WiFi key", "note-1"), "rename wifi")
    merged = enqueue_dict(
        env,
        {
            "kind": "mergeItems",
            "fromItemId": "note-5",
            "fromRevisionDate": env.revisions()["note-5"],
            "toItemId": "login-1",
            "toRevisionDate": env.revisions()["login-1"],
            "mapping": {"fields.Recovery code": "fields.Recovery code"},
        },
        "recovery codes into the login",
    )
    stale = enqueue_dict(
        env,
        {
            "kind": "mergeColumns",
            "sources": ["fields.PIN"],
            "target": "login.totp",
            "items": listed(env, "login-1"),
            "resolutions": {"login-1": "fields.PIN"},
        },
        "pin into totp",
    )
    rejected = enqueue_dict(env, env.op("trashItem", "card-1"), "old card")
    env.client.post("/api/queue/reject", json={"ids": [rejected]})
    env.item("login-1")["revisionDate"] = CHANGED
    response = approve(env, moved, renamed, merged, stale)
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == [moved, renamed]
    assert response.json()["failed"] == [merged, stale]
    assert env.item("login-2")["login"]["password"] == "shop-secret-31"
    raw = b"".join(p.read_bytes() for p in tmp_path.glob(f"{DB_FILE}*"))
    assert raw
    leaked = [secret for secret in ALL_SECRETS if secret.encode() in raw]
    assert leaked == []


def test_ui_enqueue_queues_one_entry_per_operation_in_order(env: AppEnv):
    response = enqueue_ui(
        env,
        env.set_leaf("login-1", "name", "Mail"),
        env.op("trashItem", "card-1"),
        env.set_leaf("note-1", "fields.New", "yes", fieldType=0),
    )
    assert response.status_code == 201, response.text
    ids = response.json()["ids"]
    assert len(ids) == 3
    pending = listing(env, "pending")
    assert [e["id"] for e in pending] == ids
    assert [e["source"] for e in pending] == ["ui", "ui", "ui"]
    assert [e["summary"] for e in pending] == [
        'Set name of "Example Mail"',
        'Trash "Visa debit"',
        'Set fields.New of "Backup disk"',
    ]
    assert pending[0]["operation"]["value"] == "Mail"
    assert pending[0]["diff"][0]["after"] == "Mail"
    ## queueing writes nothing
    assert env.bw.put == []
    assert env.backups == [env.login_backup]
    assert status(env)["pending"] == 3


def test_ui_enqueue_refuses_a_batch_as_a_whole(env: AppEnv):
    good = env.set_leaf("login-1", "name", "Mail")
    stale = {**env.op("trashItem", "card-1"), "revisionDate": "2026-01-01T00:00:00.000Z"}
    response = enqueue_ui(env, good, stale)
    assert response.status_code == 409, response.text
    assert response.json()["conflicts"] == [
        {
            "itemId": "card-1",
            "expected": "2026-01-01T00:00:00.000Z",
            "actual": env.revisions()["card-1"],
        }
    ]
    response = enqueue_ui(env, good, rename(env, "Password", "password", "note-4"))
    assert response.status_code == 409
    assert response.json()["mergeConflicts"][0]["itemId"] == "note-4"
    response = enqueue_ui(env, good, env.set_leaf(env.tool_note_id, "name", "renamed"))
    assert response.status_code == 422
    assert "bwtidy note" in response.json()["detail"]
    ## the secret rule still holds for the UI: a secret value may not go into a plain column
    response = enqueue_ui(env, good, env.set_leaf("login-1", "name", "pw-login-secret-e0e7"))
    assert response.status_code == 422
    assert_absent("pw-login-secret-e0e7", response.text, "the response")
    assert enqueue_ui(env, {"kind": "setLeaf", "itemId": "login-1"}).status_code == 422
    assert enqueue_ui(env).status_code == 422
    assert listing(env) == []
    env.client.cookies.clear()
    assert enqueue_ui(env, good).status_code == 401
    assert env.client.get("/api/queue/auto-commit").status_code == 401
    assert commit_now(env).status_code == 401


def three_ui_ops(env: AppEnv) -> list[Operation]:
    return [
        as_operation(env.set_leaf("login-1", "name", "Mail")),
        as_operation(env.set_leaf("login-1", "login.password", HIDDEN_VALUE)),
        as_operation(env.set_leaf("note-1", "fields.Recovery", HIDDEN_VALUE + "-b", fieldType=1)),
    ]


def record_told(env: AppEnv) -> list[tuple[int, int]]:
    """Every id the auto-commit is told of, with the number of rows that exist at that moment."""
    told: list[tuple[int, int]] = []

    def note(entry_id: int) -> None:
        told.append((entry_id, len(env.queue.db.queue_entries())))

    env.queue.on_ui_entry = note
    return told


def test_a_batch_whose_second_encryption_fails_queues_nothing(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    told = record_told(env)

    def broken(self: HiddenValues, value: str) -> str:
        raise HiddenValueError("encryption failed")

    monkeypatch.setattr(HiddenValues, "encrypt", broken)
    with pytest.raises(HiddenValueError):
        env.queue.enqueue_ui(three_ui_ops(env))
    assert env.queue.db.queue_entries() == []
    assert told == []
    assert listing(env) == []


def test_a_batch_whose_second_insert_fails_queues_nothing(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    told = record_told(env)
    db = env.queue.db
    real = db._connection()

    class FailsOnce:
        """The connection, whose second INSERT into the queue raises."""

        def __init__(self) -> None:
            self.inserts = 0

        def __enter__(self):
            return real.__enter__()

        def __exit__(self, *exc: Any):
            return real.__exit__(*exc)

        def execute(self, sql: str, *args: Any):
            if sql.startswith("INSERT INTO queue"):
                self.inserts += 1
                if self.inserts == 2:
                    raise sqlite3.OperationalError("disk I/O error")
            return real.execute(sql, *args)

    monkeypatch.setattr(db, "_conn", FailsOnce())
    with pytest.raises(sqlite3.OperationalError):
        env.queue.enqueue_ui(three_ui_ops(env))
    monkeypatch.setattr(db, "_conn", real)
    assert db.queue_entries() == []
    assert told == []


def test_the_auto_commit_is_told_of_a_batch_after_every_row_exists(env: AppEnv):
    told = record_told(env)
    ids = env.queue.enqueue_ui(three_ui_ops(env))
    assert len(ids) == 3
    assert ids == sorted(ids)
    assert told == [(entry_id, 3) for entry_id in ids]
    assert [e["id"] for e in listing(env, "pending")] == ids


def test_a_text_field_refuses_a_hidden_value_and_sqlite_never_sees_it(env: AppEnv, tmp_path: Path):
    card = env.item("card-1")
    card["fields"] = [*(card.get("fields") or []), {"name": "PIN", "value": None, "type": 0}]
    env.reload()
    grid = env.client.get("/api/grid").json()
    assert next(c for c in grid["columns"] if c["id"] == "fields.PIN")["secret"] is True
    row = next(r for r in grid["rows"] if r["id"] == "card-1")
    assert "fields.PIN" not in row["cells"]
    assert row["emptyTextFields"] == ["fields.PIN"]
    response = enqueue_ui(env, env.set_leaf("card-1", "fields.PIN", HIDDEN_VALUE, fieldType=1))
    assert response.status_code == 422
    assert_absent(HIDDEN_VALUE, response.text, "the response")
    assert_absent(HIDDEN_VALUE.encode(), raw_sqlite(tmp_path), "the database file")


def test_a_hidden_value_is_stored_only_encrypted_and_written_correctly(
    env: AppEnv, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    caplog.set_level("DEBUG")
    ids = enqueue_ui(env, *hidden_setlers(env)).json()["ids"]
    shown = listing(env, "pending")
    assert [e["operation"]["value"] for e in shown] == [MASKED, MASKED]
    assert [e["diff"][0]["after"] for e in shown] == [MASKED, MASKED]
    assert [e["diff"][0]["column"] for e in shown] == ["login.password", "fields.Recovery"]
    text = env.client.get("/api/queue").text
    assert_absent(HIDDEN_VALUE, text, "the output")
    raw = raw_sqlite(tmp_path)
    assert_absent(HIDDEN_VALUE.encode(), raw, "the stored bytes")
    assert_present(b"AGE ENCRYPTED FILE", raw, "the stored bytes")
    outcome = commit_now(env).json()
    assert outcome["applied"] == ids
    assert env.item("login-1")["login"]["password"] == HIDDEN_VALUE
    [field] = [f for f in env.item("note-1")["fields"] if f["name"] == "Recovery"]
    assert (field["value"], field["type"]) == (HIDDEN_VALUE + "-b", 1)
    assert [e["operation"]["value"] for e in listing(env, "applied")] == [MASKED, MASKED]
    assert_absent(HIDDEN_VALUE.encode(), raw_sqlite(tmp_path), "the database file")
    assert_absent(HIDDEN_VALUE, caplog.text, "the log")
    assert_absent(HIDDEN_VALUE, env.client.get("/api/change-log").text, "the change log response")


def test_a_secret_edit_of_a_field_removed_in_the_same_commit_stays_hidden(env: AppEnv):
    ops = [
        env.op("removeLeaf", "login-1", column="fields.PIN"),
        env.set_leaf("login-1", "fields.PIN", HIDDEN_VALUE),
    ]
    ids = enqueue_ui(env, *ops).json()["ids"]
    assert commit_now(env).json()["applied"] == ids
    [field] = [f for f in env.item("login-1")["fields"] if f["name"] == "PIN"]
    value_written = field["value"] == HIDDEN_VALUE
    assert value_written
    assert field["type"] == 1


TYPED_SECRET = "typed-secret-77"


NOT_HIDDEN = "fields.Code is not a hidden field on this item, a hidden value cannot go there"


@pytest.mark.parametrize("one_request", [False, True])
def test_a_secret_edit_after_add_field_in_the_same_commit_stays_hidden(
    env: AppEnv, one_request: bool
):
    text = env.set_leaf("login-1", "fields.Code", "plain-start", fieldType=0)
    hidden = env.set_leaf("login-1", "fields.Code", TYPED_SECRET, fieldType=1)
    if one_request:
        ids = enqueue_ui(env, text, hidden).json()["ids"]
    else:
        ids = [enqueue_ui(env, op).json()["ids"][0] for op in (text, hidden)]
    outcome = commit_now(env).json()
    assert (outcome["applied"], outcome["failed"]) == ([ids[0]], [ids[1]])
    assert entry(env, ids[1])["result"] == {"error": "invalid", "detail": NOT_HIDDEN}
    [field] = [f for f in env.item("login-1")["fields"] if f["name"] == "Code"]
    assert (field["type"], field["value"]) == (0, "plain-start")
    written = TYPED_SECRET in json.dumps(env.bw.put)
    in_sqlite = TYPED_SECRET in raw_sqlite_text(env)
    answered = TYPED_SECRET in env.client.get("/api/queue").text
    assert not (written or in_sqlite or answered)


LATER_HIDDEN = "later-hidden-value-5096"


def stored_rows(env: AppEnv, ids: list[int]) -> list[dict[str, Any]]:
    return env.queue.db.queue_entries(ids=ids)


@pytest.mark.parametrize("one_request", [False, True])
def test_a_value_into_a_field_a_queued_edit_adds_hidden_is_stored_encrypted(
    env: AppEnv, tmp_path: Path, one_request: bool
):
    added = env.set_leaf("note-1", "fields.Recovery", HIDDEN_VALUE, fieldType=1)
    later = env.set_leaf("note-1", "fields.Recovery", LATER_HIDDEN)
    if one_request:
        ids = enqueue_ui(env, added, later).json()["ids"]
    else:
        ids = [enqueue_ui(env, op).json()["ids"][0] for op in (added, later)]
    rows = stored_rows(env, ids)
    assert [row["value_encrypted"] for row in rows] == [1, 1]
    assert all("AGE ENCRYPTED FILE" in row["operation_json"] for row in rows)
    raw = raw_sqlite(tmp_path)
    assert_absent(HIDDEN_VALUE.encode(), raw, "the database file")
    assert_absent(LATER_HIDDEN.encode(), raw, "the database file")
    shown = listing(env, "pending")
    assert [e["operation"]["value"] for e in shown] == [MASKED, MASKED]
    assert [e["diff"][0]["after"] for e in shown] == [MASKED, MASKED]
    text = env.client.get("/api/queue").text
    assert_absent(HIDDEN_VALUE, text, "the queue response")
    assert_absent(LATER_HIDDEN, text, "the queue response")
    assert_absent("AGE ENCRYPTED FILE", text, "the queue response")
    assert commit_now(env).json()["applied"] == ids
    [field] = [f for f in env.item("note-1")["fields"] if f["name"] == "Recovery"]
    assert (field["value"], field["type"]) == (LATER_HIDDEN, 1)
    applied = listing(env, "applied")
    assert [e["operation"]["value"] for e in applied] == [MASKED, MASKED]
    assert [e["diff"][0]["after"] for e in applied] == [MASKED, MASKED]
    assert_absent(LATER_HIDDEN.encode(), raw_sqlite(tmp_path), "the database file")


def generated_recovery(env: AppEnv) -> dict[str, Any]:
    return env.op("generateLeaf", "note-1", column="fields.Recovery")


def renamed_pin(env: AppEnv) -> dict[str, Any]:
    return rename(env, "PIN", "Code", "login-1")


@pytest.mark.parametrize("one_request", [False, True])
@pytest.mark.parametrize(
    ("earlier", "item_id", "column"),
    [(generated_recovery, "note-1", "fields.Recovery"), (renamed_pin, "login-1", "fields.Code")],
    ids=["generateLeaf", "renameField"],
)
def test_a_value_into_a_field_a_queued_generation_or_rename_makes_hidden_is_stored_encrypted(
    env: AppEnv, tmp_path: Path, one_request: bool, earlier: Any, item_id: str, column: str
):
    first = earlier(env)
    later = env.set_leaf(item_id, column, LATER_HIDDEN)
    if one_request:
        ids = enqueue_ui(env, first, later).json()["ids"]
    else:
        ids = [enqueue_ui(env, op).json()["ids"][0] for op in (first, later)]
    rows = stored_rows(env, ids)
    assert [row["value_encrypted"] for row in rows] == [0, 1]
    assert "AGE ENCRYPTED FILE" in rows[1]["operation_json"]
    assert_absent(LATER_HIDDEN.encode(), raw_sqlite(tmp_path), "the database file")
    shown = listing(env, "pending")[1]
    assert shown["operation"]["value"] == MASKED
    assert shown["diff"][0]["after"] == MASKED
    text = env.client.get("/api/queue").text
    assert_absent(LATER_HIDDEN, text, "the queue response")
    assert_absent("AGE ENCRYPTED FILE", text, "the queue response")
    assert commit_now(env).json()["applied"] == ids
    [field] = [f for f in env.item(item_id)["fields"] if f["name"] == column.split(".", 1)[1]]
    assert (field["value"], field["type"]) == (LATER_HIDDEN, 1)
    assert listing(env, "applied")[1]["operation"]["value"] == MASKED
    assert_absent(LATER_HIDDEN.encode(), raw_sqlite(tmp_path), "the database file")


def test_an_agent_value_into_a_field_a_queued_edit_adds_hidden_is_refused(env: AppEnv):
    [ui_id] = enqueue_ui(
        env, env.set_leaf("note-1", "fields.Recovery", HIDDEN_VALUE, fieldType=1)
    ).json()["ids"]
    op = env.set_leaf("note-1", "fields.Recovery", "guessed-secret-1")
    with pytest.raises(InvalidOperation, match="fields.Recovery is a secret column") as raised:
        enqueue_dict(env, op, "set the recovery code")
    assert "guessed-secret-1" not in str(raised.value)
    assert [e["id"] for e in listing(env)] == [ui_id]


def test_a_ui_create_item_with_a_secret_value_is_refused(env: AppEnv, tmp_path: Path):
    value = "never-stored-secret-5521"
    op = {"kind": "createItem", "type": 1, "name": "Forge", "values": {"login.password": value}}
    response = enqueue_ui(env, op)
    assert response.status_code == 422
    assert "login.password is secret: a new item never takes a secret value" in response.text
    assert_absent(value, response.text, "the response")
    assert listing(env) == []
    assert_absent(value.encode(), raw_sqlite(tmp_path), "the database file")


TYPED_SECRET = "typed-new-item-secret-3302"


ROUTER_TEMPLATE = {
    "name": "Router",
    "type": 1,
    "coordinates": [
        {"kind": "path", "path": "login.password"},
        {"kind": "field", "name": "WPA key", "fieldType": 1},
    ],
}


CREATE_WITH_SECRET = {"kind": "createItem", "name": "Forge", "type": 1}


LOGIN_WITH_SECRET = {**CREATE_WITH_SECRET, "secretValues": {"login.password": TYPED_SECRET}}


ROUTER_WITH_SECRET = {
    "kind": "createItem",
    "name": "Attic",
    "template": "Router",
    "secretValues": {"fields.WPA key": TYPED_SECRET},
}


def assert_entry_masked(env: AppEnv, shown: dict[str, Any], column: str) -> None:
    """The entry shows the column as masked, and neither the value nor its ciphertext."""
    assert shown["operation"]["secretValues"] == {column: MASKED}
    assert "value" not in shown["operation"]
    assert [r["after"] for r in shown["diff"] if r["column"] == column] == [MASKED]
    text = env.client.get("/api/queue").text
    leaked = TYPED_SECRET in text or "AGE ENCRYPTED" in text
    assert not leaked


@pytest.mark.parametrize(
    ("operation", "column", "blank"),
    [
        (LOGIN_WITH_SECRET, "login.password", None),
        (ROUTER_WITH_SECRET, "fields.WPA key", "login.password"),
    ],
)
def test_a_ui_create_item_with_secret_values_is_stored_only_encrypted_and_written(
    env: AppEnv,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    operation: dict[str, Any],
    column: str,
    blank: str | None,
):
    caplog.set_level("DEBUG")
    store_templates(env, ROUTER_TEMPLATE)
    response = enqueue_ui(env, operation)
    assert response.status_code == 201, response.text
    [entry_id] = response.json()["ids"]

    [row] = env.queue.db.queue_entries(ids=[entry_id])
    assert row["value_encrypted"]
    stored = json.loads(row["operation_json"])["secretValues"][column]
    assert stored.startswith("-----BEGIN AGE ENCRYPTED FILE-----")
    raw = raw_sqlite(tmp_path)
    in_sqlite = TYPED_SECRET.encode() in raw or TYPED_SECRET in row["operation_json"]
    assert not in_sqlite
    assert_entry_masked(env, entry(env, entry_id), column)
    assert_absent(TYPED_SECRET, entry(env, entry_id)["summary"], "the summary")

    created = len(env.bw.created)
    outcome = commit_now(env).json()
    assert outcome["applied"] == [entry_id]
    [new] = env.bw.created[created:]
    if column == "login.password":
        written = new["login"]["password"] == TYPED_SECRET
    else:
        [field] = [f for f in new["fields"] if f["name"] == "WPA key"]
        assert field["type"] == 1
        written = field["value"] == TYPED_SECRET
    assert written

    [applied] = listing(env, "applied")
    assert_entry_masked(env, applied, column)
    to_fill = env.client.get("/api/grid").json()["toFill"].get(new["id"], [])
    assert to_fill == ([blank] if blank else [])
    in_answers = TYPED_SECRET in env.client.get("/api/change-log").text
    in_sqlite = TYPED_SECRET.encode() in raw_sqlite(tmp_path)
    in_logs = TYPED_SECRET in caplog.text
    assert not (in_answers or in_sqlite or in_logs)


def test_a_rejected_create_item_keeps_its_secret_values_masked(env: AppEnv, tmp_path: Path):
    [entry_id] = enqueue_ui(env, LOGIN_WITH_SECRET).json()["ids"]
    response = env.client.post("/api/queue/reject", json={"ids": [entry_id]})
    assert response.status_code == 200, response.text
    assert_entry_masked(env, entry(env, entry_id), "login.password")
    in_sqlite = TYPED_SECRET.encode() in raw_sqlite(tmp_path)
    assert not in_sqlite


def test_a_create_item_whose_ciphertext_cannot_be_decrypted_fails_and_writes_nothing(
    env: AppEnv, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    caplog.set_level("DEBUG")
    [entry_id] = enqueue_ui(env, LOGIN_WITH_SECRET).json()["ids"]
    env.queue.hidden._identity = pyrage.x25519.Identity.generate()
    created = len(env.bw.created)
    outcome = commit_now(env).json()
    assert outcome["failed"] == [entry_id]
    assert outcome["applied"] == []
    assert env.bw.created[created:] == []
    failed = entry(env, entry_id)
    assert failed["status"] == "failed"
    assert failed["result"]["error"] == "invalid"
    assert "cannot be decrypted" in failed["result"]["detail"]
    assert_entry_masked(env, failed, "login.password")
    in_logs = TYPED_SECRET in caplog.text or TYPED_SECRET.encode() in raw_sqlite(tmp_path)
    assert not in_logs


## ---- a plain value never equals a secret value of the same item (docs/spec.md, Secret values)

SAME = "same-value-4242"


USERNAME_FIRST = [("login.username", SAME), ("login.password", SAME)]


PASSWORD_FIRST = list(reversed(USERNAME_FIRST))


def refused_naming_both(response: Any) -> bool:
    if response.status_code != 422:
        return False
    detail = response.json()["detail"]
    return "login.username" in detail and "login.password" in detail


def username_is_password(env: AppEnv) -> bool:
    login = env.item("login-1")["login"]
    return login["username"] == login["password"]


@pytest.mark.parametrize("steps", [USERNAME_FIRST, PASSWORD_FIRST], ids=["username", "password"])
def test_a_pending_value_and_an_equal_one_of_the_other_kind_are_never_queued_together(
    env: AppEnv, steps: list[tuple[str, str]]
):
    [first, second] = [env.set_leaf("login-1", column, value) for column, value in steps]
    assert enqueue_ui(env, first).status_code == 201
    refused = refused_naming_both(enqueue_ui(env, second))
    assert refused
    assert len(listing(env, "pending")) == 1
    assert commit_now(env).json()["failed"] == []
    assert not username_is_password(env)


@pytest.mark.parametrize("steps", [USERNAME_FIRST, PASSWORD_FIRST], ids=["username", "password"])
def test_one_request_never_queues_a_value_equal_to_one_of_the_other_kind(
    env: AppEnv, steps: list[tuple[str, str]]
):
    ops = [env.set_leaf("login-1", column, value) for column, value in steps]
    refused = refused_naming_both(enqueue_ui(env, *ops))
    assert refused
    assert listing(env) == []


@pytest.mark.parametrize("steps", [USERNAME_FIRST, PASSWORD_FIRST], ids=["username", "password"])
def test_two_commits_never_make_the_username_equal_the_password(
    env: AppEnv, steps: list[tuple[str, str]]
):
    [(column, value), second] = steps
    assert enqueue_ui(env, env.set_leaf("login-1", column, value)).status_code == 201
    assert len(commit_now(env).json()["applied"]) == 1
    refused = refused_naming_both(enqueue_ui(env, env.set_leaf("login-1", *second)))
    assert refused
    assert listing(env, "pending") == []
    assert not username_is_password(env)


def test_a_commit_refuses_the_second_of_two_equal_values(env: AppEnv):
    """Entries the enqueue check never saw together (here put straight into SQLite) still
    meet at the commit: the write path refuses the later one and writes the other."""
    username = env.set_leaf("login-1", "login.username", SAME)
    [first] = enqueue_ui(env, username).json()["ids"]
    password = env.set_leaf("login-1", "login.password", SAME)
    stored = {**password, "value": env.queue.hidden.encrypt(SAME), "fieldType": None}
    second = env.writes.db.add_queue_entry(
        "ui", "set the password", json.dumps(stored), value_encrypted=True
    )
    outcome = commit_now(env).json()
    assert (outcome["applied"], outcome["failed"]) == ([first], [second])
    result = entry(env, second)["result"]
    named = "login.username" in result["detail"] and "login.password" in result["detail"]
    assert named
    assert env.item("login-1")["login"]["username"] == SAME
    assert not username_is_password(env)


def test_a_value_equal_to_a_secret_never_reaches_an_answer_a_log_or_sqlite(
    env: AppEnv, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    username = env.set_leaf("login-1", "login.username", SAME)
    password = env.set_leaf("login-1", "login.password", SAME)
    answers = [
        enqueue_ui(env, username, password),
        enqueue_ui(env, password, username),
        enqueue_ui(env, password),
        enqueue_ui(env, username),
        env.client.get("/api/queue"),
        commit_now(env),
        enqueue_ui(env, env.set_leaf("login-1", "login.username", SAME)),
        env.client.get("/api/queue"),
        env.client.get("/api/change-log"),
    ]
    assert [a.status_code for a in answers] == [422, 422, 201, 422, 200, 200, 422, 200, 200]
    leaked = [index for index, answer in enumerate(answers) if SAME in answer.text]
    assert leaked == []
    in_log = SAME in caplog.text
    assert not in_log
    in_sqlite = SAME in raw_sqlite_text(env)
    assert not in_sqlite
    assert env.item("login-1")["login"]["username"] == LOGIN_USERNAME


def test_a_different_value_still_passes(env: AppEnv):
    response = enqueue_ui(
        env,
        env.set_leaf("login-1", "login.username", SAME),
        env.set_leaf("login-1", "login.password", "another-value-1717"),
    )
    assert response.status_code == 201
    assert len(commit_now(env).json()["applied"]) == 2


def test_an_existing_collision_does_not_block_another_edit(env: AppEnv):
    login = env.item("login-1")["login"]
    login["username"] = login["password"] = SAME
    env.reload()
    response = enqueue_ui(
        env,
        env.set_leaf("login-1", "name", "Renamed Mail"),
        env.set_leaf("login-1", "notes", "a note"),
        env.set_leaf("login-1", "login.totp", "otpauth://totp/new"),
    )
    assert response.status_code == 201, response.text
    assert len(commit_now(env).json()["applied"]) == 3
    ## a new write of the equal value is refused, also into another column
    refused = enqueue_ui(env, env.set_leaf("login-1", "notes", SAME))
    assert refused.status_code == 422
    named = "notes" in refused.json()["detail"] and SAME not in refused.text
    assert named


def test_a_folder_named_like_a_secret_is_refused(env: AppEnv):
    password = env.item("login-1")["login"]["password"]
    move = {
        "kind": "moveToFolder",
        "itemIds": ["login-1"],
        "revisionDates": {"login-1": env.revisions()["login-1"]},
        "folderName": password,
    }
    response = enqueue_ui(env, move)
    assert response.status_code == 422
    named = "folder" in response.json()["detail"] and "login.password" in response.json()["detail"]
    assert named
    leaked = password in response.text
    assert not leaked
    assert listing(env) == []


def test_a_secret_removed_by_a_pending_entry_stays_out_of_a_queued_folder_name(env: AppEnv):
    folders = len(env.bw.folders)
    removal = env.op("removeLeaf", "login-1", column="fields.PIN")
    assert enqueue_ui(env, removal).status_code == 201
    move = {
        "kind": "moveToFolder",
        "itemIds": ["login-1"],
        "revisionDates": {"login-1": env.revisions()["login-1"]},
        "folderName": PIN_SECRET,
    }
    response = enqueue_ui(env, move)
    assert response.status_code == 422
    detail = response.json()["detail"]
    named = "folder" in detail and "fields.PIN" in detail
    assert named
    leaked = PIN_SECRET in response.text
    assert not leaked
    assert len(listing(env, "pending")) == 1
    assert commit_now(env).json()["failed"] == []
    assert len(env.bw.folders) == folders
