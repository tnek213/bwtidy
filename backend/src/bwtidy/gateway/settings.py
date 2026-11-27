"""The gateway's settings from environment variables, read once at startup."""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from bwtidy.config import (
    DEFAULT_IDLE_EXIT,
    MAX_PORT,
    Policy,
    env_count,
    env_flag,
    env_path,
    env_seconds,
    env_text,
    version_text,
)
from bwtidy.gateway.accounts import normalize_server
from bwtidy.protocol import ACCOUNTS_DIR, STATS_FILE

DEFAULT_ACCOUNTS_DIR = Path(ACCOUNTS_DIR)
## service uids below this are left to the host's own services, so no worker shares a uid with one
DEFAULT_UID_BASE = 70000
## how long the gateway waits for a started worker's /api/health before it kills it
DEFAULT_START_TIMEOUT = 60.0
## new accounts waiting for their first login, and workers running or starting (docs/spec.md,
## Gateway): anyone who reaches the gateway can create an account without credentials
DEFAULT_MAX_PENDING = 2
DEFAULT_MAX_WORKERS = 8
## the gateway's own variables that a worker gets as they are, when the gateway has them
## (BWTIDY_MCP_HOSTS so the public host name passes the worker's DNS rebinding check, the
## session timeouts, the backup retention and the failed entries' age limit so a deployment's
## policy reaches every worker, BWTIDY_BW_BIN so an operator can run a Bitwarden CLI of their own)
PASSED_ON = (
    "PATH",
    "NODE_EXTRA_CA_CERTS",
    "BWTIDY_IDLE_EXIT",
    "BWTIDY_MCP_HOSTS",
    "BWTIDY_BW_BIN",
    "BWTIDY_HEARTBEAT_TIMEOUT",
    "BWTIDY_SESSION_IDLE_TIMEOUT",
    "BWTIDY_BACKUP_KEEP_NEWEST",
    "BWTIDY_BACKUP_KEEP_DAYS",
    "BWTIDY_BACKUP_KEEP_ALWAYS",
    "BWTIDY_FAILED_KEEP_HOURS",
)


def default_worker_bin() -> Path:
    """The bwtidy entry point next to the gateway's own Python, the same venv."""
    return Path(sys.executable).parent / "bwtidy"


def passed_on_from_env() -> dict[str, str]:
    env = {name: os.environ[name] for name in PASSED_ON if os.environ.get(name)}
    env.setdefault("PATH", os.defpath)
    return env


@dataclass(frozen=True)
class GatewaySettings:
    host: str = "127.0.0.1"
    port: int = 8000
    accounts_dir: Path = DEFAULT_ACCOUNTS_DIR
    ## the backups in ROOT/HOST/EMAIL instead of each account's app/backups, None for the latter
    backups_root: Path | None = None
    uid_base: int = DEFAULT_UID_BASE
    ## normalized server URLs that skip the public address check (the dev stack's Vaultwarden)
    allowed_servers: frozenset[str] = frozenset()
    secure_cookies: bool = False
    static_dir: Path | None = None
    ## the documentation site served at /docs/ (the image's /app/site), None serves none
    docs_dir: Path | None = None
    version: str = field(default_factory=version_text)
    ## off only for unit tests, which cannot run as root
    switch_uid: bool = True
    worker_bin: Path = field(default_factory=default_worker_bin)
    start_timeout: float = DEFAULT_START_TIMEOUT
    max_pending: int = DEFAULT_MAX_PENDING
    max_workers: int = DEFAULT_MAX_WORKERS
    passed_on: dict[str, str] = field(default_factory=passed_on_from_env)
    ## the usage stats (docs/spec.md, Usage stats), off unless BWTIDY_STATS=1. stats_db None
    ## means stats.db in the parent of accounts_dir, see stats_path
    stats: bool = False
    stats_db: Path | None = None

    @property
    def stats_path(self) -> Path:
        return self.stats_db or self.accounts_dir.parent / STATS_FILE

    @classmethod
    def from_env(cls) -> "GatewaySettings":
        ## the workers read these, a bad value fails here at the start and not in each worker
        Policy.from_env()
        env_seconds("BWTIDY_IDLE_EXIT", DEFAULT_IDLE_EXIT)
        static = env_text("BWTIDY_STATIC_DIR")
        allowed = env_text("BWTIDY_ALLOWED_SERVERS") or ""
        return cls(
            host=os.environ.get("BWTIDY_HOST", "127.0.0.1"),
            port=env_count("BWTIDY_PORT", 8000, most=MAX_PORT),
            accounts_dir=Path(
                os.environ.get("BWTIDY_ACCOUNTS_DIR") or DEFAULT_ACCOUNTS_DIR
            ).absolute(),
            backups_root=env_path("BWTIDY_BACKUPS_ROOT"),
            ## every worker uid is above the base, so a base below 1 could hand root to a worker
            uid_base=env_count("BWTIDY_UID_BASE", DEFAULT_UID_BASE),
            allowed_servers=frozenset(
                normalize_server(server)[0] for server in allowed.split(",") if server.strip()
            ),
            secure_cookies=env_flag("BWTIDY_SECURE_COOKIES"),
            static_dir=Path(static) if static else None,
            docs_dir=env_path("BWTIDY_DOCS_DIR"),
            version=version_text(env_text("BWTIDY_COMMIT")),
            switch_uid=os.environ.get("BWTIDY_GATEWAY_SWITCH_UID", "1").strip() != "0",
            max_pending=env_count("BWTIDY_MAX_PENDING", DEFAULT_MAX_PENDING),
            max_workers=env_count("BWTIDY_MAX_WORKERS", DEFAULT_MAX_WORKERS),
            stats=env_flag("BWTIDY_STATS"),
            stats_db=env_path("BWTIDY_STATS_DB"),
        )
