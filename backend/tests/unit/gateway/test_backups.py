"""The gateway's backups root (docs/spec.md, Gateway): each account's backups directory outside its
account directory."""

import os
import stat
from pathlib import Path

import pytest

from bwtidy.gateway.accounts import Accounts
from bwtidy.gateway.backups import BackupsRoot
from bwtidy.protocol import ACCOUNT_COOKIE as COOKIE
from bwtidy.protocol import PENDING_MARKER
from tests.support.gateway_env import EMAIL, UNLOCK, backups_env, backups_of, existing_account

## the backups an earlier account or an earlier start left behind, by name
OLD_BACKUPS = {f"2026110{day}T120000Z.zip.age": f"backup {day}".encode() for day in range(1, 5)}


def files(directory: Path) -> dict[str, bytes]:
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in directory.iterdir()}


def test_with_a_backups_root_the_worker_gets_its_own_backups_directory(tmp_path: Path):
    for env in backups_env(tmp_path):
        environment = env.session(env.started()).json()["env"]
        backups = backups_of(tmp_path)
        assert environment["BWTIDY_BACKUPS_DIR"] == str(backups)
        assert environment["BWTIDY_DATA_DIR"] == str(env.directory() / "app")
        assert backups.is_dir() and not backups.is_symlink()
        assert backups.stat().st_mode & 0o777 == 0o700
        assert backups.parent.stat().st_mode & 0o777 == 0o711
        assert (tmp_path / "b").stat().st_mode & 0o777 == 0o711
        assert not (env.directory() / "app" / "backups").exists()


def test_the_backups_directory_belongs_to_the_accounts_uid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    owners: list[tuple[str, int, int]] = []

    def fchown(fd: int, uid: int, gid: int) -> None:
        kind = "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        owners.append((kind, uid, gid))

    monkeypatch.setattr(os, "fchown", fchown)
    monkeypatch.setattr(os, "chown", lambda *args, **kwargs: owners.append(("chown", *args[1:3])))
    ## the backups of the account made before this one, which had another uid
    backups = backups_of(tmp_path)
    backups.mkdir(parents=True)
    for name, data in OLD_BACKUPS.items():
        (backups / name).write_bytes(data)
    uid = os.getuid() + 1
    root = BackupsRoot(tmp_path / "b", switch_uid=True)
    root.prepare()
    root.make("vaultwarden", EMAIL, uid)
    ## the directory, then every file in it
    assert owners[0] == ("dir", uid, uid)
    assert owners.count(("chown", uid, uid)) == len(OLD_BACKUPS)
    assert files(backups) == OLD_BACKUPS


@pytest.mark.parametrize("linked", ["host", "account"])
def test_a_symlink_in_the_backups_path_refuses_the_worker(tmp_path: Path, linked: str):
    path = existing_account(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / EMAIL).mkdir(parents=True)
    (elsewhere / EMAIL / "20261101T120000Z.zip.age").write_bytes(b"another account's backup")
    elsewhere.chmod(0o755)
    backups = backups_of(tmp_path)
    if linked == "host":
        backups.parent.parent.mkdir(parents=True)
        backups.parent.symlink_to(elsewhere)
    else:
        backups.parent.mkdir(parents=True)
        backups.symlink_to(elsewhere / EMAIL)
    for env in backups_env(tmp_path):
        response = env.click(env.cookie(env.open(create=False)), UNLOCK)
        assert response.status_code == 503
        assert response.json() == {"detail": "The worker could not start."}
        assert env.worker_pids() == []
        assert env.gateway.workers.count() == 0
        assert path.is_dir()
    assert files(elsewhere / EMAIL) == {"20261101T120000Z.zip.age": b"another account's backup"}
    assert elsewhere.stat().st_mode & 0o777 == 0o755


def test_a_symlink_refuses_a_new_account_and_leaves_no_directory(tmp_path: Path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "vaultwarden").symlink_to(elsewhere)
    for env in backups_env(tmp_path):
        assert env.click(env.cookie(env.open())).status_code == 503
        assert env.directory_gone()
        assert env.gateway.accounts.pending_count() == 0
    assert list(elsewhere.iterdir()) == []


def test_backups_left_in_app_backups_stay_there_when_the_worker_starts(tmp_path: Path):
    """The gateway moves no file out of an account's app/backups."""
    path = existing_account(tmp_path, OLD_BACKUPS)
    for env in backups_env(tmp_path):
        token = env.cookie(env.open(create=False))
        assert env.click(token, UNLOCK).status_code == 200
        assert files(path / "app" / "backups") == OLD_BACKUPS
        assert backups_of(tmp_path).is_dir()
        assert files(backups_of(tmp_path)) == {}


def test_a_pending_account_that_goes_leaves_no_backups_directory(tmp_path: Path):
    for env in backups_env(tmp_path):
        token = env.started()
        assert backups_of(tmp_path).is_dir()
        assert env.request("DELETE", "/gateway/account", {COOKIE: token}).status_code == 204
        assert env.directory_gone()
        assert not backups_of(tmp_path).exists()

        ## backups there from before (the account directory was deleted by hand) stay
        token = env.started()
        (backups_of(tmp_path) / "20261101T120000Z.zip.age").write_bytes(b"kept")
        assert env.request("DELETE", "/gateway/account", {COOKIE: token}).status_code == 204
        assert env.directory_gone()
        assert files(backups_of(tmp_path)) == {"20261101T120000Z.zip.age": b"kept"}


def test_the_sweep_at_the_start_takes_the_pending_accounts_backups_directory(tmp_path: Path):
    pending = tmp_path / "a" / "vaultwarden" / "gone@example.test"
    (pending / "app").mkdir(parents=True)
    (pending / PENDING_MARKER).touch()
    backups_of(tmp_path, "gone@example.test").mkdir(parents=True)
    for _ in backups_env(tmp_path):
        assert not pending.exists()
        assert not backups_of(tmp_path, "gone@example.test").exists()


def test_a_backups_directory_keeps_its_uid_from_being_handed_on(tmp_path: Path):
    accounts = Accounts(tmp_path / "a", uid_base=0, switch_uid=False, backups_root=tmp_path / "b")
    accounts.prepare()
    assert accounts.next_uid() == 1
    backups_of(tmp_path).mkdir(parents=True)
    assert accounts.next_uid() == os.getuid() + 1
