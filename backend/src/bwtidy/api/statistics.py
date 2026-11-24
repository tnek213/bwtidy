"""The numbers behind the Statistics page (docs/spec.md, Statistics)."""

from typing import Any

from fastapi import APIRouter, Request

from bwtidy.api.app_state import app_state
from bwtidy.api.deps import Session

router = APIRouter(prefix="/api/statistics")


@router.get("")
async def get_statistics(request: Request, session: Session) -> dict[str, Any]:
    return app_state(request).formats.statistics.answer()
