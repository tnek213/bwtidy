"""The templates section (docs/spec.md, Templates): the template model, its parser and its
changes. Matching and scoring are in sections/matching.py.

A template is a name, an optional description, an item type and a list of coordinates, each of
which can name a datatype by id. A coordinate is a built-in path, a custom field by its name, or a
pattern of custom field names (fieldPattern), which matches every field whose name fits it.
Templates are known by name: a change for a name a save before it renamed or deleted fails, it
never writes an old template back.
"""

from collections.abc import Sequence
from typing import Annotated, Any, Literal, TypeIs

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_serializer,
)

from bwtidy.items.coordinates import (
    BOOLEAN_FIELD,
    FIELDS_PREFIX,
    GROUP_OF_TYPE,
    ITEM_TYPES,
    PATH_ORDER,
    SUB_OBJECTS,
    Coordinate,
)
from bwtidy.sections.notes import (
    Change,
    ChangeModel,
    InvalidToolNote,
    Rename,
    SectionRules,
    each_change,
    read_sections,
)
from bwtidy.validation import refusal, safe_message

SECTION = "templates"
## built-in leaves every item type has that a template may require
COMMON_PATHS = ("name", "notes")
DESCRIPTION_MAX = 500


class InvalidTemplates(Exception):
    """The templates are malformed or not allowed. The message names the problem."""


class _TakesDatatype(BaseModel):
    """A coordinate's datatype (an id) and optional flag, both left out of the dump when unset,
    so a coordinate without them dumps as it always did."""

    @field_validator("datatype", mode="before", check_fields=False)
    @classmethod
    def _datatype_is_an_id(cls, value: Any, info: ValidationInfo) -> Any:
        if value is None or isinstance(value, str):
            return value
        place = info.data.get("path") or f"fields.{info.data.get('name', info.data.get('pattern'))}"
        raise refusal(f"the datatype of {place} is no id")

    @model_serializer(mode="wrap")
    def _without_the_unset(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        dumped = handler(self)
        if dumped.get("datatype") is None:
            dumped.pop("datatype", None)
        if not dumped.get("optional"):
            dumped.pop("optional", None)
        return dumped


class PathCoordinate(_TakesDatatype):
    kind: Literal["path"]
    path: str
    datatype: str | None = None
    optional: StrictBool = False


class FieldCoordinate(_TakesDatatype):
    kind: Literal["field"]
    name: str
    fieldType: Literal[0, 1, 2] = 0
    datatype: str | None = None
    optional: StrictBool = False


PATTERN_RULE = "a pattern needs a * and some other character"


def folded_name(name: str) -> str:
    """A field name as names are compared: without surrounding spaces, ignoring case."""
    return name.strip().casefold()


def glob_matches(pattern: str, text: str) -> bool:
    """Whether the whole text fits the pattern, where * matches any run of characters. The
    parts between the stars are found left to right, so it takes no backtracking."""
    first, *middle, last = pattern.split("*")
    if len(text) < len(first) + len(last) or not text.startswith(first):
        return False
    if not text.endswith(last):
        return False
    at, end = len(first), len(text) - len(last)
    for part in middle:
        found = text.find(part, at, end)
        if found < 0:
            return False
        at = found + len(part)
    return True


class FieldPatternCoordinate(_TakesDatatype):
    """The custom fields whose name fits pattern, a glob where * matches any run of characters
    (also none) and everything else is literal, matched against the whole name ignoring case
    and surrounding spaces. fieldType is the type of a field made for it (New item)."""

    kind: Literal["fieldPattern"]
    pattern: str
    fieldType: Literal[0, 1] = 0
    datatype: str | None = None
    optional: StrictBool = False

    def matches(self, name: str) -> bool:
        return glob_matches(folded_name(self.pattern), folded_name(name))

    @property
    def display(self) -> str:
        """The column it shows as in missing and warnings, fields.user_* for user_*."""
        return f"{FIELDS_PREFIX}{self.pattern}"


type ExactCoordinate = PathCoordinate | FieldCoordinate
type AnyCoordinate = PathCoordinate | FieldCoordinate | FieldPatternCoordinate
type TemplateCoordinate = Annotated[AnyCoordinate, Field(discriminator="kind")]


class Template(BaseModel):
    name: str
    ## left out of the stored form when empty, so a template without one reads and writes as before
    description: str = ""
    type: int
    coordinates: list[TemplateCoordinate]

    @field_validator("description")
    @classmethod
    def _description_is_short(cls, value: str) -> str:
        if len(value) > DESCRIPTION_MAX:
            raise refusal(f"A description holds at most {DESCRIPTION_MAX} characters")
        return value

    @model_serializer(mode="wrap")
    def _without_no_description(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        dumped = handler(self)
        if not dumped.get("description"):
            dumped.pop("description", None)
        return dumped

    def coordinate_list(self) -> list[Coordinate]:
        """The coordinates that name one column, patterns left out."""
        return [coordinate_of(c) for c in self.coordinates if is_exact(c)]

    def required_count(self) -> int:
        """How many coordinates a match needs a value for, the ones not optional, a pattern
        counting once."""
        return sum(not c.optional for c in self.coordinates)

    def patterns(self) -> list[FieldPatternCoordinate]:
        return [c for c in self.coordinates if isinstance(c, FieldPatternCoordinate)]

    def field_types(self) -> dict[Coordinate, int]:
        """The field type of each custom field coordinate."""
        return {
            Coordinate.field(c.name): c.fieldType
            for c in self.coordinates
            if isinstance(c, FieldCoordinate)
        }


TEMPLATE_LIST = TypeAdapter(list[Template])


def is_exact(coordinate: AnyCoordinate) -> TypeIs[ExactCoordinate]:
    return not isinstance(coordinate, FieldPatternCoordinate)


def coordinate_of(coordinate: ExactCoordinate) -> Coordinate:
    if isinstance(coordinate, FieldCoordinate):
        return Coordinate.field(coordinate.name)
    return Coordinate.at(coordinate.path)


def column_of(coordinate: AnyCoordinate) -> str:
    """The column a coordinate shows as: its grid column, or fields.PATTERN for a pattern."""
    if isinstance(coordinate, FieldPatternCoordinate):
        return coordinate.display
    return coordinate_of(coordinate).display


def allowed_paths(item_type: int) -> tuple[str, ...]:
    group = GROUP_OF_TYPE.get(item_type)
    keys = SUB_OBJECTS[group] if group else ()
    return (*COMMON_PATHS, *(f"{group}.{key}" for key in keys))


def _check_coordinate(template: Template, coordinate: AnyCoordinate) -> None:
    if isinstance(coordinate, FieldPatternCoordinate):
        text = coordinate.pattern.strip()
        if "*" not in text or not text.replace("*", "").strip():
            raise ValueError(PATTERN_RULE)
        return
    if isinstance(coordinate, FieldCoordinate):
        if not coordinate.name:
            raise ValueError("a custom field needs a name")
        if coordinate.datatype is not None and coordinate.fieldType == BOOLEAN_FIELD:
            raise ValueError(f"the boolean field {coordinate.name} cannot have a datatype")
        return
    path = coordinate.path
    if path not in PATH_ORDER:
        raise ValueError(f"unknown coordinate {path}")
    if path in allowed_paths(template.type):
        return
    if "." in path:
        raise ValueError(f"a {ITEM_TYPES[template.type]} has no {path}")
    raise ValueError(f"{path} cannot be a template coordinate")


def check_templates(templates: list[Template]) -> None:
    """Raise InvalidTemplates for the first template that is not allowed."""
    seen: set[str] = set()
    for index, template in enumerate(templates):
        label = f"templates[{index}]"
        name = template.name.strip()
        if not name:
            raise InvalidTemplates(f"{label}: a template needs a name")
        if name.casefold() in seen:
            raise InvalidTemplates(f"{label}: there is more than one template named {name!r}")
        seen.add(name.casefold())
        if template.type not in ITEM_TYPES:
            raise InvalidTemplates(f"{label} ({name}): unknown item type {template.type}")
        coordinates: set[Coordinate | str] = set()
        for coordinate in template.coordinates:
            try:
                _check_coordinate(template, coordinate)
            except ValueError as error:
                raise InvalidTemplates(f"{label} ({name}): {error}") from None
            ## a pattern is the same as another when its text is, ignoring case
            parsed = (
                coordinate_of(coordinate)
                if is_exact(coordinate)
                else folded_name(coordinate.pattern)
            )
            if parsed in coordinates:
                column = column_of(coordinate)
                raise InvalidTemplates(f"{label} ({name}): {column} is listed twice")
            coordinates.add(parsed)
        ## without a required one a template would match every item of its type
        if template.coordinates and all(c.optional for c in template.coordinates):
            raise InvalidTemplates(
                f"{label} ({name}): a template needs at least one required field"
            )


def parse_templates(payload: Any) -> list[Template]:
    """Templates from their JSON list form, checked. Raises InvalidTemplates."""
    if not isinstance(payload, list):
        raise InvalidTemplates("templates must be a list")
    try:
        templates = TEMPLATE_LIST.validate_python(payload)
    except ValidationError as error:
        problems = [
            f"templates.{'.'.join(str(p) for p in e['loc'])}: {safe_message(e)}"
            for e in error.errors()
        ]
        raise InvalidTemplates(". ".join(problems[:5])) from None
    check_templates(templates)
    return templates


def load_templates(notes: str | None) -> list[Template]:
    """The templates section of the tool note's notes. Empty notes hold no templates."""
    try:
        sections = read_sections(notes)
    except InvalidToolNote as error:
        raise InvalidTemplates(str(error)) from None
    return parse_templates(sections.get(SECTION, []))


def template_list(templates: list[Template]) -> list[dict[str, Any]]:
    return [t.model_dump() for t in templates]


def templates_using(templates: list[Template], datatype_id: str) -> list[str]:
    """The names of the templates with a coordinate of that datatype."""
    return [t.name for t in templates if any(c.datatype == datatype_id for c in t.coordinates)]


class AddTemplate(ChangeModel):
    """A new template, last in the list. Refused at write time when the name is taken."""

    op: Literal["addTemplate"]
    template: dict[str, Any]


class SetTemplate(ChangeModel):
    """The template of that name replaced in its place, the name kept (renameTemplate renames).
    Fails at write time when no template has the name any more."""

    op: Literal["setTemplate"]
    template: dict[str, Any]


class RenameTemplate(ChangeModel):
    """The template named name renamed to to, in its place."""

    op: Literal["renameTemplate"]
    name: str
    to: str


class MoveTemplate(ChangeModel):
    """The template of that name moved to index in the list (the last place for a larger one),
    since the first of several matches wins."""

    op: Literal["moveTemplate"]
    name: str
    index: int = Field(ge=0)


class RemoveTemplate(ChangeModel):
    """The template of that name deleted."""

    op: Literal["removeTemplate"]
    name: str


CHANGES = frozenset(
    {"addTemplate", "setTemplate", "renameTemplate", "moveTemplate", "removeTemplate"}
)


def check_changes(changes: Sequence[Change]) -> None:
    """Raise ValueError naming the first change that does not fit the section, then the first
    template a change carries that is not valid on its own. Whether it fits the others is known
    only at write time (changed)."""
    for label, change in each_change(SECTION, CHANGES, changes):
        if isinstance(change, RenameTemplate | MoveTemplate | RemoveTemplate) and not change.name:
            raise ValueError(f"{label}: name is empty")
        if isinstance(change, RenameTemplate) and not change.to.strip():
            raise ValueError(f"{label}: a template needs a name")
    for index, change in enumerate(changes):
        if isinstance(change, AddTemplate | SetTemplate):
            try:
                parse_templates([change.template])
            except InvalidTemplates as error:
                problem = str(error).removeprefix("templates[0]").removeprefix("templates.0")
                raise ValueError(f"changes[{index}]{problem}") from None


def _gone(name: str) -> InvalidTemplates:
    return InvalidTemplates(
        f"no template named {name!r}, a save before this one renamed or deleted it"
    )


def _place(templates: list[dict[str, Any]], name: str) -> int:
    """The index of the template of that very name. Raises InvalidTemplates when it is gone."""
    for index, template in enumerate(templates):
        if template["name"] == name:
            return index
    raise _gone(name)


def _refuse_taken(templates: list[dict[str, Any]], name: str, skip: int | None = None) -> None:
    folded = name.strip().casefold()
    for index, template in enumerate(templates):
        if index != skip and template["name"].strip().casefold() == folded:
            raise InvalidTemplates(f"there is already a template named {name.strip()!r}")


def changed(value: Any, changes: Sequence[Change], rename: Rename) -> list[dict[str, Any]]:
    """The templates with the changes applied one after the other, as the note holds them at
    write time. Templates name no item, rename is not needed. Raises InvalidTemplates for a
    change that no longer applies, saying why.

    A section that does not parse (edited by hand) starts over empty, as a save of the whole
    list replaced it.
    """
    try:
        templates = template_list(parse_templates([] if value is None else value))
    except InvalidTemplates:
        templates = []
    for change in changes:
        if isinstance(change, AddTemplate | SetTemplate):
            template = template_list(parse_templates([change.template]))[0]
            if isinstance(change, AddTemplate):
                _refuse_taken(templates, template["name"])
                templates.append(template)
            else:
                templates[_place(templates, template["name"])] = template
        elif isinstance(change, RenameTemplate):
            index = _place(templates, change.name)
            _refuse_taken(templates, change.to, skip=index)
            templates[index] = {**templates[index], "name": change.to.strip()}
        elif isinstance(change, MoveTemplate):
            templates.insert(change.index, templates.pop(_place(templates, change.name)))
        elif isinstance(change, RemoveTemplate):
            templates.pop(_place(templates, change.name))
    return templates


RULES = SectionRules(
    changes=CHANGES,
    parse=parse_templates,
    check_changes=check_changes,
    changed=changed,
)
