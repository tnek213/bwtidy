"""The backup files of an account: their listing, their names, the retention and the fingerprint.

A backup is one file <UTC timestamp>.zip.age (protocol.backup_name). Every backup carries a
fingerprint of the vault it holds (see fingerprint), stored next to its name in SQLite, so a
write can reuse a backup taken earlier while the vault has not changed.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from bwtidy.config import DEFAULT_RETENTION, Retention
from bwtidy.protocol import backup_name, backup_time
from bwtidy.vault import Objects


class BackupError(Exception):
    """A backup step failed. The message names the step and never carries vault content."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"Backup failed at {step}: {detail}")
        self.step = step
        self.detail = detail


@dataclass(frozen=True)
class BackupInfo:
    name: str
    size: int
    time: datetime
    ## None for a backup only seen in the directory (list_backups), see fingerprint
    fingerprint: str | None = None


class FingerprintStore(Protocol):
    """Where create_backup keeps the fingerprint of every backup (the SQLite change log)."""

    def set_backup_fingerprint(self, name: str, fingerprint: str) -> None: ...


def fingerprint(
    items: Objects,
    folders: Objects,
    collections: Objects,
    organizations: Objects,
    *,
    archived: Objects,
) -> str:
    """A sha256 hex over what a backup covers, the same for the same vault whatever the order.

    The archived items that are not in the trash count like the active items, on their own, so
    archiving an item changes the fingerprint even when its revisionDate stays. Of every item
    only its id, revisionDate, folder, favorite, organization, collections and attachment ids
    count: the rest of an item is fixed by its revisionDate, and an attachment's url carries a
    token that differs between two listings. The folder and the favorite count on their own
    because the clients' partial update changes them without a new revisionDate. The
    collections count on their own because Vaultwarden changes an item's collections without a
    new revisionDate. Folders, collections and organizations count whole, they have no
    revisionDate of their own.
    """
    covered = {
        "items": _item_keys(items),
        "archived": _item_keys(archived),
        "folders": sorted(json.dumps(f, sort_keys=True) for f in folders),
        "collections": sorted(json.dumps(c, sort_keys=True) for c in collections),
        "organizations": sorted(json.dumps(o, sort_keys=True) for o in organizations),
    }
    return hashlib.sha256(json.dumps(covered, sort_keys=True).encode()).hexdigest()


def _item_keys(items: Objects) -> list[str]:
    return sorted(
        json.dumps(
            [
                item.get("id"),
                item.get("revisionDate"),
                item.get("folderId") or "",
                bool(item.get("favorite")),
                item.get("organizationId") or "",
                sorted(item.get("collectionIds") or []),
                sorted(a.get("id") or "" for a in item.get("attachments") or []),
            ]
        )
        for item in items
    )


def list_backups(directory: Path) -> list[BackupInfo]:
    """Every backup in the directory, newest first. Other files are ignored."""
    backups = []
    if not directory.is_dir():
        return backups
    for path in directory.iterdir():
        time = backup_time(path.name)
        if time is not None and path.is_file():
            backups.append(BackupInfo(path.name, path.stat().st_size, time))
    backups.sort(key=lambda b: b.time, reverse=True)
    return backups


def free_name(directory: Path, now: datetime) -> str:
    """The backup name for now, moved forward by whole seconds while that file exists."""
    when = now.astimezone(UTC).replace(microsecond=0)
    while (directory / backup_name(when)).exists():
        when += timedelta(seconds=1)
    return backup_name(when)


def apply_retention(
    names: list[str],
    now: datetime,
    keep: frozenset[str] = frozenset(),
    retention: Retention = DEFAULT_RETENTION,
) -> list[str]:
    """The backup file names to delete: not among the newest keep_newest or older than keep_days.

    The newest keep_always always stay, whatever their age. A name in keep (a backup still in
    use) stays too, beyond the count and the days, but counts among the newest like any other.
    The defaults are 40, 90 days and 5. Names that are not backups are never returned. Pure,
    the caller deletes.
    """
    dated = sorted(
        ((time, name) for name in names if (time := backup_time(name)) is not None),
        reverse=True,
    )
    oldest_kept = now - timedelta(days=retention.keep_days)
    return [
        name
        for index, (time, name) in enumerate(dated)
        if index >= retention.keep_always
        and (index >= retention.keep_newest or time < oldest_kept)
        and name not in keep
    ]


def prune(
    directory: Path,
    now: datetime | None = None,
    keep: frozenset[str] = frozenset(),
    retention: Retention = DEFAULT_RETENTION,
) -> list[str]:
    """Delete what apply_retention says and return the deleted names."""
    names = [b.name for b in list_backups(directory)]
    deleted = []
    for name in apply_retention(names, now or datetime.now(UTC), keep, retention):
        try:
            (directory / name).unlink()
        except FileNotFoundError:
            continue
        deleted.append(name)
    return deleted
