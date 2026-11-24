"""The Bitwarden CLI login from the web UI, against a fake bw executable run as a child process.

The fake copies what bw 2026.10.0 printed in the dev stack (see bw/login.py): the prompt of the
new device verification, the success text with the session key, and the error lines.
"""

import asyncio
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

import bwtidy.bw.login as bwlogin
import bwtidy.queue.autocommit as autocommit
from bwtidy.bw.login import MASK, BwLogin, Console
from bwtidy.bw.serve import BwServeProcess
from bwtidy.login_state import LOGGED_IN, LOGGED_OUT
from bwtidy.protocol import LOGIN_STATE_FILE, REQUEST_HEADER
from bwtidy.protocol import SESSION_COOKIE as COOKIE
from tests.support.app_env import AppEnv
from tests.support.fake_bw import PASSWORD
from tests.support.login_env import (
    CODE,
    EMAIL,
    KEY,
    OTP_PROMPT,
    SERVER,
    SUCCESS,
    CliEnv,
    write_fake_bw,
)
from tests.support.quiet import assert_absent, assert_present

pytestmark = pytest.mark.bw_cli


def make_env(tmp_path: Path, **worker: Any) -> Iterator[CliEnv]:
    env = CliEnv(tmp_path, **worker)
    env.bw.logged_in = False
    with env:
        yield env


@pytest.fixture
def env(tmp_path: Path) -> Iterator[CliEnv]:
    yield from make_env(tmp_path)


@pytest.fixture
def fixed_env(tmp_path: Path) -> Iterator[CliEnv]:
    """A worker whose account is fixed (docs/spec.md, Worker mode)."""
    yield from make_env(tmp_path, account_server=SERVER, account_email=EMAIL)


def test_output_streams_in_order_with_offsets_and_reports_the_exit(env: CliEnv):
    assert env.start().status_code == 200
    assert len(env.token or "") >= 43
    out = env.wait_for_text(OTP_PROMPT)
    assert out["running"] is True
    assert out["exitCode"] is None
    ## the server differed from the unconfigured CLI, so config server ran first
    assert out["text"].index("Saved setting `config`.") < out["text"].index(OTP_PROMPT)
    ## nothing new after the offset the last answer gave
    assert env.output(after=out["next"])["text"] == ""

    assert env.type_text(CODE).status_code == 204
    final = env.wait_for_exit()
    assert final["exitCode"] == 0
    assert env.logged_in()
    ## the typed answer is echoed by the terminal, and the CLI accepted it
    assert_present(CODE, final["text"], "the console")
    assert_present("You are logged in!", final["text"], "the console")
    assert final["text"].endswith("--session " + MASK + "\n")

    ## reading in pieces by offset gives the same text as reading from the start
    pieces, after = [], 0
    while after < final["next"]:
        step = env.output(after=after)
        pieces.append(step["text"][:10])
        after += len(step["text"][:10])
    assert "".join(pieces) == final["text"]


def test_the_password_and_the_key_never_appear_in_the_output_or_on_a_command_line(
    env: CliEnv, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FAKE_BW_ASK_PASSWORD", "1")
    assert env.start().status_code == 200
    env.wait_for_text("? Master password: ")
    assert env.type_password().status_code == 204
    env.wait_for_text(OTP_PROMPT)
    assert env.type_text(CODE).status_code == 204
    final = env.wait_for_exit()
    assert final["exitCode"] == 0
    text = final["text"]
    assert_absent(PASSWORD, text, "the output")
    assert_absent(KEY, text, "the output")
    ## the echo of the typed password, then the key three times
    assert text.count(MASK) == 4
    assert_present("? Master password: [input is hidden] " + MASK, text, "the console")
    assert_present(f'BW_SESSION="{MASK}"', text, "the console")
    argv = env.argv_log()
    assert ["login", EMAIL, "--passwordenv", "BWTIDY_LOGIN_PASSWORD"] in argv
    assert all(PASSWORD not in arg for call in argv for arg in call)


def test_a_missing_or_wrong_token_gives_404(env: CliEnv):
    assert env.client.get("/api/bw-login/output").status_code == 404
    assert env.start().status_code == 200
    assert env.client.get("/api/bw-login/output").status_code == 404
    assert env.client.get("/api/bw-login/output", headers=env.headers("forged")).status_code == 404
    assert env.client.post("/api/bw-login/input", json={"text": "x"}).status_code == 404
    assert env.client.delete("/api/bw-login", headers=env.headers("forged")).status_code == 404
    assert env.client.post("/api/bw-login/finish").status_code == 404
    assert env.output()["running"] is True


def test_a_second_start_and_a_start_while_logged_in_give_409(env: CliEnv):
    assert env.start().status_code == 200
    second = env.start()
    assert second.status_code == 409
    assert "already running" in second.json()["detail"]
    env.wait_for_text(OTP_PROMPT)
    env.type_text(CODE)
    env.wait_for_exit()
    ## succeeded but not finished: still one login
    assert env.start().status_code == 409
    assert env.client.delete("/api/bw-login", headers=env.headers()).status_code == 204
    ## now the CLI is logged in
    refused = env.start()
    assert refused.status_code == 409
    assert "logged in already" in refused.json()["detail"]


def test_cancel_kills_the_cli_and_forgets_the_password(env: CliEnv):
    assert env.start().status_code == 200
    env.wait_for_text(OTP_PROMPT)
    assert env.bw_login.password == PASSWORD
    assert env.client.delete("/api/bw-login", headers=env.headers()).status_code == 204
    assert env.bw_login.password is None
    assert not env.bw_login.running
    assert env.client.get("/api/bw-login/output", headers=env.headers()).status_code == 404
    assert not env.logged_in()
    ## a new login can start right away
    assert env.start().status_code == 200


def test_finish_unlocks_and_sets_the_cookie(env: CliEnv):
    env.complete_login()
    response = env.finish()
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "unlocked"
    assert response.json()["bwLogin"]["loggedIn"] is True
    assert response.headers["set-cookie"].startswith(f"{COOKIE}=")
    assert env.bw.unlocked
    assert env.session_status()["state"] == "unlocked"
    ## the password and the token are gone
    assert env.bw_login.password is None
    assert env.client.get("/api/bw-login/output", headers=env.headers()).status_code == 404


def test_finish_before_exit_0_is_refused(env: CliEnv):
    assert env.start().status_code == 200
    env.wait_for_text(OTP_PROMPT)
    assert env.finish().status_code == 409
    assert env.bw_login.password == PASSWORD
    assert env.output()["running"] is True


def test_finish_restarts_bw_serve_when_it_is_down(env: CliEnv):
    calls: list[str] = []

    class StubServe:
        ready = False

        async def stop(self) -> None:
            calls.append("stop")

        async def start(self, wait: bool = True) -> None:
            calls.append("start")
            self.ready = True

    env.app.state.bw.bw_serve = StubServe()
    env.complete_login()
    assert env.finish().status_code == 200
    assert calls == ["stop", "start"]


class RecordingServe:
    """A BwServeProcess that records each stop and start with the CLI calls made before it."""

    def __init__(self, env: CliEnv) -> None:
        self.env = env
        self.calls: list[tuple[str, list[str]]] = []
        self.waits: list[bool] = []

    def _cli_calls(self) -> list[str]:
        return [call[0] for call in self.env.argv_log() if call[0] in ("config", "login")]

    async def stop(self) -> None:
        self.calls.append(("stop", self._cli_calls()))

    async def start(self, wait: bool = True) -> None:
        assert self.env.bw_login._proc is None
        self.calls.append(("start", self._cli_calls()))
        self.waits.append(wait)


@pytest.mark.parametrize("ending", ["exit 0", "exit 1", "cancel"])
def test_bw_serve_is_stopped_while_the_cli_runs(env: CliEnv, ending: str):
    serve = RecordingServe(env)
    env.bw_login.bw_serve = cast(BwServeProcess, serve)
    assert env.start().status_code == 200
    env.wait_for_text(OTP_PROMPT)
    ## stopped before config server, and not started again while the CLI waits for input
    assert serve.calls == [("stop", [])]
    if ending == "cancel":
        assert env.client.delete("/api/bw-login", headers=env.headers()).status_code == 204
    else:
        env.type_text(CODE if ending == "exit 0" else "000000")
        assert env.wait_for_exit()["exitCode"] == (0 if ending == "exit 0" else 1)
    assert serve.calls == [("stop", []), ("start", ["config", "login"])]
    assert serve.waits == [False]


def test_a_failed_login_is_forgotten_once_its_exit_is_reported(env: CliEnv):
    assert env.start().status_code == 200
    env.wait_for_text(OTP_PROMPT)
    env.type_text("000000")
    final = env.wait_for_exit()
    assert final["exitCode"] == 1
    assert_present("Invalid email or verification code", final["text"], "the console")
    assert env.bw_login.password is None
    assert env.client.get("/api/bw-login/output", headers=env.headers()).status_code == 404
    assert env.finish().status_code == 404
    assert env.start().status_code == 200


def test_input_after_exit_gives_409(env: CliEnv):
    env.complete_login()
    assert env.type_text("more").status_code == 409
    assert env.type_password().status_code == 409


def test_nothing_of_the_login_is_saved_and_the_session_shows_the_cli_state(env: CliEnv):
    before = env.session_status()["bwLogin"]
    assert before == {"loggedIn": False, "server": "https://vault.bitwarden.com", "email": ""}
    assert env.start(server="https://vaultwarden/", email=EMAIL).status_code == 200
    env.wait_for_text(OTP_PROMPT)
    ## the browser remembers what was typed, the state names only what the CLI is set to
    assert env.session_status()["bwLogin"] == {**before, "server": "https://vaultwarden/"}
    env.type_text(CODE)
    env.wait_for_exit()
    assert env.session_status()["bwLogin"] == {
        "loggedIn": True,
        "server": "https://vaultwarden/",
        "email": EMAIL,
    }
    database = (env.tmp_path / "data" / "bwtidy.sqlite").read_bytes().decode("latin-1")
    assert "bw_server" not in database
    assert "bw_email" not in database
    assert_absent(EMAIL, database, "the database file")
    assert_absent(PASSWORD, database, "the database file")


def login_state(env: CliEnv) -> bytes | None:
    """The file the gateway reads, None while it does not exist."""
    path = env.tmp_path / "data" / LOGIN_STATE_FILE
    return path.read_bytes() if path.exists() else None


def test_the_cli_s_login_state_is_written_for_the_gateway_whenever_it_changes(env: CliEnv):
    assert login_state(env) is None
    env.session_status()
    assert login_state(env) == LOGGED_OUT
    env.complete_login()
    assert login_state(env) == LOGGED_IN
    assert env.finish().status_code == 200
    path = env.tmp_path / "data" / LOGIN_STATE_FILE
    assert path.stat().st_mode & 0o777 == 0o600
    assert env.client.post("/api/bw-logout", headers=BW_LOGOUT).status_code == 204
    assert login_state(env) == LOGGED_OUT
    ## a failed login changes nothing
    assert env.start(password="wrong").status_code == 200
    assert env.wait_for_exit()["exitCode"] != 0
    assert login_state(env) == LOGGED_OUT


def test_an_unlock_notes_the_cli_as_logged_in(env: CliEnv):
    """An unlock notes the status although no login ran in this directory."""
    env.log_in_cli()
    env.bw.logged_in = True
    assert env.client.post("/api/session", json={"password": PASSWORD}).status_code == 200
    assert login_state(env) == LOGGED_IN


def test_config_server_is_skipped_when_the_server_is_the_same(env: CliEnv):
    (env.tmp_path / "config").write_text(SERVER)
    env.complete_login()
    assert all(call[0] != "config" for call in env.argv_log())
    assert_absent("Saved setting", env.output()["text"], "the text")


def test_the_login_is_dropped_after_ten_minutes(env: CliEnv):
    assert env.start().status_code == 200
    env.wait_for_text(OTP_PROMPT)
    env.clock.advance(599)
    assert env.output()["running"] is True
    env.clock.advance(2)
    assert env.client.get("/api/bw-login/output", headers=env.headers()).status_code == 404
    assert env.bw_login.password is None
    assert not env.bw_login.running


def test_nothing_from_the_console_reaches_the_log(
    env: CliEnv, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FAKE_BW_ASK_PASSWORD", "1")
    caplog.set_level("DEBUG")
    assert env.start().status_code == 200
    env.wait_for_text("? Master password: ")
    env.type_password()
    env.wait_for_text(OTP_PROMPT)
    env.type_text(CODE)
    env.wait_for_exit()
    assert env.finish().status_code == 200
    for secret in (
        PASSWORD,
        KEY,
        CODE,
        "OTP",
        "You are logged in",
        "Saved setting",
        env.token or "",
    ):
        assert_absent(secret, caplog.text, "the log")
    assert_present("Bitwarden login started", caplog.text, "the log")
    assert_present("exited with code 0", caplog.text, "the log")


BW_LOGOUT = {REQUEST_HEADER: "1"}


def test_bw_logout_ends_the_session_and_logs_the_cli_out(env: CliEnv):
    env.log_in_cli()
    assert env.client.post("/api/session", json={"password": PASSWORD}).status_code == 200
    assert env.session_status()["bwLogin"]["loggedIn"] is True
    response = env.client.post("/api/bw-logout", headers=BW_LOGOUT)
    assert response.status_code == 204
    assert "bwtidy_session=" in response.headers["set-cookie"]
    assert not env.bw.unlocked
    assert env.session_status()["state"] == "locked"
    assert not env.logged_in()
    assert ["logout", "--nointeraction"] in env.argv_log()
    assert env.session_status()["bwLogin"]["loggedIn"] is False


def test_bw_logout_without_the_session_cookie_is_401_and_logs_nothing_out(env: CliEnv):
    """Only the logged in browser logs the CLI out (a routing cookie alone reaches this route)."""
    env.log_in_cli()
    response = env.client.post("/api/bw-logout", headers=BW_LOGOUT)
    assert response.status_code == 401
    assert response.json() == {"detail": "Not logged in"}
    assert env.client.post("/api/session", json={"password": PASSWORD}).status_code == 200
    token = env.client.cookies.get(COOKIE)
    assert token is not None
    env.client.cookies.clear()
    env.client.cookies.set(COOKIE, "a-session-that-ended")
    assert env.client.post("/api/bw-logout", headers=BW_LOGOUT).status_code == 401
    env.client.cookies.clear()
    env.client.cookies.set(COOKIE, token)
    assert env.logged_in()
    assert ["logout", "--nointeraction"] not in env.argv_log()
    assert env.session_status()["state"] == "unlocked"


def test_bw_logout_without_the_header_is_403(env: CliEnv):
    env.log_in_cli()
    assert env.client.post("/api/session", json={"password": PASSWORD}).status_code == 200
    for headers in ({}, {REQUEST_HEADER: "0"}):
        assert env.client.post("/api/bw-logout", headers=headers).status_code == 403
    assert env.logged_in()
    assert ["logout", "--nointeraction"] not in env.argv_log()
    assert env.session_status()["state"] == "unlocked"


def test_bw_logout_saves_nothing_and_the_form_state_is_the_cli_s_own(env: CliEnv):
    """The browser prefills the form, so the logout keeps no server or email behind."""
    (env.tmp_path / "config").write_text(SERVER)
    env.log_in_cli()
    assert env.client.post("/api/session", json={"password": PASSWORD}).status_code == 200
    assert env.client.post("/api/bw-logout", headers=BW_LOGOUT).status_code == 204
    assert env.session_status()["bwLogin"] == {"loggedIn": False, "server": SERVER, "email": ""}
    database = (env.tmp_path / "data" / "bwtidy.sqlite").read_bytes().decode("latin-1")
    assert "bw_server" not in database
    assert "bw_email" not in database


def test_bw_logout_commits_the_pending_ui_edits_before_the_cli_logs_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(autocommit, "TICK", 3600.0)
    fake = write_fake_bw(tmp_path)
    (tmp_path / "state").write_text(EMAIL)
    env = AppEnv(tmp_path / "app", bw_bin=str(fake))
    names_at_logout: list[str] = []
    run_logout = bwlogin.run_logout

    async def recording_logout(bw_bin: str) -> int:
        names_at_logout.append(env.bw.items[0]["name"])
        return await run_logout(bw_bin)

    monkeypatch.setattr(bwlogin, "run_logout", recording_logout)
    with env:
        env.unlock()
        item_id = env.bw.items[0]["id"]
        operation = env.set_leaf(item_id, "name", "Renamed before the logout")
        response = env.client.post("/api/queue", json={"operations": [operation]})
        assert response.status_code == 201, response.text
        assert env.client.post("/api/bw-logout", headers=BW_LOGOUT).status_code == 204
    assert names_at_logout == ["Renamed before the logout"]
    assert not (tmp_path / "state").exists()


def test_login_routes_never_answer_401(env: CliEnv):
    assert env.start().status_code == 200
    env.wait_for_text(OTP_PROMPT)
    for response in (
        env.client.get("/api/bw-login/output"),
        env.client.post("/api/bw-login/input", json={"text": "x"}),
        env.client.delete("/api/bw-login"),
        env.client.post("/api/bw-login/finish"),
        env.finish(),
        env.start(),
    ):
        assert response.status_code != 401


## Console: the terminal line handling, with sequences captured from bw 2026.10.0 in a pty

EMAIL_PROMPT_REDRAW = (
    b"? Email address: \x1b[17D\x1b[17C",
    b"\x1b[2K\x1b[G",
    b"? Email address: d\x1b[18D\x1b[18C",
    b"\x1b[2K\x1b[G",
    b"? Email address: de",
    b"\x1b[19D\x1b[19C",
    b"\x1b[2K\x1b[G? Email address: dev\x1b[20D\x1b[20C\x1b[2K\x1b[G",
    b"? Email address: dev@bwtidy.test\x1b[33D\x1b[33C",
    b"\x1b[2K\x1b[G",
    b"? Email address: dev@bwtidy.test\x1b[33D\x1b[33C",
    b"\r\n",
    b"? Master password: [input is hidden] \x1b[37D",
    b"\x1b[37C",
)


def test_console_shows_a_redrawn_prompt_once_with_its_answer():
    console = Console([])
    console.feed(EMAIL_PROMPT_REDRAW[0])
    console.commit()
    assert console.text == "? Email address: "
    for chunk in EMAIL_PROMPT_REDRAW[1:]:
        console.feed(chunk)
    console.commit()
    assert console.text == "? Email address: dev@bwtidy.test\n? Master password: [input is hidden] "


def test_console_puts_a_line_redrawn_with_other_content_below():
    console = Console([])
    console.feed(b"? Master password: [input is hidden] \x1b[37D\x1b[37C")
    console.commit()
    console.feed(b"\x1b[2K\x1b[G? Master password: [hidden]\x1b[27D\x1b[27C\r\n")
    console.feed(b"You are logged in!\r\n")
    assert console.text == (
        "? Master password: [input is hidden] \n? Master password: [hidden]\nYou are logged in!\n"
    )


def test_console_masks_the_session_key_everywhere_it_is_printed():
    console = Console([])
    console.feed(SUCCESS.replace("\n", "\r\n").encode())
    assert_absent(KEY, console.text, "the console")
    assert console.text.count(MASK) == 3
    assert console.text == SUCCESS.replace(KEY, MASK)


def test_console_masks_the_password_and_a_sequence_split_between_chunks():
    console = Console([PASSWORD])
    console.feed(b"\x1b[")
    console.feed(b"2K\x1b[G")
    console.feed(b"? code: " + PASSWORD.encode()[:7])
    console.feed(PASSWORD.encode()[7:] + b"\r\n")
    assert console.text == f"? code: {MASK}\n"


@pytest.mark.parametrize(
    ("server", "email"),
    [(SERVER, "other@bwtidy.test"), ("https://vault.bitwarden.com", EMAIL)],
    ids=["email", "server"],
)
def test_a_fixed_account_refuses_another_server_or_email(
    fixed_env: CliEnv, server: str, email: str
):
    response = fixed_env.start(server=server, email=email)
    assert response.status_code == 422
    assert response.json()["detail"] == f"This bwtidy worker belongs to {EMAIL} on {SERVER}."
    assert fixed_env.argv_log() == []
    assert not fixed_env.bw_login.in_progress


def test_a_fixed_account_accepts_its_own_in_another_spelling(fixed_env: CliEnv):
    response = fixed_env.start(server=SERVER.upper() + "/", email=f" {EMAIL.upper()} ")
    assert response.status_code == 200
    fixed_env.wait_for_text(OTP_PROMPT)
    assert fixed_env.bw_login.in_progress
    assert fixed_env.client.delete("/api/bw-login", headers=fixed_env.headers()).status_code == 204


def test_a_fixed_account_shows_in_the_session_state(fixed_env: CliEnv):
    assert fixed_env.session_status()["bwLogin"] == {
        "loggedIn": False,
        "server": SERVER,
        "email": EMAIL,
        "accountFixed": True,
    }


def test_a_refused_body_is_never_echoed(env: CliEnv):
    secret = "master-password-for-the-echo-test"
    response = env.client.post("/api/bw-login", json={"server": "https://x", "password": secret})
    assert response.status_code == 422
    echoed = secret in response.text
    assert not echoed
    assert response.json()["detail"][0]["loc"] == ["body", "email"]
    assert response.json()["detail"][0]["msg"] == "Field required"
    assert set(response.json()["detail"][0]) == {"type", "loc", "msg"}


def test_a_one_time_code_of_the_wrong_type_is_never_echoed(env: CliEnv):
    secret = "one-time-code-for-the-echo-test"
    assert env.start().status_code == 200
    response = env.client.post(
        "/api/bw-login/input", json={"text": [secret]}, headers=env.headers()
    )
    assert response.status_code == 422
    echoed = secret in response.text
    assert not echoed


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        ('{"password": ["SECRET"]}', "application/json"),
        ('["SECRET"]', "application/json"),
        ('{"password": "SECRET', "application/json"),
        ("SECRET", "text/plain"),
    ],
)
def test_a_body_of_the_wrong_shape_is_never_echoed(env: CliEnv, body: str, content_type: str):
    secret = "master-password-for-the-echo-test"
    response = env.client.post(
        "/api/session",
        content=body.replace("SECRET", secret),
        headers={"Content-Type": content_type},
    )
    assert response.status_code == 422
    echoed = secret in response.text
    assert not echoed
    assert all(set(entry) == {"type", "loc", "msg"} for entry in response.json()["detail"])


## a bw that never answers: it leaves its pid behind and sleeps
HANGING_BW = f"""#!{sys.executable}
import os, time
with open(os.environ["FAKE_BW_PID"], "w") as file:
    file.write(str(os.getpid()))
time.sleep(30)
"""


def write_hanging_bw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    pid_file = tmp_path / "pid"
    script = tmp_path / "bw"
    script.write_text(HANGING_BW)
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_BW_PID", str(pid_file))
    return script, pid_file


def assert_gone(pid_file: Path) -> None:
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_a_bw_status_over_its_timeout_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """The spec's rule for every bw the backend starts (Worker mode): a hung one is stopped."""
    script, pid_file = write_hanging_bw(tmp_path, monkeypatch)
    monkeypatch.setattr(bwlogin, "CLI_TIMEOUT", 1.0)
    login = BwLogin(str(script), cast(Any, None))

    assert asyncio.run(login.cli_status()) == {}
    assert_present("bw status failed: TimeoutError", caplog.text, "the log")
    assert_gone(pid_file)


def test_a_bw_logout_over_its_timeout_is_stopped_and_answers_124(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    script, pid_file = write_hanging_bw(tmp_path, monkeypatch)
    monkeypatch.setattr(bwlogin, "LOGOUT_TIMEOUT", 1.0)

    assert asyncio.run(bwlogin.run_logout(str(script))) == 124
    assert_present("bw logout did not finish within 1 s", caplog.text, "the log")
    assert_gone(pid_file)
