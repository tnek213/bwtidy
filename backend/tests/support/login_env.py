"""The bw login's tests: a fake bw executable and the app that runs it as its CLI, with the
helpers of the worker mode's tests that run it as a worker."""

import asyncio
import json
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from bwtidy.protocol import LOGIN_HEADER
from tests.support.app_env import AppEnv
from tests.support.fake_bw import PASSWORD, FakeBwServe
from tests.support.quiet import last_prompt
from tests.support.sample_vault import LOGIN

SERVER = "https://vaultwarden"
EMAIL = "dev@bwtidy.test"
CODE = "123456"
KEY = "fakeSessionKey+/0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ab=="
OTP_PROMPT = "? New device verification required. Enter OTP sent to login email: "
PROMPTS = ("? Master password: ", OTP_PROMPT)
## what bw login 2026.10.0 prints on success, with the fake key
SUCCESS = (
    "You are logged in!\n\n"
    "To unlock your vault, set your session key to the `BW_SESSION` environment variable. ex:\n"
    f'$ export BW_SESSION="{KEY}"\n'
    f'> $env:BW_SESSION="{KEY}"\n\n'
    "You can also pass the session key to any command with the `--session` option. ex:\n"
    f"$ bw list items --session {KEY}\n"
)
FAKE_BW = """#!{python}
import json, os, sys

STATE = {state!r}
CONFIG = {config!r}
ARGV_LOG = {argv_log!r}
PASSWORD = {password!r}
SUCCESS = {success!r}
OTP_PROMPT = {otp_prompt!r}

args = sys.argv[1:]
with open(ARGV_LOG, "a") as f:
    f.write(json.dumps(args) + "\\n")
cmd = args[0]


def logged_in():
    return os.path.exists(STATE)


if cmd == "status":
    server = open(CONFIG).read().strip() if os.path.exists(CONFIG) else None
    if logged_in():
        status = {{"serverUrl": server, "lastSync": "2026-10-27T00:17:33.664Z",
                  "userEmail": open(STATE).read().strip(), "userId": "u1", "status": "locked"}}
    else:
        status = {{"serverUrl": server, "lastSync": None, "status": "unauthenticated"}}
    print(json.dumps(status))
elif cmd == "config":
    if logged_in():
        print("Logout required before server config update.")
        sys.exit(1)
    open(CONFIG, "w").write(args[2])
    print("Saved setting `config`.")
elif cmd == "login":
    email = args[1]
    if logged_in():
        print(f"You are already logged in as {{email}}.")
        sys.exit(1)
    password = os.environ.get(args[args.index("--passwordenv") + 1])
    ## the real bw asks only when the variable is empty and hides the answer, the echo here
    ## checks the mask
    if os.environ.get("FAKE_BW_ASK_PASSWORD"):
        sys.stdout.write("? Master password: [input is hidden] ")
        sys.stdout.flush()
        password = sys.stdin.readline().rstrip("\\n")
    if password != PASSWORD:
        print("Username or password is incorrect. Try again")
        sys.exit(1)
    sys.stdout.write(OTP_PROMPT)
    sys.stdout.flush()
    code = sys.stdin.readline().rstrip("\\n")
    if code != "123456":
        print("Invalid email or verification code")
        sys.exit(1)
    open(STATE, "w").write(email)
    sys.stdout.write(SUCCESS)
elif cmd == "logout":
    if logged_in():
        os.remove(STATE)
    print("You have logged out.")
else:
    print("unknown command", cmd)
    sys.exit(2)
"""


class CliEnv(AppEnv):
    """The app with the fake bw executable in tmp_path as its CLI, on the fake bw serve's own
    vault and without fake backups, with the steps of a login from the web UI. Its data
    directory is tmp_path / "data"."""

    def __init__(self, tmp_path: Path, **knobs: Any) -> None:
        fake = write_fake_bw(tmp_path)
        super().__init__(
            tmp_path / "data", bw=FakeBwServe(), fake_backups=False, bw_bin=str(fake), **knobs
        )
        self.tmp_path = tmp_path
        self.token: str | None = None

    def headers(self, token: str | None = None) -> dict[str, str]:
        token = token or self.token
        return {LOGIN_HEADER: token} if token else {}

    def start(self, server: str = SERVER, email: str = EMAIL, password: str = PASSWORD):
        response = self.client.post(
            "/api/bw-login", json={"server": server, "email": email, "password": password}
        )
        if response.status_code == 200:
            self.token = response.json()["loginId"]
        return response

    def output(self, after: int = 0, token: str | None = None) -> dict[str, Any]:
        response = self.client.get(
            f"/api/bw-login/output?after={after}", headers=self.headers(token)
        )
        assert response.status_code == 200, response.text
        return response.json()

    def wait_for(self, done: Callable[[dict[str, Any]], bool], timeout: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            out = self.output()
            if done(out):
                return out
            assert time.monotonic() < deadline, (
                f"timed out after {timeout} s, last prompt: {last_prompt(out['text'], PROMPTS)}"
            )
            time.sleep(0.05)

    def wait_for_text(self, needle: str) -> dict[str, Any]:
        return self.wait_for(lambda out: needle in out["text"])

    def wait_for_exit(self) -> dict[str, Any]:
        return self.wait_for(lambda out: not out["running"])

    def type_text(self, text: str):
        return self.client.post("/api/bw-login/input", json={"text": text}, headers=self.headers())

    def type_password(self):
        return self.client.post(
            "/api/bw-login/input", json={"password": True}, headers=self.headers()
        )

    def finish(self):
        return self.client.post("/api/bw-login/finish", headers=self.headers())

    def argv_log(self) -> list[list[str]]:
        path = self.tmp_path / "argv.log"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def logged_in(self) -> bool:
        return (self.tmp_path / "state").exists()

    def log_in_cli(self) -> None:
        (self.tmp_path / "state").write_text(EMAIL)

    def complete_login(self) -> dict[str, Any]:
        """Start, answer the code and wait for exit 0."""
        assert self.start().status_code == 200
        self.wait_for_text(OTP_PROMPT)
        assert self.type_text(CODE).status_code == 204
        out = self.wait_for_exit()
        assert out["exitCode"] == 0
        return out


def write_fake_bw(tmp_path: Path) -> Path:
    fake = tmp_path / "bw"
    fake.write_text(
        FAKE_BW.format(
            python=sys.executable,
            state=str(tmp_path / "state"),
            config=str(tmp_path / "config"),
            argv_log=str(tmp_path / "argv.log"),
            password=PASSWORD,
            success=SUCCESS,
            otp_prompt=OTP_PROMPT,
        )
    )
    fake.chmod(0o755)
    return fake


IDLE_EXIT = 600.0


def make_env(tmp_path: Path, **worker: Any) -> Iterator[CliEnv]:
    with CliEnv(tmp_path, **worker) as env:
        yield env


## the session's end gives up on a bw serve that never answers (docs/spec.md, Worker mode)
SHORT_LIMIT = 0.5
## how long a test waits for what the limits bound, far above them
BOUND = 10.0


def queue_an_edit(env: CliEnv) -> int:
    """A pending UI edit of LOGIN's username, with the auto-commit set to wait an hour."""
    put = env.client.put("/api/queue/auto-commit", json={"afterSeconds": 3600, "maxEntries": 20})
    assert put.status_code == 200
    [row] = [r for r in env.client.get("/api/grid").json()["rows"] if r["id"] == LOGIN["id"]]
    op = {
        "kind": "setLeaf",
        "itemId": LOGIN["id"],
        "revisionDate": row["revisionDate"],
        "column": "login.username",
        "value": "someone-else",
    }
    response = env.client.post("/api/queue", json={"operations": [op]})
    assert response.status_code == 201
    [entry_id] = response.json()["ids"]
    return entry_id


def stop_answering(env: CliEnv) -> asyncio.Event:
    """From now on bw serve answers nothing until the event is set (set it in the loop)."""
    answering = asyncio.Event()
    env.bw.answering = answering
    return answering
