"""Whether the account's CLI is logged in, in a file the gateway reads (docs/spec.md, Gateway).

A worker writes the file in its data directory whenever the CLI's login state changes, so the
gateway can show the unlock or the Bitwarden login before any worker runs. The file holds one
line, `logged in` or `logged out`, and nothing else.
"""

import logging
import os
import tempfile
from pathlib import Path

from bwtidy.protocol import LOGIN_STATE_FILE

log = logging.getLogger(__name__)

LOGGED_IN = b"logged in\n"
LOGGED_OUT = b"logged out\n"
## anything longer is no state
MAX_LOGIN_STATE_FILE = 64


def parse_login_state(content: bytes) -> bool | None:
    """True for logged in, False for logged out, None for anything else."""
    if content == LOGGED_IN:
        return True
    if content == LOGGED_OUT:
        return False
    return None


class LoginStateFile:
    """The worker's side: writes the state when it differs from the one it wrote last."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / LOGIN_STATE_FILE
        self._written: bool | None = None

    def record(self, logged_in: bool) -> None:
        if self._written is logged_in:
            return
        try:
            ## mkstemp creates the file with mode 0600, and os.replace swaps it in atomically
            fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{LOGIN_STATE_FILE}.")
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(LOGGED_IN if logged_in else LOGGED_OUT)
                os.replace(temp, self.path)
            except BaseException:
                Path(temp).unlink(missing_ok=True)
                raise
        except OSError as error:
            log.warning("Could not write the CLI's login state: %s", error.strerror or error)
            return
        self._written = logged_in
        log.info("Noted for the gateway: the CLI is %s", "logged in" if logged_in else "logged out")
