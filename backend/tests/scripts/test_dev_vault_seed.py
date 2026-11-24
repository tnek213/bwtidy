import json
import subprocess

import pytest
from devlib import seed
from devlib.stacks import Stack

PERSONAL = json.loads(seed.PERSONAL_FIXTURE.read_text())
ORG = json.loads(seed.ORG_FIXTURE.read_text())
SECOND_ORG = json.loads(seed.SECOND_ORG_FIXTURE.read_text())
ALL_ITEMS = PERSONAL["items"] + ORG["items"] + SECOND_ORG["items"]


def test_item_names_are_unique_across_all_fixtures():
    names = [i["name"] for i in ALL_ITEMS]
    assert len(names) == len(set(names))


def test_every_org_has_a_fixture_with_its_collections():
    assert set(seed.ORGS) == {seed.ORG, seed.SECOND_ORG}
    for collections, path in seed.ORGS.values():
        fixture = json.loads(path.read_text())
        assert sorted(c["name"] for c in fixture["collections"]) == sorted(collections)
    ## Club's collection is named like one of Family's on purpose
    assert set(seed.SECOND_COLLECTIONS) <= set(seed.COLLECTIONS)
    assert seed.RESTORE_ORGS == [seed.RESTORE_ORG, seed.ORG, seed.SECOND_ORG]


def test_attachments_and_trash_name_existing_items():
    names = {i["name"] for i in ALL_ITEMS}
    assert set(seed.ATTACHMENTS.values()) <= names
    assert set(seed.TRASH) <= names
    for file_name in seed.ATTACHMENTS:
        assert (seed.DEV / "attachments" / file_name).is_file()


def test_personal_fixture_has_no_org_items():
    assert all(i["organizationId"] is None for i in PERSONAL["items"])
    assert all(i["collectionIds"] is None for i in PERSONAL["items"])


def test_org_fixture_gets_the_real_ids():
    collections = [
        {"id": "real-youth", "name": "Youth"},
        {"id": "real-shared", "name": "Shared"},
    ]
    result = seed.org_fixture(ORG, "real-org", collections)
    assert {c["id"] for c in result["collections"]} == {"real-shared", "real-youth"}
    assert all(c["organizationId"] == "real-org" for c in result["collections"])
    assert all(i["organizationId"] == "real-org" for i in result["items"])
    used = {c for i in result["items"] for c in i["collectionIds"]}
    assert used == {"real-shared", "real-youth"}
    ## the fixture on disk keeps its placeholders
    assert ORG["items"][0]["organizationId"] != "real-org"


def test_second_org_fixture_gets_the_real_ids():
    collections = [{"id": "real-club-shared", "name": "Shared"}]
    result = seed.org_fixture(SECOND_ORG, "real-club", collections)
    assert [c["id"] for c in result["collections"]] == ["real-club-shared"]
    assert all(c["organizationId"] == "real-club" for c in result["collections"])
    [item] = result["items"]
    assert item["name"] == "Club locker code"
    assert item["organizationId"] == "real-club"
    assert item["collectionIds"] == ["real-club-shared"]
    ## its own placeholders, so neither file's ids can stand for the other's
    assert SECOND_ORG["collections"][0]["id"] not in {c["id"] for c in ORG["collections"]}
    assert SECOND_ORG["items"][0]["organizationId"] != ORG["items"][0]["organizationId"]


def test_personal_fixture_has_an_identity_an_ssh_key_and_favorites():
    by_type = {t: [i for i in PERSONAL["items"] if i["type"] == t] for t in (4, 5)}
    [identity] = by_type[4]
    assert identity["name"] == "Dev Identity"
    [key] = by_type[5]
    assert key["name"] == "Build server key"
    assert all(key["sshKey"][k] for k in ("privateKey", "publicKey", "keyFingerprint"))
    favorites = [i for i in PERSONAL["items"] if i["favorite"]]
    assert {i["name"] for i in favorites} == {"Wiki", "Streaming video", "Dashboards"}
    assert len({i["folderId"] for i in favorites}) == 3


def test_a_failed_bw_names_its_stderr_only(monkeypatch):
    def failing(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "session-key-on-stdout\n", "bw said no\n")

    monkeypatch.setattr(seed.subprocess, "run", failing)
    with pytest.raises(SystemExit) as raised:
        seed.bw(Stack(1), "unlock", "--raw")
    assert raised.value.code == "dev-vault: bw unlock failed:\nbw said no\n"
    logged_out = {"status": "unauthenticated", "serverUrl": seed.SERVER_URL}
    monkeypatch.setattr(seed, "bw_json", lambda *args, **kwargs: logged_out)
    with pytest.raises(SystemExit) as raised:
        seed.ensure_login(Stack(1), "1")
    assert raised.value.code == "dev-vault: bw login failed:\nbw said no\n"


def test_a_failed_bw_without_stderr_names_the_exit_code_and_no_stdout(monkeypatch):
    def failing(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 3, "session-key-on-stdout\n", "")

    monkeypatch.setattr(seed.subprocess, "run", failing)
    with pytest.raises(SystemExit) as raised:
        seed.bw(Stack(1), "unlock", "--raw")
    assert raised.value.code == "dev-vault: bw unlock failed with exit 3 (no error message)"
