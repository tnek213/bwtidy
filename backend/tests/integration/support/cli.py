"""The bw CLI of the dev stack and the credentials of its seeded vault."""

import json
import os
import subprocess
from typing import Any

## dev credentials from dev/README.md, never used anywhere else
PASSWORD = "bwtidy-dev-password-1"
EMAIL = "dev@bwtidy.test"
SERVER = "https://vaultwarden"

## seconds, so a bw that hangs fails one test instead of stalling the whole run
BW_TIMEOUT = 300
## the auto-commit of the test apps (after seconds, max entries): the product waits 180 s
TEST_AUTO_COMMIT = (10, 20)


class BwCli:
    """bw in the dev stack's app container.

    Every unlock, by the CLI or by bw serve, invalidates the session keys handed out before.
    With appdata the CLI keeps its state in that directory instead of the app's, so it logs
    in as another device and leaves the app's login and session alone.
    """

    def __init__(self, email: str = EMAIL, appdata: str | None = None) -> None:
        self.email = email
        self.appdata = appdata

    def run(self, *args: str, session: str | None = None) -> str:
        env = {k: v for k, v in os.environ.items() if k != "BW_SESSION"}
        if session is not None:
            env["BW_SESSION"] = session
        if self.appdata is not None:
            env["BITWARDENCLI_APPDATA_DIR"] = self.appdata
        try:
            result = subprocess.run(
                ["bw", *args, "--nointeraction"],
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=BW_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            ## only the subcommand, the other arguments can hold a session key or an item
            raise RuntimeError(f"bw {args[0]} timed out after {BW_TIMEOUT} s") from None
        if result.returncode != 0:
            ## stderr only: bw's stdout holds vault content, the command line a session key
            message = result.stderr.strip()
            if not message:
                message = f"bw {args[0]} failed with exit {result.returncode} (no error message)"
            else:
                message = f"bw {args[0]} failed: {message}"
            raise RuntimeError(message)
        return result.stdout

    def json(self, *args: str, session: str | None = None) -> Any:
        return json.loads(self.run(*args, session=session))

    def unlock(self) -> str:
        os.environ["BW_PASSWORD"] = PASSWORD
        try:
            return self.run("unlock", "--passwordenv", "BW_PASSWORD", "--raw").strip()
        finally:
            del os.environ["BW_PASSWORD"]

    def ensure_logged_in(self) -> None:
        """Log the CLI in to the dev stack again when it is logged out, as dev-vault setup does."""
        status = self.json("status")
        if status["status"] != "unauthenticated":
            return
        if status.get("serverUrl") != SERVER:
            self.run("config", "server", SERVER)
        os.environ["BW_PASSWORD"] = PASSWORD
        try:
            self.run("login", self.email, "--passwordenv", "BW_PASSWORD")
        finally:
            del os.environ["BW_PASSWORD"]

    def status(self, session: str | None = None) -> str:
        """Without a session key bw status says locked even while bw serve is unlocked."""
        return self.json("status", session=session)["status"]
