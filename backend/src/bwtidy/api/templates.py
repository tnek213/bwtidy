"""Template routes: read and change the templates in the tool note, and the matches."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from bwtidy.api.app_state import app_state
from bwtidy.api.deps import Session, require_tool_note
from bwtidy.api.sections import body_changes, current_datatypes, current_templates, queue_changes
from bwtidy.operations import SetLeaf
from bwtidy.queue.view import TEMPLATES_SUMMARY
from bwtidy.sections.matching import datatype_warnings, duplicate_warnings
from bwtidy.sections.templates import (
    InvalidTemplates,
    template_list,
)
from bwtidy.vault import TOOL_NOTE_NAME, find_tool_note, stored_templates, template_matches

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/templates")
async def get_templates(request: Request, session: Session) -> dict[str, Any]:
    """The templates and the tool note's revisionDate.

    A save that is still queued or being committed shows its templates with "saving" true,
    else the stored ones with "saving" false. Notes that hold no valid templates (edited by
    hand) give an empty list and the problem in "error", so the page can still add templates
    (they start the section over). toolNoteId and toolNoteName name the note, so the grid can
    protect its row and warn about a rename. "warnings" names a datatype that is unknown or
    broken and a template that scores like one listed before it, only when there is one.
    """
    note = require_tool_note(session)
    templates, saving, error = current_templates(request, session)
    answer: dict[str, Any] = {
        "templates": [],
        "revisionDate": note.get("revisionDate"),
        "toolNoteId": note.get("id"),
        "toolNoteName": note.get("name"),
        "saving": saving,
    }
    if error:
        answer["error"] = error
        return answer
    answer["templates"] = template_list(templates)
    datatypes = current_datatypes(request, session)[0]
    warnings = [*datatype_warnings(templates, datatypes), *duplicate_warnings(templates, datatypes)]
    if warnings:
        answer["warnings"] = warnings
    return answer


@router.put("/templates", status_code=202)
async def put_templates(request: Request, body: dict[str, Any], session: Session) -> dict[str, Any]:
    """Queue the changes of the templates (add, set, rename, move or delete one template, by
    name) as one UI entry and start a commit at once, in the background. Answers the templates
    as the saves under way leave them.

    The write applies them to the templates section of the note as it is then, so no
    revisionDate is needed (one in the body is ignored), and a change for a template another
    save renamed or deleted fails its entry. The whole list of before is a 422.
    """
    changes = body_changes(body, "templates")
    require_tool_note(session)
    entry_id = queue_changes(request, "templates", changes, TEMPLATES_SUMMARY)
    _, templates = app_state(request).queue.saving_section("templates")
    return {"entryId": entry_id, "templates": templates}


@router.post("/tool-note/rename-back")
async def rename_tool_note_back(
    request: Request, body: dict[str, Any], session: Session
) -> dict[str, Any]:
    """Give the tool note its name bwtidy again, straight to the write path, without the queue."""
    revision_date = body.get("revisionDate")
    if not isinstance(revision_date, str) or not revision_date:
        raise HTTPException(status_code=422, detail="revisionDate of the bwtidy note is missing")
    note = require_tool_note(session)
    op = SetLeaf(
        kind="setLeaf",
        itemId=note["id"],
        revisionDate=revision_date,
        column="name",
        value=TOOL_NOTE_NAME,
    )
    result = await app_state(request).writes.apply([op], "ui", edits_tool_note=True)
    saved = find_tool_note(session.cache, session.tool_note_id) or note
    return {
        "toolNoteName": saved.get("name"),
        "revisionDate": saved.get("revisionDate"),
        "backup": result.backup,
    }


@router.get("/template-matches")
async def get_template_matches(request: Request, session: Session) -> dict[str, dict[str, Any]]:
    """Per item: the matching template or None, and the fill score for every template.

    Stored templates that are not valid match nothing.
    """
    try:
        templates = stored_templates(session.cache, session.tool_note_id)
    except InvalidTemplates:
        templates = []
    return template_matches(session.cache, app_state(request).formats, templates)
