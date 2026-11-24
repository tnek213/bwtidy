"""A section save on the tool note's notes (docs/spec.md, Writes, setToolNoteSection): its
check and the notes as its write leaves them, for the write path and the queue alike.

Each step asks the section's rules in SECTIONS. The write path needs a ValueError for a save
that does not fit, so InvalidToolNote and InvalidTemplates, which are none, become one here.
"""

from typing import Any

from bwtidy.operations import SetToolNoteSection
from bwtidy.sections import SECTIONS, InvalidToolNote, Rename, read_sections, with_section
from bwtidy.sections.templates import InvalidTemplates
from bwtidy.vault import VaultCache


def check_section(op: SetToolNoteSection) -> None:
    """Raise ValueError when the changes do not fit the section."""
    SECTIONS[op.section].check_changes(op.changes)


def check_section_value(section: str, value: Any) -> None:
    """Raise ValueError when the value does not parse as the section."""
    try:
        SECTIONS[section].parse(value)
    except (InvalidToolNote, InvalidTemplates) as error:
        raise ValueError(str(error)) from None


def section_of(notes: str | None, section: str) -> Any:
    """The section's value in the notes, None when they hold none or are no sections JSON."""
    try:
        return read_sections(notes).get(section)
    except InvalidToolNote:
        return None


def section_saved(op: SetToolNoteSection, value: Any, rename: Rename) -> Any:
    """The section's value after the save: its changes applied. Raises ValueError saying why
    when a change no longer applies (a template another save renamed)."""
    try:
        return SECTIONS[op.section].changed(value, op.changes, rename)
    except (InvalidToolNote, InvalidTemplates) as error:
        raise ValueError(str(error)) from None


def without_gone(section: str, value: Any, cache: VaultCache) -> Any:
    """The section without what belongs to items that are gone (the trash counts), for a section
    with such a rule (the generator settings).

    The cache holds the vault as the batch found it, so an item a conversion made since counts
    as there unless it went to the trash.
    """
    drop = SECTIONS[section].without_gone
    if drop is None:
        return value
    made = set(cache.own.conversions.values()) - cache.trash_ids
    return drop(value, cache.items_by_id().keys() | made)


def saved_notes(notes: str | None, op: SetToolNoteSection, cache: VaultCache) -> str:
    """The notes after the section save, as its write leaves them: its changes applied to the
    section the notes hold, item ids followed through the session's conversions, the generator
    settings of items that are gone dropped, and the whole section checked, also against the
    rest of the notes (a deleted datatype against the templates). Raises ValueError saying
    why."""
    before = section_of(notes, op.section)
    value = without_gone(op.section, section_saved(op, before, cache.own.converted), cache)
    check_section_value(op.section, value)
    check_in_notes = SECTIONS[op.section].check_in_notes
    if check_in_notes is not None:
        check_in_notes(notes, before, value)
    return with_section(notes, op.section, value)
