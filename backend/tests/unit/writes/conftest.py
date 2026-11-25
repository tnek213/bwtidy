"""The write path's fixture beside the env of every unit test: an AppEnv whose backups are real
zips of the fake vault, so a restore can read them."""

from collections.abc import Iterator
from pathlib import Path

import pytest

import bwtidy.backups.reader as reader
from tests.support.app_env import AppEnv, logged_in
from tests.support.writes_helpers import plain_decrypt


@pytest.fixture
def renv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppEnv]:
    """An AppEnv whose backups are real zips of the fake vault, so a restore can read them."""
    monkeypatch.setattr(reader, "decrypt_backup", plain_decrypt)
    yield from logged_in(tmp_path, zips=True)
