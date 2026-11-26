import asyncio
import json
import logging
import os
import stat
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bwtidy_demo import controller
from bwtidy_demo.controller import BwCli, Ledger, Pool, SeedError
from bwtidy_demo.vaultwarden import VaultwardenError

TOKEN = "controller-token-1"
SERVER = "https://vaultwarden"
WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]


def ids(count: int) -> list[str]:
    """count distinct ids of four words."""
    return [f"{WORDS[i % 8]}-{WORDS[(i // 8) % 8]}-word-{'x' * (i + 1)}" for i in range(count)]


class FakeBw:
    """bw's generator answers the given ids in turn, seed fails for the emails in fail."""

    def __init__(self, generated: Iterable[str] | None = None) -> None:
        self.generated: Iterator[str] = iter(generated if generated is not None else ids(100))
        self.seeded: list[str] = []
        self.passwords: list[str] = []
        self.fail: set[str] = set()

    async def generate_id(self) -> str:
        return next(self.generated)

    async def seed(self, server: str, email: str, password: str) -> None:
        assert server == SERVER
        self.passwords.append(password)
        if email in self.fail:
            raise SeedError("bw import failed with exit status 1")
        self.seeded.append(email)


class FakeUsers:
    """Vaultwarden's users. The most it ever held at once is in peak."""

    def __init__(self) -> None:
        self.users: set[str] = set()
        self.registered: list[str] = []
        self.deleted: list[str] = []
        self.peak = 0
        self.fail_register: set[str] = set()
        self.fail_delete = False

    async def register(self, email: str, password: str) -> None:
        self.registered.append(email)
        if email in self.fail_register:
            raise VaultwardenError("the registration failed with HTTP 400")
        self.users.add(email)
        self.peak = max(self.peak, len(self.users))

    async def delete(self, email: str) -> bool:
        if self.fail_delete:
            raise VaultwardenError("deleting a user failed with HTTP 500")
        self.deleted.append(email)
        if email in self.users:
            self.users.remove(email)
            return True
        return False


class Stack:
    def __init__(self, tmp_path: Path, **pool: int) -> None:
        self.ledger_path = tmp_path / "ledger" / "ids"
        self.bw = FakeBw()
        self.users = FakeUsers()
        self.slept: list[float] = []
        self.pool_args = pool

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def pool(self) -> Pool:
        return Pool(
            server=SERVER,
            ledger=Ledger(self.ledger_path),
            bw=self.bw,
            users=self.users,
            sleep=self.sleep,
            **self.pool_args,
        )


def settle(client: TestClient, pool: Pool) -> None:
    portal = client.portal
    assert portal is not None
    portal.call(pool.settled)


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def running(pool: Pool):
    pool.start()
    await pool.settled()


def test_the_pool_fills_to_the_ready_count(tmp_path: Path):
    stack = Stack(tmp_path, ready=2, max_vaults=6)
    pool = stack.pool()

    async def go():
        await running(pool)
        counts = pool.counts()
        await pool.stop()
        return counts

    assert asyncio.run(go()) == {"ready": 2, "taken": 0, "building": 0, "failed": 0}
    assert stack.users.registered == stack.bw.seeded
    assert len(stack.bw.seeded) == 2


def test_the_pool_never_holds_more_than_the_max(tmp_path: Path):
    stack = Stack(tmp_path, ready=2, max_vaults=3)
    pool = stack.pool()

    async def go():
        await running(pool)
        taken = []
        for _ in range(5):
            vault = pool.take()
            if vault is not None:
                taken.append(vault)
            await pool.settled()
            counts = pool.counts()
            assert counts["ready"] + counts["taken"] <= 3
        counts = pool.counts()
        await pool.stop()
        return taken, counts

    taken, counts = asyncio.run(go())
    assert len(taken) == 3
    assert counts == {"ready": 0, "taken": 3, "building": 0, "failed": 0}
    assert stack.users.peak == 3


def test_a_release_makes_room_for_a_new_vault(tmp_path: Path):
    stack = Stack(tmp_path, ready=1, max_vaults=1)
    pool = stack.pool()

    async def go():
        await running(pool)
        vault = pool.take()
        assert vault is not None
        await pool.settled()
        assert pool.counts()["ready"] == 0
        assert await pool.release(vault.email)
        await pool.settled()
        counts = pool.counts()
        await pool.stop()
        return vault, counts

    vault, counts = asyncio.run(go())
    assert counts == {"ready": 1, "taken": 0, "building": 0, "failed": 0}
    assert stack.users.deleted == [vault.email]
    assert len(stack.users.registered) == 2


def test_every_vault_has_its_own_id_email_and_password(tmp_path: Path):
    stack = Stack(tmp_path, ready=4, max_vaults=4)
    pool = stack.pool()

    async def go():
        await running(pool)
        vaults = [pool.take() for _ in range(4)]
        await pool.stop()
        return vaults

    vaults = asyncio.run(go())
    assert all(vault is not None for vault in vaults)
    assert len({vault.id for vault in vaults if vault}) == 4
    assert len({vault.password for vault in vaults if vault}) == 4
    for vault in vaults:
        assert vault is not None
        assert vault.email == f"{vault.id}@demo.bwtidy.test"
        assert len(vault.password) >= 32
        assert vault.password not in repr(vault)


def test_a_repeated_id_is_drawn_again(tmp_path: Path):
    stack = Stack(tmp_path, ready=2, max_vaults=2)
    one, two = ids(2)
    stack.bw = FakeBw([one, one, one, two])
    pool = stack.pool()

    async def go():
        await running(pool)
        await pool.stop()

    asyncio.run(go())
    assert stack.users.registered == [f"{one}@demo.bwtidy.test", f"{two}@demo.bwtidy.test"]
    assert stack.ledger_path.read_text().splitlines() == [one, two]


def test_an_answer_that_is_no_id_is_drawn_again(tmp_path: Path):
    stack = Stack(tmp_path, ready=1, max_vaults=1)
    (good,) = ids(1)
    stack.bw = FakeBw(["", "one-two-three", "Upper-case-is-out", "a@b-c-d-e", good])
    pool = stack.pool()

    async def go():
        await running(pool)
        await pool.stop()

    asyncio.run(go())
    assert stack.users.registered == [f"{good}@demo.bwtidy.test"]


def test_the_ledger_survives_a_restart(tmp_path: Path):
    first = Stack(tmp_path, ready=2, max_vaults=2)
    used = ids(2)
    first.bw = FakeBw(used)

    async def go(pool: Pool):
        await running(pool)
        await pool.stop()

    asyncio.run(go(first.pool()))
    ## a restart: Vaultwarden is empty, the generator draws the old ids first
    second = Stack(tmp_path, ready=2, max_vaults=2)
    second.bw = FakeBw([*used, *ids(4)[2:]])
    asyncio.run(go(second.pool()))
    assert second.users.registered == [f"{i}@demo.bwtidy.test" for i in ids(4)[2:]]
    assert Ledger(second.ledger_path).ids == set(ids(4))


def test_a_generator_with_no_new_id_fails_the_build(tmp_path: Path):
    stack = Stack(tmp_path, ready=1, max_vaults=1)
    (one,) = ids(1)
    stack.ledger_path.parent.mkdir()
    stack.ledger_path.write_text(one + "\n")
    stack.bw = FakeBw([one] * controller.DRAWS)
    pool = stack.pool()
    with pytest.raises(SeedError, match="no new id"):
        asyncio.run(pool.build())
    assert stack.users.registered == []


def test_a_failed_seed_deletes_the_user_and_keeps_its_id(tmp_path: Path):
    stack = Stack(tmp_path, ready=1, max_vaults=1)
    first, second = ids(2)
    stack.bw.fail = {f"{first}@demo.bwtidy.test"}
    pool = stack.pool()

    async def go():
        await running(pool)
        counts = pool.counts()
        await pool.stop()
        return counts

    counts = asyncio.run(go())
    assert counts == {"ready": 1, "taken": 0, "building": 0, "failed": 1}
    assert stack.users.deleted == [f"{first}@demo.bwtidy.test"]
    assert stack.users.users == {f"{second}@demo.bwtidy.test"}
    assert stack.ledger_path.read_text().splitlines() == [first, second]
    assert stack.slept == [controller.RETRY_FIRST]


def test_a_failed_registration_deletes_the_user(tmp_path: Path):
    stack = Stack(tmp_path, ready=1, max_vaults=1)
    first, second = ids(2)
    stack.users.fail_register = {f"{first}@demo.bwtidy.test"}
    pool = stack.pool()

    async def go():
        await running(pool)
        await pool.stop()

    asyncio.run(go())
    assert stack.users.deleted == [f"{first}@demo.bwtidy.test"]
    assert stack.bw.seeded == [f"{second}@demo.bwtidy.test"]


def test_failures_back_off_until_one_succeeds(tmp_path: Path):
    stack = Stack(tmp_path, ready=1, max_vaults=1)
    generated = ids(10)
    stack.bw.fail = {f"{i}@demo.bwtidy.test" for i in generated[:7]}
    pool = stack.pool()

    async def go():
        await running(pool)
        await pool.stop()

    asyncio.run(go())
    assert stack.slept == [10.0, 20.0, 40.0, 80.0, 160.0, 300.0, 300.0]
    assert pool.failed == 7
    assert stack.bw.seeded == [f"{generated[7]}@demo.bwtidy.test"]


def test_the_pool_refuses_a_ready_count_above_the_max(tmp_path: Path):
    with pytest.raises(ValueError):
        Stack(tmp_path, ready=3, max_vaults=2).pool()


@pytest.fixture
def stack(tmp_path: Path) -> Stack:
    return Stack(tmp_path, ready=2, max_vaults=3)


@pytest.fixture
def api(stack: Stack) -> Iterator[tuple[TestClient, Pool]]:
    pool = stack.pool()
    with TestClient(controller.create_app(pool, TOKEN)) as client:
        settle(client, pool)
        yield client, pool


def test_take_hands_out_a_ready_vault_once(api, stack: Stack):
    client, pool = api
    answer = client.post("/take", headers=auth())
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "no-store"
    vault = answer.json()
    assert set(vault) == {"server", "email", "password"}
    assert vault["server"] == SERVER
    assert vault["email"] in stack.bw.seeded
    assert vault["password"] in stack.bw.passwords
    other = client.post("/take", headers=auth()).json()
    assert other["email"] != vault["email"]
    settle(client, pool)
    assert client.get("/status", headers=auth()).json() == {
        "ready": 1,
        "taken": 2,
        "building": 0,
        "failed": 0,
    }


def test_take_answers_409_when_no_vault_is_ready(tmp_path: Path):
    stack = Stack(tmp_path, ready=0, max_vaults=1)
    pool = stack.pool()
    with TestClient(controller.create_app(pool, TOKEN)) as client:
        settle(client, pool)
        answer = client.post("/take", headers=auth())
    assert answer.status_code == 409
    assert answer.text == "No demo vault is ready. Try again in a minute."


def test_release_deletes_the_user_and_frees_its_place(api, stack: Stack):
    client, pool = api
    email = client.post("/take", headers=auth()).json()["email"]
    answer = client.post("/release", headers=auth(), json={"email": email})
    assert answer.status_code == 204
    assert stack.users.deleted == [email]
    assert email not in stack.users.users
    settle(client, pool)
    assert client.get("/status", headers=auth()).json()["taken"] == 0


def test_release_deletes_a_user_from_before_a_restart(api, stack: Stack):
    client, _ = api
    answer = client.post(
        "/release", headers=auth(), json={"email": "old-one-two-three@demo.bwtidy.test"}
    )
    assert answer.status_code == 204
    assert stack.users.deleted == ["old-one-two-three@demo.bwtidy.test"]


def test_release_refuses_a_user_outside_the_demo(api, stack: Stack):
    client, _ = api
    answer = client.post("/release", headers=auth(), json={"email": "someone@bwtidy.example"})
    assert answer.status_code == 400
    assert stack.users.deleted == []


def test_a_release_vaultwarden_refuses_keeps_the_vault_taken(api, stack: Stack):
    client, pool = api
    email = client.post("/take", headers=auth()).json()["email"]
    stack.users.fail_delete = True
    answer = client.post("/release", headers=auth(), json={"email": email})
    assert answer.status_code == 502
    assert pool.counts()["taken"] == 1
    stack.users.fail_delete = False
    assert client.post("/release", headers=auth(), json={"email": email}).status_code == 204
    assert pool.counts()["taken"] == 0


ROUTES = [
    ("POST", "/take"),
    ("POST", "/release"),
    ("GET", "/status"),
    ("GET", "/docs"),
    ("GET", "/openapi.json"),
    ("GET", "/unknown"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        auth("wrong-token"),
        auth(""),
        {"Authorization": TOKEN},
        {"Authorization": f"Basic {TOKEN}"},
        auth(TOKEN + "x"),
    ],
)
def test_every_route_requires_the_token(api, stack: Stack, method, path, headers):
    client, pool = api
    answer = client.request(method, path, headers=headers, json={"email": "x@demo.bwtidy.test"})
    assert answer.status_code == 401
    assert answer.headers["www-authenticate"] == "Bearer"
    assert pool.counts()["taken"] == 0
    assert stack.users.deleted == []


def test_the_api_has_no_docs_pages(api):
    client, _ = api
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, headers=auth()).status_code == 404


def test_status_names_no_id_or_secret(api, stack: Stack):
    client, _ = api
    vault = client.post("/take", headers=auth()).json()
    answer = client.get("/status", headers=auth())
    assert set(answer.json()) == {"ready", "taken", "building", "failed"}
    ledger = stack.ledger_path.read_text().splitlines()
    assert ledger
    for value in [*ledger, vault["password"], *stack.bw.passwords, TOKEN, "@"]:
        assert value not in answer.text


def test_no_log_line_names_a_password_or_token(tmp_path: Path, caplog):
    caplog.set_level(logging.DEBUG)
    stack = Stack(tmp_path, ready=2, max_vaults=3)
    generated = ids(10)
    stack.bw.fail = {f"{generated[0]}@demo.bwtidy.test"}
    stack.users.fail_register = {f"{generated[1]}@demo.bwtidy.test"}
    pool = stack.pool()
    with TestClient(controller.create_app(pool, TOKEN)) as client:
        settle(client, pool)
        vault = client.post("/take", headers=auth()).json()
        client.get("/status", headers=auth())
        client.get("/status", headers=auth("wrong-token"))
        stack.users.fail_delete = True
        client.post("/release", headers=auth(), json={"email": vault["email"]})
        stack.users.fail_delete = False
        client.post("/release", headers=auth(), json={"email": vault["email"]})
        settle(client, pool)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert generated[0] in text
    assert vault["email"] in text
    assert len(stack.bw.passwords) >= 4
    for secret in [*stack.bw.passwords, TOKEN]:
        assert secret not in text


FAKE_BW = """\
import json, os, sys
args = sys.argv[1:]
here = os.path.dirname(os.path.abspath(sys.argv[0]))
with open(os.path.join(here, "bw.log"), "a") as log:
    env = {k: os.environ.get(k) for k in (
        "BITWARDENCLI_APPDATA_DIR", "NODE_EXTRA_CA_CERTS", "DEMO_PASSWORD", "BW_SESSION",
        "BW_NOINTERACTION", "HOME", "FAKE_HOST_SECRET")}
    env["appdata_exists"] = os.path.isdir(os.path.dirname(env["BITWARDENCLI_APPDATA_DIR"]))
    log.write(json.dumps({"args": args, "env": env}) + "\\n")
fail_file = os.path.join(here, "fail")
fail = open(fail_file).read() if os.path.exists(fail_file) else None
if fail and args[0] == fail:
    sys.stderr.write("an error that names " + (os.environ.get("DEMO_PASSWORD") or "") + "\\n")
    sys.exit(3)
if args[0] == "generate":
    sys.stderr.write("noise without a server\\n")
    print("bold-ocean-quiet-river")
elif args[0] == "unlock":
    print("session-key-1")
"""


@pytest.fixture
def fake_bw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    script = tmp_path / "bw"
    script.write_text(f"#!{sys.executable}\n{FAKE_BW}")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "bw.log"
    monkeypatch.setenv("FAKE_HOST_SECRET", "from the host")
    return script, log


def calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def bw_cli(script: Path, tmp_path: Path) -> BwCli:
    return BwCli(tmp_path / "fixture.json", tmp_path / "ca.crt", bw=str(script))


def test_bw_generate_reads_stdout_only(fake_bw, tmp_path: Path):
    script, log = fake_bw
    assert asyncio.run(bw_cli(script, tmp_path).generate_id()) == "bold-ocean-quiet-river"
    (call,) = calls(log)
    assert call["args"] == ["generate", "--passphrase", "--words", "4", "--separator", "-"]
    assert call["env"]["appdata_exists"]
    assert not Path(call["env"]["HOME"]).exists()


def test_bw_seeds_with_the_password_in_the_environment_only(fake_bw, tmp_path: Path):
    script, log = fake_bw
    password = "pool-password-1"
    asyncio.run(bw_cli(script, tmp_path).seed(SERVER, "a-b-c-d@demo.bwtidy.test", password))
    made = calls(log)
    assert [call["args"] for call in made] == [
        ["config", "server", SERVER],
        ["login", "a-b-c-d@demo.bwtidy.test", "--passwordenv", "DEMO_PASSWORD"],
        ["unlock", "--passwordenv", "DEMO_PASSWORD", "--raw"],
        ["import", "bitwardenjson", str(tmp_path / "fixture.json")],
        ["sync"],
    ]
    for call in made:
        assert password not in call["args"]
        assert call["env"]["NODE_EXTRA_CA_CERTS"] == str(tmp_path / "ca.crt")
        assert call["env"]["BW_NOINTERACTION"] == "true"
        assert call["env"]["FAKE_HOST_SECRET"] is None
    assert [call["env"]["DEMO_PASSWORD"] for call in made[:3]] == [password] * 3
    assert [call["env"]["BW_SESSION"] for call in made[3:]] == ["session-key-1"] * 2
    assert made[3]["env"]["DEMO_PASSWORD"] is None
    homes = {call["env"]["HOME"] for call in made}
    assert len(homes) == 1
    assert not Path(homes.pop()).exists()


def test_a_failed_bw_step_names_the_step_and_no_output(fake_bw, tmp_path: Path):
    script, log = fake_bw
    (tmp_path / "fail").write_text("import")
    with pytest.raises(SeedError) as error:
        asyncio.run(bw_cli(script, tmp_path).seed(SERVER, "a-b-c-d@demo.bwtidy.test", "pw-1"))
    assert str(error.value) == "bw import failed with exit status 3"
    assert not Path(calls(log)[0]["env"]["HOME"]).exists()


def test_a_bw_step_that_hangs_times_out(tmp_path: Path):
    script = tmp_path / "bw"
    script.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    script.chmod(0o755)
    cli = BwCli(tmp_path / "fixture.json", tmp_path / "ca.crt", bw=str(script), timeout=0.2)
    with pytest.raises(SeedError, match="bw generate timed out"):
        asyncio.run(cli.generate_id())


def test_the_ledger_is_appended_and_synced(tmp_path: Path, monkeypatch):
    synced: list[int] = []
    real = os.fsync

    def fsync(fd: int) -> None:
        synced.append(fd)
        real(fd)

    monkeypatch.setattr(controller.os, "fsync", fsync)
    ledger = Ledger(tmp_path / "volume" / "ids")
    ledger.add("one-two-three-four")
    ledger.add("five-six-seven-eight")
    assert (tmp_path / "volume" / "ids").read_text() == "one-two-three-four\nfive-six-seven-eight\n"
    ## the file at each add, the directory once when the file is new
    assert len(synced) == 3
    assert "one-two-three-four" in Ledger(tmp_path / "volume" / "ids")
