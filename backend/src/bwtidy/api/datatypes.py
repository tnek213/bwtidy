"""Datatype routes: the datatypes section of the tool note and the checks behind the live previews
(docs/spec.md, Datatypes).

A save queues changes of single datatypes (add, set, delete, by id) and starts a commit at once,
like a templates save. The check routes take text that can be a secret being typed: it goes
through the engine into the answer and nowhere else, never stored, logged or echoed in an error.
"""

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from bwtidy.api.app_state import app_state
from bwtidy.api.deps import Session
from bwtidy.api.sections import body_changes, current_datatypes, queue_changes
from bwtidy.items.patterns import CompiledPattern, FormatResult, PatternError
from bwtidy.queue.view import DATATYPES_SUMMARY
from bwtidy.sections import SectionChange
from bwtidy.sections.datatypes import (
    MAX_EXAMPLES,
    AddDatatype,
    Datatype,
    RemoveDatatype,
    compile_datatype,
    used_datatypes,
)
from bwtidy.sections.matching import floored_hundredths, format_of
from bwtidy.sections.templates import InvalidTemplates, parse_templates
from bwtidy.session import SessionManager
from bwtidy.vault import stored_templates

router = APIRouter(prefix="/api/datatypes")


def _datatype_json(datatype: Datatype) -> dict[str, Any]:
    """The stored datatype with its pattern facts (None when it is broken) and what is broken."""
    compiled = datatype.compiled
    return {
        **datatype.dump(),
        "wholeValue": datatype.wholeValue,
        "states": compiled.states if compiled else None,
        "maxRepeat": compiled.max_repeat if compiled else None,
        "shortest": compiled.shortest if compiled else None,
        "broken": datatype.broken,
    }


def _result_json(result: FormatResult | None) -> dict[str, Any]:
    """A FormatResult for the UI, with the score floored like a term of the fill score."""
    if result is None:
        return {
            "distance": None,
            "score": 0,
            "firstEdit": None,
            "expected": None,
            "repeatsUsed": [],
            "steps": 0,
            "tooLong": True,
        }
    return {
        "distance": result.distance,
        "score": floored_hundredths(result.score) / 100,
        "firstEdit": result.first_edit,
        "expected": result.expected,
        "repeatsUsed": list(result.repeats_used),
        "steps": result.steps,
        "tooLong": False,
    }


async def _json_object(request: Request) -> dict[str, Any]:
    """The body, read by hand: FastAPI's own 422 for a body of another shape echoes its input."""
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=422, detail="The body is not JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="The body is not a JSON object")
    return body


def _text_list(body: dict[str, Any], key: str) -> list[str]:
    listed = body.get(key, [])
    if not isinstance(listed, list) or not all(isinstance(v, str) for v in listed):
        raise HTTPException(status_code=422, detail=f"{key} must be a list of text")
    if len(listed) > MAX_EXAMPLES:
        raise HTTPException(status_code=422, detail=f"{key} holds more than {MAX_EXAMPLES}")
    return listed


def _with_ids(changes: list[SectionChange], known: set[str]) -> list[SectionChange]:
    """The changes with an id made for each new datatype. An id the request gives a new one
    is a 422: bwtidy makes them."""
    taken = set(known)
    result: list[SectionChange] = []
    for index, change in enumerate(changes):
        if isinstance(change, AddDatatype):
            if "id" in change.datatype:
                detail = f"changes[{index}]: bwtidy makes the id of a new datatype, leave it out"
                raise HTTPException(status_code=422, detail=detail)
            new_id = secrets.token_hex(4)
            while new_id in taken:
                new_id = secrets.token_hex(4)
            taken.add(new_id)
            change = change.model_copy(update={"datatype": {**change.datatype, "id": new_id}})
        result.append(change)
    return result


def _refuse_deleting_used(
    request: Request, session: SessionManager, old: list[Datatype], changes: list[SectionChange]
) -> None:
    """A 422 for a deletion of a datatype the templates use, as the saves under way leave them.
    The write checks it again on the note as it is then."""
    saving, queued = app_state(request).queue.saving_section("templates")
    try:
        templates = (
            parse_templates(queued)
            if saving
            else stored_templates(session.cache, session.tool_note_id)
        )
    except InvalidTemplates:
        return
    removed = {c.id for c in changes if isinstance(c, RemoveDatatype)}
    used = used_datatypes(templates, removed, old)
    if used:
        datatype, names = used[0]
        raise HTTPException(
            status_code=422,
            detail=f"The datatype {datatype.name!r} is used by the templates {', '.join(names)}",
        )


@router.get("")
async def get_datatypes(request: Request, session: Session) -> dict[str, Any]:
    """The list with the pattern facts and the broken ones marked, "saving" while a save is under
    way, and "error" when the section cannot be read."""
    datatypes, saving, error = current_datatypes(request, session)
    answer: dict[str, Any] = {"datatypes": [_datatype_json(d) for d in datatypes], "saving": saving}
    if error:
        answer["error"] = error
    return answer


@router.put("", status_code=202)
async def put_datatypes(request: Request, body: dict[str, Any], session: Session) -> dict[str, Any]:
    """Queue the changes of the datatypes (add, set or delete one, by id) as one UI entry and
    start a commit at once. A new datatype comes without an id and gets one here. The datatypes
    the changes leave are checked again as a whole, so a broken datatype goes only by being
    fixed or deleted. Answers the datatypes as the saves under way leave them."""
    changes = body_changes(body, "datatypes")
    old, _, _ = current_datatypes(request, session)
    changes = _with_ids(changes, {d.id for d in old})
    _refuse_deleting_used(request, session, old, changes)
    entry_id = queue_changes(request, "datatypes", changes, DATATYPES_SUMMARY)
    datatypes, _, _ = current_datatypes(request, session)
    return {"entryId": entry_id, "datatypes": [_datatype_json(d) for d in datatypes]}


def _checked(compiled: CompiledPattern, body: dict[str, Any]) -> dict[str, Any]:
    examples = _text_list(body, "examples")
    counter_examples = _text_list(body, "counterExamples")
    samples = _text_list(body, "samples")
    return {
        "states": compiled.states,
        "maxRepeat": compiled.max_repeat,
        "shortest": compiled.shortest,
        "examples": [_result_json(format_of(compiled, v)) for v in examples],
        "counterExamples": [_result_json(format_of(compiled, v)) for v in counter_examples],
        "samples": [_result_json(format_of(compiled, v)) for v in samples],
    }


@router.post("/check")
async def check_pattern(request: Request, session: Session) -> dict[str, Any]:
    """For the editor's live preview: the pattern's facts and a result for every example,
    counter-example and sample (the Try box's lines), or "error" with the pattern's problem and its
    column. Nothing is stored or logged."""
    body = await _json_object(request)
    pattern, ignore_case = body.get("pattern"), body.get("ignoreCase", False)
    whole_value = body.get("wholeValue", True)
    if not isinstance(pattern, str):
        raise HTTPException(status_code=422, detail="pattern must be text")
    if not isinstance(ignore_case, bool):
        raise HTTPException(status_code=422, detail="ignoreCase must be true or false")
    if not isinstance(whole_value, bool):
        raise HTTPException(status_code=422, detail="wholeValue must be true or false")
    try:
        compiled = compile_datatype(pattern, ignore_case, whole_value)
    except PatternError as error:
        return {"error": {"message": error.message, "column": error.column}}
    return _checked(compiled, body)


@router.post("/{datatype_id}/check")
async def check_value(request: Request, datatype_id: str, session: Session) -> dict[str, Any]:
    """The result of one value against a stored datatype, for the grid's live editing. The value
    can be a secret being typed: never stored or logged."""
    body = await _json_object(request)
    value = body.get("value")
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="value must be text")
    datatypes, _, _ = current_datatypes(request, session)
    datatype = next((d for d in datatypes if d.id == datatype_id), None)
    if datatype is None:
        raise HTTPException(status_code=404, detail="No such datatype")
    if datatype.compiled is None:
        raise HTTPException(status_code=422, detail=f"The datatype {datatype.name!r} is broken")
    return _result_json(format_of(datatype.compiled, value))
