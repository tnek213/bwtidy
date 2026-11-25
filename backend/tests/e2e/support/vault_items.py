"""Making and removing items and templates through the app, for the tests that start from them."""

import re
import time
from typing import Any

from playwright.sync_api import Locator, Page, expect

from support.api import COMMIT_TIMEOUT, Api, commit_new_entries, last_entry_id, rows_by_name
from tests.support.section_changes import template_changes

## the Name input, whose label ends in the asterisk of a required field
NAME = re.compile(r"^Name\b")
## bw's generator as bwtidy asks for it without generator settings (DEFAULT_OPTIONS)
GENERATED_LENGTH = 24


def new_item_dialog(page: Page) -> Locator:
    page.get_by_role("button", name="New item", exact=True).click()
    dialog = page.get_by_role("dialog", name="New item")
    expect(dialog).to_be_visible()
    return dialog


def trash(api: Api, name: str) -> None:
    """Move the item of that name to the trash again, when the test got as far as making it."""
    item = rows_by_name(api).get(name)
    if item is None:
        return
    after = last_entry_id(api)
    op = {"kind": "trashItem", "itemId": item["id"], "revisionDate": item["revisionDate"]}
    api.post("/api/queue", {"operations": [op]})
    commit_new_entries(api, after)


def wait_templates_saved(api: Api) -> dict[str, Any]:
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while (doc := api.get("/api/templates"))["saving"]:
        assert time.monotonic() < deadline, "the templates save was not committed"
        time.sleep(0.5)
    return doc


def save_templates(api: Api, templates: list[dict[str, Any]]) -> None:
    doc = wait_templates_saved(api)
    if doc["templates"] == templates:
        return
    changes = template_changes(doc["templates"], templates)
    api.put("/api/templates", {"changes": changes, "revisionDate": doc["revisionDate"]})
    saved = wait_templates_saved(api)["templates"] == templates
    assert saved, "the templates were not saved"
