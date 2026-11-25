"""Display settings (docs/spec.md, Settings): how the UI shows dates and times, whether the
Vault grid shows bwtidy's own note, and the N of its Rare fields column (docs/spec.md, Grid).

Kept in the settings table, so the choices follow the account to every browser.
GET and PUT /api/settings/display read and set them.
"""

from typing import Annotated, Literal, get_args

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, model_validator

from bwtidy.api.app_state import app_state
from bwtidy.api.deps import Session
from bwtidy.db import DATE_FORMAT, HIDE_TOOL_NOTE, RARE_FIELDS_MAX

router = APIRouter(prefix="/api/settings")

DateFormat = Literal["system", "iso", "europe", "europe-dots", "usa"]
DATE_FORMATS: tuple[str, ...] = get_args(DateFormat)
DEFAULT_DATE_FORMAT: DateFormat = "system"
RARE_FIELDS_LIMIT = 99

## strict, so true or "2" is no N
RareFieldsMax = Annotated[int, Field(strict=True, ge=0, le=RARE_FIELDS_LIMIT)]


class Display(BaseModel):
    """What a PUT holds: any of the fields, and only what it holds is saved."""

    dateFormat: DateFormat | None = None
    hideToolNote: bool | None = None
    rareFieldsMax: RareFieldsMax | None = None

    @model_validator(mode="after")
    def _something_to_save(self) -> "Display":
        if not self.model_fields_set:
            raise ValueError("send dateFormat, hideToolNote, rareFieldsMax or several")
        ## a field that is sent must hold a value, null is not one
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} must not be null")
        return self


def _rare_fields_max(stored: str | None) -> int:
    """The stored N, 0 when it is missing or no number from 0 to RARE_FIELDS_LIMIT."""
    if stored is None or not stored.isascii() or not stored.isdigit():
        return 0
    value = int(stored)
    return value if value <= RARE_FIELDS_LIMIT else 0


def _display(request: Request) -> dict[str, str | bool | int]:
    db = app_state(request).db
    stored = db.get_setting(DATE_FORMAT)
    ## a value this version does not know reads as the default
    return {
        "dateFormat": stored if stored in DATE_FORMATS else DEFAULT_DATE_FORMAT,
        "hideToolNote": db.get_setting(HIDE_TOOL_NOTE) == "1",
        "rareFieldsMax": _rare_fields_max(db.get_setting(RARE_FIELDS_MAX)),
    }


@router.get("/display")
async def get_display(request: Request, session: Session) -> dict[str, str | bool | int]:
    return _display(request)


@router.put("/display")
async def set_display(
    request: Request, body: Display, session: Session
) -> dict[str, str | bool | int]:
    """Save what the body holds and keep the rest. An unknown format, an N outside 0 to 99 or an
    empty body is a 422."""
    db = app_state(request).db
    if body.dateFormat is not None:
        db.set_setting(DATE_FORMAT, body.dateFormat)
    if body.hideToolNote is not None:
        db.set_setting(HIDE_TOOL_NOTE, "1" if body.hideToolNote else "0")
    if body.rareFieldsMax is not None:
        db.set_setting(RARE_FIELDS_MAX, str(body.rareFieldsMax))
    return _display(request)
