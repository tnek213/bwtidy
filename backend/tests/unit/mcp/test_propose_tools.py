"""The propose tools: each queues one entry with the revisions the agent saw, the URI tools,
creating and converting items, and a secret guess refused like any other.
"""

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from bwtidy.operations import CreateItem, InvalidOperation
from bwtidy.protocol import DB_FILE
from bwtidy.queue.intake import check_enqueue
from tests.support.app_env import AppEnv
from tests.support.mcp_helpers import (
    NOTE_WITH_USER,
    PIN,
    PIN_ID,
    PROPOSALS,
    SECRETS,
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
)
from tests.support.quiet import assert_absent
from tests.support.section_changes import add_template, changes


@pytest.fixture
def env(tmp_path: Path) -> Iterator[AppEnv]:
    yield from mcp_env(tmp_path)


@pytest.mark.parametrize(("tool", "arguments", "kind"), PROPOSALS)
def test_each_propose_tool_queues_one_entry(
    env: AppEnv, tool: str, arguments: dict[str, Any], kind: str
):
    revision = env.client.get("/api/templates").json()["revisionDate"]
    body = {**changes(add_template(NOTE_WITH_USER)), "revisionDate": revision}
    assert env.client.put("/api/templates", json=body).status_code == 202
    ## the PUT only queued the save, the commit makes the templates real
    assert env.client.post("/api/queue/commit").status_code == 200
    env.settle()
    puts, backups = len(env.bw.put), len(env.backups)
    enable(env)
    answer = data(env, tool, **arguments, reason="Tidier")
    [entry] = [e for e in queued(env) if e["status"] == "pending"]
    assert answer == {"entryId": entry["id"], "summary": entry["summary"]}
    assert entry["summary"].endswith(". Reason: Tidier")
    assert entry["source"] == "mcp"
    assert entry["status"] == "pending"
    assert entry["operation"]["kind"] == kind
    assert entry["diff"]
    assert entry["diffError"] is None
    assert_no_secret(json.dumps(entry))
    ## proposing writes nothing
    assert len(env.bw.put) == puts
    assert len(env.backups) == backups


def test_proposals_carry_the_current_revision_dates(env: AppEnv):
    enable(env)
    data(env, "propose_rename_field", from_name="User", to_name="user name")
    [entry] = queued(env)
    revisions = env.revisions()
    assert entry["operation"]["items"] == [
        {"itemId": "login-1", "revisionDate": revisions["login-1"]},
        {"itemId": "note-3", "revisionDate": revisions["note-3"]},
    ]
    assert entry["summary"] == 'Rename field "User" to "user name" in 2 items'


def test_a_move_made_elsewhere_before_the_approval_is_a_conflict(env: AppEnv):
    enable(env)
    data(env, "propose_move_to_folder", item_ids=["login-1"], folder="Archive")
    data(env, "propose_set_collections", item_id="note-1", collections=["Youth"])
    move, collections = queued(env)
    assert move["operation"]["shownFolderIds"] == {"login-1": "f1"}
    assert collections["operation"]["shownCollectionIds"] == ["c2", "c1"]
    ## another device's partial updates, the revisionDates stay
    env.item("login-1")["folderId"] = None
    env.item("note-1")["collectionIds"] = ["c2"]
    response = env.client.post("/api/queue/approve", json={"ids": [move["id"], collections["id"]]})
    env.settle()
    assert response.status_code == 200, response.text
    assert sorted(response.json()["failed"]) == sorted([move["id"], collections["id"]])
    assert env.item("login-1")["folderId"] is None
    assert env.item("note-1")["collectionIds"] == ["c2"]


MAIL_URIS = [
    {"match": None, "uri": "https://mail.example.test"},
    {"match": 3, "uri": "https://webmail.example.test"},
]


def test_propose_set_uris_queues_the_whole_list_and_approving_writes_it(env: AppEnv):
    enable(env)
    uris = [
        {"uri": "https://webmail.example.test", "match": "host"},
        {"uri": "https://new.example.test/login", "match": "never"},
        {"uri": "^https://mail\\.example\\.test/.*", "match": "regex"},
        {"uri": "mail.example.test"},
    ]
    answer = data(env, "propose_set_uris", item_id="login-1", uris=uris)
    assert answer["summary"] == 'Set the URIs of "Example Mail" (4)'
    [entry] = queued(env)
    assert entry["source"] == "mcp"
    assert entry["operation"]["items"] == [
        {
            "itemId": "login-1",
            "revisionDate": env.revisions()["login-1"],
            "shownUris": [{"uri": u["uri"], "match": u["match"]} for u in MAIL_URIS],
            "uris": [
                {"uri": "https://webmail.example.test", "match": 1},
                {"uri": "https://new.example.test/login", "match": 5},
                {"uri": "^https://mail\\.example\\.test/.*", "match": 4},
                {"uri": "mail.example.test", "match": None},
            ],
        }
    ]
    [row] = entry["diff"]
    assert row["column"] == "login.uris"
    assert row["before"] == ["https://mail.example.test", "https://webmail.example.test"]
    assert row["after"] == [u["uri"] for u in uris]
    assert row["uris"] == {
        "before": [
            {"uri": "https://mail.example.test", "match": None},
            {"uri": "https://webmail.example.test", "match": "exact"},
        ],
        "after": [{"match": None, **u} for u in uris],
    }
    assert env.item("login-1")["login"]["uris"] == MAIL_URIS
    response = env.client.post("/api/queue/approve", json={"ids": [entry["id"]]})
    env.settle()
    assert response.json()["applied"] == [entry["id"]], response.text
    assert env.item("login-1")["login"]["uris"] == [
        {"match": 1, "uri": "https://webmail.example.test"},
        {"match": 5, "uri": "https://new.example.test/login"},
        {"match": 4, "uri": "^https://mail\\.example\\.test/.*"},
        {"match": None, "uri": "mail.example.test"},
    ]
    [row] = env.change_log()
    assert row["columns"] == ["login.uris"]
    shown = data(env, "get_item", item_id="login-1")["values"]["login.uris"]
    assert [u["match"] for u in shown] == ["host", "never", "regex", None]


@pytest.mark.parametrize(
    ("uris", "problem"),
    [
        ([{"uri": "https://a.example.test", "match": "fuzzy"}], "match"),
        ([{"uri": "https://a.example.test"}, {"uri": " "}], "URI 2 is empty"),
        ([{"uri": "^https://(a", "match": "regex"}], "URI 1 is not a valid regular expression"),
    ],
)
def test_propose_set_uris_refuses_a_bad_uri_at_once(
    env: AppEnv, uris: list[dict[str, Any]], problem: str
):
    enable(env)
    message = error(env, "propose_set_uris", item_id="login-1", uris=uris)
    assert problem in message
    assert queued(env) == []


def test_propose_set_uris_refuses_an_item_that_is_no_login(env: AppEnv):
    enable(env)
    message = error(env, "propose_set_uris", item_id="card-1", uris=[{"uri": "https://a.test"}])
    assert "only a login has URIs" in message
    assert queued(env) == []


def test_a_uri_equal_to_a_secret_is_refused_like_any_guess(env: AppEnv, tmp_path: Path):
    enable(env)
    guess = "pw-login-secret-e0e7"
    right = call(env, "propose_set_uris", item_id="login-1", uris=[{"uri": guess}])
    wrong = call(env, "propose_set_uris", item_id="login-1", uris=[{"uri": "pw-wrong-0000"}])
    assert [right.is_error, wrong.is_error] == [False, False]
    answers = [json.loads(text(right)), json.loads(text(wrong))]
    assert [a["summary"] for a in answers] == ['Set the URIs of "Example Mail" (1)'] * 2
    entries = {e["id"]: e for e in queued(env)}
    refused = entries[answers[0]["entryId"]]
    assert [refused["status"], entries[answers[1]["entryId"]]["status"]] == [
        "rejected",
        "pending",
    ]
    assert refused["summary"] == (
        'Refused proposal for "Example Mail": it would put a secret value of the item into'
        " login.uris"
    )
    assert refused["operation"]["items"][0]["uris"] == {"masked": True}
    assert refused["diff"][0]["after"] == {"masked": True}
    leaked = guess in json.dumps(refused)
    assert not leaked
    raw = b"".join(p.read_bytes() for p in tmp_path.glob(f"{DB_FILE}*"))
    in_sqlite = guess.encode() in raw
    assert not in_sqlite


def test_propose_set_uri_match_changes_only_the_matching_uris(env: AppEnv):
    enable(env)
    answer = data(
        env,
        "propose_set_uri_match",
        item_ids=["login-1", "login-2", "card-1"],
        match="host",
        uri_pattern="*WEBMAIL*",
    )
    assert answer["summary"] == "Set URI match host on 1 URI of 1 item"
    [entry] = queued(env)
    [item] = entry["operation"]["items"]
    assert item["itemId"] == "login-1"
    assert item["uris"] == [
        {"uri": "https://mail.example.test", "match": None},
        {"uri": "https://webmail.example.test", "match": 1},
    ]
    every = data(env, "propose_set_uri_match", item_ids=["login-1"], match=None)
    assert every["summary"] == "Set URI match default on 1 URI of 1 item"
    regex = data(env, "propose_set_uri_match", item_ids=["login-1"], match="exact")
    assert regex["summary"] == "Set URI match exact on 1 URI of 1 item"


def test_propose_set_uri_match_with_nothing_to_change_is_an_error(env: AppEnv):
    enable(env)
    message = error(
        env,
        "propose_set_uri_match",
        item_ids=["login-1", "login-2"],
        match="exact",
        uri_pattern="*webmail*",
    )
    assert "no URI of these items would change" in message
    assert "nothing" in error(env, "propose_set_uri_match", item_ids=["card-1"], match="host")
    assert queued(env) == []


def test_uris_changed_since_the_proposal_fail_it_as_a_conflict(env: AppEnv):
    enable(env)
    proposal = data(env, "propose_set_uri_match", item_ids=["login-1"], match="never")
    grid = {
        "kind": "setLeaf",
        "itemId": "login-1",
        "revisionDate": env.revisions()["login-1"],
        "column": "login.uris",
        "value": ["https://mail.example.test"],
    }
    assert env.client.post("/api/queue", json={"operations": [grid]}).status_code == 201
    assert env.client.post("/api/queue/commit").status_code == 200
    env.settle()
    response = env.client.post("/api/queue/approve", json={"ids": [proposal["entryId"]]})
    env.settle()
    assert response.json()["failed"] == [proposal["entryId"]]
    entry = next(e for e in queued(env) if e["id"] == proposal["entryId"])
    assert entry["result"]["error"] == "conflicts"
    assert [c["column"] for c in entry["result"]["conflicts"]] == ["login.uris"]
    assert env.item("login-1")["login"]["uris"] == [
        {"match": None, "uri": "https://mail.example.test"}
    ]


def test_a_right_and_a_wrong_secret_guess_get_the_same_answer(
    env: AppEnv, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    enable(env)
    caplog.set_level(logging.DEBUG)
    name = env.item("login-1")["name"]
    right = call(
        env,
        "propose_rename_item",
        item_id="login-1",
        name="pw-login-secret-e0e7",
        reason="guess-reason-a",
    )
    wrong = call(env, "propose_rename_item", item_id="login-1", name="pw-login-wrong-0000")
    assert not right.is_error, text(right)
    assert not wrong.is_error, text(wrong)
    answers = [json.loads(text(right)), json.loads(text(wrong))]
    pattern = re.compile(rf'Rename "{re.escape(name)}" to "[^"]+"(\. Reason: .+)?')
    for answer in answers:
        assert set(answer) == {"entryId", "summary"}
        assert isinstance(answer["entryId"], int)
        assert pattern.fullmatch(answer["summary"]), answer["summary"]
    assert answers[0]["summary"] == (
        f'Rename "{name}" to "pw-login-secret-e0e7". Reason: guess-reason-a'
    )
    entries = {e["id"]: e for e in queued(env)}
    assert entries[answers[0]["entryId"]]["status"] == "rejected"
    assert entries[answers[1]["entryId"]]["status"] == "pending"
    assert len(entries) == 2
    assert_no_secret(json.dumps(entries[answers[0]["entryId"]]))
    assert "guess-reason-a" not in json.dumps(entries)
    raw = b"".join(p.read_bytes() for p in tmp_path.glob(f"{DB_FILE}*"))
    assert raw
    for secret in SECRETS:
        assert_absent(secret.encode(), raw, "the stored bytes")
    assert_no_secret(caplog.text)


def test_a_proposal_equal_to_a_queued_secret_is_stored_refused_like_any_guess(
    env: AppEnv, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    enable(env)
    caplog.set_level(logging.DEBUG)
    typed = "queued-ui-secret-6060"
    password = {
        "kind": "setLeaf",
        "itemId": "login-1",
        "revisionDate": env.item("login-1")["revisionDate"],
        "column": "login.password",
        "value": typed,
    }
    response = env.client.post("/api/queue", json={"operations": [password]})
    assert response.status_code == 201
    right = call(env, "propose_rename_item", item_id="login-1", name=typed)
    wrong = call(env, "propose_rename_item", item_id="login-1", name="queued-ui-wrong-0000")
    into_folder = call(env, "propose_move_to_folder", item_ids=["login-1"], folder=typed)
    other_folder = call(env, "propose_move_to_folder", item_ids=["login-1"], folder="Other")
    answers = [right, wrong, into_folder, other_folder]
    assert [a.is_error for a in answers] == [False] * 4
    shapes = [set(json.loads(text(a))) for a in answers]
    assert shapes == [{"entryId", "summary"}] * 4
    entries = {e["id"]: e for e in queued(env)}
    ids = [json.loads(text(a))["entryId"] for a in answers]
    assert [entries[i]["status"] for i in ids] == ["rejected", "pending", "rejected", "pending"]
    name = env.item("login-1")["name"]
    assert entries[ids[2]]["summary"] == (
        f'Refused proposal for "{name}": it would put a secret value of the item into folder'
    )
    assert entries[ids[2]]["operation"]["folderName"] == {"masked": True}
    leaked = typed in json.dumps(entries)
    assert not leaked
    raw = b"".join(p.read_bytes() for p in tmp_path.glob(f"{DB_FILE}*"))
    assert raw
    in_sqlite = typed.encode() in raw
    assert not in_sqlite
    in_log = typed in caplog.text
    assert not in_log


def test_propose_create_item_queues_the_operation(env: AppEnv):
    enable(env)
    answer = data(
        env,
        "propose_create_item",
        name="Forge",
        type="login",
        folder="Servers",
        organization="Family",
        collections=["shared"],
        values={"login.username": "user", "login.uris": ["https://forge.example.test"]},
        generate_password=True,
    )
    [entry] = queued(env)
    assert answer == {"entryId": entry["id"], "summary": entry["summary"]}
    assert entry["summary"] == 'Create login "Forge" with a generated password'
    assert entry["source"] == "mcp"
    assert entry["operation"] == {
        "kind": "createItem",
        "name": "Forge",
        "type": 1,
        "template": None,
        "folderName": "Servers",
        "organizationName": "Family",
        "collectionNames": ["shared"],
        "values": {"login.username": "user", "login.uris": ["https://forge.example.test"]},
        "secretValues": {},
        "generatePassword": True,
        "generateColumns": [],
    }
    assert ("login.password", "(generated)") in [(r["column"], r["after"]) for r in entry["diff"]]
    ## a proposal generates nothing and writes nothing
    assert env.bw.generated == []
    assert len(env.bw.created) == 1


def test_propose_create_item_refuses_a_secret_value_and_an_unknown_type(env: AppEnv):
    enable(env)
    message = error(
        env,
        "propose_create_item",
        name="Forge",
        type="login",
        values={"login.password": "pw-login-secret-e0e7"},
    )
    assert message.endswith(
        "refused: login.password is secret: a new item never takes a secret value, fill it in"
        " the grid after it is created"
    )
    assert_no_secret(message)
    message = error(env, "propose_create_item", name="Forge", type="router")
    assert "unknown type 'router'" in message
    assert "login, note, card, identity, sshKey" in message
    message = error(env, "propose_create_item", name="Forge")
    assert "exactly one of type and template" in message
    assert queued(env) == []


def test_propose_convert_item_queues_the_operation(env: AppEnv):
    enable(env)
    created = len(env.bw.created)
    answer = data(
        env,
        "propose_convert_item",
        item_id="note-1",
        type="sshKey",
        mapping={"sshKey.privateKey": "notes"},
    )
    [entry] = queued(env)
    assert answer == {"entryId": entry["id"], "summary": entry["summary"]}
    assert entry["summary"] == 'Convert "Backup disk" to SSH key (notes to sshKey.privateKey)'
    assert entry["operation"] == {
        "kind": "convertItem",
        "itemId": "note-1",
        "revisionDate": env.revisions()["note-1"],
        "newType": 5,
        "mapping": {"sshKey.privateKey": "notes"},
    }
    assert_no_secret(json.dumps(entry))
    assert len(env.bw.created) == created


def test_propose_convert_item_without_a_mapping(env: AppEnv):
    enable(env)
    data(env, "propose_convert_item", item_id="note-1", type="login")
    [entry] = queued(env)
    assert entry["summary"] == 'Convert "Backup disk" to login'
    assert entry["operation"]["mapping"] == {}
    assert entry["operation"]["newType"] == 1


def test_propose_convert_item_refuses_what_cannot_be_converted(env: AppEnv):
    enable(env)
    message = error(env, "propose_convert_item", item_id="note-1", type="router")
    assert "unknown type 'router'" in message
    assert "sshKey" in message
    message = error(env, "propose_convert_item", item_id="no-such-item", type="login")
    assert "no item" in message
    message = error(env, "propose_convert_item", item_id="note-1", type="secureNote")
    assert "refused" in message
    message = error(
        env,
        "propose_convert_item",
        item_id="note-5",
        type="login",
        mapping={"login.username": "fields.Recovery code"},
    )
    assert "refused" in message
    assert "may not be mapped into the non-secret" in message
    assert_no_secret(message)
    assert queued(env) == []


def test_the_type_list_items_answers_is_one_the_propose_tools_take(env: AppEnv):
    enable(env)
    listed = {item["type"]: item["id"] for item in data(env, "list_items")["items"]}
    assert set(listed) == {"login", "note", "card", "identity", "sshKey"}
    for word in sorted(listed):
        if word == "sshKey":
            message = error(env, "propose_create_item", name="New key", type=word)
            assert "propose_convert_item" in message
        else:
            data(env, "propose_create_item", name=f"New {word}", type=word)
        source = "card-1" if word == "identity" else "identity-1"
        data(env, "propose_convert_item", item_id=source, type=word)
    created = [
        e["operation"]["type"] for e in queued(env) if e["operation"]["kind"] == "createItem"
    ]
    converted = [
        e["operation"]["newType"] for e in queued(env) if e["operation"]["kind"] == "convertItem"
    ]
    assert sorted(created) == [1, 2, 3, 4]
    assert sorted(converted) == [1, 2, 3, 4, 5]


def test_the_propose_tools_take_bitwardens_word_for_a_secure_note(env: AppEnv):
    enable(env)
    data(env, "propose_create_item", name="Wifi", type="secureNote")
    data(env, "propose_convert_item", item_id="card-1", type="secureNote")
    [convert, create] = sorted(queued(env), key=lambda e: e["operation"]["kind"])
    assert create["operation"]["type"] == 2
    assert create["summary"] == 'Create secure note "Wifi"'
    assert convert["operation"]["newType"] == 2
    assert convert["summary"] == 'Convert "Visa debit" to secure note'


def test_propose_convert_item_describes_the_ssh_key_way(env: AppEnv):
    tools = {tool.name: tool for tool in list_tools(env)}
    description = tools["propose_convert_item"].description or ""
    assert "the old one goes to the trash" in description
    assert "No value passes through MCP" in description
    assert "private key mapped from a column of the item" in description
    assert "only proposes the change" in description


def test_propose_create_item_refuses_an_ssh_key(env: AppEnv):
    revision = env.client.get("/api/templates").json()["revisionDate"]
    ssh_template = {"name": "Deploy key", "type": 5, "coordinates": []}
    body = {**changes(add_template(ssh_template)), "revisionDate": revision}
    assert env.client.put("/api/templates", json=body).status_code == 202
    assert env.client.post("/api/queue/commit").status_code == 200
    env.settle()
    enable(env)
    for arguments in ({"type": "sshKey"}, {"template": "Deploy key"}):
        message = error(env, "propose_create_item", name="Key", **arguments)
        assert "private key is a secret value" in message
        assert "propose_convert_item" in message
    assert [e for e in queued(env) if e["status"] == "pending"] == []


def test_propose_create_item_has_no_way_to_send_a_secret_value(env: AppEnv):
    tools = {tool.name: tool for tool in list_tools(env)}
    schema = json.dumps(tools["propose_create_item"].input_schema)
    assert "secretValues" not in schema
    assert "secret_values" not in schema


def test_the_queue_refuses_a_create_item_with_secret_values_from_mcp(env: AppEnv, tmp_path: Path):
    value = "typed-by-an-agent-8814"
    op = CreateItem(kind="createItem", name="Forge", type=1, secretValues={"login.password": value})
    queue = env.app.state.bw.queue
    with pytest.raises(InvalidOperation) as refused_error:
        check_enqueue(op, queue.cache, queue.remembered, source="mcp")
    message = str(refused_error.value)
    assert message == "login.password: the queue never takes a secret value"
    with pytest.raises(InvalidOperation) as refused_error:
        queue.enqueue(op, "a login", source="mcp")
    leaked = value in message or value in str(refused_error.value)
    assert not leaked
    assert queued(env) == []
    stored = b"".join(p.read_bytes() for p in tmp_path.rglob(f"{DB_FILE}*"))
    assert stored
    in_sqlite = value.encode() in stored
    assert not in_sqlite


def test_propose_create_item_describes_the_secret_rule(env: AppEnv):
    tools = {tool.name: tool for tool in list_tools(env)}
    description = tools["propose_create_item"].description or ""
    assert "Secret values are never taken" in description
    assert "the user fills them in the bwtidy web UI" in description
    assert "show up in the To fill in list" in description
    assert "generated by bw when the item is written" in description
    assert "nobody sees until the user reveals it" in description
    assert "only proposes the change" in description
    assert "is either left blank or generated" in description
    assert "the item shows as near its template" in description
    assert "nobody can read through MCP" in description
    assert "generate_secrets" in description
    schema = tools["propose_create_item"].input_schema["properties"]["generate_secrets"]
    assert schema["description"] == (
        "Secret columns bw generates at write time, login.password or hidden fields, which"
        " nobody can read through MCP"
    )


TWO_KEYS = {
    "name": "Two keys",
    "type": 1,
    "coordinates": [
        {"kind": "path", "path": "login.username"},
        typed_field("Generated key", 1, PIN_ID),
        typed_field("Blank key", 1, PIN_ID),
    ],
}


def test_propose_create_item_with_generated_secrets_and_a_blank_one(env: AppEnv):
    enable(env)
    store_datatypes(env, [PIN], [TWO_KEYS])
    answer = data(
        env,
        "propose_create_item",
        name="Keys",
        template="Two keys",
        values={"login.username": "user"},
        generate_secrets=["fields.Generated key"],
    )
    [entry] = queued(env)
    assert answer == {
        "entryId": entry["id"],
        "summary": 'Create "Keys" from template "Two keys" with generated values',
    }
    assert entry["operation"]["generateColumns"] == ["fields.Generated key"]
    assert entry["operation"]["generatePassword"] is False
    rows = [(r["column"], r["after"]) for r in entry["diff"]]
    assert ("fields.Generated key", "(generated)") in rows
    assert "fields.Blank key" not in [column for column, _ in rows]
    assert env.bw.generated == []

    response = env.client.post("/api/queue/approve", json={"ids": [entry["id"]]})
    assert response.json()["applied"] == [entry["id"]]
    env.settle()
    [value] = env.bw.generated
    [new] = [i for i in env.bw.items if i.get("name") == "Keys"]
    item = data(env, "get_item", item_id=new["id"])
    assert data(env, "list_items", text="Keys")["items"] == [item]
    ## near its template: the blank field is not set, the generated one set and nothing more
    assert item["template"]["match"] is None
    [score] = [s for s in item["template"]["scores"] if s["template"] == "Two keys"]
    assert score["missing"] == ["fields.Blank key"]
    assert score["filled"] == 4
    assert score["total"] == 6
    assert "formats" not in score
    answers = [
        text(call(env, tool, **arguments))
        for tool, arguments in (
            ("get_item", {"item_id": new["id"]}),
            ("list_items", {}),
            ("list_templates", {}),
        )
    ]
    leaked = value in json.dumps(answers) or value in json.dumps(queued(env))
    assert not leaked

    ## what the generated value looks like changes no answer, as long as it is set
    def looks_alike() -> list[str]:
        return [
            text(call(env, "get_item", item_id=new["id"])),
            text(call(env, "list_items", text="Keys")),
        ]

    before = looks_alike()
    field = next(f for f in env.item(new["id"])["fields"] if f["name"] == "Generated key")
    field["value"] = "x"
    env.reload()
    same = looks_alike() == before
    assert same


def test_propose_create_item_refuses_a_column_that_takes_no_generated_value(env: AppEnv):
    enable(env)
    message = error(
        env,
        "propose_create_item",
        name="Forge",
        type="card",
        generate_secrets=["card.number"],
    )
    assert message.endswith(
        "refused: a generated value goes only into login.password or a hidden field"
    )
    assert queued(env) == []
