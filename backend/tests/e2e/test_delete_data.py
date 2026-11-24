"""Delete my data and Delete my account on the Settings page (docs/spec.md, Settings, Deleting
data).

Delete my data runs on the dev account. It edits Example Forge, which no other test touches,
and writes the username back before it deletes: a put back from a backup cannot run once the
backups are gone. Delete my account runs on the restore account only, never on the dev account
the other files share. test_accounts.py makes the restore account anew at its start.
"""

import subprocess
import time

from playwright.sync_api import Locator, Page, expect
from support.api import COMMIT_TIMEOUT, Api, commit_new_entries, last_entry_id, rows_by_name
from support.elsewhere import DEV_VAULT
from support.login import EMAIL, RESTORE_EMAIL, SERVER, enter_account, log_in
from support.pages import UNLOCK_TIMEOUT, grid, search, type_into

ITEM = "Example Forge"
## the gateway's directory of an account on https://vaultwarden (docs/spec.md, Gateway)
ACCOUNT_DIR = "/data/accounts/vaultwarden/{email}"
## the deletion stops a running background backup first, which can take its next step
DELETE_MS = COMMIT_TIMEOUT * 1000


def account_dir_exists(email: str) -> bool:
    done = subprocess.run(
        [str(DEV_VAULT), "exec", "--root", "test", "-d", ACCOUNT_DIR.format(email=email)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
    )
    return done.returncode == 0


def open_settings(page: Page) -> Locator:
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("menuitem", name="Settings").click()
    section = page.get_by_role("region", name="Delete data")
    expect(section).to_be_visible()
    return section


def confirm_dialog(page: Page, section: Locator, name: str, email: str) -> Locator:
    """Open the dialog behind the button, check the confirm button waits for the email."""
    section.get_by_role("button", name=name, exact=True).click()
    dialog = page.get_by_role("dialog", name=name)
    confirm = dialog.get_by_role("button", name=name, exact=True)
    expect(confirm).to_be_disabled()
    dialog.get_by_role("textbox", name=f"Type {email} to confirm").fill(email.upper())
    expect(confirm).to_be_enabled()
    return dialog


def test_delete_my_data_empties_the_change_log_and_the_backups(unlocked_page: Page, api: Api):
    page = unlocked_page
    api.wait_saved()
    assert api.get("/api/queue?status=pending") == [], "a test before left a pending entry"
    row = rows_by_name(api)[ITEM]
    username = row["cells"]["login.username"]
    search(page, ITEM)
    after = last_entry_id(api)
    type_into(page, row["id"], "login.username", "forge-e2e")
    commit_new_entries(api, after)
    ## the username back as it was, a second write of the write path
    row = rows_by_name(api)[ITEM]
    operation = {
        "kind": "setLeaf",
        "itemId": row["id"],
        "revisionDate": row["revisionDate"],
        "column": "login.username",
        "value": username,
    }
    api.post("/api/operations", {"operations": [operation]}, timeout=DELETE_MS)
    assert api.get("/api/change-log"), "the edits left no change log row"
    assert api.get("/api/backups"), "the edits left no backup"

    dialog = confirm_dialog(page, open_settings(page), "Delete my data", EMAIL)
    ## nothing is pending, so the dialog asks nothing more
    expect(dialog.get_by_role("radio")).to_have_count(0)
    dialog.get_by_role("button", name="Delete my data", exact=True).click()
    expect(page.get_by_role("alert").filter(has_text="Deleted")).to_be_visible(timeout=DELETE_MS)
    expect(dialog).to_have_count(0)

    assert api.get("/api/change-log") == []
    assert api.get("/api/backups") == []
    page.get_by_role("button", name="Change log", exact=True).click()
    expect(page.get_by_text("No changes yet.", exact=True)).to_be_visible()
    ## the account stays: the settings page still opens in the same session
    assert account_dir_exists(EMAIL)


def test_delete_my_account_shows_the_account_form_and_the_account_is_gone(page: Page):
    enter_account(page, RESTORE_EMAIL)
    log_in(page)
    grid(page)
    dialog = confirm_dialog(page, open_settings(page), "Delete my account", RESTORE_EMAIL)
    dialog.get_by_role("button", name="Delete my account", exact=True).click()

    expect(page.get_by_role("button", name="Continue", exact=True)).to_be_visible(
        timeout=UNLOCK_TIMEOUT
    )
    ## the gateway deletes the directory once the worker has exited
    deadline = time.monotonic() + 60
    while account_dir_exists(RESTORE_EMAIL):
        assert time.monotonic() < deadline, "the account directory is still there"
        time.sleep(1)
    assert account_dir_exists(EMAIL), "the dev account went too"

    page.get_by_label("Server").fill(SERVER)
    page.get_by_label("Email").fill(RESTORE_EMAIL)
    page.get_by_role("button", name="Continue", exact=True).click()
    create = page.get_by_role("dialog", name="Create account")
    expect(create).to_contain_text(
        f"There is no bwtidy account for {RESTORE_EMAIL} on {SERVER} yet."
    )
    create.get_by_role("button", name="Cancel", exact=True).click()
