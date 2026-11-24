"""The fixtures of the unit tests: env, the app on the sample vault logged in with its backups
faked, and short_limits, the short limits of the session's end."""

from collections.abc import Iterator
from pathlib import Path

import pytest

import bwtidy.queue.autocommit as autocommit
from bwtidy import session as session_module
from tests.support.app_env import AppEnv, logged_in
from tests.support.login_env import BOUND, SHORT_LIMIT


@pytest.fixture
def env(tmp_path: Path) -> Iterator[AppEnv]:
    yield from logged_in(tmp_path)


@pytest.fixture
def short_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    ## raising=False: the test fails on a version without the limits, not at the patch
    monkeypatch.setattr(autocommit, "END_COMMIT_LIMIT", SHORT_LIMIT, raising=False)
    monkeypatch.setattr(autocommit, "CANCEL_WAIT", BOUND / 2, raising=False)
    monkeypatch.setattr(session_module, "LOCK_LIMIT", SHORT_LIMIT, raising=False)
