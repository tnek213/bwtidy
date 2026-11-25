"""The app's state: the parts every route reaches, built once by create_app."""

from dataclasses import dataclass

from fastapi import Request

from bwtidy.backups import BackupKeeper
from bwtidy.bw import BwLogin, BwServeProcess
from bwtidy.config import Settings
from bwtidy.db import Database
from bwtidy.mcp import BwtidyMcp, McpToken
from bwtidy.queue.autocommit import AutoCommit
from bwtidy.queue.review import ReviewQueue
from bwtidy.queue.to_fill import ToFill
from bwtidy.session import SessionManager
from bwtidy.vault import Formats, VaultCache
from bwtidy.worker import WorkerLifetime
from bwtidy.writes import WritePath


@dataclass
class AppState:
    settings: Settings
    db: Database
    cache: VaultCache
    session: SessionManager
    bw_login: BwLogin
    ## None when the app does not start bw serve (tests)
    bw_serve: BwServeProcess | None
    writes: WritePath
    backups: BackupKeeper
    queue: ReviewQueue
    to_fill: ToFill
    auto_commit: AutoCommit
    mcp: BwtidyMcp
    worker: WorkerLifetime
    mcp_token: McpToken
    formats: Formats


def app_state(request: Request) -> AppState:
    return request.app.state.bw
