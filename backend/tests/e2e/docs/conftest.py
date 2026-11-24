"""Docs tests: Playwright tests like the e2e tests that take the documentation screenshots.

Each test here skips unless BWTIDY_DOCS_SHOTS=1 (scripts/docs-shots sets it), on top of the skip
of every e2e test without BWTIDY_E2E_URL, so a whole scripts/e2e run writes no image. The e2e
fixtures apply here as in backend/tests/e2e, import its helpers from the support package
(`from support.pages import ...`).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Playwright

from tests.support.docs_shots import ENABLE_VAR, enabled

HERE = Path(__file__).resolve().parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if enabled():
        return
    skip = pytest.mark.skip(reason=f"needs {ENABLE_VAR}=1, run scripts/docs-shots")
    for item in items:
        if item.path.is_relative_to(HERE):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def browser(playwright: Playwright) -> Iterator[Browser]:
    """Chromium as in the e2e tests, but drawing every change in full.

    A partial raster antialiases the grid's rounded corners differently by what the page drew
    earlier, so two runs give images a few pixels apart.
    """
    browser = playwright.chromium.launch(args=["--disable-partial-raster"])
    yield browser
    browser.close()
