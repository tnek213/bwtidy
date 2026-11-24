"""Generator routes: the default and per item settings of bw's password generator, kept in the
generator section of the tool note, and an example value for the dialogs (docs/spec.md,
Settings).

Every change queues one change of the section with queue_changes (set the default, set or
remove one item's setting) and starts a commit at once, like a templates save. The write
applies it to the section as the note holds it then, so two saves built on the same settings
both land, and drops the settings of items that are no longer in the vault. The example value
goes from bw serve into the answer and nowhere else.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from bwtidy.api.deps import Session, require_tool_note
from bwtidy.api.sections import current_generator, queue_changes
from bwtidy.bw import BwError
from bwtidy.queue.view import GENERATOR_SUMMARY
from bwtidy.sections import SectionChange
from bwtidy.sections.generator import (
    GeneratorSettings,
    InvalidGenerator,
    PassphraseOptions,
    PasswordOptions,
    RemoveItem,
    SetDefault,
    SetItem,
    bw_query,
    parse_options,
)
from bwtidy.session import SessionManager

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/generator")


def _answer(settings: GeneratorSettings, saving: bool, error: str = "") -> dict[str, Any]:
    answer = {**settings.dump(), "default": settings.default_options().model_dump()}
    answer["saving"] = saving
    if error:
        answer["error"] = error
    return answer


def _options(body: Any) -> PasswordOptions | PassphraseOptions:
    try:
        return parse_options(body)
    except InvalidGenerator as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


def _save(request: Request, session: SessionManager, change: SectionChange) -> dict:
    """Queue the change, start a commit. Answers the settings with it applied."""
    entry_id = queue_changes(request, "generator", [change], GENERATOR_SUMMARY)
    settings, _, _ = current_generator(request, session)
    return {**_answer(settings, True), "entryId": entry_id}


def _item_id(session: SessionManager, item_id: str) -> str:
    if item_id not in {i.get("id") for i in session.cache.items}:
        raise HTTPException(status_code=404, detail="No such item in the vault")
    if item_id == require_tool_note(session).get("id"):
        raise HTTPException(status_code=422, detail="The bwtidy note has no generator setting")
    return item_id


@router.get("")
async def get_generator(request: Request, session: Session) -> dict[str, Any]:
    """The section with the default filled in (today's 24 characters without one), "saving"
    while a save is under way, and "error" when the stored settings are not valid."""
    settings, saving, error = current_generator(request, session)
    return _answer(settings, saving, error)


@router.put("/default", status_code=202)
async def put_default(request: Request, body: dict[str, Any], session: Session) -> dict[str, Any]:
    options = _options(body)
    return _save(request, session, SetDefault(op="setDefault", options=options.model_dump()))


@router.put("/items/{item_id}", status_code=202)
async def put_item(
    request: Request, item_id: str, body: dict[str, Any], session: Session
) -> dict[str, Any]:
    options = _options(body)
    _item_id(session, item_id)
    change = SetItem(op="setItem", itemId=item_id, options=options.model_dump())
    return _save(request, session, change)


@router.delete("/items/{item_id}", status_code=202)
async def delete_item(request: Request, item_id: str, session: Session) -> dict[str, Any]:
    """The item back to the default. Nothing is queued when it has no setting of its own."""
    settings, saving, error = current_generator(request, session)
    if item_id not in settings.items:
        return {**_answer(settings, saving, error), "entryId": None}
    return _save(request, session, RemoveItem(op="removeItem", itemId=item_id))


@router.post("/example")
async def example(body: dict[str, Any], session: Session) -> dict[str, str]:
    """One value from bw's generator with these options, for the dialogs. Never stored or
    logged."""
    options = _options(body)
    try:
        value = await session.client.generate(bw_query(options))
    except BwError:
        raise HTTPException(status_code=502, detail="bw serve generated no example") from None
    return {"value": value}
