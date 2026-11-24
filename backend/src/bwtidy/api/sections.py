"""What the templates, datatypes and generator routes share for their section of the tool note:
the changes of a request with their queue entry, and the section as the saves under way leave
it (docs/spec.md, Writes, setToolNoteSection).

A save holds changes of single entries, never the whole list, so two saves built on the same
list (two tabs) both land.
"""

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request

from bwtidy.api.app_state import app_state
from bwtidy.api.deps import require_tool_note
from bwtidy.operations import InvalidOperation
from bwtidy.sections import CHANGES_OF, InvalidToolNote, Section, SectionChange, parse_changes
from bwtidy.sections.datatypes import (
    Datatype,
    InvalidDatatypes,
    load_datatypes,
    stored_datatypes,
)
from bwtidy.sections.generator import (
    GeneratorSettings,
    InvalidGenerator,
    parse_generator,
    stored_generator,
)
from bwtidy.sections.templates import InvalidTemplates, Template, load_templates, parse_templates
from bwtidy.session import SessionManager
from bwtidy.vault import find_tool_note


def body_changes(body: dict[str, Any], section: Section) -> list[SectionChange]:
    """The changes of the request body. A body with the whole list instead of `changes` is a 422
    that says so."""
    if "changes" not in body and section in body:
        known = ", ".join(sorted(CHANGES_OF[section]))
        raise HTTPException(
            status_code=422,
            detail=f"The {section} are saved as changes ({known}), not as the whole list",
        )
    try:
        return parse_changes(body.get("changes"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


def queue_changes(
    request: Request, section: Section, changes: list[SectionChange], summary: str
) -> int:
    """Queue the changes as one UI entry and start a commit at once, in the background. The
    entry's id. A change that does not apply to the section as the saves under way leave it
    is a 422 saying why."""
    try:
        entry_id = app_state(request).queue.enqueue_section_changes(section, changes, summary)
    except InvalidOperation as error:
        raise HTTPException(status_code=422, detail=error.reason) from None
    app_state(request).auto_commit.start_commit()
    return entry_id


def _current_section[T](
    request: Request,
    session: SessionManager,
    section: Section,
    from_saves: Callable[[Any], T],
    from_notes: Callable[[str | None], T],
    invalid: tuple[type[Exception], ...],
    none: T,
) -> tuple[T, bool, str]:
    """The section as the saves under way leave it (from_saves reads their folded value), else
    as the tool note stores it (from_notes), whether a save is under way, and the problem with a
    section that cannot be read (it reads as none)."""
    note = require_tool_note(session)
    saving, folded = app_state(request).queue.saving_section(section)
    try:
        value = from_saves(folded) if saving else from_notes(note.get("notes"))
    except invalid as error:
        return none, saving, str(error)
    return value, saving, ""


def current_templates(
    request: Request, session: SessionManager
) -> tuple[list[Template], bool, str]:
    """The templates as the saves under way leave them, whether a save is under way, and the
    problem with a section that cannot be read."""
    return _current_section(
        request, session, "templates", parse_templates, load_templates, (InvalidTemplates,), []
    )


def current_datatypes(
    request: Request, session: SessionManager
) -> tuple[list[Datatype], bool, str]:
    """The datatypes as the saves under way leave them, whether a save is under way, and the
    problem with a section that cannot be read."""
    return _current_section(
        request,
        session,
        "datatypes",
        load_datatypes,
        stored_datatypes,
        (InvalidToolNote, InvalidDatatypes),
        [],
    )


def current_generator(
    request: Request, session: SessionManager
) -> tuple[GeneratorSettings, bool, str]:
    """The generator settings as the saves under way leave them, whether a save is under way,
    and the problem with stored settings that are not valid (they read as none)."""
    return _current_section(
        request,
        session,
        "generator",
        parse_generator,
        stored_generator,
        (InvalidToolNote, InvalidGenerator),
        GeneratorSettings(),
    )


def own_generator_ids(request: Request, session: SessionManager) -> set[str]:
    """The ids of the items with a setting of their own, from the view the routes use (the
    stored section folded with a save under way). A section that cannot be read has none."""
    if find_tool_note(session.cache, session.tool_note_id) is None:
        return set()
    settings, _, _ = current_generator(request, session)
    return set(settings.items)
