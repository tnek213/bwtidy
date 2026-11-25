"""The note section (docs/spec.md, The tool note): the name the tool note is expected to have,
and its one change.

Without the section, or with one that does not parse, the expected name is bwtidy, so a note
written before the section existed reads as it always did.
"""

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bwtidy.sections.notes import (
    Change,
    ChangeModel,
    InvalidToolNote,
    Rename,
    SectionRules,
    each_change,
    problem,
    read_sections,
)

SECTION = "note"
DEFAULT_NAME = "bwtidy"


class InvalidNoteSettings(ValueError):
    """A note section that does not parse. The message says why."""


class NoteSettings(BaseModel):
    """The note section: the name the tool note is expected to have."""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(DEFAULT_NAME, min_length=1)

    def dump(self) -> dict[str, Any]:
        return {"name": self.name}


def parse_note(value: Any) -> NoteSettings:
    """The note section. None (no section yet) is the default. Raises InvalidNoteSettings."""
    if value is None:
        return NoteSettings()
    try:
        return NoteSettings.model_validate(value)
    except ValidationError as error:
        raise InvalidNoteSettings(f"note settings {problem(error)}") from None


def expected_name(notes: str | None) -> str:
    """The name the tool note with these notes is expected to have, bwtidy for notes without a
    valid note section (also notes that are no sections JSON)."""
    try:
        return parse_note(read_sections(notes).get(SECTION)).name
    except (InvalidToolNote, InvalidNoteSettings):
        return DEFAULT_NAME


class SetName(ChangeModel):
    """The expected name, set or replaced."""

    op: Literal["setName"]
    name: str


CHANGES = frozenset({"setName"})


def check_changes(changes: Sequence[Change]) -> None:
    """Raise ValueError naming the first change that does not fit the section."""
    for label, change in each_change(SECTION, CHANGES, changes):
        if isinstance(change, SetName) and not change.name:
            raise ValueError(f"{label}: name is empty")


def changed(value: Any, changes: Sequence[Change], rename: Rename) -> Any:
    """The section with the changes applied. A value that does not parse starts over from the
    default, as a save of the whole section replaced it."""
    try:
        settings = parse_note(value)
    except InvalidNoteSettings:
        settings = NoteSettings()
    for change in changes:
        if isinstance(change, SetName):
            settings = parse_note({**settings.dump(), "name": change.name})
    return settings.dump()


RULES = SectionRules(
    changes=CHANGES,
    parse=parse_note,
    check_changes=check_changes,
    changed=changed,
)
