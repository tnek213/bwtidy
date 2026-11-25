"""The writes of the tool note's notes: a section save and the move of a converted item's
entries to its new id, both through _write_note (docs/spec.md, The bwtidy note).

The note is read from bw serve right before the write, so the saves of one batch build on each
other, and it is PUT only when the notes changed. generator reads the generator settings the
note holds now, for the values a write generates.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from bwtidy.bw import BwError
from bwtidy.operations import SetToolNoteSection
from bwtidy.sections import (
    SECTIONS,
    InvalidToolNote,
    dump_sections,
    read_sections,
    renamed_section,
)
from bwtidy.sections.generator import GeneratorSettings, InvalidGenerator, stored_generator
from bwtidy.vault import tool_note_id
from bwtidy.writes.saves import saved_notes

if TYPE_CHECKING:
    from bwtidy.writes.path import Write, WriteContext

log = logging.getLogger(__name__)


async def write_set_section(path: WriteContext, write: Write, op: SetToolNoteSection) -> None:
    """Save only the one section of the tool note's notes.

    The changes are applied to the section as the note holds it then (saved_notes), so
    entries of one batch and saves built on the same base each keep the others, and no
    revisionDate of the note is needed. A change that no longer applies (a template another
    save renamed, a datatype a template took meanwhile) fails the entry saying why.
    """
    await _write_note(path, write, lambda notes: saved_notes(notes, op, path.cache))


async def _write_note(
    path: WriteContext, write: Write, change: Callable[[str | None], str | None]
) -> None:
    """The one place that writes the tool note's notes: GET the note as it is now, PUT it
    with change(notes) as its notes.

    Nothing is written when the notes stay as they are: a PUT of the same notes would give
    the note a new revisionDate for nothing, and a conversion of an item without a
    generator setting or to-fill entry must leave the note alone.
    """
    await path.fresh(write)
    full = await _tool_note_now(path)
    if full is None:
        raise ValueError("the bwtidy note is missing, log in again")
    notes = full.get("notes")
    saved = change(notes)
    if saved == notes:
        return
    full["notes"] = saved
    await path.put(write, full["id"], full)


async def _tool_note_now(path: WriteContext) -> dict[str, Any] | None:
    """The tool note as bw serve has it now, an earlier section save of this batch
    included. None when there is none."""
    note_id = tool_note_id(path.cache, path.remembered)
    if note_id is None:
        return None
    return await path.past_own_write(note_id, path.client.get_item)


async def generator(path: WriteContext) -> GeneratorSettings:
    """The generator settings as the tool note holds them now. Notes that hold no valid
    settings (edited by hand) give the default, as no section does."""
    note = await _tool_note_now(path)
    try:
        return stored_generator(note.get("notes") if note else None)
    except (InvalidToolNote, InvalidGenerator):
        log.warning("The generator settings in the bwtidy note are not valid, using the default")
        return GeneratorSettings()


async def follow_conversion(path: WriteContext, write: Write, old_id: str, new_id: str) -> None:
    """Give the new item of a conversion the generator setting and the to-fill entry of
    the old one.

    The cache remembers the new id, so a queued change of either section that names the
    old one lands on the new one at its write. Both are moved in the note as it is now in
    one write, like a section save, as part of the conversion. Nothing is written when the
    old item has neither. The conversion is done by then, so a failure here is logged and
    does not fail it (the next save of the section moves it then).
    """
    path.cache.own.record_conversion(old_id, new_id)
    if tool_note_id(path.cache, path.remembered) is None:
        return

    def rename(item_id: str) -> str:
        return new_id if item_id == old_id else item_id

    def moved(notes: str | None) -> str | None:
        sections = read_sections(notes)
        renamed = {
            **sections,
            **{
                section: renamed_section(section, sections[section], rename)
                for section in SECTIONS
                if section in sections
            },
        }
        return notes if renamed == sections else dump_sections(renamed)

    try:
        await _write_note(path, write, moved)
    except InvalidToolNote:
        return
    except BwError as error:
        log.warning("Could not move the item's entries in the bwtidy note: %s", error)
