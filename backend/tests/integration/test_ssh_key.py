"""A secure note holding a private key becomes an SSH key item, against the dev Vaultwarden.

bw 2026.10.0 refuses an SSH key without its public key and fingerprint, so the conversion
works only because the write path derives them (items/ssh_keys.py). The keys are made here, never a
real one. The module deletes every item it made for good at the end. Nothing here prints a
key: each comparison goes into a bool before the assert.
"""

import base64
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from support.app import App
from support.cli import BwCli
from support.vault import Vault, same_secret

from bwtidy.items.ssh_keys import derive

pytestmark = pytest.mark.integration

NOTE, SSH_KEY = 2, 5
MAPPING = {"sshKey.privateKey": "notes"}


class KeyVault(Vault):
    """The Vault of test_writes plus the items this module made."""

    made: list[str]

    def note(self, name: str, notes: str) -> dict[str, Any]:
        """A secure note made by bw as another client would, the app's cache reloaded."""
        item = {"type": NOTE, "name": name, "notes": notes, "secureNote": {"type": 0}}
        encoded = base64.b64encode(json.dumps(item).encode()).decode()
        self.beat()
        made = self.cli("create", "item", encoded)
        self.made.append(made["id"])
        self.call(self.client.sync)
        self.call(self.app.session.cache.load)
        return made

    def convert(self, note: dict[str, Any]):
        return self.post(self.op("convertItem", note, newType=SSH_KEY, mapping=MAPPING))


@pytest.fixture(scope="module")
def vault(app: App, bw_cli: BwCli) -> Iterator[KeyVault]:
    assert app.login().status_code == 200
    app.settle()
    vault = KeyVault(app, bw_cli)
    vault.made = []
    yield vault
    assert app.client.portal is not None
    vault.beat()
    vault.cli_run("sync")
    for item_id in vault.made:
        vault.cli_run("delete", "item", item_id, "--permanent")
    app.client.portal.call(vault.client.sync)
    app.client.portal.call(app.session.cache.load)


def key_lines(private_key: str) -> list[str]:
    return [line for line in private_key.splitlines() if line and "-----" not in line]


def shows_key(text: str, private_key: str) -> bool:
    return any(line in text for line in key_lines(private_key))


def test_a_note_holding_a_key_becomes_an_ssh_key_with_all_three(
    vault: KeyVault, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    private_key = (
        ed25519.Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
        .decode()
    )
    note = vault.note("Deploy key note", private_key)
    response = vault.convert(note)
    assert response.status_code == 200, response.text
    row = vault.change_log()[0]
    assert (row["operationKind"], row["itemIds"], row["result"]) == (
        "convertItem",
        [note["id"]],
        "ok",
    )
    assert {"sshKey.publicKey", "sshKey.keyFingerprint"} <= set(row["columns"])
    [new_id] = row["createdItemIds"]
    vault.made.append(new_id)

    vault.cli_run("sync")
    item = vault.cli("get", "item", new_id)
    assert (item["type"], item["name"]) == (SSH_KEY, "Deploy key note")
    assert not item.get("notes")
    ssh = item["sshKey"]
    kept = same_secret(ssh["privateKey"].strip(), private_key.strip())
    assert kept
    public_key, fingerprint = derive(private_key)
    derived = (ssh["publicKey"], ssh["keyFingerprint"]) == (public_key, fingerprint)
    assert derived
    assert ssh["publicKey"].startswith("ssh-ed25519 ")
    assert ssh["keyFingerprint"].startswith("SHA256:")
    old = vault.cli("get", "item", note["id"])
    assert old.get("deletedDate")

    ## the grid shows the derived columns, never the private key
    rows = vault.app.client.get("/api/grid").json()["rows"]
    [cells] = [r["cells"] for r in rows if r["id"] == new_id]
    assert cells["sshKey.privateKey"] == {"masked": True}
    assert cells["sshKey.keyFingerprint"] == fingerprint
    shown = json.dumps(cells) + json.dumps(vault.change_log()) + caplog.text
    leaked = shows_key(shown, private_key)
    assert not leaked


def test_a_key_with_a_passphrase_fails_and_creates_nothing(
    vault: KeyVault, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    private_key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"a test passphrase"),
        )
        .decode()
    )
    note = vault.note("Locked key note", private_key)
    response = vault.convert(note)
    assert response.status_code == 500
    assert response.json()["applied"] == []
    assert "the private key has a passphrase" in response.json()["detail"]
    row = vault.change_log()[0]
    assert (row["operationKind"], row["result"], row["createdItemIds"]) == (
        "convertItem",
        "failed",
        [],
    )
    vault.cli_run("sync")
    names = [i["name"] for i in vault.cli("list", "items", "--search", "Locked key note")]
    assert names == ["Locked key note"]
    still = vault.cli("get", "item", note["id"])
    assert (still["type"], still.get("deletedDate")) == (NOTE, None)
    shown = response.text + json.dumps(vault.change_log()) + caplog.text
    leaked = shows_key(shown, private_key)
    assert not leaked
