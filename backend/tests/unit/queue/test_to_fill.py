"""The to-fill list in the tool note (docs/spec.md, The tool note, Writes and Grid).

What adds to it (a written createItem or applyTemplate, the user's mark), what prunes it after
every load of the vault, POST /api/to-fill and GET /api/grid's toFill, against the fake bw.
Every change of the list is a queued setToolNoteSection, committed here by hand.
"""

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import bwtidy.queue.autocommit as autocommit
from bwtidy.bw.client import BwError
from bwtidy.operations import Operation, SetToolNoteSection
from bwtidy.queue.intake import VaultLocked
from bwtidy.queue.to_fill import TO_FILL_SUMMARY
from bwtidy.sections import InvalidToolNote
from bwtidy.sections.to_fill import parse_to_fill
from bwtidy.writes.path import APPLY, WritePath
from tests.support.app_env import AppEnv
from tests.support.normalize_data import EXTRA
from tests.support.queue_helpers import commit_now, enqueue_dict, enqueue_ui, listing
from tests.support.ssh_keys import ed25519_private_key
from tests.support.writes_helpers import store_templates

TODAY = "2026-10-30"

ROUTER = {
    "name": "Router",
    "type": 1,
    "coordinates": [
        {"kind": "path", "path": "login.username"},
        {"kind": "path", "path": "login.password"},
        {"kind": "field", "name": "WPA key", "fieldType": 1},
        {"kind": "field", "name": "SSID", "fieldType": 0},
    ],
}
DISK = {
    "name": "Disk",
    "type": 2,
    "coordinates": [
        {"kind": "field", "name": "WPA key", "fieldType": 1},
        {"kind": "field", "name": "Recovery key", "fieldType": 1},
        {"kind": "field", "name": "Serial", "fieldType": 0},
    ],
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppEnv]:
    ## the auto-commit task never gets a turn on its own here, the tests ask for each commit
    monkeypatch.setattr(autocommit, "TICK", 3600.0)
    env = AppEnv(tmp_path, extra=EXTRA)
    env.app.state.bw.to_fill._today = lambda: date(2026, 10, 30)
    with env:
        env.unlock()
        yield env


def to_fill_saves(env: AppEnv, status: str = "pending") -> list[dict[str, Any]]:
    return [e for e in listing(env, status) if e["summary"] == TO_FILL_SUMMARY]


def stored(env: AppEnv) -> Any:
    """The toFill section of the tool note in the fake vault, None without one."""
    return json.loads(env.item(env.tool_note_id)["notes"]).get("toFill")


def grid_to_fill(env: AppEnv) -> dict[str, list[str]]:
    return env.client.get("/api/grid").json()["toFill"]


def create_and_commit(env: AppEnv, **fields: Any) -> str:
    """Queue a createItem from the UI, commit it and answer the new item's id."""
    created = len(env.bw.created)
    response = enqueue_ui(env, {"kind": "createItem", **fields})
    assert response.status_code == 201, response.text
    assert commit_now(env).json()["failed"] == []
    [new] = env.bw.created[created:]
    return new["id"]


def commit_list(env: AppEnv) -> None:
    """Commit the queued list saves, which leaves nothing more to queue."""
    assert to_fill_saves(env)
    assert commit_now(env).json()["failed"] == []
    assert to_fill_saves(env) == []


def entry(item_id: str, *columns: str) -> dict[str, Any]:
    return {"itemId": item_id, "columns": list(columns), "since": TODAY}


def put_list(env: AppEnv, *entries: dict[str, Any]) -> None:
    """Write a toFill section straight into the fake vault's note, as another client would,
    and load the vault again (which prunes it)."""
    note = env.item(env.tool_note_id)
    sections = json.loads(note["notes"])
    sections["toFill"] = list(entries)
    note["notes"] = json.dumps(sections)
    env.reload()


@pytest.mark.parametrize(
    ("fields", "columns"),
    [
        ({"type": 1}, ["login.password"]),
        ({"type": 3}, ["card.number", "card.code"]),
        ({"type": 5}, ["sshKey.privateKey"]),
    ],
)
def test_a_new_item_of_a_raw_type_lists_its_empty_secrets(
    env: AppEnv, fields: dict[str, Any], columns: list[str]
):
    new_id = create_and_commit(env, name="Brand new", **fields)
    [save] = to_fill_saves(env)
    assert save["source"] == "ui"
    ## the grid shows the list before it is written
    assert grid_to_fill(env) == {new_id: columns}
    commit_list(env)
    assert stored(env) == [entry(new_id, *columns)]
    assert grid_to_fill(env) == {new_id: columns}


@pytest.mark.parametrize(
    "fields", [{"type": 1, "generatePassword": True}, {"type": 2}, {"type": 4}]
)
def test_a_new_item_without_empty_secrets_lists_nothing(env: AppEnv, fields: dict[str, Any]):
    create_and_commit(env, name="Brand new", **fields)
    assert to_fill_saves(env) == []
    assert grid_to_fill(env) == {}
    assert stored(env) is None


def test_a_template_lists_its_secret_coordinates(env: AppEnv):
    store_templates(env, ROUTER)
    new_id = create_and_commit(env, template="Router", name="Attic", values={"fields.SSID": "a"})
    commit_list(env)
    assert stored(env) == [entry(new_id, "login.password", "fields.WPA key")]


def test_an_optional_secret_is_not_listed(env: AppEnv):
    coordinates = [
        {"kind": "path", "path": "login.username"},
        {"kind": "path", "path": "login.password"},
        {"kind": "field", "name": "WPA key", "fieldType": 1, "optional": True},
        {"kind": "field", "name": "SSID", "fieldType": 0},
    ]
    store_templates(env, {**ROUTER, "coordinates": coordinates})
    new_id = create_and_commit(env, template="Router", name="Attic", values={"fields.SSID": "a"})
    commit_list(env)
    assert stored(env) == [entry(new_id, "login.password")]


def test_a_generated_password_leaves_only_the_hidden_field(env: AppEnv):
    store_templates(env, ROUTER)
    new_id = create_and_commit(env, template="Router", name="Attic", generatePassword=True)
    commit_list(env)
    assert stored(env) == [entry(new_id, "fields.WPA key")]


def test_an_agents_create_lists_its_secrets_once_the_user_approves_it(env: AppEnv):
    created = len(env.bw.created)
    entry_id = enqueue_dict(env, {"kind": "createItem", "type": 3, "name": "Card"}, "a card")
    assert to_fill_saves(env) == []
    response = env.client.post("/api/queue/approve", json={"ids": [entry_id]})
    assert response.json()["applied"] == [entry_id]
    [new] = env.bw.created[created:]
    commit_list(env)
    assert stored(env) == [entry(new["id"], "card.number", "card.code")]


def test_a_direct_write_of_a_script_adds_nothing(env: AppEnv):
    response = env.post({"kind": "createItem", "type": 3, "name": "Card"})
    assert response.status_code == 200, response.text
    assert to_fill_saves(env) == []


def test_apply_template_lists_the_hidden_fields_it_left_empty(env: AppEnv):
    store_templates(env, DISK)
    ## note-1 has a WPA key with a value, so only the recovery key is left to fill in
    op = env.op("applyTemplate", "note-1", template="Disk", mapping={})
    assert enqueue_ui(env, op).status_code == 201
    assert commit_now(env).json()["failed"] == []
    commit_list(env)
    assert stored(env) == [entry("note-1", "fields.Recovery key")]


def test_a_conversion_lists_them_under_the_new_id(env: AppEnv):
    store_templates(env, DISK)
    created = len(env.bw.created)
    op = env.op("applyTemplate", "login-1", template="Disk", mapping={})
    assert enqueue_ui(env, op).status_code == 201
    assert commit_now(env).json()["failed"] == []
    [new] = env.bw.created[created:]
    commit_list(env)
    assert stored(env) == [entry(new["id"], "fields.WPA key", "fields.Recovery key")]


def empty_pin(env: AppEnv) -> None:
    """login-1's hidden field PIN without a value, so it can be marked."""
    next(f for f in env.item("login-1")["fields"] if f["name"] == "PIN")["value"] = ""
    env.reload()


def convert_login(env: AppEnv) -> str:
    """Convert login-1 to a note through the write path and answer the new id. PIN comes
    along as it is."""
    response = env.post(env.op("convertItem", "login-1", newType=2, mapping={}))
    assert response.status_code == 200, response.text
    [row] = [r for r in env.change_log() if r["operationKind"] == "convertItem"]
    [new_id] = row["createdItemIds"]
    return new_id


def test_a_mark_queued_before_a_conversion_lands_on_the_new_id(env: AppEnv):
    empty_pin(env)
    assert mark(env, "login-1", "fields.PIN").json()["entryId"] is not None
    new_id = convert_login(env)
    assert grid_to_fill(env) == {new_id: ["fields.PIN"]}
    commit_list(env)
    assert stored(env) == [entry(new_id, "fields.PIN")]
    assert to_fill_saves(env) == []


def test_a_stored_entry_moves_with_a_conversion(env: AppEnv):
    """The entry of a converted item is not lost."""
    empty_pin(env)
    put_list(env, {**entry("login-1", "fields.PIN"), "since": "2026-10-01"})
    new_id = convert_login(env)
    assert stored(env) == [{**entry(new_id, "fields.PIN"), "since": "2026-10-01"}]
    assert grid_to_fill(env) == {new_id: ["fields.PIN"]}
    assert to_fill_saves(env) == []


def test_a_value_set_elsewhere_is_dropped_at_the_next_load(env: AppEnv):
    new_id = create_and_commit(env, name="Card", type=3)
    commit_list(env)
    env.item(new_id)["card"]["number"] = "4111111111111111"
    env.reload()
    commit_list(env)
    assert stored(env) == [entry(new_id, "card.code")]
    env.item(new_id)["card"]["code"] = "123"
    env.reload()
    commit_list(env)
    assert stored(env) == []


def test_a_value_set_in_the_grid_is_dropped_after_the_commit(env: AppEnv):
    new_id = create_and_commit(env, name="Key", type=5)
    commit_list(env)
    op = env.set_leaf(new_id, "sshKey.privateKey", ed25519_private_key())
    assert enqueue_ui(env, op).status_code == 201
    assert commit_now(env).json()["failed"] == []
    ## the list is right in the grid at once, and written by the next commit
    assert grid_to_fill(env) == {}
    commit_list(env)
    assert stored(env) == []


def test_a_trashed_item_leaves_the_list(env: AppEnv):
    new_id = create_and_commit(env, name="Card", type=3)
    commit_list(env)
    assert enqueue_ui(env, env.op("trashItem", new_id)).status_code == 201
    assert commit_now(env).json()["failed"] == []
    commit_list(env)
    assert stored(env) == []


def test_a_renamed_field_and_a_gone_item_are_dropped(env: AppEnv):
    env.item("note-1")["fields"].append({"name": "Recovery key", "value": None, "type": 1})
    env.item("login-1")["login"]["totp"] = None
    put_list(
        env,
        entry("note-1", "fields.Recovery key"),
        entry("login-1", "login.totp", "fields.PIN"),
        entry("gone-1", "login.password"),
    )
    ## the load above pruned the list: PIN has a value, gone-1 does not exist
    commit_list(env)
    assert stored(env) == [entry("note-1", "fields.Recovery key"), entry("login-1", "login.totp")]
    env.item("note-1")["fields"][-1]["name"] = "BitLocker key"
    env.reload()
    commit_list(env)
    assert stored(env) == [entry("login-1", "login.totp")]


def test_a_list_that_is_already_right_queues_nothing(env: AppEnv):
    new_id = create_and_commit(env, name="Card", type=3)
    commit_list(env)
    puts = len(env.bw.put)
    env.reload()
    assert commit_now(env).json() == {"applied": [], "failed": [], "pending": [], "backup": None}
    assert to_fill_saves(env) == []
    assert len(env.bw.put) == puts
    assert stored(env) == [entry(new_id, "card.number", "card.code")]


def test_a_list_that_cannot_be_queued_never_fails_the_write(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    def locked(*args: Any) -> int:
        raise VaultLocked("the vault is locked")

    monkeypatch.setattr(env.queue, "enqueue_section_changes", locked)
    created = len(env.bw.created)
    assert enqueue_ui(env, {"kind": "createItem", "type": 3, "name": "Card"}).status_code == 201
    outcome = commit_now(env).json()
    assert len(outcome["applied"]) == 1
    assert outcome["failed"] == []
    assert len(env.bw.created) == created + 1


def test_a_hand_edited_list_reads_as_empty(env: AppEnv):
    note = env.item(env.tool_note_id)
    note["notes"] = json.dumps({"version": 1, "templates": [], "toFill": "everything"})
    env.reload()
    assert grid_to_fill(env) == {}
    ## nothing to prune in an empty list, the section stays until a change replaces it
    assert to_fill_saves(env) == []
    new_id = create_and_commit(env, name="Card", type=3)
    commit_list(env)
    assert stored(env) == [entry(new_id, "card.number", "card.code")]


def mark(env: AppEnv, item_id: str, column: str, marked: bool = True):
    return env.client.post(
        "/api/to-fill", json={"itemId": item_id, "column": column, "mark": marked}
    )


def test_the_user_marks_and_unmarks_an_empty_secret_cell(env: AppEnv):
    env.item("login-1")["login"]["totp"] = None
    env.reload()
    response = mark(env, "login-1", "login.totp")
    assert response.status_code == 200, response.text
    assert response.json()["toFill"] == {"login-1": ["login.totp"]}
    assert grid_to_fill(env) == {"login-1": ["login.totp"]}
    ## the same mark again changes nothing and queues nothing more
    assert mark(env, "login-1", "login.totp").json()["entryId"] is None
    commit_list(env)
    assert stored(env) == [entry("login-1", "login.totp")]
    response = mark(env, "login-1", "login.totp", marked=False)
    assert response.status_code == 200, response.text
    assert response.json()["toFill"] == {}
    commit_list(env)
    assert stored(env) == []


TOTP = {"name": "Totp", "type": 1, "coordinates": [{"kind": "path", "path": "login.totp"}]}


def unmark_during_a_template_write(env: AppEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    """login-1 listed for login.totp and committed, then an applyTemplate of Totp on it with an
    Unmark of login.totp after its PUT and before its reload."""
    store_templates(env, TOTP)
    env.item("login-1")["login"]["totp"] = None
    env.reload()
    assert mark(env, "login-1", "login.totp").status_code == 200
    commit_list(env)
    to_fill = env.app.state.bw.to_fill
    written = env.queue.on_written
    assert written is not None

    def unmark_after(op: Operation, created: list[str]) -> None:
        written(op, created)
        to_fill.mark("login-1", "login.totp", False)

    monkeypatch.setattr(env.queue, "on_written", unmark_after)
    op = env.op("applyTemplate", "login-1", template="Totp", mapping={})
    assert enqueue_ui(env, op).status_code == 201
    assert commit_now(env).json()["failed"] == []
    monkeypatch.setattr(env.queue, "on_written", written)


def test_an_unmark_during_a_write_stays_unmarked_after_its_reload(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    unmark_during_a_template_write(env, monkeypatch)
    assert grid_to_fill(env) == {}
    commit_list(env)
    assert stored(env) == []


def test_an_unmarked_column_does_not_come_back_at_a_later_load(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    unmark_during_a_template_write(env, monkeypatch)
    commit_list(env)
    env.reload()
    assert to_fill_saves(env) == []
    assert grid_to_fill(env) == {}
    assert stored(env) == []


def test_a_failed_save_keeps_its_columns_and_saves_them_at_the_next_load(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    new_id = create_and_commit(env, name="Card", type=3)
    assert to_fill_saves(env)

    async def refused(path: WritePath, write: Any, op: Any) -> None:
        raise BwError("bw serve refused the note")

    with monkeypatch.context() as patched:
        patched.setitem(APPLY, SetToolNoteSection, refused)
        assert len(commit_now(env).json()["failed"]) == 1
    assert stored(env) is None
    env.reload()
    assert grid_to_fill(env) == {new_id: ["card.number", "card.code"]}
    commit_list(env)
    assert stored(env) == [entry(new_id, "card.number", "card.code")]
    ## once the note holds them, a later load queues nothing
    env.reload()
    assert to_fill_saves(env) == []


def test_a_failed_mark_is_saved_at_the_next_load(env: AppEnv, monkeypatch: pytest.MonkeyPatch):
    env.item("login-1")["login"]["totp"] = None
    env.reload()
    assert mark(env, "login-1", "login.totp").status_code == 200

    async def refused(path: WritePath, write: Any, op: Any) -> None:
        raise BwError("bw serve refused the note")

    with monkeypatch.context() as patched:
        patched.setitem(APPLY, SetToolNoteSection, refused)
        assert len(commit_now(env).json()["failed"]) == 1
    env.reload()
    commit_list(env)
    assert stored(env) == [entry("login-1", "login.totp")]


def test_an_unmark_after_a_failed_save_keeps_the_column_off(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    new_id = create_and_commit(env, name="Card", type=3)

    async def refused(path: WritePath, write: Any, op: Any) -> None:
        raise BwError("bw serve refused the note")

    with monkeypatch.context() as patched:
        patched.setitem(APPLY, SetToolNoteSection, refused)
        assert len(commit_now(env).json()["failed"]) == 1
    assert mark(env, new_id, "card.code", marked=False).status_code == 200
    commit_list(env)
    assert stored(env) == [entry(new_id, "card.number")]
    env.reload()
    assert to_fill_saves(env) == []
    assert grid_to_fill(env) == {new_id: ["card.number"]}


@pytest.mark.parametrize(
    ("item_id", "column", "marked", "detail"),
    [
        ("login-1", "name", True, "name is no secret field of this item"),
        ("login-1", "fields.User", True, "fields.User is no secret field of this item"),
        ("login-1", "card.number", True, "card.number is no secret field of this item"),
        ("login-1", "login.password", True, "login.password has a value already"),
        ("login-1", "nonsense", True, "nonsense is no column"),
        ("gone-1", "login.password", True, "no item gone-1"),
        ("login-1", "name", False, "name is no secret field of this item"),
    ],
)
def test_marks_that_are_refused(env: AppEnv, item_id: str, column: str, marked: bool, detail: str):
    response = mark(env, item_id, column, marked)
    assert response.status_code == 422
    assert response.json()["detail"] == detail
    assert to_fill_saves(env) == []


def test_the_tool_note_cannot_be_marked(env: AppEnv):
    response = mark(env, env.tool_note_id, "fields.backup-password")
    assert response.status_code == 422
    assert response.json()["detail"] == "the bwtidy note has no fields to fill in"


def test_marking_needs_a_session(env: AppEnv):
    env.client.cookies.clear()
    assert mark(env, "login-1", "login.totp").status_code == 401


def test_the_grid_names_the_empty_secret_cells_of_each_row(env: AppEnv):
    env.item("login-1")["login"]["totp"] = None
    env.item("note-1")["fields"].append({"name": "Recovery key", "value": "", "type": 1})
    env.reload()
    rows = {row["id"]: row for row in env.client.get("/api/grid").json()["rows"]}
    assert rows["login-1"]["emptySecrets"] == ["login.totp"]
    assert rows["note-1"]["emptySecrets"] == ["fields.Recovery key"]
    assert "emptySecrets" not in rows["card-1"]


def test_a_listed_field_without_any_value_has_a_secret_column(env: AppEnv):
    """A column comes from the values, so a field only this item has empty needs its own."""
    env.item("note-1")["fields"].append({"name": "Recovery key", "value": "", "type": 1})
    env.item("login-1")["fields"] = [{"name": "Recovery key", "value": "text", "type": 0}]
    env.reload()
    assert mark(env, "note-1", "fields.Recovery key").status_code == 200
    columns = {column["id"]: column for column in env.client.get("/api/grid").json()["columns"]}
    assert columns["fields.Recovery key"]["secret"] is True


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"itemId": "a"}, "toFill is not a list"),
        ([{"itemId": "a", "columns": ["login.password"]}], "exactly itemId, columns and since"),
        ([entry("", "login.password")], "itemId is empty"),
        ([entry("a")], "columns is empty"),
        ([entry("a", "login.username")], "login.username is no secret column"),
        ([entry("a", "nonsense")], "nonsense is no column"),
        ([entry("a", "login.totp", "login.totp")], "a column is listed twice"),
        ([entry("a", "login.totp"), entry("a", "card.code")], "more than one entry for a"),
        ([{**entry("a", "card.code"), "since": "today"}], "since is no date"),
    ],
)
def test_the_parser_refuses_a_malformed_list(value: Any, message: str):
    with pytest.raises(InvalidToolNote, match=message):
        parse_to_fill(value)


def test_the_parser_takes_a_valid_list():
    value = [entry("a", "login.totp", "fields.Recovery key#2"), entry("b", "card.code")]
    assert parse_to_fill(value) == value
