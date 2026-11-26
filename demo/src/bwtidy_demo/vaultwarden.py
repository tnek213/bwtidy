"""The demo's Vaultwarden: register a user the way a Bitwarden client does, delete it as admin.

Registration (Vaultwarden 1.37.4, src/api/identity.rs and src/api/core/accounts.rs):

- `POST /identity/accounts/register/send-verification-email` takes `{email, name}`
  (`RegisterVerificationData`). Without SMTP it answers the token itself, as plain text unless
  the request accepts JSON (`register_verification_email`).
- `POST /identity/accounts/register/finish` takes `RegisterData`. Of its two formats this sends
  the current one (`RegisterDataCur`), the body of the Bitwarden clients' `RegisterFinishRequest`
  (libs/common/src/auth/models/request/registration/register-finish.request.ts):
  `email`, `emailVerificationToken`, `masterPasswordAuthentication` {`salt`, `kdf`,
  `masterPasswordAuthenticationHash`}, `masterPasswordUnlock` {`salt`, `kdf`,
  `masterKeyWrappedUserKey`} and `userAsymmetricKeys` {`publicKey`, `encryptedPrivateKey`}
  (`KeysRequest` in libs/common/src/models/request/keys.request.ts). `kdf` is
  `{kdfType: 0, iterations}`, the JSON of the clients' `PBKDF2KdfConfig`
  (libs/legacy-crypto/src/models/kdf-config.ts), which Vaultwarden's `KDFData` takes by
  alias. Vaultwarden answers 422 unless both salts are the email trimmed and lowercased and
  both KDFs are the same (`RegisterData::unprocessable`), and it compares the token's subject
  with `email` as it is, so the email goes lowercased everywhere. The answer is 200 with
  `{"object": "registerFinish"}`. The `salt`, `kdf` and `masterKeyWrappedUserKey` names are
  the clients' `MasterPasswordAuthenticationData` and `MasterPasswordUnlockData`
  (libs/common/src/key-management/master-password/types/master-password.types.ts).

Both registration requests count against Vaultwarden's limit for unauthenticated requests
(`UNAUTHENTICATED_RATELIMIT_MAX_BURST` 50, then one per `UNAUTHENTICATED_RATELIMIT_SECONDS` 60,
per client address), as does the prelogin of a bw login.

The crypto, as the Bitwarden clients at tag cli-v2026.10.0 (the bw of the image) do it, in
libs/legacy-crypto/src/services/legacy-compat-key.service.ts (L) and
libs/legacy-crypto/src/services/default-key-generation.service.ts (D):

- master key: PBKDF2-HMAC-SHA256 of the password, salt the email trimmed and lowercased,
  600000 iterations (the default of `PBKDF2KdfConfig.ITERATIONS` in
  libs/legacy-crypto/src/models/kdf-config.ts), 32 bytes (`makeMasterKey`, L).
- master password hash: base64 of PBKDF2-HMAC-SHA256 with the master key as the password and
  the password as the salt, 1 iteration (`hashMasterKey`, L). Vaultwarden hashes it again.
- stretched master key: HKDF-Expand-SHA256 of the master key with info `enc` and with info
  `mac`, 32 bytes each, the encryption key first (`stretchKey`, D).
- user key: 64 random bytes, an AES-256 key then an HMAC-SHA256 key
  (`PureCrypto.make_aes256_cbc_hmac_key` in `makeUserKey`, L), wrapped with the stretched
  master key (`buildProtectedSymmetricKey`, L).
- key pair: RSA-2048, the public key base64 SPKI DER, the private key PKCS#8 DER wrapped with
  the user key (`makeKeyPair`, L, with `rsaGenerateKeyPair` of
  libs/legacy-crypto/src/node/node-crypto-function.service.ts).
- wrapping: an EncString of type 2 (`AesCbc256_HmacSha256_B64`), `2.IV|DATA|MAC` with each
  part base64, DATA AES-256-CBC with PKCS#7 padding, MAC HMAC-SHA256 over IV then DATA.

The admin API (src/api/admin.rs): `POST /admin` with the form field `token` sets the
`VW_ADMIN` cookie, valid for `ADMIN_SESSION_LIFETIME` minutes (20 by default) and answers 200
(401 for a wrong token, 429 past the rate limit). The login allows a burst of 3, then one per
300 seconds (`ADMIN_RATELIMIT_MAX_BURST` and `ADMIN_RATELIMIT_SECONDS`, a GCRA in
src/ratelimit.rs). A request with an expired or missing cookie gets 401.
`GET /admin/users/by-mail/EMAIL` answers the user's JSON (`id` its UUID) or 404,
`POST /admin/users/UUID/delete` needs `Content-Type: application/json`.

No password, hash, key, token or cookie reaches a log line or an error message.
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import secrets
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import quote

import httpx2 as httpx
from cryptography.hazmat.primitives import hashes, padding, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

log = logging.getLogger(__name__)

KDF_ITERATIONS = 600_000
## the clients' KdfType.PBKDF2_SHA256
KDF_PBKDF2 = 0
## the finish takes a while: Vaultwarden hashes the master password hash again with 600000
## iterations of its own
TIMEOUT = httpx.Timeout(30.0, connect=5.0)

ADMIN_COOKIE = "VW_ADMIN"
ADMIN_SESSION_LIFETIME = 20 * 60
## the cookie counts as expired this long before Vaultwarden would refuse it
ADMIN_SESSION_MARGIN = 60
ADMIN_LOGIN_BURST = 3
ADMIN_LOGIN_PERIOD = 300.0


class VaultwardenError(Exception):
    """Vaultwarden refused a request. The text names the step and the status, never a secret."""


def client(
    base_url: str, ca_file: str | Path | None, *, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """A client for the stack's Vaultwarden, trusting ca_file (its self-signed certificate)."""
    verify: ssl.SSLContext | bool = True
    if ca_file is not None:
        verify = ssl.create_default_context(cafile=str(ca_file))
    return httpx.AsyncClient(
        base_url=base_url, verify=verify, timeout=TIMEOUT, transport=transport, trust_env=False
    )


def salt(email: str) -> str:
    return email.strip().lower()


def master_key(password: str, email: str, iterations: int = KDF_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt(email).encode(), iterations, 32)


def master_password_hash(key: bytes, password: str) -> str:
    return base64.b64encode(hashlib.pbkdf2_hmac("sha256", key, password.encode(), 1, 32)).decode()


def stretch(key: bytes) -> bytes:
    """The 64 byte stretched master key: the encryption key, then the MAC key."""

    def expand(info: bytes) -> bytes:
        return HKDFExpand(algorithm=hashes.SHA256(), length=32, info=info).derive(key)

    return expand(b"enc") + expand(b"mac")


def encrypt(plain: bytes, key: bytes, iv: bytes | None = None) -> str:
    """An EncString of type 2 of plain under the 64 byte key (encryption key, then MAC key)."""
    iv = os.urandom(16) if iv is None else iv
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key[:32]), modes.CBC(iv)).encryptor()
    data = encryptor.update(padded) + encryptor.finalize()
    mac = hmac.digest(key[32:], iv + data, "sha256")
    return "2." + "|".join(base64.b64encode(part).decode() for part in (iv, data, mac))


def decrypt(enc: str, key: bytes) -> bytes:
    kind, _, parts = enc.partition(".")
    if kind != "2":
        raise ValueError("not an EncString of type 2")
    iv, data, mac = (base64.b64decode(part) for part in parts.split("|"))
    if not hmac.compare_digest(mac, hmac.digest(key[32:], iv + data, "sha256")):
        raise ValueError("the EncString's MAC does not match")
    decryptor = Cipher(algorithms.AES(key[:32]), modes.CBC(iv)).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


@dataclass(frozen=True, repr=False)
class Keys:
    """What a client sends on registration. repr=False keeps the values out of any repr."""

    master_password_hash: str
    encrypted_user_key: str
    public_key: str
    encrypted_private_key: str


def make_keys(
    email: str,
    password: str,
    *,
    user_key: bytes | None = None,
    private_key: rsa.RSAPrivateKey | None = None,
    iterations: int = KDF_ITERATIONS,
) -> Keys:
    """The registration's keys. user_key and private_key are fresh unless given (for tests)."""
    key = master_key(password, email, iterations)
    user_key = secrets.token_bytes(64) if user_key is None else user_key
    if private_key is None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    private_der = private_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return Keys(
        master_password_hash=master_password_hash(key, password),
        encrypted_user_key=encrypt(user_key, stretch(key)),
        public_key=base64.b64encode(public_der).decode(),
        encrypted_private_key=encrypt(private_der, user_key),
    )


def finish_body(email: str, token: str, keys: Keys, iterations: int = KDF_ITERATIONS) -> dict:
    kdf = {"kdfType": KDF_PBKDF2, "iterations": iterations}
    return {
        "email": salt(email),
        "emailVerificationToken": token,
        "masterPasswordAuthentication": {
            "salt": salt(email),
            "kdf": kdf,
            "masterPasswordAuthenticationHash": keys.master_password_hash,
        },
        "masterPasswordUnlock": {
            "salt": salt(email),
            "kdf": kdf,
            "masterKeyWrappedUserKey": keys.encrypted_user_key,
        },
        "userAsymmetricKeys": {
            "publicKey": keys.public_key,
            "encryptedPrivateKey": keys.encrypted_private_key,
        },
    }


def refused(step: str, response: httpx.Response) -> VaultwardenError:
    """The error for a refused step, with Vaultwarden's own message, which names no value.

    Vaultwarden's errors are fixed texts. A body it could not parse gets Rocket's catcher page,
    never the parser's message (which could quote a value of the body).
    """
    message = ""
    try:
        body = response.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        message = ": " + body["message"][:200]
    return VaultwardenError(f"{step} failed with HTTP {response.status_code}{message}")


async def register(
    http: httpx.AsyncClient, email: str, password: str, *, name: str | None = None
) -> None:
    """Register email with password on the Vaultwarden of http, with the client-side crypto.

    The KDF and the RSA key take about a second, so they run in a thread.
    """
    email = salt(email)
    keys = await asyncio.to_thread(make_keys, email, password)
    response = await http.post(
        "/identity/accounts/register/send-verification-email",
        json={"email": email} if name is None else {"email": email, "name": name},
        headers={"Accept": "text/plain"},
    )
    if response.status_code != 200 or not response.text:
        raise refused("the registration's verification", response)
    response = await http.post(
        "/identity/accounts/register/finish", json=finish_body(email, response.text.strip(), keys)
    )
    if response.status_code != 200:
        raise refused("the registration", response)
    log.info("registered %s", email)


class LoginLimit:
    """Vaultwarden's admin login limit: a burst of ADMIN_LOGIN_BURST, then one per period."""

    def __init__(
        self,
        burst: int = ADMIN_LOGIN_BURST,
        period: float = ADMIN_LOGIN_PERIOD,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.burst = burst
        self.period = period
        self.clock = clock
        self.tokens = float(burst)
        self.at = clock()

    def wait(self) -> float:
        """Seconds until a login is allowed, 0 when one is allowed now."""
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.at) / self.period)
        self.at = now
        if self.tokens >= 1:
            return 0.0
        return (1 - self.tokens) * self.period

    def take(self) -> None:
        self.tokens -= 1


class AdminSession:
    """Vaultwarden's admin API with one login reused until it expires or is refused.

    A login waits (with sleep) while the limit allows none, so it never gets 429 from a
    Vaultwarden that only this session logs in to.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        ca_file: str | Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = client(base_url, ca_file, transport=transport)
        self._token = token
        self._clock = clock
        self._sleep = sleep
        self._limit = LoginLimit(clock=clock)
        self._lock = asyncio.Lock()
        self._cookie: str | None = None
        self._expires = 0.0

    async def __aenter__(self) -> "AdminSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _login(self) -> str:
        while (delay := self._limit.wait()) > 0:
            log.info("admin login waits %.0f seconds for Vaultwarden's rate limit", delay)
            await self._sleep(delay)
        self._limit.take()
        response = await self._http.post("/admin", data={"token": self._token})
        self._http.cookies.clear()
        cookie = SimpleCookie()
        for header in response.headers.get_list("set-cookie"):
            cookie.load(header)
        morsel = cookie.get(ADMIN_COOKIE)
        if response.status_code != 200 or morsel is None or not morsel.value:
            raise VaultwardenError(f"the admin login failed with HTTP {response.status_code}")
        lifetime = int(morsel["max-age"]) if morsel["max-age"] else ADMIN_SESSION_LIFETIME
        self._cookie = morsel.value
        self._expires = self._clock() + lifetime - ADMIN_SESSION_MARGIN
        log.info("logged in to Vaultwarden's admin API")
        return morsel.value

    async def _session(self, refused: str | None = None) -> str:
        """The cookie, after a login when there is none, it expired or it is the refused one."""
        async with self._lock:
            if self._cookie is not None and self._cookie == refused:
                self._cookie = None
            if self._cookie is None or self._clock() >= self._expires:
                return await self._login()
            return self._cookie

    async def _request(
        self, method: str, path: str, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        cookie = await self._session()
        for retry in (True, False):
            response = await self._http.request(
                method, path, headers={"Cookie": f"{ADMIN_COOKIE}={cookie}", **(headers or {})}
            )
            self._http.cookies.clear()
            if response.status_code != 401 or not retry:
                return response
            cookie = await self._session(refused=cookie)
        return response

    async def user_id(self, email: str) -> str | None:
        """The UUID of the user with email, None when there is none."""
        response = await self._request("GET", f"/admin/users/by-mail/{quote(email, safe='')}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise refused("finding a user", response)
        return response.json()["id"]

    async def delete_user(self, email: str) -> bool:
        """Delete the user with email, False when there was none (a user not there is deleted)."""
        uuid = await self.user_id(email)
        if uuid is None:
            return False
        response = await self._request(
            "POST",
            f"/admin/users/{quote(uuid, safe='')}/delete",
            {"Content-Type": "application/json"},
        )
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise refused("deleting a user", response)
        log.info("deleted the Vaultwarden user %s", email)
        return True
