"""The gateway's account directories and an account made anew (docs/spec.md, Gateway and
Deleting data)."""

import subprocess
import time

from playwright.sync_api import Browser

from support.api import COMMIT_TIMEOUT, app_api
from support.elsewhere import DEV_VAULT
from support.login import EMAIL

## the gateway's directory of an account on https://vaultwarden (docs/spec.md, Gateway)
ACCOUNT_DIR = "/data/accounts/vaultwarden/{email}"
## protocol.py's REQUEST_HEADER, which the account's deletion needs
REQUEST_HEADER = {"X-Bwtidy-Request": "1"}
## the gateway deletes the directory once the worker has exited
GONE_TIMEOUT = 60
## the auto-commit settings that the account's status answers among others
AUTO_COMMIT_KEYS = ("afterSeconds", "maxEntries")


def account_dir_exists(email: str) -> bool:
    done = subprocess.run(
        [str(DEV_VAULT), "exec", "--root", "test", "-d", ACCOUNT_DIR.format(email=email)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
    )
    return done.returncode == 0


def dev_account_anew(browser: Browser, base_url: str) -> None:
    """Delete the dev account through the API and make it again with its auto-commit.

    The new account has no queue entry, change log row or backup, and its queue numbers start
    at 1, as for a reader who just logged in the first time. Delete my data would leave the
    numbers counting on. The vault stays as it is. The account is deleted the way Delete my
    account does it, so the next login is a Bitwarden login again. The session ends afterwards.
    """
    with app_api(browser, base_url) as api:
        api.wait_saved()
        status = api.get("/api/queue/auto-commit")
        auto_commit = {key: status[key] for key in AUTO_COMMIT_KEYS}
        ## the deletion stops a running background backup first, which can take its next step
        response = api.request.post(
            "/api/account/delete",
            data={"pending": "drop"},
            headers=REQUEST_HEADER,
            timeout=COMMIT_TIMEOUT * 1000,
        )
        assert response.ok, f"{response.status} from {response.url}"
    deadline = time.monotonic() + GONE_TIMEOUT
    while account_dir_exists(EMAIL):
        assert time.monotonic() < deadline, "the dev account's directory is still there"
        time.sleep(1)
    with app_api(browser, base_url) as api:
        api.put("/api/queue/auto-commit", auto_commit)
        api.delete("/api/session")
