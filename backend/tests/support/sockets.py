"""Short directories for Unix sockets, outside TMPDIR: TMPDIR can be deep, and a Unix socket
path holds at most 107 bytes."""

import hashlib
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

ROOT = Path("/tmp")


def socket_dir() -> Iterator[Path]:
    """A new short directory for a test's sockets, removed at the end (a fixture's body)."""
    path = Path(tempfile.mkdtemp(prefix="bwt", dir=ROOT))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def run_root(tmp_path: Path) -> Path:
    """The gateway's root of the socket directories for the test whose tmp_path this is.

    Named after tmp_path, so a test's settings find it and the test's end removes it
    (short_tmp_path).
    """
    digest = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    return ROOT / f"bwtidy-run-{digest}"


def short_tmp_path(prefix: str) -> Iterator[Path]:
    """A tmp_path of the gateway's tests, removed at the end along with its run_root."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        shutil.rmtree(run_root(path), ignore_errors=True)
