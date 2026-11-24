import sys
from pathlib import Path

import pytest

import bwtidy.backups.keeper as backup_keeper
import bwtidy.bw.login as bwlogin
import bwtidy.vault.cache as vault
from bwtidy import passphrase
from bwtidy.backups.files import BackupError
from tests.support.passphrase_stand_in import stand_in_decrypt, stand_in_encrypt, stand_in_hash

## the scripts import devlib from their own directory, which is on sys.path when they run, and
## the scripts' tests import it from there too
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


@pytest.fixture(autouse=True)
def scrypt_stand_in(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """One scrypt pass of age takes over a second, so unit tests get a fast stand-in.

    Tests marked real_scrypt (they check real age files), integration and e2e tests keep pyrage.
    """
    if any(request.node.get_closest_marker(name) for name in ("real_scrypt", "integration", "e2e")):
        return
    monkeypatch.setattr(passphrase, "encrypt", stand_in_encrypt)
    monkeypatch.setattr(passphrase, "decrypt", stand_in_decrypt)
    monkeypatch.setattr(passphrase, "hash_password", stand_in_hash)


@pytest.fixture(autouse=True)
def no_empty_list_delay(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake bw serve needs no time to decrypt, so VaultCache's retries wait for nothing.

    Integration tests keep the real delay, they talk to a real bw serve.
    """
    if request.node.get_closest_marker("integration") is None:
        monkeypatch.setattr(vault, "EMPTY_LIST_DELAY", 0.0)


@pytest.fixture(autouse=True)
def no_host_bw(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """A unit test never runs the host's bw (AGENTS.md): the CLI's status is stubbed as logged in.

    Tests marked bw_cli bring their own fake bw executable and keep the real call.
    """
    if request.node.get_closest_marker("integration") or request.node.get_closest_marker("bw_cli"):
        return

    async def logged_in(self: bwlogin.BwLogin) -> dict:
        return {
            "serverUrl": "https://vaultwarden",
            "userEmail": "dev@bwtidy.test",
            "status": "locked",
        }

    monkeypatch.setattr(bwlogin.BwLogin, "cli_status", logged_in)

    ## every unlock starts a background backup, which would run the host's bw export
    async def no_backup(*args: object, **kwargs: object) -> BackupError:
        raise BackupError("session", "a unit test takes no real backup")

    monkeypatch.setattr(backup_keeper, "create_backup", no_backup)
