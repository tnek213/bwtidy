"""Helpers for tests that read and write the tool note's sections."""

import json
from typing import Any

from tests.support.app_env import AppEnv


def note_sections(env: AppEnv) -> dict[str, Any]:
    return json.loads(env.item(env.tool_note_id)["notes"])


def write_notes(env: AppEnv, sections: dict[str, Any]) -> None:
    """Change the tool note's notes behind bwtidy's back, as another client would."""
    note = env.item(env.tool_note_id)
    note["notes"] = json.dumps(sections)
    note["revisionDate"] = "2026-10-30T10:00:00.000Z"
