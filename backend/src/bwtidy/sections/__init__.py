"""The sections of the tool note's notes JSON (docs/spec.md, The tool note), one module each.

A save of a section holds changes (a set, a remove, a rename or a move of one entry), applied to
the section as the note holds it at write time, so two saves never undo each other. Each
section's module has its models, its parser, its change models and their check and application,
and fills in a SectionRules. SECTIONS maps a section's name to them, and writes/saves.py plans a
save through that one table. A new section is one module, its entry in Section and SECTIONS, and
its change models in SectionChange.

The package defines Section, SECTIONS and SectionChange and re-exports what reads and writes the
notes JSON as a whole (notes.py). The section modules are imported by name
(`from bwtidy.sections.templates import Template`).
"""

from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, ValidationError

from bwtidy.sections import datatypes, generator, note, templates, to_fill
from bwtidy.sections.datatypes import AddDatatype, RemoveDatatype, SetDatatype
from bwtidy.sections.generator import RemoveItem, SetDefault, SetItem
from bwtidy.sections.note import SetName
from bwtidy.sections.notes import (
    SECTIONS_VERSION,
    InvalidToolNote,
    Rename,
    SectionRules,
    dump_sections,
    problem,
    read_sections,
    with_section,
)
from bwtidy.sections.templates import (
    AddTemplate,
    MoveTemplate,
    RemoveTemplate,
    RenameTemplate,
    SetTemplate,
)
from bwtidy.sections.to_fill import AddColumn, RemoveColumn

__all__ = [
    "CHANGES_OF",
    "SECTIONS",
    "SECTIONS_VERSION",
    "InvalidToolNote",
    "Rename",
    "Section",
    "SectionChange",
    "dump_sections",
    "parse_changes",
    "read_sections",
    "renamed_section",
    "with_section",
]

Section = Literal["templates", "toFill", "generator", "datatypes", "note"]

SECTIONS: dict[str, SectionRules] = {
    "templates": templates.RULES,
    "toFill": to_fill.RULES,
    "generator": generator.RULES,
    "datatypes": datatypes.RULES,
    "note": note.RULES,
}

CHANGES_OF: dict[str, frozenset[str]] = {name: rules.changes for name, rules in SECTIONS.items()}

type SectionChange = Annotated[
    SetDefault
    | SetItem
    | RemoveItem
    | AddColumn
    | RemoveColumn
    | AddTemplate
    | SetTemplate
    | RenameTemplate
    | MoveTemplate
    | RemoveTemplate
    | AddDatatype
    | SetDatatype
    | RemoveDatatype
    | SetName,
    Field(discriminator="op"),
]

_CHANGES = TypeAdapter(list[SectionChange])


def parse_changes(payload: Any) -> list[SectionChange]:
    """The changes of a request in their JSON form. Raises ValueError naming the first problem
    and its place, never a value. Whether they fit their section is its check_changes's to say."""
    if not isinstance(payload, list):
        raise ValueError("changes must be a list")
    try:
        return _CHANGES.validate_python(payload)
    except ValidationError as error:
        raise ValueError(f"changes.{problem(error)}") from None


def renamed_section(section: str, value: Any, rename: Rename) -> Any:
    """The section with the item ids of its entries followed through rename (the conversions
    of the session). A section without item ids comes back as it is."""
    renamed = SECTIONS[section].renamed
    return value if renamed is None else renamed(value, rename)
