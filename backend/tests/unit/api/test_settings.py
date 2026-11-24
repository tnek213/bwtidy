"""GET and PUT /api/settings/display, the date and time format, hiding the tool note and the N of
the Rare fields column (docs/spec.md, Settings and Grid)."""

import pytest

from bwtidy.db import DATE_FORMAT, HIDE_TOOL_NOTE, RARE_FIELDS_MAX
from tests.support.app_env import AppEnv

PATH = "/api/settings/display"


def test_the_default_is_system(env: AppEnv):
    response = env.client.get(PATH)
    assert response.status_code == 200, response.text
    assert response.json() == {"dateFormat": "system", "hideToolNote": False, "rareFieldsMax": 0}


def test_every_format_is_saved_in_the_settings_table(env: AppEnv):
    for choice in ("iso", "europe", "europe-dots", "usa", "system"):
        response = env.client.put(PATH, json={"dateFormat": choice, "hideToolNote": False})
        assert response.status_code == 200, response.text
        assert response.json() == {"dateFormat": choice, "hideToolNote": False, "rareFieldsMax": 0}
        assert env.client.get(PATH).json() == {
            "dateFormat": choice,
            "hideToolNote": False,
            "rareFieldsMax": 0,
        }
        assert env.writes.db.get_setting(DATE_FORMAT) == choice


def test_another_value_is_refused(env: AppEnv):
    env.client.put(PATH, json={"dateFormat": "iso"})
    for body in ({"dateFormat": "sweden"}, {"dateFormat": ""}, {"dateFormat": None}, {}):
        assert env.client.put(PATH, json=body).status_code == 422
    assert env.client.get(PATH).json() == {
        "dateFormat": "iso",
        "hideToolNote": False,
        "rareFieldsMax": 0,
    }


def test_hiding_the_tool_note_is_saved_and_keeps_the_date_format(env: AppEnv):
    env.client.put(PATH, json={"dateFormat": "usa"})
    response = env.client.put(PATH, json={"hideToolNote": True})
    assert response.status_code == 200, response.text
    assert response.json() == {"dateFormat": "usa", "hideToolNote": True, "rareFieldsMax": 0}
    assert env.writes.db.get_setting(HIDE_TOOL_NOTE) == "1"
    response = env.client.put(PATH, json={"hideToolNote": False})
    assert response.json() == {"dateFormat": "usa", "hideToolNote": False, "rareFieldsMax": 0}
    assert env.writes.db.get_setting(HIDE_TOOL_NOTE) == "0"


def test_a_date_format_keeps_hiding_the_tool_note(env: AppEnv):
    env.client.put(PATH, json={"hideToolNote": True})
    response = env.client.put(PATH, json={"dateFormat": "iso"})
    assert response.json() == {"dateFormat": "iso", "hideToolNote": True, "rareFieldsMax": 0}
    assert env.client.get(PATH).json() == {
        "dateFormat": "iso",
        "hideToolNote": True,
        "rareFieldsMax": 0,
    }


def test_both_fields_are_saved_together(env: AppEnv):
    response = env.client.put(PATH, json={"dateFormat": "europe", "hideToolNote": True})
    assert response.json() == {"dateFormat": "europe", "hideToolNote": True, "rareFieldsMax": 0}


def test_a_body_with_neither_field_or_a_bad_one_is_refused(env: AppEnv):
    env.client.put(PATH, json={"hideToolNote": True})
    for body in ({}, {"hideToolNote": None}, {"hideToolNote": "maybe"}, {"other": 1}):
        assert env.client.put(PATH, json=body).status_code == 422
    assert env.client.get(PATH).json() == {
        "dateFormat": "system",
        "hideToolNote": True,
        "rareFieldsMax": 0,
    }


def test_a_stored_hide_value_it_does_not_know_reads_as_false(env: AppEnv):
    env.writes.db.set_setting(HIDE_TOOL_NOTE, "yes")
    assert env.client.get(PATH).json()["hideToolNote"] is False


def test_a_stored_value_it_does_not_know_reads_as_system(env: AppEnv):
    env.writes.db.set_setting(DATE_FORMAT, "klingon")
    assert env.client.get(PATH).json() == {
        "dateFormat": "system",
        "hideToolNote": False,
        "rareFieldsMax": 0,
    }


def test_rare_fields_max_is_0_by_default(env: AppEnv):
    assert env.client.get(PATH).json()["rareFieldsMax"] == 0


def test_rare_fields_max_is_saved_alone(env: AppEnv):
    env.client.put(PATH, json={"dateFormat": "iso", "hideToolNote": True})
    for value in (2, 99, 0):
        response = env.client.put(PATH, json={"rareFieldsMax": value})
        assert response.status_code == 200, response.text
        expected = {"dateFormat": "iso", "hideToolNote": True, "rareFieldsMax": value}
        assert response.json() == expected
        assert env.client.get(PATH).json() == expected
        assert env.writes.db.get_setting(RARE_FIELDS_MAX) == str(value)


def test_another_setting_keeps_rare_fields_max(env: AppEnv):
    env.client.put(PATH, json={"rareFieldsMax": 5})
    env.client.put(PATH, json={"hideToolNote": True})
    assert env.client.get(PATH).json()["rareFieldsMax"] == 5


@pytest.mark.parametrize("value", [-1, 100, None, True, "2", 2.5])
def test_rare_fields_max_outside_0_to_99_is_refused(env: AppEnv, value: object):
    env.client.put(PATH, json={"rareFieldsMax": 3})
    assert env.client.put(PATH, json={"rareFieldsMax": value}).status_code == 422
    assert env.client.get(PATH).json()["rareFieldsMax"] == 3


@pytest.mark.parametrize("stored", ["", "x", "-1", "100", "2.5", "٣"])
def test_a_stored_rare_fields_max_it_does_not_know_reads_as_0(env: AppEnv, stored: str):
    env.writes.db.set_setting(RARE_FIELDS_MAX, stored)
    assert env.client.get(PATH).json()["rareFieldsMax"] == 0


def test_both_need_a_session(env: AppEnv):
    env.client.cookies.clear()
    assert env.client.get(PATH).status_code == 401
    assert env.client.put(PATH, json={"dateFormat": "iso"}).status_code == 401
