import asyncio
import json
from pathlib import Path

import pytest

from bwtidy.bw.client import BwClient
from bwtidy.db import TOOL_NOTE_ID, Database
from bwtidy.protocol import DB_FILE
from bwtidy.vault.cache import VaultCache
from bwtidy.vault.tool_note import (
    ToolNoteError,
    ToolNoteId,
    backup_fields_hidden,
    ensure_tool_note,
    find_tool_note,
    set_backup_passwords,
    with_backup_passwords,
)
from tests.support.fake_bw import PASSWORD, FakeBwServe
from tests.support.hooks import nothing


def bootstrap(bw: FakeBwServe, remembered: ToolNoteId | None = None) -> tuple[dict, VaultCache]:
    async def go() -> tuple[dict, VaultCache]:
        client = BwClient(bw.http())
        await client.unlock(PASSWORD)
        cache = VaultCache(client, on_load=nothing)
        await cache.load()
        note = await ensure_tool_note(client, cache, remembered or ToolNoteId(), PASSWORD)
        return note, cache

    return asyncio.run(go())


def test_creates_the_note_when_missing():
    bw = FakeBwServe()
    note, cache = bootstrap(bw)
    assert len(bw.created) == 1
    posted = bw.posted[0]
    assert posted["type"] == 2
    assert posted["name"] == "bwtidy"
    assert posted["organizationId"] is None
    assert posted["secureNote"] == {"type": 0}
    assert json.loads(posted["notes"]) == {"version": 1, "templates": []}
    assert posted["fields"] == [{"name": "backup-password", "value": PASSWORD, "type": 1}]
    assert note == bw.created[0]
    assert note in cache.items


def test_finds_the_existing_note():
    existing = {"id": "tool", "type": 2, "name": "bwtidy", "organizationId": None}
    bw = FakeBwServe([existing])
    note, _ = bootstrap(bw)
    assert note["id"] == "tool"
    assert bw.created == []
    assert bw.calls("POST", "/object/item") == 0


def test_ignores_a_note_named_bwtidy_inside_an_org():
    in_org = {"id": "org-note", "type": 2, "name": "bwtidy", "organizationId": "org1"}
    bw = FakeBwServe([in_org])
    note, _ = bootstrap(bw)
    assert note["id"] != "org-note"
    assert len(bw.created) == 1
    assert bw.posted[0]["organizationId"] is None


def test_ignores_a_login_named_bwtidy():
    login = {"id": "login", "type": 1, "name": "bwtidy", "organizationId": None}
    bw = FakeBwServe([login])
    note, _ = bootstrap(bw)
    assert note["id"] != "login"
    assert len(bw.created) == 1


def test_a_renamed_note_is_found_by_its_remembered_id():
    existing = {"id": "tool", "type": 2, "name": "bwtidy", "organizationId": None}
    bw = FakeBwServe([existing])
    remembered = ToolNoteId()
    _, cache = bootstrap(bw, remembered)
    assert remembered.last_found == "tool"
    cache.items[0]["name"] = "bwtidyK"
    found = find_tool_note(cache, remembered)
    assert found is not None
    assert found["id"] == "tool"


def test_the_remembered_id_wins_over_another_note_named_bwtidy():
    renamed = {"id": "tool", "type": 2, "name": "bwtidyK", "organizationId": None}
    impostor = {"id": "other", "type": 2, "name": "bwtidy", "organizationId": None}
    bw = FakeBwServe([impostor, renamed])
    cache = VaultCache(BwClient(bw.http()), on_load=nothing)
    cache.items = [impostor, renamed]
    remembered = ToolNoteId()
    remembered.last_found = "tool"
    found = find_tool_note(cache, remembered)
    assert found is not None
    assert found["id"] == "tool"


def test_a_remembered_id_that_is_gone_falls_back_to_the_name():
    note = {"id": "new", "type": 2, "name": "bwtidy", "organizationId": None}
    cache = VaultCache(BwClient(FakeBwServe([note]).http()), on_load=nothing)
    cache.items = [note]
    remembered = ToolNoteId()
    remembered.last_found = "trashed"
    found = find_tool_note(cache, remembered)
    assert found is not None
    assert found["id"] == "new"
    assert remembered.last_found == "new"


KEPT_NOTES = json.dumps({"version": 1, "templates": [], "note": {"name": "Pass"}})


def test_a_note_that_kept_its_new_name_is_found_without_its_id():
    """After a reinstall bwtidy knows no id: a note whose note section holds its own name is
    the tool note, one whose section holds another name is not."""
    other = {"id": "other", "type": 2, "name": "Diary", "organizationId": None, "notes": KEPT_NOTES}
    in_org = {"id": "org", "type": 2, "name": "Pass", "organizationId": "org1", "notes": KEPT_NOTES}
    kept = {"id": "tool", "type": 2, "name": "Pass", "organizationId": None, "notes": KEPT_NOTES}
    bw = FakeBwServe([other, in_org, kept])
    remembered = ToolNoteId()
    note, _ = bootstrap(bw, remembered)
    assert note["id"] == "tool"
    assert remembered.last_found == "tool"
    assert bw.created == []


def test_a_note_named_bwtidy_comes_before_one_that_kept_its_name():
    kept = {"id": "kept", "type": 2, "name": "Pass", "organizationId": None, "notes": KEPT_NOTES}
    named = {"id": "named", "type": 2, "name": "bwtidy", "organizationId": None}
    cache = VaultCache(BwClient(FakeBwServe([kept, named]).http()), on_load=nothing)
    cache.items = [kept, named]
    found = find_tool_note(cache, ToolNoteId())
    assert found is not None
    assert found["id"] == "named"


def test_a_note_whose_section_holds_another_name_is_no_tool_note():
    stray = {"id": "stray", "type": 2, "name": "Diary", "organizationId": None, "notes": KEPT_NOTES}
    bw = FakeBwServe([stray])
    note, _ = bootstrap(bw)
    assert note["id"] != "stray"
    assert len(bw.created) == 1


def test_a_remembered_id_in_an_organization_is_not_the_tool_note():
    moved = {"id": "tool", "type": 2, "name": "bwtidy", "organizationId": "org1"}
    cache = VaultCache(BwClient(FakeBwServe([moved]).http()), on_load=nothing)
    cache.items = [moved]
    remembered = ToolNoteId()
    remembered.last_found = "tool"
    assert find_tool_note(cache, remembered) is None


def test_keep_writes_the_id_of_a_note_found_by_its_name(tmp_path: Path):
    """The remembered note is gone and another one is named bwtidy: the lookups settle on
    it, and keep (after a load) writes its id to the settings table."""
    db = Database(tmp_path / DB_FILE)
    db.set_setting(TOOL_NOTE_ID, "trashed")
    note = {"id": "new", "type": 2, "name": "bwtidy", "organizationId": None}
    cache = VaultCache(BwClient(FakeBwServe([note]).http()), on_load=nothing)
    cache.items = [note]
    remembered = ToolNoteId(db)
    remembered.keep(cache)
    assert remembered.last_found == "new"
    assert db.get_setting(TOOL_NOTE_ID) == "new"


def test_ensure_makes_no_second_note_after_a_rename(tmp_path: Path):
    db = Database(tmp_path / DB_FILE)
    bw = FakeBwServe()

    async def login() -> tuple[dict, VaultCache]:
        client = BwClient(bw.http())
        await client.unlock(PASSWORD)
        ## a fresh cache on the same database, as after a restart of bwtidy
        cache = VaultCache(client, on_load=nothing)
        await cache.load()
        return await ensure_tool_note(client, cache, ToolNoteId(db), PASSWORD), cache

    first, _ = asyncio.run(login())
    assert db.get_setting(TOOL_NOTE_ID) == first["id"]
    next(i for i in bw.items if i["id"] == first["id"])["name"] = "bwtidyK"
    second, cache = asyncio.run(login())
    assert second["id"] == first["id"]
    assert len(bw.created) == 1
    assert [i["name"] for i in cache.items] == ["bwtidyK"]


def unlock_with(bw: FakeBwServe, db: Database | None = None) -> tuple[dict, VaultCache]:
    async def go() -> tuple[dict, VaultCache]:
        client = BwClient(bw.http())
        await client.unlock(PASSWORD)
        cache = VaultCache(client, on_load=nothing)
        await cache.load()
        return await ensure_tool_note(client, cache, ToolNoteId(db), PASSWORD), cache

    return asyncio.run(go())


def test_a_note_named_bwtidy_is_not_written():
    existing = {"id": "tool", "type": 2, "name": "bwtidy", "organizationId": None}
    bw = FakeBwServe([existing])
    unlock_with(bw)
    assert bw.put == []
    assert bw.created == []


def test_the_backup_password_fields_are_written_hidden_whatever_type_they_had():
    other = [
        {"name": "note", "value": "keep me", "type": 0},
        {"name": "pin", "value": "1234", "type": 1},
        {"name": "flag", "value": "true", "type": 2},
    ]
    note = {
        "id": "tool",
        "fields": [
            other[0],
            {"name": "backup-password", "value": "old", "type": 0, "linkedId": None},
            other[1],
            {"name": "older-backup-passwords", "value": json.dumps(["older"]), "type": 0},
            other[2],
        ],
    }
    written = with_backup_passwords(note, ["new", "old", "older"])
    assert written["fields"] == [
        other[0],
        {"name": "backup-password", "value": "new", "type": 1, "linkedId": None},
        other[1],
        other[2],
        {"name": "older-backup-passwords", "value": json.dumps(["old", "older"]), "type": 1},
    ]
    ## the note handed in stays as it was
    assert note["fields"][1]["type"] == 0


def test_backup_fields_hidden_reads_the_type_of_the_reserved_fields():
    def fields(newest: int, older: int) -> dict:
        return {
            "fields": [
                {"name": "note", "value": "keep me", "type": 0},
                {"name": "backup-password", "value": "new", "type": newest},
                {"name": "older-backup-passwords", "value": json.dumps(["old"]), "type": older},
            ]
        }

    assert backup_fields_hidden(fields(1, 1))
    assert not backup_fields_hidden(fields(0, 1))
    assert not backup_fields_hidden(fields(1, 0))
    assert backup_fields_hidden({"fields": []})
    assert backup_fields_hidden({})


def test_backup_fields_hidden_skips_a_second_backup_password_field():
    note = {
        "fields": [
            {"name": "backup-password", "value": "new", "type": 1},
            {"name": "backup-password", "value": "the user's own", "type": 0},
        ]
    }
    assert backup_fields_hidden(note)
    ## a write of the list leaves that field as it is, so it must not ask for one
    assert backup_fields_hidden(with_backup_passwords(note, ["new"]))


def personal_note() -> dict:
    fields = [
        {"name": "note", "value": "keep me", "type": 0},
        {"name": "backup-password", "value": "old", "type": 0},
    ]
    return {"id": "tool", "type": 2, "name": "bwtidy", "organizationId": None, "fields": fields}


def set_passwords_after(bw: FakeBwServe, change: dict) -> tuple[list[dict], VaultCache]:
    """Load the cache, change the note in bw serve, then set the backup passwords."""

    async def go() -> tuple[list[dict], VaultCache]:
        client = BwClient(bw.http())
        await client.unlock(PASSWORD)
        cache = VaultCache(client, on_load=nothing)
        await cache.load()
        cached = [dict(item) for item in cache.items]
        bw.items[0].update(change)
        with pytest.raises(ToolNoteError) as raised:
            await set_backup_passwords(client, cache, "tool", ["new", "old"])
        assert str(raised.value) == (
            "the bwtidy note is not a personal secure note any more, "
            "move it back to your personal vault"
        )
        return cached, cache

    return asyncio.run(go())


def test_set_backup_passwords_refuses_a_note_moved_into_an_organization():
    bw = FakeBwServe([personal_note()])
    cached, cache = set_passwords_after(bw, {"organizationId": "org1"})
    assert bw.put == []
    assert bw.items[0]["fields"] == personal_note()["fields"]
    assert cache.items == cached


def test_set_backup_passwords_refuses_a_note_that_is_a_login_now():
    bw = FakeBwServe([personal_note()])
    cached, cache = set_passwords_after(bw, {"type": 1})
    assert bw.put == []
    assert bw.items[0]["fields"] == personal_note()["fields"]
    assert cache.items == cached


def test_set_backup_passwords_writes_a_personal_note_with_the_fields_hidden():
    bw = FakeBwServe([personal_note()])

    async def go() -> VaultCache:
        client = BwClient(bw.http())
        await client.unlock(PASSWORD)
        cache = VaultCache(client, on_load=nothing)
        await cache.load()
        await set_backup_passwords(client, cache, "tool", ["new", "old"])
        return cache

    cache = asyncio.run(go())
    [(item_id, body)] = bw.put
    assert item_id == "tool"
    assert body["fields"] == [
        {"name": "note", "value": "keep me", "type": 0},
        {"name": "backup-password", "value": "new", "type": 1},
        {"name": "older-backup-passwords", "value": json.dumps(["old"]), "type": 1},
    ]
    assert cache.items[0]["fields"] == body["fields"]
