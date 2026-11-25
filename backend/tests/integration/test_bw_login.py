"""The Bitwarden CLI login from the web UI against the real bw and Vaultwarden (app fixture).

The module logs the CLI out through the app, logs it in again through the console routes and
finishes with an unlock. Whatever happens, the CLI is logged in again at the end, so the rest of
the suite still runs.
"""

import logging
import re
import time
from collections.abc import Iterator
from typing import Any

import pytest
from support.app import App
from support.cli import EMAIL, PASSWORD, SERVER, BwCli

from bwtidy.bw.login import MASK
from bwtidy.protocol import LOGIN_HEADER, REQUEST_HEADER
from tests.support.quiet import assert_absent, assert_present, last_prompt

pytestmark = pytest.mark.integration

OUTPUT_TIMEOUT = 120.0
## a session key as bw prints it is 88 characters of base64, nothing else in the console is
LONG_BASE64 = re.compile(r"[A-Za-z0-9+/=]{40,}")
## the prompt bw falls back to when the password variable is empty
PASSWORD_PROMPT = "Master password"
OTP_PROMPT = "Enter OTP sent to login email"
PROMPTS = (PASSWORD_PROMPT, OTP_PROMPT)


@pytest.fixture(scope="module")
def web(app: App, bw_cli: BwCli) -> Iterator[App]:
    try:
        yield app
    finally:
        bw_cli.ensure_logged_in()


class Login:
    """One login through the console routes, polled the way the login page polls them."""

    def __init__(self, app: App, token: str) -> None:
        self.app = app
        self.token = token
        self.text = ""
        self.next = 0
        self.running = True
        self.exit_code: int | None = None

    def headers(self) -> dict[str, str]:
        return {LOGIN_HEADER: self.token}

    def poll(self) -> None:
        response = self.app.client.get(
            f"/api/bw-login/output?after={self.next}", headers=self.headers()
        )
        assert response.status_code == 200, response.text
        out = response.json()
        self.text += out["text"]
        self.next = out["next"]
        self.running = out["running"]
        self.exit_code = out["exitCode"]

    def until(self, done: Any) -> None:
        """Poll until done(self) or the CLI exits, and fail after OUTPUT_TIMEOUT."""
        deadline = time.monotonic() + OUTPUT_TIMEOUT
        while True:
            self.poll()
            if done(self) or not self.running:
                return
            assert time.monotonic() < deadline, (
                f"bw login hangs after {OUTPUT_TIMEOUT:.0f} s, "
                f"last prompt: {last_prompt(self.text, PROMPTS)}"
            )
            time.sleep(0.5)

    def send(self, body: dict[str, Any]) -> None:
        response = self.app.client.post("/api/bw-login/input", json=body, headers=self.headers())
        assert response.status_code == 204, response.text


def start(app: App, password: str) -> Login:
    response = app.client.post(
        "/api/bw-login", json={"server": SERVER, "email": EMAIL, "password": password}
    )
    assert response.status_code == 200, response.text
    return Login(app, response.json()["loginId"])


def asks(login: Login) -> bool:
    """Whether the last console line is a question the CLI waits for."""
    last = login.text.rsplit("\n", 1)[-1]
    return last.startswith("? ")


def test_bw_logout_logs_the_cli_out(web: App, bw_cli: BwCli):
    assert web.login().status_code == 200
    assert web.client.post("/api/bw-logout", headers={REQUEST_HEADER: "1"}).status_code == 204
    assert web.state() == "locked"
    assert web.session.session_key is None
    assert bw_cli.json("status")["status"] == "unauthenticated"
    state = web.client.get("/api/session").json()
    assert state["bwLogin"]["loggedIn"] is False
    assert state["bwLogin"]["server"] == SERVER
    assert state["bwLogin"]["email"] == ""
    ## the unlock form would answer "Wrong master password" now, the page shows the bw login
    assert web.login().status_code != 200


def test_an_answer_typed_into_the_console_reaches_the_cli(web: App, bw_cli: BwCli):
    """With an empty password bw asks for it, the console route answers (wrong on purpose)."""
    login = start(web, "")
    login.until(asks)
    assert login.running, f"bw login did not ask, last prompt: {last_prompt(login.text, PROMPTS)}"
    assert_present(PASSWORD_PROMPT, login.text, "the console")
    login.send({"text": "not-the-master-password"})
    login.until(lambda _: False)
    assert login.exit_code == 1
    assert_present("Username or password is incorrect", login.text, "the console")
    ## the typed answer is not echoed, the prompt hides it
    assert_absent("not-the-master-password", login.text, "the console")
    ## a failed login is forgotten once its exit was reported
    gone = web.client.get("/api/bw-login/output", headers=login.headers())
    assert gone.status_code == 404
    assert bw_cli.json("status")["status"] == "unauthenticated"


def test_web_login_unlocks_and_loads_the_grid(
    web: App, bw_cli: BwCli, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    login = start(web, PASSWORD)
    answered: set[str] = set()
    while True:
        login.until(asks)
        if not login.running:
            break
        question = login.text.rsplit("\n", 1)[-1]
        ## nothing but the password can be answered unattended, anything else fails the test
        assert_present(PASSWORD_PROMPT, question, "the last console line")
        assert question not in answered, "bw login asks the same question again"
        answered.add(question)
        login.send({"password": True})
    assert login.exit_code == 0, f"bw login failed with exit code {login.exit_code}"
    assert_present("You are logged in!", login.text, "the console")
    masks = login.text.count(MASK)
    assert masks >= 3, "the console shows fewer than three masks"
    assert_absent(PASSWORD, login.text, "the console")
    ## a bool first, a failed assert on the match would print the key
    key_shown = LONG_BASE64.search(login.text) is not None
    assert not key_shown, "the session key is in the console"
    assert bw_cli.json("status")["status"] != "unauthenticated"

    response = web.client.post("/api/bw-login/finish", headers=login.headers())
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "unlocked"
    assert web.session.session_key
    gone = web.client.get("/api/bw-login/output", headers=login.headers())
    assert gone.status_code == 404

    state = web.client.get("/api/session").json()
    assert state["state"] == "unlocked"
    assert state["bwLogin"] == {"loggedIn": True, "server": SERVER, "email": EMAIL}
    grid = web.client.get("/api/grid")
    assert grid.status_code == 200
    names = {row["cells"].get("name") for row in grid.json()["rows"]}
    assert {"ThinkPad X1", "Youth laptop"} <= names
    assert_absent(PASSWORD, caplog.text, "the log")
    assert_absent("You are logged in", caplog.text, "the log")
    assert_absent(MASK, caplog.text, "the log")
