import asyncio
import base64
import hmac
import json
import os
import re
import secrets
import subprocess
from pathlib import Path

import httpx2 as httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from bwtidy_demo import vaultwarden
from bwtidy_demo.vaultwarden import AdminSession, LoginLimit, VaultwardenError

ROOT = Path(__file__).resolve().parents[2]
EMAIL = "User@Demo.bwtidy.test "
PASSWORD = "demo-password-1"
## computed with Node's crypto.pbkdf2Sync, not with this module: the master key of PASSWORD and
## the lowercased EMAIL, then the master password hash of that key
MASTER_KEY = "cSs+e8F9oANtSPdL1rG/+yJLRk5nIe3xrJWix42qwrQ="
MASTER_PASSWORD_HASH = "NvGKThkYqggjF5uduycOsfWDOVZOW7cyre9B3NPVHuY="
ENC_STRING = re.compile(r"^2\.([A-Za-z0-9+/]+=*)\|([A-Za-z0-9+/]+=*)\|([A-Za-z0-9+/]+=*)$")
ADMIN_TOKEN = "admin-token-1"


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def keys(private_key: rsa.RSAPrivateKey) -> vaultwarden.Keys:
    return vaultwarden.make_keys(
        EMAIL, PASSWORD, user_key=bytes(range(64)), private_key=private_key
    )


def test_the_master_key_and_hash_match_another_implementation():
    key = vaultwarden.master_key(PASSWORD, EMAIL)
    assert base64.b64encode(key).decode() == MASTER_KEY
    assert vaultwarden.master_password_hash(key, PASSWORD) == MASTER_PASSWORD_HASH


def test_the_registration_keys_carry_the_master_password_hash(keys: vaultwarden.Keys):
    assert keys.master_password_hash == MASTER_PASSWORD_HASH


def test_the_stretched_key_is_hkdf_expand_with_enc_then_mac():
    key = base64.b64decode(MASTER_KEY)
    stretched = vaultwarden.stretch(key)
    ## HKDF-Expand of one block is HMAC(key, info + 0x01)
    assert stretched[:32] == hmac.digest(key, b"enc\x01", "sha256")
    assert stretched[32:] == hmac.digest(key, b"mac\x01", "sha256")


def test_an_enc_string_is_type_2_with_iv_data_and_mac():
    key = bytes(range(64))
    enc = vaultwarden.encrypt(b"seventeen bytes!!", key, iv=bytes(16))
    match = ENC_STRING.match(enc)
    assert match
    iv, data, mac = (base64.b64decode(part) for part in match.groups())
    assert iv == bytes(16)
    assert len(data) == 32
    assert mac == hmac.digest(key[32:], iv + data, "sha256")
    assert vaultwarden.decrypt(enc, key) == b"seventeen bytes!!"


def test_a_fresh_iv_for_every_enc_string():
    key = bytes(range(64))
    assert vaultwarden.encrypt(b"x", key) != vaultwarden.encrypt(b"x", key)


def test_decrypt_refuses_a_wrong_mac():
    key = bytes(range(64))
    enc = vaultwarden.encrypt(b"secret", key)
    other = bytes(32) + bytes(range(32))
    with pytest.raises(ValueError, match="MAC"):
        vaultwarden.decrypt(enc, key[:32] + other[32:])


def test_the_user_key_decrypts_with_the_stretched_master_key(keys: vaultwarden.Keys):
    assert ENC_STRING.match(keys.encrypted_user_key)
    stretched = vaultwarden.stretch(base64.b64decode(MASTER_KEY))
    assert vaultwarden.decrypt(keys.encrypted_user_key, stretched) == bytes(range(64))


def test_the_private_key_decrypts_with_the_user_key_and_matches_the_public_key(
    keys: vaultwarden.Keys, private_key: rsa.RSAPrivateKey
):
    assert ENC_STRING.match(keys.encrypted_private_key)
    der = vaultwarden.decrypt(keys.encrypted_private_key, bytes(range(64)))
    loaded = serialization.load_der_private_key(der, password=None)
    assert isinstance(loaded, rsa.RSAPrivateKey)
    assert loaded.key_size == 2048
    public = loaded.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert base64.b64decode(keys.public_key) == public
    assert private_key.private_numbers() == loaded.private_numbers()


def test_fresh_keys_differ_each_time():
    one = vaultwarden.make_keys(EMAIL, PASSWORD, iterations=1)
    two = vaultwarden.make_keys(EMAIL, PASSWORD, iterations=1)
    assert one.master_password_hash == two.master_password_hash
    assert one.encrypted_user_key != two.encrypted_user_key
    assert one.public_key != two.public_key


def test_the_keys_repr_shows_no_value(keys: vaultwarden.Keys):
    assert keys.master_password_hash not in repr(keys)


class Recorder:
    """A mock transport that answers with handler and keeps every request."""

    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            request.read()
            self.requests.append(request)
            return self.handler(request)

        return httpx.MockTransport(handle)


def registration(token: str = "jwt-token", finish: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/send-verification-email"):
            return httpx.Response(200, text=token)
        return httpx.Response(finish, json={"object": "registerFinish"})

    return handler


def run_register(recorder: Recorder, monkeypatch) -> None:
    ## the real KDF and RSA key take a second, the bodies are what these tests check
    real = vaultwarden.make_keys
    monkeypatch.setattr(
        vaultwarden, "make_keys", lambda email, password: real(email, password, iterations=1)
    )

    async def go():
        async with vaultwarden.client(
            "https://vaultwarden", None, transport=recorder.transport()
        ) as http:
            await vaultwarden.register(http, EMAIL, PASSWORD)

    asyncio.run(go())


def test_register_sends_the_two_requests(monkeypatch):
    recorder = Recorder(registration())
    run_register(recorder, monkeypatch)

    verify, finish = recorder.requests
    assert verify.method == "POST"
    assert verify.url == "https://vaultwarden/identity/accounts/register/send-verification-email"
    assert json.loads(verify.content) == {"email": "user@demo.bwtidy.test"}
    assert verify.headers["accept"] == "text/plain"

    assert finish.method == "POST"
    assert finish.url == "https://vaultwarden/identity/accounts/register/finish"
    body = json.loads(finish.content)
    kdf = {"kdfType": 0, "iterations": 600_000}
    assert body["email"] == "user@demo.bwtidy.test"
    assert body["emailVerificationToken"] == "jwt-token"
    authentication = body["masterPasswordAuthentication"]
    assert authentication["salt"] == "user@demo.bwtidy.test"
    assert authentication["kdf"] == kdf
    key = vaultwarden.master_key(PASSWORD, EMAIL, 1)
    hashed = vaultwarden.master_password_hash(key, PASSWORD)
    assert authentication["masterPasswordAuthenticationHash"] == hashed
    unlock = body["masterPasswordUnlock"]
    assert unlock["salt"] == "user@demo.bwtidy.test"
    assert unlock["kdf"] == kdf
    user_key = vaultwarden.decrypt(unlock["masterKeyWrappedUserKey"], vaultwarden.stretch(key))
    assert len(user_key) == 64
    pair = body["userAsymmetricKeys"]
    der = vaultwarden.decrypt(pair["encryptedPrivateKey"], user_key)
    assert serialization.load_der_private_key(der, password=None)
    assert set(pair) == {"publicKey", "encryptedPrivateKey"}
    assert set(body) == {
        "email",
        "emailVerificationToken",
        "masterPasswordAuthentication",
        "masterPasswordUnlock",
        "userAsymmetricKeys",
    }


def test_a_refused_finish_raises_without_a_value(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/send-verification-email"):
            return httpx.Response(200, text="jwt-token")
        return httpx.Response(
            400, json={"message": "Registration not allowed or user already exists"}
        )

    with pytest.raises(VaultwardenError) as error:
        run_register(Recorder(handler), monkeypatch)
    assert str(error.value) == (
        "the registration failed with HTTP 400: Registration not allowed or user already exists"
    )
    assert PASSWORD not in str(error.value)


def test_a_verification_without_a_token_raises(monkeypatch):
    recorder = Recorder(lambda request: httpx.Response(204))
    with pytest.raises(VaultwardenError, match="verification failed with HTTP 204"):
        run_register(recorder, monkeypatch)
    assert len(recorder.requests) == 1


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class FakeAdmin:
    """Vaultwarden's admin API: logins hand out numbered cookies, refused ones answer 401."""

    def __init__(self) -> None:
        self.logins = 0
        self.valid: set[str] = set()
        self.users = {"user@demo.bwtidy.test": "a1b2c3d4-0000-4000-8000-000000000001"}
        self.deleted: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin" and request.method == "POST":
            assert request.headers["content-type"] == "application/x-www-form-urlencoded"
            if request.content != f"token={ADMIN_TOKEN}".encode():
                return httpx.Response(401, text="login page")
            self.logins += 1
            cookie = f"jwt-{self.logins}"
            self.valid = {cookie}
            header = f"VW_ADMIN={cookie}; HttpOnly; SameSite=Strict; Path=/admin; Max-Age=1200"
            return httpx.Response(200, text="admin page", headers={"set-cookie": header})
        cookie = request.headers.get("cookie", "").removeprefix("VW_ADMIN=")
        if cookie not in self.valid:
            return httpx.Response(401, text="login page")
        prefix = "/admin/users/by-mail/"
        if request.method == "GET" and request.url.path.startswith(prefix):
            uuid = self.users.get(request.url.path.removeprefix(prefix))
            if uuid is None:
                return httpx.Response(404, json={"message": "User doesn't exist"})
            return httpx.Response(200, json={"id": uuid, "object": "profile"})
        match = re.fullmatch(r"/admin/users/([^/]+)/delete", request.url.path)
        if request.method == "POST" and match:
            assert request.headers["content-type"] == "application/json"
            uuid = match.group(1)
            for email, known in list(self.users.items()):
                if known == uuid:
                    del self.users[email]
                    self.deleted.append(uuid)
                    return httpx.Response(200)
            return httpx.Response(404, json={"message": "User doesn't exist"})
        return httpx.Response(404)


def admin(
    fake: FakeAdmin, clock: FakeClock, token: str = ADMIN_TOKEN
) -> tuple[AdminSession, Recorder]:
    recorder = Recorder(fake)
    session = AdminSession(
        "https://vaultwarden",
        token,
        transport=recorder.transport(),
        clock=clock,
        sleep=clock.sleep,
    )
    return session, recorder


def logins(recorder: Recorder) -> int:
    return sum(1 for r in recorder.requests if r.url.path == "/admin" and r.method == "POST")


def test_the_admin_logs_in_once_and_reuses_the_cookie():
    fake, clock = FakeAdmin(), FakeClock()
    session, recorder = admin(fake, clock)

    async def go():
        async with session:
            assert (
                await session.user_id("user@demo.bwtidy.test")
                == fake.users["user@demo.bwtidy.test"]
            )
            assert await session.user_id("other@demo.bwtidy.test") is None
            assert await session.user_id("user@demo.bwtidy.test")

    asyncio.run(go())
    assert logins(recorder) == 1
    sent = [r.headers["cookie"] for r in recorder.requests if r.url.path != "/admin"]
    assert sent == ["VW_ADMIN=jwt-1"] * 3


def test_a_401_leads_to_one_new_login():
    fake, clock = FakeAdmin(), FakeClock()
    session, recorder = admin(fake, clock)

    async def go():
        async with session:
            await session.user_id("user@demo.bwtidy.test")
            ## Vaultwarden restarted with a new key: the old cookie is refused
            fake.valid = set()
            assert await session.user_id("user@demo.bwtidy.test")

    asyncio.run(go())
    assert logins(recorder) == 2
    last = [r.headers.get("cookie") for r in recorder.requests if r.url.path != "/admin"]
    assert last == ["VW_ADMIN=jwt-1", "VW_ADMIN=jwt-1", "VW_ADMIN=jwt-2"]


def test_a_cookie_refused_after_a_new_login_raises():
    fake, clock = FakeAdmin(), FakeClock()
    session, recorder = admin(fake, clock)
    real = fake.__call__

    def refuse_everything_but_login(request: httpx.Request) -> httpx.Response:
        response = real(request)
        if request.url.path != "/admin":
            return httpx.Response(401, text="login page")
        return response

    recorder.handler = refuse_everything_but_login

    async def go():
        async with session:
            await session.user_id("user@demo.bwtidy.test")

    with pytest.raises(VaultwardenError, match="finding a user failed with HTTP 401"):
        asyncio.run(go())
    assert logins(recorder) == 2


def test_an_expired_cookie_logs_in_again_before_vaultwarden_refuses_it():
    fake, clock = FakeAdmin(), FakeClock()
    session, recorder = admin(fake, clock)

    async def go():
        async with session:
            await session.user_id("user@demo.bwtidy.test")
            clock.now += 1200 - vaultwarden.ADMIN_SESSION_MARGIN - 1
            await session.user_id("user@demo.bwtidy.test")
            assert logins(recorder) == 1
            clock.now += 1
            await session.user_id("user@demo.bwtidy.test")

    asyncio.run(go())
    assert logins(recorder) == 2


def test_a_wrong_admin_token_raises_without_the_token():
    fake, clock = FakeAdmin(), FakeClock()
    session, _ = admin(fake, clock, token="wrong-token-1")

    async def go():
        async with session:
            await session.user_id("user@demo.bwtidy.test")

    with pytest.raises(VaultwardenError) as error:
        asyncio.run(go())
    assert str(error.value) == "the admin login failed with HTTP 401"


def test_logins_wait_for_the_rate_limit():
    fake, clock = FakeAdmin(), FakeClock()
    session, recorder = admin(fake, clock)

    async def go():
        async with session:
            for _ in range(5):
                fake.valid = set()
                await session.user_id("user@demo.bwtidy.test")

    asyncio.run(go())
    ## one login per refused cookie: the burst of 3, then one per 300 seconds
    assert logins(recorder) == 5
    assert clock.slept == [300.0, 300.0]


def test_the_login_limit_refills_one_per_period():
    clock = FakeClock()
    limit = LoginLimit(clock=clock)
    for _ in range(3):
        assert limit.wait() == 0
        limit.take()
    assert limit.wait() == 300
    clock.now += 100
    assert limit.wait() == pytest.approx(200)
    clock.now += 200
    assert limit.wait() == 0


def test_delete_finds_the_user_then_deletes_it():
    fake, clock = FakeAdmin(), FakeClock()
    session, recorder = admin(fake, clock)
    uuid = fake.users["user@demo.bwtidy.test"]

    async def go():
        async with session:
            assert await session.delete_user("user@demo.bwtidy.test") is True

    asyncio.run(go())
    calls = [(r.method, r.url.path) for r in recorder.requests]
    assert calls == [
        ("POST", "/admin"),
        ("GET", "/admin/users/by-mail/user@demo.bwtidy.test"),
        ("POST", f"/admin/users/{uuid}/delete"),
    ]
    assert fake.deleted == [uuid]


def test_delete_of_a_user_not_there_counts_as_deleted():
    fake, clock = FakeAdmin(), FakeClock()
    session, recorder = admin(fake, clock)

    async def go():
        async with session:
            assert await session.delete_user("gone@demo.bwtidy.test") is False

    asyncio.run(go())
    assert [r.url.path for r in recorder.requests][-1].startswith("/admin/users/by-mail/")


def dev_stack() -> tuple[str, Path]:
    """The URL and the certificate of the dev stack's Vaultwarden that BWTIDY_STACK picks."""
    stack = int(os.environ.get("BWTIDY_STACK", "").strip() or "1")
    suffix = "" if stack == 1 else f"-{stack}"
    env_file = ROOT / f".dev-vault{suffix}.env"
    if not env_file.exists():
        pytest.fail(f"{env_file.name} is missing, run scripts/dev-vault up")
    env = dict(line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line)
    cert = ROOT / env.get("BWTIDY_TLS_DIR", f".dev-vault{suffix}") / "cert.pem"
    return f"https://localhost:{env['BWTIDY_VW_PORT']}", cert


## bw logs in with its own state directory in the app container, never the dev account's.
## The password comes on stdin and the session key never leaves the container.
BW_LOGIN = r"""
read -r BW_PASSWORD
export BW_PASSWORD
BITWARDENCLI_APPDATA_DIR=$(mktemp -d)
export BITWARDENCLI_APPDATA_DIR
trap 'rm -rf "$BITWARDENCLI_APPDATA_DIR"' EXIT
bw config server https://vaultwarden >/dev/null || exit 1
session=$(bw login "$1" --passwordenv BW_PASSWORD --raw) || exit 1
bw list items --session "$session" || exit 1
bw logout >/dev/null
"""


@pytest.mark.skipif(
    os.environ.get("BWTIDY_DEV_STACK") != "1",
    reason="needs the dev vault stack, see dev/demo.md (Testing)",
)
def test_a_registered_user_logs_in_with_bw():
    url, cert = dev_stack()
    email = f"register-{secrets.token_hex(6)}@demo.bwtidy.test"
    password = secrets.token_urlsafe(24)

    async def go():
        async with vaultwarden.client(url, cert) as http:
            await vaultwarden.register(http, email, password)

    asyncio.run(go())
    result = subprocess.run(
        [str(ROOT / "scripts" / "dev-vault"), "exec", "sh", "-c", BW_LOGIN, "sh", email],
        cwd=ROOT,
        input=password + "\n",
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert json.loads(result.stdout) == []
