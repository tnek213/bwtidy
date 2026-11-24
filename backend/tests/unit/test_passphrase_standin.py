"""The scrypt stand-in of conftest.py behaves like age where the unit tests rely on it."""

import pyrage
import pytest

from bwtidy import passphrase
from tests.support.passphrase_stand_in import (
    STAND_IN_MAGIC,
    stand_in_decrypt,
    stand_in_encrypt,
    stand_in_hash,
)

PASSWORD = "correct horse battery staple"
DATA = b"PK\x03\x04 a backup or an identity " * 10
AGE_HEADER = b"age-encryption.org/v1\n-> scrypt "


def test_unit_tests_get_the_stand_in() -> None:
    assert passphrase.encrypt is stand_in_encrypt
    assert passphrase.decrypt is stand_in_decrypt
    assert passphrase.hash_password is stand_in_hash


def test_the_right_password_returns_the_plaintext() -> None:
    ciphertext = passphrase.encrypt(DATA, PASSWORD)
    assert ciphertext.startswith(STAND_IN_MAGIC)
    assert passphrase.decrypt(ciphertext, PASSWORD) == DATA
    assert passphrase.decrypt(passphrase.encrypt(b"", PASSWORD), PASSWORD) == b""


def test_a_wrong_password_raises_decrypt_error() -> None:
    ciphertext = passphrase.encrypt(DATA, PASSWORD)
    with pytest.raises(pyrage.DecryptError):
        passphrase.decrypt(ciphertext, "not the password")
    with pytest.raises(pyrage.DecryptError):
        passphrase.decrypt(ciphertext, "")


@pytest.mark.parametrize("position", [0, len(STAND_IN_MAGIC), len(STAND_IN_MAGIC) + 20, -1])
def test_a_damaged_ciphertext_raises_decrypt_error(position: int) -> None:
    damaged = bytearray(passphrase.encrypt(DATA, PASSWORD))
    damaged[position] ^= 0xFF
    with pytest.raises(pyrage.DecryptError):
        passphrase.decrypt(bytes(damaged), PASSWORD)


def test_a_cut_or_foreign_ciphertext_raises_decrypt_error() -> None:
    ciphertext = passphrase.encrypt(DATA, PASSWORD)
    for broken in (ciphertext[:-1], ciphertext[: len(STAND_IN_MAGIC) + 3], b"", DATA):
        with pytest.raises(pyrage.DecryptError):
            passphrase.decrypt(broken, PASSWORD)


@pytest.mark.real_scrypt
def test_a_real_scrypt_test_sees_pyrage() -> None:
    assert passphrase.encrypt is not stand_in_encrypt
    assert passphrase.decrypt is not stand_in_decrypt
    ciphertext = passphrase.encrypt(DATA, PASSWORD)
    assert ciphertext.startswith(AGE_HEADER)
    assert pyrage.passphrase.decrypt(ciphertext, PASSWORD) == DATA


@pytest.mark.real_scrypt
def test_the_real_password_hash_depends_on_password_and_salt() -> None:
    assert passphrase.hash_password is not stand_in_hash
    salt = b"0123456789abcdef"
    digest = passphrase.hash_password(PASSWORD, salt)
    assert len(digest) == 32
    assert passphrase.hash_password(PASSWORD, salt) == digest
    assert passphrase.hash_password("not the password", salt) != digest
    assert passphrase.hash_password(PASSWORD, b"fedcba9876543210") != digest
