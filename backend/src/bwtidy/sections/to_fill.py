"""The toFill section (docs/spec.md, The tool note): the to-fill list as the note stores it, and
its changes. queue/to_fill.py keeps the list up to date.
"""

import re
from collections.abc import Sequence
from typing import Any, Literal

from bwtidy.items.coordinates import SECRET_PATHS, parse
from bwtidy.sections.notes import (
    Change,
    ChangeModel,
    InvalidToolNote,
    Rename,
    SectionRules,
    each_change,
)

SECTION = "toFill"
TO_FILL_KEYS = frozenset({"itemId", "columns", "since"})
SINCE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _to_fill_column(column: Any, label: str) -> str:
    if not isinstance(column, str):
        raise InvalidToolNote(f"{label}: a column is no string")
    try:
        coordinate = parse(column)
    except ValueError:
        raise InvalidToolNote(f"{label}: {column} is no column") from None
    ## a custom field is secret by its type in the item, which is checked when the vault loads
    if coordinate.kind == "path" and coordinate.path not in SECRET_PATHS:
        raise InvalidToolNote(f"{label}: {column} is no secret column")
    return coordinate.display


def parse_to_fill(value: Any) -> list[dict[str, Any]]:
    """The toFill section checked: a list of {itemId, columns, since}, at most one per item.

    columns are secret columns (grid column ids), at least one and each once, since is the
    day the entry was added (YYYY-MM-DD). Raises InvalidToolNote naming the first problem.
    """
    if not isinstance(value, list):
        raise InvalidToolNote("toFill is not a list")
    entries = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        label = f"toFill[{index}]"
        if not isinstance(entry, dict) or set(entry) != TO_FILL_KEYS:
            raise InvalidToolNote(f"{label}: an entry has exactly itemId, columns and since")
        item_id, columns, since = entry["itemId"], entry["columns"], entry["since"]
        if not isinstance(item_id, str) or not item_id:
            raise InvalidToolNote(f"{label}: itemId is empty")
        if item_id in seen:
            raise InvalidToolNote(f"{label}: there is more than one entry for {item_id}")
        seen.add(item_id)
        if not isinstance(columns, list) or not columns:
            raise InvalidToolNote(f"{label}: columns is empty")
        parsed = [_to_fill_column(column, label) for column in columns]
        if len(set(parsed)) < len(parsed):
            raise InvalidToolNote(f"{label}: a column is listed twice")
        if not isinstance(since, str) or not SINCE.match(since):
            raise InvalidToolNote(f"{label}: since is no date like 2026-10-30")
        entries.append({"itemId": item_id, "columns": parsed, "since": since})
    return entries


class AddColumn(ChangeModel):
    """One column of one item added to the to-fill list. since is the day of a new entry, an
    entry the item has keeps its own."""

    op: Literal["addColumn"]
    itemId: str
    column: str
    since: str


class RemoveColumn(ChangeModel):
    """One column of one item taken off the to-fill list, the entry with it once it is empty."""

    op: Literal["removeColumn"]
    itemId: str
    column: str


CHANGES = frozenset({"addColumn", "removeColumn"})


def check_changes(changes: Sequence[Change]) -> None:
    """Raise ValueError naming the first change that does not fit the section."""
    for label, change in each_change(SECTION, CHANGES, changes):
        if not isinstance(change, AddColumn | RemoveColumn):
            continue
        if not change.itemId:
            raise ValueError(f"{label}: itemId is empty")
        try:
            _to_fill_column(change.column, label)
        except InvalidToolNote as error:
            raise ValueError(str(error)) from None
        if isinstance(change, AddColumn) and not SINCE.match(change.since):
            raise ValueError(f"{label}: since is no date like 2026-10-30")


def _renamed_to_fill(entries: list[dict[str, Any]], rename: Rename) -> list[dict[str, Any]]:
    """The entries with every item id followed through rename, each keeping its since. An
    entry already under the new id takes the old one's columns."""
    result: list[dict[str, Any]] = []
    by_item: dict[str, dict[str, Any]] = {}
    for entry in entries:
        item_id = rename(entry["itemId"])
        found = by_item.get(item_id)
        if found is None:
            found = {**entry, "itemId": item_id, "columns": list(entry["columns"])}
            by_item[item_id] = found
            result.append(found)
        else:
            found["columns"] += [c for c in entry["columns"] if c not in found["columns"]]
    return result


def renamed(value: Any, rename: Rename) -> Any:
    """The list with its item ids followed through rename. No list, a list that does not parse
    and one where no id changes come back as they are."""
    if value is None:
        return value
    try:
        entries = parse_to_fill(value)
    except InvalidToolNote:
        return value
    if all(rename(entry["itemId"]) == entry["itemId"] for entry in entries):
        return value
    return _renamed_to_fill(entries, rename)


def changed(value: Any, changes: Sequence[Change], rename: Rename) -> Any:
    """The list with the changes applied. A list that does not parse starts over empty, as a
    save of the whole list replaced it. Raises InvalidToolNote for no secret column."""
    try:
        entries = parse_to_fill(value)
    except InvalidToolNote:
        entries = []
    entries = _renamed_to_fill(entries, rename)
    for change in changes:
        if not isinstance(change, AddColumn | RemoveColumn):
            continue
        item_id = rename(change.itemId)
        column = _to_fill_column(change.column, "toFill")
        entry = next((e for e in entries if e["itemId"] == item_id), None)
        if isinstance(change, AddColumn):
            if entry is None:
                entries.append({"itemId": item_id, "columns": [column], "since": change.since})
            elif column not in entry["columns"]:
                entry["columns"].append(column)
        elif entry is not None and column in entry["columns"]:
            entry["columns"].remove(column)
            if not entry["columns"]:
                entries.remove(entry)
    return entries


RULES = SectionRules(
    changes=CHANGES,
    parse=parse_to_fill,
    check_changes=check_changes,
    changed=changed,
    renamed=renamed,
)
