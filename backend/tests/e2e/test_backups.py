"""Backups are rows of the Change log: a click downloads one, Delete removes one
(docs/spec.md, Backups and Grid, the Change log page).

Uses Hardware store and Train tickets, which no other test touches, and puts their usernames
back afterwards (usernames_put_back). A restore cannot do that here: the test deletes the
backup of the first edit.
"""

from collections.abc import Iterator

import pytest
from playwright.sync_api import Locator, Page, expect
from support.api import COMMIT_TIMEOUT, Api, rows_by_name
from support.pages import search, type_into

ITEMS = ("Hardware store", "Train tickets")

AGE_HEADER = b"age-encryption.org/v1"
## a manual backup waits for a running background backup and takes one itself
BACKUP_MS = 180_000


def edit_and_save(page: Page, api: Api, name: str, value: str) -> str:
    """Change an item's username in the grid, wait until it is saved, answer the row's backup."""
    item_id = rows_by_name(api)[name]["id"]
    search(page, name)
    type_into(page, item_id, "login.username", value)
    api.wait_saved()
    log = api.get("/api/change-log")
    rows = [e for e in log if item_id in e["itemIds"] and e["result"] == "ok"]
    assert rows, f"no change log row for {name}"
    return max(rows, key=lambda e: e["id"])["backup"]


@pytest.fixture
def usernames_put_back(unlocked_page: Page, api: Api) -> Iterator[None]:
    """The usernames of ITEMS as they were, written again through the scripts' route."""
    before = {name: rows_by_name(api)[name]["cells"]["login.username"] for name in ITEMS}
    yield
    api.wait_saved()
    rows = rows_by_name(api)
    operations = [
        {
            "kind": "setLeaf",
            "itemId": rows[name]["id"],
            "revisionDate": rows[name]["revisionDate"],
            "column": "login.username",
            "value": username,
        }
        for name, username in before.items()
        if rows[name]["cells"].get("login.username") != username
    ]
    if operations:
        api.post("/api/operations", {"operations": operations}, timeout=COMMIT_TIMEOUT * 1000)


def backup_row(page: Page, name: str) -> Locator:
    log = page.get_by_role("table", name="Change log")
    rows = log.get_by_role("row").filter(has=page.get_by_role("link", name=name, exact=True))
    return rows.filter(has=page.get_by_role("cell", name="backup", exact=True))


def row_index(row: Locator) -> int:
    return row.evaluate("row => [...row.parentElement.children].indexOf(row)")


def change_row(page: Page, item: str) -> Locator:
    log = page.get_by_role("table", name="Change log")
    rows = log.get_by_role("row").filter(has=page.get_by_role("cell", name=item, exact=True))
    return rows.filter(has=page.get_by_text("setLeaf", exact=True)).first


def test_backups_are_rows_that_download_and_delete(
    unlocked_page: Page, api: Api, usernames_put_back: None
):
    page = unlocked_page
    first_backup = edit_and_save(page, api, "Hardware store", "hardware-e2e")
    second_backup = edit_and_save(page, api, "Train tickets", "train-e2e")
    assert first_backup != second_backup, "the two writes share one backup"
    ## a backup by hand, so the newest one is known and no background backup can replace it
    newest = api.post("/api/backups", timeout=BACKUP_MS)["name"]
    listed = [b["name"] for b in api.get("/api/backups")]
    assert listed[0] == newest
    assert first_backup in listed and second_backup in listed

    page.get_by_role("button", name="Change log", exact=True).click()
    expect(backup_row(page, first_backup)).to_have_count(1)
    expect(backup_row(page, second_backup)).to_have_count(1)
    expect(backup_row(page, newest)).to_have_count(1)
    expect(backup_row(page, newest).get_by_role("button", name="Delete")).to_be_disabled()
    expect(page.get_by_text("age -d -o backup.zip")).to_be_visible()
    expect(page.get_by_text("RESTORE.md in every backup")).to_be_visible()
    ## a backup row comes right under the change row that was written with it
    change = row_index(change_row(page, "Hardware store"))
    assert change + 1 == row_index(backup_row(page, first_backup))

    with page.expect_download() as download_info:
        backup_row(page, second_backup).get_by_role("link", name=second_backup).click()
    download = download_info.value
    assert download.suggested_filename == second_backup
    path = download.path()
    assert path is not None
    starts_with_age = path.read_bytes().startswith(AGE_HEADER)
    assert starts_with_age, "the downloaded file is not an age file"

    backup_row(page, first_backup).get_by_role("button", name="Delete").click()
    dialog = page.get_by_role("dialog", name="Delete backup")
    expect(dialog).to_contain_text(
        f"Delete backup {first_backup} for good? Changes made with it can no longer be restored."
    )
    dialog.get_by_role("button", name="Delete").click()
    expect(dialog).to_be_hidden()
    expect(backup_row(page, first_backup)).to_have_count(0)
    expect(backup_row(page, second_backup)).to_have_count(1)
    assert first_backup not in [b["name"] for b in api.get("/api/backups")]

    gone = change_row(page, "Hardware store")
    name = gone.get_by_text(first_backup, exact=True)
    expect(name).to_be_visible()
    expect(name).to_have_css("text-decoration-line", "line-through")
    expect(gone.get_by_role("button", name="Restore")).to_have_count(0)
    kept = change_row(page, "Train tickets")
    kept_name = kept.get_by_text(second_backup, exact=True)
    expect(kept_name).not_to_have_css("text-decoration-line", "line-through")
    expect(kept.get_by_role("button", name="Restore")).to_be_visible()

    refused = api.request.delete(f"/api/backups/{newest}")
    assert refused.status == 409
    assert newest in [b["name"] for b in api.get("/api/backups")]
