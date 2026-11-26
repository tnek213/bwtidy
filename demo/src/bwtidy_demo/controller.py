"""The demo's controller (bwtidy-demo-controller): a pool of ready vaults and their deletion.

It keeps DEMO_POOL_READY vaults registered and seeded, and never more than DEMO_MAX ready plus
handed out. Its HTTP API, on the internal network only, needs the controller token of the
secrets volume as a bearer token on every request:

- `POST /take` answers a ready vault as `{server, email, password}` and hands it out, 409
  when none is ready.
- `POST /release` with `{email}` deletes that Vaultwarden user and forgets it, 204. A user the
  controller does not know (one from before a restart) is deleted all the same.
- `GET /status` answers the counts: ready, taken, building and failed.

Vaultwarden's data is temporary, so the pool starts empty at every start and builds its
vaults one after another. A vault that fails is deleted, its id stays in the ledger and the
next one waits a backoff, so a broken step neither loops fast nor leaves a user behind.

No password or token reaches a log line or an error message: the logs name ids and counts.
"""

import asyncio
import contextlib
import hmac
import logging
import os
import re
import secrets
import shutil
import tempfile
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx2 as httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from bwtidy_demo import init, vaultwarden

log = logging.getLogger(__name__)

PORT = 8100
SERVER = "https://vaultwarden"
DOMAIN = "demo.bwtidy.test"
LEDGER = Path("/ledger/ids")
POOL_READY = 2
MAX_VAULTS = 6
## the EFF word list has a few words with a hyphen ("t-shirt"), so an id has four or more parts
ID = re.compile(r"[a-z]+(?:-[a-z]+){3,}")
## an email's local part is at most 64 characters
ID_LENGTH = 64
DRAWS = 20
BW_TIMEOUT = 300.0
RETRY_FIRST = 10.0
RETRY_MAX = 300.0
NOT_READY = "No demo vault is ready. Try again in a minute."


class SeedError(Exception):
    """A bw step failed. The text names the step and the exit status, never a value."""


class Bw(Protocol):
    async def generate_id(self) -> str: ...

    async def seed(self, server: str, email: str, password: str) -> None: ...


class Users(Protocol):
    async def register(self, email: str, password: str) -> None: ...

    async def delete(self, email: str) -> bool: ...


@dataclass(frozen=True)
class Vault:
    id: str
    email: str
    password: str = field(repr=False)


def email_of(vault_id: str) -> str:
    return f"{vault_id}@{DOMAIN}"


class Ledger:
    """Every id ever drawn, one per line, in a file of the persistent volume."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            lines = path.read_text().splitlines()
        except FileNotFoundError:
            lines = []
        self.ids = {line.strip() for line in lines if line.strip()}

    def __contains__(self, vault_id: str) -> bool:
        return vault_id in self.ids

    def __len__(self) -> int:
        return len(self.ids)

    def add(self, vault_id: str) -> None:
        """Note vault_id on disk (fsync) before it is used anywhere."""
        new = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as file:
            file.write(vault_id + "\n")
            file.flush()
            os.fsync(file.fileno())
        if new:
            ## the new file's directory entry is durable only once its directory is synced
            fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self.ids.add(vault_id)


class BwCli:
    """The image's bw: ids from its generator, and the seeding of a vault with the fixture.

    Every call has a fresh BITWARDENCLI_APPDATA_DIR of its own, deleted afterwards. The
    password and the session key go to bw in its environment, never in its arguments, and its
    output is never logged.
    """

    def __init__(
        self, fixture: Path, ca_file: Path, *, bw: str = "bw", timeout: float = BW_TIMEOUT
    ) -> None:
        self.fixture = fixture
        self.ca_file = ca_file
        self.bw = bw
        self.timeout = timeout

    def _env(self, home: Path, **extra: str) -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(home),
            "BITWARDENCLI_APPDATA_DIR": str(home / "bw"),
            "NODE_EXTRA_CA_CERTS": str(self.ca_file),
            "BW_NOINTERACTION": "true",
            **extra,
        }

    async def _run(self, step: str, args: list[str], env: dict[str, str]) -> str:
        process = await asyncio.create_subprocess_exec(
            self.bw,
            *args,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), self.timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise SeedError(f"bw {step} timed out") from None
        if process.returncode != 0:
            raise SeedError(f"bw {step} failed with exit status {process.returncode}")
        return stdout.decode(errors="replace")

    @contextlib.asynccontextmanager
    async def _home(self) -> AsyncGenerator[Path]:
        home = Path(tempfile.mkdtemp(prefix="bwtidy-demo-bw-"))
        try:
            yield home
        finally:
            await asyncio.to_thread(shutil.rmtree, home, True)

    async def generate_id(self) -> str:
        """Four words from bw's passphrase generator. Without a server bw writes noise on
        stderr, so only stdout counts."""
        async with self._home() as home:
            args = ["generate", "--passphrase", "--words", "4", "--separator", "-"]
            return (await self._run("generate", args, self._env(home))).strip()

    async def seed(self, server: str, email: str, password: str) -> None:
        async with self._home() as home:
            env = self._env(home, DEMO_PASSWORD=password)
            await self._run("config server", ["config", "server", server], env)
            await self._run("login", ["login", email, "--passwordenv", "DEMO_PASSWORD"], env)
            unlock = ["unlock", "--passwordenv", "DEMO_PASSWORD", "--raw"]
            session = (await self._run("unlock", unlock, env)).strip()
            if not session:
                raise SeedError("bw unlock answered no session")
            env = self._env(home, BW_SESSION=session)
            await self._run("import", ["import", "bitwardenjson", str(self.fixture)], env)
            await self._run("sync", ["sync"], env)


class VaultwardenUsers:
    """The stack's Vaultwarden: registration as a client, deletion through the admin API."""

    def __init__(self, server: str, admin_token: str, ca_file: Path) -> None:
        self.server = server
        self.ca_file = ca_file
        self.admin = vaultwarden.AdminSession(server, admin_token, ca_file=ca_file)

    async def register(self, email: str, password: str) -> None:
        async with vaultwarden.client(self.server, self.ca_file) as http:
            await vaultwarden.register(http, email, password)

    async def delete(self, email: str) -> bool:
        return await self.admin.delete_user(email)

    async def aclose(self) -> None:
        await self.admin.aclose()


def reason(error: BaseException) -> str:
    """What a log line says of a failure. Only the demo's own errors and httpx's carry their
    text, which names no value. Any other exception is named by its type alone."""
    if isinstance(error, (SeedError, vaultwarden.VaultwardenError, httpx.HTTPError)):
        return f"{type(error).__name__}: {error}"
    return type(error).__name__


class Pool:
    """The ready vaults and the ones handed out, built one at a time in the background."""

    def __init__(
        self,
        *,
        server: str,
        ledger: Ledger,
        bw: Bw,
        users: Users,
        ready: int = POOL_READY,
        max_vaults: int = MAX_VAULTS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retry_first: float = RETRY_FIRST,
        retry_max: float = RETRY_MAX,
    ) -> None:
        if ready < 0 or max_vaults < 1 or ready > max_vaults:
            raise ValueError("the pool needs 0 <= ready <= max and max >= 1")
        self.server = server
        self.ledger = ledger
        self.bw = bw
        self.users = users
        self.target = ready
        self.max = max_vaults
        self._sleep = sleep
        self._retry_first = retry_first
        self._retry_max = retry_max
        self._ready: deque[Vault] = deque()
        self._taken: dict[str, Vault] = {}
        self._building = 0
        self.failed = 0
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._task: asyncio.Task | None = None

    def counts(self) -> dict[str, int]:
        return {
            "ready": len(self._ready),
            "taken": len(self._taken),
            "building": self._building,
            "failed": self.failed,
        }

    def _wanted(self) -> bool:
        held = len(self._ready) + len(self._taken) + self._building
        return len(self._ready) + self._building < self.target and held < self.max

    async def draw(self) -> str:
        """A new id, noted in the ledger before it is used. An id drawn before is drawn again."""
        for _ in range(DRAWS):
            vault_id = await self.bw.generate_id()
            if not ID.fullmatch(vault_id) or len(vault_id) > ID_LENGTH:
                log.warning("bw generate answered no id of four words, drawing again")
                continue
            if vault_id in self.ledger:
                log.info("the id %s was used before, drawing again", vault_id)
                continue
            await asyncio.to_thread(self.ledger.add, vault_id)
            return vault_id
        raise SeedError(f"no new id in {DRAWS} draws")

    async def build(self) -> Vault:
        """Register and seed a new vault. One that fails is deleted again before this raises."""
        vault_id = await self.draw()
        vault = Vault(vault_id, email_of(vault_id), secrets.token_urlsafe(24))
        log.info("building the vault %s", vault_id)
        try:
            await self.users.register(vault.email, vault.password)
            await self.bw.seed(self.server, vault.email, vault.password)
        except Exception as error:
            log.warning("the vault %s failed: %s", vault_id, reason(error))
            await self._delete(vault.email)
            raise
        return vault

    async def _delete(self, email: str) -> bool:
        try:
            await self.users.delete(email)
        except Exception as error:
            log.warning("could not delete the user %s: %s", email, reason(error))
            return False
        return True

    async def run(self) -> None:
        """Build vaults while the pool wants one, forever."""
        retry = self._retry_first
        while True:
            if not self._wanted():
                self._idle.set()
                self._wake.clear()
                await self._wake.wait()
                continue
            self._idle.clear()
            self._building += 1
            try:
                vault = await self.build()
            except Exception:
                vault = None
            finally:
                self._building -= 1
            if vault is None:
                self.failed += 1
                log.info("the next vault waits %.0f seconds, %s", retry, self._summary())
                await self._sleep(retry)
                retry = min(retry * 2, self._retry_max)
                continue
            retry = self._retry_first
            self._ready.append(vault)
            log.info("the vault %s is ready, %s", vault.id, self._summary())

    def _summary(self) -> str:
        counts = self.counts()
        return ", ".join(f"{name} {count}" for name, count in counts.items())

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def settled(self) -> None:
        """Wait until the pool wants no more vaults (for tests)."""
        await asyncio.sleep(0)
        await self._idle.wait()

    def take(self) -> Vault | None:
        if not self._ready:
            return None
        vault = self._ready.popleft()
        self._taken[vault.email] = vault
        self._idle.clear()
        self._wake.set()
        log.info("handed out the vault %s, %s", vault.id, self._summary())
        return vault

    async def release(self, email: str) -> bool:
        """Delete the user with email and forget it. False when Vaultwarden refused, then the
        vault stays taken, so the caller can release it again."""
        if not await self._delete(email):
            return False
        self._taken.pop(email, None)
        for vault in list(self._ready):
            if vault.email == email:
                self._ready.remove(vault)
        self._idle.clear()
        self._wake.set()
        log.info("released %s, %s", email, self._summary())
        return True


class Release(BaseModel):
    email: str


def authorized(request: Request, token: str) -> bool:
    scheme, _, given = request.headers.get("authorization", "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(given.encode(), token.encode())


def create_app(pool: Pool, token: str, on_close: Callable[[], Awaitable[None]] | None = None):
    """The controller's API over pool. Every request needs token, the unknown ones too."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        pool.start()
        try:
            yield
        finally:
            await pool.stop()
            if on_close is not None:
                await on_close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if not authorized(request, token):
            return PlainTextResponse(
                "A bearer token is required.", 401, headers={"WWW-Authenticate": "Bearer"}
            )
        return await call_next(request)

    @app.post("/take")
    async def take() -> Response:
        vault = pool.take()
        if vault is None:
            return PlainTextResponse(NOT_READY, 409)
        return JSONResponse(
            {"server": pool.server, "email": vault.email, "password": vault.password},
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/release")
    async def release(body: Release) -> Response:
        if not body.email.endswith("@" + DOMAIN):
            return PlainTextResponse("Only demo users can be released.", 400)
        if not await pool.release(body.email):
            return PlainTextResponse("Vaultwarden did not delete the user. Try again.", 502)
        return Response(status_code=204)

    @app.get("/status")
    async def status() -> dict[str, int]:
        return pool.counts()

    return app


def number(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise SystemExit(f"{name} must be a whole number") from None


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    fixture = os.environ.get("DEMO_FIXTURE", "").strip()
    if not fixture:
        raise SystemExit("DEMO_FIXTURE must name the fixture to import")
    directory = Path(os.environ.get("DEMO_SECRETS", str(init.SECRETS_DIR)))
    server = os.environ.get("DEMO_SERVER", SERVER).rstrip("/")
    ca_file = directory / init.CA_CERT
    try:
        token = (directory / init.CONTROLLER_TOKEN).read_text().strip()
        admin_token = (directory / init.ADMIN_TOKEN).read_text().strip()
    except OSError as error:
        raise SystemExit(f"could not read the secrets in {directory}: {error.strerror}") from None
    users = VaultwardenUsers(server, admin_token, ca_file)
    try:
        pool = Pool(
            server=server,
            ledger=Ledger(Path(os.environ.get("DEMO_LEDGER", str(LEDGER)))),
            bw=BwCli(Path(fixture), ca_file),
            users=users,
            ready=number("DEMO_POOL_READY", POOL_READY),
            max_vaults=number("DEMO_MAX", MAX_VAULTS),
        )
    except ValueError as error:
        raise SystemExit(f"DEMO_POOL_READY and DEMO_MAX: {error}") from None
    app = create_app(pool, token, on_close=users.aclose)
    host = os.environ.get("DEMO_CONTROLLER_HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=number("DEMO_CONTROLLER_PORT", PORT), log_config=None)
