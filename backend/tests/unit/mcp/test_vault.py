"""The agent's view of the vault: no secret value, the tool note left out, the datatypes only
non-secret columns use, no pattern an oracle on a secret, and the errors of a proposal.
"""

import copy
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from bwtidy.items.patterns import MAX_VALUE_LEN
from bwtidy.mcp.vault import AgentVault
from tests.support.app_env import AppEnv
from tests.support.mcp_helpers import (
    BROKEN,
    BROKEN_ID,
    DATE,
    PIN,
    PIN_ID,
    READ_TOOLS,
    assert_no_secret,
    call,
    data,
    enable,
    error,
    mcp_env,
    queued,
    store_datatypes,
    text,
    typed_field,
    typed_item,
)
from tests.support.quiet import assert_absent


@pytest.fixture
def env(tmp_path: Path) -> Iterator[AppEnv]:
    yield from mcp_env(tmp_path)


def test_list_items_has_no_secret_value(env: AppEnv):
    enable(env)
    body = call(env, "list_items")
    answer = text(body)
    assert_no_secret(answer)
    items = {item["id"]: item for item in json.loads(answer)["items"]}
    login = items["login-1"]
    assert login["name"] == "Example Mail"
    assert login["type"] == "login"
    assert login["folder"] == "Work"
    assert login["organization"] is None
    assert login["values"]["login.username"] == "dev@mail.example.test"
    assert login["values"]["login.uris"] == [
        {"uri": "https://mail.example.test", "match": None},
        {"uri": "https://webmail.example.test", "match": "exact"},
    ]
    assert login["values"]["fields.User"] == "dev"
    assert login["values"]["passkeys"] == 1
    assert login["secretColumns"] == ["login.password", "login.totp", "fields.PIN"]
    note = items["note-1"]
    assert note["organization"] == "Family"
    assert note["collections"] == ["shared", "Youth"]
    assert note["values"]["notes"] == "Free text, not secret"
    assert note["secretColumns"] == [
        "fields.Encryption passphrase ",
        "fields.WPA key",
        "fields.WPA key#2",
    ]
    assert items["card-1"]["secretColumns"] == ["card.number", "card.code"]
    assert items["ssh-1"]["secretColumns"] == ["sshKey.privateKey"]
    assert login["template"] == {"match": None, "scores": []}
    for tool in READ_TOOLS:
        assert_no_secret(text(call(env, tool, item_id="login-1")))


def test_get_item_shows_each_attachment_without_url_or_contents(env: AppEnv):
    enable(env)
    item = data(env, "get_item", item_id="login-1")
    assert item["attachments"] == [{"id": "a1", "fileName": "codes.txt", "size": 81}]
    assert isinstance(item["attachments"][0]["size"], int)
    assert "attachments" not in item["values"]
    assert_absent("https://x", text(call(env, "get_item", item_id="login-1")), "the answer")
    assert data(env, "get_item", item_id="note-1")["attachments"] == []
    listed = {i["id"]: i for i in data(env, "list_items")["items"]}
    assert listed["login-1"]["attachments"] == item["attachments"]


def test_the_tool_note_is_invisible(env: AppEnv):
    enable(env)
    note_id = env.tool_note_id
    items = data(env, "list_items")["items"]
    assert note_id not in {i["id"] for i in items}
    assert "bwtidy" not in {i["name"] for i in items}
    assert data(env, "list_items", text="bwtidy")["count"] == 0
    assert "no item" in error(env, "get_item", item_id=note_id)
    near = json.dumps(data(env, "list_near_duplicates"))
    assert "backup-password" not in near
    assert "no item" in error(env, "propose_trash_item", item_id=note_id)
    assert "no item" in error(env, "propose_rename_item", item_id=note_id, name="x")
    assert "no item" in error(env, "propose_move_to_folder", item_ids=[note_id], folder="Work")
    assert "no item" in error(env, "propose_rename_field", from_name="backup-password", to_name="x")
    assert "no item" in error(
        env, "propose_merge_items", from_item_id=note_id, to_item_id="login-1", mapping={}
    )
    assert "no item" in error(
        env, "propose_merge_items", from_item_id="note-5", to_item_id=note_id, mapping={}
    )
    assert "no item" in error(
        env,
        "propose_merge_columns",
        sources=["fields.backup-password"],
        target="notes",
        item_ids=[note_id],
    )
    assert "no item" in error(
        env, "propose_apply_template", item_id=note_id, template="Note with user"
    )
    assert queued(env) == []


def test_a_renamed_tool_note_stays_invisible(env: AppEnv):
    note_id = env.tool_note_id
    note = env.item(note_id)
    note["name"] = "bwtidyK"
    env.app.state.bw.session.cache.items = copy.deepcopy(env.bw.items)
    enable(env)
    items = data(env, "list_items")["items"]
    assert note_id not in {i["id"] for i in items}
    assert "bwtidyK" not in {i["name"] for i in items}
    assert "no item" in error(env, "get_item", item_id=note_id)
    assert "no item" in error(env, "propose_rename_item", item_id=note_id, name="bwtidy")
    assert queued(env) == []


def test_a_merge_conflict_names_items_and_columns_only(env: AppEnv):
    enable(env)
    message = error(
        env, "propose_merge_columns", sources=["fields.Password"], target="fields.password"
    )
    assert "resolutions" in message
    assert "note-4" in message
    assert '"fields.Password"' in message
    assert_no_secret(message)
    assert queued(env) == []


def test_an_error_never_echoes_a_secret(env: AppEnv, caplog: pytest.LogCaptureFixture):
    enable(env)
    caplog.set_level(logging.DEBUG)
    refusals = [
        error(
            env,
            "propose_merge_items",
            from_item_id="note-5",
            to_item_id="note-3",
            mapping={"notes": "fields.Recovery code"},
        ),
        error(
            env,
            "propose_merge_columns",
            sources=["login.password"],
            target="fields.User",
            item_ids=["login-1"],
        ),
        error(
            env,
            "propose_merge_columns",
            sources=["fields.WPA key", "fields.WPA key#2"],
            target="notes",
            item_ids=["note-1"],
        ),
        error(
            env,
            "propose_move_to_organization",
            item_id="note-1",
            organization="Family",
            collections=["Youth"],
        ),
        error(env, "propose_set_collections", item_id="login-1", collections=["Youth"]),
        error(env, "propose_move_to_personal", item_id="login-1"),
    ]
    for message in refusals:
        assert message.startswith("Error executing tool"), message
        assert "refused" in message or "resolutions" in message, message
    assert_no_secret("\n".join(refusals))
    assert queued(env) == []
    assert_no_secret(caplog.text)


SECRET_ONLY_ID, ITEM_HIDDEN_ID = "0000eeee", "0000ffff"


SECRET_ONLY_EXAMPLE, ITEM_HIDDEN_EXAMPLE = "sentinel-secret-only-4711", "sentinel-item-hidden-4712"


SECRET_ONLY = {
    "id": SECRET_ONLY_ID,
    "name": "Secret only",
    "pattern": r"[a-z0-9-]+",
    "examples": [SECRET_ONLY_EXAMPLE],
}


ITEM_HIDDEN = {
    "id": ITEM_HIDDEN_ID,
    "name": "Held hidden",
    "pattern": r"[a-z0-9-]+",
    "examples": [ITEM_HIDDEN_EXAMPLE],
}


def test_list_datatypes_lists_the_ones_a_column_that_is_no_secret_uses(env: AppEnv):
    enable(env)
    templates = [
        {
            "name": "One",
            "type": 1,
            "coordinates": [
                typed_field("Code", 1, PIN_ID),
                typed_field("Answer", 1, SECRET_ONLY_ID),
            ],
        },
        ## the template calls it text, but an item holds it hidden
        {"name": "Held", "type": 1, "coordinates": [typed_field("Code", 0, ITEM_HIDDEN_ID)]},
        {
            "name": "Two",
            "type": 1,
            "coordinates": [
                {"kind": "path", "path": "login.password", "datatype": SECRET_ONLY_ID},
                {"kind": "path", "path": "login.username", "datatype": PIN_ID},
                {"kind": "path", "path": "login.uris", "datatype": BROKEN_ID},
            ],
        },
    ]
    datatypes = [PIN, DATE, BROKEN, SECRET_ONLY, ITEM_HIDDEN]
    store_datatypes(env, datatypes, templates, typed_item("dt-held", "1234", 1))
    assert data(env, "list_datatypes") == {
        "datatypes": [
            {
                "name": "PIN",
                "pattern": r"\d{4,8}",
                "description": "Digits only",
                "ignoreCase": False,
                "examples": ["1234"],
                "counterExamples": ["12"],
                "usedBy": [
                    {"template": "One", "column": "fields.Code"},
                    {"template": "Two", "column": "login.username"},
                ],
            },
        ]
    }
    answers = [text(call(env, tool)) for tool in READ_TOOLS if tool != "get_item"]
    answers.append(text(call(env, "get_item", item_id="dt-held")))
    leaked = [a for a in answers if SECRET_ONLY_EXAMPLE in a or ITEM_HIDDEN_EXAMPLE in a]
    assert leaked == []


def test_the_vaults_datatypes_leave_out_one_only_a_secret_coordinate_uses(env: AppEnv):
    templates = [
        {
            "name": "One",
            "type": 1,
            "coordinates": [
                typed_field("Code", 0, PIN_ID),
                {"kind": "path", "path": "login.password", "datatype": SECRET_ONLY_ID},
            ],
        }
    ]
    store_datatypes(env, [PIN, SECRET_ONLY], templates)
    vault: AgentVault = env.app.state.bw.mcp.agent
    listed, problem = vault.datatypes()
    assert [d.name for d in listed] == ["PIN"]
    assert problem is None
    ## list_templates names every datatype that is not broken, a template's coordinate holds it
    assert vault.datatype_names() == {PIN_ID: "PIN", SECRET_ONLY_ID: "Secret only"}


def test_a_datatype_comes_back_when_a_column_that_is_no_secret_uses_it(env: AppEnv):
    enable(env)
    coordinates = [
        typed_field("Answer", 1, SECRET_ONLY_ID),
        typed_field("Note", 0, SECRET_ONLY_ID),
    ]
    store_datatypes(env, [SECRET_ONLY], [{"name": "One", "type": 1, "coordinates": coordinates}])
    [listed] = data(env, "list_datatypes")["datatypes"]
    assert listed["name"] == "Secret only"
    assert listed["usedBy"] == [
        {"template": "One", "column": "fields.Answer"},
        {"template": "One", "column": "fields.Note"},
    ]


## the values to tell apart: conforming, off format with the same length, of another length, on
## the other side of the engine's limit
ORACLE_VALUES = ("123456", "12345x", "x", "1" * (MAX_VALUE_LEN + 1))


@pytest.mark.parametrize(
    "coordinate",
    [
        typed_field("Code", 1, PIN_ID),
        ## the item's field type decides: hidden there is a secret, whatever the template says
        typed_field("Code", 0, PIN_ID),
        {"kind": "path", "path": "login.password", "datatype": PIN_ID},
    ],
    ids=["hidden field", "hidden field the template calls text", "built-in secret"],
)
def test_a_secret_column_is_no_oracle_for_any_pattern(env: AppEnv, coordinate: dict[str, Any]):
    enable(env)
    item = typed_item("dt-secret", ORACLE_VALUES[0], 1)
    template = {"name": "One", "type": 1, "coordinates": [coordinate]}
    store_datatypes(env, [PIN], [template], item)

    def answers() -> dict[str, str]:
        calls = {
            "list_items": {},
            "get_item": {"item_id": "dt-secret"},
            "list_templates": {},
            "list_datatypes": {},
        }
        found = {tool: text(call(env, tool, **arguments)) for tool, arguments in calls.items()}
        found["by name"] = text(call(env, "list_items", text="Item dt-secret"))
        found["by template"] = text(call(env, "list_items", template="One"))
        found["by a guess"] = text(call(env, "list_items", text=ORACLE_VALUES[0]))
        return found

    def filled_in_the_ui() -> float:
        scores = env.client.get("/api/template-matches").json()["dt-secret"]["scores"]
        return scores[0]["filled"]

    first = answers()
    full_in_the_ui = filled_in_the_ui()
    for value in ORACLE_VALUES[1:]:
        stored = env.item("dt-secret")
        stored["login"]["password"] = value
        stored["fields"][0]["value"] = value
        env.reload()
        identical = answers() == first
        assert identical
        ## the UI does see the difference, so these answers could have differed
        assert filled_in_the_ui() < full_in_the_ui
    item_answer = json.loads(first["get_item"])
    assert item_answer["template"]["match"] == "One"
    assert "formats" not in item_answer["template"]["scores"][0]


def test_the_engine_never_runs_on_a_secret_column_for_mcp(env: AppEnv):
    enable(env)
    coordinates = [
        typed_field("Code", 1, PIN_ID),
        {"kind": "path", "path": "login.username", "datatype": PIN_ID},
    ]
    store_datatypes(
        env,
        [PIN],
        [{"name": "One", "type": 1, "coordinates": coordinates}],
        typed_item("dt-secret", "1234", 1),
    )
    results = env.app.state.bw.formats.results
    results.clear()
    data(env, "list_items")
    columns = {column for _, column, *_ in results}
    assert columns == {"login.username"}


def test_a_blank_hidden_field_with_a_datatype_counts_as_not_set(env: AppEnv):
    enable(env)
    item = typed_item("dt-blank", "1234", 1)
    item["fields"][0]["value"] = None
    template = {
        "name": "One",
        "type": 1,
        "coordinates": [{"kind": "path", "path": "login.username"}, typed_field("Code", 1, PIN_ID)],
    }
    store_datatypes(env, [PIN], [template], item)
    answer = data(env, "get_item", item_id="dt-blank")
    assert data(env, "list_items", text="Item dt-blank")["items"] == [answer]
    assert answer["template"]["match"] is None
    [score] = answer["template"]["scores"]
    assert (score["filled"], score["total"], score["missing"]) == (2, 4, ["fields.Code"])
    assert "formats" not in score
