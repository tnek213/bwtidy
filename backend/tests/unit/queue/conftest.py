"""The env of the queue's tests: the fake bw serve with the extra items of normalize_data."""

from collections.abc import Iterator
from pathlib import Path

import pytest

import bwtidy.queue.autocommit as autocommit
from tests.support.app_env import AppEnv, logged_in
from tests.support.normalize_data import EXTRA
from tests.support.queue_helpers import quick_auto_commit


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppEnv]:
    ## the auto-commit task never gets a turn on its own here, the tests ask for each check
    monkeypatch.setattr(autocommit, "TICK", 3600.0)
    for env in logged_in(tmp_path, extra=EXTRA):
        quick_auto_commit(env)
        yield env
