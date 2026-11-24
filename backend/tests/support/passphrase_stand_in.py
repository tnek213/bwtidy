"""The fast stand-in for the age scrypt passphrase functions, which the unit tests run on."""

import hashlib
import hmac

import pyrage

## what a ciphertext of the stand-in starts with, never the start of a real age file
STAND_IN_MAGIC = b"bwtidy test stand-in for age scrypt\n"
STAND_IN_TAG_SIZE = 16


def stand_in_tag(plaintext: bytes, password: str) -> bytes:
    data = password.encode() + b"\0" + plaintext
    return hashlib.sha256(data).digest()[:STAND_IN_TAG_SIZE]


def stand_in_encrypt(plaintext: bytes, password: str) -> bytes:
    """Stands in for passphrase.encrypt: the magic, a short hash of password and plaintext, the
    plaintext. No scrypt, so no second per call."""
    return STAND_IN_MAGIC + stand_in_tag(plaintext, password) + plaintext


def stand_in_decrypt(ciphertext: bytes, password: str) -> bytes:
    """Stands in for passphrase.decrypt: pyrage.DecryptError for a wrong password, a file the
    stand-in did not write or damaged bytes, like the real one."""
    if not ciphertext.startswith(STAND_IN_MAGIC):
        raise pyrage.DecryptError("not a file of the stand-in")
    rest = ciphertext[len(STAND_IN_MAGIC) :]
    tag, plaintext = rest[:STAND_IN_TAG_SIZE], rest[STAND_IN_TAG_SIZE:]
    if not hmac.compare_digest(tag, stand_in_tag(plaintext, password)):
        raise pyrage.DecryptError("wrong password or damaged file")
    return plaintext


def stand_in_hash(password: str, salt: bytes) -> bytes:
    """Stands in for passphrase.hash_password: a plain sha256, no scrypt."""
    return hashlib.sha256(salt + b"\0" + password.encode()).digest()
