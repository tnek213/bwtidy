"""What a worker does beyond the single-user app (docs/spec.md, Worker mode).

A worker serves one account for one session: it exits once that session ended, or when no
session started within the idle time after its start. The pending marker tells the gateway
that the account never got past its first unlock.
"""

import logging
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path

from bwtidy.bw import BwLogin
from bwtidy.session import SessionManager

log = logging.getLogger(__name__)


def stop_by_signal() -> None:
    """Fallback for an app not started by run(): uvicorn shuts down cleanly on SIGTERM."""
    os.kill(os.getpid(), signal.SIGTERM)


class WorkerLifetime:
    def __init__(
        self,
        session: SessionManager,
        bw_login: BwLogin,
        *,
        exit_after_session: bool = False,
        idle_exit: float = 600.0,
        pending_marker: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        request_exit: Callable[[], None] = stop_by_signal,
    ) -> None:
        self._session = session
        self._bw_login = bw_login
        self._exit_after_session = exit_after_session
        self._idle_exit = idle_exit
        self._pending_marker = pending_marker
        self._clock = clock
        self._started = clock()
        self._marker_handled = pending_marker is None
        self._exit_requested = False
        session.one_session = exit_after_session
        ## set by run() to a clean uvicorn shutdown
        self.request_exit = request_exit

    @property
    def exiting(self) -> bool:
        """The exit is decided: the session's end started, or the idle time passed."""
        return self._session.closed

    def unlocked(self) -> None:
        """Delete the pending marker after the first successful unlock."""
        if self._marker_handled or self._pending_marker is None:
            return
        try:
            self._pending_marker.unlink(missing_ok=True)
        except OSError as error:
            log.warning("Could not delete the pending marker: %s", error)
            return
        self._marker_handled = True
        log.info("First unlock done, pending marker deleted")

    def session_ended(self) -> None:
        if self._exit_after_session:
            self._exit("The session ended")

    def check_idle(self) -> None:
        """Exit when no session started within the idle time and no Bitwarden login runs."""
        if not self._exit_after_session or self.exiting:
            return
        if self._session.active or self._session.busy or self._bw_login.in_progress:
            return
        if self._clock() - self._started >= self._idle_exit:
            self._exit("No session started within the idle time")

    def _exit(self, reason: str) -> None:
        if self._exit_requested:
            return
        self._exit_requested = True
        self._session.closed = True
        log.info("%s, exit requested", reason)
        self.request_exit()
