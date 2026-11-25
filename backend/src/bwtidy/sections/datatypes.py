"""The datatypes section (docs/spec.md, Datatypes): named formats a template coordinate can
name by id, and their changes.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from bwtidy.items.patterns import CompiledPattern, PatternError, ValueTooLong, compile_pattern
from bwtidy.sections.notes import (
    Change,
    ChangeModel,
    Rename,
    SectionRules,
    each_change,
    read_sections,
)
from bwtidy.sections.templates import (
    InvalidTemplates,
    Template,
    load_templates,
    templates_using,
)

SECTION = "datatypes"
DATATYPE_ID = re.compile(r"[0-9a-f]{8}")
## bounds the work of one check, the note could never hold many more anyway
MAX_EXAMPLES = 50


class InvalidDatatypes(ValueError):
    """Datatypes that are not allowed. The message says why and never quotes an example."""


@dataclass(frozen=True)
class Datatype:
    """One datatype of the section. compiled is None and broken says why when it fails its
    checks: it stays listed, and scores nothing."""

    id: str
    name: str
    pattern: str
    description: str = ""
    ignoreCase: bool = False
    ## False lets the pattern match any part of a value, stored only then: a note without the key
    ## means a whole value match, as every datatype before the key had
    wholeValue: bool = True
    examples: tuple[str, ...] = ()
    counterExamples: tuple[str, ...] = ()
    compiled: CompiledPattern | None = None
    broken: str | None = None

    def dump(self) -> dict[str, Any]:
        """The datatype as it is stored in the section."""
        stored: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "pattern": self.pattern,
            "description": self.description,
            "ignoreCase": self.ignoreCase,
        }
        if not self.wholeValue:
            stored["wholeValue"] = False
        stored["examples"] = list(self.examples)
        stored["counterExamples"] = list(self.counterExamples)
        return stored


def compile_datatype(pattern: str, ignore_case: bool, whole_value: bool = True) -> CompiledPattern:
    """The pattern compiled. Raises PatternError, also for an empty one."""
    if not pattern:
        raise PatternError("a datatype needs a pattern")
    return compile_pattern(pattern, ignore_case, whole_value)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _texts(value: Any) -> tuple[str, ...]:
    return tuple(v for v in value if isinstance(v, str)) if isinstance(value, list) else ()


def _check_shape(entry: dict[str, Any]) -> None:
    for key in ("id", "name", "pattern"):
        if not isinstance(entry.get(key), str):
            raise InvalidDatatypes(f"{key} is no text")
    if not isinstance(entry.get("description", ""), str):
        raise InvalidDatatypes("description is no text")
    if not isinstance(entry.get("ignoreCase", False), bool):
        raise InvalidDatatypes("ignoreCase is neither true nor false")
    if not isinstance(entry.get("wholeValue", True), bool):
        raise InvalidDatatypes("wholeValue is neither true nor false")
    for key in ("examples", "counterExamples"):
        listed = entry.get(key, [])
        if not isinstance(listed, list) or not all(isinstance(v, str) for v in listed):
            raise InvalidDatatypes(f"{key} is no list of text")
        if len(listed) > MAX_EXAMPLES:
            raise InvalidDatatypes(f"{key} holds more than {MAX_EXAMPLES}")


def _check_examples(datatype: Datatype, compiled: CompiledPattern) -> None:
    for number, example in enumerate(datatype.examples, 1):
        try:
            matches = compiled.score(example).distance == 0
        except ValueTooLong:
            raise InvalidDatatypes(f"example {number} is too long to check") from None
        if not matches:
            raise InvalidDatatypes(f"example {number} does not match the format")
    for number, example in enumerate(datatype.counterExamples, 1):
        try:
            matches = compiled.score(example).distance == 0
        except ValueTooLong:
            ## too long scores 0, which is below 1
            matches = False
        if matches:
            raise InvalidDatatypes(f"counter-example {number} matches the format")


def _read_datatype(entry: Any, ids: set[str], names: set[str]) -> Datatype:
    """One entry, kept whatever it holds: what fails its checks comes back marked broken."""
    if not isinstance(entry, dict):
        return Datatype("", "", "", broken="a datatype is an object")
    datatype = Datatype(
        id=_text(entry.get("id")),
        name=_text(entry.get("name")).strip(),
        pattern=_text(entry.get("pattern")),
        description=_text(entry.get("description")),
        ignoreCase=entry.get("ignoreCase") is True,
        wholeValue=entry.get("wholeValue") is not False,
        examples=_texts(entry.get("examples")),
        counterExamples=_texts(entry.get("counterExamples")),
    )
    try:
        _check_shape(entry)
        if not DATATYPE_ID.fullmatch(datatype.id):
            raise InvalidDatatypes("the id is not 8 hex characters")
        if datatype.id in ids:
            raise InvalidDatatypes(f"there is more than one datatype with the id {datatype.id}")
        ids.add(datatype.id)
        if not datatype.name:
            raise InvalidDatatypes("a datatype needs a name")
        if datatype.name.casefold() in names:
            raise InvalidDatatypes(f"there is more than one datatype named {datatype.name!r}")
        names.add(datatype.name.casefold())
        try:
            compiled = compile_datatype(datatype.pattern, datatype.ignoreCase, datatype.wholeValue)
        except PatternError as error:
            raise InvalidDatatypes(str(error)) from None
        _check_examples(datatype, compiled)
    except InvalidDatatypes as error:
        return replace(datatype, broken=str(error))
    return replace(datatype, compiled=compiled)


def load_datatypes(value: Any) -> list[Datatype]:
    """The section as it is stored (the note is untrusted too): a datatype that fails its checks
    is kept, marked broken. None (no section yet) is no datatypes. Raises InvalidDatatypes only
    when the section is no list."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidDatatypes("datatypes is not a list")
    ids: set[str] = set()
    names: set[str] = set()
    return [_read_datatype(entry, ids, names) for entry in value]


def parse_datatypes(value: Any) -> list[Datatype]:
    """The section as it may be saved. Raises InvalidDatatypes naming the first problem."""
    if not isinstance(value, list):
        raise InvalidDatatypes("datatypes is not a list")
    datatypes = load_datatypes(value)
    for index, datatype in enumerate(datatypes):
        if datatype.broken is not None:
            named = f" ({datatype.name})" if datatype.name else ""
            raise InvalidDatatypes(f"datatypes[{index}]{named}: {datatype.broken}")
    return datatypes


def stored_datatypes(notes: str | None) -> list[Datatype]:
    """The datatypes of the tool note's notes. Raises InvalidToolNote, InvalidDatatypes."""
    return load_datatypes(read_sections(notes).get(SECTION))


class AddDatatype(ChangeModel):
    """A new datatype, last in the list, with the id the route gave it when it was queued."""

    op: Literal["addDatatype"]
    datatype: dict[str, Any]


class SetDatatype(ChangeModel):
    """The datatype of that id replaced in its place. Fails at write time when it is gone."""

    op: Literal["setDatatype"]
    datatype: dict[str, Any]


class RemoveDatatype(ChangeModel):
    """The datatype of that id deleted, nothing when it is gone already. Refused at write time
    while a template uses it."""

    op: Literal["removeDatatype"]
    id: str


CHANGES = frozenset({"addDatatype", "setDatatype", "removeDatatype"})


def check_changes(changes: Sequence[Change]) -> None:
    """Raise ValueError naming the first change that does not fit the section. Never quotes a
    value."""
    for label, change in each_change(SECTION, CHANGES, changes):
        if isinstance(change, RemoveDatatype) and not change.id:
            raise ValueError(f"{label}: id is empty")
        if isinstance(change, AddDatatype | SetDatatype):
            try:
                parse_datatypes([change.datatype])
            except InvalidDatatypes as error:
                raise ValueError(f"{label}{str(error).removeprefix('datatypes[0]')}") from None


def _datatype_id(entry: Any) -> Any:
    return entry.get("id") if isinstance(entry, dict) else None


def changed(value: Any, changes: Sequence[Change], rename: Rename) -> list[Any]:
    """The datatypes with the changes applied as the note holds them, the broken ones too: the
    whole list is checked afterwards, so a broken one still goes only by being fixed or deleted.
    Datatypes name no item, rename is not needed. Raises InvalidDatatypes for a change that no
    longer applies."""
    entries = list(value) if isinstance(value, list) else []
    for change in changes:
        if isinstance(change, RemoveDatatype):
            entries = [e for e in entries if _datatype_id(e) != change.id]
            continue
        if not isinstance(change, AddDatatype | SetDatatype):
            continue
        datatype = _read_datatype(change.datatype, set(), set()).dump()
        found = [i for i, e in enumerate(entries) if _datatype_id(e) == datatype["id"]]
        if isinstance(change, AddDatatype):
            if found:
                raise InvalidDatatypes(f"there is already a datatype with the id {datatype['id']}")
            entries.append(datatype)
        elif not found:
            raise InvalidDatatypes(
                f"the datatype {datatype['name']!r} is gone, a save before this one deleted it"
            )
        else:
            entries[found[0]] = datatype
    return entries


def used_datatypes(
    templates: list[Template], removed_ids: set[str], datatypes: list[Datatype]
) -> list[tuple[Datatype, list[str]]]:
    """The datatypes among the removed ids that templates still use, each with the names of the
    templates, in the order of the datatypes."""
    used = [(d, templates_using(templates, d.id)) for d in datatypes if d.id in removed_ids]
    return [(datatype, names) for datatype, names in used if names]


def refuse_deleting_used(notes: str | None, before: Any, after: Any) -> None:
    """Raise ValueError when the datatypes after a save lack one that a template of the notes
    uses. Templates that cannot be read use none."""
    try:
        old = load_datatypes(before)
        kept = {d.id for d in load_datatypes(after)}
        templates = load_templates(notes)
    except (InvalidDatatypes, InvalidTemplates):
        return
    removed = {d.id for d in old} - kept
    used = used_datatypes(templates, removed, old)
    if used:
        datatype, names = used[0]
        raise ValueError(
            f"the datatype {datatype.name!r} is used by the templates {', '.join(names)}"
        )


RULES = SectionRules(
    changes=CHANGES,
    parse=parse_datatypes,
    check_changes=check_changes,
    changed=changed,
    check_in_notes=refuse_deleting_used,
)
