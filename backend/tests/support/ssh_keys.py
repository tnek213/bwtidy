"""SSH keys of every kind for the tests that convert or fill in a key."""

import base64
import os
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

## PKCS1 is PKCS#1 for RSA and SEC1 for ECDSA, both "traditional" PEM
FORMS = {
    "openssh": serialization.PrivateFormat.OpenSSH,
    "pkcs8": serialization.PrivateFormat.PKCS8,
    "pkcs1": serialization.PrivateFormat.TraditionalOpenSSL,
}
OPENSSH, PKCS8, PKCS1 = FORMS.values()


def new_key(kind: str) -> Any:
    if kind == "ed25519":
        return ed25519.Ed25519PrivateKey.generate()
    if kind == "rsa":
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return ec.generate_private_key(ec.SECP256R1())


def private_text(key: Any, form: serialization.PrivateFormat, password: bytes | None = None) -> str:
    if password is not None and form == OPENSSH:
        return encrypted_openssh(private_text(key, form))
    encryption = (
        serialization.NoEncryption()
        if password is None
        else serialization.BestAvailableEncryption(password)
    )
    return key.private_bytes(serialization.Encoding.PEM, form, encryption).decode()


def _ssh_string(data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + data


def _take(data: bytes) -> tuple[bytes, bytes]:
    length = int.from_bytes(data[:4], "big")
    return data[4 : 4 + length], data[4 + length :]


def encrypted_openssh(text: str) -> str:
    """An OpenSSH key in the form a passphrase gives it (aes256-ctr, bcrypt), its private part
    padded but not encrypted, which a load without a passphrase never reaches.

    cryptography needs the bcrypt module to write an encrypted OpenSSH key, but not to refuse
    one that comes without a passphrase.
    """
    body = base64.b64decode("".join(line for line in text.splitlines() if "-----" not in line))
    magic = b"openssh-key-v1\0"
    rest = body[len(magic) :]
    for _ in range(3):
        _, rest = _take(rest)
    count, rest = rest[:4], rest[4:]
    public, rest = _take(rest)
    private, _ = _take(rest)
    private += bytes(range(1, 1 + (-len(private)) % 16))
    options = _ssh_string(os.urandom(16)) + (16).to_bytes(4, "big")
    blob = b"".join(
        [
            magic,
            _ssh_string(b"aes256-ctr"),
            _ssh_string(b"bcrypt"),
            _ssh_string(options),
            count,
            _ssh_string(public),
            _ssh_string(private),
        ]
    )
    encoded = base64.b64encode(blob).decode()
    lines = [encoded[i : i + 70] for i in range(0, len(encoded), 70)]
    return "\n".join(
        ["-----BEGIN OPENSSH PRIVATE KEY-----", *lines, "-----END OPENSSH PRIVATE KEY-----", ""]
    )


def ed25519_private_key() -> str:
    """A fresh unencrypted Ed25519 key in OpenSSH form, for the other test files."""
    return private_text(new_key("ed25519"), OPENSSH)


def passphrase_private_key() -> str:
    return private_text(new_key("ed25519"), OPENSSH, b"a test passphrase")
