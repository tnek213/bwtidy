"""Rename attachments in the cell menu of an attachments cell and its dialog (docs/spec.md, Grid,
the cell menu). The dialog is closed without a rename, so the vault stays as seeded.
"""

from playwright.sync_api import Page, expect
from support.pages import cell, settle

from tests.support.docs_shots import shot

ITEM = "Internal CA"


def test_rename_attachment(unlocked_page: Page) -> None:
    page = unlocked_page
    attachments = cell(page, ITEM, "attachments")
    settle(page)
    attachments.click(button="right")
    menu = page.get_by_role("menu")
    rename = menu.get_by_role("menuitem", name="Rename attachments")
    expect(rename).to_be_visible()
    header = page.locator(".ag-header-cell[col-id='attachments']")
    ## the band shows revisionDate too, the date of the last seeding, which would change the image
    page.locator(".ag-row .ag-cell[col-id='revisionDate']").evaluate_all(
        "cells => cells.forEach(cell => cell.setAttribute('data-shot-hide', ''))"
    )
    shot(
        page,
        "rename-attachment-menu",
        frame="band",
        around=[header, attachments, menu],
        highlight=[rename],
    )

    rename.click()
    dialog = page.get_by_role("dialog", name=f"Attachments of {ITEM}")
    expect(dialog.get_by_role("listitem")).to_have_count(1)
    shot(page, "rename-attachment-dialog", frame="box", around=[dialog])
    dialog.get_by_role("button", name="Close").click()
    expect(dialog).to_be_hidden()
