"""renameAttachment against the dev Vaultwarden: bw serve cannot rename an attachment in place,
so bwtidy uploads it again under the new name and deletes the old one.

Each case makes a note with one attachment (as another device, see Elsewhere), renames the
attachment through POST /api/operations and checks that the item holds one attachment, with
the new name and the same bytes, then restores the change log row, which gives it the old name
back. A personal note and one in the organization Family. The notes are deleted for good at
the end. Nothing here prints the bytes.
"""

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from support.app import App, Elsewhere, wait_until_listed
from support.cli import BwCli

pytestmark = pytest.mark.integration

CONTENT = b"Made-up file of todo 1545, renamed by bwtidy.\n"


@pytest.fixture(scope="module")
def logged_in(app: App) -> App:
    assert app.login().status_code == 200
    app.settle()
    return app


@pytest.fixture
def elsewhere(logged_in: App, bw_cli: BwCli, tmp_path: Path) -> Iterator[Elsewhere]:
    made = Elsewhere(logged_in, bw_cli, tmp_path)
    try:
        yield made
    finally:
        made.clean_up()


def call(app: App, fn: Any, *args: Any) -> Any:
    assert app.client.portal is not None
    return app.client.portal.call(fn, *args)


def served(app: App, item_id: str) -> dict[str, Any]:
    """The item as bw serve has it once it lists the item after a sync."""
    wait_until_listed(app, item_id)
    return call(app, app.session.client.get_item, item_id)


def downloaded(app: App, item_id: str, attachment_id: str) -> bytes:
    return call(app, app.session.client.get_attachment, attachment_id, item_id)


def post(app: App, *operations: dict[str, Any]) -> Any:
    response = app.client.post("/api/operations", json={"operations": list(operations)})
    app.settle()
    return response


def family(app: App) -> dict[str, Any]:
    """The place of a Family item: the organization and its collection Shared."""
    cache = app.session.cache
    org = next(o for o in cache.organizations if o.get("name") == "Family")
    shared = next(
        c
        for c in cache.collections
        if c.get("organizationId") == org["id"] and c.get("name") == "Shared"
    )
    return {"organizationId": org["id"], "collectionIds": [shared["id"]]}


@pytest.mark.parametrize("place", ["personal", "Family"])
def test_a_renamed_attachment_keeps_its_bytes_and_restore_renames_it_back(
    logged_in: App, elsewhere: Elsewhere, place: str
):
    app = logged_in
    where = family(app) if place == "Family" else {}
    name = f"Rename attachment {place}"
    item_id = elsewhere.note_with_attachment(name, "old.txt", CONTENT, **where)
    wait_until_listed(app, item_id)
    call(app, app.session.cache.load)
    item = app.session.cache.items_by_id()[item_id]
    [old] = item["attachments"]
    op = {
        "kind": "renameAttachment",
        "itemId": item_id,
        "revisionDate": item["revisionDate"],
        "attachmentId": old["id"],
        "fileName": "new name.txt",
        "shownFileName": "old.txt",
    }
    response = post(app, op)
    assert response.status_code == 200, response.text

    now = served(app, item_id)
    [renamed] = now["attachments"]
    assert renamed["fileName"] == "new name.txt"
    assert renamed["id"] != old["id"]
    assert renamed["size"] == old["size"]
    assert (downloaded(app, item_id, renamed["id"]) == CONTENT) is True
    assert now.get("organizationId") == where.get("organizationId")

    [row] = [r for r in app.client.get("/api/change-log").json() if r["itemIds"] == [item_id]]
    assert (row["operationKind"], row["result"]) == ("renameAttachment", "ok")
    assert row["attachmentIds"] == {"before": old["id"], "after": renamed["id"]}

    ## an edit based on the revisionDate from before the rename does not conflict with it
    edit = {
        "kind": "setLeaf",
        "itemId": item_id,
        "revisionDate": item["revisionDate"],
        "column": "notes",
        "value": "edited after the rename",
    }
    assert post(app, edit).status_code == 200

    plan = app.client.get("/api/restore-plan", params={"changeLogId": row["id"]})
    assert plan.status_code == 200, plan.text
    assert plan.json()["operation"]["fileName"] == "old.txt"
    assert post(app, plan.json()["operation"]).status_code == 200
    back = served(app, item_id)
    [restored] = back["attachments"]
    assert restored["fileName"] == "old.txt"
    assert (downloaded(app, item_id, restored["id"]) == CONTENT) is True
    ## the restore renamed only the attachment, the later edit stays
    assert back["notes"] == "edited after the rename"


def test_a_put_with_another_file_name_leaves_the_name_as_it_was(
    logged_in: App, elsewhere: Elsewhere
):
    """Why bwtidy renames by upload and delete: bw serve's item PUT ignores the attachments."""
    app = logged_in
    item_id = elsewhere.note_with_attachment("Rename in place", "old.txt", CONTENT)
    full = served(app, item_id)
    body = copy.deepcopy(full)
    body["attachments"][0]["fileName"] = "in place.txt"
    call(app, app.session.client.put_item, item_id, body)
    [attachment] = served(app, item_id)["attachments"]
    assert attachment["fileName"] == "old.txt"
    assert attachment["id"] == full["attachments"][0]["id"]
