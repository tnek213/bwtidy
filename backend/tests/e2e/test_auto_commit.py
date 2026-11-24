"""UI edits wait in the queue and save by themselves.

The auto-commit settings are shared by every test, so a test that changes them puts the old
ones back, also when it logged out on the way. The file puts the items back as it found them at
its end (file_put_back).
"""

import re
import time
import uuid
from collections.abc import Callable, Iterator

import pytest
from playwright.sync_api import Page, expect
from support.api import COMMIT_TIMEOUT, Api, rows_by_name
from support.elsewhere import edit_notes_elsewhere, put_notes_back_elsewhere
from support.login import PASSWORD, log_out, unlock
from support.pages import MASK, QUEUED, UNLOCK_TIMEOUT, grid, item_cell, row, search, type_into

pytestmark = pytest.mark.usefixtures("file_put_back")

BACKUP_NAME = re.compile(r"^\d{8}T\d{6}Z\.zip\.age$")
COMMIT_MS = COMMIT_TIMEOUT * 1000
## long enough that nothing commits on its own while a test runs
NEVER = 3600


def unsaved_edits(page: Page, count: int):
    """The Queue button, which says the unsaved edits in its name."""
    return page.get_by_role("button", name=re.compile(rf"^Queue, {count} unsaved edits"))


@pytest.fixture
def auto_commit(unlocked_page: Page, api: Api) -> Iterator[Callable[[int], None]]:
    """Set afterSeconds for the test, the old settings come back afterwards."""
    before = api.get("/api/queue/auto-commit")
    settings = {"afterSeconds": before["afterSeconds"], "maxEntries": before["maxEntries"]}

    def after(seconds: int) -> None:
        api.put("/api/queue/auto-commit", {**settings, "afterSeconds": seconds})

    yield after
    response = api.request.put("/api/queue/auto-commit", data=settings)
    if response.status == 401:
        ## the test ended the session, a new one puts the settings back
        api.post("/api/session", {"password": PASSWORD}, timeout=UNLOCK_TIMEOUT)
        response = api.request.put("/api/queue/auto-commit", data=settings)
    assert response.ok, f"{response.status} when putting the auto-commit settings back"


def test_an_edit_shows_at_once_and_saves_by_itself(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Task board"]["id"]
    search(page, "Task board")
    type_into(page, item_id, "login.username", "tasks-e2e")
    target = item_cell(page, item_id, "login.username")
    expect(target).to_have_text("tasks-e2e")
    expect(unsaved_edits(page, 1)).to_be_visible()
    expect(target).not_to_have_class(QUEUED, timeout=COMMIT_MS)
    expect(unsaved_edits(page, 1)).to_have_count(0)
    expect(target).to_have_text("tasks-e2e")
    assert rows_by_name(api)["Task board"]["cells"]["login.username"] == "tasks-e2e"


def test_the_change_log_shows_the_commit_with_its_backup_and_explains_it(
    unlocked_page: Page, api: Api, assert_no_secrets: Callable[[str], None]
):
    page = unlocked_page
    item_id = rows_by_name(api)["Code forge"]["id"]
    search(page, "Code forge")
    type_into(page, item_id, "login.username", "forge-e2e")
    api.wait_saved()
    page.get_by_role("button", name="Change log", exact=True).click()
    log = page.get_by_role("table", name="Change log")
    name_cell = page.get_by_role("cell", name="Code forge", exact=True)
    entry = log.get_by_role("row").filter(has=name_cell)
    entry = entry.filter(has=page.get_by_text("setLeaf", exact=True)).first
    expect(entry).to_be_visible()
    cells = entry.get_by_role("cell")
    ## Time, Source, Operation, Items, Result, Backup
    expect(cells.nth(1)).to_have_text("UI")
    operation = cells.nth(2)
    expect(operation.get_by_text("Set a value", exact=True)).to_be_visible()
    expect(operation.get_by_text("setLeaf", exact=True)).to_be_visible()
    ## the field the commit wrote, under the label
    expect(operation.get_by_text("login.username", exact=True)).to_be_visible()
    expect(cells.nth(4)).to_have_text("OK")
    expect(cells.nth(5)).to_have_text(BACKUP_NAME)
    operation.get_by_text("Set a value", exact=True).hover()
    expect(page.get_by_role("tooltip")).to_have_text(
        "Changed one value of an item: a built-in field such as the username, or a custom field."
    )
    assert_no_secrets(log.inner_text())


def test_commit_now_saves_at_once(unlocked_page: Page, api: Api, auto_commit):
    page = unlocked_page
    auto_commit(NEVER)
    item_id = rows_by_name(api)["NAS"]["id"]
    search(page, "NAS")
    type_into(page, item_id, "login.username", "storage-e2e")
    expect(unsaved_edits(page, 1)).to_be_visible()
    page.get_by_role("button", name="Commit now").click()
    expect(item_cell(page, item_id, "login.username")).not_to_have_class(QUEUED, timeout=COMMIT_MS)
    assert rows_by_name(api)["NAS"]["cells"]["login.username"] == "storage-e2e"


def test_the_auto_commit_waits_as_long_as_set_on_the_queue_page(
    unlocked_page: Page, api: Api, auto_commit
):
    page = unlocked_page
    seconds = 30
    page.get_by_role("button", name=re.compile(r"^Queue")).click()
    form = page.get_by_role("form", name="Auto-commit")
    form.get_by_label("After seconds").fill(str(seconds))
    form.get_by_role("button", name="Save").click()
    expect(form.get_by_text("Saved.")).to_be_visible()
    assert api.get("/api/queue/auto-commit")["afterSeconds"] == seconds
    page.get_by_role("button", name="Vault", exact=True).click()
    grid(page)
    item_id = rows_by_name(api)["DNS registrar"]["id"]
    search(page, "DNS registrar")
    type_into(page, item_id, "login.username", "domains-e2e")
    edited = time.monotonic()
    page.wait_for_timeout((seconds - 8) * 1000)
    assert api.get("/api/queue/auto-commit")["pending"] == 1, "committed before the time was up"
    expect(item_cell(page, item_id, "login.username")).to_have_class(QUEUED)
    api.wait_saved()
    assert time.monotonic() - edited >= seconds - 1, "committed before the time was up"
    assert rows_by_name(api)["DNS registrar"]["cells"]["login.username"] == "domains-e2e"


def test_a_hidden_value_stays_masked_until_saved_and_reveals_after(unlocked_page: Page, api: Api):
    page = unlocked_page
    item_id = rows_by_name(api)["Cloud object storage"]["id"]
    search(page, "Cloud object storage")
    value = f"e2e-{uuid.uuid4().hex}"
    target = item_cell(page, item_id, "login.password")
    target.dblclick()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("New value").fill(value)
    dialog.get_by_role("button", name="Save").click()
    expect(target).to_have_class(QUEUED)
    expect(target).to_have_text(MASK)
    expect(target.get_by_role("button", name="Reveal login.password")).to_have_count(0)
    pending = api.request.get("/api/queue?status=pending").text()
    assert value not in pending, "the queue answers the hidden value"
    expect(target).not_to_have_class(QUEUED, timeout=COMMIT_MS)
    target.get_by_role("button", name="Reveal login.password").click()
    expect(target.get_by_role("button", name="Hide login.password")).to_be_visible()
    shown = target.get_by_test_id("secret-value").inner_text() == value
    assert shown, "the revealed password is not the new one"
    applied = api.request.get("/api/queue?status=applied").text()
    assert value not in applied, "the queue answers the hidden value"


def test_a_change_elsewhere_fails_the_commit_and_marks_the_item(
    unlocked_page: Page, api: Api, auto_commit
):
    page = unlocked_page
    auto_commit(NEVER)
    relay = rows_by_name(api)["Mail relay"]
    item_id = relay["id"]
    search(page, "Mail relay")
    type_into(page, item_id, "login.username", "relay-e2e")
    notes = f"Edited on another device {uuid.uuid4().hex[:8]}"
    edit_notes_elsewhere(item_id, notes)
    try:
        page.get_by_role("button", name="Commit now").click()
        failure = page.get_by_role("alert").filter(has_text="Could not save")
        expect(failure).to_contain_text("Mail relay", timeout=COMMIT_MS)
        expect(row(page, item_id)).to_have_class(re.compile(r"\bbw-conflict-row\b"))
        expect(item_cell(page, item_id, "notes")).to_have_class(re.compile(r"\bbw-conflict-cell\b"))
        expect(item_cell(page, item_id, "notes")).to_have_text(notes)
        assert rows_by_name(api)["Mail relay"]["cells"].get("login.username") != "relay-e2e"
        page.get_by_role("button", name=re.compile(r"^Queue")).click()
        page.get_by_role("tab", name="Failed").click()
        expect(page.get_by_text('Set login.username of "Mail relay"').first).to_be_visible()
    finally:
        put_notes_back_elsewhere(item_id, relay["cells"].get("notes"))


def test_log_out_saves_the_unsaved_edits(unlocked_page: Page, api: Api, auto_commit):
    page = unlocked_page
    auto_commit(NEVER)
    item_id = rows_by_name(api)["Router web UI"]["id"]
    search(page, "Router web UI")
    type_into(page, item_id, "login.username", "router-e2e")
    log_out(page)
    expect(page.get_by_role("button", name="Unlock", exact=True)).to_be_visible(timeout=COMMIT_MS)
    unlock(page)
    grid(page)
    search(page, "Router web UI")
    target = item_cell(page, item_id, "login.username")
    expect(target).to_have_text("router-e2e", timeout=UNLOCK_TIMEOUT)
    expect(target).not_to_have_class(QUEUED)
    assert rows_by_name(api)["Router web UI"]["cells"]["login.username"] == "router-e2e"
