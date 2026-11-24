"""The read tools: the filters of list_items, template matches and formats, the templates and
datatypes, and the agent's own proposals.
"""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bwtidy import db
from bwtidy.protocol import DB_FILE
from bwtidy.queue.agent import PROPOSALS_LIMIT
from bwtidy.vault.tool_note import find_tool_note
from tests.support.app_env import AppEnv
from tests.support.mcp_helpers import (
    BROKEN,
    BROKEN_ID,
    DATE,
    DATE_ID,
    NOTE_WITH_USER,
    PIN,
    PIN_ID,
    assert_no_secret,
    call,
    data,
    enable,
    error,
    list_tools,
    mcp_env,
    queued,
    store_datatypes,
    text,
    typed_field,
    typed_item,
)
from tests.support.quiet import assert_absent
from tests.support.section_changes import add_template, changes


@pytest.fixture
def env(tmp_path: Path) -> Iterator[AppEnv]:
    yield from mcp_env(tmp_path)


def test_list_items_text_finds_a_file_name(env: AppEnv):
    enable(env)
    found = data(env, "list_items", text="CODES.t")["items"]
    assert [i["id"] for i in found] == ["login-1"]


def test_list_items_filters(env: AppEnv):
    enable(env)

    def ids(**filters: Any) -> set[str]:
        return {item["id"] for item in data(env, "list_items", **filters)["items"]}

    everything = ids()
    assert ids(type="card") == {"card-1"}
    assert ids(type="3") == {"card-1"}
    assert ids(folder="Work") == {"login-1"}
    assert ids(folder="") == everything - {"login-1"}
    assert ids(organization="Family") == {"note-1"}
    assert ids(organization="") == everything - {"note-1"}
    assert ids(collection="Youth") == {"note-1"}
    assert ids(text="EXAMPLE MAIL") == {"login-1", "note-5"}
    assert ids(text="webmail") == {"login-1"}
    assert ids(text="https://MAIL.example") == {"login-1"}
    ## the URI's match is shown, but the search looks at the URI's text only
    assert ids(text="exact") == set()
    assert ids(text="Example", type="note") == {"note-5"}
    ## a secret value matches nothing, so the filter tells nothing about secrets
    assert ids(text="pw-login-secret") == set()
    assert ids(text="4111") == set()


def test_template_matches_and_filter(env: AppEnv):
    session = env.app.state.bw.session
    note = find_tool_note(session.cache, session.tool_note_id)
    assert note is not None
    revision = env.client.get("/api/templates").json()["revisionDate"]
    body = {**changes(add_template(NOTE_WITH_USER)), "revisionDate": revision}
    assert env.client.put("/api/templates", json=body).status_code == 202
    ## the PUT only queued the save, the commit makes the templates real
    assert env.client.post("/api/queue/commit").status_code == 200
    enable(env)
    assert data(env, "list_templates") == {"templates": [NOTE_WITH_USER]}
    item = data(env, "get_item", item_id="note-3")
    assert item["template"]["match"] == "Note with user"
    assert item["template"]["scores"][0]["filled"] == 2
    assert {i["id"] for i in data(env, "list_items", template="Note with user")["items"]} == {
        "note-3"
    }
    assert "note-3" not in {i["id"] for i in data(env, "list_items", template="")["items"]}


@pytest.fixture
def ticking(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every queue time a second after the one before, so each decision comes later."""
    start = datetime(2026, 11, 12, 9, 0, 0)
    seconds = iter(range(10_000))
    monkeypatch.setattr(
        db, "_now", lambda: f"{(start + timedelta(seconds=next(seconds))).isoformat()}+00:00"
    )
    yield


def proposals(env: AppEnv, **arguments: Any) -> list[dict[str, Any]]:
    answer = data(env, "list_proposals", **arguments)
    assert set(answer) == {"proposals"}
    return answer["proposals"]


def ui_rename(env: AppEnv, item_id: str, name: str) -> int:
    rename = {
        "kind": "setLeaf",
        "itemId": item_id,
        "revisionDate": env.revisions()[item_id],
        "column": "name",
        "value": name,
    }
    response = env.client.post("/api/queue", json={"operations": [rename]})
    assert response.status_code == 201
    [entry_id] = response.json()["ids"]
    return entry_id


def test_list_proposals_shows_each_status_of_the_agents_own_entries_newest_first(
    env: AppEnv, ticking: None
):
    enable(env)
    applied = data(env, "propose_rename_item", item_id="card-1", name="Card renamed")
    rejected = data(env, "propose_trash_item", item_id="ssh-1", reason="Unused")
    failed = data(env, "propose_move_to_folder", item_ids=["login-1"], folder="Archive")
    approved = data(env, "propose_rename_item", item_id="note-3", name="Note renamed")
    pending = data(env, "propose_rename_item", item_id="note-5", name="Mail notes")
    ui = ui_rename(env, "note-1", "Wifi renamed")
    assert env.client.post("/api/queue/reject", json={"ids": [rejected["entryId"]]}).is_success
    ## another device moved it before the user approved
    env.item("login-1")["folderId"] = None
    response = env.client.post(
        "/api/queue/approve", json={"ids": [applied["entryId"], failed["entryId"]]}
    )
    env.settle()
    assert response.json()["failed"] == [failed["entryId"]]
    ## an approved entry is one whose commit runs right now
    env.app.state.bw.queue.db.set_queue_status(approved["entryId"], "approved")

    listed = proposals(env)
    answers = [pending, approved, failed, rejected, applied]
    assert [entry["id"] for entry in listed] == [answer["entryId"] for answer in answers]
    assert ui not in {entry["id"] for entry in listed}
    assert [entry["summary"] for entry in listed] == [answer["summary"] for answer in answers]
    assert [entry["status"] for entry in listed] == [
        "pending",
        "approved",
        "failed",
        "rejected",
        "applied",
    ]
    assert [entry["reason"] for entry in listed] == [None, None, "conflict", None, None]
    stored = {entry["id"]: entry for entry in queued(env)}
    for entry in listed:
        assert set(entry) == {"id", "summary", "status", "createdAt", "decidedAt", "reason"}
        assert entry["createdAt"] == stored[entry["id"]]["createdAt"]
        assert entry["decidedAt"] == stored[entry["id"]]["decidedAt"]
    assert listed[0]["decidedAt"] is None
    assert all(entry["decidedAt"] for entry in listed[1:])
    assert_no_secret(json.dumps(listed))

    assert [entry["id"] for entry in proposals(env, status="failed")] == [failed["entryId"]]
    assert [entry["id"] for entry in proposals(env, status="pending")] == [pending["entryId"]]
    assert "Input should be" in error(env, "list_proposals", status="done")


@pytest.mark.parametrize(
    ("error_code", "word"),
    [
        ("conflicts", "conflict"),
        ("mergeConflicts", "merge conflict"),
        ("writeFailed", "write failed"),
        ("interrupted", "interrupted"),
        ("invalid", "invalid"),
        ("somethingNew", "failed"),
        (None, "failed"),
    ],
)
def test_a_failed_proposal_says_why_in_one_fixed_word(
    env: AppEnv, error_code: str | None, word: str
):
    enable(env)
    answer = data(env, "propose_trash_item", item_id="card-1")
    result = {"detail": "pw-login-secret-e0e7", "backup": "b.zip.age"}
    if error_code is not None:
        result["error"] = error_code
    env.app.state.bw.queue.db.set_queue_status(answer["entryId"], "failed", json.dumps(result))
    [entry] = proposals(env)
    assert entry["status"] == "failed"
    assert entry["reason"] == word
    assert_no_secret(json.dumps(entry))
    assert "b.zip.age" not in json.dumps(entry)


def test_list_proposals_answers_the_newest_hundred(env: AppEnv):
    enable(env)
    queue = env.app.state.bw.queue
    for number in range(PROPOSALS_LIMIT + 5):
        queue.db.add_queue_entry(
            "mcp", f"Proposal {number}", "{}", agent_summary=queue.hidden.encrypt(f"P{number}")
        )
    listed = proposals(env)
    assert len(listed) == PROPOSALS_LIMIT
    assert listed[0]["summary"] == f"P{PROPOSALS_LIMIT + 4}"
    assert listed[-1]["summary"] == "P5"


def test_a_silent_refusal_reads_as_pending_until_the_next_decision_on_another_entry(
    env: AppEnv, ticking: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    enable(env)
    queue = env.app.state.bw.queue
    other = data(env, "propose_rename_item", item_id="card-1", name="Card renamed")
    refused = data(
        env,
        "propose_rename_item",
        item_id="login-1",
        name="pw-login-secret-e0e7",
        reason="guess-reason-a",
    )
    wrong = data(env, "propose_rename_item", item_id="login-1", name="pw-login-wrong-0000")
    stored = {entry["id"]: entry for entry in queued(env)}
    assert stored[refused["entryId"]]["status"] == "rejected"

    decrypted: list[str] = []
    decrypt = queue.hidden.decrypt

    def counting(ciphertext: str) -> str:
        decrypted.append(ciphertext)
        return decrypt(ciphertext)

    monkeypatch.setattr(queue.hidden, "decrypt", counting)
    ## shown as rejected at once, this already fails
    before = {entry["id"]: entry for entry in proposals(env)}
    assert len(decrypted) == 3
    refused_seen = before[refused["entryId"]]
    same_summary = refused_seen["summary"] == refused["summary"]
    assert same_summary
    for entry in (refused_seen, before[wrong["entryId"]], before[other["entryId"]]):
        assert {key: entry[key] for key in ("status", "decidedAt", "reason")} == {
            "status": "pending",
            "decidedAt": None,
            "reason": None,
        }
    listed = [entry["id"] for entry in proposals(env, status="pending")]
    assert listed == [wrong["entryId"], refused["entryId"], other["entryId"]]
    assert proposals(env, status="rejected") == []

    ## another refusal and a UI edit are no decision
    data(env, "propose_rename_item", item_id="login-1", name="pw-login-secret-e0e7")
    ui = ui_rename(env, "note-1", "Wifi renamed")
    assert proposals(env, status="rejected") == []

    assert env.client.post("/api/queue/reject", json={"ids": [other["entryId"]]}).is_success
    decided = {entry["id"]: entry for entry in queued(env)}[other["entryId"]]["decidedAt"]
    after = {entry["id"]: entry for entry in proposals(env)}
    for entry_id in (refused["entryId"], other["entryId"]):
        assert after[entry_id]["status"] == "rejected"
        assert after[entry_id]["decidedAt"] == decided
        assert after[entry_id]["reason"] is None
    same_summary = after[refused["entryId"]]["summary"] == refused["summary"]
    assert same_summary
    assert after[wrong["entryId"]]["status"] == "pending"

    ## a later decision leaves its time as it was
    assert env.client.post("/api/queue/commit").json()["applied"] == [ui]
    env.settle()
    again = {entry["id"]: entry for entry in proposals(env)}
    assert again[refused["entryId"]] == after[refused["entryId"]]
    rejected = [entry["id"] for entry in proposals(env, status="rejected")]
    assert refused["entryId"] in rejected

    raw = b"".join(p.read_bytes() for p in tmp_path.glob(f"{DB_FILE}*"))
    assert raw
    for value in ("pw-login-secret-e0e7", "guess-reason-a"):
        assert_absent(value.encode(), raw, "the stored bytes")
    user_sees = {entry["id"]: entry for entry in queued(env)}
    assert_absent("guess-reason-a", json.dumps(user_sees), "the queue")
    assert user_sees[refused["entryId"]]["summary"].startswith('Refused proposal for "Example')


def test_a_decision_in_the_same_second_as_a_refusal_does_not_show_it(env: AppEnv):
    enable(env)
    other = data(env, "propose_rename_item", item_id="card-1", name="Card renamed")
    refused = data(env, "propose_rename_item", item_id="login-1", name="pw-login-secret-e0e7")
    queue = env.app.state.bw.queue
    [row] = queue.db.queue_entries(ids=[refused["entryId"]])
    ## the decision falls in the second the refusal was stored
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(db, "_now", lambda: row["decided_at"])
        queue.db.set_queue_status(other["entryId"], "rejected")
    seen = {entry["id"]: entry for entry in proposals(env)}
    assert seen[refused["entryId"]]["status"] == "pending"
    assert seen[other["entryId"]]["status"] == "rejected"


def test_list_datatypes_shows_whole_value_only_when_false(env: AppEnv):
    enable(env)
    part = {**DATE, "name": "Part", "id": "0000dddd", "wholeValue": False}
    coordinates = [typed_field("Code", 0, PIN_ID), typed_field("Other", 0, "0000dddd")]
    store_datatypes(env, [PIN, part], [{"name": "One", "type": 1, "coordinates": coordinates}])
    whole, partial = data(env, "list_datatypes")["datatypes"]
    assert "wholeValue" not in whole
    assert partial["wholeValue"] is False


def test_list_datatypes_without_any_and_with_a_section_that_cannot_be_read(env: AppEnv):
    enable(env)
    assert data(env, "list_datatypes") == {"datatypes": []}
    store_datatypes(env, {"no": "list"}, [])
    answer = data(env, "list_datatypes")
    assert answer["datatypes"] == []
    assert "not a list" in answer["error"]


def test_list_templates_shows_the_description_when_set(env: AppEnv):
    enable(env)
    described = {**NOTE_WITH_USER, "description": "A note with a user name"}
    store_datatypes(env, [], [described, {**NOTE_WITH_USER, "name": "Bare"}])
    listed = data(env, "list_templates")["templates"]
    assert listed[0] == described
    assert "description" not in listed[1]


def test_list_templates_names_each_datatype_and_never_its_id(env: AppEnv):
    enable(env)
    gone = "0000dddd"
    coordinates = [
        typed_field("Code", 1, PIN_ID),
        {"kind": "path", "path": "login.username", "datatype": BROKEN_ID},
        {"kind": "path", "path": "login.uris", "datatype": gone},
        {"kind": "path", "path": "name"},
    ]
    store_datatypes(env, [PIN, BROKEN], [{"name": "One", "type": 1, "coordinates": coordinates}])
    answer = text(call(env, "list_templates"))
    assert json.loads(answer) == {
        "templates": [
            {
                "name": "One",
                "type": 1,
                "coordinates": [
                    {"kind": "field", "name": "Code", "fieldType": 1, "datatype": "PIN"},
                    {"kind": "path", "path": "login.username"},
                    {"kind": "path", "path": "login.uris"},
                    {"kind": "path", "path": "name"},
                ],
            }
        ]
    }
    for datatype_id in (PIN_ID, BROKEN_ID, gone):
        assert datatype_id not in answer


def test_list_templates_shows_an_optional_coordinate(env: AppEnv):
    enable(env)
    coordinates = [
        {"kind": "path", "path": "login.username"},
        {"kind": "field", "name": "Code", "fieldType": 0, "optional": True},
    ]
    store_datatypes(env, [], [{"name": "One", "type": 1, "coordinates": coordinates}])
    [template] = data(env, "list_templates")["templates"]
    assert template["coordinates"] == coordinates
    tools = {tool.name: tool.description or "" for tool in list_tools(env)}
    assert "An optional coordinate is not needed for a match" in tools["list_templates"]


def test_list_templates_shows_a_field_name_pattern(env: AppEnv):
    enable(env)
    coordinates = [
        {"kind": "path", "path": "login.username"},
        {"kind": "fieldPattern", "pattern": "user_*", "fieldType": 0, "datatype": PIN_ID},
    ]
    store_datatypes(env, [PIN], [{"name": "One", "type": 1, "coordinates": coordinates}])
    [template] = data(env, "list_templates")["templates"]
    assert template["coordinates"][1] == {
        "kind": "fieldPattern",
        "pattern": "user_*",
        "fieldType": 0,
        "datatype": "PIN",
    }
    tools = {tool.name: tool.description or "" for tool in list_tools(env)}
    assert "A coordinate of kind fieldPattern stands for" in tools["list_templates"]
    [datatype] = data(env, "list_datatypes")["datatypes"]
    assert datatype["usedBy"] == [{"template": "One", "column": "fields.user_*"}]


def test_a_format_of_a_column_that_is_no_secret_shows_in_list_items_and_get_item(env: AppEnv):
    enable(env)
    coordinates = [
        typed_field("Code", 0, PIN_ID),
        {"kind": "path", "path": "login.uris", "datatype": DATE_ID},
    ]
    store_datatypes(
        env,
        [PIN, DATE],
        [{"name": "One", "type": 1, "coordinates": coordinates}],
        typed_item("dt-text", " 1234", 0),
    )
    item = data(env, "get_item", item_id="dt-text")
    listed = data(env, "list_items", text="Item dt-text")["items"]
    assert listed == [item]
    assert item["template"]["match"] is None
    [score] = item["template"]["scores"]
    assert (score["total"], score["missing"]) == (5, [])
    assert score["filled"] == pytest.approx(3 + 0.8 + score["formats"][1]["score"])
    assert score["formats"][0] == {
        "column": "fields.Code",
        "datatype": "PIN",
        "score": 0.8,
        "distance": 1,
        "firstEdit": 0,
        "expected": "a digit",
        "tooLong": False,
    }
    assert score["formats"][1]["column"] == "login.uris"
    assert score["formats"][1]["datatype"] == "ISO date"
    assert score["formats"][1]["score"] < 1
