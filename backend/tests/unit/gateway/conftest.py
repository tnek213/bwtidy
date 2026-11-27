"""The fixtures of the gateway's tests: quick health polls, the fake resolver, a tmp_path whose
end removes the workers' sockets too and the gateway with fake workers."""

import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from bwtidy.gateway import accounts as accounts_module
from bwtidy.gateway import workers as workers_module
from tests.support.gateway_env import ADDRESSES, GatewayEnv, make_env
from tests.support.sockets import short_tmp_path


@pytest.fixture(autouse=True)
def quick_health_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake worker answers within milliseconds, so a start need not wait a whole poll."""
    monkeypatch.setattr(workers_module, "HEALTH_POLL", 0.01)


@pytest.fixture(autouse=True)
def fake_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(host: str) -> list[str]:
        if host not in ADDRESSES:
            raise socket.gaierror(f"{host} does not resolve")
        return ADDRESSES[host]

    monkeypatch.setattr(accounts_module, "resolve_addresses", resolve)


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    yield from short_tmp_path("gw")


@pytest.fixture
def env(tmp_path: Path) -> Iterator[GatewayEnv]:
    yield from make_env(tmp_path)
