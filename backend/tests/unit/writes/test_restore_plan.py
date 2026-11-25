"""The restore plan (writes/restore_plan.py): the attachments a backup must hold for an item
created anew, and the restore mark of the change log (a change is marked by the first later
restore that put one of its items back from its own backup or an older one, docs/spec.md,
Change log)."""

import copy
from pathlib import Path

import pytest

from bwtidy.backups import BackupContents
from bwtidy.db import Database
from bwtidy.writes import restore_plan
from bwtidy.writes.restore_plan import restored_by
from tests.support.app_env import zip_backup
from tests.support.fake_bw import FakeBwServe
from tests.support.sample_vault import LOGIN
from tests.support.writes_helpers import without_members


def test_restore_refuses_an_item_whose_attachment_the_backup_lacks():
    bw = FakeBwServe([copy.deepcopy(LOGIN)])
    bw.folders, bw.collections, bw.organizations = [], [], []
    bw.attachments[("login-1", "a1")] = b"recovery codes"
    plain = zip_backup(bw)
    contents = BackupContents("b", plain)
    [attachment] = contents.items["login-1"]["attachments"]
    assert contents.attachment_member(contents.items["login-1"], attachment) == (
        "item-attachments/login-1/codes.txt"
    )
    lacking = BackupContents(
        "b", without_members(plain, lambda m: m == "item-attachments/login-1/codes.txt")
    )
    assert lacking.attachment_member(lacking.items["login-1"], attachment) is None
    with pytest.raises(ValueError, match="login-1 cannot be created anew: the backup lacks"):
        restore_plan._check_attachments(lacking.items["login-1"], lacking)


B1 = "20261112T100000Z.zip.age"
B2 = "20261112T110000Z.zip.age"
B3 = "20261112T120000Z.zip.age"
B4 = "20261112T130000Z.zip.age"


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "bwtidy.sqlite")
    yield database
    database.close()


def change(db: Database, backup: str, item_ids: list[str], result: str = "ok", **extra) -> int:
    return db.record("ui", "setLeaf", item_ids, result, backup, columns=["notes"], **extra)


def restore(db: Database, backup: str, restored: str, item_ids: list[str], result="ok") -> int:
    return db.record("ui", "restoreItems", item_ids, result, backup, restored_backup=restored)


def marks(db: Database) -> dict[int, tuple[int, list[str]] | None]:
    """Each row's restoredBy as (restore id, item ids), by row id."""
    marked = {e["id"]: e["restoredBy"] for e in restored_by(db.latest(), db.restores())}
    return {i: None if by is None else (by["id"], by["itemIds"]) for i, by in marked.items()}


def test_a_restore_of_the_changes_own_backup_marks_it(db: Database):
    x = change(db, B1, ["a", "b"])
    r = restore(db, B2, B1, ["a", "b"])
    entries = restored_by(db.latest(), db.restores())
    by_id = {e["id"]: e for e in entries}
    assert by_id[x]["restoredBy"] == {"id": r, "at": by_id[r]["at"], "itemIds": ["a", "b"]}
    ## a restore row itself is never marked
    assert by_id[r]["restoredBy"] is None


def test_a_restore_of_an_older_backup_marks_every_later_change_of_its_items(db: Database):
    older = change(db, B1, ["a"])
    x = change(db, B2, ["a", "b"])
    other = change(db, B3, ["c"])
    r = restore(db, B4, B1, ["a"])
    ## only the items of x the restore put back, and not a change of another item
    assert marks(db) == {older: (r, ["a"]), x: (r, ["a"]), other: None, r: None}


def test_a_restore_of_a_newer_backup_does_not_mark_the_change(db: Database):
    x = change(db, B1, ["a"])
    y = change(db, B2, ["a"])
    ## B2 already holds x, only y is undone
    r = restore(db, B3, B2, ["a"])
    assert marks(db) == {x: None, y: (r, ["a"]), r: None}


def test_a_failed_restore_or_an_earlier_one_does_not_mark_it(db: Database):
    earlier = restore(db, B2, B1, ["a"])
    x = change(db, B2, ["a"])
    failed = restore(db, B3, B1, ["a"], result="failed")
    assert marks(db) == {earlier: None, x: None, failed: None}


def test_only_the_first_restore_that_undid_the_change_counts(db: Database):
    x = change(db, B2, ["a"])
    ## a newer backup leaves x as it is, so the restore after it is the first
    newer = restore(db, B3, B3, ["a"])
    first = restore(db, B4, B2, ["a"])
    second = restore(db, B4, B1, ["a", "b"])
    assert marks(db) == {x: (first, ["a"]), newer: None, first: None, second: None}


def test_a_failed_change_is_not_marked_and_a_created_item_counts(db: Database):
    failed = change(db, B1, ["a"], result="failed")
    created = db.record("ui", "createItem", [], "ok", B1, ["new"])
    r = restore(db, B2, B1, ["a", "new"])
    assert marks(db) == {failed: None, created: (r, ["new"]), r: None}
