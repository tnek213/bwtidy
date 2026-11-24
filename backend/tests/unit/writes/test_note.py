"""The writes of the tool note's notes (writes/note.py) through POST /api/operations."""

import json

from bwtidy.vault.tool_note import tool_note_id
from tests.support.app_env import AppEnv


def test_a_conversion_moves_the_generator_setting_and_the_to_fill_entry_in_one_write(env: AppEnv):
    """The note's generator and toFill sections both name the new item after one PUT."""
    options = {
        "type": "passphrase",
        "words": 4,
        "separator": "-",
        "capitalize": True,
        "includeNumber": False,
    }
    session = env.app.state.bw.session
    note_id = tool_note_id(session.cache, session.tool_note_id)
    assert note_id is not None
    note = env.item(note_id)
    sections = json.loads(note["notes"])
    sections["generator"] = {"items": {"login-1": options}}
    sections["toFill"] = [{"itemId": "login-1", "columns": ["fields.PIN"], "since": "2026-10-01"}]
    note["notes"] = json.dumps(sections)
    ## an empty PIN, so the to-fill entry survives the prune at the reload
    next(f for f in env.item("login-1")["fields"] if f["name"] == "PIN")["value"] = ""
    env.reload()
    puts_before = len(env.bw.put)

    response = env.post(env.op("convertItem", "login-1", newType=2, mapping={}))
    assert response.status_code == 200, response.text
    [row] = [r for r in env.change_log() if r["operationKind"] == "convertItem"]
    [new_id] = row["createdItemIds"]
    after = json.loads(env.item(note_id)["notes"])
    assert after["generator"]["items"] == {new_id: options}
    assert after["toFill"] == [{"itemId": new_id, "columns": ["fields.PIN"], "since": "2026-10-01"}]
    assert [item_id for item_id, _ in env.bw.put[puts_before:]].count(note_id) == 1
