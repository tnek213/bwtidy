"""create_backup against the fake bw serve and a fake bw CLI.

The fake CLI (a Python script) answers the commands create_backup runs, checks that the
session key arrives in BW_SESSION and logs its argv so the tests can see what the command
lines held.
"""

import asyncio
import io
import json
import re
import signal
import stat
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

import bwtidy.vault.cache as vault
from bwtidy.backups.collect import (
    RESTORE_DOC_COMMIT,
    attachment_members,
    check_attachments,
    create_backup,
    encrypted_size,
    run_bw,
)
from bwtidy.backups.crypto import decrypt_backup
from bwtidy.backups.files import BackupError, fingerprint
from bwtidy.bw.client import BwClient
from bwtidy.bw.process import stop_bw
from bwtidy.db import Database
from bwtidy.protocol import backup_name
from bwtidy.vault.cache import VaultCache
from tests.support.fake_bw import PASSWORD, FakeBwServe
from tests.support.fake_bw_script import FAKE_BW
from tests.support.hooks import nothing
from tests.support.quiet import assert_absent

NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)
VERSION = "261026 abc1234"
ORG = {"object": "organization", "id": "org1", "name": "Family"}


def fake_bytes(attachment_id: str, item_id: str) -> bytes:
    """What the fake bw answers for bw get attachment."""
    return f"bytes of {attachment_id} on {item_id}".encode()


def attachment(attachment_id: str, item_id: str, file_name: str) -> dict[str, str]:
    """An attachment as bw serve lists it: the size is that of the encrypted blob."""
    return {
        "id": attachment_id,
        "fileName": file_name,
        "size": str(encrypted_size(len(fake_bytes(attachment_id, item_id)))),
    }


ORG_ITEM = {
    "id": "item-org",
    "organizationId": "org1",
    "name": "Youth laptop",
    "type": 2,
    "attachments": [
        attachment("att1", "item-org", "recovery.txt"),
        attachment("att2", "item-org", "recovery.txt"),
    ],
}
PERSONAL_ITEM = {
    "id": "item-personal",
    "name": "ThinkPad X1",
    "type": 1,
    "attachments": [attachment("att3", "item-personal", "thinkpad.txt")],
}
## the same name and the same file name as PERSONAL_ITEM, only the id differs
TWIN_ITEM = {
    "id": "item-twin",
    "name": "ThinkPad X1",
    "type": 1,
    "attachments": [attachment("att4", "item-twin", "thinkpad.txt")],
}
ITEMS = [PERSONAL_ITEM, TWIN_ITEM, ORG_ITEM]
TRASHED_ITEM = {
    "id": "item-trashed",
    "name": "Old forum account",
    "deletedDate": "2026-10-20T10:00:00.000Z",
    "attachments": [attachment("att5", "item-trashed", "codes.txt")],
}
## only GET /list/object/items?archived=true lists these, with trash=true the one in the trash
ARCHIVED_ITEM = {
    "id": "item-archived",
    "name": "Old laptop",
    "folderId": "f1",
    "archivedDate": "2026-10-21T10:00:00.000Z",
    "attachments": [attachment("att6", "item-archived", "laptop.txt")],
}
ARCHIVED_TRASHED_ITEM = {
    "id": "item-archived-trashed",
    "name": "Old phone",
    "archivedDate": "2026-10-21T10:00:00.000Z",
    "deletedDate": "2026-10-22T10:00:00.000Z",
    "attachments": [attachment("att7", "item-archived-trashed", "phone.txt")],
}


class BackupRun:
    """create_backup against the fake bw serve and a fake bw executable, without the app."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.bw = FakeBwServe(list(ITEMS))
        self.bw.organizations = [ORG]
        self.bw.archived = [ARCHIVED_ITEM, ARCHIVED_TRASHED_ITEM]
        self.client = BwClient(self.bw.http())
        self.directory = tmp_path / "backups"
        self.log = tmp_path / "bw.log"
        self.bw_bin = tmp_path / "bw"
        self.bw_bin.write_text(f"#!{sys.executable}\n{FAKE_BW}")
        self.bw_bin.chmod(self.bw_bin.stat().st_mode | stat.S_IEXEC)
        self.restore_doc = tmp_path / "restore.md"
        self.restore_doc.write_text(
            "# Restoring\n\nWritten for bwtidy COMMIT. The project: https://example.test\n"
        )
        monkeypatch.setenv("FAKE_BW_LOG", str(self.log))
        monkeypatch.setenv("FAKE_BW_TRASH", json.dumps([TRASHED_ITEM]))
        self.monkeypatch = monkeypatch
        self.password: str | None = None
        self.session_key: str | None = None

    async def unlock(self) -> None:
        self.session_key = await self.client.unlock(PASSWORD)
        self.password = PASSWORD
        assert self.session_key
        self.monkeypatch.setenv("FAKE_BW_KEY", self.session_key)

    def backup(self, timeout: float = 5, store: Database | None = None, now: datetime = NOW):
        async def go():
            await self.unlock()
            return await create_backup(
                self,
                self.directory,
                str(self.bw_bin),
                store=store,
                now=now,
                timeout=timeout,
                version=VERSION,
                restore_doc=self.restore_doc,
            )

        return asyncio.run(go())

    def commands(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def files(self) -> list[str]:
        return sorted(p.name for p in self.directory.iterdir()) if self.directory.exists() else []


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BackupRun:
    return BackupRun(tmp_path, monkeypatch)


def open_backup(env: BackupRun, name: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(decrypt_backup(env.directory / name, PASSWORD)))


def test_backup_holds_every_member(env: BackupRun):
    info = env.backup()
    assert info.name == backup_name(NOW)
    assert info.time == NOW
    assert env.files() == [info.name]
    assert info.size == (env.directory / info.name).stat().st_size
    assert env.bw.calls("POST", "/sync") == 1
    with open_backup(env, info.name) as archive:
        assert sorted(archive.namelist()) == [
            "RESTORE.md",
            "item-attachments/item-archived-trashed/phone.txt",
            "item-attachments/item-archived/laptop.txt",
            "item-attachments/item-org/att2-recovery.txt",
            "item-attachments/item-org/recovery.txt",
            "item-attachments/item-personal/thinkpad.txt",
            "item-attachments/item-trashed/codes.txt",
            "item-attachments/item-twin/thinkpad.txt",
            "org-org1-export.json",
            "personal-export.zip",
            "raw/archived.json",
            "raw/collections.json",
            "raw/folders.json",
            "raw/items.json",
            "raw/organizations.json",
            "raw/trash.json",
        ]
        assert json.loads(archive.read("org-org1-export.json"))["org"] == "org1"
        assert json.loads(archive.read("raw/items.json")) == ITEMS
        assert json.loads(archive.read("raw/organizations.json")) == [ORG]
        assert json.loads(archive.read("raw/folders.json")) == env.bw.folders
        assert json.loads(archive.read("raw/collections.json")) == []
        assert json.loads(archive.read("raw/trash.json")) == [TRASHED_ITEM]
        assert json.loads(archive.read("raw/archived.json")) == [
            ARCHIVED_ITEM,
            ARCHIVED_TRASHED_ITEM,
        ]
        with zipfile.ZipFile(io.BytesIO(archive.read("personal-export.zip"))) as personal:
            assert "attachments/ThinkPad X1/thinkpad.txt" in personal.namelist()
        manual = archive.read("RESTORE.md").decode()
        assert f"Written for bwtidy {VERSION}. The project: https://example.test" in manual
        assert "COMMIT" not in manual


def test_a_backup_without_its_manual_is_still_written(
    env: BackupRun, caplog: pytest.LogCaptureFixture
):
    env.restore_doc.unlink()
    with caplog.at_level("WARNING", logger="bwtidy.backups.collect"):
        info = env.backup()
    with open_backup(env, info.name) as archive:
        assert "RESTORE.md" not in archive.namelist()
        assert "raw/items.json" in archive.namelist()
    assert any("RESTORE.md" in r.getMessage() for r in caplog.records)


def test_the_manual_names_the_cli_version_the_image_installs():
    """docs/restore.md says which Bitwarden CLI to install, the one the image's lock file pins."""
    root = Path(__file__).resolve().parents[4]
    package = json.loads((root / "docker" / "bitwarden-cli" / "package.json").read_text())
    pinned = package["dependencies"]["@bitwarden/cli"]
    assert re.fullmatch(r"[0-9.]+", pinned)
    manual = (root / "docs" / "restore.md").read_text()
    assert f"@bitwarden/cli@{pinned}" in manual
    assert manual.count(RESTORE_DOC_COMMIT) == 1


def test_every_attachment_is_filed_by_item_id(env: BackupRun):
    """Personal, organization and trashed attachments alike, each under its item's id."""
    info = env.backup()
    with open_backup(env, info.name) as archive:
        assert archive.read("item-attachments/item-personal/thinkpad.txt") == fake_bytes(
            "att3", "item-personal"
        )
        assert archive.read("item-attachments/item-org/recovery.txt") == fake_bytes(
            "att1", "item-org"
        )
        assert archive.read("item-attachments/item-trashed/codes.txt") == fake_bytes(
            "att5", "item-trashed"
        )


def test_archived_items_and_their_attachments_are_backed_up(env: BackupRun):
    """bw serve leaves an archived item out of every listing but archived=true, and the
    attachments of an archived item in the trash are in no export either."""
    info = env.backup()
    ## the items, the archived items and the archived items in the trash
    assert env.bw.calls("GET", "/list/object/items") == 3
    with open_backup(env, info.name) as archive:
        assert archive.read("item-attachments/item-archived/laptop.txt") == fake_bytes(
            "att6", "item-archived"
        )
        assert archive.read("item-attachments/item-archived-trashed/phone.txt") == fake_bytes(
            "att7", "item-archived-trashed"
        )
        ## the active items and the trash stay as the other listings answer them
        assert json.loads(archive.read("raw/items.json")) == ITEMS
        assert json.loads(archive.read("raw/trash.json")) == [TRASHED_ITEM]


def test_a_vault_without_archived_items_gets_an_empty_listing(env: BackupRun):
    env.bw.archived = []
    info = env.backup()
    with open_backup(env, info.name) as archive:
        assert json.loads(archive.read("raw/archived.json")) == []
        assert not [m for m in archive.namelist() if m.startswith("item-attachments/item-arch")]


def test_an_archived_attachment_with_the_wrong_number_of_bytes_fails_the_backup(env: BackupRun):
    env.monkeypatch.setenv("FAKE_BW_SHORT", "att7")
    with pytest.raises(BackupError) as error:
        env.backup()
    assert error.value.step == "attachments"
    assert "attachment att7 of item item-archived-trashed has " in str(error.value)
    assert env.files() == []


def test_an_archived_attachment_without_a_member_fails_the_check():
    with pytest.raises(BackupError) as error:
        check_attachments([PERSONAL_ITEM, ARCHIVED_ITEM], {})
    assert error.value.step == "attachments"
    assert "is missing from the backup" in str(error.value)


def test_an_archived_item_in_an_unlisted_organization_is_refused(env: BackupRun):
    env.bw.items = [PERSONAL_ITEM]
    env.bw.archived = [{**ARCHIVED_ITEM, "organizationId": "org1"}]
    env.bw.organizations = []
    with pytest.raises(BackupError) as error:
        env.backup()
    assert error.value.step == "vault listing"
    assert env.files() == []


def test_an_archived_item_in_the_trash_needs_no_listed_place(env: BackupRun):
    """Like the trash: a place only an item in the trash names never stops a backup."""
    env.bw.items = [PERSONAL_ITEM]
    gone = {"organizationId": "org-gone", "folderId": "f-gone", "collectionIds": ["c-gone"]}
    env.bw.archived = [{**ARCHIVED_TRASHED_ITEM, **gone}]
    env.bw.organizations = []
    info = env.backup()
    assert env.bw.calls("GET", "/list/object/organizations") == 1
    assert env.bw.calls("GET", "/list/object/folders") == 1
    assert env.files() == [info.name]


def test_a_second_file_of_the_same_name_gets_the_attachment_id_in_front(env: BackupRun):
    info = env.backup()
    with open_backup(env, info.name) as archive:
        assert archive.read("item-attachments/item-org/att2-recovery.txt") == fake_bytes(
            "att2", "item-org"
        )


def test_two_items_of_the_same_name_each_keep_their_own_file(env: BackupRun):
    assert PERSONAL_ITEM["name"] == TWIN_ITEM["name"]
    info = env.backup()
    with open_backup(env, info.name) as archive:
        first = archive.read("item-attachments/item-personal/thinkpad.txt")
        second = archive.read("item-attachments/item-twin/thinkpad.txt")
    assert first == fake_bytes("att3", "item-personal")
    assert second == fake_bytes("att4", "item-twin")
    assert first != second


def test_bw_gets_the_key_by_environment_and_never_the_password(env: BackupRun):
    env.backup()
    commands = env.commands()
    assert [c[:4] for c in commands] == [
        ["--raw", "export", "--format", "zip"],
        ["--raw", "export", "--format", "json"],
        ["list", "items", "--trash", "--nointeraction"],
        ["--raw", "get", "attachment", "att3"],
        ["--raw", "get", "attachment", "att4"],
        ["--raw", "get", "attachment", "att1"],
        ["--raw", "get", "attachment", "att2"],
        ["--raw", "get", "attachment", "att5"],
        ["--raw", "get", "attachment", "att6"],
        ["--raw", "get", "attachment", "att7"],
    ]
    assert env.session_key
    for command in commands:
        assert_absent(PASSWORD, " ".join(command), "the command line")
        assert_absent(env.session_key, " ".join(command), "the command line")
        assert command[-1] == "--nointeraction"


def test_a_failed_download_leaves_no_file_and_names_the_step(env: BackupRun):
    env.monkeypatch.setenv("FAKE_BW_FAIL", "get attachment att2")
    with pytest.raises(BackupError) as error:
        env.backup()
    assert error.value.step == "attachments"
    assert str(error.value).startswith(
        "Backup failed at attachments: attachment att2 of item item-org: bw exited with 1: "
    )
    assert "forced failure" in str(error.value)
    assert_absent("recovery.txt", str(error.value), "the error message")
    assert env.files() == []


def test_a_download_with_the_wrong_number_of_bytes_fails_the_backup(env: BackupRun):
    env.monkeypatch.setenv("FAKE_BW_SHORT", "att4")
    with pytest.raises(BackupError) as error:
        env.backup()
    assert error.value.step == "attachments"
    whole = len(fake_bytes("att4", "item-twin"))
    assert str(error.value) == (
        "Backup failed at attachments: attachment att4 of item item-twin "
        f"has {whole - 16} bytes, which does not fit the listed size {encrypted_size(whole)}"
    )
    for word in ("thinkpad.txt", "ThinkPad"):
        assert_absent(word, str(error.value), "the error message")
    assert env.files() == []


def test_an_attachment_without_a_member_fails_the_check():
    members = {"item-attachments/item-personal/thinkpad.txt": fake_bytes("att3", "item-personal")}
    check_attachments([PERSONAL_ITEM], members)
    with pytest.raises(BackupError) as error:
        check_attachments([PERSONAL_ITEM, TWIN_ITEM], members)
    assert str(error.value) == (
        "Backup failed at attachments: attachment att4 of item item-twin is missing from the backup"
    )
    ## an attachment the listing gives no size for only needs to be there
    unsized = {**TWIN_ITEM, "attachments": [{"id": "att4", "fileName": "thinkpad.txt"}]}
    check_attachments([unsized], {"item-attachments/item-twin/thinkpad.txt": b"x"})


def test_attachment_members_never_share_a_path():
    item = {
        "id": "i",
        "attachments": [
            {"id": "A", "fileName": "a.txt"},
            {"id": "B", "fileName": "C-a.txt"},
            {"id": "C", "fileName": "a.txt"},
        ],
    }
    paths = [path for _, path in attachment_members(item)]
    assert len(set(paths)) == 3
    assert paths[:2] == ["item-attachments/i/a.txt", "item-attachments/i/C-a.txt"]


def test_the_listed_size_is_that_of_the_encrypted_blob():
    """Checked on the dev vault: one type byte, the IV, the HMAC and PKCS7 padded AES-CBC."""
    assert [encrypted_size(n) for n in (0, 1, 15, 16, 17, 31, 32, 100, 1000)] == [
        65,
        65,
        65,
        81,
        81,
        81,
        97,
        161,
        1057,
    ]


def test_a_failed_export_names_its_step(env: BackupRun):
    env.monkeypatch.setenv("FAKE_BW_FAIL", "--organizationid")
    with pytest.raises(BackupError) as error:
        env.backup()
    assert error.value.step == "org export"
    assert env.files() == []


def test_a_hanging_bw_is_killed(env: BackupRun):
    env.monkeypatch.setenv("FAKE_BW_HANG", "1")
    with pytest.raises(BackupError) as error:
        env.backup(timeout=1)
    assert error.value.step == "personal export"
    assert "did not finish within 1 s" in str(error.value)
    assert env.files() == []


## like bw: it truncates the CLI state and then writes it again, and node handles SIGTERM only
## after that synchronous write
TORN_WRITE = f"""#!{sys.executable}
import os, signal, time
signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGTERM])
state = os.environ["FAKE_BW_STATE"]
with open(state) as file:
    data = file.read()
open(state, "w").close()
open(state + ".truncated", "w").close()
time.sleep(0.5)
with open(state, "w") as file:
    file.write(data)
signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGTERM])
time.sleep(30)
"""


def test_a_cancel_inside_bws_write_leaves_the_cli_state_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state = tmp_path / "data.json"
    state.write_text('{"logged": "in"}')
    truncated = tmp_path / "data.json.truncated"
    script = tmp_path / "bw"
    script.write_text(TORN_WRITE)
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_BW_STATE", str(state))

    async def cancel_inside_the_write() -> None:
        task = asyncio.create_task(run_bw(str(script), ["export"], "key", "export"))
        async with asyncio.timeout(15):
            while not truncated.exists():
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_inside_the_write())
    assert state.read_text() == '{"logged": "in"}'


def test_a_bw_that_ignores_sigterm_is_killed(tmp_path: Path):
    ignoring = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(30)"
    )

    async def stop() -> int | None:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", ignoring, stdout=asyncio.subprocess.PIPE
        )
        assert proc.stdout is not None
        await proc.stdout.readline()
        await stop_bw(proc, timeout=0.2)
        return proc.returncode

    assert asyncio.run(stop()) == -signal.SIGKILL


def test_a_missing_bw_binary_fails_cleanly(env: BackupRun):
    env.bw_bin.unlink()
    with pytest.raises(BackupError) as error:
        env.backup()
    assert error.value.step == "personal export"
    assert "cannot start bw" in str(error.value)
    assert env.files() == []


def test_a_locked_vault_fails_before_bw_runs(env: BackupRun):
    with pytest.raises(BackupError) as error:
        asyncio.run(create_backup(env, env.directory, str(env.bw_bin)))
    assert error.value.step == "session"
    assert not env.log.exists()


def test_two_backups_in_the_same_second_get_distinct_names(env: BackupRun):
    first = env.backup()
    second = env.backup()
    assert first.name == "20261026T120000Z.zip.age"
    assert second.name == "20261026T120001Z.zip.age"
    assert env.files() == [first.name, second.name]


def test_create_backup_leaves_the_retention_to_the_keeper(env: BackupRun, tmp_path: Path):
    db = Database(tmp_path / "bwtidy.sqlite")
    env.directory.mkdir()
    old = [backup_name(datetime(2020, 1, d, tzinfo=UTC)) for d in range(1, 11)]
    for name in old:
        (env.directory / name).write_bytes(b"old")
        db.set_backup_fingerprint(name, f"print of {name}")
    info = env.backup(store=db)
    ## BackupKeeper prunes, since it knows which backups the queue still needs
    assert env.files() == old + [info.name]
    assert all(db.backup_fingerprint(name) is not None for name in old)


def test_no_vault_content_in_the_error_from_a_dead_bw_serve(env: BackupRun):
    env.bw.unlocked = False
    env.password, env.session_key = PASSWORD, "stale"
    with pytest.raises(BackupError) as error:
        asyncio.run(create_backup(env, env.directory, str(env.bw_bin)))
    assert error.value.step == "vault listing"
    assert env.files() == []
    assert not env.log.exists()


def test_an_empty_item_list_after_the_sync_is_waited_out(env: BackupRun):
    env.bw.empty_lists_after_sync = 3
    info = env.backup()
    ## three empty lists and the full one, then the two of the archived items
    assert env.bw.calls("GET", "/list/object/items") == 6
    with open_backup(env, info.name) as archive:
        assert json.loads(archive.read("raw/items.json")) == ITEMS
        assert "item-attachments/item-org/recovery.txt" in archive.namelist()


def test_an_item_list_that_stays_empty_is_refused(env: BackupRun):
    env.bw.empty_lists_after_sync = vault.EMPTY_LIST_RETRIES + 5
    with pytest.raises(BackupError) as error:
        env.backup()
    assert error.value.step == "vault listing"
    assert str(error.value) == "Backup failed at vault listing: bw serve listed no items"
    assert env.bw.calls("GET", "/list/object/items") == vault.EMPTY_LIST_RETRIES + 1
    assert env.files() == []
    ## refused before any bw command ran
    assert not env.log.exists()


def test_a_full_item_list_is_taken_at_once(env: BackupRun):
    env.backup()
    ## once each: the items, the archived items and the archived items in the trash
    assert env.bw.calls("GET", "/list/object/items") == 3
    assert env.bw.calls("GET", "/list/object/folders") == 1
    assert env.bw.calls("GET", "/list/object/collections") == 1


def test_an_item_in_an_unlisted_organization_is_refused(env: BackupRun):
    env.bw.organizations = []
    with pytest.raises(BackupError) as error:
        env.backup()
    assert error.value.step == "vault listing"
    assert str(error.value) == (
        "Backup failed at vault listing: "
        "an item belongs to an organization that bw serve did not list"
    )
    ## the organizations were asked again with the whole retry budget
    assert env.bw.calls("GET", "/list/object/organizations") == vault.EMPTY_LIST_RETRIES + 1
    assert env.files() == []
    assert not env.log.exists()
    for word in ("item-org", "Youth", "org1"):
        assert_absent(word, str(error.value), "the error message")


def test_an_unlisted_folder_only_warns(env: BackupRun, caplog: pytest.LogCaptureFixture):
    env.bw.items = [{**PERSONAL_ITEM, "folderId": "f-missing"}, TWIN_ITEM, ORG_ITEM]
    with caplog.at_level("WARNING", logger="bwtidy.backups.collect"):
        info = env.backup()
    assert env.files() == [info.name]
    assert env.bw.calls("GET", "/list/object/folders") == vault.EMPTY_LIST_RETRIES + 1
    [record] = [r for r in caplog.records if "Folders" in r.getMessage()]
    assert record.getMessage() == (
        "Folders missing from bw serve's list: 1, items in them: 1. The backup goes on."
    )
    for word in ("f-missing", "item-personal", "ThinkPad"):
        assert_absent(word, record.getMessage(), "the log record")
    with open_backup(env, info.name) as archive:
        assert json.loads(archive.read("raw/items.json"))[0]["folderId"] == "f-missing"


def test_a_stale_key_in_the_shell_environment_is_replaced(env: BackupRun):
    ## the fake bw exits 1 unless BW_SESSION holds the key bw serve handed out
    env.monkeypatch.setenv("BW_SESSION", "stale-key-from-the-shell")
    env.monkeypatch.setenv("BW_PASSWORD", "must never be passed on")
    info = env.backup()
    assert env.files() == [info.name]


def test_the_fingerprint_is_stored_under_the_backups_name(env: BackupRun, tmp_path: Path):
    db = Database(tmp_path / "bwtidy.sqlite")
    info = env.backup(store=db)
    ## the archived item in the trash counts like the trash: not at all
    expected = fingerprint(ITEMS, env.bw.folders, [], [ORG], archived=[ARCHIVED_ITEM])
    assert info.fingerprint == expected
    assert db.backup_fingerprint(info.name) == expected
    assert db.backup_fingerprint("20200101T000000Z.zip.age") is None
    ## the fingerprint is a digest, never the listings
    assert len(expected) == 64
    for word in ("item-org", "Youth", "org1", "Work"):
        assert_absent(word, expected, "the fingerprint")


def test_a_backup_after_bw_serve_respelled_a_revision_matches_the_vault(env: BackupRun):
    """bw serve answers a revision of the cached item in another spelling by the time of the
    backup (revision.Spellings), and the backup still holds the vault as the cache does."""
    first = env.bw.items[0]
    env.bw.items[0] = {**first, "revisionDate": "2026-10-26T10:15:30.337Z"}
    cache = VaultCache(env.client, on_load=nothing)

    async def load() -> None:
        await env.unlock()
        await cache.load()

    asyncio.run(load())
    vault = fingerprint(
        cache.items,
        cache.folders,
        cache.collections,
        cache.organizations,
        archived=cache.archived,
    )
    env.bw.items[0] = {**first, "revisionDate": "2026-10-26T10:15:30.336Z"}
    assert env.backup().fingerprint == vault


def test_a_backup_without_a_store_still_works(env: BackupRun):
    info = env.backup()
    assert info.fingerprint
    assert env.files() == [info.name]
