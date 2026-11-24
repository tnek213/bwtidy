"""require_session, the dependency of every route behind the session: the cookie, the timeouts
and the requests that count as activity."""

from collections.abc import Iterator

import pytest

from bwtidy.protocol import BACKGROUND_HEADER
from tests.support.app_env import AppEnv, session_app


@pytest.fixture
def env() -> Iterator[AppEnv]:
    with session_app() as env:
        yield env


def test_expired_session_is_rejected_before_the_check_runs(env: AppEnv):
    env.login()
    env.clock.advance(121)
    assert env.client.get("/api/dummy").status_code == 401


def test_activity_resets_the_idle_clock(env: AppEnv):
    env.login()
    for step in range(7200 // 30 + 10):
        env.clock.advance(30)
        env.client.post("/api/session/heartbeat")
        if step % 100 == 0:
            assert env.client.get("/api/dummy").status_code == 200
        env.check_timeouts()
    assert env.session.active


def test_background_requests_are_not_activity(env: AppEnv):
    env.login()
    for _ in range(7200 // 30 - 1):
        env.clock.advance(30)
        env.client.post("/api/session/heartbeat")
        response = env.client.get("/api/dummy", headers={BACKGROUND_HEADER: "1"})
        assert response.status_code == 200
        env.check_timeouts()
    env.clock.advance(30)
    env.client.post("/api/session/heartbeat")
    env.check_timeouts()
    assert not env.session.active


def test_protected_route_needs_a_session(env: AppEnv):
    response = env.client.get("/api/dummy")
    assert response.status_code == 401
    assert response.json() == {"detail": "Not logged in"}
    env.login()
    assert env.client.get("/api/dummy").status_code == 200
