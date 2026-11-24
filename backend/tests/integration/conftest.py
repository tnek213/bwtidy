import os
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from support.app import App, purge_tool_notes
from support.cli import SERVER, TEST_AUTO_COMMIT, BwCli

from bwtidy.config import Settings
from bwtidy.main import create_app

Bw = Callable[..., Any]


@pytest.fixture(scope="session")
def bw_cli() -> BwCli:
    if os.environ.get("BWTIDY_DEV_STACK") != "1":
        pytest.skip("needs the dev vault stack, run scripts/integration")
    cli = BwCli()
    cli.ensure_logged_in()
    assert cli.json("status")["serverUrl"] == SERVER
    return cli


@pytest.fixture(scope="module")
def bw(bw_cli: BwCli) -> Iterator[Bw]:
    """bw unlocked for one test module, returning parsed JSON."""
    key = bw_cli.unlock()
    bw_cli.run("sync", session=key)
    yield lambda *args: bw_cli.json(*args, session=key)
    bw_cli.run("lock")


@pytest.fixture(scope="module")
def app(bw_cli: BwCli, tmp_path_factory: pytest.TempPathFactory) -> Iterator[App]:
    """The app with the real BwServeProcess, so bw serve is a child of pytest.

    The tool note is deleted for good before and after, so test_seed.py still sees the
    seeded vault.
    """
    purge_tool_notes(bw_cli)
    data_dir = tmp_path_factory.mktemp("data")
    settings = replace(
        Settings.from_env(),
        data_dir=data_dir,
        backup_dir=data_dir / "backups",
        static_dir=None,
    )
    fastapi_app = create_app(settings)
    try:
        with TestClient(fastapi_app) as client:
            state = fastapi_app.state.bw
            state.auto_commit.configure(*TEST_AUTO_COMMIT)
            yield App(client, state.session, state.writes, state.backups, data_dir)
    finally:
        purge_tool_notes(bw_cli)
