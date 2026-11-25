"""The to-fill list (docs/spec.md, The tool note): the secret fields the user still has to fill in.

A new item may leave a secret field empty and a template adds its missing fields empty, so
bwtidy lists the secret columns left empty, and the user can mark or unmark one by hand. The list is
the toFill section of the tool note, saved like every section through a queued
setToolNoteSection: as changes (a column of an item added or removed) applied to the list as
the note holds it at write time. After every load of the vault it is pruned: a column that has
a value now, an item that is gone or in the trash, a column the item no longer has as a secret.
Only the changes between the list as the queued saves leave it and the list wanted are queued,
so a list that is already right never writes anything.

Nothing here reads a secret value beyond whether it is empty.
"""

import logging
import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Any

from bwtidy.items.coordinates import (
    GROUP_OF_TYPE,
    HIDDEN_FIELD,
    SECRET_PATHS,
    Coordinate,
    field_entries,
    parse,
)
from bwtidy.operations import (
    ApplyTemplate,
    ConvertItem,
    CreateItem,
    InvalidOperation,
    MoveToPersonal,
    Operation,
)
from bwtidy.queue.intake import VaultLocked
from bwtidy.queue.review import ReviewQueue
from bwtidy.sections import InvalidToolNote, SectionChange, read_sections, renamed_section
from bwtidy.sections.templates import FieldCoordinate, PathCoordinate, Template
from bwtidy.sections.to_fill import AddColumn, RemoveColumn, parse_to_fill
from bwtidy.vault import ToolNoteId, VaultCache, find_tool_note, template_named, tool_note_id

log = logging.getLogger(__name__)

TO_FILL_SUMMARY = "Update the to-fill list"
SECTION = "toFill"

LOGIN, CARD, SSH_KEY = 1, 3, 5

## the secret columns a new item of a raw type starts with empty (login.password unless generated)
RAW_SECRETS = {CARD: ("card.number", "card.code"), SSH_KEY: ("sshKey.privateKey",)}

type Entries = list[dict[str, Any]]


class RefusedMark(Exception):
    """A mark or unmark that is not allowed. The message says why and names no value."""


def _empty(value: Any) -> bool:
    return value is None or value == ""


def secret_slot(item: dict[str, Any], coordinate: Coordinate) -> bool | None:
    """Whether the item's secret column is empty, None when the item has no such secret column.

    A built-in secret leaf exists on every item of its type, a custom field only as a hidden
    field of the item. The value is only checked for being empty.
    """
    if coordinate.kind == "path":
        if coordinate.path not in SECRET_PATHS:
            return None
        group, _, key = coordinate.path.partition(".")
        if GROUP_OF_TYPE.get(item.get("type", 0)) != group:
            return None
        sub = item.get(group)
        return _empty(sub.get(key) if isinstance(sub, dict) else None)
    for found, entry in field_entries(item):
        if found == coordinate:
            if entry.get("type", 0) != HIDDEN_FIELD:
                return None
            return _empty(entry.get("value"))
    return None


def template_secrets(template: Template) -> list[str]:
    """The template's required secret coordinates as columns, in its order. An optional one may
    stay empty, so it is not asked for, and a field name pattern names no column to fill."""
    columns = []
    for coordinate in template.coordinates:
        if coordinate.optional:
            continue
        if isinstance(coordinate, FieldCoordinate) and coordinate.fieldType == HIDDEN_FIELD:
            columns.append(Coordinate.field(coordinate.name).display)
        elif isinstance(coordinate, PathCoordinate) and coordinate.path in SECRET_PATHS:
            columns.append(coordinate.path)
    return columns


def raw_secrets(op: CreateItem) -> list[str]:
    """The secret columns a new item of a raw type starts with empty. login.totp never."""
    if op.type == LOGIN:
        return [] if op.generatePassword else ["login.password"]
    return list(RAW_SECRETS.get(op.type or 0, ()))


def merged(entries: Entries, additions: dict[str, list[str]], since: str) -> Entries:
    """The entries with the added columns, a new entry per new item, each column once."""
    result: Entries = [{**entry, "columns": list(entry["columns"])} for entry in entries]
    by_item = {entry["itemId"]: entry for entry in result}
    for item_id, columns in additions.items():
        entry: dict[str, Any] | None = by_item.get(item_id)
        if entry is None:
            entry = {"itemId": item_id, "columns": [], "since": since}
            by_item[item_id] = entry
            result.append(entry)
        entry["columns"] += [column for column in columns if column not in entry["columns"]]
    return [entry for entry in result if entry["columns"]]


def pruned(entries: Entries, cache: VaultCache, remembered: ToolNoteId) -> Entries:
    """The entries as the vault is now: only empty secret columns of active items.

    An item that is gone or in the trash (not among the cache's items) loses its entry, and so
    does the tool note. An entry with no column left is removed.
    """
    note_id = tool_note_id(cache, remembered)
    items = cache.items_by_id()
    result = []
    for entry in entries:
        item = items.get(entry["itemId"])
        if item is None or entry["itemId"] == note_id:
            continue
        columns = [column for column in entry["columns"] if secret_slot(item, parse(column))]
        if columns:
            result.append({**entry, "columns": columns})
    return result


def changes_between(before: Entries, after: Entries) -> list[SectionChange]:
    """The changes that turn the list before into after: each column of an item taken off,
    then each one added (with the since of its entry in after)."""
    had = {entry["itemId"]: entry["columns"] for entry in before}
    has = {entry["itemId"]: entry["columns"] for entry in after}
    changes: list[SectionChange] = []
    for entry in before:
        item_id, kept = entry["itemId"], has.get(entry["itemId"], [])
        changes += [
            RemoveColumn(op="removeColumn", itemId=item_id, column=column)
            for column in entry["columns"]
            if column not in kept
        ]
    for entry in after:
        item_id, held = entry["itemId"], had.get(entry["itemId"], [])
        changes += [
            AddColumn(op="addColumn", itemId=item_id, column=column, since=entry["since"])
            for column in entry["columns"]
            if column not in held
        ]
    return changes


def unsaved(added: dict[str, list[str]], wanted: Entries, stored: Entries) -> dict[str, list[str]]:
    """The added columns still on the wanted list that the stored list does not hold yet."""
    listed = {entry["itemId"]: entry["columns"] for entry in wanted}
    held = {entry["itemId"]: entry["columns"] for entry in stored}
    result = {}
    for item_id, columns in added.items():
        rest = [
            column
            for column in columns
            if column in listed.get(item_id, []) and column not in held.get(item_id, [])
        ]
        if rest:
            result[item_id] = rest
    return result


class ToFill:
    """The to-fill list of one session's vault, and what changes it.

    The columns a commit left empty and the cells the user marked are kept in RAM (`_added`) until
    the tool note itself holds them. Every load of the vault (the reload after the writes)
    merges them into the list and prunes it, so a save that failed is queued again. An unmark
    takes its column out of them, or the next load would list it again.
    """

    def __init__(
        self, cache: VaultCache, queue: ReviewQueue, today: Callable[[], date] = date.today
    ) -> None:
        self.cache = cache
        self.queue = queue
        self._today = today
        self._added: dict[str, list[str]] = {}

    def with_queued_saves(self) -> Entries:
        """The tool note's list with every queued save still under way applied to it.

        A section that is not valid (edited by hand) reads as an empty list, the next save
        replaces it. A queue that cannot be read counts as no save under way.
        """
        try:
            saving, value = self.queue.saving_section(SECTION)
        except (OSError, sqlite3.Error) as error:
            log.warning("Could not read the queued to-fill lists: %s", type(error).__name__)
            saving = False
        if not saving:
            return self.stored()
        try:
            return parse_to_fill(value)
        except InvalidToolNote as error:
            log.warning("The to-fill list of the bwtidy note is not valid: %s", error)
            return []

    def stored(self) -> Entries:
        """The list the tool note holds, whatever saves are still queued, the ids of items a
        conversion of this session replaced followed to the new ones."""
        note = find_tool_note(self.cache, self.queue.remembered)
        if note is None:
            return []
        try:
            stored = parse_to_fill(read_sections(note.get("notes")).get(SECTION, []))
        except InvalidToolNote as error:
            log.warning("The to-fill list of the bwtidy note is not valid: %s", error)
            return []
        return renamed_section(SECTION, stored, self.cache.own.converted)

    def current(self) -> Entries:
        """The list as the vault is now, even before the pruned list is written."""
        return pruned(
            merged(self.with_queued_saves(), self._added, self._since()),
            self.cache,
            self.queue.remembered,
        )

    def by_item(self) -> dict[str, list[str]]:
        return {entry["itemId"]: entry["columns"] for entry in self.current()}

    def _since(self) -> str:
        return self._today().isoformat()

    def note_written(self, op: Operation, created: list[str]) -> None:
        """Remember the secret columns a written createItem or applyTemplate may have left
        empty, under the new item's id when it made one. The next load checks them. What was
        remembered for an item a conversion replaced moves to the new item."""
        if isinstance(op, ConvertItem | MoveToPersonal | ApplyTemplate) and created:
            self._move_added(op.itemId, created[0])
        if isinstance(op, CreateItem):
            if not created:
                return
            if op.template is not None:
                columns = template_secrets(
                    template_named(self.cache, self.queue.remembered, op.template)
                )
            else:
                columns = raw_secrets(op)
            item_id = created[0]
        elif isinstance(op, ApplyTemplate):
            columns = template_secrets(
                template_named(self.cache, self.queue.remembered, op.template)
            )
            item_id = created[0] if created else op.itemId
        else:
            return
        if columns:
            known = self._added.setdefault(item_id, [])
            known += [column for column in columns if column not in known]

    def _move_added(self, old_id: str, new_id: str) -> None:
        moved = self._added.pop(old_id, [])
        if moved:
            known = self._added.setdefault(new_id, [])
            known += [column for column in moved if column not in known]

    def after_load(self) -> None:
        """Merge what the writes left empty, prune, and queue the changes when it changed.

        What was added stays in RAM until the tool note holds it, so a save that failed or could
        not be queued is queued again at the next load. A list that cannot be queued is only
        logged, it never fails the load or the write.
        """
        if find_tool_note(self.cache, self.queue.remembered) is None:
            return
        with_queued_saves = self.with_queued_saves()
        wanted = pruned(
            merged(with_queued_saves, self._added, self._since()), self.cache, self.queue.remembered
        )
        self._added = unsaved(self._added, wanted, self.stored())
        changes = changes_between(with_queued_saves, wanted)
        if changes:
            self._save(changes)

    def mark(self, item_id: str, column: str, mark: bool) -> int | None:
        """Add a column of an item to the list or take it off. Returns the queued entry's id,
        None when the list stays as it is. Raises RefusedMark, VaultLocked."""
        note = find_tool_note(self.cache, self.queue.remembered)
        if note is not None and item_id == note.get("id"):
            raise RefusedMark("the bwtidy note has no fields to fill in")
        item = self.cache.items_by_id().get(item_id)
        if item is None:
            raise RefusedMark(f"no item {item_id}")
        try:
            coordinate = parse(column)
        except ValueError:
            raise RefusedMark(f"{column} is no column") from None
        empty = secret_slot(item, coordinate)
        if empty is None:
            raise RefusedMark(f"{column} is no secret field of this item")
        if mark and not empty:
            raise RefusedMark(f"{column} has a value already")
        current = self.current()
        if mark:
            wanted = merged(current, {item_id: [coordinate.display]}, self._since())
        else:
            wanted = merged(
                [
                    {**entry, "columns": [c for c in entry["columns"] if c != coordinate.display]}
                    if entry["itemId"] == item_id
                    else entry
                    for entry in current
                ],
                {},
                self._since(),
            )
        entry_id = None
        changes = changes_between(self.with_queued_saves(), wanted)
        if changes:
            entry_id = self.queue.enqueue_section_changes(SECTION, changes, TO_FILL_SUMMARY)
        self._remember(item_id, coordinate.display, mark)
        return entry_id

    def _remember(self, item_id: str, column: str, mark: bool) -> None:
        """Keep a marked column with the additions, take an unmarked one out of them."""
        known = self._added.get(item_id, [])
        if mark:
            if column not in known:
                self._added[item_id] = [*known, column]
        elif column in known:
            rest = [c for c in known if c != column]
            if rest:
                self._added[item_id] = rest
            else:
                del self._added[item_id]

    def _save(self, changes: list[SectionChange]) -> None:
        try:
            self.queue.enqueue_section_changes(SECTION, changes, TO_FILL_SUMMARY)
        except (InvalidOperation, VaultLocked) as error:
            log.warning("Could not queue the to-fill list: %s", error)
        except (OSError, sqlite3.Error) as error:
            log.warning("Could not queue the to-fill list: %s", type(error).__name__)
