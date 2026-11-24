"""The generator section (docs/spec.md, The tool note): the password generator's default and
the settings of single items, by item id, and its changes.

The options are checked by parse_options, so a refused change reads like a refused section.
"""

from collections.abc import Collection, Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from bwtidy.sections.notes import (
    Change,
    ChangeModel,
    Rename,
    SectionRules,
    each_change,
    problem,
    read_sections,
)
from bwtidy.validation import refusal

SECTION = "generator"


class InvalidGenerator(ValueError):
    """Generator settings outside the limits. The message names the option, never a value."""


## strict: a "5" or a 1 for true is refused like Bitwarden's own form would never send them
_OPTIONS_CONFIG = ConfigDict(extra="forbid", strict=True)


class PasswordOptions(BaseModel):
    """A random password, the options of Bitwarden's generator. ambiguous avoids characters
    that look alike (bw generate --ambiguous)."""

    model_config = _OPTIONS_CONFIG
    type: Literal["password"]
    length: int = Field(24, ge=5, le=128)
    uppercase: bool = True
    lowercase: bool = True
    number: bool = True
    special: bool = True
    minNumber: int = Field(1, ge=0, le=9)
    minSpecial: int = Field(1, ge=0, le=9)
    ambiguous: bool = False

    @model_validator(mode="after")
    def _classes_fit(self) -> "PasswordOptions":
        if not (self.uppercase or self.lowercase or self.number or self.special):
            raise refusal("a password needs at least one of A-Z, a-z, 0-9 and special")
        ## bw silently makes the password longer than asked when the minimums do not fit, and a
        ## setting of an item is often there for an app that cannot take long passwords
        needed = self.uppercase + self.lowercase
        needed += max(self.minNumber, 1) if self.number else 0
        needed += max(self.minSpecial, 1) if self.special else 0
        if needed > self.length:
            raise refusal(f"a password of {self.length} characters cannot hold the minimums")
        return self


class PassphraseOptions(BaseModel):
    """Words from bw's word list, joined by the separator ("" for none)."""

    model_config = _OPTIONS_CONFIG
    type: Literal["passphrase"]
    words: int = Field(4, ge=3, le=20)
    separator: str = Field("-", max_length=1)
    capitalize: bool = False
    includeNumber: bool = False


type GeneratorOptions = Annotated[PasswordOptions | PassphraseOptions, Field(discriminator="type")]

## what bwtidy always generated: 24 characters of every class
DEFAULT_OPTIONS = PasswordOptions(type="password")


class GeneratorSettings(BaseModel):
    """The generator section: the default and the settings of single items, by item id."""

    model_config = ConfigDict(extra="forbid", strict=True)
    default: GeneratorOptions | None = None
    items: dict[str, GeneratorOptions] = {}

    def default_options(self) -> PasswordOptions | PassphraseOptions:
        return self.default or DEFAULT_OPTIONS

    def options_for(self, item_id: str) -> PasswordOptions | PassphraseOptions:
        """The item's own setting, else the default."""
        return self.items.get(item_id) or self.default_options()

    def dump(self) -> dict[str, Any]:
        """The section as it is stored. A missing default stays missing."""
        dumped: dict[str, Any] = {}
        if self.default is not None:
            dumped["default"] = self.default.model_dump()
        dumped["items"] = {item_id: o.model_dump() for item_id, o in self.items.items()}
        return dumped


_OPTIONS = TypeAdapter(GeneratorOptions)


## bw generate's words for a space and for no separator at all
SEPARATOR_WORDS = {" ": "space", "": "empty"}


def _flag(on: bool) -> str:
    return "true" if on else "false"


def bw_query(options: PasswordOptions | PassphraseOptions) -> dict[str, str]:
    """The query of GET /generate, whose keys are the options of `bw generate`.

    bw takes "true" or an empty value as true and anything else as false, so every flag is
    sent. Its --ambiguous avoids ambiguous characters, like ours. A minimum of a class that is
    off is sent as 0.
    """
    if isinstance(options, PassphraseOptions):
        return {
            "passphrase": "true",
            "words": str(options.words),
            "separator": SEPARATOR_WORDS.get(options.separator, options.separator),
            "capitalize": _flag(options.capitalize),
            "includeNumber": _flag(options.includeNumber),
        }
    return {
        "length": str(options.length),
        "uppercase": _flag(options.uppercase),
        "lowercase": _flag(options.lowercase),
        "number": _flag(options.number),
        "special": _flag(options.special),
        "minNumber": str(options.minNumber if options.number else 0),
        "minSpecial": str(options.minSpecial if options.special else 0),
        "ambiguous": _flag(options.ambiguous),
    }


def parse_options(value: Any) -> PasswordOptions | PassphraseOptions:
    """One OPTIONS value. Raises InvalidGenerator for one outside the limits."""
    try:
        return _OPTIONS.validate_python(value)
    except ValidationError as error:
        raise InvalidGenerator(f"generator options {problem(error)}") from None


def parse_generator(value: Any) -> GeneratorSettings:
    """The generator section. None (no section yet) is no settings. Raises InvalidGenerator."""
    if value is None:
        return GeneratorSettings()
    try:
        return GeneratorSettings.model_validate(value)
    except ValidationError as error:
        raise InvalidGenerator(f"generator settings {problem(error)}") from None


def stored_generator(notes: str | None) -> GeneratorSettings:
    """The generator section of the tool note's notes. Raises InvalidToolNote, InvalidGenerator."""
    return parse_generator(read_sections(notes).get(SECTION))


class SetDefault(ChangeModel):
    """The generator's default."""

    op: Literal["setDefault"]
    options: dict[str, Any]


class SetItem(ChangeModel):
    """One item's own generator setting, set or replaced."""

    op: Literal["setItem"]
    itemId: str
    options: dict[str, Any]


class RemoveItem(ChangeModel):
    """One item's own generator setting removed, the item back to the default."""

    op: Literal["removeItem"]
    itemId: str


CHANGES = frozenset({"setDefault", "setItem", "removeItem"})


def check_changes(changes: Sequence[Change]) -> None:
    """Raise ValueError naming the first change that does not fit the section."""
    for label, change in each_change(SECTION, CHANGES, changes):
        if isinstance(change, SetItem | RemoveItem) and not change.itemId:
            raise ValueError(f"{label}: itemId is empty")
        if isinstance(change, SetDefault | SetItem):
            parse_options(change.options)


def _renamed_generator(settings: GeneratorSettings, rename: Rename) -> GeneratorSettings:
    """The settings with every item id followed through rename. A setting already under the
    new id wins over the old id's."""
    if all(rename(item_id) == item_id for item_id in settings.items):
        return settings
    items = {}
    for item_id, options in settings.items.items():
        new_id = rename(item_id)
        if new_id != item_id and new_id in settings.items:
            continue
        items[new_id] = options
    return settings.model_copy(update={"items": items})


def renamed(value: Any, rename: Rename) -> Any:
    """The section with its item ids followed through rename. A value that does not parse and
    one where no id changes come back as they are."""
    try:
        settings = parse_generator(value)
    except InvalidGenerator:
        return value
    moved = _renamed_generator(settings, rename)
    return value if moved is settings else moved.dump()


def changed(value: Any, changes: Sequence[Change], rename: Rename) -> Any:
    """The section with the changes applied. A value that does not parse starts over empty, as
    a save of the whole section replaced it. Raises InvalidGenerator for options outside the
    limits."""
    try:
        settings = parse_generator(value)
    except InvalidGenerator:
        settings = GeneratorSettings()
    settings = _renamed_generator(settings, rename)
    default, items = settings.default, dict(settings.items)
    for change in changes:
        if isinstance(change, SetDefault):
            default = parse_options(change.options)
        elif isinstance(change, SetItem):
            items[rename(change.itemId)] = parse_options(change.options)
        elif isinstance(change, RemoveItem):
            items.pop(rename(change.itemId), None)
    return settings.model_copy(update={"default": default, "items": items}).dump()


def without_gone(value: Any, active: Collection[str]) -> Any:
    """The section without the settings of items that are not active. Settings that do not
    parse come back as they are."""
    try:
        settings = parse_generator(value)
    except InvalidGenerator:
        return value
    if all(item_id in active for item_id in settings.items):
        return value
    kept = {item_id: o for item_id, o in settings.items.items() if item_id in active}
    return settings.model_copy(update={"items": kept}).dump()


RULES = SectionRules(
    changes=CHANGES,
    parse=parse_generator,
    check_changes=check_changes,
    changed=changed,
    renamed=renamed,
    without_gone=without_gone,
)
