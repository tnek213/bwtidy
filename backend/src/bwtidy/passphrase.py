"""Age passphrase encryption (scrypt) and the password hash, the one place bwtidy calls them.

The backups and the stored identity for queued hidden values are encrypted with the master
password through encrypt and decrypt. rage picks the scrypt work factor so that one pass takes
about a second, which is why the unit tests replace them with a fast stand-in
(tests/conftest.py) and only the tests marked real_scrypt run the real ones. hash_password is
what a new login is checked against while a session runs (session.py), the stand-in replaces it
too.
"""

import hashlib

import pyrage

## OWASP's scrypt minimum (128 MiB, about 0.2 s on a laptop). hashlib's scrypt lets go of the
## GIL, unlike pyrage, so it runs in a thread without stalling the event loop.
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 256 * 1024 * 1024


def encrypt(plaintext: bytes, password: str) -> bytes:
    """The age file of plaintext for the password. Raises pyrage.EncryptError."""
    return pyrage.passphrase.encrypt(plaintext, password)


def decrypt(ciphertext: bytes, password: str) -> bytes:
    """The plaintext of an age file. Raises pyrage.DecryptError on a wrong password or damage."""
    return pyrage.passphrase.decrypt(ciphertext, password)


def hash_password(password: str, salt: bytes) -> bytes:
    """The scrypt hash of the password with the salt, 32 bytes."""
    return hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
        dklen=32,
    )
