"""The two session tools: each answers first and ends the session right after the answer."""

import logging
from collections.abc import Awaitable, Callable

from bwtidy.bw import BwLogin
from bwtidy.session import SessionManager

log = logging.getLogger(__name__)


class SessionActions:
    """What the two session tools take: ending the session, logging the Bitwarden CLI out and the
    server's after_answer, which runs work once the answer went out."""

    def __init__(
        self,
        session: SessionManager,
        bw_login: BwLogin,
        after_answer: Callable[[Callable[[], Awaitable[None]]], None],
    ) -> None:
        self._session = session
        self._bw_login = bw_login
        self.after_answer = after_answer

    async def logout(self) -> None:
        await self._session.logout()

    async def log_out_cli(self) -> int:
        return await self._bw_login.log_out()


async def end_session(actions: SessionActions) -> str:
    actions.after_answer(actions.logout)
    return "The bwtidy session ends now."


async def _log_out(actions: SessionActions) -> None:
    await actions.logout()
    code = await actions.log_out_cli()
    if code != 0:
        log.warning("MCP: bw logout exited with code %d", code)


async def log_out_of_bitwarden(actions: SessionActions) -> str:
    actions.after_answer(lambda: _log_out(actions))
    return "The bwtidy session ends now, and the Bitwarden CLI logs out."
