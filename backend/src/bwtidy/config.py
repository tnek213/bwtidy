"""Settings from environment variables, read once at startup."""

import math
import os
from dataclasses import dataclass, field
from importlib.metadata import version as installed_version
from pathlib import Path

from bwtidy.version import semver

## how long a worker waits for its first session before it exits (BWTIDY_IDLE_EXIT)
DEFAULT_IDLE_EXIT = 600.0
## a session ends without a heartbeat for this long (BWTIDY_HEARTBEAT_TIMEOUT, seconds)
HEARTBEAT_TIMEOUT = 120.0
## the frontend beats every 30 s, a shorter timeout than this would end live sessions
MIN_HEARTBEAT_TIMEOUT = 60.0
## a session ends without any activity for this long (BWTIDY_SESSION_IDLE_TIMEOUT, seconds)
IDLE_TIMEOUT = 7200.0
## the backup retention (BWTIDY_BACKUP_KEEP_NEWEST, _KEEP_DAYS and _KEEP_ALWAYS)
KEEP_NEWEST = 40
KEEP_DAYS = 90
KEEP_ALWAYS = 5
## a failed queue entry is deleted this many hours after it failed (BWTIDY_FAILED_KEEP_HOURS)
FAILED_KEEP_HOURS = 30


PREFIX = "BWTIDY_"


class SettingsError(ValueError):
    """The environment holds a setting that cannot be used."""


def version_text(commit: str | None = None) -> str:
    """The version `/api/health` answers: the package version, then the build commit.

    The package version is the one in backend/pyproject.toml, read from the installed metadata
    and spelled the semver.org way. Without a commit it is the version alone, with one
    `0.1.0-alpha.1 (261111 7db8830)`.
    """
    version = semver(installed_version("bwtidy"))
    return f"{version} ({commit})" if commit else version


@dataclass(frozen=True)
class Retention:
    """Which backups stay (docs/spec.md, Backups, Retention)."""

    keep_newest: int = KEEP_NEWEST
    keep_days: int = KEEP_DAYS
    keep_always: int = KEEP_ALWAYS


DEFAULT_RETENTION = Retention()


@dataclass(frozen=True)
class Policy:
    """The timeouts, the backup retention and the failed entries' age limit a deployment may set."""

    heartbeat_timeout: float = HEARTBEAT_TIMEOUT
    idle_timeout: float = IDLE_TIMEOUT
    retention: Retention = DEFAULT_RETENTION
    failed_keep_hours: int = FAILED_KEEP_HOURS

    @classmethod
    def from_env(cls) -> "Policy":
        """Read the six variables, SettingsError naming the one that cannot be used.

        The gateway calls this too, so a bad value stops it at the start and not each worker.
        """
        return cls(
            heartbeat_timeout=env_seconds(
                "BWTIDY_HEARTBEAT_TIMEOUT", HEARTBEAT_TIMEOUT, MIN_HEARTBEAT_TIMEOUT
            ),
            idle_timeout=env_seconds("BWTIDY_SESSION_IDLE_TIMEOUT", IDLE_TIMEOUT),
            retention=Retention(
                keep_newest=env_count("BWTIDY_BACKUP_KEEP_NEWEST", KEEP_NEWEST),
                keep_days=env_count("BWTIDY_BACKUP_KEEP_DAYS", KEEP_DAYS),
                keep_always=env_count("BWTIDY_BACKUP_KEEP_ALWAYS", KEEP_ALWAYS),
            ),
            failed_keep_hours=env_count("BWTIDY_FAILED_KEEP_HOURS", FAILED_KEEP_HOURS),
        )


DEFAULT_POLICY = Policy()


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    static_dir: Path | None
    bw_bin: str
    bw_serve_port: int
    ## where the backups are: BWTIDY_BACKUPS_DIR, else the directory backups in data_dir
    backup_dir: Path
    ## what /api/health answers: the package version and the commit the image was built from
    version: str = field(default_factory=version_text)
    ## worker mode (docs/spec.md, Worker mode): every one of these is off unless set
    socket: Path | None = None
    bw_socket: Path | None = None
    account_server: str | None = None
    account_email: str | None = None
    exit_after_session: bool = False
    idle_exit: float = DEFAULT_IDLE_EXIT
    pending_marker: Path | None = None
    secure_cookies: bool = False
    ## host names besides the loopback ones that /mcp answers, from BWTIDY_MCP_HOSTS
    mcp_hosts: tuple[str, ...] = ()
    policy: Policy = DEFAULT_POLICY
    ## set by a gateway with the usage stats on: report the names and the errors to it
    stats_report: bool = False

    @property
    def account(self) -> tuple[str, str] | None:
        """The fixed server and email of a worker, None when any account may log in."""
        if self.account_server is None or self.account_email is None:
            return None
        return self.account_server, self.account_email

    @classmethod
    def from_env(cls) -> "Settings":
        static = os.environ.get("BWTIDY_STATIC_DIR")
        server = env_text("BWTIDY_ACCOUNT_SERVER")
        email = env_text("BWTIDY_ACCOUNT_EMAIL")
        if (server is None) != (email is None):
            raise SettingsError(
                "BWTIDY_ACCOUNT_SERVER and BWTIDY_ACCOUNT_EMAIL are set together or not at all"
            )
        data_dir = Path(os.environ.get("BWTIDY_DATA_DIR", "/data/app"))
        return cls(
            host=os.environ.get("BWTIDY_HOST", "127.0.0.1"),
            port=int(os.environ.get("BWTIDY_PORT", "8000")),
            data_dir=data_dir,
            static_dir=Path(static) if static else None,
            bw_bin=os.environ.get("BWTIDY_BW_BIN", "bw"),
            bw_serve_port=int(os.environ.get("BWTIDY_BW_SERVE_PORT", "8087")),
            backup_dir=env_path("BWTIDY_BACKUPS_DIR") or data_dir / "backups",
            version=version_text(env_text("BWTIDY_COMMIT")),
            socket=env_path("BWTIDY_SOCKET"),
            bw_socket=env_path("BWTIDY_BW_SOCKET"),
            account_server=server,
            account_email=email,
            exit_after_session=env_flag("BWTIDY_EXIT_AFTER_SESSION"),
            idle_exit=float(os.environ.get("BWTIDY_IDLE_EXIT") or DEFAULT_IDLE_EXIT),
            pending_marker=env_path("BWTIDY_PENDING_MARKER"),
            secure_cookies=env_flag("BWTIDY_SECURE_COOKIES"),
            mcp_hosts=host_names(os.environ.get("BWTIDY_MCP_HOSTS", "")),
            policy=Policy.from_env(),
            stats_report=env_flag("BWTIDY_STATS_REPORT"),
        )


def env_text(name: str) -> str | None:
    """The variable trimmed, None when it is unset or blank."""
    value = os.environ.get(name, "").strip()
    return value or None


def env_path(name: str) -> Path | None:
    """The variable as an absolute path, None when it is unset or blank."""
    value = env_text(name)
    return Path(value).absolute() if value else None


def env_flag(name: str) -> bool:
    """True only when the variable is 1."""
    return os.environ.get(name, "").strip() == "1"


def env_seconds(name: str, default: float, least: float = 0.0) -> float:
    """The variable as a positive number of seconds (not below least), else the default."""
    value = env_text(name)
    if value is None:
        return default
    try:
        seconds = float(value)
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds <= 0:
        raise SettingsError(f"{name} must be a positive number of seconds")
    if seconds < least:
        raise SettingsError(f"{name} must be at least {least:g} seconds")
    return seconds


def env_count(name: str, default: int) -> int:
    """The variable as a positive whole number, else the default."""
    value = env_text(name)
    if value is None:
        return default
    try:
        count = int(value)
    except ValueError:
        count = 0
    if count <= 0:
        raise SettingsError(f"{name} must be a positive whole number")
    return count


def host_names(value: str) -> tuple[str, ...]:
    """The host names of a comma separated list, blanks dropped."""
    return tuple(name for part in value.split(",") if (name := part.strip()))
