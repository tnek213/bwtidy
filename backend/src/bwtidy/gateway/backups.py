"""The backups of each account under a root of their own (docs/spec.md, Gateway, Backups root).

With BWTIDY_BACKUPS_ROOT set, an account's backups live in ROOT/HOST/EMAIL, so the root can
be any mount. The gateway makes that directory as root, owned by the account's uid, before the
worker starts. Every step goes through directory descriptors opened without following a
symlink: the account's uid owns its backups directory and could put a symlink anywhere in it.
"""

import contextlib
import errno
import logging
import os
import shutil
import stat
from collections.abc import Generator
from pathlib import Path

from bwtidy.protocol import backup_time

log = logging.getLogger(__name__)

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC


class BackupsError(Exception):
    """The account's backups directory cannot be used, so its worker does not start."""


@contextlib.contextmanager
def open_dir(
    name: str | Path, parent: int | None = None, what: str = "", follow: bool = False
) -> Generator[int]:
    """A directory's descriptor, BackupsError when it is a symlink or not a directory.

    follow lets the last part be a symlink: only for the root, which the deployment chose.
    """
    flags = DIR_FLAGS if follow else DIR_FLAGS | os.O_NOFOLLOW
    try:
        fd = os.open(name, flags, dir_fd=parent)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise BackupsError(f"{what or name} is a symlink or not a directory") from None
        raise
    try:
        yield fd
    finally:
        os.close(fd)


def make_dir(parent: int, name: str, mode: int) -> None:
    """Make the directory unless it exists, open_dir then decides whether it is usable."""
    with contextlib.suppress(FileExistsError):
        os.mkdir(name, mode, dir_fd=parent)


class BackupsRoot:
    """The directories ROOT/HOST/EMAIL, made, owned and deleted by the gateway."""

    def __init__(self, root: Path, *, switch_uid: bool) -> None:
        self.root = root
        self._switch_uid = switch_uid

    def prepare(self) -> None:
        """The root exists, and a worker can enter it to reach its own directory."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o711)

    def path(self, host_dir: str, email: str) -> Path:
        return self.root / host_dir / email

    def owners(self) -> list[int]:
        """The owner of every directory directly at ROOT/HOST/EMAIL, symlinks left out."""
        found = []
        if not self.root.is_dir():
            return found
        for host in self.root.iterdir():
            if host.is_symlink() or not host.is_dir():
                continue
            for path in host.iterdir():
                if not path.is_symlink() and path.is_dir():
                    found.append(os.lstat(path).st_uid)
        return found

    def make(self, host_dir: str, email: str, uid: int) -> None:
        """Make ROOT/HOST/EMAIL for the account's uid.

        The host directory gets mode 0711, the account's 0700 and the uid as its owner, also
        when it was there already (an account made anew gets a new uid, and the files of the
        old one are given to it). BackupsError when any part below the root is a symlink or
        not a directory.
        """
        label = f"{host_dir}/{email}"
        with open_dir(self.root, what="The backups root", follow=True) as root_fd:
            make_dir(root_fd, host_dir, 0o711)
            with open_dir(host_dir, root_fd, f"The backups host directory of {label}") as host_fd:
                os.fchmod(host_fd, 0o711)
                make_dir(host_fd, email, 0o700)
                with open_dir(email, host_fd, f"The backups directory of {label}") as fd:
                    os.fchmod(fd, 0o700)
                    if self._switch_uid:
                        os.fchown(fd, uid, uid)
                    self._own_files(fd, uid, label)

    def _own_files(self, fd: int, uid: int, label: str) -> None:
        """Give the uid the files another uid left (an account made anew gets a new uid)."""
        if not self._switch_uid:
            return
        changed = 0
        for name in os.listdir(fd):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode) and (info.st_uid, info.st_gid) != (uid, uid):
                os.chown(name, uid, uid, dir_fd=fd, follow_symlinks=False)
                changed += 1
        if changed:
            log.info("Gave %d files in the backups directory of %s to uid %d", changed, label, uid)

    def remove(self, host_dir: str, email: str, *, force: bool = False) -> None:
        """Delete ROOT/HOST/EMAIL of an account that never logged in, unless it holds backups.

        Never through a symlink. A directory with backups stays: they can be from before the
        account directory was deleted by hand, and the root is meant to outlive it. force
        deletes it even with backups in it, for an account its worker asked to delete
        (docs/spec.md, Deleting data).
        """
        host = self.root / host_dir
        path = host / email
        if host.is_symlink() or path.is_symlink() or not path.is_dir():
            return
        if not force:
            kept = sum(1 for name in os.listdir(path) if backup_time(name) is not None)
            if kept:
                label = f"{host_dir}/{email}"
                log.warning("The backups directory of %s keeps its %d backups", label, kept)
                return
        shutil.rmtree(path)
