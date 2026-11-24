import copy
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bwtidy.backups.crypto import write_encrypted
from bwtidy.backups.files import apply_retention, fingerprint, list_backups
from bwtidy.config import KEEP_ALWAYS, KEEP_DAYS, KEEP_NEWEST, Retention
from bwtidy.protocol import backup_name, backup_time

NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)
PASSWORD = "correct horse battery staple"
DATA = b"PK\x03\x04 not really a zip but good enough " * 100


def names(ages_in_days: Sequence[float]) -> list[str]:
    return [backup_name(NOW - timedelta(days=age)) for age in ages_in_days]


def survivors(ages_in_days: Sequence[float]) -> list[str]:
    all_names = names(ages_in_days)
    deleted = apply_retention(all_names, NOW)
    assert set(deleted) <= set(all_names)
    return [n for n in all_names if n not in deleted]


def check_rules(ages_in_days: Sequence[float]) -> list[str]:
    kept = survivors(ages_in_days)
    newest_first = sorted(names(ages_in_days), reverse=True)
    assert set(newest_first[:KEEP_ALWAYS]) <= set(kept)
    assert len(kept) <= KEEP_NEWEST
    limit = backup_name(NOW - timedelta(days=KEEP_DAYS))
    for name in kept[KEEP_ALWAYS:]:
        assert name >= limit, f"{name} is older than {KEEP_DAYS} days"
    return kept


def test_three_recent_backups_all_stay():
    assert apply_retention(names([0, 1, 2]), NOW) == []


def test_five_old_backups_all_stay():
    assert apply_retention(names([100, 200, 300, 400, 500]), NOW) == []


def test_forty_recent_backups_all_stay():
    ages = [i * 0.5 for i in range(40)]
    assert apply_retention(names(ages), NOW) == []


def test_the_forty_first_goes():
    ages = [i * 0.5 for i in range(41)]
    all_names = names(ages)
    assert apply_retention(all_names, NOW) == [min(all_names)]


def test_sixty_recent_backups_keep_the_newest_forty():
    ages = [i * 1.0 for i in range(60)]
    kept = check_rules(ages)
    assert len(kept) == KEEP_NEWEST
    assert kept == sorted(names(ages), reverse=True)[:KEEP_NEWEST]


def test_sixty_old_backups_keep_only_the_newest_five():
    ages = [100 + i for i in range(60)]
    kept = check_rules(ages)
    assert len(kept) == KEEP_ALWAYS
    assert kept == sorted(names(ages), reverse=True)[:KEEP_ALWAYS]


def test_mixed_ages_keep_the_fresh_ones_and_the_newest_five():
    ages = [1, 2, 3, 50, 89, 91, 120, 200, 300, 400]
    kept = check_rules(ages)
    assert kept == names([1, 2, 3, 50, 89])


def test_old_backups_fill_up_the_five_when_fresh_ones_are_few():
    ages = [1, 2, 95, 96, 97, 98, 99]
    kept = check_rules(ages)
    assert kept == names([1, 2, 95, 96, 97])


def test_exactly_ninety_days_is_not_older_than_ninety_days():
    ages = [1, 2, 3, 4, 5, 90]
    assert apply_retention(names(ages), NOW) == []
    assert apply_retention(names(ages[:-1] + [90.001]), NOW) == names([90.001])


@pytest.mark.parametrize("count", [3, 5, 40, 41, 60])
@pytest.mark.parametrize("spread", [1 / 24, 1.0, 5.0, 100.0])
def test_rules_hold_for_many_shapes(count: int, spread: float):
    check_rules([i * spread for i in range(count)])


def test_order_of_the_input_does_not_matter():
    ages = [i * 3.0 for i in range(50)]
    forward = apply_retention(names(ages), NOW)
    backward = apply_retention(list(reversed(names(ages))), NOW)
    assert sorted(forward) == sorted(backward)
    assert len(forward) == 50 - 31


def test_files_that_are_not_backups_are_never_deleted():
    strangers = [".20260101T000000Z.zip.age.part", "notes.txt", "20260101T000000Z.zip"]
    ages = [100 + i for i in range(60)]
    deleted = apply_retention(names(ages) + strangers, NOW)
    assert not set(strangers) & set(deleted)
    assert len(deleted) == 55


def test_a_backup_in_keep_stays_beyond_the_newest_forty():
    ages = [i * 0.5 for i in range(41)]
    all_names = names(ages)
    assert apply_retention(all_names, NOW, keep=frozenset([min(all_names)])) == []


def test_a_backup_in_keep_stays_beyond_ninety_days():
    ages = [100 + i for i in range(60)]
    all_names = names(ages)
    oldest = min(all_names)
    deleted = apply_retention(all_names, NOW, keep=frozenset([oldest]))
    kept = [n for n in all_names if n not in deleted]
    assert len(kept) == KEEP_ALWAYS + 1
    assert oldest in kept


def test_a_kept_backup_still_counts_among_the_forty():
    ages = [i * 1.0 for i in range(41)]
    newest_first = sorted(names(ages), reverse=True)
    ## kept or not, the tenth is one of the 40, so the 41st still goes
    keep = frozenset([newest_first[10], "not-a-backup"])
    assert apply_retention(newest_first, NOW, keep=keep) == [newest_first[KEEP_NEWEST]]


def test_other_counts_and_days_from_the_settings():
    retention = Retention(keep_newest=6, keep_days=20, keep_always=2)
    ages = [1, 2, 3, 10, 19, 21, 30, 40]
    all_names = names(ages)
    deleted = apply_retention(all_names, NOW, retention=retention)
    ## the newest 6 are 1 to 21 days old, of those only 21 is older than 20 days, and the
    ## newest 2 stay whatever their age
    assert sorted(deleted) == sorted(names([21, 30, 40]))
    far = names([100 + i for i in range(10)])
    assert sorted(apply_retention(far, NOW, retention=retention)) == sorted(far)[: 10 - 2]


def test_a_larger_count_keeps_more_backups():
    retention = Retention(keep_newest=60, keep_days=365, keep_always=5)
    ages = [i * 1.0 for i in range(60)] + [100 + i for i in range(10)]
    assert len(apply_retention(names(ages), NOW, retention=retention)) == 10
    assert apply_retention(names(ages[:60]), NOW, retention=retention) == []
    assert len(apply_retention(names(ages[:60]), NOW)) == 60 - KEEP_NEWEST


@pytest.mark.real_scrypt
def test_list_backups_newest_first_and_ignores_strangers(tmp_path: Path):
    names = ["20260101T000000Z.zip.age", "20261026T120000Z.zip.age", "20250606T060606Z.zip.age"]
    for name in names:
        write_encrypted(tmp_path, name, DATA, PASSWORD)
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / ".20261026T120001Z.zip.age.part").write_bytes(b"half")
    (tmp_path / "20261026T120002Z.zip.age").mkdir()
    (tmp_path / "20260230T000000Z.zip.age").write_bytes(b"no such day")
    backups = list_backups(tmp_path)
    assert [b.name for b in backups] == [
        "20261026T120000Z.zip.age",
        "20260101T000000Z.zip.age",
        "20250606T060606Z.zip.age",
    ]
    assert backups[0].time == datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)
    assert all(b.size > len(DATA) for b in backups)


@pytest.mark.real_scrypt
def test_list_backups_of_a_missing_directory_is_empty(tmp_path: Path):
    assert list_backups(tmp_path / "nowhere") == []


@pytest.mark.real_scrypt
def test_backup_names():
    when = datetime(2026, 10, 26, 12, 34, 56, 789, tzinfo=UTC)
    assert backup_name(when) == "20261026T123456Z.zip.age"
    assert backup_time("20261026T123456Z.zip.age") == when.replace(microsecond=0)
    assert backup_time("20261026T123456Z.zip") is None
    assert backup_time("x20261026T123456Z.zip.age") is None
    assert backup_time("20261345T000000Z.zip.age") is None
    assert backup_time("20260230T000000Z.zip.age") is None


ITEM_A: dict[str, Any] = {"id": "a", "revisionDate": "2026-10-26T12:00:00.000Z", "name": "A"}
ATTACHMENTS: list[dict[str, str]] = [
    {"id": "att1", "fileName": "x.txt", "url": "https://v/1?token=one"},
    {"id": "att2", "fileName": "y.txt", "url": "https://v/2?token=two"},
]
ITEM_B: dict[str, Any] = {
    "id": "b",
    "revisionDate": "2026-10-26T12:00:00.000Z",
    "name": "B",
    "attachments": ATTACHMENTS,
}
FOLDER = {"object": "folder", "id": "f1", "name": "Work"}
COLLECTION = {"object": "collection", "id": "c1", "organizationId": "o1", "name": "Shared"}
ORGANIZATION = {"object": "organization", "id": "o1", "name": "Family"}


def print_of(
    items=(ITEM_A, ITEM_B),
    folders=(FOLDER,),
    collections=(COLLECTION,),
    organizations=(ORGANIZATION,),
) -> str:
    return fingerprint(list(items), list(folders), list(collections), list(organizations))


def test_the_fingerprint_ignores_the_order_of_everything():
    swapped_attachments = {**ITEM_B, "attachments": list(reversed(ATTACHMENTS))}
    assert print_of() == print_of(items=(swapped_attachments, ITEM_A))
    assert print_of(folders=(FOLDER, {**FOLDER, "id": "f2"})) == print_of(
        folders=({**FOLDER, "id": "f2"}, FOLDER)
    )
    ## key order inside an object does not count either
    reordered = {"name": "Work", "id": "f1", "object": "folder"}
    assert print_of(folders=(reordered,)) == print_of()


def test_the_fingerprint_ignores_what_a_revision_date_fixes():
    """An item's content is covered by its revisionDate, and an attachment's url carries a token
    that differs between two listings of the same vault."""
    renamed = {**ITEM_A, "name": "Renamed without a new revisionDate"}
    assert print_of(items=(renamed, ITEM_B)) == print_of()
    attachments = [{**a, "url": a["url"] + "-other"} for a in ATTACHMENTS]
    assert print_of(items=(ITEM_A, {**ITEM_B, "attachments": attachments})) == print_of()


def test_the_fingerprint_changes_with_every_covered_change():
    base = print_of()
    seen = {base}
    for changed in [
        print_of(items=(ITEM_A,)),
        print_of(items=(ITEM_A, {**ITEM_B, "revisionDate": "2026-10-26T13:00:00.000Z"})),
        print_of(items=(ITEM_A, {**ITEM_B, "attachments": ATTACHMENTS[:1]})),
        print_of(items=(ITEM_A, {**ITEM_B, "collectionIds": ["c9"]})),
        print_of(items=(ITEM_A, {**ITEM_B, "organizationId": "o9"})),
        print_of(items=(ITEM_A, ITEM_B, {**ITEM_A, "id": "c"})),
        print_of(folders=()),
        print_of(folders=(FOLDER, {**FOLDER, "id": "f2", "name": "Home"})),
        print_of(folders=({**FOLDER, "name": "Renamed"},)),
        print_of(collections=()),
        print_of(collections=({**COLLECTION, "name": "Renamed"},)),
        print_of(organizations=()),
        print_of(organizations=({**ORGANIZATION, "name": "Renamed"},)),
    ]:
        assert changed not in seen
        seen.add(changed)


ITEM: dict[str, Any] = {
    "id": "i1",
    "revisionDate": "2026-11-05T06:03:29.533946Z",
    "folderId": "f1",
    "favorite": False,
    "organizationId": None,
    "collectionIds": [],
}
FOLDERS: list[dict[str, Any]] = [{"id": "f1", "name": "A"}, {"id": "f2", "name": "B"}]


def test_a_folder_move_without_a_new_revision_changes_the_fingerprint():
    before = fingerprint([ITEM], FOLDERS, [], [])
    moved = {**copy.deepcopy(ITEM), "folderId": "f2"}
    assert fingerprint([moved], FOLDERS, [], []) != before


def test_a_favorite_toggle_without_a_new_revision_changes_the_fingerprint():
    before = fingerprint([ITEM], FOLDERS, [], [])
    starred = {**copy.deepcopy(ITEM), "favorite": True}
    assert fingerprint([starred], FOLDERS, [], []) != before


def test_the_fingerprint_reads_a_missing_folder_and_favorite_as_none_and_false():
    """bw serve's listing leaves out null keys, and the two listings of one vault must agree."""
    bare = {k: v for k, v in ITEM.items() if k not in ("folderId", "favorite")}
    explicit = {**ITEM, "folderId": None, "favorite": False}
    assert fingerprint([bare], FOLDERS, [], []) == fingerprint([explicit], FOLDERS, [], [])
