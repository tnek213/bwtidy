"""The template routes (docs/spec.md, Templates): GET and PUT /api/templates, the template matches,
applyTemplate and the tool note they live in."""

import copy
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bwtidy.protocol import backup_name
from bwtidy.vault.tool_note import TOOL_NOTE_NAME
from tests.support.app_env import AppEnv, logged_in, sample_bw
from tests.support.datatypes_helpers import COMPUTER, MEDIA_PC, NOTE_WITH_USER, THINKPAD, WEB_LOGIN
from tests.support.fake_bw import PASSWORD, FakeBwServe
from tests.support.quiet import assert_absent
from tests.support.sample_vault import LOGIN, NOTE
from tests.support.section_changes import template_changes


def templates_bw() -> FakeBwServe:
    bw = sample_bw([LOGIN, NOTE, THINKPAD, MEDIA_PC])
    bw.attachments[("login-2", "a9")] = b"recovery"
    return bw


def tool_note(env: AppEnv) -> dict[str, Any]:
    return next(i for i in env.bw.items if i["name"] == TOOL_NOTE_NAME)


def named(env: AppEnv, name: str) -> dict[str, Any]:
    return next(i for i in env.bw.items if i["name"] == name)


def save(env: AppEnv, *raw: dict[str, Any], revision: str | None = None):
    """PUT the changes that make the templates these and wait for the commit the PUT
    started (see test_queue.py)."""
    stored = env.client.get("/api/templates").json()
    if revision is None:
        revision = stored["revisionDate"]
    body = {
        "changes": template_changes(stored["templates"], list(raw)),
        "revisionDate": revision,
    }
    response = env.client.put("/api/templates", json=body)
    if response.status_code == 202:
        assert env.client.post("/api/queue/commit").status_code == 200
    env.settle()
    return response


def apply(env: AppEnv, name: str, template: str, mapping: dict[str, str]):
    item = named(env, name)
    op = {
        "kind": "applyTemplate",
        "itemId": item["id"],
        "revisionDate": item["revisionDate"],
        "template": template,
        "mapping": mapping,
    }
    response = env.client.post("/api/operations", json={"operations": [op]})
    env.settle()
    return response


@pytest.fixture
def env(tmp_path: Path) -> Iterator[AppEnv]:
    """The login's backup is reused by the first write, as in production, so the backup counts
    below are one at login and one more after every write."""
    yield from logged_in(tmp_path, bw=templates_bw())


def test_template_routes_need_a_session(env: AppEnv):
    env.client.cookies.clear()
    assert env.client.get("/api/templates").status_code == 401
    assert env.client.put("/api/templates", json={}).status_code == 401
    assert env.client.get("/api/template-matches").status_code == 401


def test_a_fresh_tool_note_holds_no_templates(env: AppEnv):
    body = env.client.get("/api/templates").json()
    note = tool_note(env)
    assert body == {
        "templates": [],
        "revisionDate": note["revisionDate"],
        "toolNoteId": note["id"],
        "toolNoteName": "bwtidy",
        "toolNoteExpectedName": "bwtidy",
        "saving": False,
    }
    assert env.client.get("/api/grid").json()["toolNoteId"] == note["id"]


def rename_in_bitwarden(env: AppEnv, name: str) -> dict[str, Any]:
    """Rename the tool note behind bwtidy's back, as another client would."""
    note = tool_note(env)
    note["name"] = name
    note["revisionDate"] = "2026-10-28T10:00:00.000Z"
    env.app.state.bw.session.cache.items = copy.deepcopy(env.bw.items)
    return note


def test_a_renamed_tool_note_still_holds_the_templates(env: AppEnv):
    assert save(env, COMPUTER).status_code == 202
    note = rename_in_bitwarden(env, "bwtidyK")
    body = env.client.get("/api/templates").json()
    assert body["templates"] == [COMPUTER]
    assert body["toolNoteId"] == note["id"]
    assert body["toolNoteName"] == "bwtidyK"
    assert body["revisionDate"] == note["revisionDate"]
    assert note["id"] not in env.client.get("/api/template-matches").json()
    response = save(env, COMPUTER, WEB_LOGIN)
    assert response.status_code == 202, response.text
    saved = named(env, "bwtidyK")
    assert json.loads(saved["notes"])["templates"] == [COMPUTER, WEB_LOGIN]
    assert len([i for i in env.bw.items if i.get("type") == 2 and "bwtidy" in i["name"]]) == 1


def test_rename_it_back_goes_through_the_write_path(env: AppEnv):
    note = rename_in_bitwarden(env, "bwtidyK")
    stale = {"revisionDate": "2026-01-01T00:00:00.000Z"}
    response = env.client.post("/api/tool-note/rename-back", json=stale)
    assert response.status_code == 409
    assert note["name"] == "bwtidyK"
    assert env.client.post("/api/tool-note/rename-back", json={}).status_code == 422
    response = env.client.post(
        "/api/tool-note/rename-back", json={"revisionDate": note["revisionDate"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["toolNoteName"] == "bwtidy"
    ## the rename in Bitwarden changed the vault, so the login's backup is not reused
    assert body["backup"] == backup_name(datetime(2026, 10, 26, 12, 0, 1, tzinfo=UTC))
    saved = tool_note(env)
    assert saved["id"] == note["id"]
    assert body["revisionDate"] == saved["revisionDate"]
    assert [f["name"] for f in saved["fields"]] == ["backup-password"]
    [entry] = env.client.get("/api/change-log").json()
    assert (entry["operationKind"], entry["itemIds"]) == ("setLeaf", [note["id"]])
    assert env.client.get("/api/templates").json()["toolNoteName"] == "bwtidy"


def test_keep_this_name_saves_the_expected_name_through_the_write_path(env: AppEnv):
    assert save(env, COMPUTER).status_code == 202
    note = rename_in_bitwarden(env, "Pass")
    assert env.client.get("/api/templates").json()["toolNoteExpectedName"] == "bwtidy"
    logged = len(env.client.get("/api/change-log").json())
    backups = len(env.backups)
    assert env.client.post("/api/tool-note/keep-name", json={}).status_code == 422
    ## the warning showed another name than the note has now
    response = env.client.post("/api/tool-note/keep-name", json={"name": "Other"})
    assert response.status_code == 409
    assert "renamed again" in response.json()["detail"]
    assert len(env.client.get("/api/change-log").json()) == logged
    assert len(env.backups) == backups
    response = env.client.post("/api/tool-note/keep-name", json={"name": "Pass"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["toolNoteName"], body["toolNoteExpectedName"]) == ("Pass", "Pass")
    assert len(env.backups) == backups + 1
    assert body["backup"] == env.backups[-1]
    saved = named(env, "Pass")
    assert saved["id"] == note["id"]
    assert body["revisionDate"] == saved["revisionDate"]
    ## only the note section is new, the templates and the password field stay
    assert json.loads(saved["notes"]) == {
        "version": 1,
        "templates": [COMPUTER],
        "note": {"name": "Pass"},
    }
    assert [f["name"] for f in saved["fields"]] == ["backup-password"]
    entries = env.client.get("/api/change-log").json()
    assert (entries[0]["operationKind"], entries[0]["itemIds"]) == (
        "setToolNoteSection",
        [note["id"]],
    )
    doc = env.client.get("/api/templates").json()
    assert (doc["toolNoteName"], doc["toolNoteExpectedName"]) == ("Pass", "Pass")
    assert doc["templates"] == [COMPUTER]


def test_rename_it_back_gives_the_note_the_name_it_kept(env: AppEnv):
    rename_in_bitwarden(env, "Pass")
    assert env.client.post("/api/tool-note/keep-name", json={"name": "Pass"}).status_code == 200
    note = rename_in_bitwarden_again(env, "Pass", "Other")
    doc = env.client.get("/api/templates").json()
    assert (doc["toolNoteName"], doc["toolNoteExpectedName"]) == ("Other", "Pass")
    response = env.client.post(
        "/api/tool-note/rename-back", json={"revisionDate": note["revisionDate"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["toolNoteName"], body["toolNoteExpectedName"]) == ("Pass", "Pass")
    assert named(env, "Pass")["id"] == note["id"]


def rename_in_bitwarden_again(env: AppEnv, name: str, to: str) -> dict[str, Any]:
    """Rename the tool note named name behind bwtidy's back."""
    note = named(env, name)
    note["name"] = to
    note["revisionDate"] = "2026-10-28T11:00:00.000Z"
    env.app.state.bw.session.cache.items = copy.deepcopy(env.bw.items)
    return note


def test_a_grid_rename_of_the_tool_note_is_refused(env: AppEnv):
    note = tool_note(env)
    op = {
        "kind": "setLeaf",
        "itemId": note["id"],
        "revisionDate": note["revisionDate"],
        "column": "name",
        "value": "bwtidyK",
    }
    response = env.client.post("/api/operations", json={"operations": [op]})
    assert response.status_code == 422
    assert "edit the templates on the Types page" in response.json()["detail"]
    assert note["name"] == "bwtidy"
    ## only the login's backup
    assert len(env.backups) == 1


def test_a_login_after_a_rename_makes_no_second_note(env: AppEnv):
    note = rename_in_bitwarden(env, "bwtidyK")
    assert env.client.delete("/api/session").status_code == 204
    assert env.client.post("/api/session", json={"password": PASSWORD}).status_code == 200
    notes = [i for i in env.bw.items if i.get("type") == 2 and i["name"].startswith("bwtidy")]
    assert [n["id"] for n in notes] == [note["id"]]
    assert env.client.get("/api/templates").json()["toolNoteName"] == "bwtidyK"


def test_the_remembered_id_survives_a_restart(env: AppEnv, tmp_path: Path):
    note = rename_in_bitwarden(env, "bwtidyK")
    ## a second app on the same data directory, as after a restart of bwtidy
    restarted = AppEnv(tmp_path, bw=templates_bw())
    restarted.bw.items = env.bw.items
    with restarted:
        assert restarted.login().status_code == 200
        assert restarted.client.get("/api/templates").json()["toolNoteId"] == note["id"]
    assert restarted.bw.created == []


def test_put_queues_the_templates_and_the_commit_writes_them_with_a_backup(env: AppEnv):
    response = save(env, COMPUTER, WEB_LOGIN)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["templates"] == [COMPUTER, WEB_LOGIN]
    [queued] = env.client.get("/api/queue", params={"status": "applied"}).json()
    assert queued["id"] == body["entryId"]
    assert (queued["source"], queued["summary"]) == ("ui", "Save the templates")
    ## the backup taken after the login, reused
    assert queued["result"] == {"backup": backup_name(datetime(2026, 10, 26, 12, tzinfo=UTC))}
    note = tool_note(env)
    assert json.loads(note["notes"]) == {"version": 1, "templates": [COMPUTER, WEB_LOGIN]}
    ## the backup-password field survives the write
    assert [f["name"] for f in note["fields"]] == ["backup-password"]
    stored = env.client.get("/api/templates").json()
    assert stored["templates"] == [COMPUTER, WEB_LOGIN]
    assert stored["saving"] is False
    assert stored["revisionDate"] == note["revisionDate"]
    [entry] = env.client.get("/api/change-log").json()
    assert (entry["operationKind"], entry["itemIds"]) == ("setToolNoteSection", [note["id"]])
    assert entry["source"] == "ui"


def test_a_description_is_saved_and_served_when_set(env: AppEnv):
    described = {**COMPUTER, "description": "A laptop or desktop"}
    assert save(env, described, WEB_LOGIN).status_code == 202
    assert json.loads(tool_note(env)["notes"])["templates"] == [described, WEB_LOGIN]
    assert env.client.get("/api/templates").json()["templates"] == [described, WEB_LOGIN]
    too_long = {**COMPUTER, "description": "x" * 501}
    response = save(env, too_long, WEB_LOGIN)
    assert response.status_code == 422
    assert "A description holds at most 500 characters" in response.json()["detail"]


def test_put_refuses_invalid_templates_and_ignores_the_revision(env: AppEnv):
    response = save(env, COMPUTER, COMPUTER)
    assert response.status_code == 422
    assert "already a template named 'Computer'" in response.json()["detail"]
    assert save(env, COMPUTER).status_code == 202
    ## the save replaces only its section of the note as it is then, a stale revision is fine
    assert save(env, WEB_LOGIN, revision="2026-01-01T00:00:00.000Z").status_code == 202
    assert json.loads(tool_note(env)["notes"])["templates"] == [WEB_LOGIN]
    ## the whole list, as the route took it before the changes came, is refused
    response = env.client.put("/api/templates", json={"templates": [COMPUTER]})
    assert response.status_code == 422
    assert "saved as changes" in response.json()["detail"]
    for body in ({}, {"changes": []}, {"changes": [{"op": "removeItem", "itemId": "a"}]}):
        assert env.client.put("/api/templates", json=body).status_code == 422
    assert json.loads(tool_note(env)["notes"])["templates"] == [WEB_LOGIN]


def test_notes_edited_by_hand_into_garbage_read_as_no_templates(env: AppEnv):
    tool_note(env)["notes"] = "not json"
    env.app.state.bw.session.cache.items = copy.deepcopy(env.bw.items)
    body = env.client.get("/api/templates").json()
    assert body["templates"] == []
    assert "not JSON" in body["error"]
    matches_body = env.client.get("/api/template-matches").json()
    assert all(m == {"match": None, "scores": []} for m in matches_body.values())
    ## a save starts the notes over
    assert save(env, COMPUTER).status_code == 202
    assert json.loads(tool_note(env)["notes"]) == {"version": 1, "templates": [COMPUTER]}


def test_template_matches_lists_every_item_but_the_tool_note(env: AppEnv):
    assert save(env, NOTE_WITH_USER, COMPUTER).status_code == 202
    body = env.client.get("/api/template-matches").json()
    assert set(body) == {"login-1", "note-1", "login-2", "note-3"}
    assert body["note-3"] == {
        "match": "Note with user",
        "scores": [
            {
                "template": "Note with user",
                "score": 1.0,
                "typeMatches": True,
                "filled": 2,
                "total": 2,
                "missing": [],
            },
            {
                "template": "Computer",
                "score": 0.6,
                "typeMatches": True,
                "filled": 3,
                "total": 5,
                "missing": ["fields.BIOS password", "fields.disk encryption"],
            },
        ],
    }
    assert body["login-2"]["match"] is None
    assert body["login-2"]["scores"][1]["typeMatches"] is False


def test_apply_template_on_the_same_type_puts_the_item(env: AppEnv):
    assert save(env, COMPUTER).status_code == 202
    response = apply(
        env, "Media PC", "Computer", {"fields.disk encryption": "fields.dm-crypt password"}
    )
    assert response.status_code == 200, response.text
    [(item_id, body)] = [p for p in env.bw.put if p[0] == "note-3"]
    assert [(f["name"], f["type"], f["value"]) for f in body["fields"]] == [
        ("user", 0, "media"),
        ("user password", 1, "media-secret-41"),
        ("Serial", 0, "SN-9"),
        ("disk encryption", 1, "disk-secret-42"),
        ("BIOS password", 1, None),
    ]
    assert env.bw.posted[1:] == []
    assert env.client.get("/api/change-log").json()[0]["operationKind"] == "applyTemplate"
    ## the mapped pair, and the template's field the item lacked, added empty
    assert env.client.get("/api/change-log").json()[0]["columns"] == [
        "fields.dm-crypt password",
        "fields.disk encryption",
        "fields.BIOS password",
    ]


def test_apply_template_with_another_type_converts_and_trashes_the_old(env: AppEnv):
    assert save(env, COMPUTER).status_code == 202
    response = apply(
        env,
        "ThinkPad",
        "Computer",
        {
            "fields.user": "fields.User",
            "fields.user password": "login.password",
            "fields.disk encryption": "fields.LUKS",
        },
    )
    assert response.status_code == 200, response.text
    new = named(env, "ThinkPad")
    assert new["id"] != "login-2"
    assert new["type"] == 2
    assert [(f["name"], f["type"]) for f in new["fields"]] == [
        ("BIOS Password", 1),
        ("Windows key", 0),
        ("user", 0),
        ("user password", 1),
        ("disk encryption", 1),
        ("BIOS password", 1),
    ]
    assert new["fields"][-1].get("value") is None
    assert [i["id"] for i in env.bw.trashed] == ["login-2"]
    assert env.bw.uploads == [(new["id"], "recovery.txt")]
    matches_body = env.client.get("/api/template-matches").json()
    assert matches_body[new["id"]]["scores"][0]["filled"] == 4
    ## each mapped pair, the type and the template's field the item lacked
    assert env.client.get("/api/change-log").json()[0]["columns"] == [
        "fields.User",
        "fields.user",
        "login.password",
        "fields.user password",
        "fields.LUKS",
        "fields.disk encryption",
        "type",
        "fields.BIOS password",
    ]


def test_apply_template_refusals_write_nothing(env: AppEnv):
    assert save(env, COMPUTER).status_code == 202
    response = apply(env, "ThinkPad", "Computer", {"fields.user": "login.password"})
    assert response.status_code == 422
    assert "secret login.password" in response.json()["detail"]
    assert_absent("user-secret-33", response.text, "the response")
    response = apply(env, "ThinkPad", "Server", {})
    assert response.status_code == 422
    assert "no template named 'Server'" in response.json()["detail"]
    response = apply(env, TOOL_NOTE_NAME, "Computer", {})
    assert response.status_code == 422
    assert len(env.backups) == 2
    assert [p[0] for p in env.bw.put] == [tool_note(env)["id"]]
