"""ssh_keys.derive and fill_in on keys made here with cryptography, never a real one.

The expected public key and fingerprint are computed from cryptography's own public key
bytes, not by derive. No assert prints a key: each comparison goes into a bool first.
"""

import base64
import hashlib
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa

from bwtidy.items.ssh_keys import PASSPHRASE, UNREADABLE, derive, fill_in
from tests.support.ssh_keys import (
    FORMS,
    OPENSSH,
    PKCS8,
    ed25519_private_key,
    new_key,
    passphrase_private_key,
    private_text,
)


def expected(key: Any) -> tuple[str, str]:
    """The public key line and fingerprint from the public key's own bytes."""
    public = key.public_key()
    line = public.public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode()
    blob = base64.b64decode(line.split(" ")[1])
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return line, fingerprint


def leaks(message: str, private_key: str) -> bool:
    """Whether the message holds a line of the key, its header line aside, or the key whole."""
    lines = [line for line in private_key.splitlines() if line and not line.startswith("-----")]
    return private_key in message or any(line in message for line in lines)


@pytest.mark.parametrize(
    ("kind", "form"),
    [
        ("ed25519", "openssh"),
        ("ed25519", "pkcs8"),
        ("rsa", "openssh"),
        ("rsa", "pkcs8"),
        ("rsa", "pkcs1"),
        ("ecdsa", "openssh"),
        ("ecdsa", "pkcs8"),
        ("ecdsa", "pkcs1"),
    ],
)
def test_derive_answers_the_public_key_and_fingerprint(kind: str, form: str):
    key = new_key(kind)
    line, fingerprint = derive(private_text(key, FORMS[form]))
    want_line, want_fingerprint = expected(key)
    same = (line, fingerprint) == (want_line, want_fingerprint)
    assert same
    prefix = {"ed25519": "ssh-ed25519 ", "rsa": "ssh-rsa ", "ecdsa": "ecdsa-sha2-nistp256 "}
    assert line.startswith(prefix[kind])
    ## no comment, and no padding in the fingerprint
    assert len(line.split(" ")) == 2
    assert fingerprint.startswith("SHA256:")
    assert not fingerprint.endswith("=")
    assert len(fingerprint) == len("SHA256:") + 43


def test_derive_reads_a_key_with_windows_line_ends_and_spaces_around():
    key = new_key("ed25519")
    text = "\n  " + private_text(key, OPENSSH).replace("\n", "\r\n") + "  \n"
    same = derive(text) == expected(key)
    assert same


@pytest.mark.parametrize("form", FORMS)
def test_a_key_with_a_passphrase_raises_without_the_key(form: str):
    text = private_text(new_key("rsa"), FORMS[form], b"a test passphrase")
    with pytest.raises(ValueError) as raised:
        derive(text)
    message = str(raised.value)
    assert message == PASSPHRASE
    leaked = leaks(message, text) or leaks(repr(raised.value), text)
    assert not leaked
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    "text",
    [
        "not a key",
        "",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nbm90IGEga2V5\n-----END OPENSSH PRIVATE KEY-----\n",
        "-----BEGIN PRIVATE KEY-----\nbm90IGEga2V5\n-----END PRIVATE KEY-----\n",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA dev@host",
    ],
    ids=["text", "empty", "openssh-garbage", "pem-garbage", "public-key"],
)
def test_text_that_is_no_key_raises_without_the_text(text: str):
    with pytest.raises(ValueError) as raised:
        derive(text)
    message = str(raised.value)
    assert message == UNREADABLE
    assert raised.value.__cause__ is None


def test_a_truncated_key_raises_without_a_line_of_it():
    text = ed25519_private_key()
    lines = text.splitlines()
    cut = "\n".join([*lines[:2], lines[-1]]) + "\n"
    with pytest.raises(ValueError) as raised:
        derive(cut)
    leaked = leaks(str(raised.value), text)
    assert str(raised.value) == UNREADABLE
    assert not leaked


def test_a_key_of_another_type_cannot_be_read():
    text = private_text(dsa.generate_private_key(key_size=2048), PKCS8)
    with pytest.raises(ValueError, match=f"^{UNREADABLE}$"):
        derive(text)


def ssh_item(**ssh: Any) -> dict[str, Any]:
    return {"type": 5, "name": "Key", "sshKey": ssh}


def test_fill_in_fills_both_when_empty():
    key = new_key("ed25519")
    item = ssh_item(privateKey=private_text(key, OPENSSH), publicKey=None, keyFingerprint="")
    assert fill_in(item) == ["sshKey.publicKey", "sshKey.keyFingerprint"]
    same = (item["sshKey"]["publicKey"], item["sshKey"]["keyFingerprint"]) == expected(key)
    assert same


def test_fill_in_keeps_a_value_already_there_even_one_that_does_not_match():
    key = new_key("ed25519")
    item = ssh_item(
        privateKey=private_text(key, OPENSSH),
        publicKey="ssh-ed25519 AAAA other",
        keyFingerprint=None,
    )
    assert fill_in(item) == ["sshKey.keyFingerprint"]
    assert item["sshKey"]["publicKey"] == "ssh-ed25519 AAAA other"
    same = item["sshKey"]["keyFingerprint"] == expected(key)[1]
    assert same


@pytest.mark.parametrize(
    "item",
    [
        ssh_item(privateKey=None, publicKey=None, keyFingerprint=None),
        ssh_item(privateKey="  ", publicKey=None, keyFingerprint=None),
        ssh_item(privateKey="not a key", publicKey="ssh-ed25519 AAAA", keyFingerprint="SHA256:x"),
        {"type": 2, "name": "Note", "sshKey": {"privateKey": "not a key"}},
        {"type": 5, "name": "Key"},
    ],
    ids=["no-key", "blank-key", "all-set", "not-ssh", "no-sub-object"],
)
def test_fill_in_leaves_other_items_alone(item: dict[str, Any]):
    before = repr(item)
    assert fill_in(item) == []
    assert repr(item) == before


def test_fill_in_raises_for_a_passphrase_key_and_changes_nothing():
    item = ssh_item(privateKey=passphrase_private_key(), publicKey=None, keyFingerprint=None)
    with pytest.raises(ValueError, match=f"^{PASSPHRASE}$"):
        fill_in(item)
    assert (item["sshKey"]["publicKey"], item["sshKey"]["keyFingerprint"]) == (None, None)
