"""A tool note section as the saves still under way leave it.

folded_section is the one fold of section saves over the notes, for the diff of a save
(view.py) and for the current view of a section (saving_section), which the templates,
datatypes and generator routes (api/sections.py) and the to-fill list (to_fill.py) read.
"""

import json
import logging
from collections.abc import Sequence
from typing import Any

from bwtidy.operations import InvalidOperation, Operation, SetToolNoteSection
from bwtidy.queue.state import APPROVED, PENDING, UI, QueueState, as_operation
from bwtidy.sections import Section, renamed_section
from bwtidy.vault import VaultCache, find_tool_note
from bwtidy.writes import section_of, section_saved, without_gone

log = logging.getLogger(__name__)


def folded_section(
    section: str, notes: str | None, saves: Sequence[SetToolNoteSection], cache: VaultCache
) -> Any:
    """The section as the saves leave the notes, applied one after the other like their writes
    will apply them: item ids followed through the session's conversions, the generator
    settings of items that are gone dropped. A save that no longer applies is left out."""
    value = renamed_section(section, section_of(notes, section), cache.own.converted)
    for save in saves:
        try:
            value = without_gone(section, section_saved(save, value, cache.own.converted), cache)
        except ValueError as error:
            log.warning("A queued save of the %s section does not apply: %s", section, error)
    return value


def saving_section(q: QueueState, section: Section) -> tuple[bool, Any]:
    """Whether a save of the section is still pending or being committed, and the section
    as every such save leaves the tool note (folded_section), the note's own without one."""
    saves = section_saves(q, section)
    note = find_tool_note(q.cache, q.remembered)
    notes = note.get("notes") if note is not None else None
    return bool(saves), folded_section(section, notes, saves, q.cache)


def section_saves(
    q: QueueState, section: str | None, before: int | None = None
) -> list[SetToolNoteSection]:
    """The saves of the section (of every section for None) still pending or being
    committed, in queue order, only those queued before the entry before when it is
    given."""
    saves = []
    for row in q.db.queue_entries(statuses=(PENDING, APPROVED), source=UI):
        if before is not None and row["id"] >= before:
            break
        operation = json.loads(row["operation_json"])
        if not (
            row["edits_tool_note"]
            and operation.get("kind") == "setToolNoteSection"
            and section in (None, operation.get("section"))
        ):
            continue
        try:
            op = as_operation(operation)
        except InvalidOperation:
            log.warning("Queue entry %d holds no valid section save", row["id"])
            continue
        assert isinstance(op, SetToolNoteSection)
        saves.append(op)
    return saves


def earlier_saves(q: QueueState, entry_id: int, op: Operation) -> list[SetToolNoteSection]:
    """For a section save, the saves of its section queued before it (see diff)."""
    if not isinstance(op, SetToolNoteSection):
        return []
    return section_saves(q, op.section, before=entry_id)
