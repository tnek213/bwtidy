"""The app's API with a page's cookies, and the helpers that wait for the review queue."""

import contextlib
import time
from collections.abc import Generator
from typing import Any
from urllib.parse import quote

from playwright.sync_api import APIRequestContext, APIResponse, Browser

from support.login import enter_account, log_in
from support.pages import TIMEOUT, grid

## a commit waits for a running background backup (about 18 s on the dev vault) and may take its own
COMMIT_TIMEOUT = 120


class Api:
    """The app's API with the page's cookies, so it acts in the page's session."""

    def __init__(self, request: APIRequestContext) -> None:
        self.request = request

    @staticmethod
    def _json(response: APIResponse) -> Any:
        ## the status and the URL only, a body could hold a value
        assert response.ok, f"{response.status} from {response.url}"
        return response.json() if response.body() else None

    def get(self, path: str) -> Any:
        return self._json(self.request.get(path))

    def post(self, path: str, data: Any = None, timeout: float | None = None) -> Any:
        """timeout in ms, for a write that waits for a backup"""
        return self._json(self.request.post(path, data=data, timeout=timeout))

    def put(self, path: str, data: Any = None) -> Any:
        return self._json(self.request.put(path, data=data))

    def delete(self, path: str) -> Any:
        return self._json(self.request.delete(path))

    def wait_saved(self, timeout: float = COMMIT_TIMEOUT) -> None:
        """Wait until no UI edit is pending and no commit runs, so the vault holds the edits."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.get("/api/queue/auto-commit")
            if status["pending"] == 0 and not status["committing"]:
                return
            assert time.monotonic() < deadline, f"edits still unsaved after {timeout:.0f} s"
            time.sleep(1)


def rows_by_name(api: Api) -> dict[str, dict[str, Any]]:
    """The grid's rows (id, revisionDate, cells) by item name, secret cells masked."""
    return {row["cells"]["name"]: row for row in api.get("/api/grid")["rows"]}


def secret_is(api: Api, row: dict[str, Any], column: str, expected: str) -> bool:
    """Whether a secret cell holds the expected value, without the value in any message."""
    path = f"/api/items/{row['id']}/secret?column={quote(column)}"
    response = api.request.get(path)
    return response.ok and response.json()["value"] == expected


def last_entry_id(api: Api) -> int:
    return max((entry["id"] for entry in api.get("/api/queue")), default=0)


def commit_new_entries(api: Api, after: int, count: int = 1) -> None:
    """Wait for count queue entries newer than after, commit them now and wait until applied.

    The UI sends its edit without waiting, so the entries show up a moment after the click.
    """
    deadline = time.monotonic() + COMMIT_TIMEOUT

    def new() -> list[dict[str, Any]]:
        return [entry for entry in api.get("/api/queue") if entry["id"] > after]

    while len(new()) < count:
        assert time.monotonic() < deadline, f"fewer than {count} entries were queued"
        time.sleep(0.5)
    ## the first commit after a login waits for the background backup, which can take longer than
    ## the default 30 s when other stacks run beside this one
    api.post("/api/queue/commit", timeout=COMMIT_TIMEOUT * 1000)
    while any(entry["status"] in ("pending", "approved") for entry in new()):
        assert time.monotonic() < deadline, "the queued entries were not committed"
        time.sleep(0.5)
    ## a summary names the kind, the items and the columns, never a value
    failed = [entry["summary"] for entry in new() if entry["status"] != "applied"]
    assert not failed, f"entries not applied: {failed}"


@contextlib.contextmanager
def app_api(browser: Browser, base_url: str) -> Generator[Api]:
    """The API of the dev account in a browser context of its own, unlocked."""
    context = browser.new_context(base_url=base_url)
    context.set_default_timeout(TIMEOUT)
    try:
        page = context.new_page()
        enter_account(page)
        log_in(page)
        grid(page)
        yield Api(page.request)
    finally:
        context.close()
