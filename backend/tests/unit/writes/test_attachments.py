"""Renaming an attachment against the fake bw serve: download, upload under the new name, delete
the old one, the checks before it, the failures that leave exactly one copy, the queue's
conflict, the restore plan of its change log row and the MCP tool.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import bwtidy.backups.reader as reader
from tests.support.app_env import MCP_URL, AppEnv
from tests.support.mcp_helpers import assert_no_secret, call, data, enable, error, queued, text
from tests.support.quiet import assert_absent
from tests.support.writes_helpers import plain_decrypt

## 21 bytes, which the fake lists as 81 like the a1 of the sample vault
CONTENTS = b"attachment-bytes-test"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppEnv]:
    """An AppEnv whose backups are real zips of the fake vault, so a restore can read them."""
    monkeypatch.setattr(reader, "decrypt_backup", plain_decrypt)
    env = AppEnv(tmp_path, base_url=MCP_URL, zips=True)
    env.bw.attachments[("login-1", "a1")] = CONTENTS
    with env:
        env.unlock()
        yield env


def rename(env: AppEnv, new: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": "renameAttachment",
        "itemId": "login-1",
        "revisionDate": env.item("login-1")["revisionDate"],
        "attachmentId": "a1",
        "fileName": new,
        "shownFileName": "codes.txt",
        **extra,
    }


def add_attachment(env: AppEnv, attachment_id: str, name: str) -> None:
    """A second attachment on the test item, with its own bytes."""
    env.item("login-1")["attachments"].append({"id": attachment_id, "fileName": name, "size": "81"})
    env.bw.attachments[("login-1", attachment_id)] = b"other-bytes-1599-xxxx"
    env.reload()


def attachments(env: AppEnv) -> list[tuple[str, str]]:
    return [(a["id"], a["fileName"]) for a in env.item("login-1").get("attachments") or []]


def attachment_calls(env: AppEnv) -> list[tuple[str, str]]:
    return [r for r in env.bw.requests if "attachment" in r[1]]


def test_a_rename_downloads_uploads_under_the_new_name_and_deletes_the_old(env: AppEnv):
    response = env.post(rename(env, "recovery codes.txt"))
    assert response.status_code == 200, response.text
    assert attachment_calls(env) == [
        ("GET", "/object/attachment/a1"),
        ("POST", "/attachment"),
        ("DELETE", "/object/attachment/a1"),
    ]
    assert attachments(env) == [("up-1", "recovery codes.txt")]
    assert (env.bw.attachments[("login-1", "up-1")] == CONTENTS) is True
    assert ("login-1", "a1") not in env.bw.attachments
    ## the item itself is never PUT
    assert env.bw.put == []
    [row] = env.change_log()
    assert (row["operationKind"], row["result"], row["itemIds"]) == (
        "renameAttachment",
        "ok",
        ["login-1"],
    )
    assert row["columns"] == ["attachments"]
    assert row["attachmentIds"] == {"before": "a1", "after": "up-1"}
    grid = {r["id"]: r for r in env.client.get("/api/grid").json()["rows"]}
    assert grid["login-1"]["cells"]["attachments"] == ["recovery codes.txt"]


def test_a_failing_delete_removes_the_new_copy_and_trashes_nothing(env: AppEnv):
    env.bw.fail_at.add(("DELETE", "/object/attachment/a1"))
    response = env.post(rename(env, "recovery codes.txt"))
    assert response.status_code == 500, response.text
    assert "forced failure" in response.json()["detail"]
    assert attachments(env) == [("a1", "codes.txt")]
    assert (env.bw.attachments[("login-1", "a1")] == CONTENTS) is True
    assert ("DELETE", "/object/attachment/up-1") in env.bw.requests
    assert env.bw.trashed == []
    [row] = env.change_log()
    assert row["result"] == "failed"
    assert row["attachmentIds"] == {"before": "a1", "after": None}


def test_a_copy_of_another_size_is_deleted_again(env: AppEnv, monkeypatch: pytest.MonkeyPatch):
    import bwtidy.bw.client as bwclient

    upload = bwclient.BwClient.upload_attachment

    async def short(self: Any, item_id: str, file_name: str, contents: bytes) -> Any:
        return await upload(self, item_id, file_name, contents + b"x" * 40)

    monkeypatch.setattr(bwclient.BwClient, "upload_attachment", short)
    response = env.post(rename(env, "recovery codes.txt"))
    assert response.status_code == 500, response.text
    assert "another size" in response.json()["detail"]
    assert attachments(env) == [("a1", "codes.txt")]
    assert ("DELETE", "/object/attachment/a1") not in env.bw.requests


def test_a_failed_upload_leaves_the_attachment_as_it_was(env: AppEnv):
    env.bw.fail_at.add(("POST", "/attachment"))
    response = env.post(rename(env, "recovery codes.txt"))
    assert response.status_code == 500, response.text
    assert attachments(env) == [("a1", "codes.txt")]
    assert not any(r[0] == "DELETE" for r in attachment_calls(env))


def test_an_upload_that_answered_an_error_after_storing_the_copy_deletes_it(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    import bwtidy.bw.client as bwclient
    from bwtidy.bw.client import BwError

    upload = bwclient.BwClient.upload_attachment

    async def lost(self: Any, item_id: str, file_name: str, contents: bytes) -> Any:
        await upload(self, item_id, file_name, contents)
        raise BwError("the answer got lost")

    monkeypatch.setattr(bwclient.BwClient, "upload_attachment", lost)
    response = env.post(rename(env, "recovery codes.txt"))
    assert response.status_code == 500, response.text
    assert attachments(env) == [("a1", "codes.txt")]
    assert ("DELETE", "/object/attachment/up-1") in env.bw.requests


def test_a_delete_that_failed_late_keeps_the_copy_once_the_old_one_is_gone(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch
):
    """A delete that went through but answered an error must not take the copy too, or the
    item would end without the file."""
    import bwtidy.bw.client as bwclient
    from bwtidy.bw.client import BwError

    delete = bwclient.BwClient.delete_attachment

    async def late(self: Any, attachment_id: str, item_id: str) -> None:
        await delete(self, attachment_id, item_id)
        if attachment_id == "a1":
            raise BwError("the answer got lost")

    monkeypatch.setattr(bwclient.BwClient, "delete_attachment", late)
    response = env.post(rename(env, "recovery codes.txt"))
    assert response.status_code == 500, response.text
    assert attachments(env) == [("up-1", "recovery codes.txt")]


def test_a_rename_made_elsewhere_is_a_conflict(env: AppEnv):
    op = rename(env, "recovery codes.txt")
    env.item("login-1")["attachments"][0]["fileName"] = "renamed elsewhere.txt"
    response = env.post(op)
    assert response.status_code == 409, response.text
    [conflict] = response.json()["conflicts"]
    assert conflict["itemId"] == "login-1"
    assert conflict["column"] == "attachments"
    assert attachment_calls(env) == []


def test_a_changed_revision_date_is_a_conflict(env: AppEnv):
    response = env.post(rename(env, "recovery codes.txt", revisionDate="2026-01-01T00:00:00.000Z"))
    assert response.status_code == 409, response.text
    assert attachment_calls(env) == []


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"fileName": ""}, "the new file name is empty"),
        ({"fileName": "  "}, "the new file name is empty"),
        ({"fileName": "a/b.txt"}, "a file name may not hold a slash or a backslash"),
        ({"fileName": "a\\b.txt"}, "a file name may not hold a slash or a backslash"),
        ({"fileName": "codes.txt"}, "the attachment has that name already"),
        ({"attachmentId": "nope"}, "the item has no attachment with the id nope"),
        ({"fileName": "notes.txt"}, 'the item has another attachment named "notes.txt"'),
        ({"fileName": "NOTES.TXT"}, 'the item has another attachment named "notes.txt"'),
    ],
)
def test_refused_renames_write_nothing(env: AppEnv, change: dict[str, Any], reason: str):
    add_attachment(env, "a2", "notes.txt")
    response = env.post({**rename(env, "new.txt"), **change})
    assert response.status_code == 422, response.text
    assert reason in response.json()["detail"]
    assert attachment_calls(env) == []
    assert env.change_log() == []


def test_changing_only_the_case_of_its_own_name_is_written(env: AppEnv):
    add_attachment(env, "a2", "notes.txt")
    response = env.post(rename(env, "Codes.TXT"))
    assert response.status_code == 200, response.text
    assert attachments(env) == [("a2", "notes.txt"), ("up-1", "Codes.TXT")]


def test_two_renames_to_one_name_in_one_request_are_refused_at_once(env: AppEnv):
    """enqueue_ui checks each operation with the request's earlier ones planned (earlier)."""
    add_attachment(env, "a2", "notes.txt")
    first = rename(env, "same.txt")
    second = rename(env, "SAME.txt", attachmentId="a2", shownFileName="notes.txt")
    response = env.client.post("/api/queue", json={"operations": [first, second]})
    assert response.status_code == 422, response.text
    assert 'the item has another attachment named "same.txt"' in response.json()["detail"]
    assert queued(env) == []
    assert attachment_calls(env) == []
    assert attachments(env) == [("a1", "codes.txt"), ("a2", "notes.txt")]


def test_two_queued_renames_to_one_name_fail_the_second_entry_at_the_write(env: AppEnv):
    add_attachment(env, "a2", "notes.txt")
    first = rename(env, "same.txt")
    second = rename(env, "SAME.txt", attachmentId="a2", shownFileName="notes.txt")
    for op in (first, second):
        assert env.client.post("/api/queue", json={"operations": [op]}).status_code == 201
    entries = queued(env)
    approved = env.client.post("/api/queue/approve", json={"ids": [e["id"] for e in entries]})
    env.settle()
    assert approved.status_code == 200, approved.text
    assert approved.json()["applied"] == [entries[0]["id"]]
    assert approved.json()["failed"] == [entries[1]["id"]]
    failed = next(e for e in queued(env) if e["id"] == entries[1]["id"])
    assert failed["result"]["detail"] == 'the item has another attachment named "same.txt"'
    assert sorted(name for _, name in attachments(env)) == ["notes.txt", "same.txt"]
    assert len(env.change_log()) == 1


def test_the_tool_note_is_refused(env: AppEnv):
    note = next(i for i in env.bw.items if i.get("name") == "bwtidy")
    op = {**rename(env, "x.txt"), "itemId": note["id"], "revisionDate": note["revisionDate"]}
    response = env.post(op)
    assert response.status_code == 422, response.text
    assert "managed by bwtidy" in response.json()["detail"]


@pytest.mark.parametrize("new_revisions", [False, True])
def test_an_edit_after_the_rename_does_not_conflict_with_it(env: AppEnv, new_revisions: bool):
    """The queue's later edit carries the revisionDate from before the rename, also on a server
    that gives the item a new one for the upload and for the delete."""
    env.bw.attachment_revisions = new_revisions
    revision = env.item("login-1")["revisionDate"]
    assert env.post(rename(env, "recovery codes.txt")).status_code == 200
    edit = {
        "kind": "setLeaf",
        "itemId": "login-1",
        "revisionDate": revision,
        "column": "notes",
        "value": "after the rename",
    }
    response = env.post(edit)
    assert response.status_code == 200, response.text
    assert attachments(env) == [("up-1", "recovery codes.txt")]
    assert (env.item("login-1")["revisionDate"] != revision) is True


def test_the_queue_shows_the_old_and_new_name_and_a_conflict_fails_only_the_rename(env: AppEnv):
    response = env.client.post("/api/queue", json={"operations": [rename(env, "new.txt")]})
    assert response.status_code == 201, response.text
    [entry] = queued(env)
    assert entry["summary"] == 'Rename attachment "codes.txt" of "Example Mail" to "new.txt"'
    assert [(r["itemId"], r["column"], r["before"], r["after"]) for r in entry["diff"]] == [
        ("login-1", "attachments", ["codes.txt"], ["new.txt"])
    ]
    env.item("login-1")["attachments"][0]["fileName"] = "renamed elsewhere.txt"
    approved = env.client.post("/api/queue/approve", json={"ids": [entry["id"]]})
    env.settle()
    assert approved.status_code == 200, approved.text
    assert approved.json()["failed"] == [entry["id"]]
    [failed] = queued(env)
    assert failed["result"]["error"] == "conflicts"
    assert failed["result"]["conflicts"][0]["column"] == "attachments"
    assert attachments(env) == [("a1", "renamed elsewhere.txt")]


def test_restoring_the_row_renames_the_copy_back(env: AppEnv):
    assert env.post(rename(env, "recovery codes.txt")).status_code == 200
    [row] = env.change_log()
    plan = env.client.get("/api/restore-plan", params={"changeLogId": row["id"]})
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["operation"] == {
        "kind": "renameAttachment",
        "itemId": "login-1",
        "revisionDate": env.item("login-1")["revisionDate"],
        "attachmentId": "up-1",
        "fileName": "codes.txt",
        "shownFileName": "recovery codes.txt",
    }
    assert body["summary"] == (
        'Rename attachment "recovery codes.txt" of "Example Mail" to "codes.txt"'
    )
    assert body["items"] == [
        {
            "itemId": "login-1",
            "itemName": "Example Mail",
            "action": "renameAttachment",
            "text": 'rename the attachment "recovery codes.txt" back to "codes.txt"',
        }
    ]
    assert env.post(body["operation"]).status_code == 200
    assert attachments(env) == [("up-2", "codes.txt")]
    assert (env.bw.attachments[("login-1", "up-2")] == CONTENTS) is True
    ## renamed back already, so there is nothing to restore
    again = env.client.get("/api/restore-plan", params={"changeLogId": row["id"]})
    assert again.status_code == 422
    assert "is gone" in again.json()["detail"]


def test_restoring_is_refused_when_another_attachment_has_the_old_name_by_then(env: AppEnv):
    assert env.post(rename(env, "recovery codes.txt")).status_code == 200
    [row] = env.change_log()
    add_attachment(env, "a2", "Codes.txt")
    plan = env.client.get("/api/restore-plan", params={"changeLogId": row["id"]})
    assert plan.status_code == 422, plan.text
    assert 'the item has another attachment named "Codes.txt"' in plan.json()["detail"]


def test_the_mcp_tool_queues_one_entry_and_never_answers_the_contents(env: AppEnv):
    enable(env)
    answer = data(
        env,
        "propose_rename_attachment",
        item_id="login-1",
        attachment_id="a1",
        new_name="recovery codes.txt",
        reason="Says what it is",
    )
    [entry] = queued(env)
    summary = 'Rename attachment "codes.txt" of "Example Mail" to "recovery codes.txt"'
    assert answer == {"entryId": entry["id"], "summary": f"{summary}. Reason: Says what it is"}
    assert entry["source"] == "mcp"
    assert entry["operation"]["shownFileName"] == "codes.txt"
    ## nothing was downloaded, and no answer holds the contents
    assert attachment_calls(env) == []
    assert_absent(CONTENTS.decode(), json.dumps(entry), "the entry")
    assert_no_secret(json.dumps(entry))
    approved = env.client.post("/api/queue/approve", json={"ids": [entry["id"]]})
    env.settle()
    assert approved.json()["applied"] == [entry["id"]]
    item = data(env, "get_item", item_id="login-1")
    assert item["attachments"] == [{"id": "up-1", "fileName": "recovery codes.txt", "size": 81}]
    for tool in ("get_item", "list_items", "list_proposals"):
        answered = text(call(env, tool, item_id="login-1"))
        assert_absent(CONTENTS.decode(), answered, "the answer")


def test_the_mcp_tool_refuses_an_unknown_attachment_and_a_bad_name(env: AppEnv):
    enable(env)
    unknown = error(
        env, "propose_rename_attachment", item_id="login-1", attachment_id="nope", new_name="x"
    )
    assert "no attachment with the id 'nope'" in unknown
    slash = error(
        env, "propose_rename_attachment", item_id="login-1", attachment_id="a1", new_name="a/b"
    )
    assert "refused: a file name may not hold a slash or a backslash" in slash
    assert queued(env) == []
    add_attachment(env, "a2", "notes.txt")
    taken = error(
        env,
        "propose_rename_attachment",
        item_id="login-1",
        attachment_id="a1",
        new_name="NOTES.txt",
    )
    assert 'refused: the item has another attachment named "notes.txt"' in taken
    assert queued(env) == []
