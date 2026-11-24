"""A tool note section as the saves under way leave it, against the fake bw serve: a save shows
as saving until its entry is applied, saves built on each other are not refused, and the diff,
the routes and the to-fill list show the one fold of queue/sections.py."""

import json
from typing import Any

import httpx2 as httpx
import pytest

from bwtidy.queue.to_fill import TO_FILL_SUMMARY
from bwtidy.sections.to_fill import AddColumn
from tests.support.app_env import AppEnv
from tests.support.queue_helpers import (
    COMPUTER,
    auto_commit,
    before_first,
    before_the_reload,
    commit_now,
    enqueue_ui,
    entry,
    listing,
    vault_unreachable,
)
from tests.support.section_changes import (
    add_datatype,
    add_template,
    changes,
    remove_datatype,
    remove_template,
)


def test_the_templates_save_is_queued_and_shows_as_saving(env: AppEnv):
    stored = env.client.get("/api/templates").json()
    assert stored["templates"] == []
    assert stored["saving"] is False
    ## the commit the save starts cannot run yet, so the entry stays visible as saving
    vault_unreachable(env, True)
    body = {**changes(add_template(COMPUTER)), "revisionDate": stored["revisionDate"]}
    response = env.client.put("/api/templates", json=body)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["templates"] == [COMPUTER]
    assert commit_now(env).status_code == 502
    saving = env.client.get("/api/templates").json()
    assert saving["templates"] == [COMPUTER]
    assert saving["saving"] is True
    assert saving["revisionDate"] == stored["revisionDate"]
    queued = entry(env, body["entryId"])
    assert (queued["source"], queued["summary"]) == ("ui", "Save the templates")
    assert queued["status"] == "pending"
    assert queued["diff"][0]["itemId"] == env.tool_note_id
    vault_unreachable(env, False)
    assert commit_now(env).json()["applied"] == [body["entryId"]]
    saved = env.client.get("/api/templates").json()
    assert saved["templates"] == [COMPUTER]
    assert saved["saving"] is False
    assert saved["revisionDate"] != stored["revisionDate"]
    ## the save replaces only its section of the note as it is then, so any revisionDate does
    body = {**changes(remove_template("Computer")), "revisionDate": "2026-01-01T00:00:00.000Z"}
    response = env.client.put("/api/templates", json=body)
    assert response.status_code == 202, response.text
    assert commit_now(env).json()["failed"] == []
    assert env.client.get("/api/templates").json()["templates"] == []
    ## other UI ops on the note stay refused, only the templates save may touch it
    response = enqueue_ui(env, env.set_leaf(env.tool_note_id, "notes", "{}"))
    assert response.status_code == 422


PIN = {"name": "PIN", "pattern": r"\d{4,8}"}


PASSPHRASE = {
    "type": "passphrase",
    "words": 4,
    "separator": "-",
    "capitalize": False,
    "includeNumber": False,
}


## per section: the save, its body and whether a GET of the section shows it saved
SECTION_SAVES = {
    "templates": (
        "/api/templates",
        changes(add_template(COMPUTER)),
        lambda doc: doc["templates"] == [COMPUTER],
    ),
    "datatypes": (
        "/api/datatypes",
        changes(add_datatype(PIN)),
        lambda doc: [d["name"] for d in doc["datatypes"]] == ["PIN"],
    ),
    "generator": (
        "/api/generator/default",
        PASSPHRASE,
        lambda doc: doc["default"]["type"] == "passphrase",
    ),
}


@pytest.mark.parametrize("section", list(SECTION_SAVES))
def test_a_section_save_shows_as_saved_once_its_entry_is_applied(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch, section: str
):
    path, body, shows_saved = SECTION_SAVES[section]
    read = path.removesuffix("/default")
    seen: dict[str, Any] = {}

    async def step(client: httpx.AsyncClient) -> None:
        seen["statuses"] = [row["status"] for row in env.queue.db.queue_entries()]
        seen["doc"] = (await client.get(read)).json()

    before_the_reload(env, monkeypatch, step)
    response = env.client.put(path, json=body)
    assert response.status_code == 202, response.text
    assert commit_now(env).status_code == 200
    assert seen["statuses"] == ["applied"]
    assert seen["doc"]["saving"] is False
    assert shows_saved(seen["doc"])


## per section: its path, its key, an entry, its addition and its deletion
REPEATS = {
    "templates": (
        "/api/templates",
        "templates",
        COMPUTER,
        add_template,
        lambda stored: remove_template(stored["name"]),
    ),
    "datatypes": (
        "/api/datatypes",
        "datatypes",
        PIN,
        add_datatype,
        lambda stored: remove_datatype(stored["id"]),
    ),
}


@pytest.mark.parametrize("section", list(REPEATS))
def test_a_save_built_on_a_read_right_after_a_save_is_not_refused(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch, section: str
):
    path, key, entry, add, remove = REPEATS[section]
    assert env.client.put(path, json=changes(add(entry))).status_code == 202
    assert commit_now(env).json()["failed"] == []
    [stored] = env.client.get(path).json()[key]
    seen: dict[str, Any] = {}

    async def step(client: httpx.AsyncClient) -> None:
        seen["listed"] = (await client.get(path)).json()[key]
        seen["response"] = await client.put(path, json=changes(add(entry)))

    ## as an e2e test deletes its entry again, then adds it to what it reads: a stale read
    ## would still hold it, and the addition would be refused as a second one
    before_the_reload(env, monkeypatch, step)
    assert env.client.put(path, json=changes(remove(stored))).status_code == 202
    assert commit_now(env).status_code == 200
    assert seen["listed"] == []
    response = seen["response"]
    assert response.status_code == 202, response.text
    assert commit_now(env).json()["failed"] == []
    assert [e["name"] for e in env.client.get(path).json()[key]] == [entry["name"]]


## when the datatype's removal comes after the removal of the template that used it: while
## that save waits, while its commit runs, once it is written and before the reload, after it
MOMENTS = ("pending", "committing", "written", "committed")


@pytest.mark.parametrize("moment", MOMENTS)
def test_a_datatype_goes_right_after_the_template_that_used_it(
    env: AppEnv, monkeypatch: pytest.MonkeyPatch, moment: str
):
    saved = env.client.put("/api/datatypes", json=changes(add_datatype(PIN)))
    [made] = [d["id"] for d in saved.json()["datatypes"]]
    coordinate = {"kind": "path", "path": "name", "datatype": made}
    named = {"name": "Named", "type": 2, "coordinates": [coordinate]}
    assert env.client.put("/api/templates", json=changes(add_template(named))).status_code == 202
    assert commit_now(env).json()["failed"] == []
    removed = changes(remove_datatype(made))
    assert env.client.put("/api/datatypes", json=removed).status_code == 422
    seen: dict[str, Any] = {}

    async def step(client: httpx.AsyncClient) -> None:
        seen["statuses"] = [row["status"] for row in env.queue.db.queue_entries()][-1:]
        seen["response"] = await client.put("/api/datatypes", json=removed)

    if moment == "pending":
        monkeypatch.setattr(auto_commit(env), "start_commit", lambda: None)
    elif moment == "committing":
        before_first(env, monkeypatch, "_apply_one", step)
    elif moment == "written":
        before_the_reload(env, monkeypatch, step)
    response = env.client.put("/api/templates", json=changes(remove_template("Named")))
    assert response.status_code == 202, response.text
    if moment in ("pending", "committed"):
        if moment == "committed":
            assert commit_now(env).json()["failed"] == []
        seen["statuses"] = [row["status"] for row in env.queue.db.queue_entries()][-1:]
        seen["response"] = env.client.put("/api/datatypes", json=removed)
    else:
        assert commit_now(env).status_code == 200
    expected = {"pending": "pending", "committing": "approved"}.get(moment, "applied")
    assert seen["statuses"] == [expected]
    assert seen["response"].status_code == 202, seen["response"].text
    assert seen["response"].json()["datatypes"] == []
    assert commit_now(env).json()["failed"] == []
    assert listing(env, "failed") == []
    sections = json.loads(env.item(env.tool_note_id)["notes"])
    assert sections["templates"] == []
    assert sections["datatypes"] == []


LAPTOP = {**COMPUTER, "name": "Laptop"}


def test_the_diff_the_routes_and_the_to_fill_list_show_one_fold_of_the_saves(env: AppEnv):
    """The diff of a save (view.py), the routes' current view (api/sections.py) and the to-fill
    list (to_fill.py) all fold the saves under way through queue/sections.py."""
    ## the commits the saves start cannot run, so both saves stay under way
    vault_unreachable(env, True)
    for template in (COMPUTER, LAPTOP):
        stored = env.client.get("/api/templates").json()
        body = {**changes(add_template(template)), "revisionDate": stored["revisionDate"]}
        assert env.client.put("/api/templates", json=body).status_code == 202
    add = AddColumn(op="addColumn", itemId="login-1", column="login.password", since="2026-11-18")
    env.queue.enqueue_section_changes("toFill", [add], TO_FILL_SUMMARY)

    def diffed(entry_id: int, section: str) -> Any:
        [row] = [row for row in entry(env, entry_id)["diff"] if row["column"] == "notes"]
        return json.loads(row["after"])[section]

    *_, second, to_fill = [row["id"] for row in listing(env, "pending")]
    saving, templates = env.queue.saving_section("templates")
    assert saving
    assert templates == [COMPUTER, LAPTOP]
    assert env.client.get("/api/templates").json()["templates"] == templates
    assert diffed(second, "templates") == templates
    saving, listed = env.queue.saving_section("toFill")
    assert saving
    assert env.app.state.bw.to_fill.with_queued_saves() == listed
    assert diffed(to_fill, "toFill") == listed
    assert [(e["itemId"], e["columns"]) for e in listed] == [("login-1", ["login.password"])]
