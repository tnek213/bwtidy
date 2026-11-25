"""The tool note's notes JSON (docs/spec.md, The tool note) and what every section module builds on.

The notes hold one JSON object, version 1, with one key per section. Each section is saved on
its own (the operation setToolNoteSection), so a save of one never loses another. A key this
version does not know is kept as it is.
"""

import json
from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from bwtidy.validation import safe_message

SECTIONS_VERSION = 1


class InvalidToolNote(Exception):
    """The tool note's notes are not the sections JSON. The message says why."""


def read_sections(notes: str | None) -> dict[str, Any]:
    """The notes JSON as a dict. Empty notes give a fresh one. Raises InvalidToolNote."""
    if not notes or not notes.strip():
        return {"version": SECTIONS_VERSION}
    try:
        stored = json.loads(notes)
    except json.JSONDecodeError:
        raise InvalidToolNote("the notes of the bwtidy note are not JSON") from None
    if not isinstance(stored, dict):
        raise InvalidToolNote("the notes of the bwtidy note are not a JSON object")
    if stored.get("version") != SECTIONS_VERSION:
        raise InvalidToolNote(f"the notes of the bwtidy note are not version {SECTIONS_VERSION}")
    return stored


def dump_sections(sections: dict[str, Any]) -> str:
    """The JSON for the notes, indented so it reads well in Bitwarden too, every key kept."""
    return json.dumps(sections, indent=2, ensure_ascii=False)


def with_section(notes: str | None, section: str, value: Any) -> str:
    """The notes with only that section replaced.

    Notes that are not the sections JSON (edited by hand) start over from a fresh object, as every
    section save replaces them. The backup of the write still holds them.
    """
    try:
        sections = read_sections(notes)
    except InvalidToolNote:
        sections = {"version": SECTIONS_VERSION}
    return dump_sections({**sections, section: value})


def problem(error: ValidationError) -> str:
    """pydantic's first problem with its place, never the input it refused."""
    first = error.errors()[0]
    place = ".".join(str(p) for p in first["loc"])
    message = safe_message(first)
    return f"{place}: {message}" if place else message


class Change(Protocol):
    """A change of one entry of a section (docs/spec.md, Writes), op says which."""

    @property
    def op(self) -> str: ...


class ChangeModel(BaseModel):
    """The base of every section's change models: a key they do not know is refused."""

    model_config = ConfigDict(extra="forbid")


## an item id as it is now, following the conversions that gave the item a new one
type Rename = Callable[[str], str]


def each_change(
    section: str, allowed: frozenset[str], changes: Sequence[Change]
) -> Iterator[tuple[str, Change]]:
    """Each change with its label for an error (changes[INDEX]). Raises ValueError for no
    changes at all and, once the loop gets to it, for a change of another section, so the first
    problem of the list is the one named."""
    if not changes:
        raise ValueError("changes is empty")
    for index, change in enumerate(changes):
        label = f"changes[{index}]"
        if change.op not in allowed:
            raise ValueError(f"{label}: {change.op} is no change of the {section} section")
        yield label, change


@dataclass(frozen=True)
class SectionRules:
    """What bwtidy knows of one section, filled in by its module.

    changes are the op names of its change models. parse checks a whole value as it may be
    saved and raises the section's error. check_changes raises ValueError for the first change
    that does not fit on its own. changed applies changes one after the other to the value the
    note holds at write time, every item id followed through rename, and raises when one no
    longer applies. A rule a section does not have is None: renamed follows the item ids of a
    stored value through rename, without_gone drops what belongs to items that are not among
    the ones given, check_in_notes raises ValueError when the value after a save does not fit
    the rest of the notes.
    """

    changes: frozenset[str]
    parse: Callable[[Any], object]
    check_changes: Callable[[Sequence[Change]], None]
    changed: Callable[[Any, Sequence[Change], Rename], Any]
    renamed: Callable[[Any, Rename], Any] | None = None
    without_gone: Callable[[Any, Collection[str]], Any] | None = None
    check_in_notes: Callable[[str | None, Any, Any], None] | None = None
