"""The HTTP answer of each domain error a worker route lets through (docs/architecture.md).

Routes raise the error, these handlers answer it. A route that needs another answer for one
error (sections.queue_changes answers the reason of an InvalidOperation) catches it itself.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from bwtidy.backups import BackupError
from bwtidy.bw import BwError
from bwtidy.operations import Conflict, InvalidOperation, MergeConflicts
from bwtidy.queue.intake import VaultLocked
from bwtidy.writes import WriteFailed

log = logging.getLogger(__name__)


def answer_domain_errors(app: FastAPI) -> None:
    """Register the handlers: 409 Conflict and MergeConflicts, 422 InvalidOperation, 401
    VaultLocked, 500 BackupError and WriteFailed, 502 BwError."""

    @app.exception_handler(Conflict)
    async def item_changed(_request: Request, error: Conflict) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": str(error), "conflicts": error.conflicts}
        )

    @app.exception_handler(MergeConflicts)
    async def merged_columns_disagree(_request: Request, error: MergeConflicts) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": str(error), "mergeConflicts": error.conflicts}
        )

    @app.exception_handler(InvalidOperation)
    async def operation_refused(_request: Request, error: InvalidOperation) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(VaultLocked)
    async def vault_locked(_request: Request, error: VaultLocked) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(error)})

    @app.exception_handler(BackupError)
    async def backup_failed(_request: Request, error: BackupError) -> JSONResponse:
        log.warning("%s", error)
        return JSONResponse(status_code=500, content={"detail": str(error)})

    @app.exception_handler(WriteFailed)
    async def write_failed_midway(_request: Request, error: WriteFailed) -> JSONResponse:
        log.warning("%s", error)
        return JSONResponse(
            status_code=500,
            content={
                "detail": str(error),
                "applied": error.applied,
                "failed": error.failed,
                "pending": error.pending,
                "backup": error.backup,
                "conflicts": error.conflicts,
            },
        )

    @app.exception_handler(BwError)
    async def vault_unreachable(_request: Request, error: BwError) -> JSONResponse:
        log.warning("Could not reach the vault: %s", error)
        return JSONResponse(status_code=502, content={"detail": str(error)})
