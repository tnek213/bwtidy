"""Account keys and account directories (docs/spec.md, Gateway).

An account is a server and an email. Its directory is ACCOUNTS_DIR/HOST/EMAIL, owned by the
account's own uid, and the directory tree is the account list. A directory is made under a
temporary name and renamed into place once its uid owns it, and one owned by root, by a uid at
or below the base or by another account's uid is refused.
"""

import asyncio
import hmac
import ipaddress
import logging
import os
import re
import shutil
import socket
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from bwtidy.gateway.backups import BackupsRoot
from bwtidy.login_state import MAX_LOGIN_STATE_FILE, parse_login_state
from bwtidy.mcp_token import TokenRecord, parse_token_file, token_digest
from bwtidy.protocol import (
    DB_FILE,
    DELETE_MARKER,
    LOGIN_STATE_FILE,
    PENDING_MARKER,
    SUBDIRS,
    TOKEN_FILE,
    WORKER_LOG,
)

log = logging.getLogger(__name__)

## a directory being made is .new-EMAIL beside the final name, so a crash leaves no account
TEMPORARY_PREFIX = ".new-"
MAX_EMAIL = 254
EMAIL_RE = re.compile(r"^[a-z0-9_%+-][a-z0-9._%+-]*@[a-z0-9.-]+[.][a-z]{2,}$")
## a host name as it can be a directory name: labels of letters, digits and hyphens
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?([.][a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
MAX_HOST = 253
## a token file holds 64 hex digits, an expiry and a creation time, anything longer is none
MAX_TOKEN_FILE = 256
## the delete marker is an empty file: anything longer is not the worker's own marker
MAX_DELETE_MARKER = 64
## ipaddress calls some of these global, but they lead to an IPv4 address, private ones too: NAT64,
## IPv4 compatible (::/96), IPv4 translated (::ffff:0:0:0/96), 6to4 and Teredo
REFUSED_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "::/96",
        "::ffff:0:0:0/96",
        "2002::/16",
        "2001::/32",
    )
)


class AccountError(ValueError):
    """The server or the email cannot name an account (422 for the browser)."""


class LimitError(Exception):
    """One of the gateway's caps is reached (429 for the browser)."""


@dataclass(frozen=True)
class AccountKey:
    ## https://host, with :PORT for a port other than 443, lowercased, no path
    server: str
    email: str
    ## the host, plus _PORT for a port other than 443
    host_dir: str

    @property
    def host(self) -> str:
        ## a host name has no underscore, so the first part is the host without the port
        return self.host_dir.split("_")[0]

    @property
    def label(self) -> str:
        return f"{self.host_dir}/{self.email}"

    def path(self, accounts_dir: Path) -> Path:
        return accounts_dir / self.host_dir / self.email


def normalize_server(server: str) -> tuple[str, str]:
    """The canonical server URL and the directory name of its host."""
    parts = urlsplit(server.strip())
    if parts.scheme.lower() != "https":
        raise AccountError("The server must be an https URL.")
    if (
        parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise AccountError("The server is the https URL of the host only, without a path.")
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        raise AccountError("The server's port is not valid.") from None
    if not HOST_RE.match(host) or len(host) > MAX_HOST:
        raise AccountError("The server's host name is not valid.")
    if port is None or port == 443:
        return f"https://{host}", host
    return f"https://{host}:{port}", f"{host}_{port}"


def normalize_email(email: str) -> str:
    """A leading dot is refused: a directory name with a dot in front is a temporary one."""
    email = email.strip().lower()
    if len(email) > MAX_EMAIL or not EMAIL_RE.match(email) or ".." in email:
        raise AccountError("The email is not valid.")
    return email


async def resolve_addresses(host: str) -> list[str]:
    """Every address the host resolves to, through the loop's resolver."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    ## a link-local IPv6 address carries its scope after a percent sign
    return [str(info[4][0]).split("%")[0] for info in infos]


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast and not any(ip in n for n in REFUSED_NETWORKS)


async def check_public(server: str, host: str) -> None:
    """Refuse a server that resolves to a non-public address, or not at all."""
    try:
        addresses = await resolve_addresses(host)
    except socket.gaierror:
        addresses = []
    if not addresses or not all(is_public(a) for a in addresses):
        raise AccountError(f"{server} does not resolve to a public address.")


async def check_server(key: AccountKey, allowed_servers: frozenset[str]) -> None:
    """The address check of the account's server, unless the server is allowed."""
    if key.server not in allowed_servers:
        await check_public(key.server, key.host)


async def account_key(server: str, email: str, allowed_servers: frozenset[str]) -> AccountKey:
    """Validate the server and the email, the server's addresses too unless it is allowed."""
    normalized, host_dir = normalize_server(server)
    key = AccountKey(server=normalized, email=normalize_email(email), host_dir=host_dir)
    await check_server(key, allowed_servers)
    return key


def directory_owner(path: Path) -> int:
    return os.lstat(path).st_uid


def account_file(path: Path, name: str, limit: int) -> bytes | None:
    """A file of the account directory's app/, None when there is none to trust.

    Read as root: the file must be a regular file owned by the account's uid, reached without a
    symlink, so a worker cannot point the gateway at another account's file or any other file.
    A file longer than limit counts as none.
    """
    app = path / "app"
    try:
        owner = directory_owner(path)
        if not stat.S_ISDIR(os.lstat(app).st_mode):
            return None
        fd = os.open(app / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != owner:
            return None
        content = os.read(fd, limit + 1)
        return content if len(content) <= limit else None
    except OSError:
        return None
    finally:
        os.close(fd)


def stored_token(path: Path) -> TokenRecord | None:
    """The MCP token record in an account directory, None when there is none to trust."""
    content = account_file(path, TOKEN_FILE, MAX_TOKEN_FILE)
    return None if content is None else parse_token_file(content)


def stored_login_state(path: Path) -> bool | None:
    """Whether the account's CLI is logged in, as its worker noted it, None when unknown."""
    content = account_file(path, LOGIN_STATE_FILE, MAX_LOGIN_STATE_FILE)
    return None if content is None else parse_login_state(content)


class Accounts:
    """The account directories under one root, made and deleted by the gateway as root."""

    def __init__(
        self,
        root: Path,
        *,
        uid_base: int,
        switch_uid: bool,
        backups_root: Path | None = None,
    ) -> None:
        self.root = root
        self._uid_base = uid_base
        self._switch_uid = switch_uid
        ## the backups' own root (BWTIDY_BACKUPS_ROOT), None keeps them in app/backups
        self.backups = (
            BackupsRoot(backups_root, switch_uid=switch_uid) if backups_root is not None else None
        )

    def prepare(self) -> None:
        """The roots exist, and a worker can enter them to reach its own directories."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o711)
        if self.backups is not None:
            self.backups.prepare()

    def make_backups(self, key: AccountKey, uid: int) -> None:
        """The account's directory under the backups root.

        Nothing without a backups root. BackupsError or OSError when it cannot be used.
        """
        if self.backups is not None:
            self.backups.make(key.host_dir, key.email, uid)

    def directory(self, key: AccountKey) -> Path:
        """The account's directory, refused when it leads outside the root or over a symlink."""
        path = key.path(self.root)
        if path.parent.is_symlink() or path.is_symlink():
            raise AccountError("The account directory is not usable.")
        resolved = path.resolve()
        if resolved != self.root.resolve() / key.host_dir / key.email:
            raise AccountError("The account directory is not usable.")
        return path

    def is_pending(self, path: Path) -> bool:
        return (path / PENDING_MARKER).exists()

    def delete_requested(self, path: Path) -> bool:
        """Whether the account's worker left the delete marker in app/ for the gateway to trust.

        The rules of the MCP token file: a regular file owned by the account's uid, reached
        without a symlink, and small, so a worker cannot ask the gateway to delete anything on
        anything but its own marker (docs/spec.md, Gateway).
        """
        return account_file(path, DELETE_MARKER, MAX_DELETE_MARKER) is not None

    def logged_in(self, path: Path) -> bool:
        """Whether the account's CLI is logged in, from the file its worker writes.

        Without a file to trust it counts as logged out.
        """
        return stored_login_state(path) is True

    def has_logged_in(self, path: Path) -> bool:
        """Whether the account got past its first login: its SQLite file is there and .pending
        is gone.

        A worker makes the SQLite file at its start, before its login succeeds, so the
        directory of a login that failed holds one too until it is deleted. Only its existence
        counts, the gateway never opens it.
        """
        if self.is_pending(path):
            return False
        app = path / "app"
        try:
            return stat.S_ISDIR(os.lstat(app).st_mode) and stat.S_ISREG(
                os.lstat(app / DB_FILE).st_mode
            )
        except OSError:
            return False

    def any_logged_in(self) -> bool:
        """Whether any account got past its first login (docs/spec.md, The account form)."""
        return any(self.has_logged_in(path) for path in self.account_directories())

    def pending_count(self) -> int:
        return sum(1 for path in self.account_directories() if self.is_pending(path))

    def uid_of(self, path: Path) -> int:
        """The account directory's owner, the uid its worker runs as.

        AccountError for an owner no worker may run as (see unusable_owner).
        """
        reason = self.unusable_owner(path)
        if reason is not None:
            log.warning("Refused the account directory %s: %s", path.relative_to(self.root), reason)
            raise AccountError("The account directory is not usable.")
        return directory_owner(path)

    def unusable_owner(self, path: Path) -> str | None:
        """Why no worker may run as the account directory's owner, None when one may.

        Refused: root or any uid at or below the base (a directory the gateway never finished
        or made by hand), and the uid of another account directory (a restored volume), as a
        worker with that uid could reach what is not the account's. Without switching uids
        every directory is the gateway's own, so nothing is refused.
        """
        if not self._switch_uid:
            return None
        owner = directory_owner(path)
        if owner <= self._uid_base:
            return f"owned by uid {owner}, at or below the uid base {self._uid_base}"
        for other in self.account_directories():
            if other != path and directory_owner(other) == owner:
                return f"owned by uid {owner} like {other.relative_to(self.root)}"
        return None

    def unusable_directories(self) -> list[Path]:
        """Every account directory uid_of refuses, each logged so an operator sees it."""
        found = []
        for path in self.account_directories():
            reason = self.unusable_owner(path)
            if reason is not None:
                log.warning(
                    "The account directory %s is not usable: %s",
                    path.relative_to(self.root),
                    reason,
                )
                found.append(path)
        return found

    def host_directories(self) -> list[Path]:
        """Every directory directly at root/HOST, symlinks left out."""
        if not self.root.is_dir():
            return []
        return [p for p in sorted(self.root.iterdir()) if not p.is_symlink() and p.is_dir()]

    def account_directories(self) -> list[Path]:
        """Every directory directly at root/HOST/EMAIL, symlinks and dotted names left out."""
        found = []
        for host_dir in self.host_directories():
            for path in sorted(host_dir.iterdir()):
                if not path.is_symlink() and path.is_dir() and not path.name.startswith("."):
                    found.append(path)
        return found

    def temporary_directories(self) -> list[Path]:
        """Every directory a create left under its temporary name, symlinks left out."""
        found = []
        for host_dir in self.host_directories():
            for path in sorted(host_dir.iterdir()):
                if (
                    not path.is_symlink()
                    and path.is_dir()
                    and path.name.startswith(TEMPORARY_PREFIX)
                ):
                    found.append(path)
        return found

    def with_mcp_token(self, token: str) -> tuple[Path, TokenRecord] | None:
        """The one account directory whose MCP token hash is the token's, with its record.

        None when no account or more than one holds the hash. The caller checks the expiry.
        """
        if not token:
            return None
        digest = token_digest(token)
        found = []
        for path in self.account_directories():
            record = stored_token(path)
            if record is not None and hmac.compare_digest(digest, record.digest):
                found.append((path, record))
        if len(found) > 1:
            log.warning("%d accounts have the same MCP token hash, none is used", len(found))
            return None
        return found[0] if found else None

    def next_uid(self) -> int:
        """One more than the highest owner of any account or backups directory, at least base+1."""
        highest = self._uid_base
        for path in self.account_directories():
            highest = max(highest, directory_owner(path))
        ## a backups directory can outlive its account directory, its uid is never handed on
        if self.backups is not None:
            highest = max([highest, *self.backups.owners()])
        return highest + 1

    def create(self, key: AccountKey) -> tuple[Path, int]:
        """A new account's directory with the pending marker, owned by a new uid.

        Built under its temporary name and renamed into place as the last step, once its uid
        owns everything: a crash before that leaves no directory an open would trust, only a
        leftover sweep_pending deletes.
        """
        path = self.directory(key)
        uid = self.next_uid()
        path.parent.mkdir(mode=0o711, exist_ok=True)
        path.parent.chmod(0o711)
        building = path.parent / (TEMPORARY_PREFIX + key.email)
        if building.is_symlink() or building.exists():
            self.delete_temporary(building)
        building.mkdir(mode=0o700)
        building.chmod(0o700)
        entries = [building]
        for name in SUBDIRS:
            sub = building / name
            sub.mkdir(mode=0o700)
            sub.chmod(0o700)
            entries.append(sub)
        for name in (PENDING_MARKER, WORKER_LOG):
            file = building / name
            file.touch(mode=0o600)
            file.chmod(0o600)
            entries.append(file)
        if self._switch_uid:
            for entry in entries:
                os.chown(entry, uid, uid)
        os.rename(building, path)
        log.info("Created the account directory for %s, uid %d", key.label, uid)
        return path, uid

    def delete_temporary(self, path: Path) -> None:
        """Delete what a create left under its temporary name, a symlink as the link alone."""
        if path.is_symlink():
            path.unlink()
        else:
            shutil.rmtree(path)
        log.info("Deleted the unfinished account directory %s", path.relative_to(self.root))

    def delete(self, path: Path, *, force: bool = False) -> None:
        """Delete an account directory, only one directly at root/HOST/EMAIL.

        Its directory under the backups root goes too, unless that holds backups. force deletes
        that directory even with backups in it, for an account its worker asked to delete
        (docs/spec.md, Deleting data): only the gateway uses it, on the worker's own marker.
        """
        if path.is_symlink() or path.parent.is_symlink() or path.parent.parent != self.root:
            raise AccountError("The account directory is not usable.")
        shutil.rmtree(path)
        if self.backups is not None:
            self.backups.remove(path.parent.name, path.name, force=force)

    def sweep_pending(self) -> list[Path]:
        """Delete every account directory whose login never succeeded.

        A leftover of a create that never got to its final name goes too.
        """
        deleted = []
        for path in self.temporary_directories():
            self.delete_temporary(path)
            deleted.append(path)
        for path in self.account_directories():
            if self.is_pending(path):
                self.delete(path)
                deleted.append(path)
                log.info("Deleted the pending account directory %s", path.relative_to(self.root))
        return deleted
