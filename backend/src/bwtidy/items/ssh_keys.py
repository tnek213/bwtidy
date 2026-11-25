"""The public key and fingerprint of an SSH key item, derived from its private key.

bw 2026.10.0 refuses an SSH key item (type 5) without all three of sshKey.privateKey,
sshKey.publicKey and sshKey.keyFingerprint, and fills in none of them itself. MCP never
gives a private key, so a key that comes in through a conversion or the grid's secret dialog
gets the other two here, at write time (docs/spec.md, Writes).

Nothing here puts a character of a key into an error message or a log line.
"""

import base64
import hashlib
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

SSH_KEY_TYPE = 5
UNREADABLE = "the private key cannot be read"
PASSPHRASE = "the private key has a passphrase"
## the columns derive fills, in the order the grid shows them
DERIVED = ("sshKey.publicKey", "sshKey.keyFingerprint")

_OPENSSH_HEADER = b"-----BEGIN OPENSSH PRIVATE KEY-----"


def _load(private_key: str) -> Any:
    """The key object of an OpenSSH or PEM (PKCS#1, PKCS#8, SEC1) private key without a
    passphrase. Raises ValueError with a fixed text."""
    data = private_key.replace("\r\n", "\n").strip().encode() + b"\n"
    load = (
        serialization.load_ssh_private_key
        if data.startswith(_OPENSSH_HEADER)
        else serialization.load_pem_private_key
    )
    try:
        return load(data, None)
    except TypeError:
        ## cryptography's answer to an encrypted key loaded without a password
        raise ValueError(PASSPHRASE) from None
    except Exception:
        raise ValueError(UNREADABLE) from None


def derive(private_key: str) -> tuple[str, str]:
    """The OpenSSH public key line (no comment) and the fingerprint (`SHA256:` and the unpadded
    base64 of the SHA-256 of the public key blob, as `ssh-keygen -lf` shows it) of an
    Ed25519, RSA or ECDSA private key.

    Raises ValueError("the private key has a passphrase") for an encrypted key and
    ValueError("the private key cannot be read") for anything else that is no such key.
    """
    key = _load(private_key)
    if not isinstance(
        key, ed25519.Ed25519PrivateKey | rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey
    ):
        raise ValueError(UNREADABLE)
    try:
        public = key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
    except Exception:
        ## an elliptic curve OpenSSH has no name for
        raise ValueError(UNREADABLE) from None
    line = public.decode()
    blob = base64.b64decode(line.split()[1])
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return line, f"SHA256:{digest}"


def _blank(value: Any) -> bool:
    return not isinstance(value, str) or not value.strip()


def fill_in(item: dict[str, Any]) -> list[str]:
    """Fill an SSH key item's empty sshKey.publicKey and sshKey.keyFingerprint from its
    sshKey.privateKey, in place. Answers the columns it filled.

    Only for type 5 with a private key and one of the two empty. A value already there is
    kept, even one that does not match the key. Raises ValueError (see derive) when the
    private key cannot give them, with the item unchanged.
    """
    ssh = item.get("sshKey")
    if item.get("type") != SSH_KEY_TYPE or not isinstance(ssh, dict):
        return []
    private_key = ssh.get("privateKey")
    if _blank(private_key):
        return []
    keys = ("publicKey", "keyFingerprint")
    empty = [key for key in keys if _blank(ssh.get(key))]
    if not empty:
        return []
    derived = dict(zip(keys, derive(private_key), strict=True))
    for key in empty:
        ssh[key] = derived[key]
    return [column for key, column in zip(keys, DERIVED, strict=True) if key in empty]
