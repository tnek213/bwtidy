"""End-to-end tests: Playwright on the host drives the real app in the dev stack.

scripts/e2e starts the gateway in the app container and sets BWTIDY_E2E_URL. Without it
every test here skips. On several stacks it sets BWTIDY_E2E_URLS instead and runs pytest-xdist,
each worker takes its own stack (pick_stack). A fresh browser context starts at the account
form (enter_account). This file holds the stack pick and the pytest fixtures, every test in
this directory gets the marker e2e and the skip on its own. The helpers a test calls are in
the support package, one module for each theme (pages, login, api, elsewhere, mcp, fixture).

Never print a value of an item: a failure message or an assert shows names and columns only.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, expect, sync_playwright
from support.api import Api, app_api
from support.elsewhere import active_ids, last_change_id, put_back
from support.fixture import load_fixture_items, secrets_of
from support.login import enter_account, log_in
from support.mcp import McpCall, create_mcp_token, mcp_caller
from support.pages import TIMEOUT, grid

HERE = Path(__file__).resolve().parent
URL_VAR = "BWTIDY_E2E_URL"
## several stacks' URLs, comma separated, from scripts/e2e: xdist worker gwK takes stack K+1
URLS_VAR = "BWTIDY_E2E_URLS"
## scripts/dev-vault works on this stack of the worktree, also when a test runs it
## (support.elsewhere.DEV_VAULT)
STACK_VAR = "BWTIDY_STACK"

## the auto-commit of the test stacks: the product waits 180 s, which would make every test
## that waits for a commit (Api.wait_saved) take minutes
TEST_AUTO_COMMIT = {"afterSeconds": 10, "maxEntries": 20}


def pick_stack(environ: dict[str, str] | os._Environ[str]) -> None:
    """Point this process at its xdist worker's stack: its URL and BWTIDY_STACK.

    Runs at import, so every scripts/dev-vault call of a test reaches the same stack. scripts/e2e
    passes --max-worker-restart 0: a worker started in place of a crashed one would get a new
    number and no stack of its own.
    """
    urls = [url for url in environ.get(URLS_VAR, "").split(",") if url]
    if not urls:
        return
    worker = environ.get("PYTEST_XDIST_WORKER", "gw0")
    number = worker.removeprefix("gw")
    if not number.isdigit() or int(number) >= len(urls):
        raise pytest.UsageError(f"xdist worker {worker} has no stack in {URLS_VAR}")
    index = int(number)
    environ[URL_VAR] = urls[index]
    environ[STACK_VAR] = str(index + 1)


pick_stack(os.environ)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip = pytest.mark.skip(reason=f"needs {URL_VAR}, run scripts/e2e")
    for item in items:
        if item.path.is_relative_to(HERE):
            item.add_marker(pytest.mark.e2e)
            if not os.environ.get(URL_VAR):
                item.add_marker(skip)


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ[URL_VAR].rstrip("/")


@pytest.fixture(scope="session")
def playwright() -> Iterator[Playwright]:
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright: Playwright) -> Iterator[Browser]:
    browser = playwright.chromium.launch()
    yield browser
    browser.close()


@pytest.fixture
def context(browser: Browser, base_url: str) -> Iterator[BrowserContext]:
    """A fresh browser context per test: no cookie, no localStorage from another test."""
    context = browser.new_context(base_url=base_url, viewport={"width": 1600, "height": 900})
    context.set_default_timeout(TIMEOUT)
    expect.set_options(timeout=TIMEOUT)
    yield context
    context.close()


@pytest.fixture
def page(context: BrowserContext) -> Page:
    return context.new_page()


@pytest.fixture
def unlocked_page(page: Page) -> Page:
    """The app of the dev account unlocked with the dev master password, showing the grid.

    When the account's CLI is logged out (the account is new, or a test before stopped
    midway) it logs in through the web login instead.
    """
    enter_account(page)
    log_in(page)
    grid(page)
    return page


@pytest.fixture
def api(page: Page) -> Api:
    return Api(page.request)


@pytest.fixture
def vault_put_back(unlocked_page: Page, api: Api) -> Iterator[None]:
    """The vault as the test found it afterwards (put_back), for a file whose tests need the
    seeded items each time. When put_back deleted something the session ends, so the next
    unlock loads the vault again."""
    since = last_change_id(api)
    active_before = active_ids(api)
    yield
    if put_back(api, since, active_before):
        api.delete("/api/session")


@pytest.fixture(scope="session", autouse=True)
def quick_auto_commit(browser: Browser, base_url: str) -> None:
    """Set the dev account's auto-commit to TEST_AUTO_COMMIT before the first test of a stack.

    The settings are kept in the account's database, so they hold until the next reset. The
    session ends afterwards, so the first test finds no session as before.
    """
    with app_api(browser, base_url) as api:
        api.put("/api/queue/auto-commit", TEST_AUTO_COMMIT)
        api.delete("/api/session")


@pytest.fixture(scope="module")
def file_put_back(browser: Browser, base_url: str) -> Iterator[None]:
    """The vault as the file found it afterwards (put_back), for a file whose tests each write
    to items of their own. Use it with pytestmark = pytest.mark.usefixtures("file_put_back").

    The session ends afterwards, so the next unlock loads the vault again: the app has not seen
    what put_back deleted, nor a change a test put back as another device.
    """
    with app_api(browser, base_url) as api:
        since = last_change_id(api)
        active_before = active_ids(api)
    yield
    with app_api(browser, base_url) as api:
        put_back(api, since, active_before)
        api.delete("/api/session")


@pytest.fixture
def mcp_token(unlocked_page: Page) -> str:
    """A fresh MCP token, made in the web UI of the unlocked page."""
    return create_mcp_token(unlocked_page)


@pytest.fixture
def mcp_call(base_url: str, mcp_token: str) -> McpCall:
    """mcp_caller with the token of mcp_token."""
    return mcp_caller(base_url, mcp_token)


@pytest.fixture(scope="session")
def fixture_items() -> dict[str, dict[str, Any]]:
    """The items of the fixture files in dev/ by name (unique)."""
    return load_fixture_items()


@pytest.fixture(scope="session")
def assert_no_secrets(fixture_items: dict[str, dict[str, Any]]) -> Callable[[str], None]:
    """Assert that a text holds none of the fixture's secret values.

    A failure names the item and the column, never the value.
    """
    secrets = secrets_of(fixture_items)

    def check(text: str) -> None:
        leaked = [f"{name}: {column}" for name, column, value in secrets if value in text]
        assert not leaked, f"secret values in the text: {', '.join(leaked)}"

    return check
