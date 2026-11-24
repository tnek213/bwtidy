"""Backup routes: list the encrypted backups, take one by hand, download or delete one."""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from bwtidy.api.app_state import app_state
from bwtidy.api.deps import Session
from bwtidy.backups import BackupInfo, BackupInUse, list_backups

router = APIRouter(prefix="/api/backups")


class Backup(BaseModel):
    name: str
    size: int
    time: datetime

    @classmethod
    def of(cls, info: BackupInfo) -> "Backup":
        return cls(name=info.name, size=info.size, time=info.time)


def backup_dir_of(request: Request) -> Path:
    return app_state(request).settings.backup_dir


@router.get("")
async def list_all(request: Request, session: Session) -> list[Backup]:
    return [Backup.of(b) for b in list_backups(backup_dir_of(request))]


@router.post("", status_code=201)
async def create(request: Request, session: Session) -> Backup:
    ## through the keeper, so it never runs beside a background backup
    return Backup.of(await app_state(request).backups.take_backup())


@router.get("/{name}")
async def download(request: Request, session: Session, name: str) -> FileResponse:
    """The encrypted file as a download. Only a name the listing knows, so no path works."""
    directory = backup_dir_of(request)
    if name not in {b.name for b in list_backups(directory)}:
        raise HTTPException(status_code=404, detail="No such backup")
    return FileResponse(directory / name, media_type="application/octet-stream", filename=name)


@router.delete("/{name}", status_code=204)
async def delete(request: Request, session: Session, name: str) -> None:
    """Delete one backup and its fingerprint. 409 for the newest backup (the next write may
    reuse it), for the one a running write selected and for one a queue entry still needs,
    404 for a name the directory lacks."""
    try:
        await app_state(request).backups.delete_backup(name)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such backup") from None
    except BackupInUse as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
